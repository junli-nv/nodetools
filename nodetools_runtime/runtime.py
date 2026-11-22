"""Parallel execution engine: build devices, collect or control."""

import concurrent.futures
import time

from . import caps, model, oem, scan
from .errors import NodetoolsError
from .transport.ipmi import Ipmi
from .transport.redfish import Redfish

_CRED_KEYS = {
    "compute": ("bmc_user", "bmc_password"),
    "nvswitch": ("nvswitch_bmc_user", "nvswitch_bmc_password"),
    "powershelf": ("powershelf_bmc_user", "powershelf_bmc_password"),
    "unknown": ("bmc_user", "bmc_password"),
}

_FAN_ACTIONS = ("auto", "max")
_PSU_ACTIONS = ("on", "off", "cycle")


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


def build_device(host, options, apply_deadline=True):
    """Construct a Device for a host and attach its cached scan map (if any).

    When ``apply_deadline`` is set, the transports share one wall-clock budget
    (``options.node_timeout``) so a slow BMC cannot stall the whole run.
    """
    user, password = _resolve_creds(host, options)
    redfish = Redfish(host.address, user, password, timeout=options.timeout)
    ipmi = Ipmi(
        host.address,
        user,
        password,
        use_env=options.ipmi_env,
        timeout=options.timeout,
    )
    node_timeout = getattr(options, "node_timeout", None)
    if apply_deadline and node_timeout:
        deadline = time.monotonic() + float(node_timeout)
        redfish.set_deadline(deadline)
        ipmi.set_deadline(deadline)
    device = model.Device(
        host, redfish_factory=lambda h: redfish, ipmi_factory=lambda h: ipmi
    )
    if not getattr(options, "no_cache", False):
        device.profile = scan.load(host.address)
    return device


class Runner:
    def __init__(self, capability, options):
        self.capability = capability
        self.options = options

    def _device(self, host):
        return build_device(host, self.options)

    def _result(self, host, readings=None, error=None):
        return {
            "name": host.name,
            "role": host.role,
            "address": host.address,
            "cap": self.capability,
            "readings": readings or [],
            "error": error,
        }

    def _control(self, device, module):
        action = self.options.action
        if self.capability == "fan":
            if action not in _FAN_ACTIONS:
                raise NodetoolsError("invalid fan action {!r}".format(action))
            module.fan_control(device, action)
        elif self.capability == "psu":
            if action not in _PSU_ACTIONS:
                raise NodetoolsError("invalid psu action {!r}".format(action))
            if device.role == "powershelf":
                oem.power_shelf_control(device, action)
            else:
                module.psu_control(device, action)
        else:
            raise NodetoolsError(
                "action not supported for capability {!r}".format(self.capability)
            )

    def _one(self, host):
        if not host.address:
            return self._result(host, error="no BMC address (missing from inventory)")
        device = None
        try:
            device = self._device(host)
            module = oem.get("unknown")
            if self.options.action:
                module = oem.get(self._detect(device))
                self._control(device, module)
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
            collector = caps.collector_for(self.capability)
            readings = collector(device)
            if not readings:
                return self._result(
                    host, error="no readings (BMC unreachable or backend mismatch)"
                )
            return self._result(host, readings=readings)
        except Exception as exc:  # noqa: BLE001 - reported per node
            return self._result(host, error="{}: {}".format(type(exc).__name__, exc))
        finally:
            if device:
                device.close()

    def _classify(self, device):
        probe = model.probe_redfish(device.redfish)
        device.probe = probe
        return model.classify(probe)

    def _detect(self, device):
        if self.options.no_cache:
            return self._classify(device)
        return model.detect_odm(device)

    def run(self, hosts):
        jobs = max(1, self.options.jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(self._one, hosts))


class Scanner:
    """Probe each BMC's Redfish interface map and cache it (the ``scan`` cmd)."""

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
        if getattr(self.options, "clear", False):
            removed = scan.clear(host.address)
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
        if not host.address:
            return self._result(host, error="no BMC address (missing from inventory)")
        device = None
        try:
            device = build_device(host, self.options, apply_deadline=False)
            profile = scan.refresh(device)
            useful = sum(
                1
                for rec in profile["caps"].values()
                if any(rec.get(key) for key in scan.COUNTS)
            )
            return self._result(
                host,
                readings=[
                    {
                        "id": "map",
                        "name": "chassis",
                        "value": "{}chassis/{}data".format(
                            len(profile["chassis"]), useful
                        ),
                        "health": "OK",
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001 - reported per node
            return self._result(host, error="{}: {}".format(type(exc).__name__, exc))
        finally:
            if device:
                device.close()

    def run(self, hosts):
        jobs = max(1, self.options.jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(self._one, hosts))
