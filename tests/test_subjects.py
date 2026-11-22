from nodetools_runtime import model, oem
from nodetools_runtime.inventory import Host
from nodetools_runtime.subjects import (
    bios,
    boot,
    bootorder,
    eventlog,
    inventory,
    macs,
    netboot,
    power,
)


class FakeRedfish:
    def __init__(self, data=None, post=None, patch=None):
        self.data = data or {}
        self.posts = {}
        self.post_results = post or {}
        self.patches = {}
        self.patch_results = patch or {}

    def get(self, path, timeout=None):
        return self.data.get(path)

    def post(self, path, body, timeout=None):
        self.posts[path] = body
        return self.post_results.get(path, (200, {}))

    def patch(self, path, body, timeout=None):
        self.patches[path] = body
        return self.patch_results.get(path, (200, {}))

    def close(self):
        pass


class FakeIpmi:
    def __init__(self, power_out="Chassis Power is on", sel=""):
        self.power_out = power_out
        self._sel = sel
        self.calls = []

    def power(self, action):
        self.calls.append(("power", action))
        return self.power_out

    def run(self, *command):
        self.calls.append(("run",) + command)
        return self._sel

    def raw(self, *payload):
        self.calls.append(("raw",) + payload)
        return ""


def _device(role="compute", redfish=None, ipmi=None, odm=None):
    host = Host("cp-01", "10.0.0.1", {}, [role])
    device = model.Device(
        host,
        redfish_factory=lambda h: redfish or FakeRedfish(),
        ipmi_factory=lambda h: ipmi or FakeIpmi(),
    )
    device.odm = odm if odm is not None else oem.get(None)
    return device


# -- power -----------------------------------------------------------------
def test_power_compute_defaults_to_ipmi():
    device = _device(ipmi=FakeIpmi("Chassis Power is on"))
    assert power.collect(device, None, None) == ["cp-01: Chassis Power is on"]
    assert device.ipmi.calls == [("power", "status")]


def test_power_compute_ipmi_failure_is_na():
    class Boom(FakeIpmi):
        def power(self, action):
            raise RuntimeError("no route")

    lines = power.collect(_device(ipmi=Boom()), "status", None)
    assert lines == ["cp-01: N/A"]


def test_power_nvsw_status_is_quoted():
    data = {"/redfish/v1/Systems/System_0": {"PowerState": "On"}}
    device = _device("nvswitch", redfish=FakeRedfish(data))
    assert power.collect(device, "status", None) == ['cp-01: "On"']


def test_power_nvsw_cycle_posts_reset():
    rf = FakeRedfish(post={"/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset": (200, {})})
    device = _device("nvswitch", redfish=rf)
    lines = power.collect(device, "cycle", None)
    assert lines == ["cp-01: {}"]
    assert rf.posts["/redfish/v1/Systems/System_0/Actions/ComputerSystem.Reset"] == {
        "ResetType": "PowerCycle"
    }


def test_power_lenovo_cycle_reads_state():
    from nodetools_runtime.oem.lenovo import Lenovo

    rf = FakeRedfish(
        data={"/redfish/v1/Systems/1": {"PowerState": "Off"}},
        post={"/redfish/v1/Systems/1/Actions/ComputerSystem.Reset": (200, {})},
    )
    device = _device(redfish=rf, odm=Lenovo())
    lines = power.collect(device, "cycle", None)
    assert lines == ["cp-01: Reset action 'On' executed successfully."]
    assert rf.posts["/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"] == {
        "ResetType": "On"
    }


def test_power_smc_status_is_quoted():
    from nodetools_runtime.oem.smc import Smc

    data = {"/redfish/v1/Systems/1": {"PowerState": "On"}}
    device = _device(redfish=FakeRedfish(data), odm=Smc())
    assert power.collect(device, "status", None) == ['cp-01: "On"']


