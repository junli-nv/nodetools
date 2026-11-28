import hashlib
import json
from pathlib import Path

import pytest

from nodetools_runtime import cli, redfish_snapshot, runtime

DOCS = Path(__file__).resolve().parents[1] / "docs"
ARCHIVE_ROOT = DOCS / "redfish-archives"
MANIFESTS = {
    directory: json.loads((directory / "MANIFEST.json").read_text())
    for directory in (ARCHIVE_ROOT / "hgx" / "b300", ARCHIVE_ROOT / "nvl" / "gb300")
}
CERTIFICATES = json.loads((DOCS / "certified-reads.json").read_text())["records"]


@pytest.mark.parametrize("directory", MANIFESTS, ids=lambda directory: directory.name)
def test_archive_checksums_and_firmware_coverage(directory):
    manifest = MANIFESTS[directory]
    checksums = dict(reversed(line.split(maxsplit=1))
                     for line in (directory / "SHA256SUMS").read_text().splitlines())
    files = {sample["file"] for sample in manifest["samples"]}
    assert files == {path.name for path in directory.glob("*.redfish.json.gz")}
    assert set(checksums) == files | {"MANIFEST.json"}
    for name, expected in checksums.items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected

    if "missing_archives" not in manifest:
        return

    present = {(sample["model"], sample["bmc_version"]) for sample in manifest["samples"]}
    missing = {(sample["model"], sample["bmc_version"]) for sample in manifest["missing_archives"]}
    models = {model for model, _ in present}
    certified = {(record["model"], record["bmc_firmware"]) for record in CERTIFICATES
                 if record["model"] in models}
    assert missing == certified - present


@pytest.mark.parametrize("directory,sample", [
    pytest.param(directory, sample, id=directory.name + "-" + sample["bmc_version"])
    for directory, manifest in MANIFESTS.items() for sample in manifest["samples"]
])
def test_archive_replays_match_certification(directory, sample, monkeypatch, capsys):
    def no_network(*args, **kwargs):
        pytest.fail("offline archive replay attempted to open a live transport")

    monkeypatch.setattr(runtime, "Redfish", no_network)
    monkeypatch.setattr(runtime, "Ipmi", no_network)
    archive = directory / sample["file"]
    snapshot = redfish_snapshot.load(archive)
    records = {record["capability"]: record for record in CERTIFICATES
               if (record["model"], record["bmc_firmware"]) == (sample["model"], sample["bmc_version"])}
    verified = {replay["capability"] for replay in sample["verified_replays"]}
    gaps = {gap["capability"] for gap in sample.get("replay_gaps", ())}
    nvos = {record["capability"] for record in records.values()
            if record["id"] in sample.get("nv_os_certificates", ())}
    assert all(path.startswith("nvos:") for cap in nvos for path in records[cap]["direct_requests"])
    assert not (verified & gaps or verified & nvos or gaps & nvos)
    private = {item["capability"] for item in sample.get("private_evidence", ())}
    assert private <= {"eventlog"} and not private.intersection(verified | gaps | nvos)
    for cap in private:
        assert all(redfish_snapshot.resource_path(path) not in snapshot["resources"]
                   for path in records[cap]["direct_requests"])
    assert verified | gaps | nvos | private == set(records)

    for replay in sample["verified_replays"]:
        record = records[replay["capability"]]
        assert replay["readings"] == record["expected_rows"]
        assert replay["exact_get_paths"] == record["direct_requests"]
        assert len(replay["exact_get_paths"]) == record["request_count"]
        args = ["redfish", "replay", str(archive), record["command"],
                "--expect-rows", str(replay["readings"]), *replay.get("options", [])]
        if "system_model" in replay:
            assert replay["system_model"] == record["configuration"]["system_model"]
            args.extend(["--system-model", replay["system_model"]])
        for path in replay["exact_get_paths"]:
            args.extend(["--expect-path", path])
        result = cli.main(args)
        output = capsys.readouterr()
        assert result == 0, output.err

    for gap in sample.get("replay_gaps", ()):
        unavailable = {}
        for path in records[gap["capability"]]["direct_requests"]:
            status = snapshot["resources"].get(redfish_snapshot.resource_path(path), {}).get("status")
            if status is None or not 200 <= status < 300:
                unavailable[path] = status
        assert unavailable and unavailable == gap["unavailable_gets"]
