"""HPE GB200 and GB300 NVL compute trays."""

from ..redfish import ReadOnlyCompute


class HpeNvl(ReadOnlyCompute):
    def __init__(self):
        super().__init__(name="hpe", system_id="System_0")
