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


def test_list_all(tmp_path, capsys):
    rc = cli.main(["list", "-i", _inv(tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert "vdc1-t10-gpu-cp-01: compute" in out.out
    assert "vdc1-t10-gpu-pwr-01: powershelf" in out.out
    assert "3 hosts" in out.err


def test_list_group_long(tmp_path, capsys):
    rc = cli.main(["list", "compute", "-i", _inv(tmp_path), "--long"])
    out = capsys.readouterr()
    assert rc == 0
    assert "vdc1-t10-gpu-cp-01: compute 10.0.0.1" in out.out
    assert "powershelf" not in out.out


def test_ping_filters_reachable(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.reach, "ping", lambda ip, timeout=1.0: ip == "10.0.0.1")
    rc = cli.main(["ping", "compute", "-i", _inv(tmp_path)])
    out = capsys.readouterr()
    assert rc == 0
    assert "vdc1-t10-gpu-cp-01: BMC pingable" in out.out
    assert "vdc1-t10-gpu-cp-02: BMC unreachable" in out.out


def test_ping_requires_selector(tmp_path, capsys):
    rc = cli.main(["ping", "-i", _inv(tmp_path)])
    assert rc == 2
