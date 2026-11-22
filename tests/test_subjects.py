import pytest

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
    vm,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


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

    def set_deadline(self, value):
        pass

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


def test_power_nvsw_reseat_defaults_to_status():
    data = {"/redfish/v1/Systems/System_0": {"PowerState": "On"}}
    rf = FakeRedfish(data)
    device = _device("nvswitch", redfish=rf)
    assert power.collect(device, "reseat", None) == ['cp-01: "On"']
    assert rf.posts == {}


def test_power_nvsw_compal_uses_self_and_full_power_cycle():
    from nodetools_runtime.oem.compal import Compal

    rf = FakeRedfish(post={"/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset": (200, {})})
    device = _device("nvswitch", redfish=rf, odm=Compal())
    lines = power.collect(device, "reseat", None)
    assert lines == ["cp-01: {}"]
    assert rf.posts["/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset"] == {
        "ResetType": "FullPowerCycle"
    }


def test_power_dgx_reseat_uses_aux_power_cycle_force():
    from nodetools_runtime.oem.dgx import Dgx

    path = "/redfish/v1/Chassis/BMC/Actions/Oem/NvidiaChassis.AuxPowerReset"
    rf = FakeRedfish(post={path: (200, {"ResetType": "AuxPowerCycleForce"})})
    device = _device(redfish=rf, odm=Dgx())
    lines = power.collect(device, "reseat", None)
    assert any("AuxPowerCycleForce" in line for line in lines)
    assert rf.posts[path] == {"ResetType": "AuxPowerCycleForce"}


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


# -- aivres ----------------------------------------------------------------
def test_aivres_power_status_quoted():
    from nodetools_runtime.oem.aivres import Aivres

    data = {"/redfish/v1/Systems/1": {"PowerState": "On"}}
    device = _device(redfish=FakeRedfish(data), odm=Aivres())
    assert power.collect(device, "status", None) == ['cp-01: "On"']


def test_aivres_power_reset_posts_system_1():
    from nodetools_runtime.oem.aivres import Aivres

    rf = FakeRedfish()
    lines = power.collect(_device(redfish=rf, odm=Aivres()), "reset", None)
    assert lines == ["cp-01: {}"]
    assert rf.posts["/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"] == {
        "ResetType": "ForceRestart"
    }


def test_aivres_bootorder_sorts_by_number():
    from nodetools_runtime.oem.aivres import Aivres

    path = "/redfish/v1/Systems/1/Bios"
    data = {
        path: {
            "Attributes": {
                "NetworkBbsPriority": [
                    {"BootOptionNumber": 2, "DescriptionName": "B",
                     "DevicePath": "p2"},
                    {"BootOptionNumber": 1, "DescriptionName": "A",
                     "DevicePath": "p1"},
                ]
            }
        }
    }
    device = _device(redfish=FakeRedfish(data), odm=Aivres())
    assert bootorder.collect(device, None, None) == [
        "cp-01: 1,A,p1",
        "cp-01: 2,B,p2",
    ]


def test_aivres_eventlog_hmc_quoted_suggestion():
    from nodetools_runtime.oem.aivres import Aivres

    path = "/redfish/v1/Managers/1/LogServices/IDL/Entries"
    data = {
        path: {
            "Members": [
                {"Created": "2024-01-01T00:00:00", "Severity": "Warning",
                 "Message": "fan", "Oem": {"Public": {"HandlingSuggestion": "fix"}}}
            ]
        }
    }
    device = _device(redfish=FakeRedfish(data), odm=Aivres())
    assert eventlog.collect(device, "status", None) == [
        'cp-01: "2024-01-01T00:00:00 Warning fan fix"'
    ]


# -- compal ----------------------------------------------------------------
def test_compal_bios_base_patches_sd_and_info():
    from nodetools_runtime.oem.compal import Compal

    rf = FakeRedfish()
    lines = bios.collect(_device(redfish=rf, odm=Compal()), "base", None)
    assert "/redfish/v1/Systems/Self/Bios/SD" in rf.patches
    assert rf.patches["/redfish/v1/Systems/Self/Bios/SD"]["Attributes"][
        "NumaEnable"
    ] == "Enabled"
    assert lines[-1] == (
        "cp-01: INFO: cycle the node to make the bios settings take effect"
    )


