"""Turn a selector expression / group / --all into a list of hosts."""

from . import hostlist
from .inventory import Host


def _row_key(name):
    return name.split("-gpu-")[0] if "-gpu-" in name else name


def _resolve(inv, names, out, seen):
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        host = inv.get(name)
        if host is None:
            host = Host(name, address=None, groups=[])
        out.append(host)


def _auto_expand(inv, hosts, enabled):
    """Add same-row powershelf/nvswitch hosts for selected compute hosts."""
    if not enabled:
        return hosts
    rows = set()
    for host in hosts:
        if host.role == "compute":
            rows.add(_row_key(host.name))
    if not rows:
        return hosts
    extra = []
    for name in inv.order:
        host = inv.get(name)
        if host.role in ("powershelf", "nvswitch") and _row_key(name) in rows:
            extra.append(name)
    out, seen = [], set(h.name for h in hosts)
    out.extend(hosts)
    _resolve(inv, extra, out, seen)
    return out


def select(inv, selectors=(), groups=(), all_=False, expand=True):
    """Resolve the requested hosts, preserving a stable order."""
    names = []
    if all_:
        names.extend(inv.order)
    for group in groups:
        names.extend(inv.members(group))
    for selector in selectors:
        for token in selector.replace(" ", "").split(","):
            if not token:
                continue
            if token in inv.groups:
                names.extend(inv.members(token))
            elif token in inv.hosts:
                names.append(token)
            else:
                names.extend(hostlist.expand(token))

    out, seen = [], set()
    _resolve(inv, names, out, seen)
    return _auto_expand(inv, out, expand)
