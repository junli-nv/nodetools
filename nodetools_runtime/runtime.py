"""Parallel execution engine: build devices, collect or control."""

import concurrent.futures
import math
import time
from functools import lru_cache

from . import caps, fan_profiles, model, oem, scan, subjects
from .caps import nvos
from .config import DEFAULT_REDFISH_CONNECT_TIMEOUT
from .diagnostics import debug
from .subjects import netboot
from .errors import CapUnsupported, NodetoolsError
from .transport.ipmi import Ipmi
from .transport.redfish import Redfish

_CRED_KEYS = {
    "compute": ("bmc_user", "bmc_password"),
    "nvswitch": ("nvswitch_bmc_user", "nvswitch_bmc_password"),
    "powershelf": ("powershelf_bmc_user", "powershelf_bmc_password"),
    "unknown": ("bmc_user", "bmc_password"),
}


# Capabilities whose fast path never consults the cached scan template. The
# inlet check reads one chassis directly, so skipping the template skips the
# identity read (a few extra BMC round-trips per node).
_PROFILELESS_CAPS = ("inlet",)


def _resolve_creds(host, options):
    role = host.role
    user_key, pass_key = _CRED_KEYS.get(role, _CRED_KEYS["unknown"])
    user = options.user or host.vars.get(user_key) or host.vars.get("bmc_user")
    password = (
        options.password
        if options.password is not None
        else host.vars.get(pass_key) or host.vars.get("bmc_password")
    )
    return user, password


def _redfish_setting(host, options, field, fallback):
    value = host.vars.get(field, getattr(options, field, None))
    if value is None:
        return fallback
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise NodetoolsError(f"{field} for {host.name} must be a finite positive number") from None
    return seconds


def build_device(host, options, apply_deadline=True, load_profile=True):
    """Construct a Device for a host and attach its cached scan map (if any).

    The scan map is a per-machine-type template looked up from the device's
    identity, so a command only needs a cheap identity read to find it. When
    ``apply_deadline`` is set, the transports share one wall-clock budget
    (``options.node_timeout``) so a slow BMC cannot stall the whole run.
    ``load_profile=False`` skips the lookup (used by ``scan`` itself).
    """
    user, password = _resolve_creds(host, options)
    read_timeout = options.timeout
    if not getattr(options, "timeout_explicit", False):
        read_timeout = _redfish_setting(host, options, "redfish_timeout", read_timeout)
    connect_timeout = _redfish_setting(
        host, options, "redfish_connect_timeout", DEFAULT_REDFISH_CONNECT_TIMEOUT
    )
    redfish = Redfish(host.address, user, password, timeout=read_timeout, connect_timeout=connect_timeout)
    if getattr(options, "debug", False):
        redfish.debug_label = host.name
    ipmi = Ipmi(
        host.address,
        user,
        password,
        use_env=options.ipmi_env,
        timeout=options.timeout,
    )
    if getattr(options, "debug", False):
        ipmi.debug_label = host.name
    node_timeout = getattr(options, "node_timeout", None)
    if apply_deadline and node_timeout:
        deadline = time.monotonic() + float(node_timeout)
        redfish.set_deadline(deadline)
        ipmi.set_deadline(deadline)
    device = model.Device(
        host, redfish_factory=lambda h: redfish, ipmi_factory=lambda h: ipmi
    )
    # Expose run options to collectors that need a parameter (e.g. the inlet
    # target/tolerance in ``caps.inlet``); most collectors ignore this.
    device.options = options
    if load_profile:
        if getattr(options, "no_cache", False):
            device.identity = scan.read_identity(device.redfish, device.vars)
        else:
            device.profile = scan.load(device)
    return device


