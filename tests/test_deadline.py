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
    connect, read = client._timeout()
    assert read <= 5.001
    assert read >= 4.5
    assert connect <= 3.0


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

