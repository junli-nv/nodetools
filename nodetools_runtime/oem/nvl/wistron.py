"""Wistron GB300 tray (Sensor-based readings, GB200-style fan control)."""

from ..base import Oem


class Wistron(Oem):
    name = "wistron"
    family = "nvl"
    fan_profile = "wistron-nvl"
    fan_chassis = ("Chassis_0",)
