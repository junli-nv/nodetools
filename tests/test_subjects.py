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

    def patch(self, path, body, timeout=None, etag_source=None):
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
    assert power.collect(device, None, None) == ["cp-01: On"]
    assert device.ipmi.calls == [("power", "status")]


def test_power_compute_normalizes_off_and_redfish_transition():
    from nodetools_runtime.oem.hgx.lenovo import Lenovo

    ipmi = FakeIpmi("Chassis Power is off")
    assert power.collect(_device(ipmi=ipmi), "status", None) == ["cp-01: Off"]
    rf = FakeRedfish({"/redfish/v1/Systems/1": {"PowerState": "PoweringOn"}})
    assert power.collect(_device(redfish=rf, odm=Lenovo()), "status", None) == [
        "cp-01: PoweringOn"
    ]


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
    from nodetools_runtime.oem.hgx.lenovo import Lenovo

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


def test_power_smc_status_is_normalized():
    from nodetools_runtime.oem.hgx.smc import Smc

    data = {"/redfish/v1/Systems/1": {"PowerState": "On"}}
    device = _device(redfish=FakeRedfish(data), odm=Smc())
    assert power.collect(device, "status", None) == ["cp-01: On"]


def test_power_nvsw_reseat_defaults_to_status():
    data = {"/redfish/v1/Systems/System_0": {"PowerState": "On"}}
    rf = FakeRedfish(data)
    device = _device("nvswitch", redfish=rf)
    assert power.collect(device, "reseat", None) == ['cp-01: "On"']
    assert rf.posts == {}


def test_power_nvsw_compal_uses_self_and_full_power_cycle():
    from nodetools_runtime.oem.hgx.compal import Compal

    rf = FakeRedfish(post={"/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset": (200, {})})
    device = _device("nvswitch", redfish=rf, odm=Compal())
    lines = power.collect(device, "reseat", None)
    assert lines == ["cp-01: {}"]
    assert rf.posts["/redfish/v1/Systems/Self/Actions/ComputerSystem.Reset"] == {
        "ResetType": "FullPowerCycle"
    }


def test_power_dgx_reseat_uses_aux_power_cycle_force():
    from nodetools_runtime.oem.hgx.dgx import Dgx

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
    from nodetools_runtime.oem.hgx.dell import Dell

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
    from nodetools_runtime.oem.hgx.lenovo import Lenovo

    data = _firmware_members()
    data["/redfish/v1/UpdateService/FirmwareInventory/BIOS"]["Version"] = "2.0 Pending"
    lines = inventory.collect(
        _device(redfish=FakeRedfish(data), odm=Lenovo()), None, None
    )
    assert lines == ["cp-01: BMC 1.0"]


def test_inventory_ami_name_style():
    from nodetools_runtime.oem.hgx.ami import Ami

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


def test_boot_compute_cdrom():
    ipmi = FakeIpmi()
    ipmi._sel = "Boot device set to CD-ROM"
    boot.collect(_device(ipmi=ipmi), "cdrom", None)
    assert ("run", "chassis", "bootdev", "cdrom") in ipmi.calls


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
    from nodetools_runtime.oem.hgx.ami import Ami

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
    from nodetools_runtime.oem.hgx.dell import Dell

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
    from nodetools_runtime.oem.hgx.lenovo import Lenovo

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
def test_aivres_power_status_is_normalized():
    from nodetools_runtime.oem.hgx.aivres import Aivres

    data = {"/redfish/v1/Systems/1": {"PowerState": "On"}}
    device = _device(redfish=FakeRedfish(data), odm=Aivres())
    assert power.collect(device, "status", None) == ["cp-01: On"]


def test_aivres_power_reset_posts_system_1():
    from nodetools_runtime.oem.hgx.aivres import Aivres

    rf = FakeRedfish()
    lines = power.collect(_device(redfish=rf, odm=Aivres()), "reset", None)
    assert lines == ["cp-01: {}"]
    assert rf.posts["/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"] == {
        "ResetType": "ForceRestart"
    }


