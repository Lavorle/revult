# Goal Document: wgpu-host Debt D — 真 GPU 计时 (TIMESTAMP_QUERY) + 打包预研

## Go / No-Go
- **Judgment**: Go
- **Reason**: C 已闭环 `sphinx 8.99.99 + badge + cpu_proxy`（`325f51ac6`），`B` 真 0 亦绿（`935fcebc3`）。剩余显债仅 `render_pass_duration_ns` 仍为 `Instant` 代理（`render_pass_cpu_proxy: true`，`benchmark_render_pass_total` 等于 `total_time`）、`TIMESTAMP_QUERY` 未接线、打包/AppImage 无预研。范围单一、无新渲染特性、回退路径已在 C 定义，可直接还债。

## Target Outcome
`host/renpy-host` 在 `BC-160 radv NAVI12` 上：
- 若 `adapter.features().contains(TIMESTAMP_QUERY)` 为 true，则 `bc160_perf_metrics.v1.json` 的 `render_pass_duration_ns` 来自 `wgpu QuerySet` 真 GPU 时间（`timestamp_period` 换算），`render_pass_cpu_proxy: false`，`render_pass_duration_ns < frame_presentation_time_ns` 且 `~ 0.3× avg` 量级；
- 否则保留 `Instant` CPU 代理但 `render_pass_cpu_proxy: true` 且 `notes` 显式标注 `cpu_proxy: TIMESTAMP_QUERY not supported on this adapter`，判定仍为 PASS（不强求硬件）；
- `one_percent_low_fps` 已在 B 落地，保持非 null；
- 附 `doc/packaging-investigation.md` 预研（sdist vs AppImage vs renpy-build 决策，不改构建）；
- 全量 `cargo fmt/check/test 34 + ruff 0 + golden 8/8 + bench MEASURED` 绿，`release_evidence_eligible true` 保持。

## Goal Definition
- **Type**: quality / operational / pre-packaging
- **Boundary**:
  - 包含：`host/renpy-host/src/gpu.rs` 中 `request_device` 按需请求 `Features::TIMESTAMP_QUERY`、`GpuState` 增 `query_set: Option<QuerySet> + query_resolve_buffer + timestamp_period`、`host/renpy-host/src/arena.rs` 的 `encode_pass_into` 与 `render_clear` 增 `timestamp_writes`（pass 首尾各 1）、`host/renpy-host/src/main.rs` 中 `benchmark_render_pass_total` 改为 GPU 真值或显式 cpu_proxy 分支并透传到 bench JSON、`host/scripts/benchmark_bc160.sh` 同步 `render_pass_cpu_proxy` 字段与 notes、`doc/packaging-investigation.md` 新增。
  - 不包含：新管线/着色器、Live2D/assimp/video 逻辑、双树剥离、lavapipe 多 GPU 矩阵、真实 AppImage 构建。
- **Non-goals**:
  - 不改 `renpy/wgpu/*.py` 任何逻辑（已 0）
  - 不重写 `host/python` 异常策略
  - 不做多平台、不发版（tag 留到 D 之后）
  - 不把 `TIMESTAMP_QUERY` 不支持判为 FAIL
- **Deferred work**:
  - 真实 `AppImage/sdist/renpy-build` 构建与签名（E）
  - `TIMESTAMP_QUERY` 的 per-draw 细分（仅 pass 级 avg 即可）
  - WebGPU timestamp 的 async 精度校准（仅需 avg 正确性，不追单帧抖动）
- **Verification rule**: 
  - `grep -q "TIMESTAMP_QUERY" host/renpy-host/src/gpu.rs && grep -q "timestamp_writes" host/renpy-host/src/arena.rs`
  - `bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && jq -e '.render_pass_duration_ns != null and .one_percent_low_fps != null' host/target/bc160_perf_metrics.json`
  - `jq -e '.render_pass_cpu_proxy == false or .render_pass_cpu_proxy == true' host/target/bc160_perf_metrics.json`（二选一显式）
  - `cargo fmt --check && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets && cargo test --workspace` 34
  - `bash host/scripts/run_golden_tests.sh | grep "8 / 8"` + `ruff check renpy/wgpu host/python`
