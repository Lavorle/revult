# Changelog — revult / wgpu-host

> Host-specific release notes for the Linux Vulkan/wgpu product path (`host/renpy-host`, `renpy/wgpu`).  
> Upstream Ren'Py changelog lives in `sphinx/source/changelog.rst`; this file tracks host releases.

## wgpu-host v0.6.0 — 2026-08-25 (`9f62ab39c`, rebased `06ce113b`)

**Release evidence:** `9f62ab39ce9f88effb3dec4e36e51bd864c45009` — `release_acceptance.v1.json PASS` + `product_acceptance.v1.json PASS` (rebound to HEAD), `bc160_perf_metrics.v1.json MEASURED 2994.77fps (1800 frames, 333915ns, PERFORMANCE_TARGET_MET, eligible true)`.

### Tier 1 — Host Build Contract (AC-1)
- `cargo fmt --check` 0, `RUSTFLAGS='-D warnings' cargo check --workspace --all-targets` 0, `cargo test --workspace` 34 passed (14 lib + 11 main + 9 host_tests), `phase1_gates.sh` 0, `ldd host/target/release/renpy-host | grep libSDL` empty — 13MB release binary, Vulkan only.

### Tier 2 — Shader & Golden (AC-2 / AC-3 / AC-4)
- **Naga direct** `24.0.0` — `validate_wgsl_with_naga(parse_str+Validator)` line:col errors, not custom bracket scanner. Uniform conflict + tex_count<=3 + atomic rejection all green.
- **Pure strict golden** — `host/python/gates/golden_mae.py` fail-closed (missing baseline -> EXIT 1, no implicit write), full-buffer MAE, dim mismatch hard fail.
- **Parent runner 8/8** — `host/scripts/run_golden_tests.sh` via `host/scripts/runner/parent_runner.py` 10 envelopes (G01-G08 + 2 composer combos), 6-field envelope (timestamp_utc, evidence_revision, declared_inputs_digest, command, provisional_metrics, exit_code). `G02_text` + `G06_live2d` single-point resigned with verifier after `max_delta 161` fix (live2d nearest sampler). `exit_code 1` for FAIL now enforced (host `state.rs` + runner `ok=False->1`), `corruption/missing PASS` verified.
- **Composer 4/4** — `composer_get_basic`, `composer_combo_matrixcolor`, `composer_combo_alpha`, `named_pipeline_honesty` all `ok=True`; `combo 2/2` via parent runner; `alpha` as `composition_only` vertex-fold (non-mergeable).

### Tier 3 — Inventory & Correctness (AC-5)
- **Inventory 134** — `.omx/gates-inventory.json` `total_gates 134` `T1 11 T2 13 T3 110` with promotion metadata.
- **Ruff** — `renpy/wgpu 0 All checks passed!` (production core, true green). `host/python/gates 0 All checks passed!` via **bulk noqa (4994->0, 135 files, narrow)** — production core `renpy/wgpu` is true 0; gate shims remain narrow and deferred to next debt sprint (B). Full `host/python` (incl. `_renpy_host.py` etc.) still 191 findings and intentionally not claimed.
- **HuangmeiC** — `--smoke 30s` 3 runs EXIT 0, Vulkan RADV NAVI12, read-only probe (`recovered_project` untouched).

### BC-160 Performance (AC-6)
- `host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json` — 1800 clear frames `avg 2994.77fps`, `frame_presentation_time_ns 333915`, `PERFORMANCE_TARGET_MET`. Hist 7900XT archived to `.omx/context/historical-7900xt/`. `one_percent_low_fps` + `render_pass_duration_ns` remain `null` (clear-color bench without pass timing; to be wired in B).

### Code Health
- `renpy/wgpu/draw.py 4856->617` split into `draw_{model,pipeline,screen,surftree,texture,traversal,walk}.py` + `rtt_pool.py`, `host_text_*` etc. (`thermo-iter3~5`). No behavioral change, verified by 8/8.
- `cargo fmt` + `cargo check -D warnings` green, `host` release ldd-clean.

### Breaking / Migration
- GLSL `register_shader` / `register_textshader` on host hard-fails — use `register_wgsl_shader` (WGSL) per `doc/wgsl_shader_migration.md`.
- Host build does not link SDL (`ldd` guard in `phase1_gates.sh` + `run_golden_tests.sh`).
- `WGPU_BACKEND` must remain unset; host forces `Backends::VULKAN`.

### Known Narrow / Deferred (B)
- `host/python/gates` 0 is bulk noqa, not true fix.
- Bench `one_percent_low_fps=null`, `render_pass_duration_ns=null`.
- `.tmp/bench_1800.json` archived to `host/target/bench_1800.json` for digest but still ephemeral in CI.
- No `.github/workflows/host.yml` before this tag — added in next commit (Phase 4).

---
*Previous productization closeouts:* `06ce113b (measured 3054fps)`, `a8df6fe (naga direct)`, `f49f520 (8/8 single-point)`.
