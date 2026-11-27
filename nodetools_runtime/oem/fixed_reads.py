"""Optional fixed management reads for independently verified OEM layouts."""

from ..errors import CapUnsupported
from ..subjects import common, firmware, inventory, power, vm


class FixedManagementReads:
    read_manager_id = None
    read_chassis_id = None
    read_log_paths = {}
    read_firmware_path = '/redfish/v1/UpdateService/FirmwareInventory'
    read_management_fixed = True

    def _target(self, device, collection, field):
        fallback = {
            'manager_id': self.read_manager_id,
            'system_id': getattr(self, 'read_system_id', None),
            'chassis_id': self.read_chassis_id,
        }.get(field)
        ident = device.vars.get(field) or fallback
        if ident:
            path = '/redfish/v1/' + collection + '/' + ident
            data = common.resource(device, path)
            if data:
                return data
            if device.vars.get(field):
                return common.resource(device, path, required=True)
        return super()._target(device, collection, field)

    def _firmware_inventory_path(self, device):
        return self.read_firmware_path

    def infrastructure_inventory(self, device):
        rows = [[item.get('Id') or path.rsplit('/', 1)[-1],
                 item.get('Version'), path, 'Version']
                for path, item in firmware.collection(
                    device, self.read_firmware_path, required=True, expand=True)]
        return inventory._format_firmware(device, rows)

    def _fixed_logs(self, device, action):
        scope, suffix = self.read_log_paths[action]
        collection, field, default = scope
        ident = device.vars.get(field) or default
        path = '/redfish/v1/' + collection + '/' + ident + suffix
        data = common.resource(device, path)
        if not isinstance(data.get('Members'), list):
            return None
        lines = common.log_lines(device, path, sep=' ')
        return self._label_lines(device, lines or ['No event log entries'])

    def infrastructure_eventlog(self, device, action):
        if action in self.read_log_paths:
            lines = self._fixed_logs(device, action)
            if lines is not None:
                return lines
        return super().infrastructure_eventlog(device, action)

    def eventlog(self, device, action):
        return self.infrastructure_eventlog(device, action)

    def vm(self, device, action, options=None):
        self._require_status(device, action)
        ident = device.vars.get('manager_id') or self.read_manager_id
        return self._label_lines(device, vm.status_lines(
            device, ['/redfish/v1/Managers/' + ident]))

    def macs(self, device, action):
        system = self._system(device)
        if getattr(self, "macs_from_boot_options", False):
            from ..subjects import netboot, boot_nics
            boot = system.get("Boot") or {}
            path = self._link(boot, "BootOptions")
            rows = {}
            for _, item in boot_nics.members(device, path, required=True):
                mac = netboot.address(str(item.get("DisplayName") or ""),
                                      str(item.get("UefiDevicePath") or ""))
                if mac:
                    rows.setdefault(mac, "UEFI/" + str(item.get("BootOptionReference") or item.get("Id")))
            if not rows:
                raise CapUnsupported("BMC BootOptions did not expose Ethernet MAC addresses")
            return self._label_lines(device, [
                "{},{}".format(label, ":".join(mac[i:i+2] for i in range(0, 12, 2)))
                for mac, label in rows.items()
            ])
        link = system.get('EthernetInterfaces')
        if isinstance(link, dict) and link.get('@odata.id'):
            return super().macs(device, action)
        from ..subjects import boot_nics
        chassis = device.vars.get('chassis_id') or self.read_chassis_id
        ports = boot_nics.inventory(device, system, '/redfish/v1/Chassis/' + chassis)
        if not ports:
            raise CapUnsupported('BMC did not expose compute Ethernet MAC addresses')
        return self._label_lines(device, [
            '{},{}'.format(row.get('type') or row.get('model') or 'NIC',
                           ':'.join(mac[i:i+2] for i in range(0, 12, 2)))
            for mac, row in sorted(ports.items())
        ])