- **Evidence source**: files + commands + JSON (`host/target/bc160_perf_metrics.json`, `/tmp/bench_*.json`)
- **Pass criteria**:
  - `bc160_perf_metrics.json`: `measurement_status==MEASURED`, `pass_status==PERFORMANCE_TARGET_MET`, `release_evidence_eligible==true`, `average_fps>=60`, `one_percent_low_fps < average_fps`, `render_pass_duration_ns < frame_presentation_time_ns`, `render_pass_cpu_proxy` 显式布尔且与 `gpu.rs` 探测一致，`notes` 含 `TIMESTAMP_QUERY` 或 `cpu_proxy` 字样
  - `host/renpy-host/src/gpu.rs` 中 `required_features` 非 `empty()` 时含 `TIMESTAMP_QUERY` 条件分支，`cargo check` 0
  - `doc/packaging-investigation.md` 存在且含 `sdist/AppImage/renpy-build` 三选一建议
- **Confidence note**: 中高 — `TIMESTAMP_QUERY` 在 `radv NAVI12` 上是否可用需实测；不可用则回退分支即为 PASS，风险可控。`wgpu 24` 已支持 `query_set` + `timestamp_writes`，API 风险低。`render_pass` 计时从 `elapsed == total_time` 修正为真值，改动面小但需验证 `timestamp_period` 换算正确。
- **Judgment owner**: `benchmark_bc160.sh --measured` JSON + `gpu.rs` 探测日志 + `cargo/ruff/golden`

## Current State
- **C 产物**: `325f51ac6 chore(docs,bench): C sphinx 8.99.99 + wgsl + badge + render_pass cpu_proxy` — `sphinx/source/changelog.rst 8.99.99` 绿，`host/README` 有 `[![host](workflows/host.yml/badge.svg)]`，`bc160 2714fps avg / 681 1% low / 368357ns render (cpu_proxy true, equal avg)`，`CHANGELOG v0.6.0 9f62ab39c` 已对齐
- **Bench 现状**:
  - `host/renpy-host/src/main.rs:548 benchmark_render_pass_total += elapsed` — `elapsed == frame total`，`main.rs:291 render_pass_cpu_proxy: true` 硬编码
  - `host/renpy-host/src/gpu.rs:164 required_features: Features::empty()` — 未探测 `TIMESTAMP_QUERY`
  - `host/renpy-host/src/arena.rs:1878 timestamp_writes: None` + `host/renpy-host/src/gpu.rs:299 timestamp_writes: None` — 无 query 写入
  - `host/scripts/benchmark_bc160.sh:178 render_pass_cpu_proxy: true` 硬编码，`:183 notes cpu_proxy not wired`
- **CI**: `.github/workflows/host.yml` 已落（`51cfe269d`），Tier1+2 不含 bench 1800，本地 `host/target/bc160_perf_metrics.json` 为 MEASURED
- **Ruff/Golden**: `ruff check renpy/wgpu host/python` 0（`935` 真 0），`run_golden_tests.sh 8/8` via `parent_runner 10 envelopes`
- **风险**: 若直接 `Features::TIMESTAMP_QUERY` 无条件请求，在不支持的 adapter 上 `request_device` 会 FAIL；必须先 `adapter.features().contains` 再条件请求

## Priority Rationale
1. **先探测后接线** — `adapter.features()` 是唯一真值，不可先写 `query_set` 再补探测，否则不支持的机器直接 `request_device` 失败，回退路径必须先定义
2. **再编码与回读** — `arena.rs` 的 `timestamp_writes` 与 `gpu.rs` 的 `query_set/resolve` 必须同一次提交，否则 bench JSON 与日志不一致会导致假绿
3. **后打包预研** — 纯文档，不碰 `host/src`，可与 D1 并行但验收集敛在 D3，避免与计时改动交叉

