import json
from types import SimpleNamespace as NS
import pytest
from nodetools_runtime import bmc_user_cli as tool, cli, os_cli
from nodetools_runtime.subjects import bmc_user_agent as agent


class Ipmi:
    def __init__(self, channel=1):
        self.channel = channel
        self.names = {1: "root", 2: "nvis", 8: ""}
        self.rights = {1: True, 2: True, 8: False}
        self.enabled = dict(self.rights)
        self.passwords = {}
        self.calls = []

    def __call__(self, argv, **kwargs):
        args = argv[3:]
        self.calls.append(args)
        text, rc = "", 0
        if args[:2] == ["channel", "info"]:
            text = "Channel Medium Type : 802.3 LAN"
            rc = 0 if int(args[2]) == self.channel else 1
        elif args[:2] == ["user", "list"]:
            for ident, name in self.names.items():
                state = "true true true ADMINISTRATOR" if self.rights[ident] else "false false false NO ACCESS"
                text += f"{ident} {name:16} {state}\n"
        elif args[:3] == ["user", "set", "name"]:
            self.names[int(args[3])] = args[4]
        elif args[:3] == ["user", "set", "password"]:
            self.passwords[int(args[3])] = args[4]
        elif args[:2] in (["user", "enable"], ["user", "disable"]):
            self.enabled[int(args[2])] = args[1] == "enable"
        elif args[:2] == ["channel", "setaccess"]:
            self.rights[int(args[3])] = "privilege=4" in args
        elif args[:2] == ["sol", "info"]:
            text = "Enabled : true\n"
        elif args[:3] == ["sol", "payload", "status"]:
            text = "User is enabled\n"
        return NS(returncode=rc, stdout=text, stderr="")


def request(operation, name="nvis", **updates):
    data = dict(role="nvswitch", operation=operation, name=name,
                password="Example'Pass1!", id=None, channel=None,
                apply=operation not in ("list", "verify"), sol=False, debug=True)
    return dict(data, **updates)


def test_crud_and_reused_slots():
    fake = Ipmi()
    result = agent.provision(request("upsert", "tester"), fake)
    assert result["ok"] and result["slot"] == 8
    assert fake.enabled[8] and fake.rights[8]
    assert "Example'Pass1!" not in json.dumps(result)
    before = len(fake.calls)
    result = agent.provision(request("modify", "tester", password="NewExample2!"), fake)
    assert result["ok"] and fake.passwords[8] == "NewExample2!"
    writes = [a for a in fake.calls[before:] if a[:2] not in (["channel", "info"], ["user", "list"])]
    assert writes == [["user", "set", "password", "8", "NewExample2!", "16"]]
    result = agent.provision(request("list", "tester"), fake)
    assert [row["name"] for row in result["users"]] == ["tester"]
    result = agent.provision(request("delete", "tester"), fake)
    assert result["ok"] and not fake.names[8] and not fake.enabled[8] and not fake.rights[8]
    assert fake.names[1] == "root"


def test_auto_channel_and_same_name_slot():
    fake = Ipmi(channel=2)
    result = agent.provision(request("upsert"), fake)
    assert result["ok"] and result["slot"] == 2
    assert ["user", "list", "2"] in fake.calls


@pytest.mark.parametrize("operation", ["delete", "modify"])
def test_root_protected(operation):
    fake = Ipmi()
    with pytest.raises(ValueError):
        agent.provision(request(operation, "root"), fake)
    assert not fake.calls


def test_modify_missing_user_never_creates():
    fake = Ipmi()
    assert not agent.provision(request("modify", "missing"), fake)["ok"]
    assert not fake.passwords


@pytest.mark.parametrize("verb,extra", [
    ("adduser", ["newuser", "ExamplePass1!"]),
    ("moduser", ["nvis", "NewExample2!"]),
    ("deluser", ["nvis"]),
    ("listuser", []),
    ("checkuser", ["nvis", "ExamplePass1!"]),
])
def test_short_cli_and_debug(tmp_path, monkeypatch, capsys, verb, extra):
    inv = tmp_path / "inventory.ini"
    inv.write_text("[nvswitch]\nsw host=192.0.2.1\n")
    fake = Ipmi()
    def ssh(host, options, command, deadline, **kwargs):
        for secret in extra[1:]:
            assert secret not in command
        data = json.loads(kwargs["input_text"])
        assert data["channel"] is None and not data["sol"]
        result = agent.provision(data, fake)
        return NS(returncode=0 if result["ok"] else 1, stdout=json.dumps(result))
    monkeypatch.setattr(tool.ssh, "run", ssh)
    verified = []
    monkeypatch.setattr(tool, "verify", lambda *args: verified.append(args) or "HTTP 200")
    argv = ["nodeos", "-x", "sw", "bmc", verb, *extra, "-i", str(inv)]
    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    assert "sw:" in captured.out and not captured.out.startswith("{")
    assert "SSH target=sw" in captured.err and "ipmitool -I open" in captured.err
    for secret in extra[1:]:
        assert secret not in captured.out + captured.err
    assert bool(verified) == (verb in ("adduser", "moduser", "checkuser"))


def test_nodebmc_does_not_run_os_user_operations(monkeypatch, capsys):
    monkeypatch.setattr(tool.ssh, "run", lambda *a, **k: pytest.fail("unexpected SSH"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["nodebmc", "sw", "listuser"])
    assert exc.value.code == 2
    assert "nodeos" in capsys.readouterr().err


def test_nodeos_can_dispatch_another_os_resource(monkeypatch):
    seen = []
    def configure(parser):
        parser.add_argument("action")
    handler = NS(configure_parser=configure, simple_action=lambda *a: None,
                 execute=lambda args, parser: seen.append((args.nodes, args.debug, args.action)) or 0)
    monkeypatch.setitem(os_cli.RESOURCES, "example", handler)
    assert cli.main(["nodeos", "-x", "host", "example", "status"]) == 0
    assert seen == [("host", True, "status")]


@pytest.mark.parametrize("fixed", [False, True])
def test_verification_direct_or_scoped_discovery(monkeypatch, fixed):
    calls, closed = [], []
    path = "/redfish/v1/Systems/System_0"
    class RF:
        read_error = None
        def __init__(self, *a, **k):
            pass
        def set_deadline(self, value):
            pass
        def get_raw(self, target):
            calls.append(target)
            return (200, {"Members": [{"@odata.id": path}]}) if target.endswith("/Systems") else (200, {"Id": "System_0"})
        def close(self):
            closed.append(True)
    monkeypatch.setattr(tool, "Redfish", RF)
    assert tool.verify(NS(name="sw", address="192.0.2.1"), "test", "test", path if fixed else None, 10).startswith("HTTP 200")
    assert calls == ([path] if fixed else ["/redfish/v1/Systems", path])
    assert closed == [True]
