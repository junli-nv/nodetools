"""Fan capability: RPM/percent readings with ODM-aware fallbacks."""

from ..config import DEFAULT_CHASSIS
from ..ladder import first
from . import common


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
                )
            )
        return out

    chassis = common.rung_chassis(device, "Fans", DEFAULT_CHASSIS)
    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_thermal(device):
    rf = device.redfish

    def scan(cid):
        out = []
        thermal = rf.get("/redfish/v1/Chassis/{}/Thermal".format(cid))
        for fan in (thermal or {}).get("Fans", []) or []:
            out.append(
                common.reading(
                    fan.get("MemberId") or fan.get("FanName") or fan.get("Id"),
                    fan.get("Name") or fan.get("FanName"),
                    common.num(fan.get("Reading") or fan.get("ReadingRPM")),
                    common.health_of(fan.get("Status")),
                )
            )
        return out

    chassis = common.rung_chassis(device, "Thermal", DEFAULT_CHASSIS)
    if not chassis:
        return []
    if common.profile(device) is None and common.absent(
        device, "/redfish/v1/Chassis/{}/Thermal".format(chassis[0])
    ):
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
                )
            )
        return out

    chassis = common.rung_chassis(device, "Sensors", DEFAULT_CHASSIS)
    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_ipmi(device):
    if not device.ipmi:
        return []
    readings = device.ipmi.sdr("Fan")
    out = []
    for name, value in readings.items():
        if "fan" not in name.lower() and "rpm" not in name.lower():
            continue
        out.append(common.reading(name, name, common.num(value)))
    return out


def collect(device):
    steps = [
        ("thermal_subsystem", lambda: _from_thermal_subsystem(device)),
        ("thermal", lambda: _from_thermal(device)),
        ("sensors", lambda: _from_sensors(device)),
        ("ipmi", lambda: _from_ipmi(device)),
    ]
    return first(steps, default=[])
