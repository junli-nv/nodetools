"""Dell iDRAC (racadm + OME-PMP telemetry)."""

import re

from ...subjects import common, power, vm
from ...transport.racadm import Racadm
from ..base import Oem

_SEL = "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel"
_SQUEEZE = re.compile(r"\s+")

_SYSTEM = "/redfish/v1/Systems/System.Embedded.1"
_IFACE = _SYSTEM + "/EthernetInterfaces/"
_NET_PXE = (
    ("set", "BIOS.NetworkSettings.NumberOfPxeDevices", "4"),
    ("set", "BIOS.NetworkSettings.PxeDev1EnDis", "Enabled"),
    ("set", "BIOS.NetworkSettings.PxeDev2EnDis", "Enabled"),
    ("set", "BIOS.NetworkSettings.PxeDev3EnDis", "Disabled"),
    ("set", "BIOS.NetworkSettings.PxeDev4EnDis", "Disabled"),
    ("set", "BIOS.PxeDev1Settings.PxeDev1Interface", "NIC.Slot.6-1-1"),
    ("set", "BIOS.PxeDev2Settings.PxeDev2Interface", "NIC.Slot.6-2-1"),
    ("set", "BIOS.NetworkSettings.HttpDev1EnDis", "Disabled"),
    ("set", "BIOS.NetworkSettings.HttpDev2EnDis", "Disabled"),
    ("set", "BIOS.NetworkSettings.HttpDev3EnDis", "Disabled"),
    ("set", "BIOS.NetworkSettings.HttpDev4EnDis", "Disabled"),
    ("jobqueue", "create", "BIOS.Setup.1-1", "-r", "pwrcycle", "-s", "TIME_NOW", "-e", "TIME_NA"),
    ("set", "iDRAC.ServerBoot.FirstBootDevice", "PXE"),
    ("set", "iDRAC.ServerBoot.BootOnce", "Disabled"),
    ("serveraction", "powerup"),
    ("jobqueue", "view"),
)


class Dell(Oem):
    name = "dell"
    family = "hgx"
    fan_chassis = ("System.Embedded.1",)

    bootorder_system = "System.Embedded.1"
    bootorder_fields = ("Id", "DisplayName")
    bootorder_quoted = False
    bootorder_sep = ","
    bootorder_expand = False

    _LICENSE_TOKENS = (
        "License #",
        "Status",
        "License Description",
        "License Type",
        "Expiration",
    )

    def fan_control(self, device, action):
        if action == "max":
            self._racadm(device, "set", "system.thermalsettings.ThermalProfile", "Maximum Performance")
        elif action == "auto":
            self._racadm(device, "set", "system.thermalsettings.ThermalProfile", "Default Thermal Profile")
        else:
            raise NotImplementedError(action)
        return "ok"

    def power(self, device, action):
        if action == "status":
            path = "/redfish/v1/Systems/" + self.bootorder_system
            state = common.resource(device, path).get("PowerState")
            if state:
                return power.host_status(device.name, state)
        return super().power(device, action)

    def eventlog(self, device, action):
        if action == "clear":
            text = self._clear_one(device, _SEL + "/Actions/LogService.ClearLog")
            return ["{}: {}".format(device.name, text)] if text else []
        lines = []
        for entry in common.entries(device, _SEL + "/Entries"):
            row = "{} {} {}".format(
                entry.get("Created", ""),
                entry.get("Severity", ""),
                entry.get("Message", ""),
            )
            lines.append(_SQUEEZE.sub(" ", row).strip())
        return ["{}: {}".format(device.name, line) for line in lines]

    # -- boot-family overrides ------------------------------------------
    def _racadm(self, device, *args):
        racadm = Racadm(
            device.address, device.cred("bmc_user"), device.cred("bmc_password")
        )
        if getattr(getattr(device, "options", None), "debug", False):
            racadm.debug_label = device.name
        return racadm.run(*args)

    def macs(self, device, action):
        rows = self._iface_rows(
            device, _IFACE, ("Id", "MACAddress", "UefiDevicePath")
        )
        return sorted(self._label(device, row) for row in rows)

    def bios(self, device, action):
        out = self._racadm(device, "get", "bios.SysProfileSettings")
        return [
            self._label(device, line) for line in out.splitlines() if line.strip()
        ]

    def netboot(self, device, action):
        if action == "mac":
            rows = self._iface_rows(
                device, _IFACE, ("Id", "MACAddress", "UefiDevicePath")
            )
            return [self._label(device, row) for row in rows]
        if action == "net":
            lines = []
            for command in _NET_PXE:
                out = self._racadm(device, *command)
                lines.extend(
                    self._label(device, line)
                    for line in out.splitlines()
                    if line.strip()
                )
            return lines
        lines = []
        for index in range(1, 5):
            endis = self._racadm_field(
                device,
                "BIOS.NetworkSettings.PxeDev{}EnDis".format(index),
                "PxeDev{}EnDis".format(index),
            )
            iface = self._racadm_field(
                device,
                "BIOS.PxeDev{}Settings.PxeDev{}Interface".format(index, index),
                "PxeDev{}Interface".format(index),
            )
            lines.append(self._label(device, "{}\t{}".format(endis, iface)))
        return lines

    def _racadm_field(self, device, key, token):
        out = self._racadm(device, "get", key)
        for line in out.splitlines():
            if token in line:
                return line.replace("\r", "")
        return ""

    # -- virtual media (nodevm.dell / racadm remoteimage) ----------------
    vm_protocols = ("http", "https")
    vm_eject_action = "umount"
    vm_extra_actions = ("enable", "lic")

    def vm(self, device, action, options=None):
        action = (action or "status").strip().lower() or "status"
        if action == "lic":
            out = self._racadm(device, "license", "view")
            rows = []
            for line in out.splitlines():
                if any(token in line for token in self._LICENSE_TOKENS):
                    rows.append(line.replace("\t", "").replace("\r", ""))
            return self._label_lines(device, rows)
        if action == "enable":
            out = self._racadm(device, "set", "iDRAC.VirtualMedia.Enable", "1")
        elif action in ("mount", "insert"):
            image, _ = vm.image_source(options, "http", "https")
            out = self._racadm(device, "remoteimage", "-c", "-l", image)
        elif action == "umount":
            out = self._racadm(device, "remoteimage", "-d")
        else:
            out = self._racadm(device, "remoteimage", "-s")
        return self._label_lines(
            device, [line for line in out.splitlines() if line.strip()]
        )
