"""Air-intake (inlet) temperature check for compute trays.

Reuses the temperature ladder (:mod:`caps.temp`) and keeps only the sensor that
reports the chassis air intake -- the tray front/ambient/inlet sensor. On GB200
that is ``Chassis_0_Front_IO_Temp_0``; other ODMs name it ``Inlet_Temp``,
``Ambient_Temp``, ``TEMP_MB_INLET`` and so on (see the legacy ``oem/*/nodemon*``
sensor lists). Internal module inlets (processor modules, PDB) are excluded:
they sit downstream of the fans and read warmer than room air.

Each reading's health is a verdict against the expected inlet temperature
(``config.INLET_TARGET`` +/- ``config.INLET_TOLERANCE``, tunable per run with
``--target`` / ``--tolerance``): ``OK`` inside the band, else ``HIGH``/``LOW``.
"""

from ..config import DEFAULT_CHASSIS, INLET_TARGET, INLET_TOLERANCE
from . import common, temp

_INLET_HINTS = ("inlet", "ambient", "intake", "front_io")
# Chassis-level names that stay valid even with a prefix (Chassis_0_Front_IO_Temp_0).
_CHASSIS_HINTS = ("front_io", "intake")
# Downstream module inlets: their "inlet" is behind the fans, not the chassis
# intake (PSU0_Inlet_Temp, HGX_Inlet_Temp, HGX_ProcessorModule_0_Inlet_Temp_0).
_MODULE_SKIP = (
    "processormodule", "pdb", "psu", "hsc", "hgx", "nvsw", "nvme",
    "gpu", "cpu", "dimm", "vrm", "sxm", "module", "board",
    "switch", "oam", "nic", "raid", "ssd", "hdd",
)


def _is_inlet(reading):
    name = (reading.get("name") or reading.get("id") or "").lower()
    if any(hint in name for hint in _CHASSIS_HINTS):
        return True
    if not any(hint in name for hint in _INLET_HINTS):
        return False
    return not any(skip in name for skip in _MODULE_SKIP)


def _limit(device, attr, fallback):
    options = getattr(device, "options", None)
    value = getattr(options, attr, None) if options is not None else None
    return fallback if value is None else float(value)


def _find(device):
    """The inlet sensor, read from a single chassis when possible.

    The front/ambient sensor lives on one chassis and a ``ThermalMetrics`` read
    is expensive for the BMC, so probing chassis one at a time and stopping at
    the first hit is far cheaper than walking every chassis (which also
    saturates the BMC and inflates each request). Discovered chassis are only
    consulted when the conventional ids miss; the full ladder is the last
    resort for a non-ThermalMetrics backend.
    """
    seen = []

    def probe(cid):
        if not cid or cid in seen:
            return None
        seen.append(cid)
        return [r for r in temp.chassis_thermal_metrics(device, cid) if _is_inlet(r)]

    for cid in DEFAULT_CHASSIS:
        found = probe(cid)
        if found:
            return found
    for cid in common.chassis_list(device, DEFAULT_CHASSIS):
        found = probe(cid)
        if found:
            return found
    return [r for r in temp.collect(device) if _is_inlet(r)]


def collect(device):
    readings = _find(device)
    if not readings:
        return []
    target = _limit(device, "inlet_target", INLET_TARGET)
    tolerance = _limit(device, "inlet_tolerance", INLET_TOLERANCE)
    for reading in readings:
        value = common.num(reading.get("value"))
        if value is None:
            reading["health"] = "Unknown"
            continue
        delta = float(value) - target
        if abs(delta) <= tolerance:
            reading["health"] = "OK"
        else:
            reading["health"] = "HIGH" if delta > 0 else "LOW"
    return readings
