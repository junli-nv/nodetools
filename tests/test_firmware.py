import json
from types import SimpleNamespace

import pytest

from nodetools_runtime import firmware_cli
from nodetools_runtime.errors import CapUnsupported, NodetoolsError
from nodetools_runtime.oem.hgx import smc_firmware as smc
from nodetools_runtime.subjects import firmware


def fake_device(model="Dell PowerEdge", extra_systems=None):
    extra_systems = extra_systems or {}
    system_path = "/redfish/v1/Systems/1"
    manager_path = "/redfish/v1/Managers/1"
    inventory = "/redfish/v1/UpdateService/FirmwareInventory"
    resources = {
        "/redfish/v1/": {"UpdateService": {"@odata.id": "/redfish/v1/UpdateService"}},
        "/redfish/v1/UpdateService": {
            "ServiceEnabled": True, "MultipartHttpPushUri": smc.UPLOAD,
            "FirmwareInventory": {"@odata.id": inventory},
        },
        "/redfish/v1/Systems": {
            "Members": [{"@odata.id": path} for path in [system_path, *extra_systems]],
        },
        system_path: {"Model": model, "SerialNumber": "serial", "PowerState": "On", "BiosVersion": "2.0"},
        "/redfish/v1/Managers": {"Members": [{"@odata.id": manager_path}]},
        manager_path: {"Model": "AST2600", "FirmwareVersion": smc.BMC_VERSION},
        "/redfish/v1/Chassis/1": {"Model": "X14DBG-LC"},
        inventory: {"Members": [{"@odata.id": inventory + "/GPU"}]},
        inventory + "/GPU": {"Name": "GPU", "Version": "A.2"},
        firmware.TASKS: {"Members": []},
        **extra_systems,
    }
    return SimpleNamespace(redfish=SimpleNamespace(get=resources.get)), resources


def test_firmware_inventory_without_vendor_adapter():
    device, _ = fake_device()
    lines = firmware_cli.run(device, SimpleNamespace(action="show"))
    assert "Dell PowerEdge BIOS  2.0" in lines
    assert "GPU  A.2" in lines
    with pytest.raises(CapUnsupported, match="no update adapter"):
        firmware_cli.run(device, SimpleNamespace(action="check"))


def test_check_selects_matching_system_and_rejects_bad_package(tmp_path):
    device, _ = fake_device(smc.MODEL, {"/redfish/v1/Systems/HGX": {"Model": "GPU tray"}})
    package = tmp_path / "package.zip"
    package.write_bytes(b"wrong content")
    with pytest.raises(NodetoolsError, match="package SHA256"):
        firmware_cli.run(device, SimpleNamespace(action="check", component="bmc", package=package))


def test_bios_requires_host_off(tmp_path):
    device, _ = fake_device(smc.MODEL)
    args = SimpleNamespace(action="check", component="bios", package=tmp_path / "package.zip")
    with pytest.raises(NodetoolsError, match="host power Off"):
        firmware_cli.run(device, args)


def test_update_accepts_task_and_does_not_allow_redirects(monkeypatch, tmp_path):
    device, _ = fake_device(smc.MODEL)
    monkeypatch.setattr(smc, "image_from_package", lambda *args: None)
    device.redfish.url = lambda path: "https://bmc" + path

    def post(url, *, files, timeout, allow_redirects):
        assert url == "https://bmc" + smc.UPLOAD
        assert not allow_redirects
        payload = json.loads(files["UpdateParameters"][1])
        assert payload["Targets"] == [smc.MANAGER]
        assert payload["Oem"]["Supermicro"]["BMC"]["PreserveCfg"]
        return SimpleNamespace(status_code=202, headers={"Location": firmware.TASKS + "/17"})

    device.redfish._session = SimpleNamespace(post=post)
    args = SimpleNamespace(action="update", component="bmc", package=tmp_path / "package.zip", reflash=False)
    with pytest.raises(NodetoolsError, match="already installed"):
        firmware_cli.run(device, args)
    args.reflash = True
    assert "task 17" in firmware_cli.run(device, args)[0]


def test_task_id_cannot_reference_another_bmc():
    device, _ = fake_device()
    with pytest.raises(CapUnsupported, match="task ID"):
        list(firmware.tasks(device, "//example.test"))


