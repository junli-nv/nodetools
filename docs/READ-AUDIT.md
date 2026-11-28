# Read audit and acceptance checkpoint

This checkpoint is not a model-complete certification. The full automated
suite passed: 430 tests. Device writes were not exercised.

## Repeatable checks

Replace DEVICE with an inventory host:

```bash
python3 -m nodetools_runtime.read_audit DEVICE --list
python3 -m nodetools_runtime.read_audit DEVICE
python3 -m nodetools_runtime.batch_audit pspsu --selector-file /path/to/selector.txt --jobs 200 --expected-readings 6 --output /tmp/psu-audit.jsonl
```

The batch audit uses the production runner, saves each completed node to a
private JSONL file and refuses to overwrite existing files. Any failed or
incomplete node makes it exit nonzero. read_ok records observed execution;
only the per-capability fixture contracts in certified-reads.json certify
exact requests and row counts. Equivalent fixture coverage for all management
cases is still pending; the audit alone is not full certification.

## Last hardware batch before this checkpoint

All runs used 200 workers, a 3-second request timeout and a 5-second node
budget. Every selected node produced a recorded outcome.

| Read | Selected | Complete | Incomplete | Failed |
| --- | ---: | ---: | ---: | ---: |
| Powershelf PSU | 290 | 283 | 4 | 3 |
| Powershelf PSU-module fans | 290 | 283 | 4 | 3 |
| Compute HSC power proxy | 200 | 198 | 0 | 2 |
| Compute fans | 200 | 198 | 0 | 2 |

Powershelf failures: two connection failures and one authentication failure.
Incomplete: one shelf reports five modules, three report empty PSU and fan
collections. Two empty shelves were checked through both published chassis
aliases: legacy Power and modern PowerSupplies returned HTTP 200 with zero
modules. The underlying cause is unresolved; missing data does not prove
physical module absence or failure. Both compute failures were connection
timeouts. Overall hardware acceptance has not passed.

NVL compute nodepsu reports two HSC power readings, not physical PSUs.
Compute fan output has eight PWM and sixteen rotor RPM readings, not
24 physical fans. Lite-On shelves report six PSU-module fans.
NVSwitch fan commands and routine fan probes are excluded. The tested N5500
uses NVOS lm5066i input telemetry; output watts are not fabricated.
Only one NVSwitch HSC sample was validated; multi-node acceptance is pending.

Normal Lite-On pspsu uses one legacy Power GET with stable slot IDs.
--details uses eight GETs to merge modern identity and legacy input power.
Site inventory, credentials, raw captures and per-node results stay private.

## Audit diagnostics and credentials

Both audit entrypoints support `-x/--debug`. Diagnostics go to stderr;
read-audit JSON, batch JSONL files and regular stdout retain their formats.
The audit tools use the normal CLI defaults from inventory, including Redfish
request/connect timeouts and per-node budgets. Explicit `--timeout` and
`--node-timeout` override the corresponding defaults; no short audit-only
request/node limits are imposed. Batch `--jobs` also inherits inventory.

```bash
python3 -m nodetools_runtime.read_audit DEVICE -x --only nodefan
python3 -m nodetools_runtime.read_audit NVSWITCH -x --only nvswpsu
python3 -m nodetools_runtime.batch_audit pspsu 'shelf[01-04]' -x --jobs 200 --expected-readings 6 --output /tmp/psu-debug.jsonl
```

For mixed Redfish/NVOS audits, `--user` and `--password-env` override only
BMC credentials. NVOS uses the configured OS credentials; `--os-user` and
`--os-password-env` provide separate optional overrides. Password-env options
take environment variable names, not expanded password values. OS passwords
are omitted from audit diagnostics. Redfish curl diagnostics include BMC
credentials for reproduction, so keep debug logs private.

Switch health/monitor wrappers and both gen_* helpers also support debug.
The credential-template generator logs only its destination and permissions,
never template contents. The BCM exporter remains optional.

Offline acceptance covers the full public help/parser catalog, actual
auxiliary execution diagnostics, separate BMC/OS credentials, inherited and
explicit timeout budgets, and unchanged structured output. These checks do
not replace hardware batch acceptance or certify additional device reads.
