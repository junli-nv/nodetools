import copy
import json
import re
from pathlib import Path

import pytest

from nodetools_runtime import cli, model, oem, runtime
from nodetools_runtime.arguments import parse_args
from nodetools_runtime.caps import common, psu
from nodetools_runtime.inventory import Host

ROOT = Path(__file__).resolve().parents[1]
# Standalone management CLIs have their own production-entrypoint contracts.
RECORDS = [r for r in json.loads((ROOT / "docs/certified-reads.json").read_text())["records"]
           if r["test"] == "tests/test_direct_read_contract.py"]
SMC = next(r for r in RECORDS if r["id"] == "smc_gb300_psu")
PDB = "/redfish/v1/Chassis/PDB_0/Sensors/"
SHELF = "/redfish/v1/Chassis/powershelf/PowerSubsystem/PowerSupplies"


class Transport:
    transport_errors = 0

    def __init__(self, data):
        self.data = data
        self.requests = []

    def get(self, path):
        self.requests.append(path)
        return copy.deepcopy(self.data.get(path))

    def set_deadline(self, deadline):
        pass

    def close(self):
        pass


class Ipmi:
    def __init__(self, *args, **kwargs):
        pass

    def set_deadline(self, deadline):
        pass

    def sdr(self, *args):
        raise AssertionError("Unexpected IPMI on a certified read")


