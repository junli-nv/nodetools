"""Wistron GB300 tray (Sensor-based readings, GB200-style fan control)."""

import time

from ...errors import CapUnsupported, TransportError
from ...subjects import common, netboot, vm
from ...subjects.boot_nics import local_link, members
from ..base import Oem


class Wistron(Oem):
    name = "wistron"
    family = "nvl"
    fan_profile = "wistron-nvl"
    fan_chassis = ("Chassis_0",)
    fan_subsystem_chassis = fan_chassis
    fan_subsystem_count = 16
    psu_sensor_chassis = {"Chassis_0": 1, "PDB_0": 10}
    inlet_sensor = ("Chassis_0", "Chassis_0_Front_IO_Temp_0")
    inventory_fixed_caps = ("fan", "psu")
    bootorder_system = Oem.system_id
    bios_write_target = "settings"
    gpu_reports_module_serials = True
    monitor_chassis = "Chassis_0"
    monitor_power_sensor = "Chassis_0_TotalHSC_Power_0"
    monitor_hsc_chassis = "PDB_0"
    monitor_hsc_sensors = {
        "0": ("PDB_0_HSC_0_Pwr_0", "PDB 0 HSC 0 Pwr 0"),
        "1": ("PDB_0_HSC_1_Pwr_0", "PDB 0 HSC 1 Pwr 0"),
    }
    vm_protocols = ("smb",)
    vm_mount_steps = ("insert",)
    vm_extra_actions = ()

    def bios_path(self, device):
        return netboot.system_path(device) + "/Bios"

    def bios(self, device, action):
        if action == "bf3":
            raise CapUnsupported("the BF3 BIOS preset is unavailable for Wistron GB300")
        if action != "pending":
            return super().bios(device, action)
        path = self.bios_path(device)
        current = common.resource(device, path, required=True)
        settings = (current.get("@Redfish.Settings") or {}).get("SettingsObject") or {}
        target = local_link(settings.get("@odata.id"))
        if not target or not target.startswith(path + "/"):
            raise CapUnsupported("Wistron BMC did not advertise pending BIOS settings")
        rows = self._bios_rows_at(device, target, ("MAPIDS", "SETUP006"))
        if not rows:
            return self._label_lines(device, ["No pending BIOS attributes reported"])
        return self._label_lines(device, rows, spaced=False)

    def vm(self, device, action, options=None):
        system = netboot.system_path(device)
        if action == "status":
            return self._label_lines(device, vm.status_lines(device, [system]))

        parent = common.resource(device, system, required=True)
        collection = local_link(parent.get("VirtualMedia"))
        if not collection or not collection.startswith(system + "/"):
            raise CapUnsupported("Wistron system did not advertise VirtualMedia")
        slots = list(members(device, collection, required=True))
        occupied = [(path, data) for path, data in slots if data.get("Inserted") is True]

        if action == "insert":
            image, _ = vm.image_source(options, *self.vm_protocols)
            if any(self._vm_matches_image(data, image) for _, data in occupied):
                return self._label_lines(device, ["Image already mounted"])
            available = [(path, data) for path, data in slots
                         if data.get("Inserted") is False and not data.get("Image")]
            if not available:
                raise CapUnsupported("no empty Wistron VirtualMedia slot")
            path, data = available[0]
            payload = {"Image": image, "TransferProtocolType": "CIFS"}
            for key, option in (("UserName", "vm_user"), ("Password", "vm_password")):
                value = getattr(options, option, None)
                if value is not None:
                    payload[key] = value
            self._vm_write(device, path, data, "InsertMedia", payload)
            self._vm_readback(device, path, True, image)
            return self._label_lines(device, ["Mounted in VirtualMedia slot " + path.rsplit("/", 1)[-1]])

        if action != "eject":
            raise CapUnsupported("Wistron supports status, insert and eject")
        image = getattr(options, "image", None)
        if image:
            image, _ = vm.image_source(options, *self.vm_protocols)
            occupied = [(path, data) for path, data in occupied if self._vm_matches_image(data, image)]
        if len(occupied) != 1:
            raise CapUnsupported("expected one inserted Wistron image; specify its exact CIFS URL")
        path, data = occupied[0]
        self._vm_write(device, path, data, "EjectMedia", {})
        self._vm_readback(device, path, False)
        return self._label_lines(device, ["Ejected VirtualMedia slot " + path.rsplit("/", 1)[-1]])

    @staticmethod
    def _vm_write(device, path, data, action, payload):
        target = ((data.get("Actions") or {}).get("#VirtualMedia." + action) or {}).get("target")
        if target != path + "/Actions/VirtualMedia." + action:
            raise CapUnsupported("Wistron slot did not advertise " + action)
        vm.post(device, target, payload)

    @staticmethod
    def _vm_matches_image(state, image):
        reported = state.get("Image")
        # Wistron can report a CIFS location without its URL scheme.
        return reported in (image, image.removeprefix("smb://"))

    def _vm_readback(self, device, path, inserted, image=None):
        for attempt in range(5):
            if attempt:
                time.sleep(1)
            state = common.resource(device, path)
            if state.get("Inserted") is not inserted:
                continue
            if not inserted or self._vm_matches_image(state, image):
                return
        raise TransportError(f"Wistron VirtualMedia state did not match at {path}")
