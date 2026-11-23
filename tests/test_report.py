from nodetools_runtime.report import format_inlet_temperature, render


def _result(readings, error=None):
    return {
        "name": "cp-01",
        "role": "compute",
        "address": "10.0.0.1",
        "cap": "fan",
        "readings": readings,
        "error": error,
    }


def test_fan_line_format():
    result = _result(
        [{"id": "Fan_1", "name": "Front Fan 1", "value": 8500, "health": "OK"}]
    )
    lines = render([result])
    assert lines == ["cp-01: Fan_1,Front_Fan_1,8500,OK"]


def test_psu_line_format():
    result = _result(
        [{"id": "PSU_1", "name": "psu", "value": "1318.8", "health": "OK"}]
    )
    result["cap"] = "psu"
    assert render([result]) == ["cp-01: PSU_1,OK,1318.8"]


def test_grep_not_ok_compatible():
    result = _result(
        [{"id": "Fan_1", "name": "f", "value": 8500, "health": "OK"},
         {"id": "Fan_2", "name": "f", "value": "NA", "health": "Critical"}]
    )
    lines = render([result])
    assert [line for line in lines if ",OK" not in line] == [
        "cp-01: Fan_2,f,NA,Critical"
    ]


def test_error_line_and_summary():
    result = _result([], error="boom")
    assert render([result]) == ["cp-01: ERROR boom"]
    assert render([result], summary=True) == ["cp-01: ERROR boom"]


def test_summary_counts():
    result = _result(
        [{"id": "a", "name": "a", "value": 1, "health": "OK"},
         {"id": "b", "name": "b", "value": "NA", "health": "Critical"}]
    )
    assert render([result], summary=True) == [
        "cp-01: FAN,compute,total=2,OK=1,nonOK=1"
    ]


def test_fan_speed_line_keeps_rpm_percent_and_unknown():
    result = _result([
        {"id": "1", "name": "Fan 1", "value": 1600, "unit": "RPM", "health": "OK"},
        {"id": "2", "name": "Fan 2", "value": 50, "unit": "%", "health": "Warning"},
        {"id": "3", "name": "Fan 3", "value": "NA", "health": "Unknown"},
    ])
    assert render([result], fan_speeds=True) == ["cp-01: 1600,50%,NA"]
    assert "cp-01: 2,Fan_2,50%,Warning" in render([result])


def test_temperature_format_preserves_sensor_name():
    result = _result([
        {"id": "1", "name": "GPU Inlet, Left", "value": 0, "health": "OK"},
        {"id": "2", "name": "CPU Temp", "value": 50, "health": "Warning"},
    ])
    result["cap"] = "temp"
    assert render([result]) == ['cp-01: 1,"GPU Inlet, Left",OK,0', "cp-01: 2,CPU Temp,Warning,50"]
    assert render([result], errors_only=True) == ["cp-01: 2,CPU Temp,Warning,50"]


def test_inlet_only_prints_first_available_temperature():
    result = _result([{"value": "NA"}, {"value": 0.0}, {"value": 27}])
    assert format_inlet_temperature(result) == "cp-01: 0"
    assert format_inlet_temperature(_result([], error="offline")) == "cp-01: NA"
