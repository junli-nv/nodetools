from nodetools_runtime import model
from nodetools_runtime.caps import common, fan, inlet, psu, temp
from nodetools_runtime.inventory import Host


class FakeRedfish:
    def __init__(self, data):
        self.data = data

    def get(self, path, timeout=None):
        return self.data.get(path)


def _device(data, role="compute", chassis=("Chassis_0",)):
    host = Host("cp-01", "10.0.0.1", {}, [role])
    device = model.Device(host, redfish_factory=lambda h: FakeRedfish(data))
    device.probe = {"chassis": list(chassis)}
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
    readings = psu.collect(_device(data, role="powershelf", chassis=("PowerShelf_0",)))
    assert readings[0]["value"] == "in=1200W"
    assert readings[0]["health"] == "OK"


def test_avr_legacy_psu_power_and_details():
    from nodetools_runtime.report import render

    detail = {
        "MemberId": "0",
        "Name": "PSU_0",
        "Manufacturer": "Great Wall",
        "Model": "CRPS3200TW",
        "FirmwareVersion": "00.01.02",
        "Status": {"Health": "OK"},
        "PowerInputWatts": 0,
        "LastPowerOutputWatts": 303.0,
    }
    data = {"/redfish/v1/Chassis/1/Power": {"PowerSupplies": [detail]}}
    device = _device(data, chassis=("1",))
    readings = psu.collect(device)
    device.profile = {"caps": {"1": {"Power": True, "PowerSupplies": 0}}}
    assert psu.collect(device) == readings
    result = {"name": "cp-01", "cap": "psu", "readings": readings}
    assert render([result]) == ["cp-01: 0,OK,in=0W/out=303.0W"]
    assert render([result], details=True) == [
        "cp-01: PSU_0,Great Wall,CRPS3200TW,00.01.02,OK,0,303.0"
    ]


def test_psu_metrics_link_power():
    base = "/redfish/v1/Chassis/PowerShelf_0/PowerSubsystem/PowerSupplies"
    data = {
        base: {"Members": [{"@odata.id": base + "/0"}]},
        base + "/0": {
            "MemberId": "0",
            "Name": "PSU 0",
            "Status": {"Health": "OK"},
            "Metrics": {"@odata.id": base + "/0/Metrics"},
        },
        base + "/0/Metrics": {
            "InputPowerWatts": {"Reading": 2628},
            "OutputPowerWatts": {"Reading": 2524},
        },
    }
    readings = psu.collect(_device(data, role="powershelf", chassis=("PowerShelf_0",)))
    assert readings[0]["value"] == "in=2628W/out=2524W"
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
    readings = psu.collect(_device(data, role="powershelf", chassis=("PowerShelf_0",)))
    assert readings[0]["health"] == "On"


def test_enumerate_sensors_skips_when_not_expandable():
    data = {
        "/redfish/v1/Chassis/Chassis_0/Sensors": {
            "Members": [{"@odata.id": "/redfish/v1/Chassis/Chassis_0/Sensors/S1"}]
        },
        "/redfish/v1/Chassis/Chassis_0/Sensors/S1": {"Name": "Temp", "Reading": 1},
    }
    assert common.enumerate_sensors(FakeRedfish(data), "Chassis_0") == []


def test_enumerate_sensors_uses_expand():
    data = {
        "/redfish/v1/Chassis/Chassis_0/Sensors?$expand=.": {
            "Members": [{"Id": "S1", "Name": "Temp", "Reading": 42}]
        }
    }
    out = common.enumerate_sensors(FakeRedfish(data), "Chassis_0")
    assert out and out[0]["Reading"] == 42


def test_scan_cache_roundtrip(tmp_path, monkeypatch):
    from nodetools_runtime import scan

    monkeypatch.setattr(scan, "SCAN_CACHE_DIR", str(tmp_path))
    data = {
        "/redfish/v1": {"Vendor": "NVIDIA"},
        "/redfish/v1/Managers": {
            "Members": [{"@odata.id": "/redfish/v1/Managers/BMC_0"}]
        },
        "/redfish/v1/Managers/BMC_0": {"Model": "GB200"},
        "/redfish/v1/Systems": {"Members": []},
    }
    device = _device(data)
    key = scan.key_for(scan.read_identity(device.redfish))
    assert key == "nvidia__gb200"
    profile = {
        "version": scan.SCAN_CACHE_VERSION,
        "key": key,
        "caps": {"Chassis_0": {"Fans": 16}},
    }
    scan.store(profile)
    assert scan.load(device)["caps"]["Chassis_0"]["Fans"] == 16
    assert scan.clear(device) is True
    assert scan.load(device) is None


