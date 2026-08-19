# Implementation Plan: wgpu-host Productization Closeout

**Date:** 2026-08-20  
**Spec:** `docs/superpowers/specs/2026-08-20-wgpu-host-productization-closeout-design.md`  
**Scoreboard:** `.omc/plans/wgpu-host-productization-consensus.md` (AC-1…AC-6)  
**Approach:** Evidence-first residual matrix → parallel fix lanes → verifier checkoff  

**writing-plans skill:** not installed in this environment; plan authored directly from approved spec.

---

## 0. Goal

Prove, keep, fix, or revert the current dirty WIP against the 6 consensus ACs. Check off only green ACs with command evidence. Publish an honest residual ledger for the rest. No new product capabilities.

## 1. Global rules (all agents)

1. Do **not** flip consensus `- [ ]` → `- [x]` unless you are the Wave2/3 verifier→writer path.
2. Do **not** write recovered HuangmeiC or upstream SDL/GL reference trees.
3. `testcases/wgpu_golden/**/actual.*` default **revert** (noise).
4. `baseline.*` keep only with explicit verifier intent after a green run.
5. Shared files `host/renpy-host/src/python.rs`, `renpy/wgpu/composer.py`, `renpy/wgpu/draw.py` → **L2 only**.
6. Two failed fix attempts on a red row → mark residual, stop expanding scope.
7. Skip project-wide formatters/linters unless the task names them; lane-local verify only until Wave2.

## 2. Artifacts

| Artifact | Path | Writer |
|---|---|---|
| Residual matrix | `.omc/artifacts/productization-residual-matrix.md` | Wave0 `scout` |
| Lane provisional notes | `.omc/artifacts/closeout-lane-{l1..l5}.md` | Wave1 lane owners |
| Verifier report | `.omc/artifacts/productization-verifier-report.md` | Wave2 `verifier` |
| Residual ledger | `.omc/artifacts/productization-residual-ledger.md` | Wave3 `writer` |
| AC checkoffs | `.omc/plans/wgpu-host-productization-consensus.md` | Wave3 `writer` (from verifier) |
| Optional ralplan truth-up | `.omx/state/ralplan-wgpu-host-productization.json` | Wave3 `writer` |
| Optional commits | git | Wave3 `git-master` |

---

## Wave 0 — Audit (read-only)

**Agents:** `scout` (required), `debugger` (optional on reds)  
**Parallelism:** single scout; debugger only after matrix draft if reds need root-cause.

### Task W0.1 — Build dirty-path bucket map

**Agent:** `scout`  
**Steps:**
1. `git status --short` and classify every path into: host_build / shader_ffi / golden / runner / inventory_hmc / bc160_hist / noise_other.
2. Diff high-risk files only (no full binary dumps):  
   - `host/renpy-host/src/main.rs` (Duration / u64, div0)  
   - `host/renpy-host/src/shader.rs` (`validate_wgsl_syntax` vs Naga)  
   - `host/renpy-host/src/python.rs` FFI 6-tuple  
   - `host/python/gates/golden_mae.py` fail-closed  
   - `host/scripts/run_golden_tests.sh` vs `host/scripts/runner/parent_runner.py`  
   - `.omx/gates-inventory.json`
3. List all dirty `testcases/wgpu_golden/**/{actual,baseline}.*`.

**Done when:** bucket table exists in residual matrix preamble.

### Task W0.2 — Seed mandatory subclaim rows

**Agent:** `scout`  
**Create one matrix row per claim below** (status TBD until commands run):

