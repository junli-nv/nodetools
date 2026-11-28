# nodetools development requirements

## Read before changing device behavior

Read the relevant OEM adapter and both:
- docs/DEVELOPMENT.md: "公共框架与厂商适配" and "所有管理命令的端点选择".
- docs/DEVELOPMENT.en.md: "Shared framework and vendor adapters" and
  "Endpoint selection for all management commands".

Preserve the shared CLI, adapter, output, credential and transport contracts.
Keep model-specific paths, interpretation and firmware scope in the OEM adapter.
Explain and regression-test any new shared extension point.

## Direct reads are a certification requirement

For a known platform/OEM/role/generation and verified capability:
1. Try its verified fixed endpoint set first. A successful normal read must not
   query the service root, Managers, Systems or unrelated collections merely
   to rediscover information already supplied by inventory.
2. Fall back only after the fixed path set is unavailable or fails validation.
   Keep discovery scoped to the requested capability. Preserve explicit
   system_id, manager_id and chassis_id selection. Explicit --no-cache and
   write-time identity/safety checks retain their documented semantics.
3. Validate resource identity, units, complete expected readings and zero values.
   Do not accept partial or misidentified data just to remain on the fast path.
4. Record per-capability evidence in docs/certified-reads.json: actual model and
   firmware, configuration, exact normal request paths/count, expected rows,
   fallback behavior and the matching automated fixture.
5. Test the normal path through runtime.Runner, including build_device and
   adapter selection. Mock transport only; assert the exact ordered requests.
   Test missing/invalid direct resources and explicit resource selection.
6. Never equate "generic discovery returned values" with model certification.
   Certification is per capability and firmware. The legacy_unreviewed list is
   a baseline audit exception, not certification; do not expand it for new work.
   Add executable contracts for new fixed-read declarations. List remaining capabilities
   and writes as unverified until independently implemented and validated.

Exercise the normal CLI defaults with a representative inventory. An OEM default
that already identifies a verified resource must work without an extra per-command
override. Treat extra startup discovery or a changed normal request order as a
regression. Verify the known endpoint and the scoped fallback independently.

Reuse an established direct-read layout for the same OEM, model and generation
across firmware revisions. A firmware version change alone does not select another
adapter or require discovery at runtime. Verify the existing contract on the new
firmware and add a layout only when observed resources or output semantics differ.

Do not inflate expected request counts, delete path assertions, or label an
untested capability certified to make a change pass. A changed contract needs
fresh hardware evidence and an explicit explanation in the change description.

## Required completion checks

Run python3 -m pytest -q and git diff --check before declaring completion.
The CI job direct-read-contract runs the full suite, including
tests/test_direct_read_contract.py, tests/test_psu_aliases.py and
tests/test_published_redfish_archives.py. The archive check covers every certified
model/firmware, including previously validated hardware when it is revalidated.
Keep MANIFEST.json as a compact schema-2 index; put detailed evidence in
manifests/<oem>/<role>/<model>.json. Update the index and recursive SHA256SUMS together.
Report actual normal request counts, real-device readback and unverified scope.
Do not call a GitHub check merge-blocking unless branch protection requires it.

## Site data

Do not commit site inventories, real credentials, tokens or raw authenticated
debug logs. Keep BCM optional; generic inventory operations must work without it.
Preserve useful operator notes such as cmsh credential lookup instructions.
Use /bin/bash for shell work. Do not issue power, firmware or other device
writes as part of read-only capability validation.

## Mandatory paired and bulk sensor acceptance

The operator requires PSU and fan reads at cluster scale. Single-node success
is never sufficient acceptance. For every supplied representative machine and
model/firmware combination, review BOTH fan and PSU capabilities together.
Record the actual endpoints, complete physical reading counts, units, zero and
alarm handling, fallback behavior and evidence for each capability.

Before declaring adaptation complete, run automated multi-node acceptance with
the requested selectors and concurrency, including --jobs 200 when requested.
Account for every selected node and every expected reading. Report failures,
timeouts, HTTP/request counts, elapsed time and process resource use. Verify
that a slow or failed node does not suppress other nodes' results, that queued
nodes receive their own execution budget, and that transports close after use.
Persist per-node results incrementally so interruptions cannot erase coverage.
Never hide failure by silently lowering concurrency or by inflating timeouts.
A supported capability with missing readings or failed bulk acceptance blocks
completion. A capability absent from hardware or BMC needs actual readback
evidence and an explicit unsupported conclusion; it is not a successful read.
No release/model-complete claim without the paired coverage and bulk evidence.

## Authoritative topology clarification

Compute-tray fan reads are mandatory on BOTH HGX and NVL. Missing compute fan
readings are a failed/incomplete adaptation, never an unsupported exception.
HGX compute trays contain physical PSUs. NVL compute trays and NVSwitch trays
do not contain physical PSUs; rack PSUs are on power shelves.
The operator explicitly permits NVL nodepsu to use HSC power as a proxy.
Label the HSC source and distinguish reported power, input/output voltage and
independently reported input/output watts. Do not invent a missing output.
Investigate NVSwitch HSC/PDB telemetry too: an empty Redfish PSU collection
does not establish the absence of HSC telemetry in BMC, NVOS or other exposed
read-only interfaces. Preserve the distinction between BMC and NVOS access.
These topology rules qualify the mandatory paired/bulk acceptance section.

## NVSwitch fan scope

Do not register NVSwitch fan commands or include fan probes in its routine
read audit. The tested N5500_LD BMC 88.0002.1962 exposes empty fan collections
and NVOS rejects the fan query. This is not a successful fan read.
Keep compute-tray fans and actual power-supply module fans on power shelves.
Reconsider another switch model only with independently verified evidence.

## Help examples are required completion checks

Follow docs/DEVELOPMENT.md section 8 and its English counterpart: every
public command, nested subcommand and new action with dedicated options must
include runnable typical examples in its own --help. Update help in the same
change as the feature; a README or separate document is not a substitute.
Cover default reads, host ranges and available preview/precheck workflows.
For writes, explain password/environment prerequisites and show application.
Run tests/test_help_examples.py: enumerate the command trees and public
auxiliary entries, require visible examples, and parse their arguments without
executing device operations. Check new action coverage, not just Examples text.

## Simple OS operations

nodeos is the unified OS SSH entrypoint; bmc is one resource group.
Keep handlers separate and reuse OS transport. BMC CRUD accepts positional
usernames/passwords. Detect slots, channels and validation resources internally.
Show short workflows in normal help and technical overrides in --help-full.
Keep nodebmc for BMC-interface operations. Test debug and help with each action.
