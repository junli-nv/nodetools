"""Supermicro (FanMode control)."""

from ..subjects import common, power, vm
from .base import Oem, _body_lines, _jq_r

_SYSTEM = "/redfish/v1/Systems/1"
_LOG = _SYSTEM + "/LogServices/Log1"
_BIOS_SD = _SYSTEM + "/Bios/SD"
_VM = "/redfish/v1/Managers/1/VirtualMedia"
_VM_IFACE = "VirtualMedia1"
_RESET = {"on": "On", "off": "ForceOff", "reset": "ForceRestart", "reseat": "ACCycle"}

_BIOS_BASE = {
    "Attributes": {
        "SNC": "Disabled",
        "IntelVirtualizationTechnology": "Disabled",
    }
}
_MAC_FIELDS = (
    "Id",
    "Name",
    "Description",
    "MACAddress",
    "LinkStatus",
    "SpeedMbps",
)


class Smc(Oem):
    name = "smc"
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
        mode = "FullSpeed" if action == "max" else "Optimal"
        status, _ = device.redfish.patch(
            "/redfish/v1/Managers/1/Oem/Supermicro/FanMode",
            {"FanMode": mode},
        )
        if status in (200, 202, 204):
            return "ok"
        raise RuntimeError("FanMode PATCH failed")

    def power(self, device, action):
        if action in _RESET:
            common.post(
                device,
                _SYSTEM + "/Actions/ComputerSystem.Reset",
                {"ResetType": _RESET[action]},
            )
            return []
        data = common.resource(device, _SYSTEM, required=True)
        if action == "status":
            return power.host_status(device.name, data.get("PowerState"))
        return ["{}: {}".format(device.name, common.jq(data.get("PowerState")))]

    def eventlog(self, device, action):
        if action == "clear":
            text = self._clear_one(device, _LOG + "/Actions/LogService.ClearLog")
            return ["{}: {}".format(device.name, text)] if text else []
        lines = common.log_lines(device, _LOG + "/Entries", sep=" ")
        return ["{}: {}".format(device.name, line) for line in lines]

    # -- boot-family overrides ------------------------------------------
    def macs(self, device, action):
        lines = []
        for base in (
            "/redfish/v1/Systems/1/EthernetInterfaces",
            "/redfish/v1/Managers/1/EthernetInterfaces",
        ):
            for data in self._iface_members(device, base):
                row = ",".join(_jq_r(data.get(field, "")) for field in _MAC_FIELDS)
                lines.append(self._label(device, row))
        return lines

    def bios(self, device, action):
        if action == "base":
            _, resp = common.patch(device, _BIOS_SD, _BIOS_BASE)
            lines = [
                self._label(device, line) for line in _body_lines(resp, pretty=True)
            ]
            if device.ipmi:
                try:
                    out = device.ipmi.power("reset")
                except Exception:  # noqa: BLE001 - best-effort reset
                    out = ""
                lines.extend(
                    self._label(device, line)
                    for line in out.splitlines()
                    if line.strip()
                )
            return lines
        if action == "pending":
            rows = self._bios_rows_at(device, _BIOS_SD)
            return [self._label(device, row) for row in rows]
        rows = self._bios_rows(device, ("BootOption",), sort=True)
        return [self._label(device, row) for row in rows]

    def netboot(self, device, action):
        order_path = _SYSTEM + "/Oem/Supermicro/FixedBootOrder"
        data = common.resource(device, order_path, required=True)
        values = list(data.get("FixedBootOrder") or [])
        network = [item for item in values if "Network" in str(item)]
        rest = [item for item in values if "Network" not in str(item)]
        _, body = common.patch(device, order_path, {"FixedBootOrder": network + rest})
        lines = _body_lines(body, pretty=True)
        _, reset = common.post(
            device,
            _SYSTEM + "/Actions/ComputerSystem.Reset",
            {"ResetType": "GracefulRestart"},
        )
        lines.extend(_body_lines(reset, pretty=True))
        return [self._label(device, line) for line in lines]

    # -- virtual media (nodevm.smc) -------------------------------------
    vm_protocols = ("http", "https")
    vm_mount_steps = ("insert",)
    vm_extra_actions = ()

    def vm(self, device, action, options=None):
        action = (action or "status").strip().lower() or "status"
        if action in ("mount", "insert"):
            image, parsed = vm.image_source(options, "http", "https")
            _, body = vm.post(
                device,
                "{}/{}/Actions/VirtualMedia.InsertMedia".format(_VM, _VM_IFACE),
                {
                    "Image": image,
                    "WriteProtected": True,
                    "Inserted": True,
                    "TransferProtocolType": parsed.scheme.upper(),
                },
            )
            return self._label_lines(device, _body_lines(body, pretty=True))
        if action == "eject":
            lines = []
            coll = common.resource(device, _VM)
            for oid in common.member_ids(coll):
                iface = common.resource(device, oid)
                if iface.get("Image") in (None, "null"):
                    continue
                _, body = vm.post(device, oid + "/Actions/VirtualMedia.EjectMedia", {})
                lines += _body_lines(body, pretty=True)
            return self._label_lines(device, lines)
        lines = []
        coll = common.resource(device, _VM)
        for oid in common.member_ids(coll):
            iface = common.resource(device, oid)
            lines.append(
                "{}\t{}\t{}".format(
                    _jq_r(iface.get("Id")),
                    _jq_r(iface.get("ImageName")),
                    _jq_r(iface.get("Image")),
                )
            )
        return self._label_lines(device, lines)
