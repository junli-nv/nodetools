"""ipmitool wrapper.

Password passing honours both conventions:

* default: ``-U user -P pass`` on the command line (matches the legacy scripts);
* environment mode (``use_env=True`` or ``NODETOOLS_IPMI_ENV``): ``-U user -E``
  with the value supplied through ``IPMI_PASSWORD`` so it never appears in the
  process table.
"""

import os
import shutil
import subprocess
import time

from ..diagnostics import debug, debug_connection
from ..errors import TransportError, TransportTimeout

_FALLBACK_TOOL = "/cm/local/apps/ipmitool/1.8.19/ipmitool"


def find_ipmitool():
    found = shutil.which("ipmitool")
    if found:
        return found
    if os.path.exists(_FALLBACK_TOOL):
        return _FALLBACK_TOOL
    return None


def env_mode_default():
    return os.environ.get("NODETOOLS_IPMI_ENV", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class Ipmi:
    def __init__(self, address, user, password, use_env=None, timeout=10, tool=None):
        self.address = address
        self.debug_label = None
        self.user = user
        self.password = password
        self.timeout = timeout
        self.retry_interval = 2
        self.retries = 1
        self.tool = tool or find_ipmitool()
        self.use_env = env_mode_default() if use_env is None else use_env
        self._deadline = None
        self.transport_errors = 0

    def set_deadline(self, deadline):
        """Absolute time.monotonic() value past which no new command is run."""
        self._deadline = deadline

    def _remaining(self):
        if self._deadline is None:
            return None
        return self._deadline - time.monotonic()

    def _argv(self, command):
        argv = [self.tool, "-I", "lanplus", "-H", self.address]
        if self.user:
            argv += ["-U", self.user]
        if self.use_env:
            argv += ["-E"]
        elif self.password is not None:
            argv += ["-P", self.password]
        argv += ["-N", str(self.retry_interval), "-R", str(self.retries)]
        return argv + list(command)

    def _run(self, command):
        label = "ipmi:" + (self.debug_label or "")
        operation = command[0]
        debug_connection(self.debug_label, label, f"ipmitool {operation}", self.address,
                         self.user, (self.password or "") if self.use_env else self.password)
        if not self.tool:
            raise TransportError("ipmitool not found")
        remaining = self._remaining()
        if remaining is not None and remaining <= 0:
            raise TransportTimeout("node deadline exceeded before IPMI request")
        timeout = self.timeout
        if remaining is not None:
            timeout = min(timeout, remaining)
        env = dict(os.environ)
        if self.use_env:
            env["IPMI_PASSWORD"] = self.password or ""
        try:
            proc = subprocess.run(
                self._argv(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            debug(self.debug_label, label, "ipmitool {} timed out".format(operation))
            self.transport_errors += 1
            raise TransportTimeout("IPMI request timed out after {:.2g}s".format(timeout)) from None
        except OSError as exc:
            self.transport_errors += 1
            raise TransportError("ipmitool failed to start: {}".format(exc)) from exc
        debug(self.debug_label, label, "ipmitool {} exit={}".format(operation, proc.returncode))
        if proc.returncode != 0:
            self.transport_errors += 1
            raise TransportError(
                "ipmitool {} exited {}".format(
                    " ".join(command), proc.returncode
                )
            )
        return proc.stdout

    def sdr(self, sdr_type=None):
        """Return {sensor_name: first_value_token} for an ``sdr`` query."""
        command = ["sdr"]
        if sdr_type:
            command += ["type", sdr_type]
        out = self._run(command)
        readings = {}
        for line in out.splitlines():
            if "|" not in line:
                continue
            name, _, rest = line.partition("|")
            name = name.strip()
            if not name:
                continue
            value = rest.strip().split(" ")[0] if rest.strip() else ""
            readings[name] = value
        return readings

    def raw(self, *payload):
        out = self._run(["raw"] + [hex(b) if isinstance(b, int) else b for b in payload])
        return out.strip()

    def run(self, *command):
        """Run an arbitrary ipmitool command, returning raw stdout."""
        return self._run(list(command))

    def power(self, action):
        """``ipmitool ... power <action>`` (status/on/off/cycle/reset)."""
        return self._run(["power", action])
