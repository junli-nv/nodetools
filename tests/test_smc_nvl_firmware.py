import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nodetools_runtime import firmware_cli, redfish_snapshot, runtime

ROOT = Path(__file__).resolve().parents[1]
BASE = "/redfish/v1/UpdateService/FirmwareInventory"


def archive(platform="nvl", generation="gb300", oem="smc", model="gb-nvl", version="70.02.02.01"):
    directory = ROOT / "docs/redfish-archives" / platform / generation
    manifest = json.loads((directory / "manifests" / oem / "compute" / (model + ".json")).read_text())
    sample = next(s for s in manifest["samples"] if s["bmc_version"] == version)
    return redfish_snapshot.load(directory / sample["file"])


def invoke(snapshot, tmp_path, monkeypatch, capsys, argv=None):
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[compute]\nfixture host=192.0.2.1\n[compute:vars]\n" +
                         "".join(f"{k}={v}\n" for k, v in snapshot["device"].items()
                                 if k not in ("system_id", "manager_id", "chassis_id")))
    from urllib.parse import urlsplit
    from nodetools_runtime.transport.redfish import Redfish
    transport = Redfish("192.0.2.1", "fixture-user", "fixture-password")
    transport.requests = []
    def get(url, **kwargs):
        parsed = urlsplit(url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        record = snapshot["resources"].get(path, {"status": 404, "body": {"error": {}}})
        status = record["status"] or 503
        transport.requests.append((path, status))
        from requests import Response
        response = Response()
        response.status_code = status
        response._content = json.dumps(record["body"]).encode()
        return response
    transport._session.get = get
    monkeypatch.setattr(runtime, "Redfish", lambda *a, **k: transport)
    monkeypatch.setattr(runtime, "Ipmi",
                        lambda *a, **k: SimpleNamespace(set_deadline=lambda d: None))
    argv = ["fixture", "show"] if argv is None else argv
    code = firmware_cli.main([*argv, "-i", str(inventory)], "nodefw")
    return code, capsys.readouterr(), transport


@pytest.mark.parametrize("argv", [["fixture"], ["fixture", "show"], ["show", "fixture"]])
def test_gb300_cli_reads_all_components_without_numeric_pagination(argv, tmp_path, monkeypatch, capsys):
    snapshot = archive()
    members = snapshot["resources"][BASE + "?$expand=."]["body"]["Members"]
    assert len(members) == 31
    snapshot["resources"][BASE + "?$expand=.&$top=16"] = {
        "status": 200, "body": {"Members@odata.count": 27, "Members": members[:16]}}
    snapshot["resources"][BASE + "?$expand=.&$top=16&$skip=16"] = {
        "status": 200, "body": {"Members@odata.count": 15, "Members": []}}
    code, output, transport = invoke(snapshot, tmp_path, monkeypatch, capsys, argv)
    assert code == 0, output.err
    assert len(output.out.splitlines()) == 33
    contract = next(r for r in json.loads((ROOT / "docs/certified-reads.json").read_text())["records"]
                    if r["id"] == "smc_gb300_firmware")
    assert contract["expected_rows"] == 33 and contract["request_count"] == 3
    assert transport.requests == [(path, 200) for path in contract["direct_requests"]]
    assert transport.requests == [
        ("/redfish/v1/Systems/System_0", 200),
        ("/redfish/v1/Managers/BMC_0", 200),
        (BASE + "?$expand=.", 200),
    ]


@pytest.mark.parametrize("action,tail", [
    ("show", []), ("status", []), ("check", ["bmc", "package.zip"]),
    ("update", ["bmc", "package.zip"]),
])
@pytest.mark.parametrize("flag", ["-x", "--debug"])
@pytest.mark.parametrize("position", ["before", "after"])
@pytest.mark.parametrize("legacy", [False, True])
def test_debug_and_node_order_for_every_action(action, tail, flag, position, legacy):
    argv = [action, "fixture", *tail] if legacy else ["fixture", action, *tail]
    argv = [flag, *argv] if position == "before" else [*argv, flag]
    args = firmware_cli.parser("nodefw").parse_args(argv)
    assert (args.nodes, args.action, args.debug) == ("fixture", action, True)


@pytest.mark.parametrize("debug", [False, True])
def test_cli_debug_emits_curl_and_status(debug, tmp_path, monkeypatch, capsys):
    argv = ["fixture", "show"] + (["-x"] if debug else [])
    code, output, _ = invoke(archive(), tmp_path, monkeypatch, capsys, argv)
    assert code == 0
    if debug:
        assert "curl -k -s --user" in output.err
        assert BASE + "?$expand=." in output.err
        assert "HTTP 200" in output.err
        assert "$top=" not in output.err
    else:
        assert output.err == ""


@pytest.mark.parametrize("fault", ["truncated", "duplicate", "invalid_count"])
def test_cli_rejects_incomplete_firmware(fault, tmp_path, monkeypatch, capsys):
    snapshot = archive()
    body = snapshot["resources"][BASE + "?$expand=."]["body"]
    if fault == "truncated":
        body["Members"].pop()
    elif fault == "duplicate":
        body["Members"][-1] = body["Members"][0]
    else:
        body["Members@odata.count"] = True
    code, output, _ = invoke(snapshot, tmp_path, monkeypatch, capsys)
    assert code == 1
    assert output.out == ""
    assert "collection" in output.err


def test_expand_failure_uses_scoped_fallback(tmp_path, monkeypatch, capsys):
    snapshot = archive()
    members = snapshot["resources"][BASE + "?$expand=."]["body"]["Members"]
    snapshot["resources"][BASE + "?$expand=."] = {"status": 404, "body": {"error": {}}}
    snapshot["resources"][BASE] = {"status": 200, "body": {
        "Members@odata.count": 31, "Members": [{"@odata.id": m["@odata.id"]} for m in members]}}
    for m in members:
        snapshot["resources"][m["@odata.id"]] = {"status": 200, "body": m}
    code, output, transport = invoke(snapshot, tmp_path, monkeypatch, capsys)
    assert code == 0, output.err
    assert len(output.out.splitlines()) == 33
    assert transport.requests[2:] == [(BASE + "?$expand=.", 404), (BASE, 200)] + [
        (m["@odata.id"], 200) for m in members]


def test_hgx_smc_keeps_numeric_pagination(tmp_path, monkeypatch, capsys):
    snapshot = archive("hgx", "b300", "supermicro", "sys-822gs-nb3rt", "01.07.04.00")
    snapshot["device"]["system_model"] = "SYS-822GS-NB3RT"
    members = [{"@odata.id": BASE + "/FW" + str(i), "Name": "Component" + str(i),
                "Version": "1.0"} for i in range(80)]
    expected = []
    for offset in range(0, 80, 16):
        path = BASE + "?$expand=.&$top=16" + ("&$skip=" + str(offset) if offset else "")
        snapshot["resources"][path] = {"status": 200, "body": {
            "Members@odata.count": 80, "Members": members[offset:offset+16]}}
        expected.append((path, 200))
    code, output, transport = invoke(snapshot, tmp_path, monkeypatch, capsys)
    assert code == 0, output.err
    assert sum(line.startswith("fixture: Component") for line in output.out.splitlines()) == 80
    assert [(p, s) for p, s in transport.requests if p.startswith(BASE)] == expected
