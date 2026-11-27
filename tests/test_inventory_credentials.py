from pathlib import Path
import subprocess

import pytest

from nodetools_runtime import cli
from nodetools_runtime.inventory import Inventory
from nodetools_runtime.inventory_credentials import read_credentials

ROOT = Path(__file__).resolve().parents[1]


def test_manual_workflow_and_template_do_not_require_bcm(tmp_path, monkeypatch, capsys):
    credentials = tmp_path / "passwd.sh"
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "gen_passwd.sh"), "-o", str(credentials)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    original = credentials.read_text()
    assert "cmsh -> device/category nvswitch -> bmcsettings" in original
    assert credentials.stat().st_mode & 0o777 == 0o600
    retry = subprocess.run(
        ["/bin/bash", str(ROOT / "gen_passwd.sh"), "-o", str(credentials)],
        capture_output=True, text=True,
    )
    assert retry.returncode == 1
    assert credentials.read_text() == original
    credentials.write_text(original.replace("CHANGE_ME_", "TEST_"))
    hosts = tmp_path / "hosts.list"
    hosts.write_text(
        "PhysicalNode cp01 192.0.2.1\n"
        "NVSwitch nvlsw-01 192.0.2.2\n"
        "PowerShelf shelf-01 192.0.2.3\n"
    )
    target = tmp_path / "inventory.ini"
    def no_external_commands(*args, **kwargs):
        raise AssertionError("manual inventory must not execute shell or BCM")
    monkeypatch.setattr(subprocess, "run", no_external_commands)
    assert cli.main(["inventory", "init", "--from", str(hosts),
                     "--passwd-file", str(credentials), "-o", str(target)]) == 0
    inv = Inventory.from_file(target)
    assert inv.get("cp01").get("bmc_user") == "root"
    assert inv.get("cp01").get("bmc_password") == "TEST_COMPUTE"
    assert inv.get("nvlsw-01").get("nvswitch_bmc_password") == "TEST_NVSWITCH_BMC"
    assert inv.get("nvlsw-01").get("nvswitch_os_password") == "TEST_NVOS"
    assert inv.get("shelf-01").get("powershelf_bmc_password") == "TEST_POWERSHELF"
    assert inv.all_vars["ibswitch_os_pass"] == "TEST_IB"
    assert target.stat().st_mode & 0o777 == 0o600
    assert "TEST_" not in capsys.readouterr().out


def test_literal_passwords_are_not_executed_or_expanded(tmp_path):
    marker = tmp_path / "executed"
    password = "p @ $HOME ; $(touch {})".format(marker)
    credentials = tmp_path / "passwd.sh"
    credentials.write_text("export bmc_password={!r}\n".format(password))
    groups = read_credentials(credentials)
    assert groups["compute"]["bmc_password"] == password
    assert not marker.exists()


@pytest.mark.parametrize("assignment", [
    "touch executed",
    "export bmc_password=$(touch executed)",
    "export bmc_password='unterminated",
    "export bmc_password=$UNSET",
    "export bmc_password='cannot#represent'",
])
def test_invalid_credentials_do_not_replace_inventory(tmp_path, assignment, capsys):
    hosts = tmp_path / "hosts.list"
    hosts.write_text("PhysicalNode cp01 192.0.2.1\n")
    credentials = tmp_path / "passwd.sh"
    credentials.write_text(assignment + "\n")
    target = tmp_path / "inventory.ini"
    target.write_text("existing inventory")
    assert cli.main(["inventory", "init", "--from", str(hosts),
                     "--passwd-file", str(credentials), "-o", str(target)]) == 1
    assert target.read_text() == "existing inventory"
    assert assignment not in capsys.readouterr().err
