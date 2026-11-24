"""Identify Redfish NICs by hardware properties joined to boot option MACs."""

import re
from collections import defaultdict

from . import common

PCI_PRODUCTS = {"101b": "cx6", "101d": "cx6", "101f": "cx6",
                "1021": "cx7", "1023": "cx8", "a2d6": "bf2", "a2dc": "bf3"}
PART_PRODUCTS = {"MCX755106AS-HEAT": "cx7", "MCX713106AS-VEAT": "cx7",
                 "MCX623106AN-CDAT": "cx6"}
BDF = re.compile(r"(?:[0-9a-f]{4}:)?[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", re.I)
LINK = re.compile(r"/redfish/v1/(?:[A-Za-z0-9_.~-]+/)*[A-Za-z0-9_.~-]+")


def local_link(value):
    if isinstance(value, dict):
        value = value.get("@odata.id")
    if isinstance(value, str) and LINK.fullmatch(value) and not {".", ".."}.intersection(value.split("/")):
        return value
    return None


def mac(value):
    text = re.sub(r"[:-]", "", str(value or "")).lower()
    return text if re.fullmatch("[0-9a-f]{12}", text) and text != "000000000000" else None


def product(*labels, pci=None):
    pci = pci or {}
    vendor = re.sub(r"^0x", "", str(pci.get("VendorId") or "").lower())
    device = re.sub(r"^0x", "", str(pci.get("DeviceId") or "").lower())
    if vendor == "15b3" and device in PCI_PRODUCTS:
        return PCI_PRODUCTS[device]
    for label in labels:
        text = str(label or "").lower()
        if str(label or "").strip().upper() in PART_PRODUCTS:
            return PART_PRODUCTS[str(label).strip().upper()]
        for pattern, prefix in ((r"bluefield[\s-]*([23])\b", "bf"),
                                (r"connectx[\s-]*([678])\b", "cx"),
                                (r"\b(bf|cx)([23678])\b", "")):
            match = re.search(pattern, text)
            if match:
                return prefix + "".join(match.groups())
    return None


def vendor_name(value):
    text = str(value or "").strip().lower()
    if any(name in text for name in ("nvidia", "mellanox")):
        return "nvidia"
    if "intel" in text:
        return "intel"
    if "broadcom" in text:
        return "broadcom"
    return text or None


def members(device, path, required=False):
    if not path:
        return
    page = path + "?$expand=."
    seen = set()
    while page and page not in seen:
        seen.add(page)
        data = common.resource(device, page)
        if "Members" not in data and page == path + "?$expand=.":
            data = common.resource(device, path, required=required)
        for item in data.get("Members") or ():
            if not isinstance(item, dict):
                continue
            item_path = local_link(item)
            if not item_path and not item.get('@odata.id') and len(item) > 1 and item.get('Id'):
                item_path = local_link(path + '/' + str(item['Id']))
            if not item_path or not item_path.startswith(path + "/"):
                continue
            resource = item if len(item) > 1 else common.resource(device, item_path, required=required)
            if resource:
                yield item_path, resource
        page = data.get("Members@odata.nextLink")
        if not isinstance(page, str) or not page.startswith(path + "?"):
            break


def slot(item):
    location = item.get("Location") or {}
    part = location.get("PartLocation") or {}
    label = str(part.get("ServiceLabel") or "").strip()
    if str(part.get("LocationType", "")).casefold() == "slot":
        return re.sub(r"^(?:PCIe\s+)?Slot\s*", "", label, flags=re.I) or str(part.get("OrdinalValue") or "")
    match = re.fullmatch(r"(?:PCIe\s+)?Slot\s+([A-Za-z0-9_-]+)", label, re.I)
    return match.group(1) if match else None


def addresses(item):
    ethernet = item.get("Ethernet") or {}
    return {
        address
        for value in [
            item.get("MACAddress"), item.get("PermanentMACAddress"),
            ethernet.get("MACAddress"), ethernet.get("PermanentMACAddress"),
            *(ethernet.get("AssociatedMACAddresses") or []),
            *(item.get("AssociatedNetworkAddresses") or []),
        ]
        for address in (mac(value),)
        if address
    }


def pci_address(*items):
    for item in items:
        for field in ("PCIAddress", "PCIeAddress", "BDF"):
            value = str(item.get(field) or "").lower()
            if BDF.fullmatch(value):
                return value if len(value.split(":")) == 3 else "0000:" + value
    return None


def linked(item, field):
    return local_link(item.get(field) or (item.get("Links") or {}).get(field))


def inventory(device, system, chassis_default):
    links = (system.get("Links") or {}).get("Chassis") or []
    chassis = device.vars.get("chassis_id")
    paths = ["/redfish/v1/Chassis/" + chassis] if chassis else (links or [chassis_default])
    by_mac = defaultdict(lambda: defaultdict(set))
    for chassis_path in dict.fromkeys(local_link(path) for path in paths):
        if not chassis_path or not chassis_path.startswith("/redfish/v1/Chassis/"):
            continue
        for path, adapter in members(device, chassis_path + "/NetworkAdapters"):
            labels = tuple(adapter.get(key) for key in ("Model", "PartNumber", "Name"))
            nic_type = product(*labels)
            maker = vendor_name(adapter.get("Manufacturer"))
            ports_link = linked(adapter, "Ports") or linked(adapter, "NetworkPorts")
            functions_link = linked(adapter, "NetworkDeviceFunctions")
            ports = dict(members(device, ports_link))
            has_port_macs = any(addresses(port) for port in ports.values())
            needs_function = not has_port_macs or not nic_type
            funcs = dict(members(device, functions_link)) if needs_function else {}
            for entry_path, item in {**ports, **funcs}.items():
                port = item if entry_path in ports else ports.get(linked(item, "PhysicalPortAssignment"), {})
                pci_link = linked(item, "PCIeFunction")
                pci = common.resource(device, pci_link) if pci_link else item
                kind = product(*labels, pci=pci)
                fields = {
                    "type": kind or nic_type,
                    "model": adapter.get("Model"),
                    "vendor": "nvidia" if kind or nic_type else maker,
                    "slot": slot(adapter) or slot(pci),
                    "port": port.get("PhysicalPortNumber") or port.get("Id") or item.get("PhysicalPortNumber"),
                    "pci": pci_address(item, pci, adapter),
                    "link": port.get("LinkNetworkTechnology") or port.get("ActiveLinkTechnology"),
                }
                for address in addresses(item):
                    for key, value in fields.items():
                        if value is not None and str(value).strip():
                            by_mac[address][key].add(str(value).strip())
    return {
        address: {key: next(iter(values)) for key, values in fields.items() if len(values) == 1}
        for address, fields in by_mac.items()
    }
