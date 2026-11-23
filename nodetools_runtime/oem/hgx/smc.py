"""Supermicro (FanMode control)."""

import time

from ...errors import CapUnsupported, TransportError
from ...subjects import common, power, vm
from ..base import Oem, _body_lines, _jq_r

_SYSTEM = "/redfish/v1/Systems/1"
_LOG = _SYSTEM + "/LogServices/Log1"
_BIOS_SD = _SYSTEM + "/Bios/SD"
_VM = "/redfish/v1/Managers/1/VirtualMedia"
_VM_IFACE = "VirtualMedia1"
_RESET = {
    "on": ("On",), "off": ("ForceOff",), "reset": ("ForceRestart",),
    "cycle": ("PowerCycle",), "reseat": ("FullPowerCycle", "ACCycle"),
}

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
    psu_read_chassis = ("1",)

    bootorder_system = "1"
    bios_system = "1"
    bios_write_target = "settings"
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False


    def fan_control(self, device, action):
        modes = {"auto": ("Optimal", "Standard"), "max": ("FullSpeed",)}
        if action not in modes:
            raise CapUnsupported("unsupported Supermicro fan action {!r}".format(action))
        path = "/redfish/v1/Managers/1/Oem/Supermicro/FanMode"
        data = common.resource(device, path, required=True)
        allowed = data.get("Mode@Redfish.AllowableValues") or []
        mode = next((mode for mode in modes[action] if mode in allowed), None)
        if mode is None:
            raise CapUnsupported("BMC does not advertise a mode for fan action " + action)
        common.require_write_success(path, common.patch(device, path, {"Mode": mode}))
        return "ok"

    def power(self, device, action):
        data = common.resource(device, _SYSTEM, required=True)
        if action in _RESET:
            chosen = common.reset_system(device, _SYSTEM, data, _RESET[action])
            return [self._label(device, "Accepted " + chosen)]
        return power.host_status(device.name, data.get("PowerState"))

    def eventlog(self, device, action):
        if action == "clear":
            data = common.resource(device, _LOG, required=True)
            target, _ = common.action_target(data, "LogService.ClearLog", _LOG)
            common.require_write_success(target, common.post(device, target))
            return [self._label(device, "Accepted clear Log1")]
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
            resp = common.require_write_success(_BIOS_SD, common.patch(device, _BIOS_SD, _BIOS_BASE))
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
        if action == "mac":
            return self.macs(device, action)
        if action == "net":
            return self._label_lines(device, network)
        if action in (None, "", "status"):
            return self._label_lines(device, values)
        if action != "pxe" or not network:
            raise CapUnsupported("no supported Supermicro PXE boot change")
        ordered = network + [item for item in values if item not in network]
        if ordered == values:
            return [self._label(device, "Network is already first in FixedBootOrder")]
        common.require_write_success(
            order_path, common.patch(device, order_path, {"FixedBootOrder": ordered})
        )
        return [self._label(device, "PXE priority staged. Reboot separately to apply.")]

    # -- virtual media (nodevm.smc) -------------------------------------
    vm_protocols = ("http", "https")
    vm_mount_steps = ("insert",)
    vm_extra_actions = ("stop",)

    def vm(self, device, action, options=None):
        action = (action or "status").strip().lower() or "status"
        if action in ("mount", "insert"):
            image, parsed = vm.image_source(options, "http", "https")
            slot = "{}/{}".format(_VM, _VM_IFACE)
            status, body = vm.post(
                device, slot + "/Actions/VirtualMedia.InsertMedia",
                {
                    "Image": image,
                    "WriteProtected": True,
                    "Inserted": True,
                    "TransferProtocolType": parsed.scheme.upper(),
                },
            )
            if status == 202:
                self._wait_for_media(device, body, slot)
                return [self._label(device, "Media inserted")]
            return self._label_lines(device, _body_lines(body, pretty=True))
        if action == "stop":
            image, _ = vm.image_source(options, *self.vm_protocols)
            items = common.resource(device, _VM, required=True)
            for oid in common.member_ids(items):
                iface = common.resource(device, oid, required=True)
                if iface.get("Image") == image:
                    return [self._label(device, self._detach(device, oid))]
            raise CapUnsupported("no Supermicro media slot matches the requested image")
        if action == "eject":
            lines = []
            coll = common.resource(device, _VM)
            for oid in common.member_ids(coll):
                iface = common.resource(device, oid)
                if iface.get("Image") in (None, "null"):
                    continue
                target = oid + "/Actions/VirtualMedia.EjectMedia"
                result = common.post(device, target)
                if result[0] in (404, 405):
                    lines.append(self._detach(device, oid))
                else:
                    lines += _body_lines(common.require_write_success(target, result), pretty=True)
            return self._label_lines(device, lines)
        lines = []
        coll = common.resource(device, _VM)
        for oid in common.member_ids(coll):
            iface = common.resource(device, oid)
            lines.append(
                "{}\t{}\t{}\tinserted={}".format(
                    _jq_r(iface.get("Id")),
                    _jq_r(iface.get("ImageName")),
                    _jq_r(iface.get("Image")),
                    _jq_r(iface.get("Inserted")),
                )
            )
        return self._label_lines(device, lines)

    @staticmethod
    def _detach(device, path):
        common.resource(device, path, required=True)
        common.require_write_success(path, common.patch(device, path, {"Inserted": False}))
        if common.resource(device, path, required=True).get("Inserted") is not False:
            raise CapUnsupported("BMC did not confirm the media was detached")
        return "Detached {}; image URL may remain configured".format(path.rsplit("/", 1)[-1])


    @staticmethod
    def _wait_for_media(device, task, slot):
        path = task.get("@odata.id") if isinstance(task, dict) else None
        if not isinstance(path, str) or not path.startswith("/redfish/v1/TaskService/Tasks/"):
            raise TransportError("BMC accepted virtual media asynchronously without a task link")
        for _ in range(20):
            data = common.resource(device, path, required=True)
            state = data.get("TaskState")
            if state == "Completed":
                if common.resource(device, slot, required=True).get("Inserted") is True:
                    return
                raise TransportError("BMC completed the task but did not report inserted media")
            if state not in ("New", "Starting", "Running", "Pending"):
                messages = [
                    m.get("Message") for m in data.get("Messages", [])
                    if isinstance(m, dict) and m.get("Message")
                ]
                raise TransportError("virtual media task {}: {}".format(
                    state or "unknown", "; ".join(messages) or path
                ))
            time.sleep(0.5)
        raise TransportError("virtual media task is still pending: " + path)