def test_aivres_bootorder_sorts_by_number():
    from nodetools_runtime.oem.hgx.aivres import Aivres

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
    from nodetools_runtime.oem.hgx.aivres import Aivres

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
    from nodetools_runtime.oem.hgx.compal import Compal

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
    from nodetools_runtime.oem.hgx.compal import Compal

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
    from nodetools_runtime.oem.hgx.compal import Compal

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
    from nodetools_runtime.oem.hgx.netweb import Netweb

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
    from nodetools_runtime.oem.hgx.netweb import Netweb

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
    from nodetools_runtime.oem.hgx.netweb import Netweb

    rf = FakeRedfish()
    bios.collect(_device(redfish=rf, odm=Netweb()), "base", None)
    assert "/redfish/v1/Systems/System_0/Bios/SD" in rf.patches


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
    vm.collect(_device(redfish=rf), "mount", Opts(image="smb://h/share/y.iso", vm_user="test-user", vm_password="test-pass"))
    assert rf.posts[path] == {
        "Image": "smb://h/share/y.iso",
        "UserName": "test-user",
        "Password": "test-pass",
        "TransferMethod": "Stream",
        "TransferProtocolType": "CIFS",
        "WriteProtected": True,
    }


def test_vm_base_no_interface_message():
    rf = FakeRedfish(data={"/redfish/v1/Managers": {"Members": []}})
    from nodetools_runtime.errors import CapUnsupported

    with pytest.raises(CapUnsupported, match="No Virtual Media Interface found"):
        vm.collect(_device(redfish=rf), "mount", Opts(image="smb://h/share/x.iso"))


def test_vm_compal_status_single_line():
    from nodetools_runtime.oem.hgx.compal import Compal

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
    from nodetools_runtime.oem.hgx.compal import Compal

    rf = FakeRedfish(data=_ami_vm_data("BMC"))
    path = "/redfish/v1/Managers/BMC/VirtualMedia/CD1/Actions/VirtualMedia.InsertMedia"
    vm.collect(_device(redfish=rf, odm=Compal()), "insert", Opts(image="smb://h/y.iso"))
    assert rf.posts[path]["Image"] == "smb://h/y.iso"


def test_vm_netweb_enable_then_status():
    from nodetools_runtime.oem.hgx.netweb import Netweb

    rf = FakeRedfish(data=_ami_vm_data("BMC_0"))
    lines = vm.collect(_device(redfish=rf, odm=Netweb()), "enable", None)
    assert "/redfish/v1/Managers/BMC_0/Actions/Oem/AMIVirtualMedia.EnableRMedia" in rf.posts
    assert lines[-1] == "cp-01: Enabled"


def test_vm_aivres_mount_parses_nfs_uri():
    from nodetools_runtime.oem.hgx.aivres import Aivres

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
    assert list(rf.posts) == [vmm + "/RemoteMount", vmm + "/RemoteMediaInsert"]
    assert rf.posts[vmm + "/RemoteMediaInsert"] == {"ImagePath": "NFS/z.iso", "FileType": "File"}
    assert 'cp-01:   "Protocol": "NFS"' in lines


def test_vm_smc_mount_http_payload():
    from nodetools_runtime.oem.hgx.smc import Smc

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
    from nodetools_runtime.oem.hgx.dell import Dell

    class FakeDell(Dell):
        def __init__(self):
            self.calls = []

        def _racadm(self, device, *args):
            self.calls.append(args)
            return "Remote Image is now Configured" if args[0] == "remoteimage" else "Enabled"

    odm = FakeDell()
    image = "https://images.example.test/site/custom.iso"
    lines = vm.collect(_device(redfish=FakeRedfish(), odm=odm), "mount", Opts(image=image))
    assert odm.calls == [
        ("set", "iDRAC.VirtualMedia.Enable", "1"),
        ("remoteimage", "-c", "-l", image),
    ]
    assert lines == ["cp-01: Enabled", "cp-01: Remote Image is now Configured"]
    vm.collect(_device(redfish=FakeRedfish(), odm=odm), "eject", None)
    assert odm.calls[-1] == ("remoteimage", "-d")


