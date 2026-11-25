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
