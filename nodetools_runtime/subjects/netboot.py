"""Discover PXE ports and plan persistent per-port boot priorities."""

from dataclasses import dataclass, field
import re
import time

from .. import model
from ..errors import CapUnsupported, NodetoolsError, TransportError
from . import boot_nics as nics, common

SYSTEMS = "/redfish/v1/Systems"
SMC_ORDER = "/Oem/Supermicro/FixedBootOrder"
FILTERS = ("type", "vendor", "slot", "port", "pci", "link", "mac", "uefi_path")
MAC_IN_ENTRY = re.compile(r"MAC\s*[:(]\s*([0-9a-f:-]{12,17})", re.I)
MAC_IN_NAME = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{2}:){5}[0-9a-f]{2}(?![0-9a-f])", re.I)


def system_path(device):
    ident = device.vars.get("system_id") or device.odm.bootorder_system
    if not ident and model.platform_for(device) == "nvl" and device.odm.name == "gb200":
        ident = device.odm.system_id
    if ident:
        path = nics.local_link(SYSTEMS + "/" + ident)
        if not path or path.rsplit("/", 1)[0] != SYSTEMS:
            raise CapUnsupported("invalid Redfish system_id")
        return path
    resources = common.member_ids(common.resource(device, SYSTEMS, required=True))
    if len(resources) != 1:
        raise CapUnsupported("expected one Redfish system; configure system_id")
    path = nics.local_link(resources[0])
    if not path or path.rsplit("/", 1)[0] != SYSTEMS:
        raise CapUnsupported("invalid Redfish Systems member")
    return path


def reference(value):
    text = str(value or "").strip().strip('"').casefold()
    match = re.fullmatch(r"(?:boot)?([0-9a-f]+)", text)
    return match.group(1).lstrip("0") or "0" if match else text


def pxe(name, uefi=""):
    text = name + " " + uefi
    if not re.search(r"\bpxe", name, re.I):
        return None
    if re.search(r"IPv4|PXEv4", text, re.I):
        return "ipv4"
    if re.search(r"IPv6|PXEv6", text, re.I):
        return "ipv6"
    return "unknown"


def address(name, path):
    for text in (path, name):
        match = MAC_IN_ENTRY.search(text)
        if match and nics.mac(match.group(1)):
            return nics.mac(match.group(1))
    match = MAC_IN_NAME.search(name)
    return nics.mac(match.group(0)) if match else None


@dataclass
class Entry:
    key: str
    label: str
    path: str
    mac: str
    family: str
    fields: dict

    def matches(self, filters, family):
        if family == "all":
            if self.family not in ("ipv4", "ipv6"):
                return False
        elif family and self.family != family:
            return False
        for field, expected in filters.items():
            if field == "uefi_path":
                if self.path != expected and not self.path.startswith(expected.rstrip("/") + "/"):
                    return False
            elif field == "mac":
                if self.mac != expected:
                    return False
            elif field == "type":
                names = (self.fields.get("type"), self.fields.get("model"))
                if expected not in {str(name or "").casefold() for name in names}:
                    return False
            elif str(self.fields.get(field, "")).casefold() != expected:
                return False
        return True

    def display(self):
        details = " ".join("{}={}".format(key, self.fields.get(key) or "-")
                           for key in ("type", "model", "vendor", "slot", "port", "pci", "link"))
        mac_text = ":".join(self.mac[i:i + 2] for i in range(0, 12, 2)) if self.mac else "-"
        return "{} {} mac={} {} | {}{}".format(
            self.key, self.family, mac_text, details, self.label,
            " | " + self.path if self.path else "",
        )


def entry(key, label, path, adapters):
    family = pxe(label, path)
    if not family:
        return None
    mac = address(label, path)
    fields = dict(adapters.get(mac, {}))
    if not fields.get("vendor") or fields.get("vendor") == "supermicro":
        vendor = nics.vendor_name(label)
        if vendor in ("nvidia", "intel", "broadcom"):
            fields["vendor"] = vendor
    kind = nics.product(label)
    if kind:
        fields.setdefault("type", kind)
    return Entry(key, label, path, mac, family, fields)


def options_for(device, path, system, adapters, required):
    boot = system.get("Boot") or {}
    collection = nics.local_link(system.get("BootOptions") or boot.get("BootOptions")) or path + "/BootOptions"
    options = []
    names = {}
    for _, item in nics.members(device, collection, required=required):
        key = item.get("BootOptionReference") or item.get("Id")
        name = str(item.get("DisplayName") or "")
        if key and name:
            names[reference(key)] = name
        if item.get("BootOptionEnabled") is False:
            continue
        row = entry(str(key or ""), name,
                    str(item.get("UefiDevicePath") or ""), adapters)
        if key and row:
            options.append(row)
    return options, names