@pytest.mark.parametrize("action", ["mount", "insert", "stop"])
def test_vm_rejects_missing_image_before_inventory(action, capsys):
    from nodetools_runtime import cli

    assert cli.main(["nodevm", "-i", "/nonexistent-inventory", "cp-01", action]) == 1
    assert "requires an explicit image URL" in capsys.readouterr().err


def test_vm_netweb_mount_uses_only_explicit_smb_address():
    from nodetools_runtime.oem.hgx.netweb import Netweb

    rf = FakeRedfish(data=_ami_vm_data("BMC_0"))
    image = "smb://images.example.test/custom/unique.iso"
    vm.collect(_device(redfish=rf, odm=Netweb()), "mount", Opts(image=image))
    enable, insert = rf.posts
    assert enable.endswith("AMIVirtualMedia.EnableRMedia")
    assert insert.endswith("VirtualMedia.InsertMedia")
    payload = rf.posts[insert]
    assert payload["Image"] == image
    assert "UserName" not in payload and "Password" not in payload


def test_vm_netweb_stop_targets_only_matching_image():
    from nodetools_runtime.errors import NodetoolsError
    from nodetools_runtime.oem.hgx.netweb import Netweb

    virtual = "/redfish/v1/Managers/BMC_0/VirtualMedia"
    data = _ami_vm_data("BMC_0")
    image = "smb://images.example.test/custom/unique.iso"
    data[virtual]["Members"].append({"@odata.id": virtual + "/CD2"})
    data[virtual + "/CD1"].update(Image=image)
    data[virtual + "/CD2"] = {"Image": "smb://other.example.test/custom/unique.iso"}
    rf = FakeRedfish(data=data)
    device = _device(redfish=rf, odm=Netweb())
    with pytest.raises(NodetoolsError, match="no mounted Netweb image"):
        vm.collect(device, "stop", Opts(image="smb://wrong.example.test/custom/unique.iso"))
    assert not rf.posts
    vm.collect(device, "stop", Opts(image=image))
    assert list(rf.posts) == [virtual + "/CD1/Actions/VirtualMedia.EjectMedia"]


def test_vm_compal_stop_uses_requested_filename(monkeypatch):
    import types
    import requests

    from nodetools_runtime.oem.hgx.compal import Compal

    calls = []

    class Session:
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return types.SimpleNamespace(
                json=lambda: {"CSRFToken": "test-token"},
                text="stopped",
                raise_for_status=lambda: None,
            )

        def close(self):
            pass

    monkeypatch.setattr(requests, "Session", Session)
    device = _device(redfish=FakeRedfish(), odm=Compal())
    vm.collect(device, "stop", Opts(image="smb://images.example.test/share/custom%20boot.iso"))
    url, request = calls[-1]
    assert url.endswith("/stop-media")
    assert request["json"]["image_name"] == "custom boot.iso"


@pytest.mark.parametrize("action", ["mount", "insert"])
def test_vm_failed_enable_does_not_insert(action):
    from nodetools_runtime.errors import TransportError
    from nodetools_runtime.oem.hgx.compal import Compal

    class RefusedRedfish(FakeRedfish):
        def post(self, path, body):
            self.posts[path] = body
            return 503, {"error": {}}

    rf = RefusedRedfish(data=_ami_vm_data("BMC"))
    with pytest.raises(TransportError, match="HTTP 503"):
        vm.collect(_device(redfish=rf, odm=Compal()), action, Opts(image="smb://h/share/x.iso"))
    assert len(rf.posts) == 1
    assert next(iter(rf.posts)).endswith("AMIVirtualMedia.EnableRMedia")


