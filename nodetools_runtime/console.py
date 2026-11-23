"""``nodeconsole`` - compute-tray SOL console.

Two modes, mirroring the legacy ``nodeconsole`` script plus the interactive
console from the ``sol`` suite:

* ``nodeconsole <noderange> [open|close]`` is the verbatim IPMI port. ``open``
  attaches the terminal to one node (``sol deactivate`` then, after a second,
  ``-C 17 -N 5 -R 5 sol activate usesolkeepalive``); ``close`` deactivates many
  nodes in parallel, prefixing each output line with ``<node>: ``.
* ``nodeconsole <node> --console`` (alias ``--interactive``) opens a raw SOL
  session backed by a pty, with an I/O log and optional reconnect. Ctrl-]
  detaches locally; Ctrl-C is forwarded to the remote console.
"""

import concurrent.futures
import errno
import json
import os
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import time
import tty
from datetime import datetime

from .diagnostics import debug
from .runtime import build_device

DEFAULT_CIPHER = 17
# Legacy ``open``: ``-N 5 -R 5`` on activate, plain defaults on deactivate.
_ACTIVATE_NET = ("-N", "5", "-R", "5")


def _argv(device, command, cipher=None, net=()):
    """Build an ipmitool argv for a device, honouring the credential mode.

    ``net`` is inserted verbatim (so callers can pass the legacy ``-N/-R``);
    an empty tuple omits it, matching the legacy ``close``/``deactivate``.
    """
    ipmi = device.ipmi
    if not ipmi or not ipmi.tool:
        raise RuntimeError("ipmitool not found")
    if getattr(ipmi, "debug_label", None):
        debug(True, "ipmi:" + ipmi.debug_label, "SOL {} for {}".format(command[1], ipmi.address))
    argv = [ipmi.tool, "-I", "lanplus", "-H", ipmi.address]
    env = os.environ.copy()
    if ipmi.user:
        argv += ["-U", ipmi.user]
    if ipmi.use_env:
        argv += ["-E"]
        env["IPMI_PASSWORD"] = ipmi.password or ""
    elif ipmi.password is not None:
        argv += ["-P", ipmi.password]
    if cipher is not None:
        argv += ["-C", str(cipher)]
    argv += list(net)
    return argv + list(command), env


def _deactivate_argv(device, cipher=None):
    return _argv(device, ["sol", "deactivate"], cipher=cipher)


def _activate_argv(device, cipher=DEFAULT_CIPHER):
    return _argv(
        device,
        ["sol", "activate", "usesolkeepalive"],
        cipher=cipher,
        net=_ACTIVATE_NET,
    )


def close_all(hosts, options, emit=print):
    """Legacy ``close``: deactivate every node in parallel, prefixing output."""
    jobs = max(1, options.jobs)
    errors = 0

    def one(host):
        nonlocal errors
        if not host.address:
            return ["{}: no BMC address (missing from inventory)".format(host.name)]
        device = build_device(host, options, apply_deadline=False)
        try:
            argv, env = _deactivate_argv(device)
            kwargs = dict(
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
            )
            if options.timeout:
                kwargs["timeout"] = options.timeout
            try:
                proc = subprocess.run(argv, **kwargs)
                text = (proc.stdout or "") + (proc.stderr or "")
            except subprocess.TimeoutExpired:
                errors += 1
                text = "ipmitool timed out"
            return ["{}: {}".format(host.name, line) for line in text.splitlines() if line.strip()]
        except Exception as exc:  # noqa: BLE001 - reported per node
            errors += 1
            return ["{}: {}: {}".format(host.name, type(exc).__name__, exc)]
        finally:
            device.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(one, host) for host in hosts]
        for future in concurrent.futures.as_completed(futures):
            for line in future.result():
                emit(line)
    return 1 if errors else 0


def open_one(host, options):
    """Legacy ``open``: attach the terminal to a single node's SOL."""
    device = build_device(host, options, apply_deadline=False)
    try:
        dargv, denv = _deactivate_argv(device, cipher=DEFAULT_CIPHER)
        subprocess.run(dargv, env=denv, stdin=subprocess.DEVNULL)
        time.sleep(1)
        aargv, aenv = _activate_argv(device)
        try:
            return subprocess.run(aargv, env=aenv).returncode
        except KeyboardInterrupt:
            print("\nnodeconsole: detached.", file=sys.stderr)
            return 130
    finally:
        device.close()


