"""Shared helpers for capability collectors."""

import re

_NUM = re.compile(r"^[+-]?\d+(\.\d+)?")


def reading(rid, name, value, health="Unknown", **extra):
    item = {
        "id": rid if rid not in (None, "") else (name or "?"),
        "name": name if name not in (None, "") else (rid or "?"),
        "value": value if value not in (None, "") else "NA",
        "health": health or "Unknown",
    }
    item.update(extra)
    return item


def health_of(status):
    if not isinstance(status, dict):
        return "Unknown"
    return status.get("Health") or status.get("State") or "Unknown"


def num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    m = _NUM.match(str(value).strip())
    return m.group(0) if m else None


def fan_speed(data):
    speed = data.get("SpeedPercent")
    if isinstance(speed, dict):
        for key in ("SpeedRPM", "Reading"):
            if speed.get(key) is not None:
                return num(speed[key])
    for key in ("Reading", "SpeedRPM", "ReadingRPM"):
        if data.get(key) is not None:
            return num(data[key])
    return None


def member_ids(collection):
    if not isinstance(collection, dict):
        return []
    members = collection.get("Members")
    if not isinstance(members, list):
        return []
    out = []
    for member in members:
        oid = member.get("@odata.id") if isinstance(member, dict) else None
        if oid:
            out.append(oid)
    return out


def chassis_list(device, defaults):
    seen = []
    for ident in list(device.probe.get("chassis") or []) + list(defaults):
        if ident and ident not in seen:
            seen.append(ident)
    return seen


def enumerate_collection(redfish, path):
    """Return detail dicts for every member of a Redfish collection."""
    collection = redfish.get(path)
    details = []
    for oid in member_ids(collection):
        detail = redfish.get(oid)
        if isinstance(detail, dict):
            details.append(detail)
    return details


def enumerate_sensors(redfish, chassis):
    """Return every Sensor under a chassis (detail dicts)."""
    return enumerate_collection(
        redfish, "/redfish/v1/Chassis/{}/Sensors".format(chassis)
    )
