"""A small, self-contained hostlist range expander.

Supported forms (nesting allowed)::

    node01                     -> ["node01"]
    node[01-03]                -> ["node01", "node02", "node03"]
    node[1-3]                  -> ["node1", "node2", "node3"]
    node[001-003,007]          -> ["node001", ..., "node007"]
    node{1..3}                 -> ["node1", "node2", "node3"]
    gpu-cp-[01-03],gpu-cp-07   -> top-level commas split independent tokens

Numeric ranges preserve the width of the low bound (zero padding), matching the
behaviour of the legacy python3-hostlist helper for the patterns used here.
"""

import itertools
import re

from .errors import HostlistError

_BRACKET = re.compile(r"[\[\{]")
_FIELD = re.compile(r"^(.*?)([0-9]+)?([^0-9]*)$")
_RANGE = re.compile(r"^(?P<low>-?\d+)-(?P<high>-?\d+)$")
_BRACE_RANGE = re.compile(r"^(?P<low>-?\d+)\.\.(?P<high>-?\d+)$")


def _emit(value, width):
    text = str(value)
    if width and not text.startswith("-"):
        return text.zfill(width)
    return text


def _expand_alts(content):
    """Expand the inside of a [] or {} occurrence into a list of strings."""
    parts = []
    for item in content.split(","):
        item = item.strip()
        if not item:
            continue
        m = _BRACE_RANGE.match(item)
        if m:
            parts.extend(_numeric(m.group("low"), m.group("high")))
            continue
        m = _RANGE.match(item)
        if m:
            parts.extend(_numeric(m.group("low"), m.group("high")))
            continue
        parts.append(item)
    return parts


def _numeric(low, high):
    lo, hi = int(low), int(high)
    width = len(low.lstrip("-")) if low.lstrip("-").isdigit() else 0
    if hi < lo:
        lo, hi = hi, lo
    return [_emit(n, width) for n in range(lo, hi + 1)]


def _expand_one(expr):
    m = _BRACKET.search(expr)
    if not m:
        return [expr]
    open_ch = expr[m.start()]
    close_ch = "]" if open_ch == "[" else "}"
    depth = 0
    end = -1
    for i in range(m.start(), len(expr)):
        if expr[i] == open_ch:
            depth += 1
        elif expr[i] == close_ch:
            depth -= 1
            if depth == 0:
                end = i
                break
    if end == -1:
        return [expr]
    prefix = expr[: m.start()]
    alts = _expand_alts(expr[m.start() + 1 : end])
    suffix = expr[end + 1 :]
    results = []
    for alt in alts:
        results.extend(_expand_one(prefix + alt + suffix))
    return results


def _split_top(expr):
    """Split on commas that are not inside [] or {}."""
    tokens = []
    depth = 0
    current = []
    for ch in expr:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == "," and depth == 0:
            tokens.append("".join(current))
            current = []
            continue
        current.append(ch)
    tokens.append("".join(current))
    return tokens


def expand(expr):
    """Expand a comma-separated hostlist expression into a list of names."""
    expr = expr.strip()
    if not expr:
        return []
    seen = set()
    out = []
    for token in _split_top(expr):
        token = token.strip()
        if not token:
            continue
        for name in _expand_one(token):
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def split_top(expr):
    """Split an expression on commas that are outside brackets (public)."""
    return [t.strip() for t in _split_top(expr) if t.strip()]


def _format_ranges(ranges):
    chunks = []
    for low, high, width in ranges:
        if low == high:
            chunks.append("%0*d" % (width, low))
        else:
            chunks.append("%0*d-%0*d" % (width, low, width, high))
    return "[" + ",".join(chunks) + "]"


def _compact_pass(pairs):
    """One collection round: fold the rightmost numeric field of ``left``."""
    remaining = set(left + right for left, right in pairs)
    entries = []
    for left, right in pairs:
        match = _FIELD.match(left)
        prefix, num, suffix = match.group(1), match.group(2), match.group(3)
        suffix = suffix + right
        if num is None:
            entries.append(((left + right, None), None, None, left + right))
        else:
            entries.append(((prefix, suffix), int(num), len(num), left + right))

    def key(entry):
        (prefix, suffix), num, width, host = entry
        return (
            prefix,
            suffix or "",
            -1 if num is None else num,
            -1 if width is None else width,
            host,
        )

    entries.sort(key=key)

    out = []
    changed = False
    for (prefix, suffix), group in itertools.groupby(entries, key=lambda e: e[0]):
        members = list(group)
        if suffix is None:
            host = members[0][3]
            out.append(("", host))
            remaining.discard(host)
            continue
        ranges = []
        for _key, num, width, host in members:
            if host not in remaining:
                continue
            low = num
            while True:
                candidate = "%s%0*d%s" % (prefix, width, num, suffix)
                if candidate not in remaining:
                    break
                remaining.discard(candidate)
                num += 1
            ranges.append((low, num - 1, width))
        changed = True
        if len(ranges) == 1 and ranges[0][0] == ranges[0][1]:
            number = "%0*d" % (ranges[0][2], ranges[0][0])
        else:
            number = _format_ranges(ranges)
        out.append((prefix, number + suffix))
    return out, changed


def compact(hosts):
    """Collapse a list of hostnames into a SLURM/scontrol-style hostlist.

    ``compact(["n01", "n02", "n10"])`` -> ``"n[01-02,10]"``.
    """
    pairs = []
    seen = set()
    for host in hosts:
        host = host.strip()
        if not host or host in seen:
            continue
        if re.search(r"[\[\],]", host):
            raise HostlistError("host contains hostlist syntax: {!r}".format(host))
        seen.add(host)
        pairs.append((host, ""))

    changed = True
    while changed:
        pairs, changed = _compact_pass(pairs)
    return ",".join(left + right for left, right in pairs)
