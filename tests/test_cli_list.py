import textwrap

from nodetools_runtime import cli

SAMPLE = textwrap.dedent(
    """
    [compute]
    vdc1-t10-gpu-cp-01 host=10.0.0.1
    vdc1-t10-gpu-cp-02 host=10.0.0.2

    [powershelf]
    vdc1-t10-gpu-pwr-01 host=10.0.2.1
    """
)


def _inv(tmp_path):
    path = tmp_path / "inv.ini"
    path.write_text(SAMPLE)
    return str(path)


def test_bare_command_prints_help_not_error(capsys):
    for command in ("scan", "nodepower", "nodefan", "nodeboot", "ping"):
        rc = cli.main([command])
        captured = capsys.readouterr()
        assert rc == 0, command
        assert "usage:" in captured.out, command
        assert captured.err == "", command


def test_bare_list_defaults_to_all(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("NODETOOLS_INVENTORY", _inv(tmp_path))
    rc = cli.main(["list"])
    out = capsys.readouterr()
    assert rc == 0
    assert "   PhysicalNode: vdc1-t10-gpu-cp-[01-02]" in out.out
    assert "     PowerShelf: vdc1-t10-gpu-pwr-01" in out.out


def test_bare_ping_prints_help_not_error(capsys):
    rc = cli.main(["ping"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "usage:" in captured.out
    assert captured.err == ""


def test_list_all_aggregated(tmp_path, capsys):
    rc = cli.main(["list", "-i", _inv(tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert "   PhysicalNode: vdc1-t10-gpu-cp-[01-02]" in out.out
    assert "     PowerShelf: vdc1-t10-gpu-pwr-01" in out.out
    assert out.err == ""


def test_list_group_long(tmp_path, capsys):
    rc = cli.main(["list", "compute", "-i", _inv(tmp_path), "--long"])
    out = capsys.readouterr()
    assert rc == 0
    assert "vdc1-t10-gpu-cp-01: compute 10.0.0.1" in out.out
    assert "powershelf" not in out.out


def test_ping_aggregated(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.reach, "ping", lambda ip, timeout=1.0: ip == "10.0.0.1")
    rc = cli.main(["ping", "compute", "-i", _inv(tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert "----------------\nvdc1-t10-gpu-cp-01\n----------------\nBMC pingable" in out.out
    assert (
        "----------------\nvdc1-t10-gpu-cp-02\n----------------\nBMC unreachable"
        in out.out
    )


def test_ping_long_per_host(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.reach, "ping", lambda ip, timeout=1.0: ip == "10.0.0.1")
    rc = cli.main(["ping", "compute", "-i", _inv(tmp_path), "--long"])
    out = capsys.readouterr()
    assert rc == 0
    assert "vdc1-t10-gpu-cp-01: BMC pingable" in out.out
    assert "vdc1-t10-gpu-cp-02: BMC unreachable" in out.out


def test_ping_default_all(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.reach, "ping", lambda ip, timeout=1.0: ip == "10.0.0.1")
    rc = cli.main(["ping", "-i", _inv(tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert "----------------\nvdc1-t10-gpu-cp-01\n----------------\nBMC pingable" in out.out
    assert (
        "----------------\nvdc1-t10-gpu-cp-02,vdc1-t10-gpu-pwr-01\n----------------\n"
        "BMC unreachable" in out.out
    )
