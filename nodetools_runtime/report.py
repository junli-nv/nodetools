"""Output formatting aligned with the legacy node* scripts.

Sensor lines are ``<node>: <field>,<field>,...`` so ``grep -v ,OK`` still works:

* ``nodefan``/``nodesensors`` fans: ``Id,Name,SpeedRPM,Health``
* ``nodepsu``:                        ``Id,Health,Reading``
* ``nodetemp``:                       ``Id,Health,Reading``

``--summary`` collapses to one ``total/OK/nonOK`` line per node.
"""

_OK = ("ok", "on", "enabled", "true")

# Per-capability field order; matches the legacy scripts' field layout.
_FIELDS = {
    "fan": ("id", "name", "value", "health"),
    "psu": ("id", "health", "value"),
    "temp": ("id", "health", "value"),
    "inlet": ("id", "health", "value"),
}
_DEFAULT_FIELDS = ("id", "name", "value", "health")


def _clean(value):
    return str(value).replace(" ", "_").replace(",", "_")


def _is_ok(health):
    return str(health).strip().lower() in _OK


def _fields(cap):
    return _FIELDS.get(str(cap).lower(), _DEFAULT_FIELDS)


def format_readings(result):
    lines = []
    if result.get("error"):
        lines.append("{}: ERROR {}".format(result["name"], result["error"]))
        return lines
    fields = _fields(result["cap"])
    for reading in result["readings"]:
        cells = ",".join(_clean(reading.get(field, "")) for field in fields)
        lines.append("{}: {}".format(result["name"], cells))
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


def format_lines(result):
    """Info subjects (power/eventlog/inventory/...): verbatim ``node: ...`` lines.

    The handler already produced the full ``<node>: <payload>`` (or
    ``<node>[HMC]: ...``) line, so this only appends the error line when one
    occurred.
    """
    if result.get("error"):
        return ["{}: ERROR {}".format(result["name"], result["error"])]
    return list(result.get("lines") or [])


def render(results, summary=False, errors_only=False):
    lines = []
    for result in results:
        if "lines" in result:
            block = format_lines(result)
        else:
            block = format_summary(result) if summary else format_readings(result)
        if errors_only:
            block = [line for line in block if "ERROR" in line or ",OK" not in line]
        lines.extend(block)
    return lines
