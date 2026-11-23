"""KAYTUS KR9288-X3-A0-F0-01 with the Aivres-compatible Redfish layout."""

from ...errors import CapUnsupported
from ...subjects import common, power
from .aivres import Aivres, _IDL, _SEL, _SYSTEM


class KaytusKr9288(Aivres):
    system_id = "1"
    bmc_id = "1"
    psu_read_chassis = ("1",)

    def power(self, device, action):
        resets = {
            "on": ("On",), "off": ("ForceOff",), "reset": ("ForceRestart",),
            "cycle": ("PowerCycle",),
        }
        if action not in ("status", *resets):
            raise CapUnsupported("no verified KR9288 power action {!r}".format(action))
        data = common.resource(device, _SYSTEM, required=True)
        if action == "status":
            return power.host_status(device.name, data.get("PowerState"))
        chosen = common.reset_system(device, _SYSTEM, data, resets[action])
        return [self._label(device, "Accepted " + chosen)]

    def eventlog(self, device, action):
        if action != "clear":
            return super().eventlog(device, action)
        targets = [
            common.action_target(common.resource(device, log, required=True), "LogService.ClearLog", log)[0]
            for log in (_SEL, _IDL)
        ]
        for target in targets:
            common.require_write_success(target, common.post(device, target))
        return [self._label(device, "Accepted clear SEL and IDL")]

    def boot(self, device, action):
        boot_target = {"pxe": "Pxe", "disk": "Hdd", "bios": "BiosSetup", "cdrom": "Cd"}
        target = boot_target.get(action or "pxe")
        if not target:
            raise CapUnsupported("no verified KR9288 boot target {!r}".format(action))
        boot = common.resource(device, _SYSTEM, required=True).get("Boot") or {}
        if target not in (boot.get("BootSourceOverrideTarget@Redfish.AllowableValues") or ()):
            raise CapUnsupported("BMC does not advertise boot target " + target)
        common.require_write_success(_SYSTEM, common.patch(device, _SYSTEM, {
            "Boot": {"BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": target},
        }))
        return [self._label(device, "One-time boot target staged: " + target)]

    def netboot(self, device, action):
        if action == "pxe":
            return self.boot(device, "pxe")
        if action == "mac":
            return self.macs(device, action)
        if action == "net":
            return self.bootorder(device, action)
        if action not in (None, "", "status"):
            raise CapUnsupported("no verified KR9288 netboot action {!r}".format(action))
        boot = common.resource(device, _SYSTEM, required=True).get("Boot") or {}
        state = ", ".join("{}={}".format(key, boot.get(key, "-")) for key in (
            "BootSourceOverrideEnabled", "BootSourceOverrideMode", "BootSourceOverrideTarget",
        ))
        return [self._label(device, state)]
