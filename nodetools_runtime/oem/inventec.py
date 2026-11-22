"""Inventec HCR7-A6-C tray."""

from .base import Oem


class Inventec(Oem):
    name = "inventec"
    family = "nvl"
    fan_chassis = ("Chassis_0",)
