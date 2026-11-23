import io

from nodetools_runtime import cli


def test_bak_fields_file_and_stdin(tmp_path, monkeypatch, capsys):
    data = (
        "node01: Mode=On\nnode02:Version=1\nnode03:Mode=Off\n"
        "node01:Version=1\nnode04:Version=1\nnode02:Mode=On\n"
        "node03:Version=1\nnode05:ERROR unavailable\n"
    )
    export = tmp_path / "bios.txt"
    export.write_text(data, encoding="utf-8")

    assert cli.main(["bak", "--key-value", str(export)]) == 0
    file_result = capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(data))
    assert cli.main(["bak", "--key-value"]) == 0
    assert capsys.readouterr() == file_result
    assert file_result.out.splitlines() == [
        "Mode",
        "  'On' (2): node[01-02]",
        "  'Off' (1): node03",
        "  <missing> (1): node04",
        "4 nodes compared, 1/2 attributes differ",
    ]
    assert "no settings for node05" in file_result.err


def test_bak_fields_rejects_conflicting_repeated_values(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("node01:Mode=On\nnode01:Mode=Off\n"))

    assert cli.main(["bak", "--key-value"]) == 1
    assert "conflicting values for node01:Mode" in capsys.readouterr().err


def test_bak_groups_arbitrary_host_output(monkeypatch, capsys):
    data = "node01: network ready\nnode03: network down\nnode02: network ready\n"
    for flags in ([], ["-c"], ["-C"]):
        monkeypatch.setattr("sys.stdin", io.StringIO(data))
        assert cli.main(["bak", *flags]) == 0
        output = capsys.readouterr().out
        assert "node[01-02]" in output
        assert "Field differences:" not in output
        if flags != ["-c"]:
            assert "-network ready\n+network down" in output
            assert "3 nodes compared, 2 distinct outputs" in output


def test_bak_combines_text_diff_with_field_summary(monkeypatch, capsys):
    data = (
        "node01:CPLD    0x00 0x0b\nnode01:Bundle    null\n"
        "node02:CPLD    0x00 0x0b\nnode02:Bundle    null\n"
        "node03:Bundle    1.2.3\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(data))

    assert cli.main(["bak", "-C", "--key-value"]) == 0
    output = capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(data))
    assert cli.main(["bak"]) == 0
    assert capsys.readouterr().out == output
    assert "Reference (2): node[01-02]" in output
    assert "Variant (1): node03" in output
    assert "-Bundle    null\n+Bundle    1.2.3" in output
    header, bundle, cpld = output.split("Field differences:\n", 1)[1].splitlines()[:3]
    assert header.split() == ["Field", "Reported", "values", "(nodes)", "Missing"]
    assert bundle.split() == ["Bundle", "'null'", "(2);", "'1.2.3'", "(1)", "0"]
    assert cpld.split() == ["CPLD", "'0x00", "0x0b'", "(2)", "1"]
    assert len(header) == len(bundle) == len(cpld)
