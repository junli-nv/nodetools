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

from ..errors import TransportError

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
        self.user = user
        self.password = password
        self.timeout = timeout
        self.tool = tool or find_ipmitool()
        self.use_env = env_mode_default() if use_env is None else use_env

    def _argv(self, command):
        argv = [self.tool, "-I", "lanplus", "-H", self.address]
        if self.user:
            argv += ["-U", self.user]
        if self.use_env:
            argv += ["-E"]
        elif self.password is not None:
            argv += ["-P", self.password]
        argv += ["-N", "2", "-R", "1"]
        return argv + list(command)

    def _run(self, command):
        if not self.tool:
            raise TransportError("ipmitool not found")
        env = dict(os.environ)
        if self.use_env:
            env["IPMI_PASSWORD"] = self.password or ""
        try:
            proc = subprocess.run(
                self._argv(command),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransportError("ipmitool timed out: {}".format(exc)) from exc
        except OSError as exc:
            raise TransportError("ipmitool failed to start: {}".format(exc)) from exc
        if proc.returncode != 0:
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
