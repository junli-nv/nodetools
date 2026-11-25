"""Independent BMC, host network, SSH, and Slurm status checks."""

import socket
import subprocess

from .runtime import InfoRunner


def ping(address, seconds):
    if not address:
        return "no_address"
    try:
        result = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", str(seconds), "--", address],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=seconds + 1,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    except FileNotFoundError:
        return "unavailable"
    except OSError:
        return "error"
    return {0: "ok", 1: "no_reply"}.get(result.returncode, "error")


def ssh(address, port, seconds):
    try:
        with socket.create_connection((address, port), timeout=seconds) as connection:
            return "ok" if connection.recv(256).startswith(b"SSH-") else "unexpected_banner"
    except TimeoutError:
        return "timeout"
    except ConnectionRefusedError:
        return "refused"
    except socket.gaierror:
        return "unresolved"
    except OSError:
        return "unreachable"


def slurm_states(timeout):
    try:
        result = subprocess.run(
            ["scontrol", "show", "nodes", "--oneliner"], text=True, capture_output=True,
            timeout=timeout, check=True,
        )
    except subprocess.TimeoutExpired:
        return "timeout", {}
    except FileNotFoundError:
        return "unavailable", {}
    except (OSError, subprocess.CalledProcessError):
        return "error", {}
    states = {}
    for line in result.stdout.splitlines():
        fields = dict(word.split("=", 1) for word in line.split() if "=" in word)
        if fields.get("NodeName") and fields.get("State"):
            states[fields["NodeName"].casefold()] = fields["State"]
    return "ok", states


def error_status(message):
    if not message:
        return "ok"
    value = message.lower()
    if value.startswith("capunsupported:"):
        return "unsupported"
    if "timed out" in value or "timeout" in value or "deadline exceeded" in value:
        return "timeout"
    if value.startswith(("no readings", "no bmc address")):
        return "no_data"
    return "error"


def power_status(result):
    message = result.get("error")
    lines = result.get("lines") or []
    if message or not lines:
        return error_status(message) if message else "no_data", None
    value = lines[0].partition(":")[2].strip().strip('"').casefold()
    state = {
        "on": "on", "off": "off", "poweringon": "powering_on",
        "poweringoff": "powering_off", "paused": "paused", "unknown": "unknown",
    }.get(value)
    return ("ok", state) if state else ("no_data", None)


def collect(host, options, seconds, port, slurm=None):
    address = host.get("ssh_host") or host.name
    bmc_ping = ping(host.address, seconds)
    host_ping = ping(address, seconds)
    ssh_state = ssh(address, port, seconds)
    power = InfoRunner("power", options)._one(host)
    power_query, power_state = power_status(power)
    checks = {
        "bmc_ping": bmc_ping,
        "power": {"status": power_query, "state": power_state, "error": power.get("error")},
        "host_ping": host_ping,
        "ssh": ssh_state,
    }
    healthy = bmc_ping == host_ping == ssh_state == "ok" and power_state == "on"
    if slurm is not None:
        status, states = slurm
        state = states.get(host.name.casefold())
        checks["slurm"] = {"status": status if status != "ok" or state else "not_found", "state": state}
        unhealthy_slurm = {"DOWN", "DRAIN", "DRAINING", "FAIL", "FAILING", "NOT_RESPONDING"}
        healthy = healthy and bool(state) and not (set(state.split("+")) & unhealthy_slurm)
    return {
        "schema_version": 1, "node": host.name, "command": "nodealive",
        "role": host.role, "status": "ok" if healthy else "degraded", "checks": checks,
    }