## Assumptions and Open Decisions
| Item | Status | Impact | Owner / Next step |
|------|--------|--------|-------------------|
| `TIMESTAMP_QUERY` 在 radv NAVI12 是否可用 | unresolved | 决定是真 GPU 还是 cpu_proxy | `gpu.rs: adapter.features().contains(TIMESTAMP_QUERY)` 探测，日志 `info!("timestamp_query supported={}")`，不支持则保留 cpu_proxy 即 PASS |
| `query_set` 数量与位置 | assumed 2 queries (0=start, 1=end) 每 pass | 影响 buffer 大小与 period 计算 | `device.create_query_set(QuerySetDescriptor {count:2, ty: Timestamp})`，`render_pass.timestamp_writes = Some(TimestampWrites {query_set, beginning_of_pass_write_index: Some(0), end_of_pass_write_index: Some(1)})` |
| `timestamp_period` 获取 | assumed `queue.get_timestamp_period()` | 换算 ticks→ns | `let period = queue.get_timestamp_period()` 存于 `GpuState`，`duration_ns = (end-start) as f64 * period` |
| `resolve` 时机 | assumed 每帧 `encoder.resolve_query_set(&query_set, 0..2, &resolve_buffer, 0)` 后 `queue.submit`，再 `buffer.slice(..).map_async` + `device.poll(Wait)` 同步读 | 影响帧延迟 | 采用同步 `pollster::block_on` 读 8 字节×2，失败则回退 cpu_proxy，不阻塞 present（仅 bench 1800 规模可接受） |
| `render_pass` 计时范围 | assumed `arena.encode_pass_into` 的单 `begin_render_pass` 围住的所有 `draw_model`，`main.rs` 累加 `benchmark_render_pass_total` 为 GPU 差值 | 与 frame total 差异 | 若 `TIMESTAMP_QUERY` 可用则用 GPU 差值，否则用原 `elapsed` 并标 `cpu_proxy true` |
| 打包预研深度 | assumed 仅 `doc/packaging-investigation.md` 含 sdist/AppImage/renpy-build 对比与推荐，不产出构建脚本 | 不影响 CI | 若需脚本则移至 E |

## Phases

### Phase D1: 探测与真 GPU 接线
- **Purpose**: `render_pass_duration_ns` 在支持的机器上为真 GPU 时间，否则显式 `cpu_proxy`
- **Entry condition**: `git status` 干净，`325f51ac6` 绿，`cargo check` 0
- **Phase rules**:
  - 禁止无条件 `Features::TIMESTAMP_QUERY`；必须 `if adapter.features().contains(FEATURES::TIMESTAMP_QUERY)` 分支
  - `GpuState` 新增 `query_set: Option<QuerySet>`, `query_resolve_buffer: Option<Buffer>`, `timestamp_period: f32`, `timestamp_supported: bool` 四字段，`arena.rs` 不直接持有 query_set 而通过 `&GpuState` 传入
  - `cpu_proxy` 时 `render_pass_duration_ns` 仍非 null（用 `elapsed`），保持 `release_evidence_eligible true`
  - 单 query/buffer 大小固定 16 字节，不随帧数增长
- **Todos**:
  - [ ] 探测并条件请求 `TIMESTAMP_QUERY`
    - **Surface**: `host/renpy-host/src/gpu.rs`
    - **Proof**: `grep -q "TIMESTAMP_QUERY" host/renpy-host/src/gpu.rs && grep -q "timestamp_supported\|query_set" host/renpy-host/src/gpu.rs && cd host && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets 2>&1 | tail -1 | grep -q "Finished"`
    - **Depends on**: none
  - [ ] `arena.rs` 接 `timestamp_writes` 与 `resolve_query_set`
    - **Surface**: `host/renpy-host/src/arena.rs` (`encode_pass_into`, 新增 `resolve_timestamp_query` 或内联)
    - **Proof**: `grep -q "timestamp_writes" host/renpy-host/src/arena.rs && grep -q "resolve_query_set" host/renpy-host/src/arena.rs && cd host && cargo check --workspace --all-targets 2>&1 | tail -1 | grep -q "Finished"`
    - **Depends on**: gpu.rs
  - [ ] `main.rs` 真值透传与 `render_pass_cpu_proxy` 动态化
    - **Surface**: `host/renpy-host/src/main.rs` (`benchmark_render_pass_total` 累加改为 GPU 差值或条件分支，JSON 中 `render_pass_cpu_proxy` 由 `gpu.timestamp_supported` 决定)
    - **Proof**: `grep -q "render_pass_cpu_proxy" host/renpy-host/src/main.rs && grep -q "TIMESTAMP_QUERY\|timestamp_supported\|get_timestamp_period" host/renpy-host/src/main.rs && cd host && cargo check --workspace --all-targets 2>&1 | tail -1 | grep -q "Finished"`
    - **Depends on**: arena.rs
  - [ ] 同步 `benchmark_bc160.sh` 的 `render_pass_cpu_proxy` 与 notes
    - **Surface**: `host/scripts/benchmark_bc160.sh`
    - **Proof**: `grep -q "render_pass_cpu_proxy" host/scripts/benchmark_bc160.sh && grep -q "TIMESTAMP_QUERY\|cpu_proxy" host/scripts/benchmark_bc160.sh`
    - **Depends on**: main.rs
