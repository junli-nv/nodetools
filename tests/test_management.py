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


def test_bmc_selects_manager_linked_to_system_on_wistron():
    device, _ = make_device()
    primary = device.redfish.get(MANAGER)
    primary["Links"]["ManagerForServers"][0]["@odata.id"] = "/redfish/v1/Systems/system"
    device.redfish.get("/redfish/v1/Managers/Tray")["Links"] = {
        "ManagerForServers": [{"@odata.id": "/redfish/v1/Systems/HGX_Baseboard_0"}],
    }
    assert management.bmc(device, "info") == ["Model primary", "FirmwareVersion 1.0"]


def test_firmware_collection_uses_expanded_items_and_fetches_missing_details():
    from nodetools_runtime.subjects import firmware

    path = "/redfish/v1/UpdateService/FirmwareInventory"
    inline = {"@odata.id": path + "/CX8_0", "Version": "1"}
    resources = {
        path + "?$expand=.": {"Members": [inline, {"@odata.id": path + "/CX8_1"}]},
        path + "/CX8_1": {"Version": "2"},
    }
    device = SimpleNamespace(redfish=SimpleNamespace(get=resources.__getitem__))
    result = list(firmware.collection(device, path, required=True, expand=True))
    assert result == [(path + "/CX8_0", inline), (path + "/CX8_1", {"Version": "2"})]


def test_bmc_read_prefers_explicit_manager_and_falls_back():
    device, _ = make_device()
    device.vars = {"manager_id": "1"}
    original_get = device.redfish.get
    calls = []

    def get(path):
        calls.append(path)
        return original_get(path)

    device.redfish.get = get
    assert management.bmc(device, "info") == ["Model primary", "FirmwareVersion 1.0"]
    assert calls == [MANAGER]

    calls.clear()
    device.vars["manager_id"] = "missing"
    assert management.bmc(device, "info") == ["Model primary", "FirmwareVersion 1.0"]
    assert calls[:2] == ["/redfish/v1/Managers/missing", "/redfish/v1/Managers"]


def test_firmware_paged_collection_returns_all_components_and_rejects_repeat():
    from types import SimpleNamespace

    from nodetools_runtime.errors import TransportError
    from nodetools_runtime.subjects import firmware

    path = "/redfish/v1/UpdateService/FirmwareInventory"
    first = {"@odata.id": path + "/A", "Version": "1"}
    second = {"@odata.id": path + "/B", "Version": "2"}
    query = path + "?$expand=.&$top=16"
    pages = {
        query: {"Members@odata.count": 2, "Members": [first]},
        query + "&$skip=1": {"Members@odata.count": 2, "Members": [second]},
    }
    device = SimpleNamespace(redfish=SimpleNamespace(get=pages.get))
    read = lambda: list(firmware.collection(device, path, required=True, expand=True, page_size=16))
    assert read() == [(path + "/A", first), (path + "/B", second)]
    pages[query + "&$skip=1"]["Members"] = [first]
    with pytest.raises(TransportError, match="repeated collection member"):
        read()
