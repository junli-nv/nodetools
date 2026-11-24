"""Supermicro NVL compute trays."""

from ..redfish import ReadOnlyCompute


class SmcNvl(ReadOnlyCompute):
    """Keep generic Redfish discovery until NVL-specific behavior is verified."""
