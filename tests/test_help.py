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
    assert "Power the tray off and back on" in texts["nvswpower"]
    assert "  reset " not in texts["nvswpower"]
    assert "  reseat " not in texts["nvswpower"]
    assert "Read HMC logs" in texts["nodeeventlog"]
    assert "Read BMC logs" in texts["nvsweventlog"]
    assert "Read BMC logs" not in texts["pseventlog"]
    assert "Read the default logs" in texts["pseventlog"]
    for command in ("nvsweventlog", "pseventlog"):
        assert "hmc" not in texts[command].lower()


@pytest.mark.parametrize("command", ["nvsweventlog", "pseventlog"])
@pytest.mark.parametrize("action", [["hmc"], ["--action", "HMC"]])
def test_infrastructure_eventlog_rejects_hmc_before_inventory(command, action, capsys):
    from nodetools_runtime import cli

    assert cli.main([command, "-i", "/nonexistent-inventory", "device-01", *action]) == 1
    assert "only available for compute trays" in capsys.readouterr().err


def test_hidden_common_options_still_parse():
    args = parse_args(["nodefan", "HGX474", "check", "--jobs", "4", "--node-timeout", "8"])
    assert (args.noderange, args.action, args.jobs, args.node_timeout) == ("HGX474", "check", 4, 8)


@pytest.mark.parametrize("flag", ["-x", "--debug"])
def test_debug_flag_is_available_on_all_commands(flag):
    for command in (*NODE_PROGRAMS, "scan", "list", "ping"):
        assert parse_args([command, flag]).debug is True, command
    for action in ("init", "show"):
        assert parse_args(["inventory", action, flag]).debug is True
        assert parse_args(["inventory", flag, action]).debug is True
        assert parse_args([flag, "inventory", action]).debug is True