def test_scan_load_ignores_wrong_version(tmp_path, monkeypatch):
    import json

    from nodetools_runtime import scan

    monkeypatch.setattr(scan, "SCAN_CACHE_DIR", str(tmp_path))
    with open(scan.cache_path("nvidia__gb200"), "w") as handle:
        json.dump({"version": 999, "key": "nvidia__gb200"}, handle)
    device = _device(
        {
            "/redfish/v1": {"Vendor": "NVIDIA"},
            "/redfish/v1/Managers": {
                "Members": [{"@odata.id": "/redfish/v1/Managers/BMC_0"}]
            },
            "/redfish/v1/Managers/BMC_0": {"Model": "GB200"},
            "/redfish/v1/Systems": {"Members": []},
        }
    )
    assert scan.load(device) is None


def test_scan_unidentified_machine_is_not_cached(tmp_path, monkeypatch):
    import os

    from nodetools_runtime import scan

    monkeypatch.setattr(scan, "SCAN_CACHE_DIR", str(tmp_path))
    device = _device({})
    profile = scan.refresh(device)
    assert profile["key"] is None
    assert os.listdir(str(tmp_path)) == []
    assert scan.load(device) is None
    assert scan.clear(device) is False


def test_scan_refresh_records_caps(tmp_path, monkeypatch):
    from nodetools_runtime import scan

    monkeypatch.setattr(scan, "SCAN_CACHE_DIR", str(tmp_path))
    base = "/redfish/v1/Chassis/Chassis_0"
    data = {
        "/redfish/v1": {"Vendor": "NVIDIA"},
        "/redfish/v1/Managers": {
            "Members": [{"@odata.id": "/redfish/v1/Managers/BMC_0"}]
        },
        "/redfish/v1/Managers/BMC_0": {"Model": "GB200"},
        "/redfish/v1/Systems": {"Members": []},
        "/redfish/v1/Chassis": {
            "Members": [{"@odata.id": base}]
        },
        base + "/Sensors": {"Members@odata.count": 5},
        base + "/ThermalSubsystem/Fans": {"Members@odata.count": 16},
        base + "/ThermalSubsystem/ThermalMetrics": {
            "TemperatureReadingsCelsius": [{"Reading": 1}, {"Reading": 2}]
        },
    }
    device = _device(data, chassis=("Chassis_0",))
    profile = scan.refresh(device)
    assert profile["chassis"] == ["Chassis_0"]
    assert profile["key"] == "nvidia__gb200"
    rec = profile["caps"]["Chassis_0"]
    assert rec["Fans"] == 16
    assert rec["Sensors"] == 5
    assert rec["Temperatures"] == 2
    assert rec["PowerSupplies"] == 0
    assert rec["Thermal"] is False
    assert scan.load(device)["caps"]["Chassis_0"]["Fans"] == 16


def test_rung_chassis_uses_profile():
    device = _device({})
    device.profile = {"caps": {"Chassis_0": {"Fans": 16}, "Nope": {"Fans": 0}}}
    assert common.rung_chassis(device, "Fans", ("Chassis_0", "Nope")) == ["Chassis_0"]


def test_rung_chassis_without_profile_is_live():
    device = _device({})
    assert common.rung_chassis(device, "Fans", ("Chassis_0",)) == ["Chassis_0"]


def test_fan_uses_cached_profile():
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
    device = _device(data, chassis=("Chassis_0", "Chassis_9"))
    device.profile = {
        "caps": {"Chassis_0": {"Fans": 16}, "Chassis_9": {"Fans": 0}}
    }
    readings = fan.collect(device)
    assert readings[0]["id"] == "Fan_1"



def test_inlet_keeps_front_io_only():
    base = "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/ThermalMetrics"
    data = {
        base: {
            "TemperatureReadingsCelsius": [
                {"@odata.id": base + "/Chassis_0_Front_IO_Temp_0", "Reading": 24.5},
                {"@odata.id": base + "/HGX_ProcessorModule_0_Inlet_Temp_0", "Reading": 33.0},
                {"@odata.id": base + "/PDB_0_Inlet_Temp_0", "Reading": 34.0},
            ]
        }
    }
    readings = inlet.collect(_device(data))
    assert [r["id"] for r in readings] == ["Chassis_0_Front_IO_Temp_0"]
    assert readings[0]["health"] == "OK"


def test_inlet_flags_high_and_low():
    base = "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/ThermalMetrics"
    data = {
        base: {
            "TemperatureReadingsCelsius": [
                {"@odata.id": base + "/hot", "Name": "Inlet_Temp", "Reading": 45.0},
                {"@odata.id": base + "/cold", "Name": "Ambient_Temp", "Reading": 8.0},
            ]
        }
    }
    verdicts = {r["id"]: r["health"] for r in inlet.collect(_device(data))}
    assert verdicts == {"hot": "HIGH", "cold": "LOW"}


