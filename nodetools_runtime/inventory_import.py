"""Merge legacy hosts.list records into an existing inventory."""

from collections import defaultdict
import fcntl
import os
from pathlib import Path
import re
import stat
import tempfile

from . import hostlist
from .errors import InventoryError
from .inventory import Inventory, _strip_comment, hostslist_entries

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_ADDRESS = re.compile(r"[A-Za-z0-9_.:%\[\]-]+")


def plan_import(text, source):
    inventory = Inventory.from_text(text)
    existing = defaultdict(list)
    for host in inventory.hosts.values():
        existing[host.name.casefold()].append(host)

    records = {}
    for group, legacy_group, name, address in hostslist_entries(source, strict=True):
        if not _NAME.fullmatch(name) or not _ADDRESS.fullmatch(address):
            raise InventoryError(f"invalid hostname or BMC address in {source}")
        key = name.casefold()
        prior = records.get(key)
        if prior and (prior[0], prior[1], prior[3].casefold()) != (group, legacy_group, address.casefold()):
            raise InventoryError(f"conflicting hosts.list entries for {name}")
        records.setdefault(key, (group, legacy_group, name, address))

    additions = defaultdict(list)
    conflicts = []
    registered = 0
    for key, (group, legacy_group, name, address) in records.items():
        matches = existing[key]
        if len(matches) > 1:
            conflicts.append(f"{name}: ambiguous existing hostname")
        elif not matches:
            additions[group].append((name, address))
            if legacy_group:
                additions[legacy_group].append((name, None))
        elif (matches[0].address or "").casefold() != address.casefold():
            conflicts.append(f"{name}: BMC address differs")
        else:
            registered += 1
            host = matches[0]
            # Legacy Switch records are NVSwitches regardless of hostname.
            # Repair inventories generated before this classification fix.
            if group == "nvswitch" and host.role == "unknown" and not host.get("role"):
                additions[group].append((host.name, None))
            if legacy_group and legacy_group not in host.groups:
                additions[legacy_group].append((host.name, None))

    if not additions:
        return text, additions, registered, conflicts

    lines = text.splitlines(keepends=True)
    headers = []
    for index, raw in enumerate(lines):
        line = _strip_comment(raw).strip()
        if line.startswith("[") and line.endswith("]"):
            headers.append((index, line[1:-1].strip()))
    endings = {}
    for offset, (index, group) in enumerate(headers):
        end = headers[offset + 1][0] if offset + 1 < len(headers) else len(lines)
        while end > index + 1 and not lines[end - 1].strip():
            end -= 1
        endings[group] = end

    newline = "\r\n" if "\r\n" in text else "\n"
    inserts = []
    for group, hosts in additions.items():
        rows = "".join(
            f"{name} host={address}{newline}" for name, address in hosts if address is not None
        )
        members = [name for name, address in hosts if address is None]
        if members:
            rows += hostlist.compact(members) + newline
        if group in endings:
            at = endings[group]
            prefix = "" if lines[at - 1].endswith(("\n", "\r")) else newline
            inserts.append((at, False, prefix + rows))
        else:
            inserts.append((len(lines), True, newline + f"[{group}]{newline}" + rows))

    for at, _, block in reversed(sorted(inserts, key=lambda insertion: insertion[:2])):
        lines.insert(at, block)
    candidate = "".join(lines)
    parsed = Inventory.from_text(candidate)
    for group, hosts in additions.items():
        for name, address in hosts:
            node = parsed.get(name)
            if node is None or group not in node.groups or (address is not None and node.address != address):
                raise InventoryError(f"could not place {name} in [{group}]")
            before = inventory.get(name)
            role_repair = (
                group == "nvswitch" and before is not None
                and before.role == "unknown" and not before.get("role")
            )
            if role_repair and node.role != "nvswitch":
                raise InventoryError(f"could not restore NVSwitch role for {name}")
            if address is None and before is not None and node.vars != before.vars and not role_repair:
                raise InventoryError(f"[{group}:vars] would change existing settings for {name}")
    return candidate, additions, registered, conflicts


def import_hostslist(source, target, apply=False):
    """Preview, or atomically add legacy records to an existing inventory."""
    try:
        path = Path(target).resolve(strict=True)
        if not apply:
            _, additions, registered, conflicts = plan_import(path.read_bytes().decode(), source)
            return additions, registered, conflicts

        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(directory, fcntl.LOCK_EX)
            with path.open("rb") as handle:
                original = handle.read()
                metadata = os.fstat(handle.fileno())
            text, additions, registered, conflicts = plan_import(original.decode(), source)
            if additions:
                _replace_inventory(path, text.encode(), original, metadata)
        finally:
            os.close(directory)
        return additions, registered, conflicts
    except (OSError, UnicodeError) as exc:
        raise InventoryError(f"could not import into {target}: {exc}") from exc


def _replace_inventory(path, candidate, original, metadata):
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            current = os.fstat(handle.fileno())
            if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
                os.fchown(handle.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(metadata.st_mode))
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != original:
            raise InventoryError("inventory changed during import; retry")
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)