def test_compal_bios_status_filters_and_sorts():
    from nodetools_runtime.oem.compal import Compal

    path = "/redfish/v1/Systems/Self/Bios"
    data = {
        path: {
            "Attributes": {
                "BootOrder": "x",
                "MAPIDS": "y",
                "UefiPriorities": "z",
                "NumaEnable": "Enabled",
            }
        }
    }
    device = _device(redfish=FakeRedfish(data), odm=Compal())
    assert bios.collect(device, "status", None) == ["cp-01: NumaEnable=Enabled"]


def test_compal_macs_pairs_self_and_bmc():
    from nodetools_runtime.oem.compal import Compal

    sys_eth = "/redfish/v1/Systems/Self/EthernetInterfaces"
    bmc_eth = "/redfish/v1/Managers/BMC/EthernetInterfaces"
    data = {
        sys_eth: {"Members": [{"@odata.id": sys_eth + "/eth0"}]},
        sys_eth + "/eth0": {"Id": "eth0", "MACAddress": "AA:BB:CC:DD:EE:FF",
                            "UefiDevicePath": "PciRoot"},
        bmc_eth: {"Members": [{"@odata.id": bmc_eth + "/b0"}]},
        bmc_eth + "/b0": {"Id": "b0", "MACAddress": "11:22:33:44:55:66"},
    }
    device = _device(redfish=FakeRedfish(data), odm=Compal())
    assert macs.collect(device, None, None) == [
        "cp-01: eth0\tAA:BB:CC:DD:EE:FF\tPciRoot",
        "cp-01: b0\t11:22:33:44:55:66",
    ]


# -- netweb ----------------------------------------------------------------
def test_netweb_bootorder_system_0():
    from nodetools_runtime.oem.netweb import Netweb

    base = "/redfish/v1/Systems/System_0/BootOptions"
    data = {
        base: {"Members": [{"@odata.id": base + "/1"}]},
        base + "/1": {"Id": "Boot0001", "DisplayName": "PXE",
                      "UefiDevicePath": "p"},
        "/redfish/v1/Systems/System_0?$select=Boot/BootOrder": {
            "Boot": {"BootOrder": ["Boot0001"]}
        },
    }
    device = _device(redfish=FakeRedfish(data), odm=Netweb())
    assert bootorder.collect(device, None, None) == ["cp-01: Boot0001,PXE,p"]


def test_netweb_eventlog_sensor_number():
    from nodetools_runtime.oem.netweb import Netweb

    path = "/redfish/v1/Chassis/BMC_0/LogServices/Logs/Entries"
    data = {
        path: {
            "Members": [
                {"Created": "2024-01-01T00:00:00", "Severity": "Critical",
                 "SensorType": "Fan", "SensorNumber": 7, "EntryCode": "0x1"}
            ]
        }
    }
    device = _device(redfish=FakeRedfish(data), odm=Netweb())
    assert eventlog.collect(device, "status", None) == [
        "cp-01: 2024-01-01T00:00:00 Critical Fan Sensor_Number:7 0x1"
    ]


def test_netweb_bios_base_patches_system_0():
    from nodetools_runtime.oem.netweb import Netweb

    rf = FakeRedfish()
    bios.collect(_device(redfish=rf, odm=Netweb()), "base", None)
    assert "/redfish/v1/Systems/System_0/Bios/SD" in rf.patches


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


# -- nodevm ----------------------------------------------------------------
class Opts:
    def __init__(self, image=None, vm_user=None, vm_password=None):
        self.image = image
        self.vm_user = vm_user
        self.vm_password = vm_password


def _ami_vm_data(manager="BMC_0"):
    managers = "/redfish/v1/Managers"
    virtual = managers + "/{}/VirtualMedia".format(manager)
    return {
        managers: {
            "Members": [
                {"@odata.id": managers + "/{}".format(manager)},
                {"@odata.id": managers + "/HGX_Manager_0"},
            ]
        },
        managers + "/{}".format(manager): {
            "Oem": {"Ami": {"VirtualMedia": {"RMediaStatus": "Enabled"}}}
        },
        virtual: {"Members": [{"@odata.id": virtual + "/CD1"}]},
        virtual + "/CD1": {"Id": "CD1", "ImageName": "x.iso", "Image": None},
    }


def test_vm_base_status_discovers_bmc_and_interface():
    rf = FakeRedfish(data=_ami_vm_data())
    lines = vm.collect(_device(redfish=rf), "status", None)
    assert lines == [
        "cp-01: BMC_ID=BMC_0, VMI=CD1",
        "cp-01: {",
        'cp-01:   "RMediaStatus": "Enabled"',
        "cp-01: }",
        "cp-01: CD1\tx.iso\tnull",
    ]


