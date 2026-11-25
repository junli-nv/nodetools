from types import SimpleNamespace

import pytest

from nodetools_runtime import fan_profiles, model, oem, runtime
from nodetools_runtime.errors import CapUnsupported, NodetoolsError, TransportError
from nodetools_runtime.inventory import Host

COMMON = {"auto": [(0x3C, 0x73, 0)], "max": [(0x3C, 0x73, 1), (0x3C, 0x74, 100)]}
NETWEB = {"auto": [(0x3A, 0xD0, 0x11) + (0,) * 16]}
for action, duty in (("low", 50), ("opt", 80), ("max", 100)):
    NETWEB[action] = [(0x3A, 0xD0, 0x11) + (2,) * 16, (0x3A, 0xD0, 0x0E) + (duty,) * 16]


@pytest.mark.parametrize("adapter, expected", (
    ("ami", {"auto": [(0x3A, 0x30, 0)], "max": [(0x3A, 0x30, 1)]}),
    ("byd", {"auto": [(0x3C, 0x06)], "max": [(0x3C, 0x03)]}),
    ("compal", {"auto": [(0x3C, 0x73, 0)],
                "max": [(0x3C, 0x73, 1)] + [(0x3C, 0x75, zone, 100) for zone in range(3)]}),
    ("dgx", COMMON),
    ("gb200", COMMON),
    ("gigabyte", {"auto": [(0x2E, 0x10, 0)], "max": [(0x2E, 0x10, 1)]}),
    ("netweb", NETWEB),
    ("wistron", COMMON),
))
def test_bundled_sequences_match_existing_adapter_actions(adapter, expected):
    module = oem.get(adapter)
    for action, rows in expected.items():
        sent = []
        device = SimpleNamespace(ipmi=SimpleNamespace(raw=lambda *args: sent.append(args)))
        assert module.fan_control(device, action) == "ok"
        assert sent == rows


@pytest.mark.parametrize("adapter, network", [("wistron", ("8", "3")), ("gb200", ("8", "1"))])
def test_fan_control_uses_oem_ipmi_retry_settings(monkeypatch, adapter, network):
    from nodetools_runtime.transport.ipmi import Ipmi

    sent = []

    def run(argv, **kwargs):
        sent.append(argv)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("nodetools_runtime.transport.ipmi.subprocess.run", run)
    device = SimpleNamespace(ipmi=Ipmi("bmc.example.test", None, None, tool="ipmitool"))
    runner = runtime.Runner("fan", SimpleNamespace(action="max"))
    runner._control(device, oem.get(adapter))

    assert len(sent) == 2
    assert all((argv[argv.index("-N") + 1], argv[argv.index("-R") + 1]) == network for argv in sent)


def test_local_exact_model_preview(monkeypatch, tmp_path):
    path = tmp_path / "fan-profiles.ini"
    path.write_text(
        "[profile:lab]\nplatform=hgx\noem=compal\nrole=compute\nmodel=sample-model\n"
        "max=\n    0x3c 0x73 0x01\n    0x3c 0x75 0x00 100 # zone zero\n"
    )
    profiles = fan_profiles.load(path, local=True)
    host = Host("test", variables={"platform": "hgx", "oem": "compal"}, groups=["compute"])
    device = model.Device(host)
    live = {"model": "SAMPLE-MODEL"}
    monkeypatch.setattr(fan_profiles.scan, "read_identity", lambda *args: live)
    options = SimpleNamespace(action="max", fan_profiles=profiles, fan_dry_run=True)
    runner = runtime.Runner("fan", options)
    plan = runner._control(device, oem.get("compal"))
    assert plan == [
        "lab max step 1/2: ipmitool raw 0x3c 0x73 0x01",
        "lab max step 2/2: ipmitool raw 0x3c 0x75 0x00 0x64",
    ]
    live["model"] = "other"
    with pytest.raises(CapUnsupported, match="no local fan profile"):
        runner._control(device, oem.get("compal"))


def test_raw_bytes_are_validated_before_use(tmp_path):
    path = tmp_path / "fan-profiles.ini"
    path.write_text("[profile:test]\nmax=0x3c 0x73 256\n")
    with pytest.raises(NodetoolsError, match="outside"):
        fan_profiles.load(path)


def test_failed_step_stops_the_sequence():
    sent = []

    def raw(*args):
        sent.append(args)
        if len(sent) == 2:
            raise TransportError("controller rejected request")

    device = SimpleNamespace(ipmi=SimpleNamespace(raw=raw))
    with pytest.raises(TransportError, match=r"step 2/4 failed \(1 steps completed\)"):
        oem.get("compal").fan_control(device, "max")
    assert len(sent) == 2


def test_local_ipmi_settings_inherit_and_override(tmp_path):
    path = tmp_path / "fan-profiles.ini"
    profile = (
        "[profile:lab]\nplatform=nvl\noem=wistron\nrole=compute\n"
        "model=Sample\nmax=0x3c 0x73 0x01\n"
    )
    path.write_text(profile, encoding="utf-8")
    inherited = fan_profiles.load(path, local=True)["lab"]
    assert (inherited.ipmi_timeout, inherited.ipmi_retries) == (8, 1)

    path.write_text("[DEFAULT]\nipmi_timeout=5\nipmi_retries=2\n" + profile + "ipmi_timeout=7\n",
                    encoding="utf-8")
    override = fan_profiles.load(path, local=True)["lab"]
    assert (override.ipmi_timeout, override.ipmi_retries) == (7, 2)

    path.write_text(profile + "ipmi_timeout=0\n", encoding="utf-8")
    with pytest.raises(NodetoolsError, match="must be positive integers"):
        fan_profiles.load(path, local=True)
