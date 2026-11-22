import types

from nodetools_runtime import model, runtime
from nodetools_runtime.inventory import Host


class _Options:
    def __init__(self, no_cache=False):
        self.no_cache = no_cache
        self.action = None
        self.timeout = 10
        self.jobs = 1
        self.node_timeout = 30.0
        self.ipmi_env = None
        self.user = None
        self.password = None


def _host():
    return Host("cp-01", "10.0.0.1", {}, ["compute"])


def test_odm_for_reads_scan_profile():
    assert model.odm_for(types.SimpleNamespace(profile={"odm": "lenovo"})) == "lenovo"


def test_odm_for_defaults_to_unknown_without_profile():
    assert model.odm_for(types.SimpleNamespace(profile=None)) == "unknown"
    assert model.odm_for(types.SimpleNamespace(profile={})) == "unknown"


def test_odm_for_ignores_empty_odm():
    assert model.odm_for(types.SimpleNamespace(profile={"odm": None})) == "unknown"


def _device(transport_errors, profile=None):
    device = model.Device(_host())
    device.redfish = types.SimpleNamespace(
        transport_errors=transport_errors, close=lambda: None
    )
    device.ipmi = None
    device.profile = profile
    return device


def test_info_runner_reports_unreachable_when_silent(monkeypatch):
    monkeypatch.setattr(runtime, "build_device", lambda host, options: _device(1))
    runner = runtime.InfoRunner("eventlog", _Options())
    runner.handler = lambda device, action, options: []
    result = runner._one(_host())
    assert result["lines"] == []
    assert result["error"] and "unreachable" in result["error"]


def test_info_runner_keeps_empty_output_when_reachable(monkeypatch):
    monkeypatch.setattr(runtime, "build_device", lambda host, options: _device(0))
    runner = runtime.InfoRunner("eventlog", _Options())
    runner.handler = lambda device, action, options: []
    result = runner._one(_host())
    assert result["lines"] == []
    assert result["error"] is None


def test_info_runner_dispatches_from_scan_profile(monkeypatch):
    captured = []
    device = _device(0, profile={"odm": "smc"})
    monkeypatch.setattr(runtime, "build_device", lambda host, options: device)
    runner = runtime.InfoRunner("power", _Options())
    runner.handler = lambda d, action, options: captured.append(d.odm) or ["ok"]
    result = runner._one(_host())
    assert result["error"] is None
    assert captured and captured[0] is not None


def test_info_runner_unscanned_falls_back_to_base(monkeypatch):
    captured = []
    device = _device(0, profile=None)
    monkeypatch.setattr(runtime, "build_device", lambda host, options: device)
    runner = runtime.InfoRunner("power", _Options())
    runner.handler = lambda d, action, options: captured.append(d.odm) or ["ok"]
    runner._one(_host())
    from nodetools_runtime import oem

    assert captured[0] is oem.get("unknown")
