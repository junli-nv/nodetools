import pytest

from nodetools_runtime.arguments import NODE_PROGRAMS, parse_args


def test_command_help_focuses_on_actions(capsys):
    texts = {}
    for command in (*NODE_PROGRAMS, "scan", "inventory"):
        with pytest.raises(SystemExit) as exc:
            parse_args([command, "--help"])
        assert exc.value.code == 0
        texts[command] = capsys.readouterr().out
        for flag in ("--jobs", "--node-timeout", "--password", "--no-cache"):
            assert flag not in texts[command]

    assert "may restart the node immediately" in texts["nodebios"]
    assert "Write the nodetools OEM preset" in texts["nodebios"]
    assert "fixed 50% PWM" in texts["nodefan"]
    assert "fixed 80% PWM" in texts["nodefan"]
    assert "Netweb" not in texts["nvswfan"]


def test_hidden_common_options_still_parse():
    args = parse_args(["nodefan", "HGX474", "check", "--jobs", "4", "--node-timeout", "8"])
    assert (args.noderange, args.action, args.jobs, args.node_timeout) == ("HGX474", "check", 4, 8)