def test_shared_redfish_reads_the_selected_infrastructure_resources():
    from nodetools_runtime.errors import CapUnsupported

    switch = "/redfish/v1/Systems/Fabric2"
    shelf = "/redfish/v1/Chassis/Shelf3"
    manager = "/redfish/v1/Managers/PowerControl"
    services = manager + "/LogServices"
    log = services + "/Events"
    rf = FakeRedfish({
        "/redfish/v1/Systems": {"Members": [
            {"@odata.id": "/redfish/v1/Systems/Compute1"}, {"@odata.id": switch},
        ]},
        switch: {"PowerState": "On"},
        shelf: {"SerialNumber": "S3", "PowerState": "On"},
        manager: {"LogServices": {"@odata.id": services}},
        services: {"Members": [{"@odata.id": log}]},
        log: {"Entries": {"@odata.id": log + "/Entries"}},
        log + "/Entries": {"Members": [{"Created": "now", "Severity": "OK", "Message": "test"}]},
    })
    sw = model.Device(Host("sw", groups=["nvswitch"]), lambda _: rf)
    sw.odm = oem.for_device(sw)
    with pytest.raises(CapUnsupported, match="system_id"):
        power.collect(sw, "status", None)
    sw.vars["system_id"] = "Fabric2"
    assert power.collect(sw, "status", None) == ["sw: On"]
    with pytest.raises(CapUnsupported, match="no verified"):
        power.collect(sw, "cycle", None)

    ps = model.Device(Host("shelf", groups=["powershelf"], variables={
        "chassis_id": "Shelf3", "manager_id": "PowerControl", "oem": "liteon",
    }), lambda _: rf)
    ps.odm = oem.for_device(ps)
    assert power.collect(ps, "status", None) == ["shelf: S3,On"]
    assert eventlog.collect(ps, "status", None) == ["shelf: now OK test"]
    with pytest.raises(CapUnsupported, match="uses status"):
        eventlog.collect(ps, "bmc", None)
    for device in (sw, ps):
        with pytest.raises(CapUnsupported, match="only available for compute trays"):
            eventlog.collect(device, "hmc", None)
    with pytest.raises(CapUnsupported, match="no verified"):
        eventlog.collect(ps, "clear", None)
    assert not rf.posts

    target = log + "/Actions/LogService.ClearLog"
    rf.data[log]["Actions"] = {"#LogService.ClearLog": {"target": target}}
    calls = []

    def clear(path, body):
        calls.append((path, body))
        return 204, None

    rf.post = clear
    assert eventlog.collect(ps, "clear", None) == ["shelf: Event log cleared (HTTP 204)"]
    assert calls == [(target, {})]

    rf.data[log]["Actions"]["#LogService.ClearLog"]["target"] = "https://other.example" + target
    with pytest.raises(CapUnsupported, match="no verified"):
        eventlog.collect(ps, "clear", None)
    assert calls == [(target, {})]


def test_infrastructure_firmware_follows_redfish_service_links():
    update = "/redfish/v1/UpdateService"
    firmware = update + "/FirmwareInventory"
    path = firmware + "/Controller"
    rf = FakeRedfish({
        "/redfish/v1": {"UpdateService": {"@odata.id": update}},
        update: {"FirmwareInventory": {"@odata.id": firmware}},
        firmware: {"Members": [{"@odata.id": path}]},
        path: {"Id": "Controller", "Version": "1.2"},
    })
    device = model.Device(Host("shelf", groups=["powershelf"]), lambda _: rf)
    device.odm = oem.for_device(device)
    assert inventory.collect(device, None, None) == ["shelf: Controller  1.2"]


@pytest.mark.parametrize("maker, product, ident", [
    ("Dell", "HGX B300", "System.Embedded.1"),
    ("Lenovo", "GB300 NVL72", "System_0"),
    ("HPE", "GB300 NVL72", "System_0"),
    ("NVIDIA", "GB200 NVL72", "System_0"),
])
def test_documented_redfish_power_state_routes(maker, product, ident):
    system = "/redfish/v1/Systems/" + ident
    device = _device(redfish=FakeRedfish({system: {"PowerState": "Off"}}))
    device.identity = {"manufacturer": maker, "model": product}
    device.odm = oem.for_device(device)
    assert power.collect(device, None, None) == ["cp-01: Off"]
    assert not device.ipmi.calls


