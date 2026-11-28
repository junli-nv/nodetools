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

_INFO_READ_ACTIONS = {
    "bios": (None, "pending"),
    "bootorder": (None,),
    "eventlog": (None, "bmc", "hmc"),
    "inventory": (None,),
    "macs": (None,),
    "netboot": (None, "list", "status", "mac", "net"),
    "power": (None,),
    "vm": (None,),
}


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


def build_device(host, options, apply_deadline=True, load_profile=True, *,
                 redfish_factory=None, ipmi_factory=None):
    """Construct a Device for a host and attach its cached scan map (if any).

    The scan map is a per-machine-type template looked up from the device's
    identity, so a command only needs a cheap identity read to find it. When
    ``apply_deadline`` is set, the transports share one wall-clock budget
    (``options.node_timeout``) so a slow BMC cannot stall the whole run.
    ``load_profile=False`` skips the lookup (used by ``scan`` itself).
    """
    redfish_factory = redfish_factory or Redfish
    ipmi_factory = ipmi_factory or Ipmi
    user, password = _resolve_creds(host, options)
    read_timeout = options.timeout
    if not getattr(options, "timeout_explicit", False):
        read_timeout = _redfish_setting(host, options, "redfish_timeout", read_timeout)
    connect_timeout = _redfish_setting(
        host, options, "redfish_connect_timeout", DEFAULT_REDFISH_CONNECT_TIMEOUT
    )
    redfish = redfish_factory(host.address, user, password,
                              timeout=read_timeout, connect_timeout=connect_timeout)
    if getattr(options, "debug", False):
        redfish.debug_label = host.name
    ipmi = ipmi_factory(
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
        return build_device(host, self.options, load_profile=self._needs_profile(host))

    def _needs_profile(self, host):
        if self.capability in _PROFILELESS_CAPS:
            return False
        if (self.options.action or getattr(self.options, "no_cache", False)
                or not all(host.vars.get(field) for field in ("platform", "oem", "generation"))):
            return True
        if self.capability == "temp" and host.get("temp_chassis_id"):
            return False
        adapter = oem.for_device(model.Device(host), self.capability, configured_read=True)
        if (host.role == "nvswitch" and self.capability == "psu"
                and host.vars.get("nvswitch_sensor_backend") == "bmc"
                and getattr(adapter, "bmc_psu_collection", None)):
            return False
        return self.capability not in getattr(adapter, "inventory_fixed_caps", ())

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
            result["model"] = str(identity.get("model") or host.get("system_model") or "").strip() or None
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
                backend = host.vars.get("nvswitch_" + self.capability + "_backend",
                                        host.vars.get("nvswitch_sensor_backend", "nvos")).lower()
                if backend == "nvos":
                    readings = nvos.collect(host, self.capability, self.options)
                    return self._result(host, readings=readings)
                if backend != "bmc":
                    raise NodetoolsError("nvswitch_sensor_backend must be nvos or bmc")
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
            if self.capability in ("fan", "psu", "temp", "inlet", "monitor"):
                device.odm = oem.for_device(device, self.capability, configured_read=True)
                if self.capability == "monitor" and oem.hgx_read_adapter(device) is device.odm:
                    device.redfish.get = lru_cache(maxsize=None)(device.redfish.get)
            if device.vars.get("chassis_id"):
                device.ipmi = None
            if (host.role == "nvswitch" and self.capability == "psu"
                    and host.vars.get("nvswitch_sensor_backend") == "bmc"
                    and getattr(device.odm, "bmc_psu_collection", None)):
                chassis = device.vars.get("chassis_id") or device.odm.read_chassis_id
                path = device.odm.bmc_psu_collection.format(chassis)
                collection = device.redfish.get(path)
                if (isinstance(collection, dict)
                        and collection.get("@odata.id", path) == path
                        and collection.get("Members") == []
                        and collection.get("Members@odata.count") == 0):
                    return self._result(host, error=(
                        "BMC exposes no local PSU resources at {}; query the power shelf. "
                        "NVSwitch chip power is not PSU input power."
                    ).format(path), device=device)
                raise CapUnsupported("BMC PSU layout requires validation at " + path)
            collector = caps.collector_for(self.capability)
            readings = collector(device)
            if getattr(device.redfish, "read_error", None) is not None:
                raise device.redfish.read_error
            from .caps.fixed import completeness_error
            incomplete = completeness_error(device, self.capability, readings)
            if incomplete:
                return self._result(host, readings=readings, error=incomplete, device=device)
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

    def _needs_profile(self, host):
        if (getattr(self.options, "no_cache", False)
                or getattr(self.options, "bios_updates", None)
                or self.options.action not in _INFO_READ_ACTIONS.get(self.subject, ())
                or not all(host.vars.get(key) for key in ("platform", "oem", "generation"))):
            return True
        device = model.Device(host)
        if host.role != "compute":
            return not getattr(oem.for_device(device), "read_management_fixed", False)
        platform = model.platform_for(device)
        if platform == "unknown" or model.generation_for(device) == "unknown":
            return True
        if platform == "hgx":
            if oem.hgx_read_adapter(device) is not None:
                return False
            vendor = model.odm_for(device)
            if vendor in ("aivres", "kaytus"):
                return True
            if vendor in ("hpe", "cisco") and not host.vars.get("system_id"):
                return True
        return oem.for_device(device).name == "generic"

    def _device(self, host):
        if self.device_factory is not None:
            return self.device_factory(host)
        if self._needs_profile(host):
            return build_device(host, self.options)
        return build_device(host, self.options, load_profile=False)

    def _one(self, host):
        if not host.address:
            return self._result(host, error="no BMC address (missing from inventory)")
        device = None
        try:
            device = self._device(host)
            read = (self.options.action in _INFO_READ_ACTIONS.get(self.subject, ())
                    and not getattr(self.options, "bios_updates", None))
            device.odm = oem.for_device(device, configured_read=read)
            if getattr(device.odm, "read_management_fixed", False) and read:
                device.redfish.get = lru_cache(maxsize=None)(device.redfish.get)
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
            if getattr(device.redfish, "read_error", None) is not None:
                raise device.redfish.read_error
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
