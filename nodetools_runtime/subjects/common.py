"""Shared helpers for the info subjects.

Info subjects emit verbatim ``<node>: <payload>`` lines that mirror the legacy
``node*`` / ``nvsw*`` / ``ps*`` scripts, so the helpers here only fetch data and
format it; they never assume a hardcoded collection layout beyond what the
original scripts used.
"""

import json

from ..errors import CapUnsupported, TransportError, TransportTimeout


def resource(device, path, required=False):
    """GET a single Redfish resource, returning ``{}`` unless ``required``.

    A Redfish error body is a JSON object too, so it must be treated as absent.
    """
    data = device.redfish.get(path)
    if isinstance(data, dict) and "error" not in data:
        return data
    if required:
        reason = getattr(device.redfish, "timeout_reason", None)
        if reason:
            raise TransportTimeout("{} {}".format(reason, path))
        raise TransportError("no data at {}".format(path))
    return {}


def member_ids(collection):
    out = []
    if isinstance(collection, dict) and isinstance(collection.get("Members"), list):
        for member in collection["Members"]:
            oid = member.get("@odata.id") if isinstance(member, dict) else None
            if oid:
                out.append(oid)
    return out


def entries(device, path):
    """Detail dicts for every member of an event-log style collection.

    The legacy scripts read ``.Members[]`` directly, which only works when the
    collection inlines its entries. When a vendor returns references instead,
    the member is fetched so output is the same either way.
    """
    coll = resource(device, path, required=True)
    out = []
    for member in coll.get("Members") or []:
        if not isinstance(member, dict):
            continue
        if len(member) > 1:
            out.append(member)
            continue
        oid = member.get("@odata.id")
        if not oid:
            continue
        detail = device.redfish.get(oid)
        if isinstance(detail, dict):
            out.append(detail)
    return out


def log_lines(device, path, sep=" "):
    """``Created<sep>Severity<sep>Message`` lines, sorted like the scripts."""
    rows = []
    for entry in entries(device, path):
        rows.append(
            "{}{}{}{}{}".format(
                entry.get("Created", ""),
                sep,
                entry.get("Severity", ""),
                sep,
                entry.get("Message", ""),
            )
        )
    rows.sort()
    return rows


def column_t(rows):
    """Pad cells like ``column -t`` (align each column to its widest cell)."""
    rows = [[str(cell) for cell in row] for row in rows]
    if not rows:
        return []
    widths = [0] * max(len(row) for row in rows)
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    return [
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()
        for row in rows
    ]


def jq(value):
    """Mimic ``jq '.'`` default output for a scalar/object (JSON, quoted str)."""
    return json.dumps(value)


def jq_out(body):
    """Mimic ``curl ... | jq`` for a POST body: JSON, or nothing if not JSON."""
    if isinstance(body, (dict, list)):
        return json.dumps(body)
    return ""


def post(device, path, body=None):
    return device.redfish.post(path, body if body is not None else {})


def patch(device, path, body, etag_source=None):
    if etag_source:
        return device.redfish.patch(path, body, etag_source=etag_source)
    return device.redfish.patch(path, body)


def body_text(body):
    """Raw text of a POST response for scripts that pipe straight to ``echo``."""
    if isinstance(body, str):
        return body.strip()
    if isinstance(body, (dict, list)):
        return json.dumps(body)
    return ""


def require_write_success(path, result):
    status, body = result
    if not 200 <= status < 300 or (isinstance(body, dict) and "error" in body):
        raise TransportError("Redfish write failed at {} (HTTP {})".format(path, status))
    return body


def action_target(resource, action, parent):
    descriptor = (resource.get("Actions") or {}).get("#" + action) or {}
    target = descriptor.get("target")
    if not isinstance(target, str) or not target.startswith(parent.rstrip("/") + "/"):
        raise CapUnsupported("Redfish did not advertise {} under {}".format(action, parent))
    return target, descriptor


def reset_system(device, system, data, preferred):
    target, descriptor = action_target(data, "ComputerSystem.Reset", system)
    allowable = descriptor.get("ResetType@Redfish.AllowableValues")
    info = descriptor.get("@Redfish.ActionInfo")
    if allowable is None and isinstance(info, str) and info.startswith(system + "/"):
        parameters = resource(device, info, required=True).get("Parameters") or []
        allowable = next((p.get("AllowableValues") for p in parameters if p.get("Name") == "ResetType"), None)
    chosen = next((value for value in preferred if value in (allowable or [])), None)
    if chosen is None:
        raise CapUnsupported("Redfish did not advertise reset type {}".format("/".join(preferred)))
    require_write_success(target, post(device, target, {"ResetType": chosen}))
    return chosen
