"""Validated fixed reading sets supplied by a firmware-specific OEM adapter."""

import math

from . import common


def collect(device, capability):
    specs = getattr(getattr(device, "odm", None), "direct_read_sets", {}).get(capability, ())
    selected = device.vars.get("temp_chassis_id") if capability == "temp" else None
    selected = selected or device.vars.get("chassis_id")
    if selected and any(spec["path"].split("/")[4] != selected for spec in specs):
        return []
    out = {}
    valid = True
    for spec in specs:
        path, kind, expected = spec["path"], spec["kind"], spec["readings"]
        data = device.redfish.get(path)
        if (data is None and capability == "temp"
                and getattr(device.odm, "temp_scoped_fallback", False)
                and getattr(device.redfish, "timeout_reason", None)):
            data = device.redfish.get(path)
        if not isinstance(data, dict) or "error" in data:
            valid = False
            continue
        entries = data.get(kind)
        if not isinstance(entries, list) or data.get("Members@odata.nextLink"):
            valid = False
            continue
        found = {}
        for item in entries:
            if not isinstance(item, dict):
                valid = False
                continue
            source = item.get("DataSourceUri") or item.get("@odata.id")
            ident = (source.rsplit("/", 1)[-1] if kind == "TemperatureReadingsCelsius"
                     and isinstance(source, str) else item.get("Id") or item.get("MemberId"))
            if ident not in expected:
                continue
            rule = expected[ident]
            raw = item.get("ReadingCelsius") if kind == "Temperatures" else item.get("Reading")
            numeric = isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw)
            if not numeric and not (raw is None and rule.get("nullable")):
                valid = False
            if kind == "Members":
                if item.get("ReadingUnits") != rule["unit"] or item.get("ReadingType") != rule["type"]:
                    valid = False
                if source and source != path.split("?")[0] + "/" + ident:
                    valid = False
            if kind == "Fans" and item.get("ReadingUnits") != rule["unit"]:
                valid = False
            if kind == "TemperatureReadingsCelsius" and source != rule["source"]:
                valid = False
            if ident in found:
                valid = False
            health = common.health_of(item.get("Status")) if item.get("Status") else "Unknown"
            found[ident] = common.reading(
                ident, item.get("Name") or item.get("DeviceName") or ident,
                raw, health, unit=rule["unit"], source=source or path, available=raw is not None,
            )
        if found.keys() != expected.keys():
            valid = False
        for ident, row in found.items():
            if ident in out and out[ident].get("source") != row.get("source"):
                valid = False
            out.setdefault(ident, row)
    return list(out.values()) if valid else []


def completeness_error(device, capability, rows):
    """Do not accept a partial fallback for a newly verified fixed layout."""
    adapter = getattr(device, "odm", None)
    layouts = getattr(adapter, "direct_read_sets", {})
    if not layouts:
        return None
    selected = device.vars.get("temp_chassis_id") if capability == "temp" else None
    selected = selected or device.vars.get("chassis_id")
    expected = None
    if capability in layouts:
        specs = layouts[capability]
        if selected and any(spec["path"].split("/")[4] != selected for spec in specs):
            return None
        expected = {ident for spec in specs for ident in spec["readings"]}
    elif capability == "psu":
        if getattr(device.options, "details", False) and getattr(adapter, "psu_sensor_details", False):
            return None
        sensors = getattr(adapter, "psu_read_sensors", ())
        legacy = getattr(adapter, "psu_legacy_slots", None)
        if sensors:
            if selected and any(cid != selected for cid, _, _ in sensors):
                return None
            expected = {label for _, _, label in sensors}
        elif legacy:
            cid, _, count = legacy
            if selected and cid != selected:
                return None
            expected = {str(i) for i in range(count)}
    if capability == "monitor" and not selected:
        power = getattr(adapter, "monitor_power_sensor", None)
        inlet = getattr(adapter, "inlet_sensor", None)
        supplies = getattr(adapter, "psu_read_sensors", ())
        if monitor_ids := getattr(adapter, "monitor_reading_ids", ()):
            expected = set(monitor_ids)
        elif power and inlet and supplies:
            expected = {power, inlet[1], *(label for _, _, label in supplies)}
    if expected is None:
        return None
    actual = [str(row.get("id") or "") for row in rows]
    missing = sorted(expected - set(actual))
    extra = sorted(set(actual) - expected)
    if missing or extra or len(actual) != len(expected):
        return ("incomplete {} data: BMC returned {} of {} expected readings; "
                "missing={}; unexpected={}").format(capability, len(actual),
                                                   len(expected), missing, extra)
    invalid = []
    rules = {ident: rule for spec in layouts.get(capability, ())
             for ident, rule in spec["readings"].items()}
    for row in rows:
        ident = str(row.get("id") or "")
        value = row.get("input_watts") if capability == "psu" else row.get("value")
        rule = rules.get(ident, {})
        if (value is None or value == "NA") and rule.get("nullable") and row.get("available") is False:
            continue
        try:
            numeric = not isinstance(value, bool) and math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            numeric = False
        if capability == "fan" and row.get("unit") != rule.get("unit"):
            numeric = False
        if not numeric:
            invalid.append(ident)
    if invalid:
        return "incomplete {} data: unavailable or invalid readings={}".format(capability, invalid)
    return None
