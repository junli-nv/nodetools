"""Shared Redfish resource discovery."""

from .. import model
from ..errors import CapUnsupported
from ..subjects import common, inventory, power, vm
from .base import Oem


class RedfishDiscovery(Oem):
    _infrastructure_eventlog_actions = ("status", "bmc", "hmc")
    def _target(self, device, collection, field):
        base = "/redfish/v1/" + collection
        ident = device.vars.get(field)
        if ident:
            return common.resource(device, base + "/" + ident, required=True)
        paths = common.member_ids(common.resource(device, base, required=True))
        if len(paths) != 1:
            raise CapUnsupported("expected one {} resource; configure {}".format(collection, field))
        return common.resource(device, paths[0], required=True)

    def _require_status(self, device, action, allowed=("status",)):
        if action not in allowed:
            raise CapUnsupported(
                "no verified {} adapter for {} {} action {!r}".format(
                    device.role, model.odm_for(device), model.generation_for(device), action
                )
            )

    def nvswitch_power(self, device, action):
        self._require_status(device, action)
        system = self._target(device, "Systems", "system_id")
        return power.host_status(device.name, system.get("PowerState"))

    def powershelf_power(self, device, action):
        self._require_status(device, action)
        if device.vars.get("system_id") and not device.vars.get("chassis_id"):
            data = self._target(device, "Systems", "system_id")
        else:
            data = self._target(device, "Chassis", "chassis_id")
        return ["{}: {},{}".format(
            device.name, data.get("SerialNumber") or "-", data.get("PowerState") or "Unknown"
        )]

    def infrastructure_eventlog(self, device, action):
        self._require_status(device, action, self._infrastructure_eventlog_actions)
        if device.vars.get("manager_id") or device.role == "powershelf":
            parent = self._target(device, "Managers", "manager_id")
        else:
            parent = self._target(device, "Systems", "system_id")
        services = common.resource(device, self._link(parent, "LogServices"), required=True)
        paths = common.member_ids(services)
        if action == "clear":
            return self._clear_eventlog(device, paths)
        lines = []
        for path in paths:
            service = common.resource(device, path, required=True)
            lines.extend(common.log_lines(device, self._link(service, "Entries"), sep=" "))
        return self._label_lines(device, lines)

    def _clear_eventlog(self, device, paths):
        targets = set()
        for path in paths:
            service = common.resource(device, path, required=True)
            action = (service.get("Actions") or {}).get("#LogService.ClearLog") or {}
            target = action.get("target")
            if path.startswith("/redfish/v1/") and target == path.rstrip("/") + "/Actions/LogService.ClearLog":
                targets.add(target)
        if len(targets) != 1:
            raise CapUnsupported("no verified ClearLog target: expected one advertised log action")
        target, = targets
        result = common.post(device, target, {})
        common.require_write_success(target, result)
        status, _ = result
        message = "Event log clear accepted" if status == 202 else "Event log cleared"
        return self._label_lines(device, ["{} (HTTP {})".format(message, status)])

    def infrastructure_inventory(self, device):
        return inventory._firmware(device, self._firmware_inventory_path(device))

    def _firmware_inventory_path(self, device):
        root = common.resource(device, "/redfish/v1", required=True)
        service = common.resource(device, self._link(root, "UpdateService"), required=True)
        return self._link(service, "FirmwareInventory")

    @staticmethod
    def _link(resource, name):
        link = resource.get(name) or {}
        path = link.get("@odata.id") if isinstance(link, dict) else None
        if not path:
            raise CapUnsupported("Redfish did not advertise {}".format(name))
        return path


class ReadOnlyCompute(RedfishDiscovery):
    """Generic compute queries using the Redfish resources the BMC exposes."""

    info_subjects = ("power", "eventlog", "inventory", "vm", "bios", "bootorder", "macs")
    vm_write_supported = False

    def __init__(self, name="generic", system_id=None, family="nvl"):
        self.name = name
        self.family = family
        self.read_system_id = system_id

    def _system(self, device):
        if self.read_system_id and not device.vars.get("system_id"):
            data = common.resource(device, "/redfish/v1/Systems/" + self.read_system_id)
            if data:
                return data
        return self._target(device, "Systems", "system_id")

    def power(self, device, action):
        self._require_status(device, action)
        return power.host_status(device.name, self._system(device).get("PowerState"))

    def bios(self, device, action):
        action = action or "status"
        self._require_status(device, action, ("status", "read", "pending"))
        system = self._system(device)
        data = common.resource(device, self._link(system, "Bios"), required=True)
        if action == "pending":
            settings = data.get("@Redfish.Settings") or {}
            data = common.resource(device, self._link(settings, "SettingsObject"), required=True)
        attributes = data.get("Attributes")
        if not isinstance(attributes, dict):
            raise CapUnsupported("Redfish did not report BIOS attributes")
        return self._label_lines(device, [
            "{}={}".format(key, common.jq(value)) for key, value in sorted(attributes.items())
        ])

    def bootorder(self, device, action):
        boot = self._system(device).get("Boot") or {}
        order = boot.get("BootOrder")
        if not isinstance(order, list):
            raise CapUnsupported("Redfish did not report a persistent boot order")
        return self._label_lines(device, order)

    def macs(self, device, action):
        system = self._system(device)
        path = self._link(system, "EthernetInterfaces")
        lines = []
        for iface in common.entries(device, path):
            mac = iface.get("MACAddress") or iface.get("PermanentMACAddress")
            if mac:
                lines.append("{},{}".format(iface.get("Id") or iface.get("Name") or "-", mac))
        return self._label_lines(device, lines)

    def eventlog(self, device, action):
        return self.infrastructure_eventlog(device, action)

    def firmware_inventory(self, device):
        return self.infrastructure_inventory(device)

    def vm(self, device, action, options=None):
        parents = []
        for collection, field in (("Managers", "manager_id"), ("Systems", "system_id")):
            if any(device.vars.get(key) for key in ("manager_id", "system_id")) and not device.vars.get(field):
                continue
            selected = device.vars.get(field)
            base = "/redfish/v1/" + collection
            parents.extend([base + "/" + selected] if selected else common.member_ids(
                common.resource(device, base)
            ))
        return self._label_lines(device, vm.status_lines(device, parents))
