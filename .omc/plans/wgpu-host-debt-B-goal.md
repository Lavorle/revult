# Goal Document: wgpu-host Debt B — 真修 (ruff true 0 + bench 1% low + pass duration)

## Go / No-Go
- **Judgment**: Go
- **Reason**: A 封版 `3774893c/51cfe269d` 已绿但落下 `host/python 191` narrow、bench `null` 两笔显债；范围明确、无新 product 决策，可直接还债。

## Target Outcome
`host/python` 无 bulk noqa 真 0（`ruff check renpy/wgpu host/python` PASS），`bc160_perf_metrics.v1.json` 的 `one_percent_low_fps` 与 `render_pass_duration_ns` 非 null 且 `release_evidence_eligible true`，`draw_*` 拆分有最小回归覆盖；所有验证在 `cargo fmt/check/test 34 + ruff 0 + golden 8/8 + bench MEASURED` 下全绿。

## Goal Definition
- **Type**: quality / technical debt
- **Boundary**:
  - 包含：`host/python` ruff 真修（配置 + 代码）、`host/renpy-host/src/main.rs` bench 1% low 计算、`host/renpy-host/src/arena.rs` 或 `gpu.rs` 的 `render_pass_duration_ns` 采集（timestamp 或 CPU 代理）、`host/scripts/benchmark_bc160.sh` 同步、最小 `draw_*` 单测补齐。
  - 不包含：新渲染特性、Live2D/assimp 逻辑改动、全量重写 `host/python` 异常策略为窄异常。
- **Non-goals**:
  - 不把 `BLE001/S110` 全部改为窄异常（host 垫片故意 swallow，保证 parity）
  - 不引入 UI/游戏逻辑变更
  - 不改 `renpy/wgpu` 行为（已 0）
- **Deferred work**:
  - `sphinx/source/changelog.rst` 上游条目（C）
  - packaging / multi-GPU
  - 完整 `draw_*` 100% 覆盖（仅最小回归）
- **Verification rule**: `ruff check renpy/wgpu host/python 2>&1 | grep -q 'All checks passed'` 且 `ruff check host/python/gates` 仍 0；`bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && jq -e '.one_percent_low_fps != null and .render_pass_duration_ns != null and .pass_status=="PERFORMANCE_TARGET_MET"'`；`cargo test --workspace` 34 + 新增 draw 单测；`bash host/scripts/run_golden_tests.sh` 8/8。
- **Evidence source**: commands + artifacts
- **Pass criteria**: 同 Verification rule 全满足；`host/python` 无 `bulk noqa`（`grep -r "# noqa: BLE001,S110" host/python | wc -l` ==0 或仅保留必要单行 noqa 且在 `pyproject.toml` 中显式 `per-file-ignores` 披露）。
- **Confidence note**: 高 — ruff 与 bench 均为确定性本地命令；bench 1% low 依赖 per-frame 计时，需对比 `total_time/frames` 与实测分布一致性。
- **Judgment owner**: `ruff` + `benchmark_bc160.sh --measured` + `cargo test` + `parent_runner` 8/8

## Current State
- **A 产物**: `CHANGELOG v0.6.0`, `release_acceptance 25d2748` PASS, `host target 2994fps`, `ruff renpy/wgpu 0 gates 0 narrow` 但 `ruff check host/python` 191（BLE001 92 + S110 35 + F401 37 + RET501 11 等）。`host/python/gates` 0 靠 bulk 135 文件单行 noqa 实现。
- **Bench**: `host/renpy-host/src/main.rs:242-265` 仅写 `total_time/avg/min/max`，未收集 per-frame 列；`host/scripts/benchmark_bc160.sh:167,200` 硬写 `one_percent_low_fps: null, render_pass_duration_ns: null`；`host/target/bc160_perf_metrics.json` 同 null。
- **Draw**: `renpy/wgpu/draw.py 615 + draw_*.py 7 文件 4560 行` 已拆但无独立单测，仅 `test_wgpu_composer.py 5` 例。
- **约束**: `host/python` 的 `except Exception: pass` 多为故意容错（保证 Ren'Py 不崩），不应全改为窄异常；需用 `pyproject.toml` 显式放宽。
- **风险**: 粗暴 `--fix` 会改语义；bench per-frame 存储需防 1800 规模内存/精度问题；render_pass 计时若用 wgpu timestamp 需 feature 探测回退。

## Priority Rationale
1. **先 Ruff 配置** — 区分“故意宽”与“真错”（F401/F841/UP031 等），先让 `per-file-ignores` 落地，再 `--fix` 真错，避免 bulk。
2. **再 Bench 计时** — 需改 Rust 采集 + 脚本透传，改动面小但需验证 1% low 计算正确性，必须在 Ruff 绿后做（避免格式化干扰）。
3. **后 Draw 单测** — 仅最小回归，防拆分回归，不阻塞前两步。

