"""Turn a selector expression / group / --all into a list of hosts."""

from . import hostlist
from .inventory import Host


def _resolve(inv, names, out, seen):
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        host = inv.get(name)
        if host is None:
            host = Host(name, address=None, groups=[])
        out.append(host)


def select(inv, selectors=(), groups=(), all_=False):
    """Resolve the requested hosts, preserving a stable order."""
    names = []
    if all_:
        names.extend(inv.order)
    for group in groups:
        names.extend(inv.members(group))
    for selector in selectors:
        for token in hostlist.split_top(selector):
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
    return out
