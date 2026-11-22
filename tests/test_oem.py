import pytest

from nodetools_runtime import oem
from nodetools_runtime.errors import CapUnsupported


def test_known_odms_registered():
    for name in ("gb200", "dell", "lenovo", "smc", "ami", "byd",
                 "giga", "dgx", "wistron", "inventec"):
        assert oem.get(name).name in ("giga", "gb200") or oem.get(name).name == name


def test_gigabyte_alias():
    assert oem.get("gigabyte").name == "giga"


def test_unknown_falls_back_to_generic():
    assert oem.get("nope").name == "generic"


def test_power_shelf_cycle_unsupported():
    class Device:
        redfish = None

    with pytest.raises(CapUnsupported):
        oem.power_shelf_control(Device(), "cycle")
