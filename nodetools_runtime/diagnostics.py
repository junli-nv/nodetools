"""Opt-in CLI diagnostics."""

import sys


def debug(enabled, component, message):
    if enabled:
        print("[{}] {}".format(component, message), file=sys.stderr, flush=True)


def debug_connection(enabled, component, operation, address, user, password):
    debug(enabled, component,
          f"{operation} address={address!r} user={user!r} password={password!r}")
