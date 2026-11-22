from nodetools_runtime.report import render


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
