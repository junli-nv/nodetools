import copy
import json
from pathlib import Path

import pytest

from nodetools_runtime import cli, model, oem, runtime
from nodetools_runtime.arguments import parse_args
from nodetools_runtime.caps import common, psu
from nodetools_runtime.inventory import Host

ROOT = Path(__file__).resolve().parents[1]
RECORDS = json.loads((ROOT / "docs/certified-reads.json").read_text())["records"]
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
    if record["fixture"] == "smc_gb300_psu":
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
            assert command == runtime.nvos._HSC
            transport.requests.append("nvos:lm5066i-vin-iin-pin")
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
    return runtime.Runner(record["capability"], options), host, transport


@pytest.mark.parametrize("record", RECORDS, ids=lambda r: r["id"])
def test_certified_read_uses_exact_runtime_request_contract(record, monkeypatch):
    runner, host, transport = prepare(record, monkeypatch)
    result = runner._one(host)
    assert result["error"] is None
    assert len(result["readings"]) == record["expected_rows"]
    assert transport.requests == record["direct_requests"]
    assert len(transport.requests) == record["request_count"]
    assert record["request_count"] == {
        "smc_gb300_psu": 2, "liteon_pf1333_psu": 1, "liteon_pf1333_psu_details": 8,
        "smc_gb300_fan": 1, "smc_gb300_temp": 9,
        "smc_gb300_inlet": 1, "smc_gb300_monitor": 4, "nvidia_n5500_temp": 3, "nvidia_n5500_hsc": 1,
        "liteon_pf1333_fan": 1, "liteon_pf1333_temp": 1,
    }[record["id"]]
    assert record["status"] == "verified"
    assert record["model"] and record["bmc_firmware"] and record["unverified_scope"]
    if record["capability"] == "psu":
        assert all(r.get("input_watts") is not None for r in result["readings"])
    elif record["capability"] in ("fan", "temp"):
        units = {}
        for row in result["readings"]:
            units[row["unit"]] = units.get(row["unit"], 0) + 1
        expected = {
            "smc_gb300_fan": {"%": 8, "RPM": 16},
            "smc_gb300_temp": {"Cel": 50},
            "nvidia_n5500_temp": {"Cel": 3},
            "liteon_pf1333_fan": {"RPM": 6},
            "liteon_pf1333_temp": {"Cel": 21},
        }
        assert units == expected[record["id"]]


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
                 if r["status"] == "verified"}
    declared = set()
    for registry in (oem._MODULES, oem._PLATFORM_MODULES, oem._ROLE_MODULES):
        for adapter in registry.values():
            name = type(adapter).__module__ + "." + type(adapter).__name__
            declared.update((name, cap) for cap in (*getattr(adapter, "inventory_fixed_caps", ()),
                                                     *getattr(adapter, "nvos_fixed_caps", ())))
    assert not legacy & certified
    assert declared == legacy | certified
    for record in manifest["records"]:
        host = Host("fixture-01", "192.0.2.1", record["configuration"])
        adapter = oem.for_device(model.Device(host))
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
