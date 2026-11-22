"""Shared helpers for capability collectors.

Collections are walked with the Redfish ``$expand=.`` query when the BMC
supports it (OpenBMC does), which keeps a whole Sensors/Fans/PowerSupplies
collection to a single request. Every id is discovered from the service, never
hardcoded, so vendor differences (chassis/system/manager names) are handled by
probing rather than assumption.
"""

import concurrent.futures
import re

_NUM = re.compile(r"^[+-]?\d+(\.\d+)?")

# Chassis a single node is fanned out over in parallel (ladder stays ordered).
SCAN_WORKERS = 12


def probe_get(device, path):
    """GET once per device+path, reusing the result for the whole run."""
    cache = getattr(device, "_get_cache", None)
    if cache is None:
        cache = {}
        device._get_cache = cache
    if path not in cache:
        try:
            cache[path] = device.redfish.get(path)
        except Exception:
            cache[path] = None
    return cache[path]


def absent(device, path):
    """True when an endpoint is uniformly absent (probed on one chassis).

    Legacy endpoints (``Thermal``, the plain ``Power`` object) are gone on
    OpenBMC; without this every capability would spend a full extra pass over
    all chassis just to collect 404s. A Redfish error is a JSON object too, so
    it must be treated as absent.
    """
    data = probe_get(device, path)
    return not (isinstance(data, dict) and "error" not in data)


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


def dedup_readings(readings):
    """Drop only *identical* readings, keeping the first occurrence.

    Deliberately does not collapse distinct sensors that merely share an id:
    every distinct reading is kept so output can be checked line by line.
    """
    seen = set()
    out = []
    for item in readings:
        key = (item.get("id"), item.get("name"), item.get("value"),
               item.get("health"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def member_ident(oid):
    return oid.rstrip("/").rsplit("/", 1)[-1] if oid else None


def discover_ids(redfish, collection_path):
    """Member ids of a Redfish collection, discovered (never assumed)."""
    out = []
    for oid in member_ids(redfish.get(collection_path)):
        ident = member_ident(oid)
        if ident and ident not in out:
            out.append(ident)
    return out


def discover_chassis(redfish):
    """Chassis ids actually exposed by the BMC (Redfish, dynamic)."""
    return discover_ids(redfish, "/redfish/v1/Chassis")


def chassis_list(device, defaults=()):
    """Chassis ids the BMC actually exposes; ``defaults`` only pad a failure.

    Discovery is cached on the device so a single node asks once, and it is
    authoritative: the NVSwitch trays expose ``MGX_*`` chassis that no static
    list would contain, and every vendor names chassis differently.
    """
    selected = getattr(device, "vars", {}).get("chassis_id")
    if selected:
        return [selected]
    cached = getattr(device, "_chassis_cache", None)
    if cached is not None:
        return cached
    prof = getattr(device, "profile", None) or {}
    discovered = list(prof.get("chassis") or [])
    if not discovered:
        probe = getattr(device, "probe", None) or {}
        discovered = list(probe.get("chassis") or [])
    if not discovered:
        try:
            discovered = discover_chassis(device.redfish)
        except Exception:
            discovered = []
    seen = []
    for ident in discovered + list(defaults):
        if ident and ident not in seen:
            seen.append(ident)
    device._chassis_cache = seen
    return seen


def profile(device):
    """The cached scan map for this BMC, if any (see ``scan.py``)."""
    return getattr(device, "profile", None)


def cached_chassis(device, key):
    """Chassis whose cached scan record marks ``key`` present.

    ``key`` is a collection flag or count recorded by ``nodetools scan``. A
    ``None`` result means there is no profile, so callers fall back to live
    discovery instead of assuming the BMC has nothing.
    """
    prof = getattr(device, "profile", None)
    if not prof:
        return None
    caps = prof.get("caps") or {}
    out = []
    selected = getattr(device, "vars", {}).get("chassis_id")
    for cid, rec in caps.items():
        if selected and cid != selected:
            continue
        value = rec.get(key)
        if isinstance(value, bool):
            if value:
                out.append(cid)
        elif isinstance(value, int) and value > 0:
            out.append(cid)
    return out


def rung_chassis(device, key, defaults=()):
    """Chassis to walk for one ladder rung, using the scan cache when present.

    With a cached profile an empty result means "this BMC exposes none of
    this", which lets the rung short-circuit; without one the live chassis list
    is returned so behaviour is unchanged.
    """
    targets = cached_chassis(device, key)
    if targets is None:
        return chassis_list(device, defaults)
    return targets


def flatten(groups):
    """Concatenate a list of lists, preserving order."""
    out = []
    for group in groups:
        out.extend(group)
    return out


def map_chassis(func, chassis, workers=SCAN_WORKERS):
    """Run ``func(chassis)`` for every chassis, in parallel, order-preserving."""
    chassis = list(chassis)
    if len(chassis) <= 1:
        return [func(c) for c in chassis]
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(workers, len(chassis))
    ) as pool:
        return list(pool.map(func, chassis))


def _inlined(member):
    return isinstance(member, dict) and len(member) > 1


def enumerate_collection(redfish, path, expand_only=False):
    """Return detail dicts for every member of a Redfish collection.

    ``$expand=.`` inlines a whole collection in one request when the BMC
    supports it. ``expand_only`` forces that path: large vendor collections
    (Sensors) can be thousands of entries, and fetching them one-by-one is too
    slow, so when expansion is unavailable they are skipped rather than walked.
    """
    sep = "&" if "?" in path else "?"
    data = redfish.get(path + sep + "$expand=.")
    members = data.get("Members") if isinstance(data, dict) else None
    if not isinstance(members, list):
        data = redfish.get(path)
        members = data.get("Members") if isinstance(data, dict) else None
    if not isinstance(members, list):
        return []
    details = []
    for member in members:
        if not isinstance(member, dict):
            continue
        if _inlined(member):
            details.append(member)
            continue
        if expand_only:
            continue
        oid = member.get("@odata.id")
        if not oid:
            continue
        detail = redfish.get(oid)
        if isinstance(detail, dict):
            details.append(detail)
    return details


def enumerate_sensors(redfish, chassis):
    """Return every Sensor under a chassis, ``$expand=.`` only.

    Sensors is the collection that motivated the policy: entry counts vary
    wildly across vendors, so it is never walked member by member.
    """
    return enumerate_collection(
        redfish, "/redfish/v1/Chassis/{}/Sensors".format(chassis), expand_only=True
    )