def test_generic_nvl_reads_boot_bios_and_macs():
    system = "/redfish/v1/Systems/OEM-Tray"
    rf = FakeRedfish({
        "/redfish/v1/Systems": {"Members": [{"@odata.id": system}]},
        system: {
            "Boot": {"BootOrder": ["Boot0002", "Boot0001"]},
            "Bios": {"@odata.id": system + "/Bios"},
            "EthernetInterfaces": {"@odata.id": system + "/EthernetInterfaces"},
        },
        system + "/Bios": {
            "Attributes": {"Mode": "Default"},
            "@Redfish.Settings": {"SettingsObject": {"@odata.id": system + "/Bios/Pending"}},
        },
        system + "/Bios/Pending": {"Attributes": {"Mode": "Custom"}},
        system + "/EthernetInterfaces": {
            "Members": [{"Id": "eth0", "MACAddress": "00:11:22:33:44:55"}],
        },
    })
    device = _device(redfish=rf)
    device.identity = {"manufacturer": "GIGABYTE", "model": "GB300 NVL72"}
    device.odm = oem.for_device(device)
    assert bios.collect(device, None, None) == ['cp-01: Mode="Default"']
    assert bios.collect(device, "pending", None) == ['cp-01: Mode="Custom"']
    assert bootorder.collect(device, None, None) == ["cp-01: Boot0002", "cp-01: Boot0001"]
    assert macs.collect(device, None, None) == ["cp-01: eth0,00:11:22:33:44:55"]
    assert not rf.posts and not rf.patches


def test_inventory_views_from_cli(monkeypatch, tmp_path, capsys):
    from nodetools_runtime import cli, runtime

    path = tmp_path / "inventory.ini"
    path.write_text("[compute]\ncp-01 host=192.0.2.1 system_id=Tray\n")
    system = "/redfish/v1/Systems/Tray"
    rf = FakeRedfish({
        system: {"SerialNumber": "TRAY-123"},
        system + "/Processors": {
            "Members": [{"Id": "GPU0", "ProcessorType": "GPU", "SerialNumber": "GPU-1"}],
        },
        "/redfish/v1/TaskService/Tasks": {
            "Members": [{"Id": "9", "TaskState": "Completed", "TaskStatus": "OK"}],
        },
    })
    monkeypatch.setattr(runtime, "build_device", lambda host, options: model.Device(host, lambda _: rf))
    for option, expected in (
        ("--details", "serial=TRAY-123"), ("--gpu", "GPU0 serial=GPU-1"),
        ("--tasks", "9 Completed OK"),
    ):
        assert cli.main(["nodeinventory", "-i", str(path), option]) == 0
        assert expected in capsys.readouterr().out


def test_smc_netboot_status_is_read_only_and_pxe_stages_without_reset():
    from nodetools_runtime.oem.hgx.smc import Smc

    path = "/redfish/v1/Systems/1/Oem/Supermicro/FixedBootOrder"
    rf = FakeRedfish({path: {"FixedBootOrder": ["UEFI Hard Disk", "UEFI Network"]}})
    device = _device(redfish=rf, odm=Smc())
    assert netboot.collect(device, None, None) == ["cp-01: UEFI Hard Disk", "cp-01: UEFI Network"]
    assert not rf.posts and not rf.patches
    assert "staged" in netboot.collect(device, "pxe", None)[0]
    assert rf.patches[path] == {"FixedBootOrder": ["UEFI Network", "UEFI Hard Disk"]}
    assert not rf.posts


def test_smc_fan_and_power_write_use_advertised_values():
    from nodetools_runtime.oem.hgx.smc import Smc

    system = "/redfish/v1/Systems/1"
    info = system + "/ResetActionInfo"
    target = system + "/Actions/ComputerSystem.Reset"
    fan = "/redfish/v1/Managers/1/Oem/Supermicro/FanMode"
    rf = FakeRedfish({
        system: {"Actions": {"#ComputerSystem.Reset": {"target": target, "@Redfish.ActionInfo": info}}},
        info: {"Parameters": [{"Name": "ResetType", "AllowableValues": ["PowerCycle"]}]},
        fan: {"Mode@Redfish.AllowableValues": ["FullSpeed", "Optimal"]},
    })
    device = _device(redfish=rf, odm=Smc())
    assert device.odm.fan_control(device, "auto") == "ok"
    assert rf.patches[fan] == {"Mode": "Optimal"}
    assert power.collect(device, "cycle", None) == ["cp-01: Accepted PowerCycle"]
    assert rf.posts[target] == {"ResetType": "PowerCycle"}


