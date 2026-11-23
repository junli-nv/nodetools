import time
import types

import pytest

from nodetools_runtime.errors import TransportError
from nodetools_runtime.transport.ipmi import Ipmi
from nodetools_runtime.transport.redfish import Redfish


def test_redfish_expired_skips_request(monkeypatch):
    client = Redfish("10.0.0.1", "u", "p", timeout=10)
    client.set_deadline(time.monotonic() - 1)

    def boom(*a, **k):
        raise AssertionError("request must not be issued past the deadline")

    monkeypatch.setattr(client._session, "get", boom)
    assert client.get_raw("/redfish/v1") == (None, None)
    assert client.get("/redfish/v1") is None


def test_redfish_timeout_clamped_to_remaining():
    client = Redfish("10.0.0.1", "u", "p", timeout=10)
    client.set_deadline(time.monotonic() + 5)
    budget = client._timeout()
    assert 4.5 <= budget.total <= 5.001
    assert budget.connect_timeout <= 3.0


def test_redfish_timeout_without_deadline():
    client = Redfish("10.0.0.1", "u", "p", timeout=10)
    assert client._timeout() == (3.0, 10)


def test_ipmi_expired_raises(monkeypatch):
    client = Ipmi("10.0.0.1", "u", "p", tool="/bin/true")
    client.set_deadline(time.monotonic() - 1)

    def boom(*a, **k):
        raise AssertionError("ipmitool must not run past the deadline")

    monkeypatch.setattr("nodetools_runtime.transport.ipmi.subprocess.run", boom)
    with pytest.raises(TransportError):
        client.sdr("Fan")


def test_ipmi_timeout_clamped_to_remaining(monkeypatch):
    client = Ipmi("10.0.0.1", "u", "p", timeout=10, tool="/bin/true")
    client.set_deadline(time.monotonic() + 4)
    seen = {}

    def fake_run(argv, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        return types.SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(
        "nodetools_runtime.transport.ipmi.subprocess.run", fake_run
    )
    client.sdr("Fan")
    assert seen["timeout"] <= 4.001
    assert seen["timeout"] > 0


def test_redfish_counts_transport_errors(monkeypatch):
    import requests

    client = Redfish("10.0.0.1", "u", "p", timeout=10)

    def boom(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(client._session, "get", boom)
    assert client.get_raw("/redfish/v1") == (None, None)
    assert client.transport_errors == 1


def test_ipmi_counts_transport_errors(monkeypatch):
    client = Ipmi("10.0.0.1", "u", "p", tool="/bin/true")

    def boom(*a, **k):
        return types.SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr("nodetools_runtime.transport.ipmi.subprocess.run", boom)
    with pytest.raises(TransportError):
        client.run("power", "status")
    assert client.transport_errors == 1



def test_status_redfish_timeout_is_reported(monkeypatch):
    import requests
    from nodetools_runtime.errors import TransportTimeout
    from nodetools_runtime.subjects.common import resource

    client = Redfish("10.0.0.1", "u", "p")

    def silent(*args, **kwargs):
        raise requests.ReadTimeout()

    monkeypatch.setattr(client._session, "get", silent)
    device = types.SimpleNamespace(redfish=client)
    with pytest.raises(TransportTimeout, match="Redfish GET timed out"):
        resource(device, "/redfish/v1/Systems/1", required=True)


def test_status_ipmi_timeout_is_reported_without_command_args(monkeypatch):
    import subprocess
    from nodetools_runtime.errors import TransportTimeout
    from nodetools_runtime.oem.base import Oem

    client = Ipmi("10.0.0.1", "u", "private-example", tool="/bin/true")

    def silent(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr("nodetools_runtime.transport.ipmi.subprocess.run", silent)
    with pytest.raises(TransportTimeout, match="IPMI request timed out") as error:
        Oem().power(types.SimpleNamespace(name="cp-01", ipmi=client), "status")
    assert "private-example" not in str(error.value)


def test_ctrl_c_exits_sensor_query_without_waiting_or_traceback(tmp_path):
    import signal
    import subprocess
    import sys

    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[compute]\ncp-01 127.0.0.1\n")
    script = """
import concurrent.futures
import sys
import time
from nodetools_runtime import cli

class BlockedRunner:
    def __init__(self, capability, options):
        pass

    def stream(self, hosts):
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(time.sleep, 30)
            print("query started", flush=True)
            future.result()
            yield {}

cli.Runner = BlockedRunner
sys.exit(cli.main(["nodepsu", "-i", sys.argv[1], "cp-01"]))
"""
    with subprocess.Popen(
        [sys.executable, "-c", script, str(inventory)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        try:
            assert proc.stdout.readline().strip() == "query started"
            proc.send_signal(signal.SIGINT)
            output, errors = proc.communicate(timeout=2)
            assert proc.returncode == 130
            assert output == ""
            assert errors == "nodetools: interrupted\n"
        finally:
            if proc.poll() is None:
                proc.kill()


def test_redfish_patch_uses_the_etag_of_the_requested_parent(monkeypatch):
    from types import SimpleNamespace

    from nodetools_runtime.transport.redfish import Redfish

    reads, writes = [], []
    requests = Redfish("example.test", None, None)

    def load(url, **kwargs):
        reads.append(url)
        headers = {"ETag": '"parent-revision"'} if url.endswith("/Bios") else {}
        return SimpleNamespace(ok=True, status_code=200, headers=headers, text="{}", json=dict)

    def patch(url, **kwargs):
        writes.append((url, kwargs.get("headers")))
        return SimpleNamespace(status_code=412, json=dict)

    monkeypatch.setattr(requests._session, "get", load)
    monkeypatch.setattr(requests._session, "patch", patch)
    parent = "/redfish/v1/Systems/1/Bios"
    settings = parent + "/Settings"
    try:
        requests.get(parent)
        requests.get(settings)
        assert requests.patch(settings, {"Attributes": {}}, etag_source=parent)[0] == 412
        assert writes == [(requests.url(settings), {"If-Match": '"parent-revision"'})]
    finally:
        requests.close()