def standard_order(device, system, target, path):
    active = system.get("Boot") or {}
    if target == path:
        return active.get("BootOrder")
    pending = common.resource(device, target, required=True).get("Boot") or {}
    return pending.get("BootOrder") or active.get("BootOrder")


def smc_entries(values, options, adapters):
    for index, name in enumerate(values, 1):
        row = entry(str(index), str(name), "", adapters)
        if row:
            matches = [item for item in options if row.mac and row.mac == item.mac and row.family == item.family]
            if len(matches) == 1:
                row.path = matches[0].path
            yield row


def stable_first(values, selected):
    return [value for index, value in enumerate(values) if index in selected] + [
        value for index, value in enumerate(values) if index not in selected
    ]


def network_before_disk(groups):
    network = [i for i, value in enumerate(groups) if value.startswith("UEFI Network")]
    disk = [i for i, value in enumerate(groups) if value.startswith("UEFI Hard Disk")]
    if len(network) != 1 or len(disk) > 1:
        raise CapUnsupported("cannot identify the BIOS network and disk groups")
    if not disk or network[0] < disk[0]:
        return groups
    result = list(groups)
    result.insert(disk[0], result.pop(network[0]))
    return result


@dataclass
class Plan:
    target: str
    system: str
    before: dict
    after: dict
    entries: dict
    matched: set
    names: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def preferences(device, path, system, fixed, options, adapters, filters, family, action, names=None):
    if fixed and isinstance(fixed.get("UEFINetwork"), list):
        groups, order = fixed.get("FixedBootOrder"), fixed["UEFINetwork"]
        rows = list(smc_entries(order, options, adapters))
        selected = [row for row in rows if row.matches(filters, family)]
        if action != "prefer":
            return [row.display() for row in selected]
        if not groups or not selected:
            raise CapUnsupported("no matching PXE entry or BIOS network group")
        before = {"FixedBootOrder": groups, "UEFINetwork": order}
        after = {"FixedBootOrder": network_before_disk(groups),
                 "UEFINetwork": stable_first(order, {int(row.key) - 1 for row in selected})}
        return Plan(path + SMC_ORDER, path, before, after,
                    {row.label: row for row in rows}, {row.label for row in selected})

    settings = (system.get("@Redfish.Settings") or {}).get("SettingsObject")
    target = nics.local_link(settings) if settings else path
    if not target or (target != path and not target.startswith(path + "/")):
        raise CapUnsupported("invalid Redfish boot settings resource")
    order = standard_order(device, system, target, path)
    rank = {reference(value): index for index, value in enumerate(order or ())}
    selected = sorted((row for row in options if row.matches(filters, family)),
                      key=lambda row: rank.get(reference(row.key), len(rank)))
    if action != "prefer":
        return ["rank={} {}".format(rank.get(reference(row.key), -1) + 1 or "-", row.display())
                for row in selected]
    if model.platform_for(device) != "nvl" or not order:
        raise CapUnsupported("persistent per-port boot order is unavailable for this system")
    keys = [reference(row.key) for row in selected]
    if not keys or len(keys) != len(set(keys)) or len(rank) != len(order) or any(key not in rank for key in keys):
        raise CapUnsupported("selected PXE options do not map uniquely to the existing BootOrder")
    before = {"BootOrder": order}
    after = {"BootOrder": stable_first(order, {rank[key] for key in keys})}
    return Plan(target, path, before, after,
                {reference(row.key): row for row in options}, set(keys), names or {})


def collect(device, action, options):
    action = action or "list"
    if action == "pxe":
        return device.odm.netboot(device, action)
    if action not in ("list", "status", "mac", "net", "prefer"):
        raise CapUnsupported("unsupported network boot action " + action)
    filters = getattr(options, "netboot_filters", None) or {}
    family = getattr(options, "netboot_family", None) or ("ipv4" if action == "prefer" else None)
    path = system_path(device)
    system = common.resource(device, path, required=True)
    fixed = common.resource(device, path + SMC_ORDER) if device.odm.name == "smc" else {}
    chassis = "/redfish/v1/Chassis/" + ("Chassis_0" if model.platform_for(device) == "nvl" else "1")
    adapters = nics.inventory(device, system, chassis)
    if device.odm.name == "aivres" and action != "prefer":
        bios = common.resource(device, path + "/Bios")
        bbs = (bios.get("Attributes") or {}).get("NetworkBbsPriority")
        if isinstance(bbs, list):
            entries = (
                entry(str(item.get("BootOptionNumber") or "-"), item.get("DescriptionName") or "",
                      item.get("DevicePath") or "", adapters)
                for item in bbs if isinstance(item, dict)
            )
            rows = [row.display() for row in entries if row and row.matches(filters, family)]
            return label(device, rows or ["No matching PXE entries"])
    have_bbs = isinstance(fixed.get("UEFINetwork"), list)
    rows, names = options_for(device, path, system, adapters, required=not have_bbs)
    result = preferences(device, path, system, fixed, rows, adapters, filters, family, action, names)
    if isinstance(result, Plan):
        state = system.get("Boot") or {}
        if state.get("BootSourceOverrideEnabled") in ("Once", "Continuous"):
            result.notes.append("Existing boot override: {} {} (separate from persistent order)".format(
                state["BootSourceOverrideEnabled"], state.get("BootSourceOverrideTarget", "-")
            ))
        return result
    return label(device, result or ["No matching PXE entries"])


