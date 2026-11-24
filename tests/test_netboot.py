from types import SimpleNamespace

import pytest

from nodetools_runtime import cli
from nodetools_runtime.errors import TransportError
from nodetools_runtime.subjects import netboot


def test_smc_moves_only_selected_pxe_entries_and_verifies_both_orders(monkeypatch):
    system = "/redfish/v1/Systems/1"
    path = system + netboot.SMC_ORDER
    first = "UEFI PXE IPv4 Intel NIC (MAC:020000000001)"
    target = "UEFI PXE IPv4 ConnectX-7 NIC (MAC:020000000002)"
    ipv6 = "UEFI PXE IPv6 ConnectX-7 NIC (MAC:020000000002)"
    groups = ["UEFI USB", "UEFI Hard Disk", "UEFI Network"]
    state = {"FixedBootOrder": groups, "UEFINetwork": [first, ipv6, target]}
    device = SimpleNamespace(name="smc")
    plan = netboot.preferences(device, system, {}, state, [], {}, {"type": "cx7"}, "ipv4", "prefer")
    assert plan.after == {
        "FixedBootOrder": ["UEFI USB", "UEFI Network", "UEFI Hard Disk"],
        "UEFINetwork": [target, first, ipv6],
    }
    assert plan.target == path
    monkeypatch.setattr(netboot.common, "resource", lambda *args, **kwargs: state)
    patches = []

    def patch(device, request_path, payload, etag_source):
        patches.append((request_path, payload, etag_source))
        return 202, {}

    monkeypatch.setattr(netboot.common, "patch", patch)
    monkeypatch.setattr(netboot.time, "sleep", lambda _: None)
    with pytest.raises(TransportError, match="did not match on readback"):
        netboot.apply(device, plan)
    assert patches == [(path, plan.after, path)]
    state["UEFINetwork"] = [ipv6, first, target]
    with pytest.raises(TransportError, match="changed after preflight"):
        netboot.apply(device, plan)
    assert len(patches) == 1


def test_cli_aborts_batch_if_a_node_does_not_match(monkeypatch):
    stages = []
    hosts = [SimpleNamespace(name=name) for name in ("one", "two")]
    monkeypatch.setattr(cli.Inventory, "from_file", lambda path: object())
    monkeypatch.setattr(cli, "_apply_defaults", lambda *args: None)
    monkeypatch.setattr(cli, "_role_hosts", lambda *args: hosts)
    monkeypatch.setattr(cli, "render", lambda *args, **kwargs: [])

    class Runner:
        def __init__(self, subject, options):
            self.options = options

        def stream(self, hosts):
            stages.append(getattr(self.options, "netboot_plans", None))
            yield {"name": "one", "netboot_plan": object(), "error": None}
            yield {"name": "two", "error": "no matching ports"}

    monkeypatch.setattr(cli, "InfoRunner", Runner)
    assert cli.main(["nodenetboot", "one,two", "prefer", "--type", "bf3"]) == 1
    assert stages == [None]


def test_inventory_uses_reported_slot_port_and_pci():
    from nodetools_runtime.subjects import boot_nics

    chassis = "/redfish/v1/Chassis/1"
    adapters = chassis + "/NetworkAdapters"
    ports = adapters + "/nic/Ports"
    data = {
        adapters + "?$expand=.": {"Members": [{
            "@odata.id": adapters + "/nic", "Model": "ConnectX-6",
            "Location": {"PartLocation": {"LocationType": "Slot", "ServiceLabel": "Slot 3"}},
            "Ports": {"@odata.id": ports},
        }]},
        ports + "?$expand=.": {"Members": [{
            "@odata.id": ports + "/p2", "PhysicalPortNumber": "2", "BDF": "4e:00.1",
            "Ethernet": {"AssociatedMACAddresses": ["02:00:00:00:00:01"]},
        }]},
    }
    device = SimpleNamespace(vars={}, redfish=SimpleNamespace(get=data.get))
    info = boot_nics.inventory(device, {}, chassis)
    row = netboot.entry("B1", "UEFI PXE IPv4 NIC (MAC:020000000001)", "", info)
    assert row.matches({"type": "cx6", "slot": "3", "port": "2", "pci": "0000:4e:00.1"}, "ipv4")
    assert not row.matches({"port": "1"}, "ipv4")



def test_smc_accepts_firmware_refresh_of_network_group_label():
    expected = {"FixedBootOrder": ["UEFI Network: old", "UEFI Hard Disk"], "UEFINetwork": ["new", "old"]}
    actual = {"FixedBootOrder": ["UEFI Network: new", "UEFI Hard Disk"], "UEFINetwork": ["new", "old"]}
    assert netboot.confirmed(actual, expected)
    actual["UEFINetwork"].reverse()
    assert not netboot.confirmed(actual, expected)