@pytest.mark.parametrize("maker, product, attr, current, changes, suffix", [
    ("Supermicro", "SYS-822GS-NB3RT", "QuietBoot=false", {"QuietBoot": True}, {"QuietBoot": False}, "/SD"),
    ("KAYTUS", "KR9288-X3-A0-F0-01", "Cpu.X=Disabled", {"Cpu": {"X": "Enabled"}}, {"Cpu": {"X": "Disabled"}}, "/Settings"),
])
def test_hgx_b300_bios_settings_are_staged(maker, product, attr, current, changes, suffix):
    from types import SimpleNamespace

    path = "/redfish/v1/Systems/1/Bios"
    rf = FakeRedfish({
        path: {
            "Attributes": current,
            "@Redfish.Settings": {"SettingsObject": {"@odata.id": path + suffix}},
        },
        path + suffix: {"Attributes": {}},
    })
    device = _device(redfish=rf)
    device.identity = {"model": product, "manufacturer": maker}
    device.odm = oem.for_device(device)
    assert model.generation_for(device) == "b300"
    assert "staged" in bios.collect(device, None, SimpleNamespace(bios_updates=[attr]))[0]
    assert rf.patches == {path + suffix: {"Attributes": changes}}
    assert not rf.posts and not device.ipmi.calls


def test_smc_psu_scopes_to_primary_chassis_and_merges_same_serial():
    from nodetools_runtime.caps import psu
    from nodetools_runtime.oem.hgx.smc import Smc

    path = "/redfish/v1/Chassis/1"
    collection = path + "/PowerSubsystem/PowerSupplies"
    rf = FakeRedfish({
        collection: {"Members": [{"@odata.id": collection + "/1"}]},
        collection + "/1": {"Id": "1", "SerialNumber": "P1"},
        path + "/Power": {"PowerSupplies": [{"MemberId": "0", "SerialNumber": "P1", "PowerInputWatts": 15}]},
    })
    rows = psu.collect(_device(redfish=rf, odm=Smc()))
    assert len(rows) == 1
    assert (rows[0]["id"], rows[0]["input_watts"]) == ("1", 15)


def test_kr9288_host_mac_from_network_device_function():
    from nodetools_runtime.oem.hgx.kaytus import KaytusKr9288

    adapters = "/redfish/v1/Chassis/1/NetworkAdapters"
    card = adapters + "/card"
    functions = card + "/NetworkDeviceFunctions"
    function = functions + "/eth0"
    rf = FakeRedfish({
        adapters: {"Members": [{"@odata.id": card, "Slot": "slot 1"}]},
        "/redfish/v1/Managers/1/EthernetInterfaces": {"Members": []},
        card: {"Oem": {"Public": {"CardModel": "CX8", "NetworkPorts": []}}},
        functions: {"Members": [{"@odata.id": function}]},
        function: {"Id": "eth0", "Ethernet": {"PermanentMACAddress": "00:11:22:33:44:55"}},
    })
    rows = macs.collect(_device(redfish=rf, odm=KaytusKr9288()), None, None)
    assert len(rows) == 1 and "00:11:22:33:44:55" in rows[0]


def test_kr9288_stages_one_time_pxe_without_reset():
    from nodetools_runtime.oem.hgx.kaytus import KaytusKr9288

    system = "/redfish/v1/Systems/1"
    rf = FakeRedfish({system: {"Boot": {"BootSourceOverrideTarget@Redfish.AllowableValues": ["Pxe"]}}})
    device = _device(redfish=rf, odm=KaytusKr9288())
    assert "One-time" in netboot.collect(device, "pxe", None)[0]
    assert rf.patches[system] == {"Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": "Pxe"}}
    assert not rf.posts


