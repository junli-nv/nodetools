"""Wiwynn NVL compute trays."""

from ..redfish import ReadOnlyCompute


class WiwynnNvl(ReadOnlyCompute):
    """Use standard Redfish discovery for the currently supported reads."""