- **Exit proof**: `grep -c "TIMESTAMP_QUERY" host/renpy-host/src/gpu.rs >=1` 且 `grep -q "TIMESTAMP_QUERY\|cpu_proxy" host/target/bc160_perf_metrics.json`（重采后），`cargo check -D warnings` 0
- **Stop condition**: 若 `radv` 不支持，保留 `cpu_proxy true` + 日志 `timestamp_query supported=false` 即为 PASS，不强求 `false→true`

### Phase D2: 打包预研（文档）
- **Purpose**: 明确下一步发行形态，不改构建
- **Entry condition**: D1 `cargo check` 绿（可与 D1 并行）
- **Phase rules**:
  - 只新增 `doc/packaging-investigation.md`，不改 `host/scripts` 或 `pyproject.toml`
  - 必须含三选一对比表（sdist / AppImage / renpy-build）与推荐（默认推荐 `sdist + host binary` 分离，AppImage 作为可选）
  - 明确 `TIMESTAMP_QUERY` 与打包无耦合
- **Todos**:
  - [ ] 撰写 `doc/packaging-investigation.md`
    - **Surface**: `doc/packaging-investigation.md`
    - **Proof**: `test -f doc/packaging-investigation.md && grep -q "AppImage" doc/packaging-investigation.md && grep -q "sdist\|renpy-build" doc/packaging-investigation.md`
    - **Depends on**: none
- **Exit proof**: 文件存在且含对比表与推荐结论
- **Stop condition**: 无 — 文档失败不阻塞 D1/D3

### Phase D3: 全量复验
- **Purpose**: 闭环 D，证据与 C 同级可比
- **Entry condition**: D1+D2 绿
- **Phase rules**: 同 B3/C3，必须 `cargo fmt/check/test 34 + 8/8 + ruff 0 + bench MEASURED` 全绿
- **Todos**:
  - [ ] 重采 1800 并验 `render_pass < frame`（或 `cpu_proxy` 显式）
    - **Surface**: `host/target/bc160_perf_metrics.json`, `/tmp/bench_1800.json`
    - **Proof**: `bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && jq -e '.render_pass_duration_ns != null and .render_pass_duration_ns < .frame_presentation_time_ns' host/target/bc160_perf_metrics.json || (jq -e '.render_pass_cpu_proxy==true' host/target/bc160_perf_metrics.json && echo "cpu_proxy fallback PASS") && cat host/target/bc160_perf_metrics.json | python3 -m json.tool | head -n 25`
    - **Depends on**: D1
  - [ ] 全量复验 8 项
    - **Surface**: `host/target/verify-*.log`
    - **Proof**: `cd host && cargo fmt --check 2>&1 | tee ../host/target/verify-fmt-D.log && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets 2>&1 | tee ../host/target/verify-check-D.log && cargo test --workspace 2>&1 | tee ../host/target/verify-test-D.log && ruff check renpy/wgpu host/python 2>&1 | tee ../host/target/verify-ruff-D.log && bash host/scripts/run_golden_tests.sh 2>&1 | tee ../host/target/verify-golden-D.log && grep -q "8 / 8" ../host/target/verify-golden-D.log && grep -q "All checks passed" ../host/target/verify-ruff-D.log`
    - **Depends on**: 重采
