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

Do not inflate expected request counts, delete path assertions, or label an
untested capability certified to make a change pass. A changed contract needs
fresh hardware evidence and an explicit explanation in the change description.

## Required completion checks

Run python3 -m pytest -q and git diff --check before declaring completion.
The CI job direct-read-contract runs the full suite, including
tests/test_direct_read_contract.py and tests/test_psu_aliases.py.
Report actual normal request counts, real-device readback and unverified scope.
Do not call a GitHub check merge-blocking unless branch protection requires it.

## Site data

Do not commit site inventories, real credentials, tokens or raw authenticated
debug logs. Keep BCM optional; generic inventory operations must work without it.
Preserve useful operator notes such as cmsh credential lookup instructions.
Use /bin/bash for shell work. Do not issue power, firmware or other device
writes as part of read-only capability validation.
