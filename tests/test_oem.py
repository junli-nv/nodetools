import pytest

from nodetools_runtime import oem


def test_known_odms_registered():
    for name in ("gb200", "dell", "lenovo", "smc", "ami", "byd",
                 "giga", "dgx", "wistron", "inventec",
                 "aivres", "compal", "netweb"):
        assert oem.get(name).name in ("giga", "gb200") or oem.get(name).name == name


def test_gigabyte_alias():
    assert oem.get("gigabyte").name == "giga"


def test_unknown_falls_back_to_generic():
    assert oem.get("nope").name == "generic"


@pytest.mark.parametrize("command", ["nodepsu", "nvswpsu", "pspsu"])
def test_psu_actions_are_rejected_before_inventory(command, capsys):
    from nodetools_runtime import cli

    assert cli.main([command, "-i", "/nonexistent-inventory", "cp-01", "on"]) == 1
    assert "is read-only" in capsys.readouterr().err


def test_fan_modes_stay_with_the_oem(monkeypatch):
    import types

    from nodetools_runtime import model, runtime
    from nodetools_runtime.errors import NodetoolsError
    from nodetools_runtime.inventory import Host

    device = model.Device(Host("HGX390", groups=["compute"]))
    device.identity = {"manufacturer": "KAYTUS", "model": "KR9288-X3-A0-F0-01"}
    assert oem.for_device(device, "fan").name == "aivres"

    options = types.SimpleNamespace(action="low")
    runner = runtime.Runner("fan", options)
    netweb = oem.get("netweb")
    calls = []
    monkeypatch.setattr(netweb, "fan_control", lambda device, mode: calls.append(mode))
    runner._control(device, netweb)
    assert calls == ["low"]
    for mode in ("on", "off", "cycle"):
        options.action = mode
        with pytest.raises(NodetoolsError, match="invalid fan action"):
            runner._control(device, netweb)
    assert calls == ["low"]


@pytest.mark.parametrize("platform, family", [("hgx", "hgx"), ("nvl", "nvl")])
def test_dell_dispatch_uses_the_inventory_platform(platform, family):
    from nodetools_runtime.model import Device
    from nodetools_runtime.inventory import Host

    host = Host("HGX999", variables={"platform": platform, "oem": "dell"}, groups=["compute"])
    assert oem.for_device(Device(host)).family == family


def test_dell_nvl_virtual_media_reads_links_and_rejects_writes():
    from types import SimpleNamespace

    from nodetools_runtime.errors import CapUnsupported
    from nodetools_runtime.inventory import Host
    from nodetools_runtime.model import Device
    from nodetools_runtime.subjects import vm

    manager = "/redfish/v1/Managers/Tray"
    media = manager + "/VirtualMedia"
    cd = media + "/CD"
    responses = {
        "/redfish/v1/Managers": {"Members": [{"@odata.id": manager}]},
        manager: {"VirtualMedia": {"@odata.id": media}},
        media: {"Members": [{"@odata.id": cd}]},
        cd: {"Id": "CD", "Inserted": True, "ImageName": "boot.iso"},
    }
    device = Device(Host("tray-01", variables={"platform": "nvl", "oem": "dell"},
                         groups=["compute"]))
    requests = []

    def get(path):
        requests.append(path)
        return responses.get(path)

    device.redfish = SimpleNamespace(get=get)
    device.odm = oem.for_device(device)
    for action in ("mount", "insert", "enable", "eject", "umount", "stop"):
        with pytest.raises(CapUnsupported, match="dell/nvl"):
            vm.collect(device, action, None)
    assert not requests
    assert vm.collect(device, "status", None) == ["tray-01: CD\tinserted=True\timage=boot.iso"]


def test_generation_specific_adapter_takes_precedence(monkeypatch):
    from nodetools_runtime.model import Device, generation_for
    from nodetools_runtime.inventory import Host
    from nodetools_runtime import scan

    device = Device(Host("tray", groups=["compute"], variables={"oem": "dell"}))
    device.identity = {"model": "Dell GB200 NVL72"}
    shared = oem.for_device(device)
    adapter = object()
    monkeypatch.setitem(oem._PLATFORM_MODULES, ("nvl", "dell", "compute", "gb300"), adapter)
    assert generation_for(device) == "gb200"
    assert oem.for_device(device) is shared
    device.vars["generation"] = "gb300"
    assert generation_for(device) == "gb300"
    assert oem.for_device(device) is adapter
    assert scan.key_for(device.identity, device.vars) != scan.key_for(device.identity)