| ac_id | claim | evidence_command (initial) |
|---|---|---|
| AC-1 | cargo fmt --check | `cd host && cargo fmt --check` |
| AC-1 | cargo check -D warnings / all-targets | `cd host && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets` |
| AC-1 | cargo test --workspace | `cd host && cargo test --workspace` |
| AC-1 | phase1_gates + libSDL*=0 | `./host/scripts/phase1_gates.sh` |
| AC-2 | shader unit tests | `cd host && cargo test -p renpy-host shader::` |
| AC-2 | Naga prevalidation w/ line errors | code audit + test; if only `validate_wgsl_syntax` → **red/residual** |
| AC-2 | FFI ComposedPipelineInfo 6-tuple | audit `get_or_compile_pipeline_from_parts` signature + composer unpack |
| AC-2 | composer gates quartet | run `composer_get_basic`, `composer_combo_matrixcolor`, `composer_combo_alpha`, `named_pipeline_honesty` |
| AC-3 | golden_mae pure + missing baseline fails | `pytest tests/test_golden_mae.py -q` |
| AC-3 | no implicit baseline write on compare | static audit `compare_or_bootstrap` / evaluate paths |
| AC-4 | parent_runner unit envelope | `pytest tests/test_parent_runner.py -q` |
| AC-4 | G01–G08 via **parent runner** envelope | must use `parent_runner.py`; shell-only `run_golden_tests.sh` is **not** enough |
| AC-4 | run_golden_tests.sh delegates to parent | code audit; if no → yellow/red fix in L4 |
| AC-5 | inventory 134 + tiers | python load `.omx/gates-inventory.json` |
| AC-5 | HuangmeiC smoke read-only recovered | `./host/scripts/run_huangmeic_playtest.sh` (smoke/build as available) |
| AC-5 | production ruff blockers | `ruff check renpy/wgpu host/python/gates` (or residual if env blocks) |
| AC-6 | historical-7900xt archived | `test -d .omx/context/historical-7900xt` |
| AC-6 | benchmark_bc160 executable | `./host/scripts/benchmark_bc160.sh --help` or dry-run |
| AC-6 | product/release acceptance same evidence_revision | look for artifacts; else **residual** |

**Known WIP hints (re-verify, do not trust):**
- lib tests 14/14 were green; custom WGSL validator (not Naga); FFI already returns 6-tuple; `run_golden_tests.sh` loops `cargo run` and does **not** call parent_runner.

### Task W0.3 — Run read-only proof commands and fill status

**Agent:** `scout`  
**Steps:**
1. Run each evidence command that is safe/read-only (builds/tests OK; no baseline writes).
2. Fill `status`, `evidence_summary`, `decision` (`keep`/`fix`/`revert`/`checkoff`), `owner_agent` (L1–L5 / verifier / none).
3. Default decisions:
   - green → `checkoff` (Wave3 only)
   - yellow/red → `fix` + lane owner
   - actual.* → `revert`
   - suspicious baseline.* without green parent-runner proof → `revert` unless notes say intentional
4. Optional: spawn `debugger` on false-green suspects (golden write paths, envelope bypass).

**Done when:** `.omc/artifacts/productization-residual-matrix.md` complete; every AC has ≥1 row; AC-2 Naga and AC-4 parent-envelope rows explicit.

**Barrier:** Wave1 must not start until matrix exists.

---

## Wave 1 — Parallel fix lanes

Dispatch **only rows with decision=fix or revert**. Empty lane → write “no work” note and exit.

### Lane L1 — Host Build (`executor`)

**Owns:** `host/renpy-host/src/main.rs`, warnings in host crate (except python.rs ownership conflicts → coordinate), `lib.rs`, `tests/host_tests.rs`, `phase1_gates.sh`, Cargo.toml/lock as needed.  
**Must not:** composer.py, golden baselines, parent_runner semantics.

| Step | Action | Verify |
|---|---|---|
| L1.1 | Apply matrix reverts/fixes for AC-1 reds/yellows (Duration/u64, dead mut, fmt) | `cargo fmt --check` |
| L1.2 | Clear warnings under `-D warnings` | `RUSTFLAGS='-D warnings' cargo check --workspace --all-targets` |
| L1.3 | Keep/extend unit tests (benchmark ns, zero-SDL assertions) | `cargo test --workspace` |
| L1.4 | Ensure phase1 script is executable and SDL checks remain | `./host/scripts/phase1_gates.sh` |

**Provisional note:** `.omc/artifacts/closeout-lane-l1.md`

### Lane L2 — Shader / FFI (`executor`)

**Owns:** `shader.rs`, `python.rs`, `state.rs` composer field, `renpy/wgpu/composer.py`, `draw.py`.  
**Must not:** baseline writes; parent_runner.

| Step | Action | Verify |
|---|---|---|
| L2.1 | Confirm FFI 6-tuple wiring + Python unpack (`ComposerResult`) | unit/integration or gate |
| L2.2 | **Naga subclaim:** either implement Naga validate with line errors, **or** leave residual/red (do not claim equivalence for custom `validate_wgsl_syntax` without explicit approval row) | `cargo test -p renpy-host shader::` + matrix note |
| L2.3 | Register-on-HostState before GPU; bootstrap-safe `register_shader_part` | early-register test or gate |
| L2.4 | Run composer gate quartet | `RENPY_HOST_GATE=… cargo run -p renpy-host` ×4 |