- **Exit proof**: `host/target/verify-*.log` 均 0，`bc160_perf_metrics.json` 中 `one_percent_low_fps` 非 null 且 `average_fps >=60`，`render_pass_cpu_proxy` 显式且与 `gpu.rs` 探测一致
- **Stop condition**: 任一金库失败即停；`TIMESTAMP_QUERY` 不支持不视为失败

## Dry-Run Findings
- `host/renpy-host/src/gpu.rs:164` 当前 `required_features: Features::empty()`，`host/renpy-host/src/arena.rs:1878 timestamp_writes: None`，`host/renpy-host/src/main.rs:548 benchmark_render_pass_total += elapsed` 且 `291 render_pass_cpu_proxy: true` 硬编码 — 三处需同改
- `wgpu 24` 在 `Linux Vulkan` 上 `Features::TIMESTAMP_QUERY` 需 `Instance::new(Backends::VULKAN)` 已满足，`DeviceDescriptor::required_features` 条件请求已在 `wgpu 24` 上验证可行；`queue.get_timestamp_period()` 在 `wgpu 24` 为 `f32`（需确认签名，若为 `f64` 则同步调）
- `benchmark_bc160.sh:124-147` 已解析 `one_percent_low_fps` 与 `render_ns` 来自 `bench JSON`，但 `178 render_pass_cpu_proxy: true` 硬编码 — D1 后需改为从 `bench JSON` 的 `render_pass_cpu_proxy` 透传（`bench JSON` 新增布尔字段）
- `main.rs:246-301` 的 bench JSON 写入为 `format!` 手拼，需同步改 `render_pass_cpu_proxy` 为变量而非字面 `true`
- `doc/packaging-investigation.md` 不存在 — D2 新建，无冲突

## Final Validation
```bash
# 1. 探测与接线存在
grep -q "TIMESTAMP_QUERY" host/renpy-host/src/gpu.rs
grep -q "timestamp_writes" host/renpy-host/src/arena.rs
grep -q "render_pass_cpu_proxy" host/renpy-host/src/main.rs
# 2. 编译与测试
cd host && cargo fmt --check && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets && cargo test --workspace | tail -n 10
# 3. 重采并验
bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json
cat host/target/bc160_perf_metrics.json | python3 -m json.tool
jq -e '.one_percent_low_fps != null and .render_pass_duration_ns != null and .render_pass_duration_ns < .frame_presentation_time_ns' host/target/bc160_perf_metrics.json || jq -e '.render_pass_cpu_proxy==true' host/target/bc160_perf_metrics.json
jq -e '.release_evidence_eligible==true and .pass_status=="PERFORMANCE_TARGET_MET"' host/target/bc160_perf_metrics.json
# 4. 金库与 ruff
ruff check renpy/wgpu host/python && echo ruff0
bash host/scripts/run_golden_tests.sh | tail -n 5
# 5. 打包预研
test -f doc/packaging-investigation.md && grep -q "AppImage" doc/packaging-investigation.md
```

## First Execution Step
编辑 `host/renpy-host/src/gpu.rs:120-172`：在 `adapter.get_info()` 后探测 `let ts_supported = adapter.features().contains(wgpu::Features::TIMESTAMP_QUERY); info!("timestamp_query supported={}", ts_supported);`，将 `request_device` 的 `required_features` 改为 `if ts_supported { Features::TIMESTAMP_QUERY } else { Features::empty() }`，并在 `GpuState` 新增 `timestamp_supported: bool, query_set: Option<wgpu::QuerySet>, query_resolve_buffer: Option<wgpu::Buffer>, timestamp_period: f32`，`Ok(Self{...})` 中初始化（`ts_supported` 时 `create_query_set(2)` + `create_buffer(16, QUERY_RESOLVE|MAP_READ|COPY_DST)` + `queue.get_timestamp_period()`）。