# -- eventlog --------------------------------------------------------------
def test_eventlog_base_hmc_lines():
    path = "/redfish/v1/Systems/HGX_Baseboard_0/LogServices/EventLog/Entries"
    data = {
        path: {
            "Members": [
                {"Created": "2024-01-02T03:04:05", "Severity": "Warning",
                 "Message": "fan slow"},
                {"Created": "2024-01-01T00:00:00", "Severity": "OK",
                 "Message": "boot"},
            ]
        }
    }
    lines = eventlog.collect(_device(redfish=FakeRedfish(data)), "status", None)
    assert lines == [
        "cp-01[HMC]: 2024-01-01T00:00:00\tOK\tboot",
        "cp-01[HMC]: 2024-01-02T03:04:05\tWarning\tfan slow",
    ]


def test_eventlog_nvsw_space_separated():
    path = "/redfish/v1/Systems/System_0/LogServices/EventLog/Entries"
    data = {
        path: {
            "Members": [
                {"Created": "2024-01-01T00:00:00", "Severity": "OK", "Message": "up"}
            ]
        }
    }
    device = _device("nvswitch", redfish=FakeRedfish(data))
    assert eventlog.collect(device, "status", None) == [
        "cp-01: 2024-01-01T00:00:00 OK up"
    ]


def test_eventlog_dell_posts_clear():
    from nodetools_runtime.oem.dell import Dell

    path = "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries"
    data = {
        path: {
            "Members": [
                {"Created": "2024-01-01T00:00:00", "Severity": "OK", "Message": "ok"}
            ]
        }
    }
    lines = eventlog.collect(
        _device(redfish=FakeRedfish(data), odm=Dell()), "status", None
    )
    assert lines == ["cp-01: 2024-01-01T00:00:00 OK ok"]


# -- inventory -------------------------------------------------------------
def _firmware_members():
    base = "/redfish/v1/UpdateService/FirmwareInventory"
    return {
        base: {
            "Members": [
                {"@odata.id": base + "/BMC"},
                {"@odata.id": base + "/BIOS"},
            ]
        },
        base + "/BMC": {"Id": "BMC", "Version": "1.0"},
        base + "/BIOS": {"Id": "BIOS", "Version": "2.0"},
    }


def test_inventory_compute_has_no_space_after_colon():
    data = _firmware_members()
    lines = inventory.collect(_device(redfish=FakeRedfish(data)), None, None)
    assert lines == ["cp-01:BMC   1.0", "cp-01:BIOS  2.0"]


def test_inventory_nvsw_has_space_after_colon():
    data = _firmware_members()
    device = _device("nvswitch", redfish=FakeRedfish(data))
    lines = inventory.collect(device, None, None)
    assert lines == ["cp-01: BMC   1.0", "cp-01: BIOS  2.0"]


def test_inventory_lenovo_filters_pending():
    from nodetools_runtime.oem.lenovo import Lenovo

    data = _firmware_members()
    data["/redfish/v1/UpdateService/FirmwareInventory/BIOS"]["Version"] = "2.0 Pending"
    lines = inventory.collect(
        _device(redfish=FakeRedfish(data), odm=Lenovo()), None, None
    )
    assert lines == ["cp-01: BMC 1.0"]


def test_inventory_ami_name_style():
    from nodetools_runtime.oem.ami import Ami

    base = "/redfish/v1/UpdateService/FirmwareInventory"
    data = {
        base: {"Members": [{"@odata.id": base + "/x"}]},
        base + "/x": {"Name": "CPLD", "Version": "3.1"},
    }
    lines = inventory.collect(
        _device(redfish=FakeRedfish(data), odm=Ami()), None, None
    )
    assert lines == ["cp-01: CPLD 3.1"]


