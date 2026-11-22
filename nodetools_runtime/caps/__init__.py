"""Capability collectors: fan, psu, temp, inlet."""

from . import fan, inlet, psu, temp

COLLECTORS = {
    "fan": fan.collect,
    "psu": psu.collect,
    "temp": temp.collect,
    "inlet": inlet.collect,
}


def collector_for(name):
    return COLLECTORS.get(name)
