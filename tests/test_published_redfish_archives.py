import hashlib
import json
from pathlib import Path, PurePosixPath

import pytest

from nodetools_runtime import cli, redfish_snapshot, runtime

DOCS = Path(__file__).resolve().parents[1] / "docs"
ARCHIVE_ROOT = DOCS / "redfish-archives"
def load_archive_index(directory):
    index = json.loads((directory / "MANIFEST.json").read_text())
    assert index["schema"] == 2 and "samples" not in index
    assert (index["platform"], index["generation"]) == (directory.parent.name, directory.name)
    samples, missing, paths, models = [], [], set(), set()
    for entry in index["models"]:
        assert set(entry) <= {"oem", "role", "model", "firmware", "missing_firmware", "manifest"}
        relative = PurePosixPath(entry["manifest"])
        assert not relative.is_absolute() and ".." not in relative.parts
        assert relative.parts[0] == "manifests" and relative.suffix == ".json"
        assert relative.as_posix() == entry["manifest"] and "\\" not in entry["manifest"]
        assert entry["manifest"] not in paths
        paths.add(entry["manifest"])
        key = (entry["oem"], entry["role"], entry["model"])
        assert key not in models
        models.add(key)
        detail = json.loads((directory / relative).read_text())
        assert detail["schema"] == 2
        for field in ("platform", "generation"):
            assert detail[field] == index[field]
        for field in ("oem", "role", "model"):
            assert detail[field] == entry[field]
        rows = detail["samples"]
        gaps = detail.get("missing_archives", [])
        assert rows or gaps
        assert entry["firmware"] == sorted({row["bmc_version"] for row in rows})
        assert entry.get("missing_firmware", []) == sorted({gap["bmc_version"] for gap in gaps})
        assert not set(entry["firmware"]).intersection(entry.get("missing_firmware", []))
        for row in rows:
            assert row["model"] == detail["model"] and row["oem"] == detail["oem"]
            assert row.get("role", detail["role"]) == detail["role"]
            assert Path(row["file"]).name == row["file"]
        assert all(gap["model"] == detail["model"] for gap in gaps)
        samples.extend(rows)
        missing.extend(gaps)
    assert len({row["file"] for row in samples}) == len(samples)
    actual = {p.relative_to(directory).as_posix() for p in (directory / "manifests").rglob("*.json")}
    assert paths == actual, "Unindexed or missing model manifest"
    return dict(index, samples=samples, missing_archives=missing)


MANIFESTS = {path.parent: load_archive_index(path.parent)
             for path in sorted(ARCHIVE_ROOT.glob("*/*/MANIFEST.json"))}
CERTIFICATES = json.loads((DOCS / "certified-reads.json").read_text())["records"]


@pytest.mark.parametrize("directory", MANIFESTS, ids=lambda directory: directory.name)
def test_archive_checksums_and_firmware_coverage(directory):
    manifest = MANIFESTS[directory]
    lines = (directory / "SHA256SUMS").read_text().splitlines()
    checksums = dict(reversed(line.split(maxsplit=1)) for line in lines)
    assert len(checksums) == len(lines)
    files = {sample["file"] for sample in manifest["samples"]}
    assert files == {path.name for path in directory.glob("*.redfish.json.gz")}
    json_files = {p.relative_to(directory).as_posix() for p in directory.rglob("*.json")}
    assert set(checksums) == files | json_files
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
    matching = [record for record in CERTIFICATES
                if (record["model"], record["bmc_firmware"]) == (sample["model"], sample["bmc_version"])]
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
    expected_variants = {(r["capability"], tuple(r.get("options", [])))
                         for r in matching if r["capability"] not in gaps | nvos | private}
    captured_variants = {(r["capability"], tuple(r.get("options", [])))
                         for r in sample["verified_replays"]}
    assert captured_variants == expected_variants

    for replay in sample["verified_replays"]:
        record = next(record for record in matching
                      if record["capability"] == replay["capability"]
                      and record.get("options", []) == replay.get("options", []))
        assert replay["readings"] == record["expected_rows"]
        assert replay["exact_get_paths"] == record["direct_requests"]
        assert len(replay["exact_get_paths"]) == record["request_count"]
        if sample.get("role") == "nvswitch" and record["capability"] == "temp":
            verify_switch_redfish(snapshot, record)
            continue
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


def test_every_certified_model_firmware_has_an_archive_or_explicit_gap():
    certified = {(r["model"], r["bmc_firmware"]) for r in CERTIFICATES}
    published = {(s["model"], s["bmc_version"])
                 for m in MANIFESTS.values() for s in m["samples"]}
    gaps = {(s["model"], s["bmc_version"])
            for m in MANIFESTS.values() for s in m.get("missing_archives", [])}
    assert not (published & gaps)
    assert certified <= published | gaps, "Certified hardware is missing its Redfish archive"


def verify_switch_redfish(snapshot, record):
    from nodetools_runtime.arguments import parse_args
    from nodetools_runtime.inventory import Host
    transport = redfish_snapshot.Replay(snapshot)
    host = Host("snapshot", "snapshot", snapshot["device"])
    options = cli.Options(parse_args([record["command"], host.name]))
    def factory(selected):
        return runtime.build_device(
            selected, options, apply_deadline=False,
            load_profile=runner._needs_profile(selected),
            redfish_factory=lambda *a, **k: transport,
            ipmi_factory=lambda *a, **k: None)
    runner = runtime.Runner(record["capability"], options, device_factory=factory)
    result = runner._one(host)
    assert result["error"] is None
    assert len(result["readings"]) == record["expected_rows"]
    assert transport.requests == [(path, 200) for path in record["direct_requests"]]
