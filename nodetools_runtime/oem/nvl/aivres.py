"""Aivres NVL compute trays."""

from ..redfish import ReadOnlyCompute


class AivresNvl(ReadOnlyCompute):
    """Use standard Redfish discovery for the currently supported reads."""
