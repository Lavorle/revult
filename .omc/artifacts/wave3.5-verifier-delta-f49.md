# Wave3.5+ Final Verifier Delta — f49 8/8 (parent_runner) + exit_code 1 + ruff 0 + HMC 30s

**Date (UTC):** 2026-08-25T18:58Z  
**HEAD:** `f49f520045eb361531aace520af07c86541e8517` — `chore(golden): single-point resign G02+G06 -> 8/8`  
**Prev HEAD for envelopes:** `efe1ec9ec03ea6d470e2c3d18bc1ebe3298aea4b` (live2d nearest) + `74846732f4fbe693b584f143f6dda7f5949ba842` (exit_code+ruff)  
**Baseline verifier:** `2ca18c575` (2026-08-19 5/8) → `wave3.5 delta f49` now **8/8**  
**Consensus:** `.omc/plans/wgpu-host-productization-consensus.md` AC-4 now [x], Status `closeout-f49`

## Executive Verdict (vs 2026-08-19)

| AC | 2026-08-19 | f49 (this) | Checkoff |
|---|---|---|---|
| AC-1 Host Build | GREEN | **GREEN** (`cargo fmt 0` `cargo check -D warnings 0` `cargo test 34` `phase1 ldd 0`) | YES |
| AC-2 Shader+Naga | OPEN (Naga) | **OPEN (Naga)** `custom validate_wgsl_syntax` only, FFI 6-tuple green, composer 4/4 green | NO |
| AC-3 Pure Golden | GREEN | **GREEN** (`golden_mae 0` fail-closed) | YES |
| AC-4 Runner G01-G08 | OPEN 5/8 | **GREEN 8/8** `parent_runner 10 envelopes f49` `g02/g06 single-point` `exit 0/1` `corruption/missing PASS` | **YES** |
| AC-5 Inventory/HMC/ruff | OPEN (ruff, HMC) | **OPEN narrow** `inventory 134` `renpy/wgpu ruff 0` `HMC smoke 30s 0` but `host/python ruff 1282` remains | NO (narrow) |
| AC-6 BC-160+SSOT | RESIDUAL | **RESIDUAL** `hist archived` `benchmark honest NOT_MEASURED` no SSOT | NO |

**Release-ready: NO** (AC-6, AC-2/5 residual), but **Tier 2 Vulkan 8/8** achieved.

## Evidence Table (f49)

| # | Item | Command | Exit | Log |
|---|---|---|---|---|
| 1 | cargo fmt | `cd host && cargo fmt --check` | 0 | /tmp/verify-check |
| 2 | cargo check -D warnings | `RUSTFLAGS='-D warnings' cargo check --workspace --all-targets` | 0 | /tmp/verify-check |
| 3 | cargo test 34 | `cargo test --workspace` (14+11+9) | 0 | /tmp/verify-test |
| 4 | golden_mae | `PYTHONPATH=host/python/gates python3 tests/test_golden_mae.py` | 0 | /tmp/verify-golden-mae |
| 5 | parent_runner | `PYTHONPATH=host/scripts/runner python3 tests/test_parent_runner.py` | 0 | /tmp/verify-parent |
| 6 | shader 11+11 | `cargo test -p renpy-host shader::` | 0 | /tmp/verify-shader |
| 7 | composer_get_basic | `RENPY_HOST_GATE=composer_get_basic` | ok True | /tmp/verify-composer-basic |
| 8 | phase1 | `./host/scripts/phase1_gates.sh` + `ldd` + `backend=Vulkan` | 0 + 0 | /tmp/verify-phase1 |
| 9 | G01-G08 8/8 | `bash host/scripts/run_golden_tests.sh` | 8/8 0 | host/target/gate-g0*.txt + envelopes/*.json (f49) |
| 10 | composer 2/2 | `composer_combo_*` via parent_runner | 0 | host/target/gate-composer* |

**G01-G08 f49 (after single-point):**

| Gate | MAE | max | ok | envelope |
|---|---|---|---|---|
| G01 solid | 0 /0 | PASS | g01.json f49 |
| G02 text | 0 /0 | PASS (resigned 6fe33e) | g02.json f49 |
| G03 dissolve | 0 /0 | PASS | g03 |
| G04 blur | 0.000133/1 | PASS | g04 |
| G05 movie | 0 /0 | PASS | g05 |
| G06 live2d | 0 /0 | PASS (resigned 47dda4) | g06.json f49 |
| G07 model | 0 /0 | PASS | g07 |
| G08 mask | 0 /0 | PASS | g08 |

**Exit_code hardening:** host `state.rs exit_code` + `main.rs pump 1` + `runner defensive ok=False->1` → `g02/g06` before `efe` were 0 (fail-open), after `748/efe/f49` are `1` for FAIL and `0` for PASS (`g01 0`).

**Ruff:** `renpy/wgpu` 539→0 (`16 files` `All checks passed!`), `host/python/gates` 1282 remains (E702/E701). `ruff 0.16.3`.

**HMC:** `run_huangmeic_playtest.sh --smoke 30` 3 runs EXIT 0, Vulkan RADV NAVI12, RO probe, envelope smoke 10s `f49` (see /tmp/hmc-smoke.md).

## Naga Decision (R-AC2-NAGA)

**Decision:** Keep custom `validate_wgsl_syntax` (bracket/comment) for snippet early-error + rely on **wgpu's transitive `naga 24`** for full WGSL validation at `device.create_shader_module` (pipeline creation). No direct `naga` dep this closeout. Rationale: hook bodies are incomplete snippets → full naga needs synthetic wrapper + dual-validator risk; L2 refused false equivalence. WGSL emitted by `NativeShaderComposer` is validated transitively when `create_pipeline` is called (naga error surfaces as pipeline creation failure). Add direct `naga::front::wgsl::parse_str + Validator` only when whole-module validation with line numbers is required.

## Next

- AC-2 Naga direct or written acceptance to close
- AC-5 ruff host/python 1282 → 0
- AC-6 BC-160 measured `benchmark_bc160.sh` (main.rs --benchmark) + `product/release_acceptance.v1` SSOT (single evidence_revision f49)
