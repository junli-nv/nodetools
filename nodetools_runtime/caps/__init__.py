"""Capability collectors: fan, psu, temp."""

from . import fan, psu, temp

COLLECTORS = {
    "fan": fan.collect,
    "psu": psu.collect,
    "temp": temp.collect,
}


def collector_for(name):
    return COLLECTORS.get(name)