def test_inlet_target_override():
    from types import SimpleNamespace

    base = "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/ThermalMetrics"
    data = {
        base: {
            "TemperatureReadingsCelsius": [
                {"@odata.id": base + "/INLET", "Reading": 30.0}
            ]
        }
    }
    device = _device(data)
    device.options = SimpleNamespace(inlet_target=30.0, inlet_tolerance=1.0)
    assert inlet.collect(device)[0]["health"] == "OK"


def test_inlet_reads_a_single_chassis():
    base = "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/ThermalMetrics"
    data = {
        base: {
            "TemperatureReadingsCelsius": [
                {"@odata.id": base + "/Chassis_0_Front_IO_Temp_0", "Reading": 23.0}
            ]
        }
    }

    class Counting(FakeRedfish):
        def __init__(self, data):
            super().__init__(data)
            self.paths = []

        def get(self, path, timeout=None):
            self.paths.append(path)
            return super().get(path)

    redfish = Counting(data)
    host = Host("cp-01", "10.0.0.1", {}, ["compute"])
    device = model.Device(host, redfish_factory=lambda h: redfish)
    device.probe = {"chassis": ["Chassis_0", "Chassis_9"]}
    inlet.collect(device)
    assert redfish.paths == [base]


def test_inlet_probes_chassis_until_found():
    d0 = "/redfish/v1/Chassis/Chassis_0/ThermalSubsystem/ThermalMetrics"
    d9 = "/redfish/v1/Chassis/Chassis_9/ThermalSubsystem/ThermalMetrics"
    data = {
        d0: {
            "TemperatureReadingsCelsius": [
                {"@odata.id": d0 + "/HGX_GPU_0_Temp_0", "Reading": 40.0}
            ]
        },
        d9: {
            "TemperatureReadingsCelsius": [
                {"@odata.id": d9 + "/Inlet_Temp", "Reading": 22.0}
            ]
        },
    }
    readings = inlet.collect(_device(data, chassis=("Chassis_0", "Chassis_9")))
    assert [r["id"] for r in readings] == ["Inlet_Temp"]
    assert readings[0]["health"] == "OK"


def test_aivres_fan_uses_canonical_legacy_chassis():
    from nodetools_runtime.oem.hgx.aivres import Aivres

    fan_row = {"MemberId": "0", "Name": "Fan 0", "Reading": 0, "Status": {"Health": "OK"}}
    data = {
        "/redfish/v1/Chassis/1/Thermal": {"Fans": [fan_row]},
        "/redfish/v1/Chassis/Chassis_0/Thermal": {"Fans": [{**fan_row, "Reading": 1200}]},
    }
    device = _device(data, chassis=("1", "Chassis_0"))
    device.odm = Aivres()
    assert [(row["id"], row["value"]) for row in fan.collect(device)] == [("0", 0)]


def test_nvswitch_sensors_use_nvos_without_a_bmc_address(monkeypatch):
    from types import SimpleNamespace

    from nodetools_runtime import runtime
    from nodetools_runtime.caps import nvos
    from nodetools_runtime.inventory import Host

    host = Host("switch-01", groups=["nvswitch"])
    options = SimpleNamespace(action=None, timeout=5, node_timeout=10)
    replies = {
        "nv show platform environment temperature -o json": (0, '{"ASIC1":{"current":"50.00","state":"ok"}}', ""),
        "nv show platform environment psu -o json": (2, "", "'psu' is not one of ['temperature']"),
        "nv show platform environment fan -o json": (2, "", "'fan' is not one of ['temperature']"),
        nvos._HSC: (0, "49932\n3873\n191651969\n0\n0\n0\n0\n0\n0\n0\n", ""),
    }

    def query(host, options, command, deadline):
        code, stdout, stderr = replies[command]
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(nvos, "_ssh", query)
    temp = runtime.Runner("temp", options)._one(host)
    assert temp["error"] is None and temp["readings"][0]["value"] == "50.00"
    psu = runtime.Runner("psu", options)._one(host)
    reading, = psu["readings"]
    assert (reading["id"], reading["input_volts"], reading["input_amps"], reading["input_watts"]) == (
        "HSC-DC-IN", 49.932, 3.873, 191.65,
    )
    fan = runtime.Runner("fan", options)._one(host)
    assert reading["health"] == "OK"
    assert fan["error"] is None
    assert fan["notice"] == "N/A (NVOS exposes no fan sensors)"
    replies["nv show platform environment fan -o json"] = (255, "", "Permission denied")
    assert "SSH exit 255" in runtime.Runner("fan", options)._one(host)["error"]
