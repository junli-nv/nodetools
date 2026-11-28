"""Collect inlet temperature, node power, and PSU input readings."""

import math
import re

from ..errors import NodetoolsError
from . import common, inlet, psu

_NODE_POWER = {"totalpower", "systempowercontrol", "systempowerconsumption", "systempower"}
_INPUT_SLOT = re.compile(r"(?:powersupply|psu|ps)(?:slot|bay)?(\d+)")


def _key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if not isinstance(value, bool) and math.isfinite(number) else None


def _node_power(items, field, source, names=_NODE_POWER):
    for item in items:
        value = _number(item.get(field))
        name = item.get("Name") or item.get("Id")
        if value is not None and _key(name) in names:
            return dict(kind="node_power", id=item.get("Id") or name, name=name,
                        value=value, unit="W", source=item.get("@odata.id") or source)
    return None


def _power(device):
    rf = device.redfish
    selected = device.vars.get("chassis_id") or getattr(device.odm, "monitor_chassis", None)
    sensor_id = getattr(device.odm, "monitor_power_sensor", None)
    sensor_chassis = getattr(device.odm, "monitor_power_chassis", None) or selected
    if sensor_id and sensor_chassis and (not selected or sensor_chassis == selected):
        path = "/redfish/v1/Chassis/{}/Sensors/{}".format(sensor_chassis, sensor_id)
        sensor = rf.get(path) or {}
        if (sensor.get("ReadingType") == "Power" and sensor.get("Id") == sensor_id
                and (not getattr(device.odm, "monitor_power_unit", None)
                     or sensor.get("ReadingUnits") == device.odm.monitor_power_unit)
                and sensor.get("@odata.id", path) == path):
            value = _node_power([sensor], "Reading", path, {_key(sensor.get("Name") or sensor_id)})
            if value is not None:
                return [value]
    suffix = getattr(device.odm, "monitor_sensor_collection", None)
    control_name = getattr(device.odm, "monitor_power_control", None)
    names = _NODE_POWER | {_key(control_name)} if control_name else _NODE_POWER
    def read_power(cid):
        base = "/redfish/v1/Chassis/{}".format(cid)
        path = base + "/Power"
        controls = (rf.get(path) or {}).get("PowerControl") or []
        value = _node_power(controls, "PowerConsumedWatts", path, names)
        if value is None and control_name:
            averages = (
                dict(item, average=(item.get("PowerMetrics") or {}).get("AverageConsumedWatts"))
                for item in controls if item.get("Name") == control_name
            )
            value = _node_power(averages, "average", path, names)
            if value is not None:
                value["aggregation"] = "average"
        if value is None and suffix:
            path = base + suffix
            value = _node_power((rf.get(path) or {}).get("Sensors") or [], "Reading", path)
        return value

    preferred = None if selected else getattr(device.odm, "monitor_preferred_chassis", None)
    if preferred:
        value = read_power(preferred)
        if value is not None:
            return [value]
    chassis = [selected] if selected else common.chassis_list(device, ("1",))
    for cid in chassis:
        if cid != preferred:
            value = read_power(cid)
            if value is not None:
                return [value]

    for cid in chassis:
        sensors = (sensor for sensor in common.enumerate_sensors(rf, cid)
                   if str(sensor.get("ReadingType", "")).lower() == "power")
        value = _node_power(sensors, "Reading", "/redfish/v1/Chassis/{}/Sensors".format(cid))
        if value is not None:
            return [value]
    return []


def estimate_settings(variables, options):
    values = []
    for key, option, maximum in (
        ("psu_input_volts", "psu_voltage", None),
        ("psu_input_power_factor", "psu_power_factor", 1),
    ):
        raw = getattr(options, option, None)
        if raw is None:
            raw = variables.get(key)
        value = _number(raw)
        if raw is not None and (value is None or value <= 0 or (maximum is not None and value > maximum)):
            raise NodetoolsError("{} must be {}".format(
                key, "a finite positive voltage" if maximum is None else "greater than 0 and at most 1",
            ))
        values.append(value)
    return tuple(values)


