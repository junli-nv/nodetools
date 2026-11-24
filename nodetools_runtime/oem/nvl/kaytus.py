"""KAYTUS NVL compute trays."""

from ..redfish import ReadOnlyCompute


class KaytusNvl(ReadOnlyCompute):
    """Use standard Redfish discovery for the currently supported reads."""
