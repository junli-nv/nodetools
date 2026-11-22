from nodetools_runtime import model, oem
from nodetools_runtime.inventory import Host
from nodetools_runtime.subjects import eventlog, inventory, power


class FakeRedfish:
    def __init__(self, data=None, post=None):
        self.data = data or {}
        self.posts = {}
        self.post_results = post or {}

    def get(self, path, timeout=None):
        return self.data.get(path)

    def post(self, path, body, timeout=None):
        self.posts[path] = body
        return self.post_results.get(path, (200, {}))

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
