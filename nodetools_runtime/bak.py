"""Collect and compare host-prefixed command output."""

import re
import sys
from collections import defaultdict
from difflib import unified_diff

from . import hostlist
from .errors import NodetoolsError

_HOST = re.compile(r"[A-Za-z0-9_.-]+")
_ERROR = re.compile(r"ERROR(?:\s|:|$)")


_ALIGNED_FIELD = re.compile(r"(\S+)(?: {2,}|[ \t]*\t[ \t]*)(.+)")


def compare_fields(lines, *, table=False, autodetect=False):
    """Print differing field values, grouped by the nodes that reported them."""
    settings = {}
    ignored = 0
    hosts_without_settings = set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        host, colon, item = line.partition(":")
        host = host.strip()
        item = item.strip()
        key, equals, value = item.partition("=")
        key = key.strip()
        if not colon or not _HOST.fullmatch(host):
            ignored += 1
            continue
        error = _ERROR.match(item)
        if not equals and autodetect:
            aligned = _ALIGNED_FIELD.fullmatch(item)
            key, value = aligned.groups() if aligned else ("", "")
        elif not equals:
            parts = item.split(maxsplit=1)
            key, value = parts if len(parts) == 2 else ("", "")
        if autodetect and not error and (not key or len(key.split()) != 1):
            return
        if not key or error:
            hosts_without_settings.add(host)
            ignored += 1
            continue
        attributes = settings.setdefault(host, {})
        value = value.strip()
        if attributes.setdefault(key, value) != value:
            if autodetect:
                return
            raise NodetoolsError(f"line {number}: conflicting values for {host}:{key}")

    if autodetect and not settings:
        return
    if ignored:
        print(f"bak: ignored {ignored} non-setting line(s)", file=sys.stderr)
    hosts_without_settings.difference_update(settings)
    if hosts_without_settings:
        print(f"bak: no settings for {hostlist.compact(hosts_without_settings)}", file=sys.stderr)
    if not settings:
        raise NodetoolsError("no key-value fields found (expected HOST:KEY=VALUE or HOST:KEY VALUE)")

    all_hosts = set(settings)
    keys = sorted({key for attributes in settings.values() for key in attributes})
    differences = 0
    rows = []
    for key in keys:
        groups = defaultdict(list)
        for host in all_hosts:
            groups[settings[host].get(key)].append(host)
        if len(groups) == 1:
            continue
        differences += 1
        ranked = sorted(
            groups.items(), key=lambda entry: (-len(entry[1]), entry[0] is None, entry[0] or "")
        )
        if table:
            reported = "; ".join(f"{value!r} ({len(hosts)})" for value, hosts in ranked if value is not None)
            rows.append((key, reported, str(len(groups.get(None, ())))))
        else:
            print(key)
            for value, hosts in ranked:
                display = "<missing>" if value is None else repr(value)
                print(f"  {display} ({len(hosts)}): {hostlist.compact(hosts)}")
    if table:
        print("\nField differences:")
        if rows:
            _show_field_table(rows)
    print(f"{len(all_hosts)} nodes compared, {differences}/{len(keys)} attributes differ")


def _show_field_table(rows):
    header = ("Field", "Reported values (nodes)", "Missing")
    rows = [header, *rows]
    field_width, values_width, missing_width = (max(map(len, column)) for column in zip(*rows))
    for field, values, missing in rows:
        print(f"{field:<{field_width}}  {values:<{values_width}}  {missing:>{missing_width}}")


def collect_text(lines, mode):
    """Group output by host, optionally merging identical output or diffing groups."""
    outputs = defaultdict(list)
    unmatched = 0
    for line in lines:
        host, colon, payload = line.rstrip("\r\n").partition(":")
        host = host.strip()
        if colon and _HOST.fullmatch(host):
            outputs[host].append(payload[1:] if payload.startswith(" ") else payload)
        elif line.strip():
            unmatched += 1

    if unmatched:
        print(f"bak: ignored {unmatched} line(s) without a host prefix", file=sys.stderr)
    if not outputs:
        raise NodetoolsError("no host output found (expected HOST:OUTPUT)")

    groups = defaultdict(list)
    for host, output in outputs.items():
        groups[tuple(output)].append(host)
    ranked = sorted(groups.items(), key=lambda group: (-len(group[1]), min(group[1])))
    if mode == "collect":
        for output, hosts in ranked:
            _show_group(hosts, output)
        return

    reference, hosts = ranked[0]
    print(f"Reference ({len(hosts)}): {hostlist.compact(hosts)}")
    for variant, hosts in ranked[1:]:
        print(f"\nVariant ({len(hosts)}): {hostlist.compact(hosts)}")
        for line in unified_diff(reference, variant, fromfile="reference",
                                 tofile="variant", lineterm=""):
            print(line)
    print(f"\n{len(outputs)} nodes compared, {len(groups)} distinct outputs")


def _show_group(hosts, output):
    print("---------------")
    print(hostlist.compact(hosts))
    print("---------------")
    for line in output:
        print(line)
