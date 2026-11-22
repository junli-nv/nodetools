"""``nodevm`` - virtual media (mount/eject ISO) via the ODM Redfish dialect.

The action set and endpoints differ per ODM (reference ``oem/<vendor>/nodevm*``);
the compute-tray ODM dispatches to its own implementation, everything else falls
back to the NVIDIA/AMI ``AMIVirtualMedia`` behaviour.
"""

from urllib.parse import urlsplit

from ..errors import CapUnsupported, NodetoolsError, TransportError


def image_source(options, *schemes):
    """Return the explicit URL and its parsed components."""
    image = getattr(options, "image", None)
    if not image:
        raise NodetoolsError("nodevm requires an explicit image URL for this action")
    allowed = schemes or ("smb", "cifs", "nfs", "http", "https")
    try:
        parsed = urlsplit(image)
        host = parsed.hostname
        parsed.port
    except ValueError:
        raise NodetoolsError("invalid image URL") from None
    if (
        parsed.scheme not in allowed or not host or parsed.path in ("", "/")
        or parsed.username is not None or parsed.password is not None
        or parsed.fragment or any(char.isspace() for char in image)
    ):
        raise NodetoolsError("image URL must use {} with a host and path".format("/".join(allowed)))
    return image, parsed


def post(device, path, body):
    status, response = device.redfish.post(path, body)
    if not 200 <= status < 300 or (isinstance(response, dict) and "error" in response):
        raise TransportError("virtual media action {} failed (HTTP {})".format(path, status))
    return status, response


def collect(device, action, options):
    action = (action or "status").strip().lower()
    odm = device.odm
    if action != "status" and not odm.vm_write_supported:
        raise CapUnsupported(
            "virtual media action {!r} has no verified adapter for {}/{}".format(
                action, odm.name, odm.family
            )
        )
    if action in ("mount", "insert"):
        _, image = image_source(options, *odm.vm_protocols)
        if image.scheme == "nfs" and not image.path.lower().endswith(".iso"):
            raise NodetoolsError("mount requires an NFS ISO file URL")
        steps = odm.vm_mount_steps
    elif action in ("eject", "umount") and action not in odm.vm_extra_actions:
        steps = (odm.vm_eject_action,)
    elif action == "status" or action in odm.vm_extra_actions:
        steps = (action,)
    else:
        raise CapUnsupported("virtual media action {!r} is unsupported for {}".format(action, odm.name))

    lines = []
    for step in steps:
        lines.extend(odm.vm(device, step, options))
    return lines