## Assumptions and Open Decisions
| Item | Status | Impact | Owner / Next step |
|------|--------|--------|-------------------|
| BLE001/S110 是否全放宽 | assumed 放宽于 `host/python` 非 gates | 否则需改 127 处 swallow，风险高 | 在 `pyproject.toml [tool.ruff.lint.per-file-ignores]` 为 `host/python/**/*.py = ["BLE001","S110"]` 显式放宽，并在 CHANGELOG 披露 |
| F401/F841/UP031 等真错是否 `--fix` | assumed `--fix` 安全 | 自动修可能改 import 语义 | 先 `--fix --select F401,F841,UP031,...` 试跑，人工复核 `draw*` 的 `F401` 是否为 `F401,F403` 故意 re-export |
| 1% low 定义 | assumed 1800 帧中按帧耗时排序取最慢 1% 的平均 fps | 影响指标可比性 | 取 `sorted frame_durations descending` 的前 `ceil(0.01*N)` 平均，`fps = 1e9 / avg_ns` |
| render_pass_duration 来源 | unresolved | wgpu timestamp 需 `TIMESTAMP_QUERY` feature，非所有后端支持 | 优先用 `Instant` 围住 `encoder.begin_render_pass`..`submit` 的 CPU 时间作为代理；若 `wgpu` 支持 timestamp 则记录两者并取 CPU 为 null 回退 |
| draw 单测范围 | assumed 仅 `rtt_pool`, `host_texture` 纯逻辑 | 避免启动 GPU | 用 `pytest` 纯 Python，不依赖 `renpy_host` |

## Phases

### Phase B1: Ruff 真 0
- **Purpose**: 去 bulk，显式配置 + 真错修复，使 `ruff check renpy/wgpu host/python` 真绿。
- **Entry condition**: `git status` 干净，`ruff --version 0.16.x`。
- **Phase rules**:
  - 禁止新增 `bulk noqa` 或 ` # noqa: BLE001,S110` 全文件头
  - 允许 `pyproject.toml` 中 `per-file-ignores` 放宽 `BLE001/S110` 于 `host/python/**/*.py`（需注释原因：host shim intentional swallow）
  - `renpy/wgpu` 必须保持 0，不得为修 host 而放宽其规则
  - 每步后 `ruff check` 计数必须下降，不得新增
- **Todos**:
  - [ ] 审计 host/python 191 归类
    - **Surface**: `pyproject.toml`, `host/python/**/*.py`
    - **Proof**: `ruff check host/python 2>&1 | grep -oE "[A-Z]+[0-9]+" | sort | uniq -c | tee host/target/ruff-B1-triage.log`
    - **Depends on**: none
  - [ ] 配置 per-file-ignores 放宽 intentional
    - **Surface**: `pyproject.toml`
    - **Proof**: `grep -q 'host/python' pyproject.toml && ruff check host/python --select BLE001,S110 2>&1 | grep -q 'All checks passed' || test $(ruff check host/python --select BLE001,S110 2>&1 | grep -c BLE001) -eq 0`
    - **Depends on**: 审计
  - [ ] 自动修真错 (F401,F841,UP031,UP045,I001 等可 fix)
    - **Surface**: `host/python/_renpy_host.py`, `host/python/host_pygame/*.py`, `host/python/renpy_*.py`
    - **Proof**: `ruff check host/python --fix --select F401,F841,UP031,UP045,I001,RUF100,RUF046,RET501,PLR1711 2>&1 | tee host/target/ruff-B1-fix.log && ruff check host/python 2>&1 | tee host/target/verify-ruff-B1.log; grep -q 'All checks passed' host/target/verify-ruff-B1.log`
    - **Depends on**: 配置
  - [ ] 手修剩余不可 fix (F403 re-export, TRY002 等) 逐文件 noqa 单行
    - **Surface**: `host/python/host_pygame/__init__.py` 等
    - **Proof**: `ruff check renpy/wgpu host/python 2>&1 | tee host/target/verify-ruff.log && grep -q 'All checks passed' host/target/verify-ruff.log`
    - **Depends on**: 自动修
- **Exit proof**: `ruff check renpy/wgpu host/python` All checks passed，且 `grep -r "bulk noqa" host/python` 0，`pyproject.toml` 中放宽有注释。
- **Stop condition**: 若某 F401 是故意 re-export（如 `from .locals import *`），保留 `F401,F403` noqa 单行而非删 import。

### Phase B2: Bench 1% low + pass duration
- **Purpose**: 让 `bc160_perf_metrics` 完整，`release_evidence_eligible` 依赖真实分布而非 null。
- **Entry condition**: B1 绿，`cargo check` 0。
- **Phase rules**:
  - `main.rs` 需存 per-frame `Duration` 列（Vec 1800），计算 `one_percent_low_fps`
  - `render_pass_duration_ns` 优先 CPU 代理（`Instant` 围 `begin_render_pass..submit`），若支持 `wgpu::Features::TIMESTAMP_QUERY` 则加 query
  - 脚本与 JSON schema 保持兼容（null -> number），旧 release 仍可解析
