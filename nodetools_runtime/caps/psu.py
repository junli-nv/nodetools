"""PSU capability: health plus input/output power for trays and shelves.

The PSU list, and every chassis/system id, is discovered from the BMC at run
time. Readings come from whichever Redfish shape the vendor exposes
(``PowerSubsystem/PowerSupplies`` with a per-supply ``Metrics``, the legacy
``Power`` object, or ``Sensors``), never from a hardcoded path.
"""

from ..config import DEFAULT_CHASSIS
from ..ladder import first
from . import common

_PSU_NAME_HINTS = ("psu", "hsc", "pwr", "power supply")


def _metric(metrics, key):
    entry = metrics.get(key)
    if isinstance(entry, dict):
        for k in ("Reading", "SpeedRPM"):
            if entry.get(k) is not None:
                return common.num(entry[k])
        return None
    if isinstance(entry, list) and entry and isinstance(entry[0], dict):
        return common.num(entry[0].get("Reading"))
    return None


def _wire_value(metrics, detail):
    """Compose ``in=<x>W/out=<y>W`` (falling back to current) for one supply."""
    in_w = _metric(metrics, "InputPowerWatts")
    if in_w is None:
        in_w = common.num(metrics.get("InputWatts") or detail.get("InputWatts"))
    out_w = _metric(metrics, "OutputPowerWatts")
    if out_w is None:
        out_w = common.num(
            metrics.get("AverageConsumedWatts") or metrics.get("PowerConsumedWatts")
        )
    parts = []
    if in_w is not None:
        parts.append("in={}W".format(in_w))
    if out_w is not None:
        parts.append("out={}W".format(out_w))
    if not parts:
        for label, key in (("in", "InputCurrentAmps"), ("out", "OutputCurrentAmps")):
            amp = _metric(metrics, key)
            if amp is not None:
                parts.append("{}={}A".format(label, amp))
    return "/".join(parts) if parts else None


def _psu_health(detail):
    status = detail.get("Status") or {}
    if status.get("Health"):
        return status["Health"]
    oem = (detail.get("Oem") or {}).get("deltaenergysystems") or {}
    if isinstance(oem.get("Power"), bool):
        return "On" if oem["Power"] else "Off"
    return common.health_of(status)


def _metrics_for(redfish, detail):
    metrics = detail.get("PowerMetrics")
    if isinstance(metrics, dict) and metrics:
        return metrics
    link = (detail.get("Metrics") or {}).get("@odata.id")
    if link:
        fetched = redfish.get(link)
        if isinstance(fetched, dict):
            return fetched
    return {}


def _supply_reading(redfish, detail):
    return common.reading(
        detail.get("MemberId") or detail.get("Id"),
        detail.get("Name"),
        _wire_value(_metrics_for(redfish, detail), detail),
        _psu_health(detail),
    )


def _from_power_supplies(device):
    rf = device.redfish
    chassis = common.rung_chassis(device, "PowerSupplies", DEFAULT_CHASSIS)
    if not chassis:
        return []
    prof = common.profile(device)
    if prof is None:
        use_power = not common.absent(
            device, "/redfish/v1/Chassis/{}/Power".format(chassis[0])
        )
    else:
        caps = prof.get("caps") or {}
        use_power = any((caps.get(cid) or {}).get("Power") for cid in chassis)

    def scan(cid):
        out = []
        base = "/redfish/v1/Chassis/{}".format(cid)
        for detail in common.enumerate_collection(
            rf, base + "/PowerSubsystem/PowerSupplies"
        ):
            out.append(_supply_reading(rf, detail))
        if use_power:
            legacy = rf.get(base + "/Power")
            if isinstance(legacy, dict):
                for detail in legacy.get("PowerSupplies", []) or []:
                    if isinstance(detail, dict):
                        out.append(_supply_reading(rf, detail))
        return out

    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_sensors(device):
    rf = device.redfish
    chassis = common.rung_chassis(device, "Sensors", DEFAULT_CHASSIS)

    def scan(cid):
        out = []
        for sensor in common.enumerate_sensors(rf, cid):
            name = (sensor.get("Name") or sensor.get("Id") or "").lower()
            if not any(hint in name for hint in _PSU_NAME_HINTS):
                continue
            out.append(
                common.reading(
                    sensor.get("Id"),
                    sensor.get("Name"),
                    common.num(sensor.get("Reading")),
                    common.health_of(sensor.get("Status")),
                )
            )
        return out

    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_delta_all_sensors(device):
    rf = device.redfish

    def scan(cid):
        data = rf.get(
            "/redfish/v1/Chassis/{}/Oem/deltaenergysystems/AllSensors".format(cid)
        )
        if not isinstance(data, dict):
            return []
        out = []
        for sensor in data.get("Sensors", []) or []:
            dtype = (sensor.get("ReadingType") or "").lower()
            name = (sensor.get("DeviceName") or sensor.get("SensorName") or "").lower()
            if "power" not in dtype and not any(h in name for h in _PSU_NAME_HINTS):
                continue
            out.append(
                common.reading(
                    sensor.get("SensorName") or sensor.get("DeviceName"),
                    sensor.get("DeviceName"),
                    common.num(sensor.get("Reading")),
                    sensor.get("State") or "Unknown",
                )
            )
        return out

    chassis = common.chassis_list(device, DEFAULT_CHASSIS)
    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_ipmi(device):
    if not device.ipmi:
        return []
    out = []
    for name, value in device.ipmi.sdr("Power Supply").items():
        out.append(common.reading(name, name, common.num(value)))
    return out


def collect(device):
    steps = [("power_supplies", lambda: _from_power_supplies(device))]
    if device.role == "powershelf":
        steps.append(("delta_all_sensors", lambda: _from_delta_all_sensors(device)))
    steps += [
        ("sensors", lambda: _from_sensors(device)),
        ("ipmi", lambda: _from_ipmi(device)),
    ]
    return first(steps, default=[])
