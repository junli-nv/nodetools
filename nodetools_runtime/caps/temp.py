"""Temperature capability: chassis/board/PSU temperatures with fallbacks."""

from ..config import DEFAULT_CHASSIS
from ..ladder import first
from . import common

_TEMP_HINTS = ("temp", "inlet", "exhaust", "therm", "t_core")


def _is_temp(name):
    lowered = (name or "").lower()
    return any(hint in lowered for hint in _TEMP_HINTS)


def _from_thermal(device, chassis=None):
    rf = device.redfish

    def scan(cid):
        out = []
        thermal = rf.get("/redfish/v1/Chassis/{}/Thermal".format(cid))
        for temp in (thermal or {}).get("Temperatures", []) or []:
            out.append(
                common.reading(
                    temp.get("MemberId") or temp.get("SensorNumber") or temp.get("Id"),
                    temp.get("Name") or temp.get("SensorName"),
                    common.num(temp.get("ReadingCelsius") if temp.get("ReadingCelsius") is not None
                               else temp.get("Reading")),
                    common.health_of(temp.get("Status")),
                    source=temp.get("@odata.id") or "/redfish/v1/Chassis/{}/Thermal".format(cid),
                )
            )
        return out

    if chassis is None:
        chassis = common.rung_chassis(device, "Thermal", DEFAULT_CHASSIS)
        if not chassis:
            return []
        if common.profile(device) is None and common.absent(
            device, "/redfish/v1/Chassis/{}/Thermal".format(chassis[0])
        ):
            return []
    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def chassis_thermal_metrics(device, cid):
    """Temperature readings from a single chassis' ThermalMetrics endpoint.

    Exposed so callers that only need one chassis (e.g. the inlet check) can
    ask for it directly instead of walking every chassis with ``collect``.
    """
    rf = device.redfish
    out = []
    metrics = rf.get(
        "/redfish/v1/Chassis/{}/ThermalSubsystem/ThermalMetrics".format(cid)
    )
    for entry in (metrics or {}).get("TemperatureReadingsCelsius", []) or []:
        source = entry.get("DataSourceUri") or entry.get("@odata.id") or ""
        out.append(
            common.reading(
                source.rsplit("/", 1)[-1] if source else None,
                entry.get("Name") or source.rsplit("/", 1)[-1],
                common.num(entry.get("Reading")),
                "OK",
                source=source or "/redfish/v1/Chassis/{}/ThermalSubsystem/ThermalMetrics".format(cid),
            )
        )
    return out


def _from_thermal_metrics(device):
    chassis = common.rung_chassis(device, "Temperatures", DEFAULT_CHASSIS)

    def scan(cid):
        return chassis_thermal_metrics(device, cid)

    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


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
                    source=prop,
                )
            )
    return out


def _from_sensors(device):
    rf = device.redfish

    def scan(cid):
        out = []
        for sensor in common.enumerate_sensors(rf, cid):
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
                    source=sensor.get("@odata.id") or "/redfish/v1/Chassis/{}/Sensors".format(cid),
                )
            )
        return out

    chassis = common.rung_chassis(device, "Sensors", DEFAULT_CHASSIS)
    return common.dedup_readings(common.flatten(common.map_chassis(scan, chassis)))


def _from_ipmi(device):
    if not device.ipmi:
        return []
    out = []
    for name, value in device.ipmi.sdr("Temperature").items():
        out.append(common.reading(name, name, common.num(value), source="ipmi:sdr:Temperature"))
    return out


def collect(device):
    steps = []
    fixed_chassis = device.vars.get("temp_chassis_id")
    if fixed_chassis:
        steps.append(("fixed_thermal", lambda: _from_thermal(device, (fixed_chassis,))))
    ipmi_first = getattr(getattr(device, "odm", None), "temp_read_ipmi", False)
    if ipmi_first:
        steps.append(("oem_ipmi", lambda: _from_ipmi(device)))
    steps += [
        ("thermal", lambda: _from_thermal(device)),
        ("thermal_metrics", lambda: _from_thermal_metrics(device)),
        ("telemetry", lambda: _from_telemetry(device)),
        ("sensors", lambda: _from_sensors(device)),
    ]
    if not ipmi_first:
        steps.append(("ipmi", lambda: _from_ipmi(device)))
    return first(steps, default=[])