def test_smc_media_eject_falls_back_to_detach_on_missing_action():
    from nodetools_runtime.oem.hgx.smc import Smc

    virtual = "/redfish/v1/Managers/1/VirtualMedia"
    slot = virtual + "/VirtualMedia1"
    action = slot + "/Actions/VirtualMedia.EjectMedia"
    rf = FakeRedfish({virtual: {"Members": [{"@odata.id": slot}]},
                      slot: {"Image": "http://example.test/test.iso", "Inserted": False}},
                     post={action: (404, {})})
    device = _device(redfish=rf, odm=Smc())
    assert "Detached" in vm.collect(device, "eject", None)[0]
    assert rf.patches == {slot: {"Inserted": False}}
    assert "inserted=false" in vm.collect(device, None, None)[0]


def test_smc_media_stop_only_detaches_the_matching_url():
    from types import SimpleNamespace

    from nodetools_runtime.errors import CapUnsupported
    from nodetools_runtime.oem.hgx.smc import Smc

    collection = "/redfish/v1/Managers/1/VirtualMedia"
    slot = collection + "/VirtualMedia1"
    image = "http://example.test/test.iso"
    rf = FakeRedfish({collection: {"Members": [{"@odata.id": slot}]},
                      slot: {"Image": image, "Inserted": False}})
    device = _device(redfish=rf, odm=Smc())
    with pytest.raises(CapUnsupported, match="matches"):
        vm.collect(device, "stop", SimpleNamespace(image=image + "x"))
    assert not rf.patches
    assert "Detached" in vm.collect(device, "stop", SimpleNamespace(image=image))[0]
    assert rf.patches == {slot: {"Inserted": False}}


@pytest.mark.parametrize("state, inserted, error", [
    ("Completed", True, None),
    ("Completed", False, "did not report inserted"),
    ("Exception", False, "File Not Found"),
])
def test_smc_mount_checks_async_task_and_inserted_state(state, inserted, error):
    from types import SimpleNamespace

    from nodetools_runtime.errors import TransportError
    from nodetools_runtime.oem.hgx.smc import Smc

    slot = "/redfish/v1/Managers/1/VirtualMedia/VirtualMedia1"
    action = slot + "/Actions/VirtualMedia.InsertMedia"
    task = "/redfish/v1/TaskService/Tasks/4"
    rf = FakeRedfish(
        data={task: {"TaskState": state, "Messages": [{"Message": "File Not Found"}]},
              slot: {"Inserted": inserted}},
        post={action: (202, {"@odata.id": task})},
    )
    device = _device(redfish=rf, odm=Smc())
    options = SimpleNamespace(image="http://example.test/test.iso")
    if error:
        with pytest.raises(TransportError, match=error):
            vm.collect(device, "mount", options)
    else:
        assert vm.collect(device, "mount", options) == ["cp-01: Media inserted"]


def test_liteon_psu_inventory_uses_hardware_ids_when_software_records_lack_versions():
    from nodetools_runtime.subjects import inventory

    firmware = inventory.FIRMWARE
    missing = firmware + "/FW_PSU_6"
    manager = firmware + "/FW_PMC"
    supplies = "/redfish/v1/Chassis/powershelf/PowerSubsystem/PowerSupplies"
    module = supplies + "/5"
    rf = FakeRedfish({
        firmware: {"Members": [{"@odata.id": missing}, {"@odata.id": manager}]},
        missing: {"Id": "FW_PSU_6", "Version": "null"},
        manager: {"Id": "FW_PMC", "Version": "3.2.9"},
        supplies: {"Members": [{"@odata.id": module}]},
        module: {"Id": "5", "FirmwareVersion": "0x1 / 0x1"},
    })
    host = Host("shelf", groups=["powershelf"], variables={"oem": "liteon"})
    device = model.Device(host, lambda _: rf)

    traced = []
    rf.debug_label = host.name
    rf.trace_source = lambda *args: traced.append(args)
    rows = inventory._firmware(device)
    assert traced == [("FW_PMC", manager, "Version"), ("PSU_5", module, "FirmwareVersion")]
    assert [line.split() for line in rows] == [
        ["shelf:", "FW_PMC", "3.2.9"], ["shelf:", "PSU_5", "0x1", "/", "0x1"],
    ]
    del rf.data[supplies]
    assert inventory._firmware(device)[0].split() == ["shelf:", "FW_PSU_6", "N/A"]
