"""Capability collectors: fan, psu, temp, inlet."""

from . import fan, inlet, monitor, psu, temp

COLLECTORS = {
    "fan": fan.collect,
    "psu": psu.collect,
    "temp": temp.collect,
    "inlet": inlet.collect,
    "monitor": monitor.collect,
}


def collector_for(name):
    return COLLECTORS.get(name)
