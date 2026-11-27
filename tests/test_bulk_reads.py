import copy
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from nodetools_runtime import batch_audit, cli
from nodetools_runtime.arguments import parse_args
from nodetools_runtime.inventory import Host


def response(status, body):
    return SimpleNamespace(status_code=status, ok=status < 400, headers={},
                           json=lambda: copy.deepcopy(body), text="")


def powers():
    return {"PowerSupplies": [
        {"MemberId": "powerdevice" + str(i), "Name": "Power Supply",
         "PowerInputWatts": 0.0 if i == 0 else 900.0 + i,
         "Status": {"Health": "Critical" if i == 0 else "OK"}}
        for i in range(6)
    ]}


def fans():
    return {"Fans": [
        {"MemberId": "p{}_fan1".format(i), "Name": "PSU {} fan1".format(i),
         "Reading": 0 if i == 0 else 6000, "ReadingUnits": "RPM",
         "Status": {"Health": "Critical" if i == 0 else "OK"}}
        for i in range(6)
    ]}


def hosts(count):
    config = dict(platform="nvl", oem="liteon", generation="gb300", chassis_id="powershelf")
    return [Host("shelf-" + str(i), "192.0.2." + str(i+1), dict(config), ["powershelf"])
            for i in range(count)]


def options(cap, jobs, budget=2):
    opts = cli.Options(parse_args(["ps"+cap, "shelf-0"]))
    opts.jobs, opts.timeout, opts.timeout_explicit, opts.node_timeout = jobs, 1, True, budget
    return opts


@pytest.mark.parametrize("cap", ["fan", "psu"])
def test_200_concurrent_reads_isolate_failures_and_close_every_transport(cap, monkeypatch):
    barrier = threading.Barrier(200)
    lock = threading.Lock()
    active = peak = 0

    def get(session, url, **kwargs):
        nonlocal active, peak
        parsed = urlsplit(url)
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=8)
            if parsed.hostname == "192.0.2.1":
                raise requests.ConnectTimeout("fixture unreachable")
            if parsed.hostname == "192.0.2.2":
                return response(401, {})
            expected = "/redfish/v1/Chassis/powershelf/" + ("Power" if cap == "psu" else "Thermal")
            assert parsed.path == expected
            return response(200, powers() if cap == "psu" else fans())
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(requests.Session, "get", get)
    results = []
    summary = batch_audit.run(hosts(200), cap, options(cap, 200), 6, results.append)
    assert peak == 200
    assert summary["completed"] == 200
    assert summary["outcomes"] == {"passed": 198, "failed": 2}
    assert summary["requests"] == 200
    assert len({r["name"] for r in results}) == 200
    assert all(r["closed"] and len(r["requests"]) == 1 for r in results)
    failed = {r["name"]: r for r in results if r["acceptance"] != "passed"}
    assert "ConnectTimeout" in failed["shelf-0"]["error"]
    assert "HTTP 401" in failed["shelf-1"]["error"]
    good = next(r for r in results if r["acceptance"] == "passed")
    assert good["readings"][0]["health"] == "Critical"
    key = "input_watts" if cap == "psu" else "value"
    assert good["readings"][0][key] == 0


def test_each_queued_node_receives_its_own_execution_budget(monkeypatch):
    timeouts = []
    def get(session, url, **kwargs):
        timeouts.append(kwargs["timeout"])
        time.sleep(.04)
        return response(200, powers())
    monkeypatch.setattr(requests.Session, "get", get)
    results = []
    summary = batch_audit.run(hosts(4), "psu", options("psu", 1, .1), 6, results.append)
    assert summary["seconds"] > .1
    assert summary["outcomes"] == {"passed": 4}
    assert len(timeouts) == 4
    assert all(.05 < value.total <= .1 and 0 < value.connect_timeout <= value.total
               for value in timeouts)
    for value in timeouts:
        probe = value.clone()
        probe.start_connect()
        assert 0 < probe.read_timeout <= probe.total
    assert all(row["closed"] for row in results)


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf")])
def test_bulk_acceptance_rejects_invalid_watts(value):
    result = dict(cap="psu", error=None, readings=[
        dict(id=str(i), input_watts=value if i == 0 else 900) for i in range(6)
    ])
    assert batch_audit.validate(result, 6) == "incomplete"


def test_bulk_acceptance_rejects_duplicate_or_missing_slots():
    result = dict(cap="psu", error=None, readings=[
        dict(id="0", input_watts=900) for _ in range(6)
    ])
    assert batch_audit.validate(result, 6) == "incomplete"
    result["readings"] = []
    assert batch_audit.validate(result, 6) == "incomplete"


def test_tls_reuse_does_not_share_credentials_or_change_verified_mode():
    import ssl
    from nodetools_runtime.transport.redfish import Redfish, _UnverifiedAdapter
    a = Redfish("192.0.2.1", "user-a", "fixture-a")
    b = Redfish("192.0.2.2", "user-b", "fixture-b")
    verified = Redfish("192.0.2.3", "user-c", "fixture-c", verify=True)
    try:
        first = a._session.get_adapter("https://192.0.2.1")
        second = b._session.get_adapter("https://192.0.2.2")
        assert isinstance(first, _UnverifiedAdapter)
        context = first.poolmanager.connection_pool_kw["ssl_context"]
        assert context is second.poolmanager.connection_pool_kw["ssl_context"]
        assert context.verify_mode == ssl.CERT_NONE
        assert not isinstance(verified._session.get_adapter("https://192.0.2.3"), _UnverifiedAdapter)
        assert verified._session.verify is True
        assert a._session is not b._session
        assert a._session.auth == ("user-a", "fixture-a")
        assert b._session.auth == ("user-b", "fixture-b")
    finally:
        for client in (a, b, verified):
            client.close()