# -- boot ------------------------------------------------------------------
def test_boot_compute_runs_chassis_bootdev():
    from nodetools_runtime.oem.base import Oem

    ipmi = FakeIpmi()
    ipmi._sel = "Boot device set to PXE"
    device = _device(ipmi=ipmi, odm=Oem())
    assert boot.collect(device, "pxe", None) == ["cp-01: Boot device set to PXE"]
    assert ("run", "chassis", "bootdev", "pxe") in ipmi.calls


def test_boot_compute_defaults_to_pxe():
    ipmi = FakeIpmi()
    ipmi._sel = "ok"
    boot.collect(_device(ipmi=ipmi), None, None)
    assert ("run", "chassis", "bootdev", "pxe") in ipmi.calls


# -- bootorder -------------------------------------------------------------
def test_bootorder_base_quoted_and_expanded():
    base = "/redfish/v1/Systems/system/BootOptions?$expand=."
    data = {
        base: {
            "Members": [
                {"Id": "Boot0001", "DisplayName": "UEFI PXE",
                 "UefiDevicePath": "PciRoot(0x16)/MAC(A088C2123456,0x1)"},
                {"Id": "Boot0002", "DisplayName": "UEFI Shell",
                 "UefiDevicePath": "VenHw(abcd)"},
            ]
        },
        "/redfish/v1/Systems/system?$select=Boot/BootOrder": {
            "Boot": {"BootOrder": ["Boot0002", "Boot0001"]}
        },
    }
    lines = bootorder.collect(_device(redfish=FakeRedfish(data)), None, None)
    assert lines == [
        'cp-01: "Boot0002"\t"UEFI Shell"\t"VenHw(abcd)"',
        'cp-01: "Boot0001"\t"UEFI PXE"\t"PciRoot(0x16)/MAC(A088C2123456,0x1)"',
    ]


def test_bootorder_ami_comma_unquoted():
    from nodetools_runtime.oem.ami import Ami

    base = "/redfish/v1/Systems/1/BootOptions"
    data = {
        base: {"Members": [{"@odata.id": base + "/1"}]},
        base + "/1": {"Id": "Boot0001", "DisplayName": "PXE", "UefiDevicePath": "p"},
        "/redfish/v1/Systems/1?$select=Boot/BootOrder": {
            "Boot": {"BootOrder": ["Boot0001"]}
        },
    }
    device = _device(redfish=FakeRedfish(data), odm=Ami())
    assert bootorder.collect(device, None, None) == ["cp-01: Boot0001,PXE,p"]


def test_bootorder_dell_two_fields():
    from nodetools_runtime.oem.dell import Dell

    base = "/redfish/v1/Systems/System.Embedded.1/BootOptions"
    data = {
        base: {"Members": [{"@odata.id": base + "/1"}]},
        base + "/1": {"Id": "Boot0001", "DisplayName": "NIC PXE",
                      "UefiDevicePath": "p"},
        "/redfish/v1/Systems/System.Embedded.1?$select=Boot/BootOrder": {
            "Boot": {"BootOrder": ["Boot0001"]}
        },
    }
    device = _device(redfish=FakeRedfish(data), odm=Dell())
    assert bootorder.collect(device, None, None) == ["cp-01: Boot0001,NIC PXE"]


# -- macs ------------------------------------------------------------------
def test_macs_base_os_and_bmc():
    expand = "/redfish/v1/Systems/system/BootOptions?$expand=."
    bmc = "/redfish/v1/Managers/BMC_0/EthernetInterfaces"
    data = {
        expand: {
            "Members": [
                {"Id": "Boot0001", "DisplayName": "UEFI PXE IPv4",
                 "UefiDevicePath":
                     "PciRoot(0x16)/Pci(1|0)/MAC(A088C2123456,0x1)"},
            ]
        },
        bmc: {"Members": [{"@odata.id": bmc + "/eth0"}]},
        bmc + "/eth0": {"Id": "eth0", "MACAddress": "AA:BB:CC:DD:EE:FF"},
    }
    lines = macs.collect(_device(redfish=FakeRedfish(data)), None, None)
    assert lines == [
        "cp-01:OS Boot0001 A0:88:C2:12:34:56 PciRoot(0x16)/Pci(1|0)",
        'cp-01:BMC "eth0"\t"AA:BB:CC:DD:EE:FF"',
    ]


