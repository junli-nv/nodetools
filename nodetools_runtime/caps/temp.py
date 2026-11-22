"""Temperature capability: chassis/board/PSU temperatures with fallbacks."""

from ..config import DEFAULT_CHASSIS
from ..ladder import first
from . import common

_TEMP_HINTS = ("temp", "inlet", "exhaust", "therm", "t_core")


def _is_temp(name):
    lowered = (name or "").lower()
    return any(hint in lowered for hint in _TEMP_HINTS)


def _from_thermal(device):
    rf = device.redfish
    out = []
    for chassis in common.chassis_list(device, DEFAULT_CHASSIS):
        thermal = rf.get("/redfish/v1/Chassis/{}/Thermal".format(chassis))
        for temp in (thermal or {}).get("Temperatures", []) or []:
            out.append(
                common.reading(
                    temp.get("MemberId") or temp.get("SensorNumber") or temp.get("Id"),
                    temp.get("Name") or temp.get("SensorName"),
                    common.num(temp.get("ReadingCelsius") or temp.get("Reading")),
                    common.health_of(temp.get("Status")),
                )
            )
        if out:
            return out
    return out


def _from_thermal_metrics(device):
    rf = device.redfish
    out = []
    for chassis in common.chassis_list(device, DEFAULT_CHASSIS):
        metrics = rf.get(
            "/redfish/v1/Chassis/{}/ThermalSubsystem/ThermalMetrics".format(chassis)
        )
        for entry in (metrics or {}).get("TemperatureReadingsCelsius", []) or []:
            source = entry.get("DataSourceUri") or entry.get("@odata.id") or ""
            out.append(
                common.reading(
                    source.rsplit("/", 1)[-1] if source else None,
                    entry.get("Name") or source.rsplit("/", 1)[-1],
                    common.num(entry.get("Reading")),
                    "OK",
                )
            )
        if out:
            return out
    return out


def _from_telemetry(device):
    rf = device.redfish
    reports = common.member_ids(rf.get("/redfish/v1/TelemetryService/MetricReports"))
    out = []
    for oid in reports:
        report = rf.get(oid)
        if not report:
            continue
        for entry in report.get("MetricValues", []) or []:
            prop = entry.get("MetricProperty") or ""
            if not _is_temp(prop):
                continue
            out.append(
                common.reading(
                    prop,
                    prop,
                    common.num(entry.get("MetricValue")),
                    entry.get("MetricState") or "OK",
                )
            )
    return out


def _from_sensors(device):
    rf = device.redfish
    out = []
    for chassis in common.chassis_list(device, DEFAULT_CHASSIS):
        for sensor in common.enumerate_sensors(rf, chassis):
            name = sensor.get("Name") or sensor.get("Id") or ""
            rtype = sensor.get("ReadingType") or ""
            if not (_is_temp(name) or "temperature" in rtype.lower()):
                continue
            out.append(
                common.reading(
                    sensor.get("Id"),
                    name,
                    common.num(sensor.get("Reading")),
                    common.health_of(sensor.get("Status")),
                )
            )
        if out:
            return out
    return out


def _from_ipmi(device):
    if not device.ipmi:
        return []
    out = []
    for name, value in device.ipmi.sdr("Temperature").items():
        out.append(common.reading(name, name, common.num(value)))
    return out


def collect(device):
    steps = [
        ("thermal", lambda: _from_thermal(device)),
        ("thermal_metrics", lambda: _from_thermal_metrics(device)),
        ("telemetry", lambda: _from_telemetry(device)),
        ("sensors", lambda: _from_sensors(device)),
        ("ipmi", lambda: _from_ipmi(device)),
    ]
    return first(steps, default=[])
