import json
import os
from types import SimpleNamespace

import pytest

from nodetools_runtime import batch_audit, read_audit, runtime
from nodetools_runtime.inventory import Host, Inventory
from nodetools_runtime.transport import ssh


def case(role, name):
    return next(c for c in read_audit.cases_for(role) if c["id"] == name)


@pytest.mark.parametrize("override", [False, True])
def test_read_audit_keeps_bmc_credentials_out_of_nvos(monkeypatch, capsys, override):
    host = Host("nvlsw-01", "192.0.2.1", dict(
        platform="nvl", oem="nvidia", generation="gb300", psu_backend="nvos",
        nvswitch_os_host="192.0.2.2", nvswitch_os_user="os-admin",
        nvswitch_os_password="os-private"), ["nvswitch"])
    calls = []
    def fake_run(argv, **kwargs):
        secret = os.read(kwargs["pass_fds"][0], 4096).decode().strip()
        assert secret == ("explicit-private" if override else "os-private")
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="50000 3000 150000000 0 0 0 0 0 0 0", stderr="")
    monkeypatch.setattr(ssh.subprocess, "run", fake_run)
    result = read_audit.run_case(
        host, case("nvswitch", "nvswpsu"), user="bmc-only", password="bmc-private",
        debug=True, os_user="explicit-os" if override else None,
        os_password="explicit-private" if override else None)
    assert result["state"] == "read_ok", result
    assert result["requests"][0]["transport"] == "nvos_ssh"
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[argv.index("-l") + 1] == ("explicit-os" if override else "os-admin")
    assert "192.0.2.2" in argv
    assert "bmc-only" not in repr(calls) and "bmc-private" not in repr(calls)
    output = capsys.readouterr()
    assert not output.out
    assert "SSH exit=0" in output.err and "192.0.2.2" in output.err
    for secret in ("os-private", "explicit-private", "bmc-private"):
        assert secret not in output.err


@pytest.mark.parametrize("explicit", [False, True])
def test_read_audit_uses_production_defaults(monkeypatch, explicit):
    inv = Inventory()
    inv.defaults.update(timeout="12", node_timeout="45", redfish_timeout="9", jobs="7")
    host = Host("shelf01", "192.0.2.3", groups=["powershelf"])
    seen = []
    def invoke(host, spec, opts):
        seen.append(opts)
        return dict(lines=["fixture"], error=None)
    monkeypatch.setattr(read_audit, "_invoke", invoke)
    result = read_audit.run_case(host, case("powershelf", "pspsu"), inventory=inv,
                                timeout=2 if explicit else None,
                                node_timeout=7 if explicit else None, debug=True)
    assert result["state"] == "read_ok"
    opts = seen[0]
    assert opts.timeout == (2 if explicit else 12)
    assert opts.node_timeout == (7 if explicit else 45)
    assert opts.timeout_explicit is explicit
    assert float(opts.redfish_timeout) == 9 and opts.jobs == 7 and opts.debug


