"""PSU capability: health plus input/output power for trays and shelves.

Known OEM sensor layouts can be read directly. Otherwise the PSU list and
chassis IDs are discovered from the BMC. Readings use the vendor's Redfish shape
(``PowerSubsystem/PowerSupplies`` with a per-supply ``Metrics``, the legacy
``Power`` object, or ``Sensors``).
"""

import math

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
    return common.num(entry)


def _first_numeric(*values):
    for value in values:
        number = common.num(value)
        if number is not None:
            return number
    return None


def _power_watts(metrics, detail):
    return (
        _first_numeric(
            _metric(metrics, "InputPowerWatts"),
            detail.get("PowerInputWatts"),
            metrics.get("InputWatts"),
            detail.get("InputWatts"),
        ),
        _first_numeric(
            _metric(metrics, "OutputPowerWatts"),
            detail.get("LastPowerOutputWatts"),
            metrics.get("AverageConsumedWatts"),
            metrics.get("PowerConsumedWatts"),
        ),
    )


def _wire_value(metrics, in_w, out_w):
    """Compose input/output power, falling back to current when absent."""
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
    metrics = _metrics_for(redfish, detail)
    in_w, out_w = _power_watts(metrics, detail)
    input_power = metrics.get("InputPowerWatts")
    if not isinstance(input_power, dict):
        input_power = {}
    nominal = str(detail.get("InputNominalVoltageType") or "").upper()
    input_type = str(detail.get("PowerSupplyType") or "").upper()
    return common.reading(
        detail.get("MemberId") or detail.get("Id"),
        detail.get("Name"),
        _wire_value(metrics, in_w, out_w),
        _psu_health(detail),
        manufacturer=detail.get("Manufacturer"),
        model=detail.get("Model"),
        firmware=detail.get("FirmwareVersion"),
        source=detail.get("@odata.id"),
        input_amps=_first_numeric(_metric(metrics, "InputCurrentAmps"), detail.get("InputCurrentAmps")),
        input_volts=_first_numeric(_metric(metrics, "InputVoltage"), detail.get("LineInputVoltage")),
        input_apparent_va=common.num(input_power.get("ApparentVA")),
        input_power_factor=common.num(input_power.get("PowerFactor")),
        input_is_dc=nominal.startswith("DC") or (not nominal and input_type == "DC"),
        input_voltage_type=nominal,
        input_phase_wiring=detail.get("PhaseWiringType"),
        input_watts=in_w,
        output_watts=out_w,
    )


def _supply_serial(detail):
    serial = str(detail.get("SerialNumber") or "").strip()
    if serial.casefold() in ("", "0", "unknown", "n/a", "na", "none", "null", "not available"):
        return None
    return serial


def _unique_supplies(details):
    """Merge physical PSU aliases before sampling metrics; never key on watts."""
    out, seen = [], {}
    for detail in details:
        serial = _supply_serial(detail)
        resource = detail.get("@odata.id")
        key = (("serial", str(detail.get("Manufacturer") or "").casefold(), serial) if serial
               else ("resource", resource) if resource else None)
        if key is None or key not in seen:
            if key is not None:
                seen[key] = len(out)
            out.append(dict(detail))
            continue
        combined = out[seen[key]]
        if detail.get("Id") and "MemberId" not in detail:
            combined.pop("MemberId", None)
        combined.update({k: v for k, v in detail.items() if v not in (None, "", [], {})})
    return out


def _from_power_supplies(device, endpoint=None):
    rf = device.redfish
    if endpoint:
        chassis_id, backend = endpoint
        chassis_id = device.vars.get("chassis_id") or chassis_id
        modern = [chassis_id] if backend in ("modern", "both") else []
        legacy = [chassis_id] if backend in ("legacy", "both") else []
    else:
        modern = common.cached_chassis(device, "PowerSupplies")
        legacy = common.cached_chassis(device, "Power")
        selected = getattr(device, "vars", {}).get("chassis_id")
        preferred = (selected,) if selected else getattr(getattr(device, "odm", None), "psu_read_chassis", ())
        if modern is None:
            modern = list(preferred) if preferred else common.chassis_list(device, DEFAULT_CHASSIS)
            legacy = modern
        elif preferred:
            modern = [cid for cid in modern if cid in preferred]
            legacy = [cid for cid in legacy if cid in preferred]
    chassis = list(dict.fromkeys(modern + legacy))

    def scan(cid):
        base = "/redfish/v1/Chassis/{}".format(cid)
        current = common.enumerate_collection(rf, base + "/PowerSubsystem/PowerSupplies") if cid in modern else []
        old = []
        if cid in legacy:
            power = rf.get(base + "/Power")
            if isinstance(power, dict):
                old = [item for item in power.get("PowerSupplies", []) or [] if isinstance(item, dict)]
        old_by_serial = {_supply_serial(item): item for item in old if _supply_serial(item)}
        matched = set()
        out = []
        for detail in current:
            serial = _supply_serial(detail)
            if serial in old_by_serial:
                matched.add(serial)
                combined = dict(old_by_serial[serial])
                if detail.get("Id") and "MemberId" not in detail:
                    combined.pop("MemberId", None)
                combined.update({key: value for key, value in detail.items() if value not in (None, "")})
                detail = combined
            out.append(detail)
        out.extend(detail for detail in old if _supply_serial(detail) not in matched)
        return out


    details = _unique_supplies(common.flatten(common.map_chassis(scan, chassis)))
    return [_supply_reading(rf, detail) for detail in details]


