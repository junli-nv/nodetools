import argparse
from types import SimpleNamespace

import pytest

from nodetools_runtime.management_cli import datetime_with_zone
from nodetools_runtime.subjects import management

MANAGER = "/redfish/v1/Managers/1"
RESET = MANAGER + "/Actions/Manager.Reset"


def make_device(reset_method="GracefulRestart"):
    calls = []
    resources = {
        "/redfish/v1/Managers": {
            "Members": [{"@odata.id": MANAGER}, {"@odata.id": "/redfish/v1/Managers/Tray"}],
        },
        MANAGER: {
            "ManagerType": "BMC", "Model": "primary", "FirmwareVersion": "1.0",
            "Links": {"ManagerForServers": [{"@odata.id": "/redfish/v1/Systems/1"}]},
            "Actions": {"#Manager.Reset": {
                "target": RESET, "ResetType@Redfish.AllowableValues": [reset_method],
            }},
        },
        "/redfish/v1/Managers/Tray": {"ManagerType": "BMC", "Model": "tray"},
    }

    def request(path, body, **kwargs):
        calls.append((path, body, kwargs))
        return 204, None

    rf = SimpleNamespace(get=resources.get, post=request, patch=request)
    return SimpleNamespace(redfish=rf), calls


def test_bmc_reset_matches_primary_manager():
    device, calls = make_device()
    assert management.bmc(device, "reset") == ["BMC reset accepted"]
    assert calls == [(RESET, {"ResetType": "GracefulRestart"}, {})]


def test_bmc_clock_write_has_explicit_timezone():
    device, calls = make_device()
    with pytest.raises(argparse.ArgumentTypeError):
        datetime_with_zone("2026-11-24T19:30:00")
    value = datetime_with_zone("2026-11-24T19:30:00+08:00")
    management.bmc(device, "time", value)
    assert calls == [(MANAGER, {"DateTime": "2026-11-24T19:30:00+08:00"}, {"etag_source": MANAGER})]


def test_fru_reports_system_only():
    resources = {
        "/redfish/v1/Systems": {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
        "/redfish/v1/Systems/1": {
            "Model": "server", "SerialNumber": "ABC",
            "Links": {"Chassis": [{"@odata.id": "/redfish/v1/Chassis/Main"}]},
        },
    }
    device = SimpleNamespace(redfish=SimpleNamespace(get=resources.__getitem__))
    assert list(management.fru(device)) == ["System server serial=ABC"]


def test_parallel_queries_print_fast_node_first(monkeypatch, capsys):
    import builtins
    from threading import Event

    from nodetools_runtime import firmware_cli

    hosts = [SimpleNamespace(name=name) for name in ("slow", "fast")]
    inv = SimpleNamespace(default=lambda name, default: default, default_bool=lambda name, default: default)
    monkeypatch.setattr(firmware_cli.Inventory, "from_file", lambda path: inv)
    monkeypatch.setattr(firmware_cli.selection, "select", lambda *args, **kwargs: hosts)
    monkeypatch.setattr(firmware_cli, "build_device", lambda host, *args, **kwargs:
                        SimpleNamespace(name=host.name, close=lambda: None))
    fast_printed = Event()
    original_print = builtins.print

    def print_line(line, **kwargs):
        original_print(line, **kwargs)
        if line.startswith("fast:"):
            fast_printed.set()

    def query(device, args):
        if device.name == "slow":
            assert fast_printed.wait(2)
        return ["result"]

    monkeypatch.setattr(builtins, "print", print_line)
    args = SimpleNamespace(inventory=None, nodes="slow,fast", jobs=2)
    assert firmware_cli.run_selected(args, "nodefru", query, single=False) == 0
    assert capsys.readouterr().out.splitlines() == ["fast: result", "slow: result"]
