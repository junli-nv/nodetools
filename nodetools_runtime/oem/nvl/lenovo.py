"""Lenovo HG635N_V2 Redfish adapter."""

import time
from functools import lru_cache

from ...errors import CapUnsupported, TransportError
from ...subjects import boot_nics, common, firmware, management, netboot, vm
from ..redfish import ReadOnlyCompute


class LenovoNvl(ReadOnlyCompute):
    fan_profile = "lenovo-gb300-nvl"
    monitor_chassis = "Chassis_0"
    fan_read_chassis = (monitor_chassis,)
    inventory_fixed_caps = ("fan",)
    fan_thermal_sensor_numbers = True
    monitor_power_control = "Chassis Power Control"
    monitor_hsc_sensors = {"79": ("79_0", "HSC0_Power"), "84": ("84_0", "HSC1_Power")}

    vm_write_supported = True
    vm_protocols = ("nfs", "cifs", "smb", "https")
    vm_mount_steps = ("enable", "insert")
    vm_extra_actions = ("enable", "stop")

    def __init__(self):
        super().__init__(name="lenovo", system_id="System_0")

    def fan_control(self, device, action):
        if action != "max":
            return super().fan_control(device, action)

        try:
            return super().fan_control(device, action)
        except TransportError as exc:
            try:
                super().fan_control(device, "auto")
            except TransportError as restore_error:
                raise TransportError(f"{exc}; restoring automatic fan control failed: {restore_error}") from exc
            raise TransportError(f"{exc}; automatic fan control restore command accepted") from exc

    def _host_manager(self, device):
        paths = management.managed_by(self._system(device))
        if len(paths) != 1:
            raise CapUnsupported("Lenovo host system did not advertise a unique BMC")
        return next(iter(paths))

    def eventlog(self, device, action):
        self._require_status(device, action, ("status", "bmc", "hmc"))
        streams = []
        if action in ("status", "bmc"):
            streams.append(("BMC", self._host_manager(device) + "/LogServices/SEL"))
        if action in ("status", "hmc"):
            streams.append(("HMC", "/redfish/v1/Managers/HGX_BMC_0/LogServices/EventLog"))

        lines = []
        for name, path in streams:
            service = common.resource(device, path, required=True)
            entries = common.log_lines(device, self._link(service, "Entries"))
            lines.extend(f"{device.name}[{name}]: {entry}" for entry in entries or ["No entries"])
        return lines

    @staticmethod
    def _vm_state(parent):
        ami = (parent.get("Oem") or {}).get("Ami") or {}
        return (ami.get("VirtualMedia") or {}).get("RMediaStatus")

    @staticmethod
    def _vm_wait(device, path, ready, attempts=8, interval=0.7):
        for attempt in range(attempts):
            if attempt:
                time.sleep(interval)
            if ready(common.resource(device, path, required=True)):
                return
        raise TransportError(f"Lenovo VirtualMedia state did not match at {path}")

    @staticmethod
    def _vm_post(device, path, parent, name, payload, oem=False):
        actions = parent.get("Actions") or {}
        if oem:
            actions = actions.get("Oem") or {}
        target = path + ("/Actions/Oem/" if oem else "/Actions/") + name
        if (actions.get("#" + name) or {}).get("target") != target:
            raise CapUnsupported(f"Lenovo did not advertise {name} at {path}")
        status, body = device.redfish.post(target, payload)
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        pending = oem and code == "Ami.1.0.DelayInActionCompletion"
        if not 200 <= status < 300 or (error is not None and not pending):
            detail = f", {code}" if code else ""
            raise TransportError(f"Lenovo {name} failed (HTTP {status}{detail})")
        if status == 202:
            task = boot_nics.local_link(body)
            prefix = "/redfish/v1/TaskService/Tasks/"
            if not task or task.rpartition("/")[0] + "/" != prefix:
                raise TransportError(f"Lenovo {name} did not return a Redfish task link")
            LenovoNvl._vm_wait(device, task, LenovoNvl._vm_task_done, attempts=30, interval=1)

    @staticmethod
    def _vm_task_done(task):
        state = task.get("TaskState")
        if state in ("Exception", "Killed", "Cancelled", "Interrupted") or task.get("TaskStatus") == "Critical":
            ids = [message.get("MessageId") for message in (task.get("Messages") or [])
                   if isinstance(message, dict) and message.get("MessageId")]
            detail = f" ({', '.join(ids)})" if ids else ""
            raise TransportError(f"Lenovo VirtualMedia task {state}{detail}")
        return state == "Completed"

    def vm(self, device, action, options=None):
        if action == "status":
            return super().vm(device, action, options)
        path = self._host_manager(device)
        parent = common.resource(device, path, required=True)
        collection = boot_nics.local_link(parent.get("VirtualMedia"))
        if collection != path + "/VirtualMedia":
            raise CapUnsupported("Lenovo BMC did not advertise its VirtualMedia collection")
        if action == "enable":
            return self._vm_toggle(device, path, parent, collection, True)

        slots = [(slot, data) for slot, data in firmware.collection(device, collection, required=True)
                 if slot.rpartition("/")[0] == collection]
        if action == "stop":
            if any(data.get("Inserted") is not False for _, data in slots):
                raise CapUnsupported("eject inserted media before stopping Lenovo Remote Media")
            return self._vm_toggle(device, path, parent, collection, False)
        return self._vm_slot_action(device, action, options, slots)

    def _vm_toggle(self, device, path, parent, collection, enable):
        state = "Enabled" if enable else "Disabled"
        current = self._vm_state(parent)
        if current not in ("Enabled", "Disabled"):
            raise CapUnsupported("Lenovo BMC did not report Remote Media state")
        if current != state:
            self._vm_post(device, path, parent, "AMIVirtualMedia.EnableRMedia",
                          {"RMediaState": "Enable" if enable else "Disable"}, oem=True)
        self._vm_wait(device, path, lambda item: self._vm_state(item) == state)
        self._vm_wait(device, collection, lambda item: bool(common.member_ids(item)) == enable)
        return self._label_lines(device, [f"Remote Media {state.lower()}"])

    @staticmethod
    def _vm_matches(data, image):
        source = image.partition("://")[2]
        return data.get("Image") in (image, source, "//" + source)

    def _vm_slot_action(self, device, action, options, slots):
        occupied = [(path, data) for path, data in slots if data.get("Inserted") is True]
        if action == "eject":
            if getattr(options, "image", None):
                image, _ = vm.image_source(options, *self.vm_protocols)
                occupied = [(path, data) for path, data in occupied if self._vm_matches(data, image)]
            if not occupied:
                raise CapUnsupported("no matching inserted Lenovo image")
            if len(occupied) != 1:
                raise CapUnsupported("multiple inserted Lenovo images; specify the exact URL")
            path, data = occupied[0]
            self._vm_post(device, path, data, "VirtualMedia.EjectMedia", {})
            self._vm_wait(device, path, lambda item: item.get("Inserted") is False)
            return self._label_lines(device, [f"Ejected VirtualMedia slot {path.rsplit('/', 1)[-1]}"])

        if action != "insert":
            raise CapUnsupported(f"unsupported Lenovo VirtualMedia action {action!r}")
        image, parsed = vm.image_source(options, *self.vm_protocols)
        if any(self._vm_matches(data, image) for _, data in occupied):
            return self._label_lines(device, ["Image already mounted"])
        available = [(path, data) for path, data in slots if path.rsplit("/", 1)[-1].startswith("CD")
                     and data.get("Inserted") is False and not data.get("Image")]
        if not available:
            raise CapUnsupported("no empty Lenovo VirtualMedia CD slot")
        path, data = available[0]
        protocol = {"nfs": "NFS", "smb": "CIFS", "cifs": "CIFS", "https": "HTTPS"}[parsed.scheme]
        # AMI uses a network path for NFS and CIFS instead of a scheme-prefixed URL.
        location = image if protocol == "HTTPS" else "//" + image.partition("://")[2]
        payload = {"Image": location, "TransferProtocolType": protocol}
        for key, attr in (("UserName", "vm_user"), ("Password", "vm_password")):
            value = getattr(options, attr, None)
            if value is not None:
                payload[key] = value
        self._vm_post(device, path, data, "VirtualMedia.InsertMedia", payload)
        self._vm_wait(device, path, lambda item: item.get("Inserted") is True and self._vm_matches(item, image))
        return self._label_lines(device, [f"Mounted in VirtualMedia slot {path.rsplit('/', 1)[-1]}"])

    def bootorder(self, device, action):
        system = self._system(device)
        boot = system.get("Boot") or {}
        order = boot.get("BootOrder")
        if not isinstance(order, list):
            raise CapUnsupported("Redfish did not report a persistent boot order")
        if not order:
            return []

        collection = boot_nics.local_link(system.get("BootOptions") or boot.get("BootOptions"))
        if not collection:
            system_path = boot_nics.local_link(system)
            ident = system.get("Id") or device.vars.get("system_id") or self.read_system_id
            if not system_path and ident:
                system_path = boot_nics.local_link("/redfish/v1/Systems/" + str(ident))
            if system_path:
                collection = system_path + "/BootOptions"

        options = {}
        for _, option in boot_nics.members(device, collection):
            key = option.get("BootOptionReference") or option.get("Id")
            if key:
                options[netboot.reference(key)] = [
                    str(option[field]) for field in ("DisplayName", "UefiDevicePath")
                    if option.get(field)
                ]

        rows = ["  ".join([str(item), *options.get(netboot.reference(item), [])]) for item in order]
        return self._label_lines(device, rows)

    def macs(self, device, action):
        system = self._system(device)
        interfaces = common.entries(device, self._link(system, "EthernetInterfaces"))

        @lru_cache(maxsize=None)
        def read(path):
            return common.resource(device, path) if path else {}

        rows = []
        for iface in interfaces:
            address = iface.get("MACAddress") or iface.get("PermanentMACAddress")
            if not address:
                continue
            ident = iface.get("Id") or iface.get("Name") or "-"
            function = read(boot_nics.linked(iface, "NetworkDeviceFunction"))
            iface_macs = boot_nics.addresses(iface)
            function_macs = boot_nics.addresses(function)
            if iface_macs and function_macs and iface_macs.isdisjoint(function_macs):
                function = {}

            port = read(boot_nics.linked(function, "PhysicalNetworkPortAssignment")
                        or boot_nics.linked(function, "PhysicalPortAssignment"))
            link = port.get("LinkStatus") or iface.get("LinkStatus")
            speed = _nic_speed(port, iface)
            port_name = port.get("PhysicalPortNumber")
            if port_name is None:
                port_name = port.get("Id") or port.get("Name") or "N/A"

            part = None
            if str(ident).lower().startswith("virtual"):
                vendor, model = "N/A", "Virtual Ethernet"
            else:
                pci = read(boot_nics.linked(function, "PCIeFunction"))
                card = read(boot_nics.linked(pci, "PCIeDevice"))
                vendor, model = _nic_identity(pci, card)
                part = str(card.get("PartNumber") or "").strip()

            row = (f"{ident},{address}  vendor={vendor}  model={model}  "
                   f"link={link or 'N/A'}  speed={speed}  port={port_name}")
            rows.append(row + (f"  part={part}" if part else ""))
        return self._label_lines(device, rows)


def _nic_identity(pci, card):
    vendor_id = str(pci.get("VendorId") or "").strip().lower().removeprefix("0x")
    device_id = str(pci.get("DeviceId") or "").strip().lower().removeprefix("0x")
    manufacturer = str(card.get("Manufacturer") or "").strip()
    vendor = {"15b3": "NVIDIA", "8086": "Intel"}.get(vendor_id) or manufacturer
    model = str(card.get("Model") or "").strip()
    if not model:
        kind = boot_nics.product(pci=pci)
        if kind:
            family = {"bf": "BlueField-", "cx": "ConnectX-"}[kind[:2]]
            model = "NVIDIA " + family + kind[2:]
        elif (vendor_id, device_id) == ("8086", "1533"):
            model = "Intel I210"
    return vendor or "N/A", model or "N/A"


def _nic_speed(port, iface):
    for value, unit in (
        (port.get("CurrentSpeedGbps"), "Gbps"),
        (port.get("CurrentLinkSpeedMbps"), "Mbps"),
        (iface.get("SpeedMbps"), "Mbps"),
    ):
        if isinstance(value, (int, float)) and value > 0:
            return f"{value:g} {unit}"
    return "N/A"
