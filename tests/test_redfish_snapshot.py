import json
from threading import Barrier

import pytest

from nodetools_runtime import cli, redfish_snapshot as snapshots, runtime
from nodetools_runtime.errors import CapUnsupported, NodetoolsError

ROOT = snapshots.ROOT
SYSTEM = ROOT + "/Systems/system"
BIOS = SYSTEM + "/Bios"
SEEDS = {ROOT, ROOT + "/Systems", ROOT + "/Chassis", ROOT + "/Managers"}


def test_parallel_capture_and_offline_replay(tmp_path, monkeypatch, capsys):
    ready = Barrier(4)
    calls = []
    clients = []
    replies = {
        ROOT: (200, {"RedfishVersion": "1.2.3.4",
                     "External": {"@odata.id": "https://other.example/redfish/v1/Systems/extra"}}),
        ROOT + "/Systems": (200, {"Members": [{"@odata.id": "https://192.0.2.20" + SYSTEM}]}),
        SYSTEM: (200, {"Model": "GB300 NVL72", "Manufacturer": "Wistron",
                       "SerialNumber": "unit-private", "Bios": {"@odata.id": BIOS},
                       "Actions": {"Reset": {"target": SYSTEM + "/Actions/Reset"}}}),
        BIOS: (200, {"Attributes": {"Language": "English", "HostName": "private-host"},
                     "Message": "must redact sample-password"}),
        SYSTEM + "/BootOptions": (None, None),
    }

    class BMC:
        def __init__(self):
            clients.append(self)
            self.closed = False

        def set_deadline(self, deadline):
            pass

        def get_raw(self, path, *, follow_redirects):
            assert not follow_redirects
            calls.append(path)
            if path in SEEDS:
                ready.wait(timeout=3)
            return replies.get(path, (404, {"error": {"code": "not_found"}}))

        def close(self):
            self.closed = True

    snapshot = snapshots.capture(
        "192.0.2.20", BMC, {"role": "compute", "platform": "nvl", "oem": "wistron"},
        ("private-host", "192.0.2.20", "sample-password"), workers=4, seconds=8, limit=80,
    )
    assert len(clients) == 4 and all(client.closed for client in clients)
    assert len(calls) == len(set(calls))
    assert SYSTEM + "/Actions/Reset" not in calls
    encoded = json.dumps(snapshot)
    for value in ("unit-private", "private-host", "192.0.2.20", "sample-password", "other.example"):
        assert value not in encoded
    assert "1.2.3.4" in encoded
    assert snapshot["capture"]["transport_errors"] == 1

    path = tmp_path / "capture.json"
    snapshots.save(path, snapshot)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(NodetoolsError, match="cannot save"):
        snapshots.save(path, snapshot)

    def no_network(*args, **kwargs):
        raise AssertionError("replay contacted a live device")

    monkeypatch.setattr(runtime, "build_device", no_network)
    assert cli.main(["redfish", "replay", str(path), "nodebios"]) == 0
    assert "snapshot:Language=English" in capsys.readouterr().out
    assert cli.main(["redfish", "replay", str(path), "nodeboot"]) == 1

    replay = snapshots.Replay(snapshots.load(path))
    assert replay.get_raw(ROOT + "/Chassis")[0] == 404
    assert replay.get_raw(SYSTEM + "/BootOptions")[0] is None
    assert replay.get_raw(ROOT + "/Chassis/missing")[0] is None
    assert replay.failed == {SYSTEM + "/BootOptions"}
    assert replay.missing == {ROOT + "/Chassis/missing"}
    with pytest.raises(CapUnsupported, match="read-only"):
        replay.patch(BIOS, {})


def test_request_limit_records_pending_work():
    class BMC:
        def set_deadline(self, deadline):
            pass

        def get_raw(self, path, *, follow_redirects):
            return 200, {"Systems": {"@odata.id": ROOT + "/Systems"}}

        def close(self):
            pass

    result = snapshots.capture("bmc.example", BMC, {}, (), workers=4, seconds=2, limit=1)
    assert list(result["resources"]) == [ROOT]
    assert result["capture"]["pending"] > 0


