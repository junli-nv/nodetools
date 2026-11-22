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