def _from_sensors(device, chassis=None):
    rf = device.redfish
    if chassis is None:
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
                    unit=sensor.get("ReadingUnits"),
                    source=sensor.get("@odata.id"),
                )
            )
        return out

    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_ipmi(device):
    if not device.ipmi:
        return []
    out = []
    for name, value in device.ipmi.sdr("Power Supply").items():
        out.append(common.reading(name, name, common.num(value)))
    return out


def _from_known_sensors(device, expected):
    rows = _from_sensors(device, expected)
    if len(rows) != sum(expected.values()) or len({row["id"] for row in rows}) != len(rows):
        return []
    if any(not row["id"] or row["value"] is None or not row.get("source") for row in rows):
        return []
    for chassis, count in expected.items():
        prefix = f"/redfish/v1/Chassis/{chassis}/Sensors/"
        if sum(row["source"].startswith(prefix) for row in rows) != count:
            return []
    return rows


def _from_direct_sensors(device, sensors):
    """Read a verified set of DC input sensors without collection discovery."""
    selected = device.vars.get("chassis_id")
    if selected and any(chassis != selected for chassis, _, _ in sensors):
        return []
    rows = []
    valid = True
    for chassis, sensor_id, label in sensors:
        path = "/redfish/v1/Chassis/{}/Sensors/{}".format(chassis, sensor_id)
        data = device.redfish.get(path)
        if not isinstance(data, dict):
            valid = False
            continue
        value = data.get("Reading")
        if (data.get("Id") != sensor_id or data.get("@odata.id", path) != path
                or data.get("ReadingType") != "Power"
                or data.get("ReadingUnits") != "W" or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value)):
            valid = False
            continue
        rows.append(common.reading(
            label, data.get("Name"), "in={}W".format(value),
            common.health_of(data.get("Status")), unit="W", source=path,
            input_watts=value, input_is_dc=True,
        ))
    return rows if valid and len(rows) == len(sensors) else []


def _from_legacy_slots(device, specification):
    """Use verified stable MemberIds, never array positions or changing watts."""
    import math
    chassis, prefix, count = specification
    if device.vars.get("chassis_id") not in (None, chassis):
        return []
    path = "/redfish/v1/Chassis/" + chassis + "/Power"
    data = device.redfish.get(path)
    if not isinstance(data, dict) or data.get("@odata.id", path) != path:
        return []
    supplies = data.get("PowerSupplies")
    if not isinstance(supplies, list) or len(supplies) != count:
        return []
    expected = {prefix + str(i): str(i) for i in range(count)}
    found = {}
    for supply in supplies:
        if not isinstance(supply, dict):
            return []
        ident = supply.get("MemberId")
        value = supply.get("PowerInputWatts")
        if (ident not in expected or ident in found
                or not isinstance(value, (int, float)) or isinstance(value, bool)
                or not math.isfinite(value) or value < 0):
            return []
        if supply.get("Metrics"):
            return []
        slot = expected[ident]
        detail = dict(supply, MemberId=slot, Id=slot)
        detail.setdefault("@odata.id", path + "#/PowerSupplies/" + ident)
        found[ident] = _supply_reading(device.redfish, detail)
    if found.keys() != expected.keys():
        return []
    return [found[ident] for ident in expected]


def collect(device):
    steps = []
    legacy_slots = getattr(getattr(device, "odm", None), "psu_legacy_slots", None)
    if legacy_slots and not getattr(getattr(device, "options", None), "details", False):
        steps.append(("oem_legacy_slots", lambda: _from_legacy_slots(device, legacy_slots)))
    direct = getattr(getattr(device, "odm", None), "psu_read_sensors", ())
    if direct:
        steps.append(("oem_direct_sensors", lambda: _from_direct_sensors(device, direct)))
    expected = getattr(getattr(device, "odm", None), "psu_sensor_chassis", None)
    if expected:
        selected = device.vars.get("chassis_id")
        if selected:
            expected = {selected: expected[selected]} if selected in expected else None
        if expected:
            steps.append(("oem_psu_sensors", lambda: _from_known_sensors(device, expected)))
    endpoint = getattr(getattr(device, "odm", None), "psu_read_endpoint", None)
    if endpoint:
        steps.append(("oem_power_supplies", lambda: _from_power_supplies(device, endpoint)))
    steps.append(("power_supplies", lambda: _from_power_supplies(device)))
    if device.role == "powershelf":
        from ..oem import powershelf_psu_fallback

        steps.append(("powershelf_oem", lambda: powershelf_psu_fallback(device)))
    steps += [
        ("sensors", lambda: _from_sensors(device)),
        ("ipmi", lambda: _from_ipmi(device)),
    ]
    return first(steps, default=[])
