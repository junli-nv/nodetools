"""BMC controls and serial number queries."""

from ..errors import CapUnsupported
from . import boot_nics, common, firmware


def managed_by(system):
    links = common.member_ids({"Members": (system.get("Links") or {}).get("ManagedBy")})
    prefix = "/redfish/v1/Managers/"
    return {path for value in links if (path := boot_nics.local_link(value))
            and path.startswith(prefix) and "/" not in path[len(prefix):]}


def manager(device):
    managers = list(firmware.collection(device, "/redfish/v1/Managers"))
    if len(managers) == 1:
        return managers[0]
    controllers = [(path, data) for path, data in managers if data.get("ManagerType") == "BMC"]
    if len(controllers) == 1:
        return controllers[0]
    selected = getattr(device, "vars", {}).get("system_id")
    for system_id in (selected,) if selected else ("1", "system"):
        system = firmware.link("/redfish/v1/Systems/" + system_id)
        linked = [
            (path, data) for path, data in controllers
            if system in common.member_ids({"Members": (data.get("Links") or {}).get("ManagerForServers")})
        ]
        if len(linked) == 1:
            return linked[0]
        if len(linked) > 1:
            break
    if controllers and (selected or getattr(device, "role", None) == "compute"):
        from .. import oem

        ident = selected or getattr(oem.for_device(device), "read_system_id", None)
        if ident:
            system = firmware.link("/redfish/v1/Systems/" + ident)
            paths = managed_by(common.resource(device, system))
            linked = [(path, data) for path, data in controllers if path in paths]
            if len(linked) == 1:
                return linked[0]
    if managers:
        raise CapUnsupported("could not identify a unique host BMC")
    return None, {}


def bmc(device, action, value=None):
    from .. import model, oem
    selected = getattr(device, "vars", {}).get("manager_id")
    if not selected and action in ("info", "time") and value is None:
        adapter = getattr(device, "odm", None) or (oem.for_device(device, configured_read=True)
                  if isinstance(device, model.Device) else None)
        selected = getattr(adapter, "read_manager_id", None)
    path, data = None, {}
    if selected and action in ("info", "time") and value is None:
        base = "/redfish/v1/Managers/"
        target = boot_nics.local_link(base + selected)
        if not target or target.rsplit("/", 1)[0] + "/" != base:
            raise CapUnsupported("invalid Redfish manager_id")
        data = common.resource(device, target)
        if data and (action != "time" or data.get("DateTime")):
            path = target
    if path is None:
        path, data = manager(device)
    if action == "info":
        if not path:
            return device.ipmi.run("mc", "info").splitlines()
        return ["{} {}".format(key, data[key]) for key in
                ("Manufacturer", "Model", "FirmwareVersion", "DateTime", "DateTimeLocalOffset")
                if data.get(key) is not None]
    if action == "time":
        if value is None:
            return [data["DateTime"]] if data.get("DateTime") else device.ipmi.run("sel", "time", "get").splitlines()
        if not path:
            raise CapUnsupported("setting time requires a Redfish Manager")
        common.require_write_success(path, device.redfish.patch(
            path, {"DateTime": value.isoformat()}, etag_source=path))
        return ["BMC time update accepted"]
    if action == "reset":
        descriptor = (data.get("Actions") or {}).get("#Manager.Reset")
        if not descriptor:
            device.ipmi.run("mc", "reset", "cold")
        else:
            target, descriptor = common.action_target(data, "Manager.Reset", path)
            allowed = descriptor.get("ResetType@Redfish.AllowableValues")
            if allowed is None and descriptor.get("@Redfish.ActionInfo"):
                info = common.resource(device, firmware.link(descriptor["@Redfish.ActionInfo"]), required=True)
                allowed = next((p.get("AllowableValues") for p in info.get("Parameters", [])
                                if p.get("Name") == "ResetType"), [])
            reset = next((method for method in ("GracefulRestart", "ForceRestart")
                          if method in (allowed or [])), None)
            if not reset:
                raise CapUnsupported("BMC did not advertise a supported Manager.Reset method")
            common.require_write_success(target, device.redfish.post(target, {"ResetType": reset}))
        return ["BMC reset accepted"]
    raise CapUnsupported("unknown BMC action")


def fru(device):
    if getattr(device, "role", None) == "powershelf":
        from .. import oem
        adapter = getattr(device, "odm", None) or oem.for_device(device)
        if getattr(adapter, "read_management_fixed", False):
            for path, data in firmware.read_collection(device, "Chassis"):
                if data.get("SerialNumber"):
                    yield "Chassis {} serial={}".format(
                        data.get("Model") or path.rsplit("/", 1)[-1], data["SerialNumber"])
                    return
    have_system = False
    for path, data in firmware.read_collection(device, "Systems"):
        serial = data.get("SerialNumber")
        if serial:
            have_system = True
            name = data.get("Model") or path.rsplit("/", 1)[-1]
            yield "System {} serial={}".format(name, serial)
    if have_system:
        return
    for line in device.ipmi.run("fru", "print", "0").splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "Product Serial" and value.strip():
            yield "System serial={}".format(value.strip())