# -- bios ------------------------------------------------------------------
def test_bios_base_filters_and_formats():
    path = "/redfish/v1/Systems/system/Bios"
    data = {
        path: {
            "Attributes": {
                "BootOrder": "x",
                "SETUP006": "y",
                "MAPIDS": "z",
                "HyperThreading": True,
                "SomeNum": 4.0,
                "Empty": None,
            }
        }
    }
    lines = bios.collect(_device(redfish=FakeRedfish(data)), "status", None)
    assert lines == [
        "cp-01:BootOrder=x",
        "cp-01:HyperThreading=true",
        "cp-01:SomeNum=4",
        "cp-01:Empty=null",
    ]


def test_bios_lenovo_base_patches_pending():
    from nodetools_runtime.oem.lenovo import Lenovo

    pending = "/redfish/v1/Systems/1/Bios/Pending"
    rf = FakeRedfish(patch={pending: (200, {"ok": True})})
    lines = bios.collect(_device(redfish=rf, odm=Lenovo()), "base", None)
    assert lines == ["cp-01: {", 'cp-01:   "ok": true', "cp-01: }"]
    attributes = rf.patches[pending]["Attributes"]
    assert attributes["SystemSettings_WorkloadProfile"] == "HighPerformanceComputing"


# -- netboot ---------------------------------------------------------------
def test_netboot_base_continuous_pxe():
    system = "/redfish/v1/Systems/system"
    expand = system + "/BootOptions?$expand=."
    data = {
        system: {
            "Boot": {
                "BootSourceOverrideEnabled": "Continuous",
                "BootSourceOverrideMode": "UEFI",
                "BootSourceOverrideTarget": "Pxe",
            }
        },
        system + "?$select=Boot/BootOrder": {
            "Boot": {"BootOrder": ["Boot0002", "Boot0001"]}
        },
        expand: {
            "Members": [
                {"Id": "Boot0001", "DisplayName": "PXE",
                 "UefiDevicePath": "PciRoot(0x16)/NIC"},
                {"Id": "Boot0002", "DisplayName": "Shell",
                 "UefiDevicePath": "VenHw"},
            ]
        },
    }
    rf = FakeRedfish(data=data, patch={system: (200, {"Status": "ok"})})
    lines = netboot.collect(_device(redfish=rf), None, None)
    assert lines == [
        "cp-01:Enable continuous PXE boot override option",
        'cp-01:{"Status": "ok"}',
        "cp-01:Continuous,UEFI,Pxe",
        'cp-01:OLD_BOOT_ORDER="Boot0002","Boot0001"',
        "cp-01:New BootOrder:",
        "cp-01:Boot0002,Boot0001",
        "cp-01:INFO: reboot node to take effect",
    ]
    assert rf.patches[system] == {"Boot": {"BootOrder": ['"Boot0001"', '"Boot0002"']}}


def test_netboot_smc_reorders_network_first():
    from nodetools_runtime.oem.smc import Smc

    order_path = "/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
    reset_path = "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
    data = {order_path: {"FixedBootOrder": ["UEFI Shell", "UEFI Network", "Disk"]}}
    rf = FakeRedfish(
        data=data,
        patch={order_path: (200, {"ok": 1})},
        post={reset_path: (200, {"ResetType": "GracefulRestart"})},
    )
    lines = netboot.collect(_device(redfish=rf, odm=Smc()), None, None)
    assert rf.patches[order_path] == {
        "FixedBootOrder": ["UEFI Network", "UEFI Shell", "Disk"]
    }
    assert rf.posts[reset_path] == {"ResetType": "GracefulRestart"}
    assert all(line.startswith("cp-01:") for line in lines)
