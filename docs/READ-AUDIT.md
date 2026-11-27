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
