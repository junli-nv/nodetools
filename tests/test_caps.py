from nodetools_runtime import model
from nodetools_runtime.caps import fan, psu, temp
from nodetools_runtime.inventory import Host


class FakeRedfish:
    def __init__(self, data):
        self.data = data

    def get(self, path, timeout=None):
        return self.data.get(path)


def _device(data, role="compute"):
    host = Host("cp-01", "10.0.0.1", {}, [role])
    device = model.Device(host, redfish_factory=lambda h: FakeRedfish(data))
    device.probe = {"chassis": []}
    return device


def test_fan_from_thermal_subsystem():
    data = {
        "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/Fans": {
            "Members": [
                {"@odata.id": "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/Fans/Fan_1"}
            ]
        },
        "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/Fans/Fan_1": {
            "MemberId": "Fan_1",
            "Name": "Front Fan 1",
            "SpeedPercent": {"SpeedRPM": 8500},
            "Status": {"Health": "OK"},
        },
    }
    readings = fan.collect(_device(data))
    assert readings[0]["id"] == "Fan_1"
    assert readings[0]["value"] == 8500
    assert readings[0]["health"] == "OK"


def test_temp_from_thermal():
    data = {
        "/redfish/v1/Chassis/Chassis_0/Thermal": {
            "Temperatures": [
                {"MemberId": "0", "Name": "Inlet Temp", "ReadingCelsius": 24,
                 "Status": {"Health": "OK"}}
            ]
        }
    }
    readings = temp.collect(_device(data))
    assert readings[0]["name"] == "Inlet Temp"
    assert readings[0]["value"] == 24


def test_psu_from_power_supplies():
    data = {
        "/redfish/v1/Chassis/PowerShelf_0/PowerSubsystem/PowerSupplies": {
            "Members": [
                {"@odata.id": "/redfish/v1/Chassis/PowerShelf_0/PowerSubsystem/PowerSupplies/0"}
            ]
        },
        "/redfish/v1/Chassis/PowerShelf_0/PowerSubsystem/PowerSupplies/0": {
            "MemberId": "0",
            "Name": "PSU 0",
            "PowerMetrics": {"InputWatts": 1200},
            "Status": {"Health": "OK"},
        },
    }
    readings = psu.collect(_device(data, role="powershelf"))
    assert readings[0]["value"] == 1200
    assert readings[0]["health"] == "OK"


def test_psu_delta_state():
    data = {
        "/redfish/v1/Chassis/PowerShelf_0/PowerSubsystem/PowerSupplies": {
            "Members": [
                {"@odata.id": "/redfish/v1/Chassis/PowerShelf_0/PowerSubsystem/PowerSupplies/0"}
            ]
        },
        "/redfish/v1/Chassis/PowerShelf_0/PowerSubsystem/PowerSupplies/0": {
            "Name": "PSU 0",
            "Oem": {"deltaenergysystems": {"Power": True}},
            "Status": {"State": "Enabled"},
        },
    }
    readings = psu.collect(_device(data, role="powershelf"))
    assert readings[0]["health"] == "On"
