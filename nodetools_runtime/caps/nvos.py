"""Read NVOS telemetry over SSH."""

import json
import time

from ..errors import CapUnsupported, TransportError
from . import common

_FIELDS = {
    "psu": ("psu", "power", "W"),
    "temp": ("temperature", "current", "°C"),
}

_HSC = r'''
for hw in /sys/class/hwmon/hwmon*; do
    [ "$(cat "$hw/name" 2>/dev/null)" = lm5066i ] || continue
    [ "$(cat "$hw/in1_label")" = vin ] || continue
    [ "$(cat "$hw/curr1_label")" = iin ] || continue
    [ "$(cat "$hw/power1_label")" = pin ] || continue
    cat "$hw/in1_input" "$hw/curr1_input" "$hw/power1_input" || exit 1
    for alarm in temp1_crit_alarm temp1_max_alarm curr1_max_alarm in1_max_alarm in1_min_alarm in3_min_alarm power1_alarm; do
        cat "$hw/$alarm" 2>/dev/null || printf 'NA\n'
    done
done
'''


def _ssh(host, options, command, deadline):
    from ..transport.ssh import run
    return run(host, options, command, deadline,
               query_label="read lm5066i hwmon" if command == _HSC else None)


def collect(host, capability, options):
    from .. import model, oem
    command, field, unit = _FIELDS[capability]
    deadline = time.monotonic() + (options.node_timeout or options.timeout)
    adapter = oem.for_device(model.Device(host))
    if capability == "psu" and getattr(adapter, "nvos_psu_is_hsc", False):
        return _hsc(host, options, deadline)
    query = "nv show platform environment {} -o json".format(command)
    result = _ssh(host, options, query, deadline)
    if result.returncode:
        if "'{}' is not one of".format(command) in result.stderr:
            if capability == "psu":
                return _hsc(host, options, deadline)
            raise CapUnsupported("NVOS exposes no {} sensors on this tray".format(command))
        raise TransportError("NVOS {} query failed (SSH exit {})".format(command, result.returncode))
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise TransportError("NVOS {} did not return JSON".format(command)) from exc
    readings = [
        common.reading(name, name, common.num(item.get(field)), item.get("state"), unit=unit)
        for name, item in sorted(data.items()) if isinstance(item, dict)
    ] if isinstance(data, dict) else []
    if not readings:
        raise CapUnsupported("NVOS reported no {} sensors".format(command))
    return readings


def _hsc(host, options, deadline):
    result = _ssh(host, options, _HSC, deadline)
    if result.returncode:
        raise TransportError("NVOS HSC query failed (SSH exit {})".format(result.returncode))
    try:
        values = result.stdout.split()
        if len(values) != 10:
            raise ValueError()
        mv, ma, uw = map(int, values[:3])
        if min(mv, ma, uw) < 0:
            raise ValueError()
    except ValueError as exc:
        raise CapUnsupported("NVOS exposes neither PSU modules nor readable tray HSC input") from exc
    alarms = values[3:]
    health = "Unknown"
    if alarms[0] == "1":
        health = "Critical"
    elif "1" in alarms:
        health = "Warning"
    elif set(alarms) == {"0"}:
        health = "OK"
    watts = round(uw / 1000000, 2)
    return [common.reading("HSC-DC-IN", "HSC input (lm5066i)", watts, health, unit="W",
                           input_watts=watts, input_volts=mv / 1000, input_amps=ma / 1000,
                           output_watts=None, reading_kind="hsc_input",
                           source="NVOS hwmon lm5066i: vin/iin/pin")]