def _estimate_watts(entry, settings):
    configured_volts, configured_factor = settings
    factor = _number(entry.get("input_power_factor"))
    if factor is None or not 0 <= factor <= 1:
        factor = configured_factor
    va = _number(entry.get("input_apparent_va"))
    if va is not None and va >= 0 and factor is not None:
        return _number(va * factor)
    volts = _number(entry.get("input_volts"))
    amps = _number(entry.get("input_amps"))
    if volts is None:
        nominal = re.fullmatch(r"(?:AC|DC(?:NEG)?)([1-9]\d*)V", entry.get("input_voltage_type") or "")
        volts = configured_volts if configured_volts is not None else float(nominal[1]) if nominal else None
    if volts is None or amps is None or volts == 0 or amps < 0:
        return None
    if entry.get("input_is_dc"):
        return _number(abs(volts) * amps)
    phase = entry.get("input_phase_wiring")
    if (phase == "OnePhase3Wire" or (not phase and configured_factor is not None)) and volts > 0 and factor is not None:
        return _number(volts * amps * factor)
    return None


def _hsc_supplies(device, sensors):
    chassis = (getattr(device.odm, "monitor_hsc_chassis", None)
               or device.vars.get("chassis_id") or device.odm.monitor_chassis)
    base = "/redfish/v1/Chassis/{}/Sensors/".format(chassis)
    readings = []
    for slot, (sensor_id, name) in sensors.items():
        path = base + sensor_id
        sensor = device.redfish.get(path) or {}
        watts = None
        valid = sensor.get("Name") == name and sensor.get("ReadingType") == "Power"
        if getattr(device.odm, "monitor_reading_ids", ()):
            valid = (valid and sensor.get("Id") == sensor_id and sensor.get("ReadingUnits") == "W"
                     and sensor.get("@odata.id", path) == path)
        if valid:
            watts = _number(sensor.get("Reading"))
        readings.append(dict(kind="psu", id=slot, name=name, value=watts, unit="W",
                             estimated=False, source=path, health=common.health_of(sensor.get("Status"))))
    return readings


def _supplies(device):
    sensors = getattr(getattr(device, "odm", None), "monitor_hsc_sensors", None)
    if sensors:
        return _hsc_supplies(device, sensors)
    settings = estimate_settings(getattr(device, "vars", {}), getattr(device, "options", None))
    slots = {}
    for entry in psu.collect(device):
        slot = str(entry["id"])
        watts = _number(entry.get("input_watts"))
        if "input_watts" not in entry:
            name = _key(entry["name"])
            match = _INPUT_SLOT.search(name) or _INPUT_SLOT.search(_key(slot))
            unit = str(entry.get("unit") or "").lower()
            if match is None or "input" not in name or unit not in ("w", "watt", "watts", "a", "amp", "amps"):
                continue
            slot = match[1]
            if unit in ("a", "amp", "amps"):
                entry = dict(entry, input_amps=entry["value"])
            else:
                watts = _number(entry["value"])

        estimated = False
        if watts is None:
            watts = _estimate_watts(entry, settings)
            estimated = watts is not None
        previous = slots.get(slot)
        if previous and (watts is None or (previous["value"] is not None and
                                            (estimated or not previous["estimated"]))):
            continue
        slots[slot] = dict(kind="psu", id=slot, name=entry["name"], value=watts,
                           unit="W", estimated=estimated,
                           source=entry.get("source"), health=entry["health"])
    return list(slots.values())


def _inlet(device):
    return [dict(entry, kind="inlet", unit="°C", value=_number(entry.get("value")))
            for entry in inlet.collect(device)]


def collect(device):
    readings = []
    failures = []
    for reader in (_power, _inlet, _supplies):
        try:
            readings.extend(reader(device))
        except NodetoolsError as exc:
            failures.append(str(exc))
    expected = getattr(device.odm, "monitor_reading_ids", ())
    if expected and not device.vars.get("chassis_id"):
        valid = {row["id"] for row in readings if _number(row.get("value")) is not None}
        missing = sorted(set(expected) - valid)
        if missing:
            raise NodetoolsError("incomplete monitor data: unavailable readings=" + ", ".join(missing))
    if failures and not readings:
        raise NodetoolsError("; ".join(failures))
    return readings