def test_management_construction_failure_is_an_audit_result(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("fixture unavailable")
    monkeypatch.setattr(runtime, "build_device", fail)
    host = Host("shelf01", groups=["powershelf"])
    result = read_audit.run_case(host, case("powershelf", "nodebmc/info"))
    assert result["state"] == "failed"
    assert "fixture unavailable" in result["error"]


@pytest.mark.parametrize("explicit", [False, True])
def test_batch_audit_defaults_debug_and_jsonl(monkeypatch, tmp_path, capsys, explicit):
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[nodehealth:defaults:vars]\njobs=7\ntimeout=12\nnode_timeout=45\nredfish_timeout=9\n[powershelf]\nshelf01 host=192.0.2.3\n")
    target = tmp_path / "audit.jsonl"
    def fake_run(hosts, cap, opts, expected, emit):
        assert len(hosts) == 1 and cap == "psu" and expected == 6
        assert opts.debug and opts.jobs == (3 if explicit else 7)
        assert opts.timeout == (2 if explicit else 12)
        assert opts.node_timeout == (5 if explicit else 45)
        assert float(opts.redfish_timeout) == 9
        assert opts.timeout_explicit is explicit
        emit(dict(name="shelf01", acceptance="passed", error=None, requests=[], seconds=.01))
        return dict(completed=1, outcomes={"passed": 1})
    monkeypatch.setattr(batch_audit, "run", fake_run)
    argv = ["pspsu", "shelf01", "-i", str(inventory), "-x",
            "--expected-readings", "6", "--output", str(target)]
    if explicit:
        argv += ["--jobs", "3", "--timeout", "2", "--node-timeout", "5"]
    assert batch_audit.main(argv) == 0
    output = capsys.readouterr()
    assert "[batch_audit]" in output.err
    assert "completed 1/1" in output.out
    records = [json.loads(line) for line in target.read_text().splitlines()]
    assert [record["type"] for record in records] == ["selection", "node", "summary"]
    assert target.stat().st_mode & 0o777 == 0o600


def test_read_audit_cli_separates_overrides_and_keeps_json(monkeypatch, tmp_path, capsys):
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[nvswitch]\nnvlsw-01 host=192.0.2.1\n")
    monkeypatch.setenv("BMC_TEST_SECRET", "bmc-fixture")
    monkeypatch.setenv("OS_TEST_SECRET", "os-fixture")
    def fake_case(host, spec, user, password, **kwargs):
        assert user == "bmc-user" and password == "bmc-fixture"
        assert kwargs["os_user"] == "os-user" and kwargs["os_password"] == "os-fixture"
        assert kwargs["debug"] and kwargs["timeout"] == 11 and kwargs["node_timeout"] == 40
        assert kwargs["inventory"].get(host.name) is host
        return dict(case=spec["id"], state="read_ok")
    monkeypatch.setattr(read_audit, "run_case", fake_case)
    assert read_audit.main(["nvlsw-01", "-i", str(inventory), "--only", "nvswpsu", "-x",
        "--user", "bmc-user", "--password-env", "BMC_TEST_SECRET",
        "--os-user", "os-user", "--os-password-env", "OS_TEST_SECRET",
        "--timeout", "11", "--node-timeout", "40"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "read_ok"


@pytest.mark.parametrize("debug", [False, True])
def test_read_audit_redfish_debug_is_on_stderr(monkeypatch, capsys, debug):
    import requests
    host = Host("shelf01", "192.0.2.3", dict(
        platform="nvl", oem="liteon", generation="gb300", chassis_id="powershelf"),
        ["powershelf"])
    calls = []
    def get(session, url, **kwargs):
        calls.append(url)
        body = {"PowerSupplies": [
            {"MemberId": "powerdevice" + str(i), "Name": "Power Supply",
             "PowerInputWatts": 900, "Status": {"Health": "OK"}}
            for i in range(6)]}
        return SimpleNamespace(status_code=200, ok=True, headers={}, text="", json=lambda: body)
    monkeypatch.setattr(requests.Session, "get", get)
    result = read_audit.run_case(host, case("powershelf", "pspsu"), debug=debug)
    assert result["state"] == "read_ok" and result["rows"] == 6
    assert calls == ["https://192.0.2.3/redfish/v1/Chassis/powershelf/Power"]
    output = capsys.readouterr()
    assert not output.out
    if debug:
        assert "curl -k -s" in output.err and "HTTP 200" in output.err
    else:
        assert not output.err


def test_batch_nvos_debug_preserves_transport_metrics_and_hides_os_password(monkeypatch, capsys):
    from nodetools_runtime import cli
    from nodetools_runtime.arguments import parse_args
    host = Host("nvlsw-01", "192.0.2.1", dict(
        platform="nvl", oem="nvidia", generation="gb300", psu_backend="nvos",
        nvswitch_os_user="os-admin", nvswitch_os_password="os-private"), ["nvswitch"])
    def fake_run(argv, **kwargs):
        assert os.read(kwargs["pass_fds"][0], 4096) == b"os-private\n"
        return SimpleNamespace(returncode=0, stdout="50000 3000 150000000 0 0 0 0 0 0 0", stderr="")
    monkeypatch.setattr(ssh.subprocess, "run", fake_run)
    args = parse_args(["nvswpsu", host.name, "-x"])
    opts = cli.Options(args)
    cli._apply_defaults(opts, Inventory(), args)
    results = []
    summary = batch_audit.run([host], "psu", opts, 1, results.append)
    assert summary["outcomes"] == {"passed": 1} and summary["requests"] == 1
    assert results[0]["closed"] and results[0]["requests"][0]["transport"] == "nvos_ssh"
    output = capsys.readouterr()
    assert not output.out
    assert "SSH exit=0" in output.err and "os-private" not in output.err


@pytest.mark.parametrize("flag", ["--timeout", "--node-timeout"])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_read_audit_rejects_invalid_budgets_before_inventory(flag, value, capsys):
    with pytest.raises(SystemExit) as result:
        read_audit.main(["fixture", flag, value, "-i", "/no/such/inventory"])
    assert result.value.code == 2
    assert "must be finite and positive" in capsys.readouterr().err