- **Todos**:
  - [ ] 扩展 `main.rs` 采集 per-frame
    - **Surface**: `host/renpy-host/src/main.rs`, `host/renpy-host/src/lib.rs` (calculate helper)
    - **Proof**: `cd host && cargo check --workspace --all-targets 2>&1 | tee ../host/target/verify-check.log && cargo test --workspace 2>&1 | tee ../host/target/verify-test.log; grep -q 'ok' ../host/target/verify-test.log`
    - **Depends on**: B1
  - [ ] 同步 `benchmark_bc160.sh` 透传新字段
    - **Surface**: `host/scripts/benchmark_bc160.sh`
    - **Proof**: `grep -q 'one_percent_low_fps' host/scripts/benchmark_bc160.sh && grep -q 'render_pass_duration_ns' host/scripts/benchmark_bc160.sh`
    - **Depends on**: main.rs
  - [ ] 重采 1800 并验非 null
    - **Surface**: `host/target/bc160_perf_metrics.json`, `/tmp/bench_1800.json`
    - **Proof**: `bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && jq -e '.one_percent_low_fps != null and .render_pass_duration_ns != null' host/target/bc160_perf_metrics.json && cat host/target/bc160_perf_metrics.json | python3 -m json.tool | head -n 20`
    - **Depends on**: 脚本
- **Exit proof**: `jq` 非 null 且 `one_percent_low_fps < average_fps` 且 `render_pass_duration_ns < frame_presentation_time_ns`。
- **Stop condition**: 若 wgpu timestamp 不可用，`render_pass_duration_ns` 取 CPU 代理并在 notes 标注 `cpu_proxy: true`，不阻塞。

### Phase B3: 最小 Draw 回归 + 全量复验
- **Purpose**: 防拆分回归，闭环 B。
- **Entry condition**: B1+B2 绿。
- **Phase rules**:
  - 单测仅覆盖纯逻辑（rtt_pool 尺寸键、host_texture fingerprint、composer 不依赖 GPU）
  - 全量复验必须 `cargo fmt/check/test 34 + 8/8 + ruff 0 + bench MEASURED` 全绿才可 commit
- **Todos**:
  - [ ] 新增 `tests/test_wgpu_draw_split.py` (rtt + fingerprint)
    - **Surface**: `tests/test_wgpu_draw_split.py`, `renpy/wgpu/rtt_pool.py`, `renpy/wgpu/host_texture.py`
    - **Proof**: `PYTHONPATH=. python -m pytest tests/test_wgpu_draw_split.py -v 2>&1 | tee host/target/verify-draw-split.log && grep -q 'passed' host/target/verify-draw-split.log`
    - **Depends on**: B2
  - [ ] 全量复验
    - **Surface**: `host/target/verify-*.log`, `.omc/artifacts/release_acceptance.v1.json` (可选重绑)
    - **Proof**: `cd host && cargo fmt --check && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets && cargo test --workspace && bash host/scripts/run_golden_tests.sh && ruff check renpy/wgpu host/python && bash host/scripts/phase1_gates.sh` 均 0
    - **Depends on**: 单测
- **Exit proof**: 全绿日志 + `ruff 0` + `bench 1% low` 非 null。
- **Stop condition**: 任一金库失败即停。

## Dry-Run Findings
- `host/python` 191 中 `BLE001 92 + S110 35` 占 2/3，属 intentional，需配置而非代码改；剩余 `F401 37` 多为未用 imports，可 `--fix`。
- `main.rs` 当前 `benchmark_*` 仅 `total/min/max`，缺 `Vec<Duration>`；需加 `benchmark_frames_durations: Vec<Duration>` 并注意 `Duration::MAX` 初始化。
- `benchmark_bc160.sh` 当前硬写 null，改后需同时更新 `.omc/artifacts/bc160_perf_metrics.v1.json` 示例。
- `draw_*` 7 文件已拆，但 `rtt_pool.py` 的 `cap 8` + `host_texture.py` 的 `blake2b 8B` 逻辑可纯测，无需 GPU。

## Final Validation
```bash
ruff check renpy/wgpu host/python && echo ruff0
bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && jq -e '.one_percent_low_fps != null and .render_pass_duration_ns != null' host/target/bc160_perf_metrics.json && cat host/target/bc160_perf_metrics.json | python3 -m json.tool
cd host && cargo fmt --check && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets && cargo test --workspace
bash host/scripts/run_golden_tests.sh | tail -n 5
```

## First Execution Step
`ruff check host/python 2>&1 | grep -oE "[A-Z]+[0-9]+" | sort | uniq -c | tee host/target/ruff-B1-triage.log` 已完成，下一步：编辑 `pyproject.toml` 增加 `host/python/**/*.py` 的 `BLE001,S110` per-file-ignores 显式放宽。
