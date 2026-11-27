"""Read NVOS telemetry over SSH."""

import json
import os
import subprocess
import time

from ..diagnostics import debug
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
    address = host.vars.get("nvswitch_os_host") or host.name
    user = options.user or host.vars.get("nvswitch_os_user")
    password = options.password
    password_source = "--password"
    if password is None:
        password_source = "nvswitch_os_password"
        if password_source not in host.vars:
            password_source = "nvswitch_os_pass"
        password = host.vars.get(password_source)
        if password is None:
            password_source = "unset"
    if not user:
        raise CapUnsupported("configure nvswitch_os_user for NVOS SSH")
    remaining = min(options.timeout, deadline - time.monotonic())
    if remaining <= 0:
        raise TransportError("NVOS node deadline exceeded")
    argv = [
        "ssh", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout={}".format(max(1, int(remaining))),
        "-o", "NumberOfPasswordPrompts=1", "-l", user, "--", address, command,
    ]
    enabled = getattr(options, "debug", False)
    label = "nvos:" + host.name
    query = "read lm5066i hwmon" if command == _HSC else command
    if enabled:
        argv.insert(1, "-v")
        debug(True, label, f"SSH {address}: {query} "
              f"(user={user!r}, password={password!r}, password_source={password_source})")
    kwargs = dict(capture_output=True, text=True, timeout=remaining, stdin=subprocess.DEVNULL)
    try:
        if not password:
            argv[1:1] = ["-o", "BatchMode=yes"]
            result = subprocess.run(argv, **kwargs)
        else:
            secret = (password + "\n").encode()
            if len(secret) > 4096:
                raise TransportError("NVOS SSH password is too long")
            reader, writer = os.pipe()
            with os.fdopen(reader, "rb") as pipe:
                with os.fdopen(writer, "wb") as stream:
                    stream.write(secret)
                result = subprocess.run(
                    ["sshpass", "-d", str(pipe.fileno()), *argv], pass_fds=(pipe.fileno(),), **kwargs
                )
        if enabled:
            for line in result.stderr.splitlines():
                if line.startswith("debug1: Connecting to "):
                    debug(True, label, line.removeprefix("debug1: "))
            debug(True, label, f"SSH exit={result.returncode}")
        return result
    except FileNotFoundError as exc:
        raise TransportError("NVOS SSH requires OpenSSH and sshpass for password login") from exc
    except subprocess.TimeoutExpired as exc:
        debug(enabled, label, "SSH timed out")
        raise TransportError("NVOS SSH query timed out") from exc


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
