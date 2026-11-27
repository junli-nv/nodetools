"""Shared OS SSH transport; credentials and input stay out of argv."""

import os
import subprocess
import time

from ..diagnostics import debug
from ..errors import CapUnsupported, TransportError


def run(host, options, command, deadline, *, role="nvswitch", input_text=None, query_label=None):
    address = host.vars.get(role + "_os_host") or host.vars.get("os_host") or host.name
    user = options.user or host.vars.get(role + "_os_user") or host.vars.get("os_user")
    password = options.password
    password_source = "--password"
    if password is None:
        password_source = role + "_os_password"
        if password_source not in host.vars:
            password_source = role + "_os_pass"
        password = host.vars.get(password_source)
        if password is None:
            password_source = "os_password" if "os_password" in host.vars else "os_pass"
            password = host.vars.get(password_source)
        if password is None:
            password_source = "unset"
    if not user:
        raise CapUnsupported("configure os_user or role-specific OS user for SSH")
    remaining = min(options.timeout, deadline - time.monotonic())
    if remaining <= 0:
        raise TransportError("OS SSH node deadline exceeded")
    argv = [
        "ssh", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout={}".format(max(1, int(remaining))),
        "-o", "NumberOfPasswordPrompts=1", "-l", user, "--", address, command,
    ]
    enabled = getattr(options, "debug", False)
    label = ("nvos:" if role == "nvswitch" else "os:") + host.name
    query = query_label or command
    if enabled:
        argv.insert(1, "-v")
        debug(True, label, f"SSH {address}: {query} "
              f"(user={user!r}, password={password!r}, password_source={password_source})")
    kwargs = dict(capture_output=True, text=True, timeout=remaining, stdin=subprocess.DEVNULL)
    if input_text is not None:
        kwargs.pop("stdin")
        kwargs["input"] = input_text
    try:
        if not password:
            argv[1:1] = ["-o", "BatchMode=yes"]
            result = subprocess.run(argv, **kwargs)
        else:
            secret = (password + "\n").encode()
            if len(secret) > 4096:
                raise TransportError("OS SSH password is too long")
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
        raise TransportError("OS SSH requires OpenSSH and sshpass for password login") from exc
    except subprocess.TimeoutExpired as exc:
        debug(enabled, label, "SSH timed out")
        raise TransportError("OS SSH query timed out") from exc
