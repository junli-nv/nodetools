from types import SimpleNamespace

import pytest

from nodetools_runtime import model, oem
from nodetools_runtime.errors import CapUnsupported, TransportError
from nodetools_runtime.inventory import Host
from nodetools_runtime.subjects import boot

SYSTEM = "/redfish/v1/Systems/Tray1"


def make_device(*, ipmi_output=None, system_id=None, ambiguous=False, modes=None, patch_status=204):
    vars_ = {"system_id": system_id} if system_id is not None else {}
    device = model.Device(Host("tray-01", variables=vars_, groups=["compute"]))
    calls = []

    def run(*args):
        calls.append(("ipmi", args))
        if ipmi_output is None:
            raise TransportError("IPMI timeout")
        return ipmi_output

    def get(path):
        calls.append(("get", path))
        if path == boot.SYSTEMS:
            members = [{"@odata.id": SYSTEM}]
            if ambiguous:
                members.append({"@odata.id": boot.SYSTEMS + "/Tray2"})
            return {"Members": members}
        if path == SYSTEM:
            return {"Boot": {
                "BootSourceOverrideTarget@Redfish.AllowableValues": ["Pxe", "Hdd", "BiosSetup", "Cd"],
                "BootSourceOverrideEnabled@Redfish.AllowableValues": modes,
                "BootOrder": ["Boot0001", "Boot0002"],
            }}
        return None

    def patch(path, body, etag_source=None):
        calls.append(("patch", path, body, etag_source))
        return patch_status, {}

    device.ipmi = SimpleNamespace(run=run)
    device.redfish = SimpleNamespace(get=get, patch=patch)
    device.odm = oem.get("unknown")
    return device, calls


@pytest.mark.parametrize(("action", "target"), [
    ("pxe", "Pxe"), ("disk", "Hdd"), ("bios", "BiosSetup"), ("cdrom", "Cd"),
])
def test_ipmi_failure_uses_only_a_redfish_once_override(action, target):
    device, calls = make_device()
    assert "Redfish fallback: " + target in boot.collect(device, action, None)[0]
    assert calls == [
        ("ipmi", ("chassis", "bootdev", action)),
        ("get", boot.SYSTEMS),
        ("get", SYSTEM),
        ("patch", SYSTEM, {"Boot": {
            "BootSourceOverrideEnabled": "Once", "BootSourceOverrideTarget": target,
        }}, SYSTEM),
    ]


def test_ipmi_success_never_uses_redfish():
    device, calls = make_device(ipmi_output="")
    assert "via IPMI" in boot.collect(device, None, None)[0]
    assert calls == [("ipmi", ("chassis", "bootdev", "pxe"))]


def test_scoped_system_skips_unscoped_ipmi():
    device, calls = make_device(system_id="Tray1", ambiguous=True)
    assert "via Redfish: Pxe" in boot.collect(device, None, None)[0]
    assert [call[0] for call in calls] == ["get", "patch"]


@pytest.mark.parametrize(("params", "reason"), [
    ({"ambiguous": True}, "configure system_id"),
    ({"modes": ["Continuous"]}, "does not advertise the Once"),
    ({"patch_status": 400}, "HTTP 400"),
])
def test_redfish_failure_reports_both_errors(params, reason):
    device, calls = make_device(**params)
    with pytest.raises(TransportError, match=r"IPMI bootdev failed.*IPMI timeout.*Redfish fallback failed.*" + reason):
        boot.collect(device, "pxe", None)
    if "patch_status" not in params:
        assert all(call[0] != "patch" for call in calls)


def test_invalid_action_rejected_before_any_request():
    device, calls = make_device()
    with pytest.raises(CapUnsupported, match="unsupported boot device"):
        boot.collect(device, "off", None)
    assert calls == []
