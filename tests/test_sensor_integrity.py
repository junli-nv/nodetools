import copy
import pytest

from nodetools_runtime.caps import common, fan, temp
from test_direct_read_contract import RECORDS, prepare

MODELS = ["smc_gb300_fan", "smc_gb300_temp", "liteon_pf1333_fan", "liteon_pf1333_temp"]


@pytest.mark.parametrize("record_id", MODELS)
@pytest.mark.parametrize("fault", ["missing", "duplicate", "bool", "null", "nan", "identity", "shape"])
def test_invalid_fixed_view_cannot_accept_a_partial_fallback(record_id, fault, monkeypatch):
    record = next(r for r in RECORDS if r["id"] == record_id)
    runner, host, transport = prepare(record, monkeypatch)
    body = transport.data[record["direct_requests"][0]]
    key = (("Members" if "Members" in body else "Fans") if record["capability"] == "fan"
           else ("TemperatureReadingsCelsius" if "TemperatureReadingsCelsius" in body else "Temperatures"))
    entries = body[key]
    reading_key = "ReadingCelsius" if key == "Temperatures" else "Reading"
    if fault == "missing":
        entries.pop(0)
    elif fault == "duplicate":
        entries.append(copy.deepcopy(entries[0]))
    elif fault in ("bool", "null", "nan"):
        entries[0][reading_key] = {"bool": True, "null": None, "nan": float("nan")}[fault]
    elif fault == "shape":
        body[key] = {}
    elif key == "TemperatureReadingsCelsius":
        entries[0]["DataSourceUri"] = "/redfish/v1/Chassis/wrong/Sensors/wrong"
    else:
        entries[0]["Id" if key == "Members" else "MemberId"] = "wrong"
    calls = []
    def fallback(device, *args, **kwargs):
        assert transport.requests == record["direct_requests"]
        calls.append(device.name)
        return [common.reading("partial", "partial", 12, unit="RPM")]
    module = fan if record["capability"] == "fan" else temp
    method = "_from_thermal_subsystem" if record["capability"] == "fan" else "_from_thermal"
    monkeypatch.setattr(module, method, fallback)
    result = runner._one(host)
    assert result["error"].startswith("incomplete " + record["capability"] + " data:")
    assert calls == [host.name]
    assert transport.requests == record["direct_requests"]


@pytest.mark.parametrize("fault", ["bool", "null", "nan", "missing", "wrong_id"])
def test_liteon_invalid_fast_path_requires_a_valid_complete_fallback(fault, monkeypatch):
    record = next(r for r in RECORDS if r["id"] == "liteon_pf1333_psu")
    runner, host, transport = prepare(record, monkeypatch)
    path = "/redfish/v1/Chassis/powershelf/Power"
    supplies = transport.data[path]["PowerSupplies"]
    if fault == "missing":
        supplies.pop(0)
    elif fault == "wrong_id":
        supplies[0]["MemberId"] = "unverified-slot"
    else:
        supplies[0]["PowerInputWatts"] = {"bool": True, "null": None, "nan": float("nan")}[fault]
    result = runner._one(host)
    assert transport.requests[0] == path
    assert transport.requests[1].endswith("/PowerSubsystem/PowerSupplies?$expand=.")
    if fault == "wrong_id":
        assert result["error"] is None
        assert [r["id"] for r in result["readings"]] == [str(i) for i in range(6)]
    else:
        assert result["error"].startswith("incomplete psu data:")
