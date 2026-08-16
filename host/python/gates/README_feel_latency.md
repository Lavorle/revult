# Feel latency / freeze gates (AC-F / AC-T / AC-Z)

## SSOT probes

| Gate | File | Artifact |
| --- | --- | --- |
| hmc_feel_latency_freeze_probe | hmc_feel_latency_freeze_probe.py | host/target/gate-hmc_feel_latency_freeze_probe.json |
| hmc_feel_page_switch_probe | hmc_feel_page_switch_probe.py | host/target/gate-hmc_feel_page_switch_probe.json |
| thrash (AC-R companion) | hmc_prefs_hover_thrash.py | host/target/gate-hmc_prefs_hover_thrash.txt |

## Runners

```bash
bash host/scripts/run_hmc_feel_latency_freeze_probe.sh
bash host/scripts/run_hmc_feel_page_switch_probe.sh
bash .omx/tmp/wp0/run_thrash_gate.sh
```

## Bars

- AC-T: every first_interactive_ms < 200
- AC-F: continuous p99_inter_present_ms <= ~66 (host take_inter_present_gaps_ms SSOT)
- AC-Z: no hang / stall>=2s / crash / take_focuses None
- product_fps >=30 is a floor only

## Host FFI used by AC-F

- renpy_host.take_inter_present_gaps_ms() — snapshot+clear gap ring + start new epoch
- renpy_host.inter_present_gaps_ms() — peek
- Gaps recorded on each successful product swapchain present in end_frame_present

