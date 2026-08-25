# Goal Document: wgpu-host Debt C — 文档与硬件计时 (Sphinx + TIMESTAMP_QUERY)

## Go / No-Go
- **Judgment**: Go
- **Reason**: B 已真 0 + bench 非 null；剩余显债仅 `sphinx` 上游条目、`render_pass` 仍 CPU 代理、`host README` 无 CI 徽章，无新 product 决策，范围可控。

## Target Outcome
`sphinx/source/changelog.rst` 含 `wgpu-host v0.6.0` 上游条目（shader 迁移 + Vulkan 强制 + 8/8），`doc/wgsl_shader_migration.md` 与 `CHANGELOG.md` 一致，`host/README` 有 CI 徽章指向 `.github/workflows/host.yml`，`bc160_perf_metrics` 的 `render_pass_duration_ns` 来自 `wgpu TIMESTAMP_QUERY`（有 `TIMESTAMP_QUERY` 时）否则 CPU 代理并在 notes 显式标注 `cpu_proxy`，全量 `cargo fmt/check/test 34 + ruff 0 + golden 8/8 + bench MEASURED` 绿。

## Goal Definition
- **Type**: docs / operational / quality
- **Boundary**:
  - 包含：`sphinx/source/changelog.rst` 增量、`doc/wgsl_shader_migration.md` 复核、`host/README` CI badge、`host/renpy-host/src/arena.rs` 或 `gpu.rs` 增 `TIMESTAMP_QUERY` 探测与 query set、`main.rs` 透传真实 GPU 时间、`benchmark_bc160.sh` 标注 `cpu_proxy`。
  - 不包含：新渲染特性、packaging/distributor、`renpy-build` 改动。
- **Non-goals**:
  - 不改 `renpy/wgpu` 逻辑
  - 不重写 `host/python` 异常策略
  - 不做多平台
- **Deferred work**:
  - packaging / AppImage
  - lavapipe 矩阵
- **Verification rule**: `grep -q "wgpu-host v0.6.0" sphinx/source/changelog.rst` 且 `grep -q "TIMESTAMP_QUERY\|cpu_proxy" host/target/bc160_perf_metrics.json`，`jq -e '.render_pass_duration_ns != null'`，`ruff 0`，`cargo test 34`，`run_golden 8/8`。
- **Evidence source**: files + commands + JSON
- **Pass criteria**: 同上 + `host/README` 含 `[![host](actions/workflows/host.yml/badge.svg)]` 且 `sphinx` 构建 `make html` 不报错（若有 `sphinx`）。
- **Confidence note**: 中 — sphinx 条目为文档，`TIMESTAMP_QUERY` 需在 BC-160 radv 上验证是否支持 `wgpu::Features::TIMESTAMP_QUERY`，不支持则回退 CPU 并显式标注。
- **Judgment owner**: `sphinx` 文本 + `benchmark` JSON + `cargo/ruff/golden`

## Current State
- **B 产物**: `935fcebc3` 真 0，`bc160 2870fps one_low 870 render 348394(cpu_proxy, equal avg)`，`CHANGELOG.md` 有 `wgpu-host v0.6.0` 但 `sphinx/source/changelog.rst` 无，`doc/wgsl_shader_migration.md` 未与 `CHANGELOG` 对齐，`host/README` 无 badge。
- **Bench**: `main.rs` 用 `start.elapsed()` 同值作 `render_pass`，`arena.rs` 无 query set。
- **CI**: `.github/workflows/host.yml` 已落但 `host/README` 未引。

## Priority Rationale
1. **先文档** — 无代码风险，`sphinx` 条目与 `wgsl_migration` 对齐可立即验。
2. **后硬件计时** — 需改 Rust + 探测 feature，回退路径必须先定义，否则 bench 假绿。

## Assumptions and Open Decisions
| Item | Status | Impact | Owner / Next step |
|------|--------|--------|-------------------|
| sphinx 条目位置 | assumed 在 `8.6.0` 之后/之前插 `8.99.99 wgpu-host` | 不影响构建 | 插在 `changelog.rst` 顶部 `8.6.0` 之前作为 `8.99.99 wgpu-host v0.6.0` |
| TIMESTAMP_QUERY 在 radv NAVI12 是否可用 | unresolved | 决定是否真 GPU 计时 | 先探测 `adapter.features().contains(TIMESTAMP_QUERY)`，不支持则 `cpu_proxy true` |
| render_pass 计时范围 | assumed `encoder.begin_render_pass`..`queue.submit` 围住 | 与 frame 差异 | 取 GPU timestamp `query_set` 在 `render_clear` 前后，或 CPU `Instant` 围 `encoder` |

## Phases

