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


def _node_power(items, field, source):
    for item in items:
        value = _number(item.get(field))
        name = item.get("Name") or item.get("Id")
        if value is not None and _key(name) in _NODE_POWER:
            return dict(kind="node_power", id=item.get("Id") or name, name=name,
                        value=value, unit="W", source=item.get("@odata.id") or source)
    return None


def _power(device):
    rf = device.redfish
    selected = device.vars.get("chassis_id")
    chassis = [selected] if selected else common.chassis_list(device, ("1",))
    suffix = getattr(device.odm, "monitor_sensor_collection", None)
    for cid in chassis:
        base = "/redfish/v1/Chassis/{}".format(cid)
        path = base + "/Power"
        value = _node_power((rf.get(path) or {}).get("PowerControl") or [],
                            "PowerConsumedWatts", path)
        if value is None and suffix:
            path = base + suffix
            value = _node_power((rf.get(path) or {}).get("Sensors") or [], "Reading", path)
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


def _supplies(device):
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
    if failures and not readings:
        raise NodetoolsError("; ".join(failures))
    return readings
