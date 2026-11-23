"""Opt-in CLI diagnostics for non-Redfish operations."""

import sys


def debug(enabled, component, message):
    if enabled:
        print("[{}] {}".format(component, message), file=sys.stderr, flush=True)
