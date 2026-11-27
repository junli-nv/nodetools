import os
from pathlib import Path
import subprocess
import sys

import pytest

from nodetools_runtime.inventory import Inventory, generate_from_hostslist

SCRIPT = Path(__file__).resolve().parents[1] / "gen_hosts.sh"


@pytest.fixture
def exporter(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "python3").symlink_to(sys.executable)
    fake = bindir / "cmsh"
    fake.write_text("#!" + sys.executable + "\n" + '''
import os
import sys
mode = os.environ.get("BCM_TEST_MODE", "ok")
query = sys.argv[-1]
if mode == "fail":
    sys.exit(3)
if mode == "empty":
    sys.exit(0)
if "PhysicalNode" in query:
    print("cp01")
    if mode != "missing":
        print("bmc   rf0   192.0.2.1   ipminet   always")
elif "PowerShelf" in query:
    print("shelf-01")
    print("physical eth1 192.0.2.99 external always")
    print("physical eth0 192.0.2.3 power always")
else:
    print("nvlsw-01")
    print("bmc rf0 2001:db8::2 nvl always")
''')
    fake.chmod(0o700)
    target = tmp_path / "hosts.list"
    env = dict(os.environ, PATH=str(bindir))
    def run(mode="ok"):
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), "-o", str(target)],
            env=dict(env, BCM_TEST_MODE=mode), capture_output=True, text=True, timeout=10,
        )
    return run, target, fake


def test_export_is_compatible_with_generic_inventory(exporter):
    run, target, _ = exporter
    target.write_text("old")
    result = run()
    assert result.returncode == 0, result.stderr
    inv = Inventory.from_text(generate_from_hostslist(target))
    assert inv.get("cp01").role == "compute"
    assert inv.get("nvlsw-01").role == "nvswitch"
    assert inv.get("nvlsw-01").address == "2001:db8::2"
    assert inv.get("shelf-01").address == "192.0.2.3"
    assert len(inv.hosts) == 3


@pytest.mark.parametrize("mode", ["fail", "empty", "missing"])
def test_export_failure_preserves_old_hosts(exporter, mode):
    run, target, _ = exporter
    target.write_text("existing data")
    result = run(mode)
    assert result.returncode == 1
    assert target.read_text() == "existing data"
    assert list(target.parent.glob(".hosts.list.*")) == []


def test_missing_bcm_is_only_an_exporter_error(exporter):
    run, target, fake = exporter
    fake.unlink()
    target.write_text("existing data")
    result = run()
    assert result.returncode == 2
    assert "cmsh not found" in result.stderr
    assert target.read_text() == "existing data"
