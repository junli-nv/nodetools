import json
from types import SimpleNamespace as NS
import pytest
from nodetools_runtime.subjects import bmc_user_agent as agent


def payload(**changes):
    data = dict(role="compute", name="nvis", id=8, channel=1, apply=True,
                password="Test'Quote;$X!", sol=True)
    return dict(data, **changes)


def fake_ipmi(existing="", fail=False):
    calls = []
    def call(argv, **kwargs):
        args = argv[3:]
        calls.append(args)
        assert argv[:3] == ["ipmitool", "-I", "open"]
        if fail and args[:3] == ["user", "set", "password"]:
            return NS(returncode=1, stdout="", stderr="sensitive")
        if args[:2] == ["sol", "info"]:
            return NS(returncode=0, stdout="Enabled : true\n", stderr="")
        if args[:3] == ["sol", "payload", "status"]:
            return NS(returncode=0, stdout="User 8 on channel 1 is enabled\n", stderr="")
        name = "nvis" if len(calls) > 1 else existing
        table = f"1 root false true true ADMINISTRATOR\n8 {name:16} true true true ADMINISTRATOR\n"
        return NS(returncode=0, stdout=table, stderr="")
    return call, calls


@pytest.mark.parametrize("role,sol", [("compute", True), ("nvswitch", False)])
def test_exact_apply_and_sol(role, sol):
    call, calls = fake_ipmi()
    data = payload(role=role, sol=sol)
    result = agent.provision(data, call)
    assert result["ok"]
    assert calls[2] == ["user", "set", "password", "8", data["password"], "16"]
    assert calls[4] == ["channel", "setaccess", "1", "8", "callin=on", "ipmi=on", "link=on", "privilege=4"]
    assert (["sol", "payload", "enable", "1", "8"] in calls) == sol
    assert calls[-1] == ["user", "list", "1"]
    assert data["password"] not in json.dumps(result)


def test_preview_and_auto_slot():
    call, calls = fake_ipmi("nvis")
    result = agent.provision(payload(id=None, apply=False), call)
    assert result["ok"] and result["slot"] == 8
    assert calls == [["user", "list", "1"]]


def test_conflict_no_writes():
    call, calls = fake_ipmi("another")
    assert not agent.provision(payload(), call)["ok"]
    assert len(calls) == 1


def test_partial_failure():
    call, calls = fake_ipmi(fail=True)
    result = agent.provision(payload(), call)
    assert not result["ok"] and result["completed"] == ["set name"]
    assert len(calls) == 3
    assert "sensitive" not in json.dumps(result)


@pytest.mark.parametrize("change", [
    dict(name="root"), dict(id=1), dict(name="bad;cmd"), dict(password="x"*21),
    dict(password="line\nbreak"), dict(role="nvswitch", sol=True),
])
def test_invalid_never_executes(change):
    call, calls = fake_ipmi()
    with pytest.raises(ValueError):
        agent.provision(payload(**change), call)
    assert not calls


@pytest.mark.parametrize("apply", [False, True])
def test_mixed_cli_uses_same_agent_and_compute_only_sol(tmp_path, monkeypatch, capsys, apply):
    from nodetools_runtime import bmc_user_cli as tool, cli
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[compute]\ncp host=192.0.2.1 system_id=System_0\n"
                         "[nvswitch]\nsw host=192.0.2.2 system_id=System_0\n")
    requests, verifications = [], []
    password = "New'Account;$Pass"
    monkeypatch.setenv("TEST_ACCOUNT_PASSWORD", password)

    def ssh(host, options, command, deadline, **kwargs):
        assert password not in command and not options.debug
        data = json.loads(kwargs["input_text"])
        assert data["password"] == password
        assert data["sol"] == (host.role == "compute")
        call, calls = fake_ipmi("nvis")
        result = agent.provision(data, call)
        requests.append((host.name, calls))
        return NS(returncode=0, stdout=json.dumps(result))

    monkeypatch.setattr(tool.ssh, "run", ssh)
    monkeypatch.setattr(tool, "verify", lambda host, *args: verifications.append(host.name) or "HTTP 200")
    argv = ["nodebmc", "cp,sw", "user", "-i", str(inventory), "--name", "nvis",
            "--sol", "--password-env", "TEST_ACCOUNT_PASSWORD"]
    if apply:
        argv.append("--apply")
    assert cli.main(argv) == 0
    output = capsys.readouterr().out
    assert password not in output and len(output.splitlines()) == 2
    assert len(requests) == 2
    assert len(verifications) == (2 if apply else 0)
    if not apply:
        assert all(calls == [["user", "list", "1"]] for _, calls in requests)


def test_ssh_credentials_and_new_password_are_not_in_argv(monkeypatch):
    from nodetools_runtime.inventory import Host
    from nodetools_runtime.transport import ssh
    import os
    import time
    captured = []
    def run(argv, **kwargs):
        assert "os-secret" not in argv and "new-secret" not in argv
        assert kwargs["input"] == "new-secret" and "stdin" not in kwargs
        assert os.read(int(argv[2]), 100) == b"os-secret\n"
        captured.append(argv)
        return NS(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ssh.subprocess, "run", run)
    host = Host("cp", variables={"os_user": "root", "os_password": "os-secret"})
    options = NS(user=None, password=None, timeout=5, debug=False)
    ssh.run(host, options, "python3 -c pass", time.monotonic()+5,
            role="compute", input_text="new-secret")
    assert len(captured) == 1


@pytest.mark.parametrize("status,body", [
    (403, {}), (200, {"Id": "wrong"}), (200, {"Id": "System_0", "error": {}}),
])
def test_redfish_rejects_wrong_or_inaccessible_resources(monkeypatch, status, body):
    from nodetools_runtime import bmc_user_cli as tool
    closed = []
    class Redfish:
        read_error = None
        def __init__(self, *args, **kwargs):
            pass
        def set_deadline(self, value):
            pass
        def get_raw(self, path):
            assert path == "/redfish/v1/Systems/System_0"
            return status, body
        def close(self):
            closed.append(True)
    monkeypatch.setattr(tool, "Redfish", Redfish)
    with pytest.raises(ValueError):
        tool.verify(NS(address="192.0.2.1"), "nvis", "test", "/redfish/v1/Systems/System_0", 10)
    assert closed == [True]


def test_sixteen_and_twenty_byte_password_modes():
    call, calls = fake_ipmi()
    assert agent.provision(payload(password="x"*20), call)["ok"]
    assert calls[2][-1] == "20"


def test_auto_slot_cannot_select_reserved_named_account():
    def call(*args, **kwargs):
        return NS(returncode=0, stdout="1 nvis true true true ADMINISTRATOR\n", stderr="")
    assert not agent.provision(payload(id=None), call)["ok"]


def test_sol_disabled_readback_is_failure():
    call, calls = fake_ipmi()
    def disabled(argv, **kwargs):
        result = call(argv, **kwargs)
        if argv[3:5] == ["sol", "info"]:
            result.stdout = "Enabled : false\n"
        return result
    result = agent.provision(payload(), disabled)
    assert not result["ok"] and "SOL" in result["error"]


def test_username_at_other_slot_is_not_duplicated():
    def call(*args, **kwargs):
        return NS(returncode=0, stdout="2 nvis true true true ADMINISTRATOR\n8   true false false NO ACCESS\n", stderr="")
    assert not agent.provision(payload(), call)["ok"]
    result = agent.provision(payload(id=None, apply=False), call)
    assert result["ok"] and result["slot"] == 2
