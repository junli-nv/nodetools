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

import re

_BRACKET = re.compile(r"[\[\{]")
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