**Provisional note:** `.omc/artifacts/closeout-lane-l2.md`  
**If Naga not done this pass:** status residual on AC-2 Naga row; do not block other AC-2 subclaims from separate checkoff language in ledger (parent AC stays open unless all subclaims green — **parent AC-2 checks off only if all four subclaims green**).

### Lane L3 — Golden strict (`test-engineer` + `executor`)

**Owns:** `host/python/gates/golden_mae.py`, `tests/test_golden_mae.py`.  
**Default revert:** all dirty `actual.*`.

| Step | Action | Verify |
|---|---|---|
| L3.1 | `git checkout --` / restore dirty `actual.*` (and unapproved `baseline.*`) | `git status` clean on those paths |
| L3.2 | Ensure compare path never writes baseline; missing → non-zero | `pytest tests/test_golden_mae.py -q` |
| L3.3 | Add/keep tests for dim mismatch, missing baseline, MAE fail | same pytest |

**Provisional note:** `.omc/artifacts/closeout-lane-l3.md`

### Lane L4 — Runner + G01–G08 (`executor`)

**Owns:** `host/scripts/runner/parent_runner.py`, `host/scripts/run_golden_tests.sh`, G0x invocation wiring.  
**Must not:** bulk delete gates; L2 files.

| Step | Action | Verify |
|---|---|---|
| L4.1 | Keep parent_runner 6-field envelope as authority | `pytest tests/test_parent_runner.py -q` |
| L4.2 | **Wire** `run_golden_tests.sh` (or replacement entry) to invoke `parent_runner.py` for each G01–G08 so final verdict/envelope is parent-owned | script audit + dry run |
| L4.3 | Run G01–G08 through parent runner; collect envelopes under `host/target/envelopes/` or similar | all 8 green or list failures |
| L4.4 | Do not bulk-resign baselines; per-failure fix or residual | matrix update note |

**Suggested parent invocation shape (implementer may adjust paths):**
```bash
python3 host/scripts/runner/parent_runner.py \
  --envelope-out host/target/envelopes/g01.json \
  --declared-input testcases/wgpu_golden/G01_*/baseline.rgba \
  -- cargo run -p renpy-host
# with RENPY_HOST_GATE=g01 in env
```

**Provisional note:** `.omc/artifacts/closeout-lane-l4.md`

### Lane L5 — Inventory / HMC / BC-160 perimeter (`executor` or `writer`)

**Owns:** `.omx/gates-inventory.json` metadata fixes only, `run_huangmeic_playtest.sh` read-only guards, `benchmark_bc160.sh` runnability docs/flags.  
**Must not:** re-promote 7900XT; delete 134 gates.

| Step | Action | Verify |
|---|---|---|
| L5.1 | Validate inventory `total_gates==134` and tier fields present | python assert |
| L5.2 | HuangmeiC smoke; assert recovered tree clean (`git status` / mtime guard as script provides) | playtest smoke exit 0 or residual |
| L5.3 | Confirm `.omx/context/historical-7900xt/` present | path exists |
| L5.4 | benchmark script runs help/dry; full BC-160 suite optional | help exit 0; full suite residual OK |

**Provisional note:** `.omc/artifacts/closeout-lane-l5.md`

### Wave1 barrier

All lanes finished (or no-op notes) before Wave2. If L2 and L4 both need host binary behavior, L4 runs after L2 merges or on integrated tree in Wave2 only for final G0x — prefer integrate then verify.

---

## Wave 2 — Integrate + verify

**Agents:** `verifier` (required), `security-reviewer` (light, optional parallel)

### Task W2.1 — Integrated AC command battery

**Agent:** `verifier`  
Re-run on the **integrated** tree (not lane worktrees in isolation):

```bash
# AC-1
cd host && cargo fmt --check
RUSTFLAGS='-D warnings' cargo check --workspace --all-targets
cargo test --workspace
cd .. && ./host/scripts/phase1_gates.sh

# AC-2
cd host && cargo test -p renpy-host shader::
# + Naga subclaim audit result from matrix (pass only if truly Naga or approved equiv)
# + composer gates x4

# AC-3
cd <repo> && pytest tests/test_golden_mae.py -q

# AC-4
pytest tests/test_parent_runner.py -q
# G01-G08 via parent_runner entry (not shell-only loop)

# AC-5
python3 -c "import json;d=json.load(open('.omx/gates-inventory.json')); assert d['total_gates']==134"
# HuangmeiC smoke if feasible

# AC-6
test -d .omx/context/historical-7900xt
# release aggregates or explicit fail → residual
```

