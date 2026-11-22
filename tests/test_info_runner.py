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


def test_platform_and_oem_dispatch_are_independent():
    from nodetools_runtime import oem

    for product, maker, family, adapter in (
        ("HGX B200", "Aivres", "hgx", "aivres"),
        ("B300", "Supermicro", "hgx", "smc"),
        ("HGX B300", "KAYTUS", "hgx", "generic"),
        ("GB200 NVL72", "Compal", "nvl", "generic"),
        ("GB300 NVL72", "Dell", "nvl", "dell"),
        ("GB300 NVL72", "NVIDIA", "nvl", "gb200"),
        ("HGX B300", "NVIDIA", "hgx", "generic"),
        ("GB200", "", "nvl", "generic"),
    ):
        device = model.Device(_host())
        device.identity = {"model": product, "manufacturer": maker}
        assert model.platform_for(device) == family
        assert oem.for_device(device).name == adapter


def test_inventory_hardware_overrides_and_cache_keys():
    from nodetools_runtime import oem, scan

    host = Host("HGX390", groups=["compute"],
                variables={"platform": "gb300", "oem": "netweb"})
    device = model.Device(host)
    device.identity = {"model": "HGX B300", "manufacturer": "Aivres"}
    assert model.platform_for(device) == "nvl"
    assert oem.for_device(device).name == "generic"
    assert scan.key_for(device.identity, device.vars) != scan.key_for(device.identity)
    assert scan.key_for(device.identity, device.vars) != scan.key_for(
        device.identity, {"platform": "gb300", "oem": "compal"}
    )


def test_kaytus_oem_is_retained_without_an_avr_override():
    device = model.Device(Host("HGX390", groups=["compute"]))
    device.identity = {"manufacturer": "KAYTUS", "model": "KR9288-X3-A0-F0-01"}
    assert model.platform_for(device) == "hgx"
    assert model.odm_for(device) == "kaytus"
