"""Import legacy credential assignments without executing a shell."""
import os
from pathlib import Path
import re
import shlex
import tempfile

from .errors import InventoryError
from .inventory import Inventory

FIELDS = (
    ("compute", "bmc_user", ("bmc_user", "bmc_username", "ipmi_bmc_username")),
    ("compute", "bmc_password", ("bmc_password", "ipmi_bmc_password")),
    ("nvswitch", "nvswitch_bmc_user", ("nvswitch_bmc_user",)),
    ("nvswitch", "nvswitch_bmc_password", ("nvswitch_bmc_password", "nvswitch_bmc_pass")),
    ("nvswitch", "nvswitch_os_user", ("nvswitch_os_user",)),
    ("nvswitch", "nvswitch_os_password", ("nvswitch_os_password", "nvswitch_os_pass")),
    ("powershelf", "powershelf_bmc_user", ("powershelf_bmc_user",)),
    ("powershelf", "powershelf_bmc_password", ("powershelf_bmc_password", "powershelf_bmc_pass")),
)
LEGACY_SWITCH_FIELDS = (
    "ibswitch_os_user", "ibswitch_os_pass", "spx_switch_os_user", "spx_switch_os_pass",
)

def read_credentials(path):
    try:
        lines = Path(path).read_text().splitlines()
    except (OSError, UnicodeError) as exc:
        raise InventoryError("could not read credentials file {}".format(path)) from exc
    assignments = {}
    for number, raw in enumerate(lines, 1):
        try:
            tokens = shlex.split(raw, comments=True)
        except ValueError:
            raise InventoryError("invalid credential quoting at line {}".format(number)) from None
        if not tokens:
            continue
        if tokens[0] == "export":
            tokens = tokens[1:]
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise InventoryError("expected a credential assignment at line {}".format(number))
        key, value = tokens[0].split("=", 1)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise InventoryError("invalid credential name at line {}".format(number))
        rhs = raw.partition("=")[2].strip()
        reference = re.fullmatch(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))", rhs)
        if reference:
            name = reference.group(1) or reference.group(2)
            if name not in assignments:
                raise InventoryError("unknown credential reference at line {}".format(number))
            value = assignments[name]
        assignments[key] = value
    groups = {}
    for group, key, aliases in FIELDS:
        for alias in aliases:
            if alias in assignments:
                groups.setdefault(group, {})[key] = assignments[alias]
                break
    for key in LEGACY_SWITCH_FIELDS:
        if key in assignments:
            groups.setdefault("all", {})[key] = assignments[key]
    if not groups:
        raise InventoryError("credentials file contains no supported assignments")
    return groups

def with_credentials(text, path):
    groups = read_credentials(path)
    lines = text.splitlines()
    for group, values in groups.items():
        header = "[{}:vars]".format(group)
        if header not in lines:
            lines.extend(["", header])
        start = lines.index(header) + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("[")), len(lines))
        pending = dict(values)
        for i in range(start, end):
            key = lines[i].partition("=")[0].strip()
            if key in pending:
                lines[i] = key + "=" + pending.pop(key)
        lines[end:end] = [key + "=" + value for key, value in pending.items()]
    candidate = "\n".join(lines) + "\n"
    parsed = Inventory.from_text(candidate)
    for group, values in groups.items():
        actual = parsed.all_vars if group == "all" else parsed.group_vars.get(group, {})
        for key, value in values.items():
            if actual.get(key) != value:
                raise InventoryError("credential {} cannot be represented by the inventory format".format(key))
    return candidate

def write_private(path, text):
    target = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="."+target.name+".", dir=target.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
