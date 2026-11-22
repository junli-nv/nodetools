"""Probe and cache each BMC's Redfish interface map.

Scanning a GB200 BMC means walking every chassis (dozens of them) to learn
which collections exist and how many members each holds. Doing that on every
``fan``/``psu``/``temp`` run is the single biggest cost, so ``nodetools scan``
records it once per BMC and the collectors then read the cache and only touch
the chassis that can actually answer. Re-run ``scan`` to refresh an entry for a
machine that changed, or ``scan --clear`` to drop it.
"""

import concurrent.futures
import json
import os
import time

from . import model
from .caps import common
from .config import SCAN_CACHE_DIR, SCAN_CACHE_VERSION

# Member counts recorded per chassis (0 when the collection is absent/empty).
# Counts come from the collection endpoints directly rather than from what the
# chassis detail advertises: some vendors (Wistron) expose a Sensors collection
# that the detail object never lists, so trusting the detail silently drops it.
COUNTS = ("Sensors", "Fans", "PowerSupplies", "Temperatures")

# Legacy singleton objects, recorded as booleans (almost always absent on
# OpenBMC, but still present on some vendors).
OBJECTS = ("Thermal", "Power")


def cache_path(address):
    safe = (address or "unknown").replace("/", "_").replace(":", "_")
    return os.path.join(SCAN_CACHE_DIR, safe + ".json")


def load(address):
    if not address:
        return None
    try:
        with open(cache_path(address), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != SCAN_CACHE_VERSION:
        return None
    return data


def store(address, profile):
    if not address:
        return None
    os.makedirs(SCAN_CACHE_DIR, exist_ok=True)
    path = cache_path(address)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, sort_keys=True)
    os.replace(tmp, path)
    return path


def clear(address):
    try:
        os.remove(cache_path(address))
        return True
    except OSError:
        return False


def _count(redfish, path):
    """Member count from a collection without expanding it."""
    data = redfish.get(path)
    if not isinstance(data, dict):
        return 0
    count = data.get("Members@odata.count")
    if isinstance(count, int):
        return count
    members = data.get("Members")
    return len(members) if isinstance(members, list) else 0


def _shown(redfish, path):
    """True when an endpoint exists (a Redfish error is itself a JSON object)."""
    data = redfish.get(path)
    return isinstance(data, dict) and "error" not in data


def _chassis_caps(redfish, cid):
    base = "/redfish/v1/Chassis/" + cid
    rec = {
        "Sensors": _count(redfish, base + "/Sensors"),
        "Fans": _count(redfish, base + "/ThermalSubsystem/Fans"),
        "PowerSupplies": _count(redfish, base + "/PowerSubsystem/PowerSupplies"),
    }
    metrics = redfish.get(base + "/ThermalSubsystem/ThermalMetrics")
    readings = (
        metrics.get("TemperatureReadingsCelsius")
        if isinstance(metrics, dict)
        else None
    )
    rec["Temperatures"] = len(readings) if isinstance(readings, list) else 0
    for obj in OBJECTS:
        rec[obj] = _shown(redfish, base + "/" + obj)
    return rec


def probe(device, workers=common.SCAN_WORKERS):
    """Walk a BMC and build its interface map (does not touch the cache)."""
    redfish = device.redfish
    root = redfish.get("/redfish/v1")
    vendor = None
    if isinstance(root, dict):
        vendor = root.get("Vendor") or (root.get("Oem") or {}).get("Vendor")
    model_name = manufacturer = None
    for collection in ("/redfish/v1/Managers", "/redfish/v1/Systems"):
        for oid in common.member_ids(redfish.get(collection)):
            data = redfish.get(oid)
            if isinstance(data, dict):
                model_name = model_name or data.get("Model")
                manufacturer = manufacturer or data.get("Manufacturer")
    chassis = common.discover_chassis(redfish)
    caps = {}

    def one(cid):
        return cid, _chassis_caps(redfish, cid)

    if chassis:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, len(chassis))
        ) as pool:
            for cid, rec in pool.map(one, chassis):
                caps[cid] = rec
    profile = {
        "version": SCAN_CACHE_VERSION,
        "address": device.address,
        "scanned": time.time(),
        "vendor": vendor,
        "model": model_name,
        "manufacturer": manufacturer,
        "chassis": chassis,
        "caps": caps,
    }
    profile["odm"] = model.classify(profile)
    return profile


def refresh(device, workers=common.SCAN_WORKERS):
    """Probe and write/update the cached map for this BMC."""
    profile = probe(device, workers=workers)
    store(device.address, profile)
    return profile