class Runner:
    def __init__(self, capability, options, device_factory=None):
        self.device_factory = device_factory
        self.capability = capability
        self.options = options

    def _device(self, host):
        if self.device_factory is not None:
            return self.device_factory(host)
        return build_device(
            host,
            self.options,
            load_profile=self.capability not in _PROFILELESS_CAPS,
        )

    def _result(self, host, readings=None, error=None, device=None):
        result = {
            "name": host.name,
            "role": host.role,
            "address": host.address,
            "cap": self.capability,
            "readings": readings or [],
            "error": error,
        }
        if self.capability == "monitor":
            identity = model.hardware_identity(device)
            result["model"] = str(identity.get("model") or "").strip() or None
        return result

    def _control(self, device, module):
        action = self.options.action
        if self.capability != "fan":
            raise NodetoolsError("action not supported for capability {!r}".format(self.capability))
        local = getattr(self.options, "fan_profiles", None)
        dry_run = getattr(self.options, "fan_dry_run", False)
        if local is not None:
            profile = fan_profiles.for_model(device, local)
            if profile is None:
                raise CapUnsupported("no local fan profile matches the detected model")
            if dry_run:
                return fan_profiles.preview(profile, action)
            fan_profiles.execute(device, profile, action)
            return None
        if action not in module.fan_actions:
            raise NodetoolsError("invalid fan action {!r}".format(action))
        if dry_run:
            if module.fan_profile:
                profile = fan_profiles.bundled()[module.fan_profile]
                return fan_profiles.preview(profile, action)
            if type(module).fan_control is oem.Oem.fan_control:
                raise CapUnsupported("fan control is not supported by " + module.name)
            return ["{} {}: OEM adapter selects the mode at execution".format(module.name, action)]
        module.fan_control(device, action)

    def _one(self, host):
        device = None
        try:
            if host.role == "nvswitch" and self.capability in ("psu", "temp"):
                if self.options.action:
                    raise CapUnsupported("NVOS sensor queries are read-only")
                readings = nvos.collect(host, self.capability, self.options)
                return self._result(host, readings=readings)
            if not host.address:
                return self._result(host, error="no BMC address (missing from inventory)")
            device = self._device(host)
            if self.options.action:
                module = oem.for_device(device, self.capability)
                plan = self._control(device, module)
                if plan is not None:
                    return dict(self._result(host), fan_plan=plan)
                return self._result(
                    host,
                    readings=[
                        {
                            "id": self.options.action,
                            "name": self.capability,
                            "value": self.options.action,
                            "health": "OK",
                        }
                    ],
                )
            if self.capability == "monitor":
                device.redfish.get = lru_cache(maxsize=None)(device.redfish.get)
            if self.capability in ("fan", "psu", "monitor"):
                device.odm = oem.for_device(device, self.capability)
            if device.vars.get("chassis_id"):
                device.ipmi = None
            collector = caps.collector_for(self.capability)
            readings = collector(device)
            if not readings:
                return self._result(
                    host, error="no readings (BMC unreachable or backend mismatch)", device=device
                )
            return self._result(host, readings=readings, device=device)
        except Exception as exc:  # noqa: BLE001 - reported per node
            return self._result(host, error="{}: {}".format(type(exc).__name__, exc), device=device)
        finally:
            if device:
                device.close()

    def stream(self, hosts):
        """Yield results as each node finishes (completion order, no sorting)."""
        jobs = max(1, self.options.jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(self._one, host) for host in hosts]
            for future in concurrent.futures.as_completed(futures):
                yield future.result()

    def run(self, hosts):
        return list(self.stream(hosts))


