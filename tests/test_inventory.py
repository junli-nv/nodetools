import textwrap

from nodetools_runtime.inventory import Inventory, generate_from_hostslist

SAMPLE = textwrap.dedent(
    """
    [compute]
    cp-01 host=10.0.0.1
    cp-02 bmc_ip=10.0.0.2 bmc_user=admin

    [compute:vars]
    bmc_user=root
    bmc_password=0penBmc

    [powershelf]
    pwr-01 host=10.0.1.1

    [all:vars]
    timeout=5

    [nodehealth:defaults]
    jobs=4
    summary=false
    """
)


def test_groups_and_addresses():
    inv = Inventory.from_text(SAMPLE)
    assert inv.members("compute") == ["cp-01", "cp-02"]
    assert inv.get("cp-01").address == "10.0.0.1"
    assert inv.get("cp-02").address == "10.0.0.2"


def test_role_from_group_and_name():
    inv = Inventory.from_text(SAMPLE)
    assert inv.get("cp-01").role == "compute"
    assert inv.get("pwr-01").role == "powershelf"


def test_variable_precedence():
    inv = Inventory.from_text(SAMPLE)
    # group :vars overrides all:vars
    assert inv.get("cp-01").get("bmc_user") == "root"
    # per-host inline overrides group :vars
    assert inv.get("cp-02").get("bmc_user") == "admin"


def test_defaults():
    inv = Inventory.from_text(SAMPLE)
    assert inv.default("jobs") == "4"
    assert inv.default_bool("summary", True) is False


def test_hash_comments_are_ignored():
    text = textwrap.dedent(
        """
        # whole-line comment
        ; semicolon comment
        [compute]
        cp-01 host=10.0.0.1   # inline comment
        # cp-99 host=10.0.0.99 (commented-out host)
        cp-02 host=10.0.0.2
        """
    )
    inv = Inventory.from_text(text)
    assert inv.members("compute") == ["cp-01", "cp-02"]
    assert inv.get("cp-01").address == "10.0.0.1"
    assert inv.get("cp-99") is None


def test_generate_from_hostslist(tmp_path):
    src = tmp_path / "hosts.list"
    src.write_text(
        "PhysicalNode vdc1-t10-gpu-cp-01 10.0.0.1\n"
        "PowerShelf vdc1-t10-gpu-pwr-01 10.0.2.1\n"
        "Switch vdc1-t10-gpu-nvl-01 10.0.1.1\n"
    )
    text = generate_from_hostslist(str(src))
    inv = Inventory.from_text(text)
    assert inv.members("compute") == ["vdc1-t10-gpu-cp-01"]
    assert inv.members("powershelf") == ["vdc1-t10-gpu-pwr-01"]
    assert inv.members("nvswitch") == ["vdc1-t10-gpu-nvl-01"]
    assert inv.get("vdc1-t10-gpu-cp-01").address == "10.0.0.1"


def test_custom_role_group_and_inline_overrides():
    from types import SimpleNamespace

    from nodetools_runtime.cli import _role_hosts

    inv = Inventory.from_text("""
    [all:vars]
    oem=dell

    [dell_nvl]
    tray-01 host=192.0.2.20
    tray-02 host=192.0.2.21 platform=hgx oem=lenovo

    [dell_nvl:vars]
    role=compute
    platform=nvl

    [rack]
    tray-02
    """)
    assert inv.get("tray-01").get("platform") == "nvl"
    assert inv.get("tray-02").get("platform") == "hgx"
    assert inv.get("tray-02").get("oem") == "lenovo"
    assert [host.name for host in _role_hosts(SimpleNamespace(), inv, "compute")] == [
        "tray-01", "tray-02"
    ]


def test_legacy_import_previews_preserves_and_is_repeatable(tmp_path):
    from nodetools_runtime.inventory_import import import_hostslist

    source = tmp_path / "hosts.list"
    source.write_text(
        "PowerShelf shelf-01 192.0.2.5\n"
        "SMC-L1POD4 HGX654 192.0.2.9\n"
        "SMC HGX655 192.0.2.2\n"
        "AVR-L5POD4 HGX390 192.0.2.3\n"
        "AVR-L5POD4 hgx390 192.0.2.3\n"
    )
    target = tmp_path / "inventory.ini"
    original = b"# example\n[compute]\nHGX654 host=192.0.2.1 oem=smc temp_chassis_id=1"
    target.write_bytes(original)
    target.chmod(0o640)
    added, present, conflicts = import_hostslist(source, target)
    assert added == {"compute": [("HGX655", "192.0.2.2"), ("HGX390", "192.0.2.3")],
                     "powershelf": [("shelf-01", "192.0.2.5")]}
    assert present == 0 and conflicts == ["HGX654: BMC address differs"]
    assert target.read_bytes() == original

    assert import_hostslist(source, target, apply=True) == (added, present, conflicts)
    merged = target.read_bytes()
    inv = Inventory.from_file(target)
    assert merged.startswith(original)
    assert inv.members("compute") == ["HGX654", "HGX655", "HGX390"]
    assert inv.members("powershelf") == ["shelf-01"]
    assert inv.get("HGX654").get("temp_chassis_id") == "1"
    assert target.stat().st_mode & 0o777 == 0o640
    assert import_hostslist(source, target, apply=True) == ({}, 3, conflicts)
    assert target.read_bytes() == merged


def test_legacy_import_rejects_conflicting_duplicate_before_writing(tmp_path):
    import pytest
    from nodetools_runtime.errors import InventoryError
    from nodetools_runtime.inventory_import import import_hostslist

    source = tmp_path / "hosts.list"
    source.write_text("SMC HGX655 192.0.2.2\nAVR hgx655 192.0.2.3\n")
    target = tmp_path / "inventory.ini"
    target.write_text("[compute]\n")
    with pytest.raises(InventoryError, match="conflicting hosts.list entries"):
        import_hostslist(source, target, apply=True)
    assert target.read_text() == "[compute]\n"