def prepare(record, monkeypatch):
    nvos_stdout = None
    if record["fixture"] == "nvl_gb300_n5500_8800021961":
        fixture = ROOT / "tests/fixtures/certified" / (record["fixture"] + ".json")
        nvos_stdout = json.loads(fixture.read_text())[record["direct_requests"][0]]
        data = {}
    elif record["fixture"].startswith(("hgx_b300_", "nvl_gb300_")):
        fixture = ROOT / "tests/fixtures/certified" / (record["fixture"] + ".json")
        data = json.loads(fixture.read_text())
    elif record["fixture"] == "smc_gb300_psu":
        data = {}
        for i in range(2):
            ident = "PDB_0_HSC_{}_Pwr_0".format(i)
            data[PDB + ident] = {
                "Id": ident, "Name": "PDB 0 HSC {} Pwr 0".format(i),
                "ReadingType": "Power", "ReadingUnits": "W",
                "Reading": 0.0 if i == 0 else 950.0, "Status": {"Health": "OK"},
            }
    elif record["fixture"] in ("liteon_pf1333_psu", "liteon_pf1333_psu_details"):
        data = {SHELF + "?$expand=.": {
            "Members": [{"@odata.id": SHELF + "/" + str(i)} for i in range(6)]
        }}
        for i in range(6):
            data[SHELF + "/" + str(i)] = {
                "@odata.id": SHELF + "/" + str(i), "Id": str(i),
                "Name": "PSU " + str(i), "SerialNumber": "SERIAL-" + str(i),
                "Status": {"Health": "OK"},
            }
        data["/redfish/v1/Chassis/powershelf/Power"] = {"PowerSupplies": [
            {"MemberId": "powerdevice" + str(i), "SerialNumber": "SERIAL-" + str(i),
             "PowerInputWatts": 900.0 + i} for i in range(6)
        ]}
    elif record["fixture"] in (
        "smc_gb300_fan", "smc_gb300_temp", "smc_gb300_inlet", "smc_gb300_monitor",
        "liteon_pf1333_fan", "liteon_pf1333_temp",
    ):
        family = record["fixture"].rsplit("_", 1)[0]
        data = json.loads((ROOT / "tests/fixtures/certified" / (family + ".json")).read_text())
        if record["capability"] in ("inlet", "monitor"):
            sensors = data["/redfish/v1/Chassis/Chassis_0/Sensors?$expand=."]["Members"]
            for sensor in sensors:
                data[sensor["@odata.id"]] = sensor
            for i in range(2):
                ident = "PDB_0_HSC_{}_Pwr_0".format(i)
                data[PDB+ident] = {"Id":ident, "ReadingType":"Power",
                    "ReadingUnits":"W", "Reading":900.0, "Status":{"Health":"OK"}}
    elif record["fixture"] == "nvidia_n5500_temp":
        data = json.loads((ROOT / "tests/fixtures/certified/nvidia_n5500_sensors.json").read_text())
    elif record["fixture"] == "nvidia_n5500_hsc":
        data = {}
        nvos_stdout = "49911 3927 182830000 0 0 0 0 0 0 0"
    else:
        raise AssertionError("Certification has no independent transport fixture")
    transport = Transport(data)
    if nvos_stdout is not None:
        from types import SimpleNamespace
        def ssh(host, options, command, deadline):
            marker = "nvos:lm5066i-vin-iin-pin" if command == runtime.nvos._HSC else "nvos:" + command
            assert marker == record["direct_requests"][0]
            transport.requests.append(marker)
            return SimpleNamespace(returncode=0, stdout=nvos_stdout, stderr="")
        monkeypatch.setattr(runtime.nvos, "_ssh", ssh)
    monkeypatch.setattr(runtime, "Redfish", lambda *args, **kwargs: transport)
    monkeypatch.setattr(runtime, "Ipmi", Ipmi)
    def forbidden_profile(*args):
        raise AssertionError("Startup discovery is forbidden on the certified normal path")
    monkeypatch.setattr(runtime.scan, "load", forbidden_profile)
    host = Host("fixture-01", "192.0.2.1", dict(record["configuration"]),
                [record["configuration"]["role"]])
    args = parse_args([record["command"], host.name, *record.get("options", [])])
    options = cli.Options(args)
    options.timeout = 3
    options.timeout_explicit = True
    options.node_timeout = 10
    info = record["capability"] in {"macs", "inventory", "eventlog", "power"}
    if info:
        options.action = getattr(args, "action", None) or getattr(args, "action_opt", None)
    runner = runtime.InfoRunner if info else runtime.Runner
    return runner(record["capability"], options), host, transport


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_certified_read_uses_exact_runtime_request_contract(record, monkeypatch):
    runner, host, transport = prepare(record, monkeypatch)
    result = runner._one(host)
    assert result["error"] is None
    rows = result.get("lines", result.get("readings"))
    assert len(rows) == record["expected_rows"]
    assert transport.requests == record["direct_requests"]
    assert len(transport.requests) == record["request_count"]
    counts = {
        "smc_gb300_psu": 2, "liteon_pf1333_psu": 1, "liteon_pf1333_psu_details": 8,
        "smc_gb300_fan": 1, "smc_gb300_temp": 9,
        "smc_gb300_inlet": 1, "smc_gb300_monitor": 4, "nvidia_n5500_temp": 3, "nvidia_n5500_hsc": 1,
        "liteon_pf1333_fan": 1, "liteon_pf1333_temp": 1,
        "wistron_gb300_fan": 1, "wistron_gb300_psu": 2,
        "wistron_gb300_inlet": 1, "wistron_gb300_monitor": 4,
        "delta_gb300_fan": 1, "delta_gb300_psu": 13, "wistron_gb300_macs": 1,
        "nvidia_n5500_1961_psu": 1, "nvidia_n5500_1961_temp": 1,
        "wistron_gb300_inventory": 45, "wistron_gb300_temp": 10, "wistron_gb300_eventlog": 1,
        "delta_gb300_temp": 1, "delta_gb300_power": 1,
        "delta_gb300_eventlog": 3, "delta_gb300_inventory": 1,
    }
    if record["fixture"].startswith("hgx_b300_"):
        vendor = record["configuration"]["oem"]
        counts[record["id"]] = {
            "fan": 1, "temp": 1, "inlet": 1, "psu": 8 if vendor == "smc" else 1,
            "monitor": 9 if vendor == "smc" else 3, "macs": 1 if vendor == "smc" else 30,
        }[record["capability"]]
    key = record["fixture"] if record["id"].startswith("liteon_pf1333_7rb_") else record["id"]
    assert record["request_count"] == counts[key]
    assert record["status"] == "verified"
    assert record["model"] and record["bmc_firmware"] and record["unverified_scope"]
    if record["capability"] == "psu":
        assert all(r.get("input_watts") is not None for r in result["readings"])
    elif record["capability"] == "macs":
        assert all("/Managers/" not in path for path in transport.requests)
        assert all(re.search(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", row) for row in rows)
    elif record["fixture"].startswith("hgx_b300_") and record["capability"] == "fan":
        assert all(row.get("unit") == "RPM" for row in rows)
    elif not record["fixture"].startswith("hgx_b300_") and record["capability"] in ("fan", "temp"):
        units = {}
        for row in result["readings"]:
            units[row["unit"]] = units.get(row["unit"], 0) + 1
        expected = {
            "smc_gb300_fan": {"%": 8, "RPM": 16},
            "smc_gb300_temp": {"Cel": 50},
            "nvidia_n5500_temp": {"Cel": 3},
            "liteon_pf1333_fan": {"RPM": 6},
            "liteon_pf1333_temp": {"Cel": 21},
            "wistron_gb300_fan": {"RPM": 16}, "delta_gb300_fan": {"RPM": 12},
            "nvidia_n5500_1961_temp": {"°C": 8},
            "wistron_gb300_temp": {"Cel": 57}, "delta_gb300_temp": {"Cel": 33},
        }
        key = record["fixture"] if record["id"].startswith("liteon_pf1333_7rb_") else record["id"]
        assert units == expected[key]


@pytest.mark.parametrize("fault", [
    "missing", "wrong_id", "wrong_unit", "wrong_type", "wrong_resource",
    "null", "nan", "bool", "not_an_object",
])
def test_invalid_fixed_set_falls_back_only_after_all_direct_reads(fault, monkeypatch):
    runner, host, transport = prepare(SMC, monkeypatch)
    path = SMC["direct_requests"][0]
    if fault == "missing":
        del transport.data[path]
    elif fault == "not_an_object":
        transport.data[path] = []
    else:
        field, value = {
            "wrong_id": ("Id", "wrong"),
            "wrong_unit": ("ReadingUnits", "A"),
            "wrong_type": ("ReadingType", "Temperature"),
            "wrong_resource": ("@odata.id", "/redfish/v1/Chassis/wrong/Sensors/wrong"),
            "null": ("Reading", None),
            "nan": ("Reading", float("nan")),
            "bool": ("Reading", True),
        }[fault]
        transport.data[path][field] = value
    fallback_calls = []
    def fallback(device, endpoint=None):
        assert transport.requests == SMC["direct_requests"]
        fallback_calls.append(device.name)
        return [common.reading("fallback", "fallback", 7, input_watts=7)]
    monkeypatch.setattr(psu, "_from_power_supplies", fallback)
    result = runner._one(host)
    assert result["error"].startswith("incomplete psu data:")
    assert [r["id"] for r in result["readings"]] == ["fallback"]
    assert fallback_calls == [host.name]


def test_zero_and_critical_health_do_not_trigger_discovery(monkeypatch):
    runner, host, transport = prepare(SMC, monkeypatch)
    transport.data[SMC["direct_requests"][0]]["Status"]["Health"] = "Critical"
    host.vars["manager_id"] = "BMC_0i"
    result = runner._one(host)
    assert result["error"] is None
    assert transport.requests == SMC["direct_requests"]
    assert result["readings"][0]["input_watts"] == 0
    assert result["readings"][0]["health"] == "Critical"


@pytest.mark.parametrize("selected", ["PDB_0", "custom"])
def test_explicit_chassis_selection_is_preserved(selected, monkeypatch):
    runner, host, transport = prepare(SMC, monkeypatch)
    host.vars["chassis_id"] = selected
    if selected == "custom":
        def fallback(device, endpoint=None):
            assert device.vars["chassis_id"] == "custom"
            assert transport.requests == []
            return [common.reading("selected", "selected", 7)]
        monkeypatch.setattr(psu, "_from_power_supplies", fallback)
    result = runner._one(host)
    assert result["error"] is None
    assert transport.requests == (SMC["direct_requests"] if selected == "PDB_0" else [])


@pytest.mark.parametrize("generation", ["gb200", None])
def test_unverified_generation_keeps_discovery(generation, monkeypatch):
    runner, host, _ = prepare(SMC, monkeypatch)
    if generation is None:
        host.vars.pop("generation")
    else:
        host.vars["generation"] = generation
    assert runner._needs_profile(host)
    assert not getattr(oem.for_device(model.Device(host)), "psu_read_sensors", ())


def test_new_fixed_capabilities_require_executable_certification():
    manifest = json.loads((ROOT / "docs/certified-reads.json").read_text())
    legacy = {(adapter, cap) for adapter, caps in manifest["legacy_unreviewed"].items()
              for cap in caps}
    certified = {(r["adapter"], r["capability"]) for r in manifest["records"]
                 if r["status"] == "verified" and r["capability"] not in {"macs", "power", "eventlog", "inventory", "firmware"}}
    declared = set()
    for registry in (oem._MODULES, oem._PLATFORM_MODULES, oem._ROLE_MODULES, oem._HGX_READ_MODELS):
        for adapter in registry.values():
            name = type(adapter).__module__ + "." + type(adapter).__name__
            declared.update((name, cap) for cap in (*getattr(adapter, "inventory_fixed_caps", ()),
                                                     *getattr(adapter, "nvos_fixed_caps", ())))
    assert not legacy & certified
    assert declared == legacy | certified
    # Standalone firmware CLI contracts are separate from sensor Runner caps.
    firmware_declared = set()
    for registry in (oem._MODULES, oem._PLATFORM_MODULES, oem._ROLE_MODULES, oem._HGX_READ_MODELS):
        for adapter in registry.values():
            if hasattr(adapter, "firmware_page_size"):
                name = type(adapter).__module__ + "." + type(adapter).__name__
                firmware_declared.add((name, "firmware"))
    firmware_certified = {(r["adapter"], r["capability"]) for r in manifest["records"]
                          if r["status"] == "verified" and r["capability"] == "firmware"}
    assert firmware_declared == firmware_certified
    for record in manifest["records"]:
        host = Host("fixture-01", "192.0.2.1", record["configuration"])
        adapter = oem.for_device(model.Device(host), configured_read=True)
        assert type(adapter).__module__ + "." + type(adapter).__name__ == record["adapter"]
        assert (ROOT / record["test"]).is_file()


def test_declared_null_temperature_slots_are_explicitly_unavailable(monkeypatch):
    record = next(r for r in RECORDS if r["id"] == "smc_gb300_temp")
    runner, host, transport = prepare(record, monkeypatch)
    result = runner._one(host)
    assert result["error"] is None
    unavailable = [r for r in result["readings"] if r.get("available") is False]
    assert len(unavailable) == 4
    assert all("SSD" in r["id"] and r["health"] == "Unknown" for r in unavailable)
    assert transport.requests == record["direct_requests"]


def test_compute_macs_incomplete_collection_fails(monkeypatch):
    record = next(r for r in RECORDS if r["capability"] == "macs"
                  and r["configuration"]["oem"] == "smc")
    runner, host, transport = prepare(record, monkeypatch)
    first = record["direct_requests"][0]
    second = first + "&$skip=2"
    transport.data[first]["Members@odata.nextLink"] = second
    result = runner._one(host)
    assert not result["lines"]
    assert "incomplete EthernetInterfaces collection" in result["error"]
    assert transport.requests == [first, second]


def test_compute_macs_falls_back_to_unexpanded_ethernet_collection(monkeypatch):
    record = next(r for r in RECORDS if r["capability"] == "macs"
                  and r["configuration"]["oem"] == "smc")
    runner, host, transport = prepare(record, monkeypatch)
    expanded = record["direct_requests"][0]
    base = expanded.split("?")[0]
    member = base + "/1"
    del transport.data[expanded]
    transport.data[base] = {"Members": [{"@odata.id": member}], "Members@odata.count": 1}
    transport.data[member] = {"Id": "1", "MACAddress": "02:00:00:00:00:11"}
    result = runner._one(host)
    assert result["error"] is None
    assert result["lines"] == ["fixture-01: 1, 02:00:00:00:00:11"]
    assert transport.requests == [expanded, base, member]


def test_kaytus_compute_macs_fails_when_advertised_port_is_unreadable(monkeypatch):
    record = next(r for r in RECORDS if r["capability"] == "macs"
                  and r["configuration"]["oem"] == "kaytus")
    runner, host, transport = prepare(record, monkeypatch)
    port = next(path for path in record["direct_requests"] if "/NetworkPorts/" in path)
    del transport.data[port]
    result = runner._one(host)
    assert not result["lines"]
    assert f"no data at {port}" in result["error"]
    assert transport.requests[-1] == port


def test_kaytus_empty_pending_bios_is_successful(monkeypatch):
    record = next(r for r in RECORDS if r["capability"] == "macs"
                  and r["configuration"]["oem"] == "kaytus")
    _, host, transport = prepare(record, monkeypatch)
    path = "/redfish/v1/Systems/1/Bios/Settings"
    transport.data[path] = {"Attributes": {}}
    options = cli.Options(parse_args(["nodebios", host.name, "pending"]))
    options.timeout, options.node_timeout, options.action = 3, 10, "pending"
    result = runtime.InfoRunner("bios", options)._one(host)
    assert result["error"] is None
    assert result["lines"] == ["fixture-01: No pending BIOS settings"]
    assert transport.requests == [path]


@pytest.mark.parametrize("use_fallback", [False, True])
def test_wistron_hsc_reads_keep_zero_and_alarm(use_fallback, monkeypatch):
    record = next(r for r in RECORDS if r["id"] == "wistron_gb300_psu")
    runner, host, transport = prepare(record, monkeypatch)
    paths = record["direct_requests"]
    collection = "/redfish/v1/Chassis/PDB_0/Sensors?$expand=."
    if use_fallback:
        transport.data[paths[0]]["ReadingUnits"] = "A"
        details = transport.data[collection]["Members"]
        sensor = next(entry for entry in details if entry["@odata.id"] == paths[0])
    else:
        sensor = transport.data[paths[0]]
    sensor.update(Reading=0, Status={"State": "Enabled", "Health": "Critical"})
    result = runner._one(host)
    assert result["error"] is None
    assert len(result["readings"]) == 2
    assert result["readings"][0]["input_watts"] == 0
    assert result["readings"][0]["health"] == "Critical"
    assert transport.requests == paths + ([collection] if use_fallback else [])


def test_wistron_hsc_does_not_accept_an_incomplete_fallback(monkeypatch):
    record = next(r for r in RECORDS if r["id"] == "wistron_gb300_psu")
    runner, host, transport = prepare(record, monkeypatch)
    paths = record["direct_requests"]
    collection = "/redfish/v1/Chassis/PDB_0/Sensors?$expand=."
    transport.data.pop(paths[0])
    members = transport.data[collection]["Members"]
    transport.data[collection]["Members"] = [entry for entry in members if entry["@odata.id"] != paths[0]]
    assert runner._one(host)["error"]
    assert transport.requests == paths + [collection]


@pytest.mark.parametrize("record_id", ["wistron_gb300_fan", "delta_gb300_fan"])
def test_nvl_fixed_fan_honors_selected_chassis(record_id, monkeypatch):
    record = next(r for r in RECORDS if r["id"] == record_id)
    runner, host, transport = prepare(record, monkeypatch)
    host.vars["chassis_id"] = "Selected"
    alternate = re.sub(r"/Chassis/[^/]+", "/Chassis/Selected", record["direct_requests"][0])
    transport.data[alternate] = transport.data[record["direct_requests"][0]]
    assert runner._one(host)["error"] is None
    assert transport.requests == [alternate]


@pytest.mark.parametrize("selected", [None, "HostSystem"])
def test_wistron_default_mac_uses_scoped_boot_options_fallback(selected, monkeypatch):
    record = next(r for r in RECORDS if r["id"] == "wistron_gb300_macs")
    runner, host, transport = prepare(record, monkeypatch)
    expanded = record["direct_requests"][0]
    if selected:
        host.vars["system_id"] = selected
    target = expanded.replace("/system/", "/" + (selected or "system") + "/")
    plain = target.split("?", 1)[0]
    transport.data[plain] = transport.data.pop(expanded)
    result = runner._one(host)
    assert result["error"] is None
    assert len(result["lines"]) == 7
    assert transport.requests == [target, plain]


def test_wistron_fan_retries_direct_path_before_scoped_fallback(monkeypatch):
    record = next(record for record in RECORDS if record["id"] == "wistron_gb300_fan")
    runner, host, transport = prepare(record, monkeypatch)
    original_get = transport.get

    def transient_timeout(path):
        if not transport.requests:
            transport.requests.append(path)
            transport.timeout_reason = "Redfish read timed out"
            return None
        transport.timeout_reason = None
        return original_get(path)

    monkeypatch.setattr(transport, "get", transient_timeout)
    result = runner._one(host)
    assert result["error"] is None
    assert len(result["readings"]) == 16
    assert transport.requests == record["direct_requests"] * 2


def test_fixed_event_log_reads_next_page_and_resolves_members():
    from types import SimpleNamespace

    from nodetools_runtime.subjects.common import entries

    path = "/redfish/v1/Managers/BMC_0/LogServices/EventLog/Entries"
    next_page = path + "?$skip=1"
    detail = path + "/2"
    first = {"Id": "1", "Message": "First event"}
    second = {"Id": "2", "Message": "Second event"}
    transport = Transport({
        path: {"Members@odata.count": 2, "Members": [first],
               "Members@odata.nextLink": next_page},
        next_page: {"Members@odata.count": 2, "Members": [{"@odata.id": detail}]},
        detail: second,
    })
    assert entries(SimpleNamespace(redfish=transport), path, require_members=True) == [first, second]
    assert transport.requests == [path, next_page, detail]


def test_mixed_nvl_inventory_example_selects_direct_shelf_reads():
    from nodetools_runtime.inventory import Inventory
    inv = Inventory.from_file(str(ROOT / "inventory.nvl.example.ini"))
    hosts = [host for host in inv.hosts.values() if host.role == "powershelf"]
    assert {host.vars["oem"] for host in hosts} == {"liteon", "delta"}
    assert {"liteon-7rb", "liteon-9rb"} <= {host.name for host in hosts}
    for host in hosts:
        for command, cap in (("psfan", "fan"), ("pspsu", "psu")):
            options = cli.Options(parse_args([command, host.name]))
            assert not runtime.Runner(cap, options)._needs_profile(host)