class InfoRunner:
    """Run an info subject (power/eventlog/inventory) and return verbatim lines."""

    def __init__(self, subject, options, device_factory=None):
        self.device_factory = device_factory
        self.subject = subject
        self.options = options
        self.handler = subjects.handler_for(subject)

    def _result(self, host, lines=None, error=None):
        return {
            "name": host.name,
            "role": host.role,
            "address": host.address,
            "cap": self.subject,
            "lines": lines or [],
            "error": error,
        }

    def _one(self, host):
        if not host.address:
            return self._result(host, error="no BMC address (missing from inventory)")
        device = None
        try:
            device = (self.device_factory(host) if self.device_factory is not None
                      else build_device(host, self.options))
            device.odm = oem.for_device(device)
            supported = getattr(device.odm, "info_subjects", None)
            network_discovery = self.subject == "netboot" and self.options.action != "pxe"
            if supported is not None and self.subject not in supported and not network_discovery:
                raise CapUnsupported("{} has no verified {} adapter".format(
                    device.name, self.subject
                ))
            plans = getattr(self.options, "netboot_plans", None)
            if self.subject == "netboot" and plans is not None:
                lines = netboot.apply(device, plans[host.name])
            else:
                lines = self.handler(device, self.options.action, self.options)
            if isinstance(lines, netboot.Plan):
                return dict(self._result(host, lines=netboot.describe(device, lines)), netboot_plan=lines)
            if not lines and device.transport_errors:
                return self._result(
                    host,
                    error="BMC unreachable (no response from {})".format(host.address),
                )
            return self._result(host, lines=lines)
        except Exception as exc:  # noqa: BLE001 - reported per node
            return self._result(host, error="{}: {}".format(type(exc).__name__, exc))
        finally:
            if device:
                device.close()

    def stream(self, hosts):
        """Yield results as each node finishes (completion order, no sorting)."""
        jobs = max(1, self.options.jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(self._one, host) for host in hosts]
            for future in concurrent.futures.as_completed(futures):
                yield future.result()

    def run(self, hosts):
        return list(self.stream(hosts))


class Scanner:
    """Probe a BMC's Redfish map and cache it per machine type (``scan`` cmd)."""

    def __init__(self, options):
        self.options = options

    def _result(self, host, readings=None, error=None):
        return {
            "name": host.name,
            "role": host.role,
            "address": host.address,
            "cap": "scan",
            "readings": readings or [],
            "error": error,
        }

    def _one(self, host):
        if not host.address:
            return self._result(host, error="no BMC address (missing from inventory)")
        device = None
        try:
            device = build_device(
                host, self.options, apply_deadline=False, load_profile=False
            )
            if getattr(self.options, "clear", False):
                removed = scan.clear(device)
                debug(getattr(self.options, "debug", False), "scan:" + host.name,
                      "cache {}".format("cleared" if removed else "absent"))
                return self._result(
                    host,
                    readings=[
                        {
                            "id": "cache",
                            "name": "cache",
                            "value": "cleared" if removed else "absent",
                            "health": "OK" if removed else "Unknown",
                        }
                    ],
                )
            profile = scan.refresh(device)
            debug(getattr(self.options, "debug", False), "scan:" + host.name,
                  "cached {} chassis resource(s)".format(len(profile["chassis"])))
            useful = sum(
                1
                for rec in profile["caps"].values()
                if any(rec.get(key) for key in scan.COUNTS)
            )
            result = self._result(
                host,
                readings=[
                    {
                        "id": "map",
                        "name": "chassis",
                        "value": "{}chassis/{}data".format(
                            len(profile["chassis"]), useful
                        ),
                        "health": "OK",
                    },
                    {
                        "id": "hardware",
                        "name": "classification",
                        "value": "platform={}/oem={}/role={}/generation={}".format(
                            profile["platform"], profile["odm"], host.role,
                            profile["generation"]
                        ),
                        "health": "OK",
                    },
                ],
            )
            result["cache_path"] = profile["cache_path"]
            return result
        except Exception as exc:  # noqa: BLE001 - reported per node
            return self._result(host, error="{}: {}".format(type(exc).__name__, exc))
        finally:
            if device:
                device.close()

    def stream(self, hosts):
        """Yield results as each node finishes (completion order, no sorting)."""
        jobs = max(1, self.options.jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(self._one, host) for host in hosts]
            for future in concurrent.futures.as_completed(futures):
                yield future.result()

    def run(self, hosts):
        return list(self.stream(hosts))
