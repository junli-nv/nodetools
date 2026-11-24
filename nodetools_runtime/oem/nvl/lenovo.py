"""Lenovo HG635N_V2 Redfish adapter."""

from functools import lru_cache

from ...errors import CapUnsupported
from ...subjects import boot_nics, common, netboot
from ..redfish import ReadOnlyCompute


class LenovoNvl(ReadOnlyCompute):
    monitor_chassis = "Chassis_0"
    monitor_power_control = "Chassis Power Control"
    monitor_hsc_sensors = {"79": ("79_0", "HSC0_Power"), "84": ("84_0", "HSC1_Power")}

    def __init__(self):
        super().__init__(name="lenovo", system_id="System_0")

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
