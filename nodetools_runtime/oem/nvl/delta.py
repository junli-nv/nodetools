"""Read-only Delta NVL power shelf adaptations."""

from ...caps import common as sensor_common
from ...config import DEFAULT_CHASSIS
from ...errors import AuthError, CapUnsupported, TransportError, TransportTimeout
from ...subjects import common, inventory
from ..redfish import RedfishDiscovery

_PSU_NAME_HINTS = ("psu", "hsc", "pwr", "power supply")


class DeltaPowerShelf(RedfishDiscovery):
    name = "delta"

    def infrastructure_inventory(self, device):
        discovery_error = None
        try:
            rows = inventory._firmware_rows(device, self._firmware_inventory_path(device))
        except (AuthError, TransportTimeout):
            raise
        except (CapUnsupported, TransportError) as exc:
            discovery_error = exc
            rows = []
        if rows:
            return inventory._format_firmware(device, rows)

        manager = "/redfish/v1/Managers/" + (device.vars.get("manager_id") or "PMC_0")
        chassis = device.vars.get("chassis_id") or "chassis"
        supplies = "/redfish/v1/Chassis/" + chassis + "/PowerSubsystem/PowerSupplies"
        controller = common.resource(device, manager)
        if controller.get("FirmwareVersion"):
            rows.append([controller.get("Id") or "PMC_0", controller["FirmwareVersion"],
                         manager, "FirmwareVersion"])
        for path in common.member_ids(common.resource(device, supplies)):
            if not path.startswith(supplies + "/"):
                continue
            supply = common.resource(device, path)
            if supply.get("FirmwareVersion"):
                ident = supply.get("Id") or path.rsplit("/", 1)[-1]
                rows.append(["PSU_" + str(ident), supply["FirmwareVersion"],
                             path, "FirmwareVersion"])
        if not rows and discovery_error is not None:
            raise discovery_error
        return inventory._format_firmware(device, rows)


def legacy_psu_readings(device):
    rf = device.redfish

    def scan(cid):
        data = rf.get(
            "/redfish/v1/Chassis/{}/Oem/deltaenergysystems/AllSensors".format(cid)
        )
        if not isinstance(data, dict):
            return []
        out = []
        for sensor in data.get("Sensors", []) or []:
            dtype = (sensor.get("ReadingType") or "").lower()
            name = (sensor.get("DeviceName") or sensor.get("SensorName") or "").lower()
            if "power" not in dtype and not any(h in name for h in _PSU_NAME_HINTS):
                continue
            out.append(
                sensor_common.reading(
                    sensor.get("SensorName") or sensor.get("DeviceName"),
                    sensor.get("DeviceName"),
                    sensor_common.num(sensor.get("Reading")),
                    sensor.get("State") or "Unknown",
                )
            )
        return out

    chassis = sensor_common.chassis_list(device, DEFAULT_CHASSIS)
    return sensor_common.dedup_readings(sensor_common.flatten(sensor_common.map_chassis(scan, chassis)))