def test_vm_base_mount_cifs_payload():
    rf = FakeRedfish(data=_ami_vm_data())
    path = "/redfish/v1/Managers/BMC_0/VirtualMedia/CD1/Actions/VirtualMedia.InsertMedia"
    vm.collect(_device(redfish=rf), "mount", Opts(image="smb://h/share/y.iso"))
    assert rf.posts[path] == {
        "Image": "smb://h/share/y.iso",
        "UserName": "smbuser",
        "Password": "smbuser",
        "TransferMethod": "Stream",
        "TransferProtocolType": "CIFS",
        "WriteProtected": True,
    }


def test_vm_base_no_interface_message():
    rf = FakeRedfish(data={"/redfish/v1/Managers": {"Members": []}})
    assert vm.collect(_device(redfish=rf), "mount", None) == [
        "cp-01: No Virtual Media Interface found"
    ]


def test_vm_compal_status_single_line():
    from nodetools_runtime.oem.compal import Compal

    rf = FakeRedfish(data=_ami_vm_data("BMC"))
    rf.data["/redfish/v1/Managers/BMC/VirtualMedia/CD1"].update(
        {"Inserted": True, "ConnectedVia": "CIFS", "RedirectionStatus": "Active"}
    )
    lines = vm.collect(_device(redfish=rf, odm=Compal()), "status", None)
    assert lines == [
        "cp-01: RMediaStatus:Enabled,ID:CD1,NAME:x.iso,Image:null,"
        "Inserted:true,ConnectedVia:CIFS,RedirectionStatus:Active"
    ]


def test_vm_compal_insert_posts_cd1():
    from nodetools_runtime.oem.compal import Compal

    rf = FakeRedfish(data=_ami_vm_data("BMC"))
    path = "/redfish/v1/Managers/BMC/VirtualMedia/CD1/Actions/VirtualMedia.InsertMedia"
    vm.collect(_device(redfish=rf, odm=Compal()), "insert", Opts(image="smb://h/y.iso"))
    assert rf.posts[path]["Image"] == "smb://h/y.iso"


def test_vm_netweb_enable_then_status():
    from nodetools_runtime.oem.netweb import Netweb

    rf = FakeRedfish(data=_ami_vm_data("BMC_0"))
    lines = vm.collect(_device(redfish=rf, odm=Netweb()), "enable", None)
    assert "/redfish/v1/Managers/BMC_0/Actions/Oem/AMIVirtualMedia.EnableRMedia" in rf.posts
    assert lines[-1] == "cp-01: Enabled"


def test_vm_aivres_mount_parses_nfs_uri():
    from nodetools_runtime.oem.aivres import Aivres

    vmm = "/redfish/v1/Managers/1/VMMService"
    rf = FakeRedfish(data={vmm: {"RemoteMount": {"Protocol": "NFS"}}})
    lines = vm.collect(
        _device(redfish=rf, odm=Aivres()), "mount", Opts(image="nfs://1.2.3.4/isos/z.iso")
    )
    assert rf.posts[vmm + "/RemoteMount"] == {
        "Host": "1.2.3.4",
        "Path": "/isos",
        "Protocol": "NFS",
    }
    assert lines[-3:] == [
        "cp-01: {",
        'cp-01:   "Protocol": "NFS"',
        "cp-01: }",
    ]


def test_vm_smc_mount_http_payload():
    from nodetools_runtime.oem.smc import Smc

    path = "/redfish/v1/Managers/1/VirtualMedia/VirtualMedia1/Actions/VirtualMedia.InsertMedia"
    rf = FakeRedfish()
    vm.collect(_device(redfish=rf, odm=Smc()), "mount", Opts(image="http://h/x.iso"))
    assert rf.posts[path] == {
        "Image": "http://h/x.iso",
        "WriteProtected": True,
        "Inserted": True,
        "TransferProtocolType": "HTTP",
    }


def test_vm_dell_mount_uses_racadm():
    from nodetools_runtime.oem.dell import Dell

    class FakeDell(Dell):
        def _racadm(self, device, *args):
            self.calls = args
            return "Remote Image is now Configured"

    odm = FakeDell()
    lines = vm.collect(_device(redfish=FakeRedfish(), odm=odm), "mount", None)
    assert odm.calls == ("remoteimage", "-c", "-l", odm.vm_image)
    assert lines == ["cp-01: Remote Image is now Configured"]
