"""CLI for a single BMC capture and offline replay."""

import math
import sys
from pathlib import Path

from . import model, redfish_snapshot, runtime, scan
from .arguments import INFO_COMMANDS, SENSOR_COMMANDS
from .cli import Options, _apply_redfish_defaults, _inventory_path
from .config import DEFAULT_TIMEOUT
from .errors import NodetoolsError
from .inventory import Host, Inventory, RESOURCE_ID_KEYS
from .report import render

_METADATA = ("role", "platform", "oem", "generation", "system_model", *RESOURCE_ID_KEYS)
_INFO_READS = {"power", "inventory", "bootorder", "netboot", "macs", "bios", "vm"}


def dump(args):
    if not (1 <= args.workers <= 32 and args.max_requests > 0
            and math.isfinite(args.seconds) and args.seconds > 0):
        raise NodetoolsError("workers must be 1..32; seconds and max-requests must be positive")
    if Path(args.output).exists():
        raise NodetoolsError(f"snapshot already exists: {args.output}")

    inventory = Inventory.from_file(_inventory_path(args.inventory))
    host = inventory.get(args.host)
    if host is None or not host.address:
        raise NodetoolsError("select one inventory host with a BMC address")
    options = Options(args)
    if options.timeout is None:
        options.timeout = float(inventory.default("timeout", DEFAULT_TIMEOUT))
    if not math.isfinite(options.timeout) or options.timeout <= 0:
        raise NodetoolsError("timeout must be a finite positive number")
    _apply_redfish_defaults(options, inventory)
    show_progress = options.debug or sys.stderr.isatty()
    options.debug = False

    def factory():
        return runtime.build_device(host, options, apply_deadline=False, load_profile=False).redfish

    user, password = runtime._resolve_creds(host, options)
    metadata = {key: host.vars[key] for key in _METADATA if host.vars.get(key)}
    metadata["role"] = host.role
    def progress(elapsed, completed, active, pending):
        print(f"Redfish dump {host.name}: {elapsed:.0f}/{args.seconds:g}s, "
              f"{completed}/{args.max_requests} completed, {active} active, {pending} queued",
              file=sys.stderr, flush=True)

    try:
        snapshot = redfish_snapshot.capture(
            host.address, factory, metadata, (host.name, host.address, user, password),
            workers=args.workers, seconds=args.seconds, limit=args.max_requests, extra_paths=args.path,
            progress=progress if show_progress else None,
        )
    except KeyboardInterrupt:
        print("Redfish dump interrupted", file=sys.stderr)
        return 130
    snapshot["identity"] = scan.read_identity(redfish_snapshot.Replay(snapshot), metadata)
    redfish_snapshot.save(args.output, snapshot)
    stats = snapshot["capture"]
    label = "Snapshot" if stats["complete"] else "Partial snapshot"
    note = f" ({stats['stop_reason']})" if not stats["complete"] else ""
    print(f"{label}{note} saved to {args.output}: {len(snapshot['resources'])} requests in "
          f"{stats['seconds']:g}s; {stats['pending']} pending, "
          f"{stats['transport_errors']} transport errors")
    return 0


def replay(args):
    snapshot = redfish_snapshot.load(args.file)
    command = args.snapshot_tool
    if args.action == "pending" and command != "nodebios":
        raise NodetoolsError("offline pending is only available for nodebios")
    entry = SENSOR_COMMANDS.get(command)
    runner_kind = runtime.Runner
    if entry is None:
        entry = INFO_COMMANDS.get(command)
        runner_kind = runtime.InfoRunner
        if entry is not None and entry[1] not in _INFO_READS:
            entry = None
    if entry is None or (runner_kind is runtime.Runner and entry[0] == "nvswitch"):
        raise NodetoolsError(f"{command} is unavailable in offline Redfish replay")
    role, subject = entry
    metadata = {key: snapshot["device"][key] for key in _METADATA
                if isinstance(snapshot["device"].get(key), str)}
    if metadata.get("role") not in (None, "unknown", role):
        raise NodetoolsError(f"snapshot role {metadata['role']} does not match {command}")
    metadata["role"] = role
    host = Host("snapshot", address="snapshot", variables=metadata)
    transport = redfish_snapshot.Replay(snapshot)
    options = Options(args)
    options.jobs = 1
    options.action = "pending" if args.action == "pending" else None

    expected_rows = getattr(args, "expect_rows", None)
    expected_paths = getattr(args, "expect_path", None)
    strict = expected_rows is not None or expected_paths is not None
    if strict:
        if expected_rows is None or expected_rows < 1 or not expected_paths:
            raise NodetoolsError("strict replay requires positive --expect-rows and at least one --expect-path")
        if runner_kind is not runtime.Runner:
            raise NodetoolsError("strict replay is only available for Redfish sensor commands")
        if not all(metadata.get(field) for field in ("platform", "oem", "generation")):
            raise NodetoolsError("strict replay needs snapshot platform, oem and generation")
        expected_paths = [redfish_snapshot.resource_path(path) for path in expected_paths]
        if None in expected_paths:
            raise NodetoolsError("--expect-path must be a local Redfish resource")

    def factory(selected):
        if strict:
            return runtime.build_device(
                selected, options, apply_deadline=False, load_profile=runner._needs_profile(selected),
                redfish_factory=lambda *args, **kwargs: transport,
                ipmi_factory=lambda *args, **kwargs: None,
            )
        device = model.Device(selected, redfish_factory=lambda _: transport)
        device.options = options
        device.identity = scan.read_identity(transport, device.vars)
        return device

    runner = runner_kind(subject, options, device_factory=factory)
    result, = runner.stream([host])
    for line in render([result], summary=False, errors_only=False,
                       details=getattr(args, "details", False)):
        print(line)
    for label, paths in (("not captured", transport.missing), ("capture failed", transport.failed)):
        for path in sorted(paths):
            print(f"snapshot {label}: {path}", file=sys.stderr)
    if strict:
        return _verify_replay(result, transport.requests, expected_rows, expected_paths)
    return 1 if result.get("error") else 0


def _verify_replay(result, requests, expected_rows, expected_paths):
    found_rows = len(result.get("readings", ()))
    found_paths = [path for path, _ in requests]
    errors = []
    if result.get("error"):
        errors.append(result["error"])
    if found_rows != expected_rows:
        errors.append(f"expected {expected_rows} readings, got {found_rows}")
    if found_paths != expected_paths:
        errors.append(f"expected GETs {expected_paths}, got {found_paths}")
    for path, status in requests:
        if status is None or not 200 <= status < 300:
            errors.append(f"GET {path}: {status if status is not None else 'no captured response'}")
    if errors:
        for error in errors:
            print(f"snapshot verification failed: {error}", file=sys.stderr)
        return 1
    print(f"snapshot verified: {found_rows} readings, {len(requests)} GETs", file=sys.stderr)
    return 0
