import types

from nodetools_runtime import reach


def test_ping_no_address():
    assert reach.ping(None) is None
    assert reach.ping("") is None


def test_ping_success(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "ping"
        assert "10.0.0.1" in cmd
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(reach.subprocess, "run", fake_run)
    assert reach.ping("10.0.0.1") is True


def test_ping_unreachable(monkeypatch):
    monkeypatch.setattr(
        reach.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1)
    )
    assert reach.ping("10.0.0.1") is False


def test_ping_timeout_returns_false(monkeypatch):
    def boom(*a, **k):
        raise reach.subprocess.TimeoutExpired(cmd="ping", timeout=1)

    monkeypatch.setattr(reach.subprocess, "run", boom)
    assert reach.ping("10.0.0.1") is False