def test_capture_reports_progress_while_request_is_in_flight(monkeypatch):
    from threading import Event

    release = Event()
    updates = []

    class BMC:
        def set_deadline(self, deadline):
            pass

        def get_raw(self, path, *, follow_redirects):
            release.wait(0.5)
            return 200, {}

        def close(self):
            pass

    def report(elapsed, completed, active, pending):
        updates.append((completed, active, pending))
        if len(updates) == 2:
            release.set()

    monkeypatch.setattr(snapshots, "_PROGRESS_INTERVAL", 0.01)
    result = snapshots.capture("bmc.example", BMC, {}, (), workers=1, seconds=2,
                               limit=1, progress=report)
    assert len(updates) >= 2
    assert updates[0][:2] == updates[1][:2] == (0, 1)
    assert result["capture"]["stop_reason"] == "request limit"


def test_snapshot_keeps_psu_serial_relationships_without_serial_values():
    from nodetools_runtime.redfish_snapshot import Redactor

    scrub = Redactor(("https", "192.0.2.10"), ())
    a = scrub.clean({"SerialNumber": "private-unit-alpha"})["SerialNumber"]
    b = scrub.clean({"SerialNumber": "private-unit-beta"})["SerialNumber"]
    assert a != b
    assert a == scrub.clean({"SerialNumber": "private-unit-alpha"})["SerialNumber"]
    assert scrub.clean({"SerialNumber": "unknown"})["SerialNumber"] == "unknown"
    assert scrub.clean({"SerialNumber": None})["SerialNumber"] is None
    assert "private-unit" not in a + b
    assert scrub.clean({"Password": "keep-private"})["Password"] == "<redacted>"


@pytest.mark.parametrize(
    "variant,error",
    [("complete", None), ("collapsed", "expected 2 readings"),
     ("missing", "no captured response"), ("http-error", "GET /redfish/v1/Chassis/1/Power: 500"),
     ("wrong-path", "expected GETs")],
)
def test_strict_psu_snapshot_verifies_production_reads(tmp_path, monkeypatch, capsys, variant, error):
    import gzip

    def no_network(*args, **kwargs):
        raise AssertionError("strict replay must replace both live transports")

    monkeypatch.setattr(runtime, "Redfish", no_network)
    monkeypatch.setattr(runtime, "Ipmi", no_network)
    power = ROOT + "/Chassis/1/Power"
    scrub = snapshots.Redactor(("https", "192.0.2.10"), ())
    body = scrub.clean({"PowerSupplies": [
        {"MemberId": str(i), "Name": f"PSU {i}", "SerialNumber": f"supply-{i}",
         "Manufacturer": "test-supply", "PowerInputWatts": 200 * i, "Status": {"Health": "OK"}}
        for i in range(2)
    ]})
    if variant == "collapsed":
        for entry in body["PowerSupplies"]:
            entry["SerialNumber"] = "<redacted>"
    resources = {} if variant == "missing" else {
        power: {"status": 500 if variant == "http-error" else 200, "body": body},
    }
    archive = tmp_path / "capture.json.gz"
    with gzip.open(archive, "wt", encoding="utf-8") as stream:
        json.dump({"schema": 1, "device": {"platform": "hgx", "oem": "aivres",
                                         "generation": "b300", "role": "compute"},
                   "resources": resources}, stream)
    expected = power + "?$expand=." if variant == "wrong-path" else power
    rc = cli.main(["redfish", "replay", str(archive), "nodepsu", "--details",
                   "--expect-rows", "2", "--expect-path", expected])
    output = capsys.readouterr()
    if error:
        assert rc == 1 and error in output.err
    else:
        assert rc == 0
        assert "snapshot verified: 2 readings, 1 GETs" in output.err
        assert sum(line.startswith("snapshot:") for line in output.out.splitlines()) == 2


def test_mac_redaction_keeps_invalid_placeholders_invalid():
    from nodetools_runtime.subjects.boot_nics import mac

    scrub = snapshots.Redactor(("https", "192.0.2.10"), ())
    values = ["00:00:00:00:00:00", "FFFFFFFFFFFF", "0011AA22BB33", "00-11-AA-22-BB-33"]
    actual = scrub.clean({"AssociatedNetworkAddresses": values})["AssociatedNetworkAddresses"]
    assert [mac(x) for x in actual[:2]] == [None, None]
    assert mac(actual[2]) == mac(actual[3])
    assert mac(actual[2]) != mac(values[2])
