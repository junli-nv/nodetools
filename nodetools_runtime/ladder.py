"""A tiny "try steps in order, return the first non-empty result" helper."""

from .errors import NodetoolsError


def first(steps, default=None):
    """Run ``(name, callable)`` steps until one returns a non-empty value.

    A step returning an empty value or raising a transport error is skipped so
    later, more permissive steps can still succeed.
    """
    for _name, func in steps:
        try:
            result = func()
        except NodetoolsError:
            result = None
        except Exception:  # pragma: no cover - defensive
            result = None
        if result:
            return result
    return default
