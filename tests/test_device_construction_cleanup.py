import pytest
from nodetools_runtime import cli, runtime
from nodetools_runtime.arguments import parse_args
from nodetools_runtime.errors import AuthError
from nodetools_runtime.inventory import Host, Inventory


@pytest.mark.parametrize("no_cache", [False, True])
@pytest.mark.parametrize("error", [AuthError("authentication failed"), KeyboardInterrupt()])
def test_failed_identity_read_closes_transport_and_preserves_error(monkeypatch, no_cache, error):
    class Transport:
        closed = False
        def set_deadline(self, deadline):
            pass
        def close(self):
            self.closed = True

    redfish, ipmi = Transport(), Transport()
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(runtime.scan, "load", fail)
    monkeypatch.setattr(runtime.scan, "read_identity", fail)
    args = parse_args(["pspsu", "unconfigured-shelf"])
    options = cli.Options(args)
    cli._apply_defaults(options, Inventory(), args)
    options.no_cache = no_cache
    host = Host("unconfigured-shelf", "192.0.2.1", groups=["powershelf"])
    with pytest.raises(type(error)) as raised:
        runtime.build_device(host, options,
                             redfish_factory=lambda *a, **k: redfish,
                             ipmi_factory=lambda *a, **k: ipmi)
    assert raised.value is error
    assert redfish.closed
