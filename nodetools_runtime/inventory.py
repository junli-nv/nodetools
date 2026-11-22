"""Inventory parsing and generation.

The inventory is an Ansible-style INI file. Each host lives in a group section
and carries its BMC address plus optional per-host variables::

    [compute]
    vdc1-pro2-t10-gpu-cp-01 host=10.10.0.1
    vdc1-pro2-t10-gpu-cp-02 host=10.10.0.2

    [compute:vars]
    bmc_user=root
    bmc_password=0penBmc

    [nodehealth:defaults]
    jobs=18

Variable precedence: ``[all:vars]`` < group ``:vars`` < per-host inline.
"""

import os

from .config import DEFAULTS_SECTION, NAME_ROLE_PATTERNS, ROLE_GROUPS
from .errors import InventoryError

_ADDRESS_KEYS = ("host", "bmc_ip", "ansible_host", "ip", "address")


def _strip_comment(line):
    if line.lstrip().startswith("#") or line.lstrip().startswith(";"):
        return ""
    out = []
    for ch in line:
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _parse_kv(text):
    if "=" in text:
        key, _, value = text.partition("=")
        return key.strip(), value.strip()
    return None, None


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class Host:
    def __init__(self, name, address=None, variables=None, groups=None):
        self.name = name
        self.address = address
        self.vars = dict(variables or {})
        self.groups = list(groups or [])

    @property
    def role(self):
        for group in self.groups:
            if group in ROLE_GROUPS:
                return ROLE_GROUPS[group]
        for pattern, role in NAME_ROLE_PATTERNS:
            if pattern in self.name:
                return role
        return "unknown"

    def get(self, key, default=None):
        return self.vars.get(key, default)


class Inventory:
    def __init__(self):
        self.hosts = {}       # name -> Host
        self.order = []       # insertion order of host names
        self.groups = {}      # group -> [names]
        self.all_vars = {}
        self.group_vars = {}  # group -> {k: v}
        self.defaults = {}

    # -- construction ----------------------------------------------------
    @classmethod
    def from_file(cls, path):
        if not os.path.exists(path):
            raise InventoryError("inventory not found: {}".format(path))
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_text(handle.read())

    @classmethod
    def from_text(cls, text):
        inv = cls()
        section = None
        section_vars = False
        host_lines = []  # (group, name, raw)
        for raw in text.splitlines():
            line = _strip_comment(raw)
            if not line.strip():
                continue
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip()
                section_vars = section.endswith(":vars")
                if section_vars:
                    section = section[: -len(":vars")].strip()
                if section == DEFAULTS_SECTION:
                    section_vars = True
                if not section_vars:
                    inv.groups.setdefault(section, [])
                continue
            if section is None:
                raise InventoryError("host line outside a section: {!r}".format(raw))
            if section_vars:
                key, value = _parse_kv(stripped)
                if key is None:
                    continue
                if section == "" or section == "all":
                    inv.all_vars[key] = value
                elif section == DEFAULTS_SECTION:
                    inv.defaults[key] = value
                else:
                    inv.group_vars.setdefault(section, {})[key] = value
            else:
                host_lines.append((section, stripped))

        for group, raw in host_lines:
            inv._add_host_line(group, raw)
        return inv

    def _add_host_line(self, group, raw):
        tokens = raw.split()
        name = tokens[0]
        inline = {}
        address = None
        for token in tokens[1:]:
            key, value = _parse_kv(token)
            if key is None:
                if address is None:
                    address = token
                continue
            if key in _ADDRESS_KEYS:
                address = value
            else:
                inline[key] = value

        merged = {}
        merged.update(self.all_vars)
        merged.update(self.group_vars.get(group, {}))
        merged.update(inline)

        if name in self.hosts:
            host = self.hosts[name]
            if group not in host.groups:
                host.groups.append(group)
            host.vars.update(merged)
            if address and not host.address:
                host.address = address
        else:
            host = Host(name, address=address, variables=merged, groups=[group])
            self.hosts[name] = host
            self.order.append(name)
        if name not in self.groups.setdefault(group, []):
            self.groups[group].append(name)

    # -- queries ---------------------------------------------------------
    def members(self, group):
        return list(self.groups.get(group, []))

    def get(self, name):
        return self.hosts.get(name)

    def default(self, key, fallback=None):
        return self.defaults.get(key, fallback)

    def default_bool(self, key, fallback=False):
        return _as_bool(self.defaults.get(key), fallback)


def generate_from_hostslist(path):
    """Build inventory text from a ``DeviceType Hostname BMC_IP`` hosts.list."""
    if not os.path.exists(path):
        raise InventoryError("hosts.list not found: {}".format(path))
    buckets = {"compute": [], "nvswitch": [], "powershelf": [], "switch": []}
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = _strip_comment(raw)
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            device_type, hostname, bmc_ip = parts[0], parts[1], parts[2]
            if device_type == "PhysicalNode":
                group = "compute"
            elif device_type == "PowerShelf":
                group = "powershelf"
            elif device_type == "Switch":
                group = "nvswitch" if "-gpu-nvl-" in hostname else "switch"
            else:
                group = "switch"
            buckets.setdefault(group, []).append((hostname, bmc_ip))

    lines = [
        "# nodetools inventory (generated from hosts.list)",
        "# Edit credentials below to match your environment.",
        "",
    ]
    group_order = ("compute", "nvswitch", "powershelf", "switch")
    for group in group_order:
        entries = buckets.get(group) or []
        if not entries:
            continue
        lines.append("[{}]".format(group))
        for hostname, bmc_ip in entries:
            lines.append("{} host={}".format(hostname, bmc_ip))
        lines.append("")

    lines += [
        "[all:vars]",
        "bmc_user=root",
        "bmc_password=0penBmc",
        "",
        "[compute:vars]",
        "bmc_user=root",
        "bmc_password=0penBmc",
        "",
        "[nvswitch:vars]",
        "nvswitch_bmc_user=root",
        "nvswitch_bmc_password=p@ssw0rd@NVSW",
        "",
        "[powershelf:vars]",
        "powershelf_bmc_user=root",
        "powershelf_bmc_password=0penBmc",
        "",
        "[{}]".format(DEFAULTS_SECTION),
        "jobs=18",
        "timeout=10",
        "node_timeout=30",
        "summary=false",
        "",
    ]
    return "\n".join(lines)