def label(device, lines):
    return ["{}: {}".format(device.name, line) for line in lines]


def normalize_filters(values):
    result = {key: value.strip() for key, value in values.items() if value is not None}
    if not all(result.values()):
        raise NodetoolsError("empty network boot selector")
    for key, value in tuple(result.items()):
        if key == "uefi_path":
            continue
        normalized = value.casefold()
        if key == "mac":
            normalized = nics.mac(value)
        elif key == "type":
            normalized = nics.product(value) or normalized
        elif key == "vendor":
            normalized = nics.vendor_name(value)
        elif key == "pci":
            normalized = nics.pci_address({"BDF": value})
        if not normalized:
            raise NodetoolsError("invalid --{} value {!r}".format(key, value))
        result[key] = normalized
    return result


def short_entry(row):
    product = row.fields.get("type") or row.fields.get("model") or row.fields.get("vendor")
    if not product:
        return row.label
    product = product.upper() if row.fields.get("type") else product
    port = " port " + row.fields["port"] if row.fields.get("port") else ""
    mac = " " + ":".join(row.mac[i:i + 2] for i in range(0, 12, 2)) if row.mac else ""
    family = {"ipv4": "IPv4", "ipv6": "IPv6"}.get(row.family, row.family)
    return "{}{} PXE {}{}".format(product, port, family, mac)


def describe(device, plan, order=None, message=None):
    lines = [message] if message else ["Planned boot order"]
    effective = plan.after if order is None else order
    if "UEFINetwork" in effective:
        lines.append("BIOS boot groups")
        for number, group in enumerate(effective["FixedBootOrder"], 1):
            if group.startswith("UEFI Network:"):
                group = "UEFI Network"
            lines.append("  {}. {}".format(number, group))
        lines.append("Network boot order (* matched)")
        values = effective["UEFINetwork"]
    else:
        lines.append("Boot order (* matched)")
        values = effective["BootOrder"]

    for number, value in enumerate(values, 1):
        key = value if "UEFINetwork" in effective else reference(value)
        row = plan.entries.get(key)
        name = short_entry(row) if row else plan.names.get(key, value)
        if "BootOrder" in effective and name != value:
            name += " ({})".format(value)
        mark = "*" if key in plan.matched else " "
        lines.append("  {:>2}. {} {}".format(number, mark, name))
    return label(device, lines + plan.notes)


def read_plan_order(device, plan):
    if plan.target.endswith(SMC_ORDER):
        data = common.resource(device, plan.target, required=True)
        return {key: data.get(key) for key in plan.before}
    system = common.resource(device, plan.system, required=True)
    return {"BootOrder": standard_order(device, system, plan.target, plan.system)}


def confirmed(actual, expected):
    if "FixedBootOrder" not in expected:
        return actual == expected
    groups = actual.get("FixedBootOrder")
    if not isinstance(groups, list):
        return False
    return (
        [item.split(":", 1)[0] for item in groups]
        == [item.split(":", 1)[0] for item in expected["FixedBootOrder"]]
        and actual.get("UEFINetwork") == expected["UEFINetwork"]
    )


def apply(device, plan):
    current = read_plan_order(device, plan)
    if current != plan.before:
        raise TransportError("boot order changed after preflight; run the command again")
    if plan.before == plan.after:
        return describe(device, plan, order=current, message="Persistent PXE preference is already set")
    payload = plan.after if plan.target.endswith(SMC_ORDER) else {"Boot": plan.after}
    response = common.patch(device, plan.target, payload, etag_source=plan.target)
    common.require_write_success(plan.target, response)
    for delay in (0, 1, 2) if response[0] == 202 else (0,):
        if delay:
            time.sleep(delay)
        current = read_plan_order(device, plan)
        if confirmed(current, plan.after):
            return describe(device, plan, order=current,
                            message="Boot order verified in BMC. Reboot separately to apply.")
    raise TransportError("BMC accepted change but persistent boot order did not match on readback")
