"""Stream one compact CSV row per completed node sample."""

import csv
import json

_FIELDS = ("TIMESTAMP", "NODE", "MODEL", "TEMP_INLET_C", "POWER_NODE_W")
_MODEL_PSU_SLOTS = {
    "KR9288-X3-A0-F0-01": range(8),
    "SYS-822GS-NB3RT": range(1, 7),
}


def _number(value):
    return "NA" if value is None else format(value, ".12g")


def _slot_order(slot):
    return (0, int(slot), slot) if slot.isascii() and slot.isdecimal() else (1, slot, "")


class MonitorCSV:
    def __init__(self, stream, multiple_nodes=False):
        self.stream = stream
        self.writer = csv.writer(stream, lineterminator="\n")
        self.multiple_nodes = multiple_nodes
        self.slots = None

    def write(self, result):
        inlet = power = None
        psus = {}
        for item in result["readings"]:
            value = item["value"]
            if item["kind"] == "inlet" and inlet is None:
                inlet = value
            elif item["kind"] == "node_power":
                power = value
            elif item["kind"] == "psu":
                psus[str(item["id"])] = _number(value)

        if self.slots is None:
            model = (result.get("model") or "").upper()
            if self.multiple_nodes:
                expected = {str(slot) for slots in _MODEL_PSU_SLOTS.values() for slot in slots}
            else:
                expected = {str(slot) for slot in _MODEL_PSU_SLOTS.get(model, ())}
            self.slots = sorted(expected | psus.keys(), key=_slot_order)
            self.writer.writerow([*_FIELDS, *("PSU_{}_W".format(slot) for slot in self.slots), "PSU_OTHER_W"])

        other = {slot: psus[slot] for slot in psus.keys() - set(self.slots)}
        self.writer.writerow([
            result["timestamp"], result["name"], result.get("model") or "NA",
            _number(inlet), _number(power), *(psus.get(slot, "NA") for slot in self.slots),
            json.dumps(other, sort_keys=True, separators=(",", ":")) if other else "NA",
        ])
        self.stream.flush()
