import copy

import pytest

from nodetools_runtime.caps import common, psu
from nodetools_runtime.inventory import Host
from nodetools_runtime.model import Device
from nodetools_runtime.oem.nvl.liteon import LiteOnPowerShelf

BASE = "/redfish/v1/Chassis/"


class Redfish:
    def __init__(self, data):
        self.data = data
        self.requests = []

    def get(self, path):
        self.requests.append(path)
        return copy.deepcopy(self.data.get(path))


def shelf():
    data = {BASE.rstrip("/"): {"Members": [
        {"@odata.id": BASE + "PowerShelf_0"},
        {"@odata.id": BASE + "powershelf"},
    ]}}
    for view in ("PowerShelf_0", "powershelf"):
        collection = BASE + view + "/PowerSubsystem/PowerSupplies"
        data[collection + "?$expand=."] = {
            "Members": [{"@odata.id": collection + "/" + str(i)} for i in range(6)]
        }
        for i in range(6):
            data[collection + "/" + str(i)] = {
                "@odata.id": collection + "/" + str(i),
                "Id": str(i), "Name": "Power Supply " + str(i),
                "SerialNumber": "SERIAL-" + str(i),
                "Manufacturer": "LITE-ON", "Model": "SP-2552-6RB",
                "FirmwareVersion": "1", "Status": {"Health": "OK"},
            }
    data[BASE + "PowerShelf_0/Power"] = {"PowerSupplies": []}
    data[BASE + "powershelf/Power"] = {"PowerSupplies": [{
        "@odata.id": BASE + "powershelf/Power/Oem/LiteOn/PowerUnits/" + str(i),
        "MemberId": "powerdevice" + str(i), "Name": "Power Supply",
        "SerialNumber": "SERIAL-" + str(i), "Manufacturer": "LITE-ON",
        "PowerInputWatts": 900 + i, "Status": {"Health": "OK"},
    } for i in range(6)]}
    rf = Redfish(data)
    d = Device(Host("shelf-01", "192.0.2.51", {}, ["powershelf"]),
               redfish_factory=lambda _: rf)
    return d, rf


def test_generic_aliases_return_six_complete_supplies():
    device, rf = shelf()
    rows = psu.collect(device)
    assert len(rows) == 6
    assert {r["id"] for r in rows} == {str(i) for i in range(6)}
    assert {r["input_watts"] for r in rows} == set(range(900, 906))
    assert all(r["model"] == "SP-2552-6RB" for r in rows)
    assert all(r["value"] != "NA" for r in rows)
    assert all("/powershelf/" in r["source"] for r in rows)
    assert not any("/" + bad + "/" in p for p in rf.requests
                   for bad in ("DGX", "1", "PDB_0", "Chassis_0", "System.Embedded.1"))


def test_liteon_reads_only_canonical_view_and_merges_legacy_power():
    device, rf = shelf()
    device.odm = LiteOnPowerShelf()
    from types import SimpleNamespace
    device.options = SimpleNamespace(details=True)
    rows = psu.collect(device)
    assert len(rows) == 6
    assert all(r["input_watts"] is not None for r in rows)
    assert len(rf.requests) == 8
    assert all(p.startswith(BASE + "powershelf/") for p in rf.requests)


def test_liteon_respects_explicit_chassis():
    device, rf = shelf()
    device.odm = LiteOnPowerShelf()
    device.vars["chassis_id"] = "PowerShelf_0"
    rows = psu.collect(device)
    assert len(rows) == 6
    assert all(p.startswith(BASE + "PowerShelf_0/") for p in rf.requests)


def test_liteon_falls_back_when_canonical_view_is_absent():
    device, rf = shelf()
    device.odm = LiteOnPowerShelf()
    rf.data[BASE + "PowerShelf_0/Power"] = copy.deepcopy(rf.data[BASE + "powershelf/Power"])
    for path in list(rf.data):
        if path.startswith(BASE + "powershelf/"):
            del rf.data[path]
    rf.data[BASE.rstrip("/")]["Members"] = [{"@odata.id": BASE + "PowerShelf_0"}]
    rows = psu.collect(device)
    assert len(rows) == 6
    assert BASE.rstrip("/") in rf.requests
    assert {r["input_watts"] for r in rows} == set(range(900, 906))
    assert not any("/DGX/" in p for p in rf.requests)


def test_alias_wattage_changes_do_not_create_duplicate_slots():
    device, rf = shelf()
    rf.data[BASE + "PowerShelf_0/Power"] = copy.deepcopy(rf.data[BASE + "powershelf/Power"])
    for item in rf.data[BASE + "PowerShelf_0/Power"]["PowerSupplies"]:
        item["PowerInputWatts"] += 20
        item["@odata.id"] = item["@odata.id"].replace("/powershelf/", "/PowerShelf_0/")
    rows = psu.collect(device)
    assert len(rows) == 6
    assert {r["input_watts"] for r in rows} == set(range(900, 906))


@pytest.mark.parametrize("serial", [None, "unknown", "0", "not available", "distinct"])
def test_distinct_chassis_psus_with_same_slot_and_watts_are_preserved(serial):
    device, rf = shelf()
    rf.data = {BASE.rstrip("/"): {"Members": [
        {"@odata.id": BASE + "a"}, {"@odata.id": BASE + "b"},
    ]}}
    for view in ("a", "b"):
        collection = BASE + view + "/PowerSubsystem/PowerSupplies"
        rf.data[collection + "?$expand=."] = {"Members": [{
            "@odata.id": collection + "/0", "Id": "0", "Name": "PSU 0",
            "SerialNumber": view if serial == "distinct" else serial,
            "Manufacturer": "maker", "PowerInputWatts": 100,
        }]}
    rows = psu.collect(device)
    assert len(rows) == 2
    assert len({r["source"] for r in rows}) == 2


@pytest.mark.parametrize("source", ["live", "probe", "profile"])
def test_discovered_chassis_are_authoritative(source):
    device, rf = shelf()
    expected = ["PowerShelf_0", "powershelf"]
    if source == "probe":
        device.probe = {"chassis": ["powershelf"]}
        expected = ["powershelf"]
    elif source == "profile":
        device.profile = {"chassis": ["powershelf"]}
        expected = ["powershelf"]
    assert common.chassis_list(device, ("DGX", "1", "Chassis_0")) == expected
    assert common.chassis_list(device, ("PDB_0",)) == expected


def test_chassis_defaults_remain_available_when_discovery_fails():
    device, rf = shelf()
    rf.data = {}
    assert common.chassis_list(device, ("1", "DGX", "1")) == ["1", "DGX"]


def test_liteon_bulk_normal_read_is_one_get_with_stable_slot_ids():
    device, rf = shelf()
    device.odm = LiteOnPowerShelf()
    rows = psu.collect(device)
    assert [r["id"] for r in rows] == [str(i) for i in range(6)]
    assert all(r["input_watts"] is not None for r in rows)
    assert rf.requests == ["/redfish/v1/Chassis/powershelf/Power"]
