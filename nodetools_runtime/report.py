"""Output formatting compatible with ``grep -v ,OK`` workflows."""

_OK = ("ok", "on", "enabled", "true")


def _clean(value):
    return str(value).replace(" ", "_").replace(",", "_")


def _is_ok(health):
    return str(health).strip().lower() in _OK


def format_readings(result):
    lines = []
    cap = result["cap"].upper()
    if result.get("error"):
        lines.append("{}: ERROR {}".format(result["name"], result["error"]))
        return lines
    for reading in result["readings"]:
        lines.append(
            "{}: {},{},{},{},{},{}".format(
                result["name"],
                cap,
                result["role"],
                _clean(reading["id"]),
                _clean(reading["name"]),
                _clean(reading["value"]),
                _clean(reading["health"]),
            )
        )
    return lines


def format_summary(result):
    name = result["name"]
    if result.get("error"):
        return ["{}: ERROR {}".format(name, result["error"])]
    readings = result["readings"]
    ok = sum(1 for r in readings if _is_ok(r["health"]))
    bad = len(readings) - ok
    return [
        "{}: {},{},total={},OK={},nonOK={}".format(
            name, result["cap"].upper(), result["role"], len(readings), ok, bad
        )
    ]


def render(results, summary=False, errors_only=False):
    lines = []
    for result in results:
        block = format_summary(result) if summary else format_readings(result)
        if errors_only:
            block = [line for line in block if "ERROR" in line or ",OK" not in line]
        lines.extend(block)
    return lines
