import csv
import io
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from nodetools_runtime import switch_health

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("program", ["ibswhc", "ibswmon", "spxswhc", "spxswmon"])
@pytest.mark.parametrize("debug", [False, True])
def test_switch_debug_reaches_execution_without_corrupting_output(program, debug, tmp_path, monkeypatch, capsys):
    inventory = tmp_path / "inventory.ini"
    inventory.write_text("[ibswitch]\nleaf01 host=192.0.2.1 ssh_user=operator\n")
    checks = tmp_path / "checks.sh"
    checks.write_text("echo fixture\n")
    monkeypatch.setenv("SSHPASS", "fixture-ssh-secret")
    monkeypatch.setattr(switch_health.shutil, "which", lambda name: "/fixture/" + name)
    calls = []
    monitor = program.endswith("mon")
    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="31\n32\nforward\nOK\nOK\n" if monitor else "OK\n", stderr="")
    monkeypatch.setattr(switch_health.subprocess, "run", fake_run)
    argv = ["leaf01", "-i", str(inventory), "--checks", str(checks)]
    if debug:
        argv += ["-x"]
    assert switch_health.main(program, argv) == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[-3:] == ["operator@192.0.2.1", "bash", "-s"]
    assert command[:2] == ["sshpass", "-e"]
    assert kwargs["input"] == checks.read_text()
    output = capsys.readouterr()
    assert "fixture-ssh-secret" not in output.err
    if debug:
        assert "operator@192.0.2.1" in output.err and "SSH success=True" in output.err
    else:
        assert not output.err
    if monitor:
        rows = list(csv.reader(io.StringIO(output.out)))
        assert len(rows) == 2 and len(rows[1]) == 7 and rows[1][1] == "leaf01"
    else:
        assert output.out == "leaf01: OK\n"


def test_password_template_debug_is_private_and_never_overwrites(tmp_path):
    target = tmp_path / "passwd.sh"
    argv = ["/bin/bash", str(ROOT / "gen_passwd.sh"), "-x", "-o", str(target)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "[gen_passwd]" in result.stderr and "mode=0600" in result.stderr
    assert "CHANGE_ME" not in result.stderr and "cmsh ->" not in result.stderr
    assert "cmsh ->" in target.read_text()
    assert target.stat().st_mode & 0o777 == 0o600
    target.write_text("private-existing-content")
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert target.read_text() == "private-existing-content"
    assert "private-existing-content" not in result.stderr