class _Log:
    """Timestamped JSON Lines console log (control characters are escaped)."""

    def __init__(self, path, host):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.path = path
        self.file = os.fdopen(fd, "w", encoding="utf-8")
        self.host = host

    def record(self, direction, data):
        if isinstance(data, bytes):
            data = data.decode("utf-8", "backslashreplace")
        entry = {
            "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "host": self.host,
            "direction": direction,
            "data": data,
        }
        self.file.write(json.dumps(entry, ensure_ascii=True) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


def _label(host):
    return re.sub(r"[^A-Za-z0-9._-]", "_", host.address or host.name)[:100] or "bmc"


def _log_path(options, host):
    if options.log_file:
        return options.log_file
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f%z")
    return os.path.join(options.log_dir, "sol_{}_{}.jsonl".format(_label(host), stamp))


def _write_all(fd, data):
    while data:
        try:
            count = os.write(fd, data)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return
            raise
        data = data[count:]


def _relay(fd, log):
    """Raw terminal relay. Returns ``"detached"`` or ``"disconnected"``."""
    input_fd, output_fd = sys.stdin.fileno(), sys.stdout.fileno()
    saved = termios.tcgetattr(input_fd)
    print(
        "Interactive SOL: Ctrl-] detaches; Ctrl-C is sent to the remote console.",
        file=sys.stderr,
        flush=True,
    )
    try:
        tty.setraw(input_fd, when=termios.TCSANOW)
        while True:
            ready, _, _ = select.select([input_fd, fd], [], [])
            if fd in ready:
                try:
                    data = os.read(fd, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    data = b""
                if not data:
                    return "disconnected"
                if log:
                    log.record("OUT", data)
                _write_all(output_fd, data)
            if input_fd in ready:
                data = os.read(input_fd, 4096)
                if not data:
                    return "detached"
                before, separator, _ = data.partition(b"\x1d")
                if before:
                    if log:
                        log.record("IN", before)
                    _write_all(fd, before)
                if separator:
                    if log:
                        log.record("EVENT", "detached with Ctrl-]")
                    return "detached"
    finally:
        termios.tcsetattr(input_fd, termios.TCSANOW, saved)


def _reap(pid, fd):
    try:
        os.write(fd, b"~.")
    except OSError:
        pass
    for _ in range(20):
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
        except ChildProcessError:
            return
        time.sleep(0.05)
    for signum in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            break
        for _ in range(20):
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    return
            except ChildProcessError:
                return
            time.sleep(0.05)


def attach(host, options):
    """Enhanced ``--console``: pty-backed SOL with logging and reconnect."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("nodeconsole: --console requires a terminal", file=sys.stderr)
        return 2
    device = build_device(host, options, apply_deadline=False)
    log = None
    try:
        if not options.no_log:
            path = _log_path(options, host)
            if options.log_file:
                if os.path.exists(path):
                    print("nodeconsole: --log-file already exists: {}".format(path), file=sys.stderr)
                    return 2
            else:
                os.makedirs(options.log_dir, exist_ok=True)
            log = _Log(path, host.address)
            log.record("EVENT", "console started")
            print("SOL log: {} (input, including passwords, is recorded).".format(path), file=sys.stderr)
        attempts = options.reconnect_attempts if options.reconnect else 1
        try:
            dargv, denv = _deactivate_argv(device, cipher=DEFAULT_CIPHER)
            subprocess.run(
                dargv, env=denv, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, errors="replace",
            )
        except (OSError, subprocess.SubprocessError):
            pass
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                print(
                    "SOL disconnected; reconnecting in {:g}s ({}/{}). Ctrl-C cancels.".format(
                        options.reconnect_delay, attempt, attempts
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                if log:
                    log.record("EVENT", "reconnect {}/{}".format(attempt, attempts))
                time.sleep(options.reconnect_delay)
            argv, env = _activate_argv(device, cipher=options.cipher)
            sys.stdout.flush()
            sys.stderr.flush()
            pid, fd = pty.fork()
            if pid == 0:  # child: exec ipmitool on the pty
                os.execvpe(argv[0], argv, env)
            outcome = _relay(fd, log)
            _reap(pid, fd)
            if outcome == "detached":
                return 0
            if attempt >= attempts:
                break
        print("nodeconsole: SOL reconnect attempts exhausted", file=sys.stderr)
        return 1
    finally:
        if log:
            log.record("EVENT", "console closed")
            log.close()
        device.close()
