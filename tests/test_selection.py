import textwrap

from nodetools_runtime.inventory import Inventory
from nodetools_runtime.selection import select

SAMPLE = textwrap.dedent(
    """
    [compute]
    vdc1-t10-gpu-cp-01 host=10.0.0.1
    vdc1-t10-gpu-cp-02 host=10.0.0.2
    vdc1-t10-gpu-cp-03 host=10.0.0.3

    [powershelf]
    vdc1-t10-gpu-pwr-01 host=10.0.2.1

    [nvswitch]
    vdc1-t10-gpu-nvl-01 host=10.0.1.1
    """
)


def _names(hosts):
    return [h.name for h in hosts]


def test_all():
    inv = Inventory.from_text(SAMPLE)
    assert len(select(inv, all_=True)) == 5


def test_group():
    inv = Inventory.from_text(SAMPLE)
    assert _names(select(inv, groups=["compute"], expand=False)) == [
        "vdc1-t10-gpu-cp-01",
        "vdc1-t10-gpu-cp-02",
        "vdc1-t10-gpu-cp-03",
    ]


def test_pattern_expansion():
    inv = Inventory.from_text(SAMPLE)
    assert _names(select(inv, selectors=["vdc1-t10-gpu-cp-[01-02]"], expand=False)) == [
        "vdc1-t10-gpu-cp-01",
        "vdc1-t10-gpu-cp-02",
    ]


def test_auto_expand_adds_same_row():
    inv = Inventory.from_text(SAMPLE)
    got = _names(select(inv, selectors=["vdc1-t10-gpu-cp-01"]))
    assert "vdc1-t10-gpu-pwr-01" in got
    assert "vdc1-t10-gpu-nvl-01" in got


def test_no_expand():
    inv = Inventory.from_text(SAMPLE)
    got = _names(select(inv, selectors=["vdc1-t10-gpu-cp-01"], expand=False))
    assert got == ["vdc1-t10-gpu-cp-01"]


def test_unknown_host_kept_without_address():
    inv = Inventory.from_text(SAMPLE)
    hosts = select(inv, selectors=["unknown-node"], expand=False)
    assert hosts[0].name == "unknown-node"
    assert hosts[0].address is None
