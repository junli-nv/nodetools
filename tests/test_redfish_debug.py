import json
import shlex
import subprocess
import pytest
from requests import Response
from nodetools_runtime.transport.redfish import Redfish


@pytest.mark.parametrize("label", ["compute-01", "nvlsw-01", "powershelf-01"])
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH"])
def test_curl_reproduces_request(label, method, monkeypatch, capsys):
    password = "p' $(printf unsafe);$HOME&\""
    client = Redfish("example.test", "root", password)
    client.debug_label = label
    response = Response()
    response.status_code = 200
    response._content = b'{"Members":[]}'
    for verb in ("get", "post", "patch"):
        monkeypatch.setattr(client._session, verb, lambda *a, **k: response)
    path = "/redfish/v1/Systems?$expand=.&$top=2"
    payload = {"Value": "quote' $HOME;$(printf unsafe)"}
    client._etags[client.url(path)] = '"revision-1"'
    try:
        if method == "GET":
            client.get(path)
        else:
            getattr(client, method.lower())(path, payload)
        captured = capsys.readouterr()
        assert captured.out == ""
        lines = captured.err.splitlines()
        assert len(lines) == 2
        command = lines[0].split("] ", 1)[1]
        argv = shlex.split(command)
        assert argv[:3] == ["curl", "-k", "-s"]
        assert argv[argv.index("--user") + 1] == "root:" + password
        assert argv[-1] == client.url(path)
        assert "--globoff" in argv
        replay = subprocess.run(
            ["/bin/bash", "-c", 'curl() { printf "%s\\0" "$@"; }\n' + command],
            check=True, capture_output=True, text=True,
        )
        assert replay.stdout.split("\0")[:-1] == argv[1:]
        assert "HTTP 200" in lines[1]
        if method == "GET":
            assert "-L" in argv and "--data-raw" not in argv
        else:
            assert argv[argv.index("-X") + 1] == method
            assert json.loads(argv[argv.index("--data-raw") + 1]) == payload
            assert "Content-Type: application/json" in argv
        if method == "PATCH":
            assert 'If-Match: "revision-1"' in argv
    finally:
        client.close()


@pytest.mark.parametrize("nested", [False, True])
def test_password_change_required(nested, monkeypatch, capsys):
    detail = {"@Message.ExtendedInfo": [{
        "MessageId": "Base.1.18.1.PasswordChangeRequired",
        "Message": "Change this account password before access is granted.",
        "Resolution": "Change the password."
    }]}
    response = Response()
    response.status_code = 403
    response._content = json.dumps({"error": detail} if nested else detail).encode()
    client = Redfish("example.test", "root", "example-password")
    monkeypatch.setattr(client._session, "get", lambda *a, **k: response)
    try:
        assert client.get_raw("/redfish/v1/Systems") == (403, None)
        assert capsys.readouterr().err == ""
        client.debug_label = "nvlsw-01"
        client.get_raw("/redfish/v1/Systems")
        output = capsys.readouterr().err
        assert "curl -k -s --user" in output
        assert "HTTP 403" in output
        assert "Base.1.18.1.PasswordChangeRequired" in output
        assert "Change this account password" in output
    finally:
        client.close()