### Phase C1: 文档闭环
- **Purpose**: 让 `sphinx` 与 `host README` 与 `CHANGELOG` 一致。
- **Entry condition**: `935fcebc3` 绿。
- **Phase rules**:
  - 只改文档，不改 `host/src` 或 `renpy/wgpu`
  - `sphinx` 条目需含 `register_shader -> register_wgsl_shader`、`WGSL`、`Vulkan`、`8/8`
- **Todos**:
  - [ ] 增 `sphinx/source/changelog.rst` 8.99.99 条目
    - **Surface**: `sphinx/source/changelog.rst`
    - **Proof**: `grep -q "wgpu-host v0.6.0" sphinx/source/changelog.rst && grep -q "register_wgsl_shader" sphinx/source/changelog.rst`
    - **Depends on**: none
  - [ ] 复核 `doc/wgsl_shader_migration.md` 与 CHANGELOG 一致
    - **Surface**: `doc/wgsl_shader_migration.md`
    - **Proof**: `grep -q "wgsl_shader_migration" doc/wgsl_shader_migration.md && diff -q <(grep -o "wgpu-host" CHANGELOG.md) <(grep -o "wgpu" doc/wgsl_shader_migration.md) || true`
    - **Depends on**: sphinx
  - [ ] 加 `host/README` CI badge
    - **Surface**: `host/README.md`
    - **Proof**: `grep -q "workflows/host.yml/badge.svg" host/README.md`
    - **Depends on**: sphinx
- **Exit proof**: 三文件 grep 均命中，`ruff` 仍 0。
- **Stop condition**: sphinx 构建若要求 `make`，失败则仅保留 `changelog.rst` 文本，不阻塞。

### Phase C2: 硬件计时
- **Purpose**: `render_pass_duration_ns` 真 GPU 或显式 `cpu_proxy`。
- **Entry condition**: C1 绿。
- **Phase rules**:
  - 探测 `TIMESTAMP_QUERY`，不支持则保留 CPU 代理但 JSON notes 加 `cpu_proxy: true`
  - 不改 bench 阈值（仍 60fps）
- **Todos**:
  - [ ] 探测并实现 query set 或 CPU 代理显式化
    - **Surface**: `host/renpy-host/src/gpu.rs` 或 `arena.rs`, `host/renpy-host/src/main.rs`
    - **Proof**: `grep -q "TIMESTAMP_QUERY\|cpu_proxy" host/renpy-host/src/main.rs && cd host && cargo check --workspace --all-targets 2>&1 | tail -1 | grep -q "Finished"`
    - **Depends on**: C1
  - [ ] 重采并验 notes
    - **Surface**: `host/target/bc160_perf_metrics.json`
    - **Proof**: `bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && jq -e '.render_pass_duration_ns != null' host/target/bc160_perf_metrics.json && cat host/target/bc160_perf_metrics.json | grep -E "one_percent|render_pass|cpu_proxy"`
    - **Depends on**: 实现
- **Exit proof**: `render_pass_duration_ns` 非 null 且 `render_pass <= frame_presentation`，notes 有 `cpu_proxy` 或 `TIMESTAMP_QUERY` 标记。
- **Stop condition**: 若 radv 不支持 timestamp，保留 CPU 代理即为 PASS，不强求 GPU。

### Phase C3: 全量复验
- **Purpose**: 闭环 C。
- **Entry condition**: C1+C2 绿。
- **Phase rules**: 同 B3
- **Todos**:
  - [ ] 全量复验 8 项
    - **Surface**: `host/target/verify-*.log`
    - **Proof**: `cd host && cargo fmt --check && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets && cargo test --workspace && bash host/scripts/run_golden_tests.sh && ruff check renpy/wgpu host/python && bash host/scripts/phase1_gates.sh`
    - **Depends on**: C2

## Dry-Run Findings
- `sphinx/source/changelog.rst` 首条为 `8.6.0`，插入需保持 `.. _renpy-8.99.99:` 锚。
- `arena.rs` 当前 `begin_render_pass` 无 query，需在 `GpuState::new` 时创建 `query_set` 若支持。

## Final Validation
```bash
grep -q "wgpu-host v0.6.0" sphinx/source/changelog.rst
grep -q "workflows/host.yml/badge.svg" host/README.md
jq -e '.one_percent_low_fps != null and .render_pass_duration_ns != null' host/target/bc160_perf_metrics.json
ruff check renpy/wgpu host/python && echo ruff0
cd host && cargo fmt --check && cargo test --workspace && bash host/scripts/run_golden_tests.sh | grep "8 / 8"
```

## First Execution Step
编辑 `sphinx/source/changelog.rst` 在 `8.6.0` 前插入 `8.99.99 wgpu-host v0.6.0` 条目。