Write `.omc/artifacts/productization-verifier-report.md` with per-subclaim: command, exit, verdict.

### Task W2.2 — Light security / contract pass

**Agent:** `security-reviewer`  
- `ldd host/target/debug/renpy-host | grep -i SDL` must be empty  
- recovered project path not modified  
- no secrets in new scripts  

Append to verifier report.

### Checkoff rule

- Parent AC green **only if all mandatory subclaims green**.  
- Else AC stays open and appears in residual ledger with subclaim breakdown.

---

## Wave 3 — Closeout writeback

**Agents:** `writer`, optional `git-master`

### Task W3.1 — Residual ledger

**Agent:** `writer`  
Create `.omc/artifacts/productization-residual-ledger.md`:

For each non-green subclaim:
- ac_id / claim  
- status  
- reason  
- evidence  
- suggested owner agent for follow-up  
- blocks_checkoff: yes/no for parent AC  

### Task W3.2 — Consensus AC boxes

**Agent:** `writer`  
Edit `.omc/plans/wgpu-host-productization-consensus.md`:
- Flip only verifier-green parent ACs to `- [x]`  
- Optionally annotate residuals under Plan Status  
- Do not claim release-ready if AC-6 residual  

### Task W3.3 — Optional ralplan truth-up

**Agent:** `writer`  
If desired: set factual fields on `.omx/state/ralplan-wgpu-host-productization.json` reflecting closeout (do not fake `consensus_complete` unless planning state truly closed). Prefer a note in residual ledger if ambiguous.

### Task W3.4 — Optional atomic commits

**Agent:** `git-master`  
Suggested commit slices (only if user wants commits):
1. `fix(host): build contract + unit tests` (L1)  
2. `feat(host): native shader composer / FFI` (L2)  
3. `fix(golden): strict fail-closed mae` (L3)  
4. `feat(runner): parent envelope owns G01-G08` (L4)  
5. `chore(evidence): inventory/hmc/bc160 perimeter` (L5)  
6. `docs: checkoff AC + residual ledger` (W3)  

Exclude `actual.*` noise. Do not commit secrets or recovered project writes.

---

## 3. OMP / Task dispatch map

Use one `task` batch per wave. Example Wave0:

```text
context: closeout plan + spec paths + no edits
tasks:
  - name: CloseoutScout
    agent: scout
    task: W0.1–W0.3 produce residual matrix
```

Wave1 (after matrix):

```text
tasks:
  - name: LaneL1Build
    agent: executor
    task: only matrix rows owner=L1
  - name: LaneL2Shader
    agent: executor
    task: only matrix rows owner=L2
  - name: LaneL3Golden
    agent: test-engineer
    task: only matrix rows owner=L3
  - name: LaneL4Runner
    agent: executor
    task: only matrix rows owner=L4
  - name: LaneL5Evidence
    agent: executor
    task: only matrix rows owner=L5
```

Wave2:

```text
tasks:
  - name: CloseoutVerifier
    agent: verifier
    task: W2.1 full battery + report
  - name: CloseoutSecLite
    agent: security-reviewer
    task: W2.2 SDL + readonly
```

Wave3:

```text
tasks:
  - name: CloseoutWriter
    agent: writer
    task: W3.1–W3.3
  - name: CloseoutGit
    agent: git-master
    task: W3.4 if user requested commits
```

---

## 4. Definition of done

1. Residual matrix + verifier report + residual ledger exist.  
2. Every parent AC is checked off **or** residualed with subclaim detail.  
3. No claimed-green path still has known false-green (implicit baseline write, shell-only AC-4, Naga lie).  
4. Dirty noise (`actual.*`) resolved.  
5. User can start follow-up work from residual ledger alone.

## 5. Out of scope follow-ups (ledger fodder, not this plan)

- Full release_acceptance.v1 SSOT packaging  
- True Naga integration if deferred  
- Bulk gate promotion/deletion program  
- Cross-platform hosts  

---

## 6. Immediate next action

1. User approves this plan (or requests edits).  
2. Leader dispatches **Wave0 CloseoutScout** only.  
3. After matrix lands, dispatch Wave1 lanes in one parallel batch.  
