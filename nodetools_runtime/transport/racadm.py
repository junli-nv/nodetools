"""racadm wrapper (used by the Dell ODM module)."""

import shutil
import subprocess

from ..diagnostics import debug
from ..errors import TransportError


class Racadm:
    def __init__(self, address, user, password, tool=None, timeout=15):
        self.address = address
        self.debug_label = None
        self.user = user
        self.password = password
        self.tool = tool or shutil.which("racadm")
        self.timeout = timeout

    def available(self):
        return self.tool is not None

    def run(self, *args):
        label = "racadm:" + (self.debug_label or "")
        action = args[0] if args else ""
        debug(self.debug_label, label, "{} requested".format(action))
        if not self.tool:
            raise TransportError("racadm not found")
        argv = [
            self.tool,
            "-r",
            self.address,
            "-u",
            self.user,
            "-p",
            self.password,
        ] + list(args)
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            debug(self.debug_label, label, type(exc).__name__)
            raise TransportError("racadm failed: {}".format(type(exc).__name__)) from None
        debug(self.debug_label, label, "{} exit={}".format(action, proc.returncode))
        if proc.returncode != 0:
            raise TransportError("racadm exited {}".format(proc.returncode))
        return proc.stdout
