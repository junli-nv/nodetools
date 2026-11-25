import json

from nodetools_runtime import alive
from nodetools_runtime.cli import main


def test_nodealive_cli_keeps_independent_checks(monkeypatch, tmp_path, capsys):
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[compute]\nHGX390 host=192.0.2.10 ssh_host=host-a\n")
    monkeypatch.setattr(alive, "ping", lambda address, seconds: "ok" if address == "host-a" else "no_reply")
    monkeypatch.setattr(alive, "ssh", lambda address, port, seconds: "refused")
    monkeypatch.setattr(alive, "slurm_states", lambda timeout: ("ok", {"hgx390": "DOWN+NOT_RESPONDING"}))
    monkeypatch.setattr(alive.InfoRunner, "_one", lambda self, host: {"lines": [host.name + ": On"]})
    command = ["nodealive", "HGX390", "-i", str(inventory)]
    assert main(command) == 1
    assert "BMC=no_reply Power=on Host=ok SSH=refused" in capsys.readouterr().out
    assert main([*command, "--slurm", "--json", "--errors-only"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == 1 and result["status"] == "degraded"
    assert result["checks"]["power"]["state"] == "on"
    assert result["checks"]["slurm"] == {"status": "ok", "state": "DOWN+NOT_RESPONDING"}


def test_sensor_json_separates_no_readings_and_bmc_timeout(monkeypatch, tmp_path, capsys):
    from nodetools_runtime.runtime import Runner

    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[compute]\nHGX390 host=192.0.2.10\nHGX391 host=192.0.2.11\n")
    def stream(self, hosts):
        yield {"name": hosts[0].name, "cap": "temp", "readings": [{"value": "NA", "health": "OK"}]}
        yield {"name": hosts[1].name, "cap": "temp", "error": "TransportTimeout: Redfish timed out"}
    monkeypatch.setattr(Runner, "stream", stream)
    assert main(["nodetemp", "HGX[390-391]", "--json", "-i", str(inventory)]) == 1
    empty, timed_out = map(json.loads, capsys.readouterr().out.splitlines())
    assert empty["status"] == "no_data" and empty["readings"][0] == {"value": None, "health": "OK", "unit": "C"}
    assert timed_out["status"] == "timeout" and timed_out["readings"] == []
