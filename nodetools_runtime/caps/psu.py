"""PSU capability: state and input power for compute trays and power shelves."""

from ..config import DEFAULT_CHASSIS
from ..ladder import first
from . import common

_PSU_NAME_HINTS = ("psu", "hsc", "pwr", "power supply")


def _psu_value(detail):
    metrics = detail.get("PowerMetrics") or {}
    for key in ("InputWatts", "AverageConsumedWatts", "PowerConsumedWatts"):
        value = metrics.get(key, detail.get(key))
        if value is not None:
            return common.num(value)
    return None


def _psu_health(detail):
    oem = (detail.get("Oem") or {}).get("deltaenergysystems") or {}
    if "Power" in oem and isinstance(oem["Power"], bool):
        return "On" if oem["Power"] else "Off"
    return common.health_of(detail.get("Status"))


def _from_power_supplies(device):
    rf = device.redfish
    out = []
    for chassis in common.chassis_list(device, DEFAULT_CHASSIS) + ["PowerShelf_0"]:
        for detail in common.enumerate_collection(
            rf,
            "/redfish/v1/Chassis/{}/PowerSubsystem/PowerSupplies".format(chassis),
        ):
            out.append(
                common.reading(
                    detail.get("MemberId") or detail.get("Id"),
                    detail.get("Name"),
                    _psu_value(detail),
                    _psu_health(detail),
                )
            )
        if out:
            return out
    return out


def _from_sensors(device):
    rf = device.redfish
    out = []
    for chassis in common.chassis_list(device, DEFAULT_CHASSIS) + [
        "PowerShelf_0",
        "PDB_0",
    ]:
        for sensor in common.enumerate_sensors(rf, chassis):
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
        if out:
            return out
    return out


def _from_delta_all_sensors(device):
    rf = device.redfish
    data = rf.get(
        "/redfish/v1/Chassis/PowerShelf_0/Oem/deltaenergysystems/AllSensors"
    )
    if not data:
        return []
    out = []
    for sensor in data.get("Sensors", []) or []:
        dtype = (sensor.get("ReadingType") or "").lower()
        device_name = (sensor.get("DeviceName") or "").lower()
        if "power" not in dtype and "psu" not in device_name:
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
