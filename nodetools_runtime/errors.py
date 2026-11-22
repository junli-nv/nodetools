"""Exception hierarchy."""


class NodetoolsError(Exception):
    """Base class for all nodetools errors."""


class InventoryError(NodetoolsError):
    """Raised for malformed inventory input."""


class HostlistError(NodetoolsError):
    """Raised for malformed hostlist expressions."""


class TransportError(NodetoolsError):
    """Raised when a Redfish/IPMI/CLI call cannot be performed."""


class TransportTimeout(TransportError):
    """The BMC did not respond within the request or node budget."""


class AuthError(TransportError):
    """Raised on authentication failure."""


class CapUnsupported(NodetoolsError):
    """Raised when a capability has no usable backend for a device."""
