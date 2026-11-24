"""Read-only Cisco HGX management."""

import re

from ... import model
from ...subjects import common
from ..redfish import ReadOnlyCompute


class CiscoHgx(ReadOnlyCompute):
    def __init__(self):
        super().__init__(name="cisco", family="hgx")

    def _target(self, device, collection, field):
        if collection == "Systems" and not device.vars.get(field):
            product = str(model.hardware_identity(device).get("model") or "")
            if re.search(r"\bC880A[ -]M8\b", product, re.I):
                host = common.resource(device, "/redfish/v1/Systems/DGX")
                if host:
                    return host
        return super()._target(device, collection, field)
