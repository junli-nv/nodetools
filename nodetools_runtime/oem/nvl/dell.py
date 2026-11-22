"""Dell NVL compute via standard, discoverable Redfish resources."""

from ..redfish import ReadOnlyCompute


class DellNvl(ReadOnlyCompute):
    def __init__(self):
        super().__init__(name="dell")
