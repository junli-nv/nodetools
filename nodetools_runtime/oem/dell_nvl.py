"""Dell NVL compute tray support via Redfish resource discovery."""

from ..errors import CapUnsupported
from ..subjects import common
from .base import Oem


class DellNvl(Oem):
    name = "dell"
    family = "nvl"
    vm_write_supported = False

    def vm(self, device, action, options=None):
        media_paths = set()
        lines = []
        for collection in ("Managers", "Systems"):
            parents = common.resource(device, "/redfish/v1/" + collection)
            for parent_path in common.member_ids(parents):
                parent = common.resource(device, parent_path)
                media = parent.get("VirtualMedia")
                if not isinstance(media, dict) or not media.get("@odata.id"):
                    continue
                members = common.resource(device, media["@odata.id"], required=True)
                for path in common.member_ids(members):
                    if path in media_paths:
                        continue
                    media_paths.add(path)
                    data = common.resource(device, path, required=True)
                    lines.append("{}\tinserted={}\timage={}".format(
                        data.get("Id") or path, data.get("Inserted", "unknown"),
                        data.get("ImageName") or data.get("Image") or "-",
                    ))
        if not lines:
            raise CapUnsupported("Dell NVL BMC did not advertise any VirtualMedia devices")
        return self._label_lines(device, lines)
