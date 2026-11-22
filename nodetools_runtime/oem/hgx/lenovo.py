"""Lenovo XCC (FanSpeedBoost control)."""

import json

from ...errors import CapUnsupported
from ...subjects import common, power
from ..base import FIRMWARE, Oem, _body_lines, _jq_r, _members_static

_SYSTEM = "/redfish/v1/Systems/1"
_PLATFORM_LOG = _SYSTEM + "/LogServices/PlatformLog"
_RESET = {"on": "On", "off": "ForceOff"}

_BIOS_BASE = {
    "Attributes": {
        "Processors_SNC": "Disabled",
        "Processors_IntelVirtualizationTechnology": "Disabled",
        "SystemSettings_WorkloadProfile": "HighPerformanceComputing",
    }
}
_BIOS_EXCLUDES = ("BootOrder", "MACAddress", "NvidiaNetworkAdapter")


class Lenovo(Oem):
    name = "lenovo"
    family = "hgx"
    fan_chassis = ("1",)

    bootorder_system = "1"
    bios_system = "1"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    def fan_control(self, device, action):
        if action not in ("auto", "max"):
            raise NotImplementedError(action)
        value = "High" if action == "max" else "Normal"
        for chassis in self.fan_chassis:
            status, _ = device.redfish.patch(
                "/redfish/v1/Chassis/{}".format(chassis),
                {"Oem": {"Lenovo": {"FanSpeedBoost": value}}},
            )
            if status in (200, 202, 204):
                return "ok"
        raise RuntimeError("FanSpeedBoost PATCH failed")

    def power(self, device, action):
        if action == "reseat":
            raise CapUnsupported("lenovo reseat requires onecli (not available)")
        if action == "cycle":
            data = common.resource(device, _SYSTEM, required=True)
            reset = "ForceRestart" if data.get("PowerState") == "On" else "On"
            return self._reset(device, reset)
        if action in _RESET:
            return self._reset(device, _RESET[action])
        data = common.resource(device, _SYSTEM, required=True)
        if action == "status":
            return power.host_status(device.name, data.get("PowerState"))
        return ["{}: PowerState={}".format(device.name, data.get("PowerState"))]

    def _reset(self, device, reset_type):
        status, _ = common.post(
            device, _SYSTEM + "/Actions/ComputerSystem.Reset", {"ResetType": reset_type}
        )
        if 200 <= status < 300:
            return [
                "{}: Reset action '{}' executed successfully.".format(
                    device.name, reset_type
                )
            ]
        return [
            "{}: Reset action '{}' failed with HTTP code {}".format(
                device.name, reset_type, status
            )
        ]

    def eventlog(self, device, action):
        if action == "clear":
            text = self._clear_one(
                device, _PLATFORM_LOG + "/Actions/LogService.ClearLog"
            )
            return ["{}: {}".format(device.name, text)] if text else []
        lines = common.log_lines(device, _PLATFORM_LOG + "/Entries", sep=" ")
        return ["{}: {}".format(device.name, line) for line in lines]

    def firmware_inventory(self, device):
        rows = []
        for data in _members_static(device, FIRMWARE):
            line = "{} {}".format(data.get("Id", ""), data.get("Version", ""))
            if "Pending" not in line:
                rows.append(line)
        rows.sort()
        return ["{}: {}".format(device.name, row) for row in rows]

    # -- boot-family overrides ------------------------------------------
    def macs(self, device, action):
        lines = []
        for adapter in self._iface_members(
            device, "/redfish/v1/Chassis/1/NetworkAdapters"
        ):
            head = "{},{}".format(
                _jq_r(adapter.get("Model", "")), _jq_r(adapter.get("Name", ""))
            )
            for controller in adapter.get("Controllers") or []:
                links = controller.get("Links") if isinstance(controller, dict) else None
                for port in (links or {}).get("Ports") or []:
                    oid = port.get("@odata.id") if isinstance(port, dict) else None
                    if not oid:
                        continue
                    data = device.redfish.get(oid)
                    if not isinstance(data, dict) or "error" in data:
                        continue
                    parts = [
                        _jq_r(data.get("@odata.id", "")),
                        _jq_r(
                            ((data.get("Oem") or {}).get("Lenovo") or {}).get(
                                "PermanentAddress", ""
                            )
                        ),
                        _jq_r(data.get("LinkNetworkTechnology", "")),
                        _jq_r(data.get("LinkStatus", "")),
                        _jq_r(data.get("CurrentSpeedGbps", "")),
                    ]
                    lines.append(self._label(device, "{},{}".format(head, ",".join(parts))))
        return lines

    def bios(self, device, action):
        if action == "base":
            _, resp = common.patch(device, _SYSTEM + "/Bios/Pending", _BIOS_BASE)
            return [self._label(device, line) for line in _body_lines(resp, pretty=True)]
        rows = self._bios_rows(device, _BIOS_EXCLUDES, sort=True)
        return [self._label(device, row) for row in rows]

    def netboot(self, device, action):
        pending = _SYSTEM + "/Pending"
        lines = []
        _, body = common.patch(
            device,
            _SYSTEM,
            {
                "Boot": {
                    "BootSourceOverrideEnabled": "Once",
                    "BootSourceOverrideTarget": "Pxe",
                }
            },
        )
        lines.extend(_body_lines(body))
        targets = []
        for member in self._boot_members(device, True):
            if "Network" in (member.get("UefiDevicePath") or "") and member.get("Id"):
                targets.append(json.dumps(member["Id"]))
        order = self._boot_order(device, quoted=True)
        new = targets + [item for item in order if item not in targets]
        lines.append("OLD_BOOT_ORDER={}".format(",".join(order)))
        lines.append("NEW_BOOT_ORDER={}".format(",".join(new)))
        lines.append("Applying the NEW_BOOT_ORDER")
        _, resp = common.patch(device, pending, {"Boot": {"BootOrder": new}})
        lines.extend(_body_lines(resp, pretty=True))
        lines.append("Pending boot option, restart server to take effect")
        data = common.resource(device, pending, required=True)
        final = (data.get("Boot") or {}).get("BootOrder") or []
        lines.append(",".join(json.dumps(item) for item in final))
        return [self._label(device, line) for line in lines]
