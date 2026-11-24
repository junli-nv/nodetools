"""Read-only HPE HGX management through iLO or OpenBMC."""

import re

from ... import model
from ...subjects import common
from ..redfish import ReadOnlyCompute


class HpeHgx(ReadOnlyCompute):
    def __init__(self):
        super().__init__(name="hpe", family="hgx")

    def _target(self, device, collection, field):
        if collection == "Systems" and not device.vars.get(field):
            product = str(model.hardware_identity(device).get("model") or "")
            if re.search(r"\bXD685\b", product, re.I):
                host = common.resource(device, "/redfish/v1/Systems/1")
                if host:
                    return host
        return super()._target(device, collection, field)
