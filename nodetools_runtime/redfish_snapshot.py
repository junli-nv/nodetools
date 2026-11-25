"""Capture read-only Redfish resources for offline adapter development."""

import hashlib
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections import deque
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .errors import CapUnsupported, NodetoolsError

ROOT = "/redfish/v1"
ROOTS = (
    "Systems", "Chassis", "Managers", "UpdateService", "TelemetryService",
    "PowerEquipment", "Fabrics", "Oem",
)
SCHEMA = 1
_BLOCKED = {
    "accountservice", "sessionservice", "certificateservice", "keyservice",
    "externalaccountproviders", "accounts", "sessions", "certificates", "entries",
}
_PROTECTED = re.compile(
    r"password|passwd|secret|token|credential|authorization|privatekey|apikey|"
    r"username|serialnumber|assettag|uuid|hostname|fqdn", re.I
)
_IP = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
_MAC = re.compile(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}|(?<=MAC\()[0-9a-f]{12}")


def resource_path(value, origin=None):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.netloc or parsed.scheme:
        if origin is None or (parsed.scheme, parsed.netloc) != origin:
            return None
    if parsed.fragment:
        return None
    path = parsed.path.rstrip("/")
    parts = unquote(path).split("/")
    if (parts[:3] != ["", "redfish", "v1"]
            or any(part in ("", ".", "..") or "\\" in part for part in parts[3:])
            or any(part.casefold() in _BLOCKED for part in parts[3:])
            or (len(parts) > 3 and parts[3] not in ROOTS)):
        return None
    return path + ("?" + parsed.query if parsed.query else "")


def _links(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "@odata.id" or key.endswith("@odata.nextLink"):
                yield child
            elif key != "Actions":
                yield from _links(child)
    elif isinstance(value, list):
        for child in value:
            yield from _links(child)


class Redactor:
    def __init__(self, origin, private_values):
        self.origin = origin
        self.private_values = sorted({value for value in private_values if value}, key=len, reverse=True)
        self.salt = secrets.token_bytes(16)

    def _mac(self, match):
        original = match.group()
        normalized = re.sub(r"[:-]", "", original.lower())
        masked = "02" + hashlib.blake2s(
            normalized.encode(), key=self.salt, digest_size=5
        ).hexdigest()
        return ":".join(masked[i:i + 2] for i in range(0, 12, 2)) if len(original) > 12 else masked

    def clean(self, value, field=""):
        if _PROTECTED.search(re.sub(r"[^a-z0-9]", "", field.lower())):
            return "<redacted>"
        if isinstance(value, dict):
            return {key: self.clean(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [self.clean(item, field) for item in value]
        if not isinstance(value, str):
            return value
        if field in ("@odata.id", "target") or field.endswith("@odata.nextLink"):
            local = resource_path(value, self.origin)
            if local:
                return local
        if value in self.private_values:
            return "<redacted>"
        if field.lower().endswith("version"):
            return value
        for private in self.private_values:
            if len(private) >= 6:
                value = value.replace(private, "<redacted>")
        if re.search(r"(?i)(?:https?|smb|cifs|nfs)://", value):
            return "<redacted-url>"
        try:
            if ipaddress.ip_address(value).version == 6:
                return "2001:db8::1"
        except ValueError:
            pass
        return _IP.sub("192.0.2.1", _MAC.sub(self._mac, value))


def capture(address, factory, metadata, private_values, *, workers, seconds, limit, extra_paths=()):
    origin = ("https", address)
    redactor = Redactor(origin, private_values)
    deadline = time.monotonic() + seconds
    pending = deque()
    scheduled = set()
    records = {}
    local = threading.local()
    clients = []

    def schedule(value):
        path = resource_path(value, origin)
        if path and path not in scheduled:
            scheduled.add(path)
            pending.append(path)

    def fetch(path):
        if not hasattr(local, "client"):
            local.client = factory()
            clients.append(local.client)
            local.client.set_deadline(deadline)
        return local.client.get_raw(path, follow_redirects=False)

    for path in extra_paths:
        if not resource_path(path):
            raise NodetoolsError("additional paths must be allowed local Redfish resources")
    for path in (ROOT, *extra_paths, *(ROOT + "/" + root for root in ROOTS)):
        schedule(path)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            active = {}
            while pending or active:
                while (pending and len(active) < workers
                       and len(records) + len(active) < limit and time.monotonic() < deadline):
                    path = pending.popleft()
                    active[pool.submit(fetch, path)] = path
                if not active:
                    break
                finished, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in finished:
                    path = active.pop(future)
                    status, body = future.result()
                    value = redactor.clean(body) if isinstance(body, (dict, list)) else None
                    records[path] = {"status": status, "body": value}
                    if status is None or not 200 <= status < 300 or not isinstance(body, dict):
                        continue
                    for link in _links(body):
                        if isinstance(link, str) and link.startswith("?"):
                            link = path.split("?", 1)[0] + link
                        schedule(link)
                    if "Members" in body and "?" not in path:
                        schedule(path + "?$expand=.")
                    parts = path.split("/")
                    if len(parts) == 5 and parts[3] == "Chassis":
                        for suffix in ("Sensors", "Thermal", "ThermalSubsystem/Fans",
                                       "ThermalSubsystem/ThermalMetrics", "Power",
                                       "PowerSubsystem/PowerSupplies", "NetworkAdapters"):
                            schedule(path + "/" + suffix)
                    if len(parts) == 5 and parts[3] == "Systems":
                        for suffix in ("Bios", "BootOptions", "EthernetInterfaces"):
                            schedule(path + "/" + suffix)
    finally:
        for client in clients:
            client.close()

    root = records.get(ROOT, {})
    if root.get("status") != 200 or not isinstance(root.get("body"), dict):
        raise NodetoolsError(f"Redfish root unavailable (HTTP {root.get('status')})")
    errors = sum(record["status"] is None for record in records.values())
    return {
        "schema": SCHEMA, "device": metadata, "resources": records,
        "capture": {"complete": not pending and not errors,
                    "pending": len(pending), "transport_errors": errors,
                    "seconds": round(seconds + time.monotonic() - deadline, 2)},
    }


def save(path, snapshot):
    content = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                       "w", encoding="utf-8") as stream:
            stream.write(content)
    except OSError as exc:
        raise NodetoolsError(f"cannot save snapshot: {exc}") from exc


def load(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NodetoolsError(f"cannot read snapshot: {exc}") from exc
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA
            or not isinstance(data.get("resources"), dict)
            or not isinstance(data.get("device"), dict)):
        raise NodetoolsError("invalid or unsupported Redfish snapshot schema")
    for path, record in data["resources"].items():
        if (resource_path(path) != path or not isinstance(record, dict)
                or "status" not in record or "body" not in record
                or (record["status"] is not None
                    and (not isinstance(record["status"], int) or not 100 <= record["status"] <= 599))):
            raise NodetoolsError("invalid resource record in Redfish snapshot")
    return data


class Replay:
    def __init__(self, snapshot):
        self.records = snapshot["resources"]
        self.missing = set()
        self.failed = set()
        self.transport_errors = 0

    def get_raw(self, path, timeout=None):
        normalized = resource_path(path)
        record = self.records.get(normalized)
        if record is None:
            self.missing.add(normalized or "<uncaptured path>")
            return None, None
        if record["status"] is None:
            self.failed.add(normalized)
            self.transport_errors += 1
        return record["status"], record["body"]

    def get(self, path, timeout=None):
        status, data = self.get_raw(path, timeout=timeout)
        return data if status is not None and isinstance(data, (dict, list)) else None

    def post(self, *args, **kwargs):
        raise CapUnsupported("Redfish snapshot replay is read-only")

    patch = post

    def close(self):
        pass
