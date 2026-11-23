import csv
import io
import json
from types import SimpleNamespace

from nodetools_runtime import cli, model, oem
from nodetools_runtime.inventory import Host
from nodetools_runtime.caps import monitor, psu
from nodetools_runtime.monitor_report import MonitorCSV


def test_node_power_ignores_subsystem_power_and_keeps_zero():
    base = "/redfish/v1/Chassis/1"
    data = {
        base + "/Power": {"PowerControl": [
            {"Name": "GPU Total Power", "PowerConsumedWatts": 25},
            {"Name": "System Power Control", "PowerConsumedWatts": 0},
        ]},
    }
    device = SimpleNamespace(redfish=SimpleNamespace(get=data.get),
                             vars={"chassis_id": "1"}, odm=SimpleNamespace())
    readings = monitor._power(device)
    assert [(r["kind"], r["value"]) for r in readings] == [("node_power", 0)]


def test_monitor_psus_report_only_watts(monkeypatch):
    supplies = [
        psu._supply_reading(None, {
            "Id": slot, "Name": "PSU " + slot, "PowerMetrics": metrics, **detail,
        })
        for slot, metrics, detail in (
            ("1", {"InputPowerWatts": {"Reading": 0}, "InputCurrentAmps": {"Reading": 1.5}}, {}),
            ("2", {"InputCurrentAmps": {"Reading": 4.2}}, {}),
            ("3", {"InputCurrentAmps": {"Reading": 5}, "InputVoltage": {"Reading": 48}},
             {"PowerSupplyType": "DC"}),
            ("4", {"InputPowerWatts": {"ApparentVA": 1000, "PowerFactor": 0.9}}, {}),
            ("12", {"InputCurrentAmps": {"Reading": 5}, "InputVoltage": {"Reading": 200},
                    "InputPowerWatts": {"PowerFactor": 0.8}},
             {"PhaseWiringType": "OnePhase3Wire"}),
        )
    ]
    monkeypatch.setattr(psu, "collect", lambda _: supplies)
    readings = monitor._supplies(None)
    assert [(r["value"], r["unit"], r["estimated"]) for r in readings] == [
        (0, "W", False), (None, "W", False), (240, "W", True),
        (900, "W", True), (800, "W", True),
    ]

    output = io.StringIO()
    table = MonitorCSV(output, multiple_nodes=True)
    first = dict(model="KR9288-X3-A0-F0-01", name="HGX390",
                 timestamp="2026-11-23T00:00:00+00:00", readings=readings[:-1])
    table.write(first)
    table.write(dict(first, name="HGX474", readings=readings[-1:]))
    rows = list(csv.DictReader(io.StringIO(output.getvalue())))
    assert rows[0]["PSU_1_W"] == "0"
    assert rows[0]["PSU_2_W"] == "NA"
    assert rows[0]["PSU_3_W"] == "240"
    assert rows[1]["PSU_1_W"] == "NA"
    assert json.loads(rows[1]["PSU_OTHER_W"]) == {"12": "800"}
    assert len(rows[0]) == len(rows[1]) == 14


def test_monitor_csv_outputs_fast_node_before_slow_node_finishes(monkeypatch, capsys):
    hosts = [Host(name, variables={"oem": "lenovo", "generation": "gb300"}, groups=["compute"])
             for name in ("slow", "fast")]
    inventory = SimpleNamespace(default=lambda key, value: value,
                                default_bool=lambda key, value: value)
    monkeypatch.setattr(cli.Inventory, "from_file", lambda _: inventory)
    monkeypatch.setattr(cli, "_role_hosts", lambda *_: hosts)

    def stream(self, selected):
        yield dict(name="fast", readings=[], error=None)
        first = list(csv.reader(io.StringIO(capsys.readouterr().out)))
        assert first[0][:3] == ["TIMESTAMP", "NODE", "MODEL"]
        assert first[0][5:] == ["PSU_79_W", "PSU_84_W", "PSU_OTHER_W"]
        assert len(first) == 2 and first[1][1] == "fast"
        yield dict(name="slow", readings=[], error=None)

    monkeypatch.setattr(cli.Runner, "stream", stream)
    assert cli.main(["nodemon", "slow,fast"]) == 0
    rest = list(csv.reader(io.StringIO(capsys.readouterr().out)))
    assert len(rest) == 1 and rest[0][1] == "slow"


def test_current_only_psus_use_configured_input_conditions(monkeypatch):
    supplies = [
        dict(id="1", name="PSU1 Input Current", unit="A", value=4.2, health="OK"),
        dict(id="2", name="PSU2", input_watts=800, input_amps=4.2, health="OK"),
        dict(id="3", name="PSU3", input_watts=None, input_amps=4.2, health="OK",
             input_phase_wiring="ThreePhase4Wire"),
    ]
    monkeypatch.setattr(psu, "collect", lambda _: supplies)
    device = SimpleNamespace(vars={"psu_input_volts": "220", "psu_input_power_factor": "0.95"},
                             options=SimpleNamespace(psu_voltage=230))
    rows = monitor._supplies(device)
    assert round(rows[0]["value"], 1) == 917.7 and rows[0]["estimated"]
    assert rows[1]["value"] == 800 and not rows[1]["estimated"]
    assert rows[2]["value"] is None


def test_detected_nominal_voltage_can_be_used_when_sensor_is_missing():
    assert monitor._estimate_watts({
        "input_voltage_type": "AC230V", "input_amps": 4,
    }, (None, 0.95)) == 874


def test_lenovo_gb300_monitor_reads_chassis_power_and_hsc_sensors():
    base = "/redfish/v1/Chassis/Chassis_0"
    control = {"Name": "Chassis Power Control", "PowerConsumedWatts": None,
               "PowerMetrics": {"AverageConsumedWatts": 1195}}
    data = {
        base + "/Power": {"PowerControl": [control]},
        base + "/Sensors/79_0": {"Name": "HSC0_Power", "ReadingType": "Power", "Reading": 686.4},
        base + "/Sensors/84_0": {"Name": "HSC1_Power", "ReadingType": "Current", "Reading": 12},
    }
    host = Host("tray-01", variables={"oem": "lenovo", "generation": "gb300"}, groups=["compute"])
    device = model.Device(host, redfish_factory=lambda _: SimpleNamespace(get=data.get))
    device.odm = oem.for_device(device)

    power, = monitor._power(device)
    assert (power["value"], power["aggregation"]) == (1195, "average")
    control["PowerConsumedWatts"] = 0
    power, = monitor._power(device)
    assert power["value"] == 0 and "aggregation" not in power
    assert [(row["id"], row["value"]) for row in monitor._supplies(device)] == [
        ("79", 686.4), ("84", None),
    ]
