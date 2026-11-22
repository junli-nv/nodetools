import types

import pytest

from nodetools_runtime import console
from nodetools_runtime.arguments import NODE_PROGRAMS, parse_args
from nodetools_runtime.inventory import Host


class StubIpmi:
    def __init__(self, tool="/usr/bin/ipmitool", user="root", password="0penBmc",
                 use_env=False):
        self.tool = tool
        self.address = "10.0.0.9"
        self.user = user
        self.password = password
        self.use_env = use_env


class StubDevice:
    def __init__(self, ipmi):
        self.ipmi = ipmi
        self.closed = False

    def close(self):
        self.closed = True


class Options:
    jobs = 4
    timeout = 10.0
    cipher = 17
    reconnect = None
    reconnect_attempts = 3
    reconnect_delay = 0.0
    log_file = None
    log_dir = "logs"
    no_log = True
    console = False


def _device(ipmi=None):
    return StubDevice(ipmi or StubIpmi())


def test_close_deactivate_has_no_cipher_or_net():
    argv, env = console._deactivate_argv(_device())
    assert argv == [
        "/usr/bin/ipmitool", "-I", "lanplus", "-H", "10.0.0.9",
        "-U", "root", "-P", "0penBmc", "sol", "deactivate",
    ]
    assert "IPMI_PASSWORD" not in env


def test_open_deactivate_uses_cipher_only():
    argv, _ = console._deactivate_argv(_device(), cipher=17)
    assert argv[-4:] == ["-C", "17", "sol", "deactivate"]


def test_activate_argv_legacy_flags():
    argv, _ = console._activate_argv(_device())
    assert argv[-9:] == [
        "-C", "17", "-N", "5", "-R", "5", "sol", "activate", "usesolkeepalive",
    ]


def test_env_mode_uses_dash_e_and_environment():
    device = _device(StubIpmi(use_env=True))
    argv, env = console._activate_argv(device)
    assert "-E" in argv
    assert "-P" not in argv
    assert env["IPMI_PASSWORD"] == "0penBmc"


def test_missing_ipmitool_raises():
    device = _device(StubIpmi(tool=None))
    with pytest.raises(RuntimeError):
        console._deactivate_argv(device)


def test_close_all_prefixes_nonempty_lines(monkeypatch):
    device = _device()
    device.ipmi.address = "10.0.0.9"
    monkeypatch.setattr(console, "build_device", lambda host, options, apply_deadline=True: device)
    captured = {}

    class Proc:
        returncode = 0
        stdout = "Info: SOL payload already deactivated\n\n"
        stderr = "extra\n"

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return Proc()

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    host = Host("cp-01", "10.0.0.9", {}, ["compute"])
    lines = []
    rc = console.close_all([host], Options(), emit=lines.append)
    assert rc == 0
    assert lines == [
        "cp-01: Info: SOL payload already deactivated",
        "cp-01: extra",
    ]
    assert device.closed is True


def test_close_all_timeout_reports_error(monkeypatch):
    device = _device()
    monkeypatch.setattr(console, "build_device", lambda host, options, apply_deadline=True: device)

    def fake_run(argv, **kwargs):
        raise console.subprocess.TimeoutExpired(cmd="ipmitool", timeout=10)

    monkeypatch.setattr(console.subprocess, "run", fake_run)
    host = Host("cp-01", "10.0.0.9", {}, ["compute"])
    lines = []
    rc = console.close_all([host], Options(), emit=lines.append)
    assert rc == 1
    assert lines == ["cp-01: ipmitool timed out"]


def test_console_requires_terminal(monkeypatch):
    class FakeIO:
        @staticmethod
        def isatty():
            return False

        def write(self, *_):
            return 0

        def flush(self):
            pass

    monkeypatch.setattr(
        console,
        "sys",
        types.SimpleNamespace(stdin=FakeIO, stdout=FakeIO, stderr=FakeIO),
    )
    host = Host("cp-01", "10.0.0.9", {}, ["compute"])
    assert console.attach(host, Options()) == 2


def test_nodeconsole_registered_and_parsed():
    assert "nodeconsole" in NODE_PROGRAMS
    args = parse_args(["nodeconsole", "cp-01"])
    assert args.action == "open"
    assert args.console is False
    args = parse_args(["nodeconsole", "cp-01", "close", "--no-reconnect"])
    assert args.action == "close"
    assert args.reconnect is False
