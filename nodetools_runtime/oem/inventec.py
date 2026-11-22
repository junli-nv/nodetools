"""Inventec HCR7-A6-C tray."""

from .base import Oem


class Inventec(Oem):
    name = "inventec"
    fan_chassis = ("Chassis_0",)
