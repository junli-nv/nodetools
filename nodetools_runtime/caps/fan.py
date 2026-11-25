"""Fan capability: RPM/percent readings with ODM-aware fallbacks."""

from ..config import DEFAULT_CHASSIS
from ..ladder import first
from . import common


def _speed_unit(data):
    units = str(data.get("ReadingUnits") or data.get("ReadingUnit") or "").lower()
    if units in ("%", "percent", "percentage"):
        return "%"
    if units in ("rpm", "r/min", "rev/min"):
        return "RPM"
    return None


def _fan_unit(detail):
    speed = detail.get("SpeedPercent")
    if isinstance(speed, dict):
        if speed.get("SpeedRPM") is not None or speed.get("SpeedRpm") is not None:
            return "RPM"
        if speed.get("Reading") is not None:
            return "%"
    return _speed_unit(detail) or "RPM"


def _from_thermal_subsystem(device):
    rf = device.redfish

    def scan(cid):
        out = []
        for detail in common.enumerate_collection(
            rf, "/redfish/v1/Chassis/{}/ThermalSubsystem/Fans".format(cid)
        ):
            out.append(
                common.reading(
                    detail.get("MemberId") or detail.get("Id"),
                    detail.get("Name"),
                    common.fan_speed(detail),
                    common.health_of(detail.get("Status")),
                    unit=_fan_unit(detail),
                )
            )
        return out

    chassis = common.rung_chassis(device, "Fans", DEFAULT_CHASSIS)
    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_thermal(device, chassis=None, sensor_numbers=False):
    rf = device.redfish

    def scan(cid):
        out = []
        thermal = rf.get("/redfish/v1/Chassis/{}/Thermal".format(cid))
        fans = (thermal or {}).get("Fans") or []
        if sensor_numbers:
            ids = [fan.get("SensorNumber") for fan in fans]
            if not all(isinstance(rid, int) for rid in ids) or len(set(ids)) != len(ids):
                return []
            fans = sorted(fans, key=lambda fan: str(fan["SensorNumber"]))
        for fan in fans:
            ident = (str(fan["SensorNumber"]) if sensor_numbers
                     else fan.get("MemberId") or fan.get("FanName") or fan.get("Id"))
            out.append(
                common.reading(
                    ident,
                    fan.get("Name") or fan.get("FanName"),
                    common.fan_speed(fan),
                    common.health_of(fan.get("Status")),
                    unit=_fan_unit(fan),
                )
            )
        return out

    if chassis is None:
        chassis = common.rung_chassis(device, "Thermal", DEFAULT_CHASSIS)
    if not chassis:
        return []
    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_sensors(device):
    rf = device.redfish

    def scan(cid):
        out = []
        for sensor in common.enumerate_sensors(rf, cid):
            name = sensor.get("Name") or sensor.get("Id") or ""
            if "fan" not in name.lower():
                continue
            out.append(
                common.reading(
                    sensor.get("Id"),
                    name,
                    common.num(sensor.get("Reading")),
                    common.health_of(sensor.get("Status")),
                    unit=_speed_unit(sensor),
                )
            )
        return out

    chassis = common.rung_chassis(device, "Sensors", DEFAULT_CHASSIS)
    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_ipmi(device):
    if not device.ipmi:
        return []
    out = []
    for line in device.ipmi.run("sdr", "type", "Fan").splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 3:
            continue
        name = fields[0]
        health = fields[2]
        value = fields[-1] if len(fields) >= 5 else fields[1]
        if "fan" not in name.lower() and "rpm" not in (name + value).lower():
            continue
        health = {"ok": "OK", "ns": "Unknown", "na": "Unknown"}.get(health.lower(), health)
        out.append(common.reading(name, name, common.num(value), health, unit="RPM"))
    return out


def collect(device):
    steps = []
    odm = getattr(device, "odm", None)
    preferred = getattr(odm, "fan_read_chassis", ())
    if preferred:
        selected = device.vars.get("chassis_id")
        if selected and "fan" in getattr(odm, "inventory_fixed_caps", ()):
            preferred = (selected,)
        sensor_numbers = getattr(odm, "fan_thermal_sensor_numbers", False)
        steps.append(("oem_thermal", lambda: _from_thermal(device, preferred, sensor_numbers)))
    steps += [
        ("thermal_subsystem", lambda: _from_thermal_subsystem(device)),
        ("thermal", lambda: _from_thermal(device)),
        ("sensors", lambda: _from_sensors(device)),
        ("ipmi", lambda: _from_ipmi(device)),
    ]
    return first(steps, default=[])