def test_batch_update_requires_explicit_jobs_and_unique_endpoint(monkeypatch):
    from threading import Barrier

    hosts = [
        SimpleNamespace(name="first", address="bmc-first"),
        SimpleNamespace(name="second", address="bmc-second"),
    ]
    inv = SimpleNamespace(default=lambda key, default: default, default_bool=lambda key, default: default)
    monkeypatch.setattr(firmware_cli.Inventory, "from_file", lambda path: inv)
    monkeypatch.setattr(firmware_cli.selection, "select", lambda *args, **kwargs: hosts)
    monkeypatch.setattr(firmware_cli, "build_device",
                        lambda host, *args, **kwargs: SimpleNamespace(close=lambda: None))
    updates = Barrier(2)

    def query(device, args):
        assert args.action == "update"
        updates.wait(timeout=2)
        return ["accepted"]

    monkeypatch.setattr(firmware_cli, "run", query)
    command = ["update", "first,second", "bmc", "package.zip"]
    assert firmware_cli.main(command, "nodefw") == 1
    assert firmware_cli.main(command + ["--jobs", "2"], "nodefw") == 0
    hosts[1].address = hosts[0].address
    assert firmware_cli.main(command + ["--jobs", "2"], "nodefw") == 1


def test_task_listing_includes_all_states_across_pages():
    device, resources = fake_device()
    page = firmware.TASKS + "?$skip=2"
    paths = [firmware.TASKS + "/" + str(number) for number in (1, 2, 3)]
    resources[firmware.TASKS] = {
        "Members": [{"@odata.id": path} for path in paths[:2]],
        "Members@odata.nextLink": page,
    }
    resources[page] = {"Members": [{"@odata.id": paths[2]}]}
    for path, state in zip(paths, ("Completed", "Exception", "Running")):
        resources[path] = {"TaskState": state}
    rows = firmware_cli.run(device, SimpleNamespace(action="status", task=None))
    assert [row.split()[2] for row in rows] == ["Completed", "Exception", "Running"]


def test_missing_task_reports_absence_on_the_bmc():
    device, _ = fake_device()
    device.redfish.get_raw = lambda path: (404, {"error": {}})
    with pytest.raises(NodetoolsError, match="task 17 is not present"):
        list(firmware.tasks(device, "17"))


def test_task_detail_includes_failure_message_and_timestamp():
    device, _ = fake_device()
    payload = {
        "Name": "BIOS Update", "TaskState": "Exception", "TaskStatus": "Critical",
        "PercentComplete": 0, "StartTime": "2026-11-24T20:00:00Z",
        "Messages": [{"Severity": "Critical", "MessageId": "Update.Error",
                      "Message": "Image failed verification", "Resolution": "Check the package"}],
    }
    device.redfish.get_raw = lambda path: (200, payload)
    lines = firmware_cli.run(device, SimpleNamespace(action="status", task="13"))
    assert lines == [
        "Task 13  BIOS Update", "  State  Exception", "  Status  Critical", "  Progress  0%",
        "  Started  2026-11-24T20:00:00Z",
        "  Message  Critical  Update.Error  Image failed verification",
        "  Resolution  Check the package",
    ]


def test_kaytus_bios_setup_task_has_text_id_and_oem_trigger():
    device, resources = fake_device()
    path = firmware.TASKS + "/BIOSSetup"
    task = {
        "Name": "Task BIOSSetup", "Description": "BIOS-Setup",
        "TaskState": "Completed", "TaskStatus": "OK", "PercentComplete": 100,
        "Oem": {"Public": {"Type": "CONFIGURE", "Trigger": "SystemReset"}},
    }
    resources[firmware.TASKS] = {"Members": [{"@odata.id": path}]}
    resources[path] = task
    device.redfish.get_raw = lambda path: (200, resources[path])
    assert firmware_cli.run(device, SimpleNamespace(action="status", task=None)) == [
        "BIOSSetup  BIOS-Setup  Completed  OK  100%",
    ]
    assert firmware_cli.run(device, SimpleNamespace(action="status", task="BIOSSetup")) == [
        "Task BIOSSetup  BIOS-Setup", "  State  Completed", "  Status  OK", "  Progress  100%",
        "  Type  CONFIGURE", "  Trigger  SystemReset",
    ]
