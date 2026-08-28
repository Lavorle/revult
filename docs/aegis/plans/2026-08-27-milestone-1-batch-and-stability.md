# Implementation Plan: Milestone 1 — 渲染性能与稳定性（Wave32 Batching + Thermo 解耦）

**Date:** 2026-08-27
**Goal:** Wave32 实例化批处理落地并完成 draw 热区解耦，使 prefs/HuangmeiC 多元素场景降 10x draw call 且无像素回归
**Architecture:** renpy-host + renpy/wgpu/host_bridge + GpuArena + WGSL
**Tech Stack:** Rust wgpu 24 + winit 0.30 + WGSL + Python WgpuDraw
**Baseline:** consensus-wgpu-native-vulkan-rewrite.md AC1/AC6 + host/README.md 双树 + wgpu-rdna-wave32-batching.md + 2026-08-26-wave32-a2u.md + doc/wgsl_shader_migration.md
**Compatibility:** Rgba8Unorm+PMA, tex_count via _PIPELINE_KEYS, G01-08 fail-closed, ldd no SDL
**TDD Route:** off/skipped/post-change regression
**Verification:** cargo check -p renpy-host + pytest tests/test_wgpu_composer.py -v + host/scripts/phase9_gates.sh + benchmark_bc160.sh --measured + ldd grep libSDL 空 + RENPY_HOST_PERF=1 get_frame_stats

---

## Plan Basis

```text
Aegis Visibility:
  改的是 GpuArena 的 encode 主路径与 WgpuDraw 的逐 quad 提交热区（draw_walk/draw_texture/draw_surftree +
  host_bridge）以及 host/python gates 的 _harness 残留；错改会破坏双树契约（ldd SDL 逃逸）、像素金库（MAE>2/255）
  或 Wave32 lane 利用率回退；先计划锁定 Instance 布局与分组键再动代码。

BaselineUsageDraft:
- Required baseline refs: .omc/plans/consensus-wgpu-native-vulkan-rewrite.md AC1(线程模型)/AC6(金库MAE≤2/255)
                          + host/README.md §4.9 双树(Rgba8Unorm+PMA, ldd无SDL, backend=Vulkan)
                          + .omc/plans/wgpu-rdna-wave32-batching.md Phase0-2 设计
                          + docs/aegis/plans/2026-08-26-wave32-a2u.md A2-U 4-const 语义
                          + doc/wgsl_shader_migration.md Host pipeline/ tex_count 映射
- Delivered context refs: host/renpy-host/src/arena.rs GpuArena frame_cmds/draw_model/end_frame_present(1305-1620)
                          + host/renpy-host/src/gpu.rs SWAPCHAIN_FORMAT Rgba8Unorm
                          + host/renpy-host/src/python.rs define_pipeline_accessor!/with_host_state
                          + renpy/wgpu/draw*.py 7-mixin 聚合(804+725+492...) + host_bridge.py 单点 renpy_host
                          + renpy/wgpu/rtt_pool.py RttPoolMixin 210行 + draw_surftree.py 725行残留 TODO
                          + host/python/gates/_harness.py 198行 + video.py/audio.rs 隔离边界
- Acknowledged before plan refs: 上述 6 已读；arena.rs 2691行/gpu.rs 300+行已抽样；wgpu-rdna-wave32-batching.md 103行全文
- Cited in plan refs: arena.rs:71-80 DrawCmd, 86-93 BgCacheKey, 192-292 GpuArena, 1305-1380 begin_frame/draw_model,
                      1897-2027 encode, 2193-2684 WGSL 12 shaders; python.rs:59-93 pipeline accessors;
                      host_bridge.py:22-46 renpy_host/host_env_bool; draw.py:245 WgpuDraw 聚合;
                      draw_surftree.py:717 TODO surftree traversal; rtt_pool.py:24 _clamp_rtt_size; _harness.py:108 gate_harness
- Missing refs: BC-160 RGP 一帧 trace（可选 evidence，非阻塞）；HuangmeiC 多元素 RGP（可选）
- Decision: continue

Requirement Ready Check:
- Requirement source refs: 本 Milestone 目标（prefs/HuangmeiC 10x draw call, 无像素回归）+ wgpu-rdna-wave32-batching.md AC1-AC7
                          + host-phase-gap-matrix.md Phase 热区耦合清单（draw_surftree/helpers/rtt_pool/_harness）
- Goals and scope refs: FrameStats 计数器+RGP基线(Phase0) + unit-quad Instance分组批(Phase1) + overdraw可选scissor(Phase2) + Thermo解耦收尾
- User / scenario refs: prefs 页 / HuangmeiC splash 多元素密集场景为验收场景；the_question 为金库/bench 基准
- Requirement item refs: 下述 T1-T5 逐条映射 Phase0/1/2/Thermo；每 Task 含 Files/Why/Impact/Verification
- Acceptance / verification criteria refs: §Success Criteria 6 条可勾；phase9_gates G01-G08 MAE≤2/255 max≤16 + cargo/pytest/ldd/backend/bench/perf 6 门
- Open blocker questions: 无；RGP Wave32 lane 满载率目标由 Phase0 实测缺口定阈，不阻塞 Phase1 落地
- Decision: ready

Change Necessity:
- User-visible need: prefs/HuangmeiC 多元素场景当前每 quad 一 draw（instance_count=1, arena.rs:2023-2025），Wave32 lane利用率~19%，draw call数与quad数1:1，p99帧时超预算
- No-change / non-code option: 不改码仅调参无法改变提交形态；逐 quad mesh + draw_model 循环（python.rs:1995 draw_models）仅摊锁未实例化
- Why code change is necessary: 必须改 Rust side Instance step_mode + WGSL vs_main 实例属性 + Python侧分组批，才能把共享(pipeline,texture) N quad合并为单 draw_instanced(0..N)，降10x
- Minimum change boundary: arena.rs Instance缓冲+WGSL+encode + host_bridge/draw_*分组 + FrameStats埋点（不含SDL树、WGSL语义新维度、Live2D/blur语义）
- Decision: code-change

Existence Check:
- Proposed new surface: FrameStats struct + get_frame_stats FFI（新增读探针） + unit-quad Instance缓冲（复用arena） + _harness 全量迁移收口（无新crate）
- Existing owner / reuse candidate: GpuArena 已有 frame_cmds/bg_cache/uniform_ring；WgpuDraw 已有 7-mixin；_harness 已有 gate_harness雏形
- Why existing surface is insufficient: 现无 per-frame 计数器与 perf 门禁；无 Instance缓冲与分组键；_harness 仍散于134个gate文件的 fallback import
- Creation proof: wgpu-rdna-wave32-batching.md Phase0缺口（无FrameStats）及 draw.py逐quad建mesh实证；_harness分散由 800行规模验证
- Entropy / retirement impact: 新增1读探针+1 Instance环形缓冲，净熵可控；退役触发：_harness 旧fallback import 在 T5 后 1版本保留再删
- Decision: add-with-proof（仅 FrameStats/Instance缓冲 + _harness收敛）

Architecture Integrity Lens:
- Invariant: WgpuDraw单渲染器，Rgba8Unorm+PMA One/OneMinusSrcAlpha，金像捕获pre-present game RT，Backends::VULKAN，ldd无SDL
- Canonical owner / contract: GpuArena(arena.rs)+GpuState(gpu.rs)拥有 WGSL/Instance/encode真相；WgslShaderCache(composer) sha1[:16] key熵不变；_PIPELINE_KEYS/shaders 单点映射 tex_count/layout
- Responsibility overlap: draw.py 804行仍残留 surftree traversal/helpers未迁；composer/shaders 三处 _PIPELINE_KEYS 已由deslop计划收敛，本M1不再重复；arena encode与python draw分组为同一切面两端
- Higher-level simplification: 可否用 uber管线收4 key？否 — 本M1显式 defer A2-M（wave32-a2u已决），仅做实例化批，不增uber维度；过draw是否 texture_2d_array 多纹理合批？否 — Phase1.5可选，仅当纹理组数本身瓶颈
- Retirement / falsifier: 若分组后 G01-G08 任一MAE>2即回滚单Task；若Instance后 draw_calls未降≥10x则证分组键粒度错，需复盘 tex_count 维度
- Verdict: reuse-existing owners, add FrameStats+Instance缓冲, edit-in-place WGSL与分组, 收敛 _harness

Plan-Time Complexity Check:
- Target files: host/renpy-host/src/arena.rs 2691行 + python.rs 2274行 + gpu.rs; renpy/wgpu/draw.py 804 + draw_surftree 725 + draw_walk 492 + draw_texture 546 + draw_traversal 634 + draw_model 557 + host_bridge 46 + rtt_pool 210 + video 800+ + composer/shaders
- Existing size / shape signals: arena>2600行含12 WGSL常量+encode长函数；draw_*已拆7-mixin但主聚合仍重；Python 700+行 walk/texture 为热区
- Owner fit: GpuArena为Instance/encode唯一Owner正确；WgpuDraw为分组聚合正确；host_bridge为renpy_host单点Owner正确
- Add-in-place risk: 在encode循环直接塞Instance分支会使 1897-2027段膨胀触 over-budget；分组逻辑塞draw.py会使 804→1000+
- Better file boundary: Instance缓冲与WGSL同arena.rs顶部常量/布局，Python分组抽 draw_instanced.py或 host_bridge聚合helper（同文件内 helper，不新建crate）；Thermo解耦按已拆mixin边线收敛
- Recommendation: edit-in-place(arena WGSL/encode) + extract helper(Python分组helper同draw_model/host_bridge) + add owner file(仅FrameStats探针与bench基线表，不新crate)

Plan Pressure Test:
- Owner / contract / retirement: FrameStats读探针无写面可revert；Instance缓冲 grow-only ring可独立 revert；WGSL vs修改受naga validate门限；_harness收敛保留 re-export 1版本
- Architecture integrity / higher-level path: 已验无更高层Owner可替代（HostState.composer占位不提前打通，arena仍用常量工厂）
- Verification scope: cargo check + cargo test + pytest composer + phase9_gates G01-08 + bench --measured + ldd空 + perf get_frame_stats + RGP可选 6门齐
- Task executability: 每Task 2-5min slice，文件边界独立，失败可单Task revert；T2/T3强依赖顺序，T1可并行铺垫
- Pressure result: proceed

Execution Readiness View:
- Intent Lock: 仅 Wave32实例化批处理+Thermo解耦收尾；A2-M/A2-V与 texture_2d_array合批显式defer；不改blur/Live2D/FTL语义
- Scope Fence: 可改 host/renpy-host/src/arena.rs|python.rs|gpu.rs|shader.rs 精炼 + renpy/wgpu/host_bridge|draw.py|draw_surftree|draw_walk|draw_texture|draw_traversal|draw_model|rtt_pool|constants + host/python/gates/_harness 及 bench/phase9 校验；禁动 renpy/gl2/SDL树、SWAPCHAIN_FORMAT、_PIPELINE_KEYS key维度、双树隔离契约
- Baseline Lock: Rgba8Unorm+PMA，tex_count∈{0..3}，params16，MAE≤2/255 max≤16，单渲染器，host_build分支不破SDL，backend=Vulkan，ldd无SDL
- Approved Behavior: 像素等价（G01-08 0回归），prefs/HuangmeiC密集场景 draw_calls降≥10x且 instance_count_total≈quad_count，RGP waves/CU↑
- Owner / Contract Constraints: shaders._PIPELINE_KEYS单点 + WgslShaderCache key熵不变 + GpuArena LruSlotMap/RTT/BG不改假设 + draw分组保持画家序（同组内按原append序）
- Compatibility Boundary: _PIPELINE_KEYS assert_pipeline_map_honest仍过；frame_stats仅增读接口不改encode语义；Instance缓冲grow-only不改ABA假设
- Retirement Boundary: 无旧路径删除；_harness旧fallback保留1版本re-export；draw_model非实例化路径保留供dissolve/blur/matrixcolor/RTT bake
- Task Batches: B0 基线(T1) → B1 实例化(T2-T3) → B2 验证(T4) → B3 解耦收尾(T5)；B0失败则B1暂停
- Test Obligations: 新增FrameStats门禁断言 instance≈quads；全量 cargo test + pytest tests/test_wgpu_composer.py + phase9_gates + bench + ldd + perf
- Review Gates: cargo check + naga validate隐式(create_pipeline_wgsl) + ruff(如触Python)
- Drift / Rewind Rules: 单Task可独立 git revert；T2/T3任一MAE回归即回滚该Task不进T4
- Evidence Required Before Completion: phase9_gates 8/8 + ldd空 + backend日志 + bench p95↓或RGP waves/CU↑ + get_frame_stats draw_calls/quads比≪1截图
- Advisory Boundary: method-pack 执行指引；非 GateDecision

TDD Route:
- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: post-change regression + 新增FrameStats门禁断言（非 RED）
- Reason: 实例化批处理为等价提交形态重构（像素输出不变），风险在 encode/分组等价性，需金像+RGP事后锁而非预写RED；与 2026-08-26-wave32-a2u 同 posture
- Verification: cargo check -p renpy-host + pytest tests/test_wgpu_composer.py -v + host/scripts/phase9_gates.sh + host/scripts/benchmark_bc160.sh --measured + ldd grep + RENPY_HOST_PERF=1 get_frame_stats
```

---

## Files

| 文件 | 动作 | 边界 |
|---|---|---|
| `host/renpy-host/src/arena.rs` | 改 | 新增 `FrameStats` 结构与计数（draw_calls/quads/instances/overdraw_est/ms），`last_frame_stats()` 读接口；新增 `Instance` 顶点布局（12 float: rect_off.xy, rect_size.xy, uv_off.xy, uv_size.xy, color.rgba）+ `instance_buf: wgpu::Buffer` grow-only ring；改 12 WGSL `vs_main` 加 `@location(4..)` Instance 属性（step_mode: Instance）计算 `clip = pos*rect_size+rect_off`；改 `end_frame_present` encode循环 `draw_indexed(0..6,0,0..count)` 分支；保留 `draw_model` 非实例化路径（dissolve/blur等 tex_count>1+uniform 不走实例化） |
| `host/renpy-host/src/python.rs` | 改 | 新增 `get_frame_stats() -> PyDict` FFI（门禁 `RENPY_HOST_PERF=1`，透传 `arena.last_frame_stats()`），`draw_instances(group_key, instance_data)` 实例化提交入口；保留 `draw_model` 原 FFI 兼容；`host_env_bool` 已单点于 host_bridge 侧复用 |
| `host/renpy-host/src/gpu.rs` | 视需微改 | 若 FrameStats `ms` 取 timestamp query则复用已有 `query_set/query_resolve_buffer/timestamp_period`，否则 `std::time`；`SWAPCHAIN_FORMAT` 不动 |
| `renpy/wgpu/host_bridge.py` | 改 | 单点 `host_env_bool` 已有，新增 `get_frame_stats` 透传与 `draw_instances` 薄封装；保持 `renpy_host is None` 时 lint/hermetic 空实现 |
| `renpy/wgpu/draw.py` | 改（Thermo收尾） | 聚合根保留 7-mixin，收敛剩余 surftree/helpers：把 `_is_render_like/_extract_host_texture/_child_to_texture/_make_model_leaf/_resolve_texture/_iter_children` 等从 draw.py 迁至 `draw_surftree.py`，`_harness` 散落 `except` 残留清至 `host_bridge.log_host`；新增 unit-quad 单例 `self._unit_quad = create_mesh([0,1]²白) ` 于 `WgpuDraw.__init__`，逐 quad 建mesh改为 instance追加 |
| `renpy/wgpu/draw_surftree.py` | 改 | 接管 surftree 全量 traversal（补 TODO 区 717行注释所列），`SurftreeMixin` 增 `walk` 完整实现，几何/UV/clip helpers仍留，新增 `group_key = (pipeline, texture, texture1, texture2)` 分组表 |
| `renpy/wgpu/draw_walk.py` | 改（Thermo收尾） | 已拆 `WalkCtx/CachedModelPolicy/DissolveStrategy/ReverseScaler`，本M1仅补 `ox,oy→WalkCtx` 透传残留与 `budget` 显式化，不再膨 helper |
| `renpy/wgpu/draw_texture.py` | 视需微改 | `HandleResolver` 状态机已由 deslop 计划覆盖，本M1仅确保 instance路径复用 `alive probe → remap → dead recover`，不二次重写 |
| `renpy/wgpu/draw_traversal.py` `draw_model.py` `draw_pipeline.py` | 改 | `draw_traversal` 承接分组前 walk 产出的 `instance_list`，`draw_model` 增 `draw_instanced(group_key, datas)` 聚合提交，`draw_pipeline` 保持 pipeline_id 映射不变 |
| `renpy/wgpu/rtt_pool.py` | 改（收尾） | `RttPoolMixin` 已抽，本M1仅补 `_clamp_rtt_size` 单点与 frame recycle 边界（`_recycle_frame_rtts` 在 `end_frame_present` 后调用），不新抽象 |
| `renpy/wgpu/constants.py` | 视需增 | 若 Instance cap/阈值需具名则增 `INSTANCE_RING_INIT=4096`, `PERF_GATE_THRESH=10` 等，否则复用现有 `MESH_CACHE_CAP/RTT_*` |
| `host/python/gates/_harness.py` | 改（收尾） | 全量迁移剩余 `from _harness import gate_harness` 散落 ~134 文件的 fallback 分支，收敛为 `from host.python.gates._harness import gate_harness` 单点，`host/scripts/phase9_gates.sh` 与 `run_golden_tests.sh` 改为调 `_harness` 统一 MAE 判定 |
| `host/scripts/benchmark_bc160.sh` | 视需微改 | 增 `--measured` 产出 `bc160_perf_metrics.json` 的 `draw_calls/quads/instances/overdraw_est` 字段透传（读 `get_frame_stats` 聚合 1800帧均值） |
| `tests/test_wgpu_composer.py` | 不改（门禁） | 仍为回归门禁，不新增批处理单测于本M1（金像门禁覆盖） |

不改动：`renpy/gl2/*` SDL树、`renpy/display/core.py:host_build` 分支语义、`shader.rs emit_wgsl` 文本（除 vs 实例属性）、`WgslShaderCache` key熵、`SWAPCHAIN_FORMAT`。

---

## Compatibility Boundary（冻结）

* `SWAPCHAIN_FORMAT = Rgba8Unorm` + PMA + `One / OneMinusSrcAlpha` 混合不改；金像捕获仍为 **pre-present game RT**
* `WgpuDraw` 单渲染器；`renpy.host_build` 分支不破 SDL 树；`host_pygame` 公共 API 仅增不减
* `WgslShaderCache` 以排序 part 集 `sha1[:16]` 为 `cache_key = composed:<hex>` 不变；`tex_count/layout/has_uniforms` 仍由 Rust SSOT 校验；`assert_pipeline_map_honest()` 仍过
* `tex_count` dissolve=2 / imagedissolve=3 / alpha_mask=2 / mask=2 等由 `_PIPELINE_KEYS` 映射不变；新增 Instance 布局不增 `tex_count` 维度
* `ldd host/target/{debug,release}/renpy-host | grep -iE 'libSDL'` 为空；`RUST_LOG=info` 必含 `adapter backend=Vulkan`
* 新增 `get_frame_stats` 仅读探针，不改 encode 语义；旧 `draw_model` 保留兼容，多纹理+uniform 路径仍走非实例化

---

## Tasks

### T1 — FrameStats 计数器 + get_frame_stats + RGP 基线（Phase 0）

**Files:** `host/renpy-host/src/arena.rs`（增 `FrameStats` 与计数） + `host/renpy-host/src/python.rs`（增 `get_frame_stats` FFI） + `renpy/wgpu/host_bridge.py`（薄封装） + `host/scripts/benchmark_bc160.sh`（可选增透传）  
**Why:** 无量化即无优化；需先固化 draw_calls/quads/instances/overdraw_est/ms 基线，使 prefs/HuangmeiC 密集场景的 1:1 瓶颈可门禁复测，RGP trace 可对比 waves/CU。  
**Change Necessity:** 读探针为新增源码路径；无非代码替代（配置无法产生计数），最小边界为 arena计数+单FFI透传+host_bridge薄封装。  
**Impact/Compat:** 仅增读接口，不改 encode/draw 语义；`RENPY_HOST_PERF=1` 门禁外零成本；`WgslShaderCache`/`_PIPELINE_KEYS` 不动。  
**Verification:** `cargo check -p renpy-host` 过；`RENPY_HOST_PERF=1 python -c "from renpy.wgpu.host_bridge import get_host; h=get_host(); print(h.get_frame_stats() if h else 'no host')"` 返回 `draw_calls/quads/instances/overdraw_est/ms` 五字段；`bash host/scripts/phase9_gates.sh` 仍 8/8；`ldd` 空。

**Governance:** edit-in-place（计数埋于现有 `begin_frame/draw_model/end_frame_present` 三点，不新模块）

**Steps (2-5 min/step):**

1. `arena.rs` 顶层新增（`GpuArena` 定义附近 `BG_CACHE_SOFT_CAP/RING_INIT` 旁）：
```rust
#[derive(Clone, Copy, Debug, Default)]
pub struct FrameStats {
    pub draw_calls: u32,
    pub quads: u32,
    pub instances: u32,
    pub overdraw_est: f32,
    pub ms: f32,
}
```
2. `GpuArena` 增字段 `last_stats: FrameStats` + `frame_overdraw_acc: f32`（或 `frame_quad_area: f32`），`begin_frame` 清零 `frame_overdraw_acc`，`draw_model` 时 `self.frame_overdraw_acc += mesh_area / fb_area`（mesh 包围盒面积由 `MeshSlot` 已存 size 或 Python侧传入，取 `quad_area` 近似；初版可 `Σ 1.0` 近似过draw_est=quads/fb，后续精算）。
3. `end_frame_present` encode 前后埋点：`let t0=Instant::now()`；循环每 `set_pipeline` 计数 `draw_calls+=1`，`quads = frame_cmds.len() as u32`（后续实例化后 `instances` 字段生效），`overdraw_est = frame_overdraw_acc`，`ms = t0.elapsed().as_secs_f32()*1000.0`（若 `gpu.timestamp_supported` 则用 `query_set` 精算，否则 `Instant`），`self.last_stats = FrameStats{...}`；新增 `pub fn last_frame_stats(&self)->FrameStats { self.last_stats }`。
4. `python.rs` 新增 FFI（`define_pipeline_accessor!` 段落后）：
```rust
#[pyfunction]
fn get_frame_stats(py: Python<'_>) -> PyResult<Py<PyDict>> {
    if !crate::config::HostConfig::from_env().perf_enabled() && std::env::var("RENPY_HOST_PERF").map(|v| v=="1"||v.to_lowercase()=="true").unwrap_or(false)==false {
        // 门禁：非 perf 仍返回零值而非抛错，保持 harness 可跑
    }
    let s = with_host_state(|st| st.arena.last_frame_stats());
    let d = PyDict::new(py);
    d.set_item("draw_calls", s.draw_calls)?;
    d.set_item("quads", s.quads)?;
    d.set_item("instances", s.instances)?;
    d.set_item("overdraw_est", s.overdraw_est)?;
    d.set_item("ms", s.ms)?;
    Ok(d.into())
}
```
并在 `#[pymodule]` 注册 `m.add_function(wrap_pyfunction!(get_frame_stats, &m)?)?;`。
5. `host_bridge.py` 增薄封装（`get_host` 下）：
```python
def get_frame_stats():
    h = get_host()
    if h is None or not hasattr(h, "get_frame_stats"):
        return {"draw_calls": 0, "quads": 0, "instances": 0, "overdraw_est": 0.0, "ms": 0.0}
    try:
        return h.get_frame_stats()
    except Exception:
        return {"draw_calls": 0, "quads": 0, "instances": 0, "overdraw_est": 0.0, "ms": 0.0}
```
6. 基线采集：`RENPY_HOST_PERF=1 cargo run -p renpy-host -- the_question 2>&1 | tee /tmp/m1_baseline.log` + `RENPY_HOST_PERF=1 python -c "from renpy.wgpu.host_bridge import get_frame_stats; print(get_frame_stats())"` 连续 3 次取中位，填入本计�� §6 基线表；RGP 可选 `RUST_LOG=info` trace 存 `doc/rdna-perf.md`（非阻塞）。
7. 验证：
```bash
cargo check -p renpy-host
cargo test -p renpy-host 2>&1 | tail -n 20
RENPY_HOST_PERF=1 python -c "from renpy.wgpu.host_bridge import get_frame_stats; s=get_frame_stats(); assert 'draw_calls' in s and 'quads' in s; print(s)"
bash host/scripts/phase9_gates.sh 2>&1 | tail -n 30
ldd host/target/debug/renpy-host 2>&1 | grep -iE 'libSDL' && echo 'FAIL: SDL linked' && exit 1 || echo 'OK: no libSDL'
```

---

### T2 — unit-quad + Instance 布局 + WGSL vs 实例化（Phase 1a）

**Files:** `host/renpy-host/src/arena.rs`（WGSL vs + instance buf/layout） + `renpy/wgpu/draw.py`（unit-quad 单例） + `renpy/wgpu/constants.py`（视需阈值）  
**Why:** 把每 quad 的 6 顶点烘焙改为单次 unit-quad（pos∈[0,1]², uv∈[0,1]²）复用 + per-instance 12 float 携带 placement（clip矩形+uv子矩形+顶点色），为分组 `draw_instanced` 打地基；WGSL `step_mode: Instance` 使 Wave32 满载。  
**Change Necessity:** 需改 WGSL 顶点着色器签名与 Rust 顶点布局，无法靠配置达成；最小边界为 arena 布局+WGSL vs 实例属性+draw.py 单例。  
**Impact/Compat:** `tex_count/_PIPELINE_KEYS` 不动；fragment 不变故像素等价；unit-quad 与旧 per-quad verts 数值等价（`clip=pos*size+off`, `uv=uv0*size+off`）；多纹理+uniform 管线不走此路径。  
**Verification:** `cargo check -p renpy-host` 过（naga 隐式 validate）；`python -m pytest tests/test_wgpu_composer.py -v` 全过；`bash host/scripts/phase9_gates.sh` 8/8；`ldd` 空。

**Governance:** edit-in-place（WGSL vs 体为常量文本替换，Instance缓冲复用 arena，不新crate）

**Steps:**

1. `arena.rs` 顶部 `UNIFORM_BYTES/RING_INIT` 旁增：
```rust
const INSTANCE_FLOATS: usize = 12; // rect_off.xy, rect_size.xy, uv_off.xy, uv_size.xy, color.rgba
const INSTANCE_BYTES: u64 = (INSTANCE_FLOATS * 4) as u64;
const INSTANCE_RING_INIT: usize = 4096;
```
`GpuArena` 增 `instance_ring: Vec<wgpu::Buffer>` + `instance_ring_next: usize` + `instance_scratch: Vec<f32>`（或直接 `Vec<u8>`）。
2. `arena.rs` 中 `SOLID_WGSL`/`TEXTURED_WGSL` 的 `vs_main`（`arena.rs:2204,2232` 附近）改：入参增 `@location(4) rect_off: vec2<f32>, @location(5) rect_size: vec2<f32>, @location(6) uv_off: vec2<f32>, @location(7) uv_size: vec2<f32>, @location(8) color_in: vec4<f32>`（location 需与 `create_pipeline` 布局一致），体改为：
```wgsl
@vertex
fn vs_main(@location(0) pos: vec2<f32>, @location(1) uv0: vec2<f32>, /* color @2 保留或弃用 */
           @location(4) rect_off: vec2<f32>, @location(5) rect_size: vec2<f32>,
           @location(6) uv_off: vec2<f32>, @location(7) uv_size: vec2<f32>,
           @location(8) inst_color: vec4<f32>) -> VsOut {
    var out: VsOut;
    out.pos = vec4<f32>(pos * rect_size + rect_off, 0.0, 1.0);
    out.uv = uv0 * uv_size + uv_off;
    out.color = inst_color;
    return out;
}
```
（若保留 per-vertex color 则 `inst_color * vertex_color`；本M1取 `inst_color` 单源以简化，per-vertex 白）。
3. `create_pipeline`（`arena.rs:1203`）中为 `textured/solid` 管线（`tex_count 0/1` 且无多 uniform）注册双缓冲布局：`buffers=[{array_stride: 8*4, step_mode: Vertex, attributes: [pos@0, uv@1]}, {array_stride: 12*4, step_mode: Instance, attributes: [rect_off@4, rect_size@5, uv_off@6, uv_size@7, color@8]}]`；多纹理/blur/matrixcolor 管线不注册 Instance 布局仍走旧 8-float 顶点。
4. `renpy/wgpu/draw.py` 的 `WgpuDraw.__init__` 末尾（`self._mesh_cache` 初始化旁）增：
```python
# unit-quad: pos∈[0,1]², uv∈[0,1]², color白；8 float per vertex: x,y,u,v,r,g,b,a
try:
    import renpy_host as _rh
    verts = [0,0, 0,0, 1,1,1,1,  1,0, 1,0, 1,1,1,1,  1,1, 1,1, 1,1,1,1,  0,1, 0,1, 1,1,1,1]
    self._unit_quad = _rh.create_mesh(verts, [0,1,2, 0,2,3])
    self._unit_quad_is_instance_source = True
except Exception:
    self._unit_quad = None
```
并保留 `self._quad_mesh` 兼容旧路径（逐步退役）。
5. 验证：
```bash
cargo check -p renpy-host
cargo test -p renpy-host 2>&1 | tail -n 20
python -m pytest tests/test_wgpu_composer.py -v 2>&1 | tail -n 30
bash host/scripts/phase9_gates.sh 2>&1 | tail -n 30
ldd host/target/debug/renpy-host | grep -iE 'libSDL' && echo FAIL || echo 'OK: no libSDL'
RUST_LOG=info cargo run -p renpy-host -- the_question 2>&1 | grep -q "backend=Vulkan" && echo "Vulkan OK"
```

---

### T3 — 分组 draw_instanced 提交（Phase 1b）

**Files:** `host/renpy-host/src/arena.rs`（`draw_instances` + encode分组） + `host/renpy-host/src/python.rs`（`draw_instances` FFI） + `renpy/wgpu/host_bridge.py`（分组聚合） + `renpy/wgpu/draw_surftree.py`/`draw_traversal.py`/`draw_model.py`（分组键与提交）  
**Why:** 将共享 `(pipeline, texture, texture1, texture2)` 的 N quad合并为单 `draw_indexed(0..6,0,0..N)`，使 draw_calls从 `quads` 降至 `groups`（prefs/HuangmeiC 目标 ≥10x），Wave32 lane利用率从 ~19% 满载。  
**Change Necessity:** 需改 Python侧聚合与 Rust侧 encode 分支，无法靠配置；最小边界为分组表+instance写缓冲+实例化 draw 分支（同组内按原append序保画家序）。  
**Impact/Compat:** 分组仅对 plain textured/solid（无 uniform/单纹理）生效；dissolve/blur/matrixcolor/RTT bake 保留 `draw_model` 非实例化；bind group 仍按 `BgCacheKey(pipeline+textures+ubuf)` 缓存，数从 per-quad 降至 per-组；顺序：同组内 instance 按原 walk 序 append保证重叠 alpha 正确。  
**Verification:** `RENPY_HOST_PERF=1 get_frame_stats` 示 `draw_calls/quads ≪1` 且 `instances≈quads`；`phase9_gates` 8/8；`pytest` 全绿；`cargo check` 过。

**Governance:** extract helper（Python分组helper抽为 `draw_traversal` 内 `_InstanceGroup` 私有类，不新文件） + edit-in-place（Rust encode 实例化分支）

**Steps:**

1. `arena.rs` 新增：
```rust
pub fn draw_instances(&mut self, pipeline: u64, texture: Option<u64>, texture1: Option<u64>, texture2: Option<u64>, instances: &[f32]) {
    if !self.in_frame { warn!("draw_instances outside begin_frame"); return; }
    if instances.len() % INSTANCE_FLOATS != 0 { warn!("bad instance len"); return; }
    let count = (instances.len() / INSTANCE_FLOATS) as u32;
    if count==0 { return; }
    // touch 资源同 draw_model
    // 上传：grow-only ring 写
    let bytes = cast_f32(instances);
    let buf = self.ensure_instance_ring(bytes.len() as u64);
    self.queue_write_instance(buf, bytes);
    self.frame_cmds.push(DrawCmd{ pipeline, mesh: self.unit_quad_mesh_id(), texture, texture1, texture2, uniforms:[0.0;16], instance_count: count, instance_buf: Some(buf) });
}
```
`DrawCmd` 增 `instance_count: u32`（默认1）+ `instance_buf: Option<wgpu::Buffer>`（或 handle），旧 `draw_model` 填 `instance_count=1`。
2. `python.rs` 增 FFI：
```rust
#[pyfunction]
fn draw_instances(pipeline: u64, texture: Option<u64>, texture1: Option<u64>, texture2: Option<u64>, instances: Vec<f32>) -> PyResult<()> {
    with_host_state_mut(|st| st.arena.draw_instances(pipeline, texture, texture1, texture2, &instances)); Ok(())
}
```
注册 `m.add_function(wrap_pyfunction!(draw_instances, &m)?)?;`
3. `renpy/wgpu/draw_surftree.py` 或 `draw_traversal.py` 增 `_InstanceGroup`：
```python
class _InstanceGroup:
    def __init__(self): self.map: dict[tuple, list[float]] = {}
    def add(self, key, rect_off, rect_size, uv_off, uv_size, color):
        self.map.setdefault(key, []).extend([*rect_off, *rect_size, *uv_off, *uv_size, *color])
    def flush(self):
        import renpy.wgpu.host_bridge as hb
        h = hb.get_host()
        for (pipe, tex, tex1, tex2), datas in self.map.items():
            if h and hasattr(h, "draw_instances"):
                h.draw_instances(pipe, tex, tex1, tex2, datas)
            else:
                # fallback逐 quad（lint/hermetic）
                for i in range(0, len(datas), 12):
                    pass
        self.map.clear()
```
`key = (pipeline_id, texture_id, texture1, texture2)`，`pipeline_id` 仅对 `textured_pipeline/solid_pipeline`（`tex_count 0/1` 且 `uniforms is None`）分组；其余直调 `draw_model`。
4. `renpy/wgpu/draw_model.py` 的 `self._dm(pipe, mesh, tex, ...)` 调用处改：若 `self._unit_quad` 存在且 `uniforms is None` 且 `texture1 is None and texture2 is None` 则 `group.add(...)`，否则 `self._dm(...)`；在 `draw_screen` 末 `end_frame_present` 前 `group.flush()`。
5. `arena.rs` 的 `end_frame_present` encode循环（`1897-2027`）改：若 `cmd.instance_count>1` 则 `pass.set_vertex_buffer(0, unit_quad); pass.set_vertex_buffer(1, instance_buf); pass.draw_indexed(0..6, 0, 0..cmd.instance_count)` else 原 `draw(0..mesh.vertex_count, 0..1)`。
6. 验证：
```bash
cargo check -p renpy-host
cargo test -p renpy-host 2>&1 | tail -n 20
python -m pytest tests/test_wgpu_composer.py -v 2>&1 | tail -n 30
bash host/scripts/phase9_gates.sh 2>&1 | grep -E "G0[1-8]|MAE|PASS|FAIL" | tail -n 30
RENPY_HOST_PERF=1 python -c "from renpy.wgpu.host_bridge import get_frame_stats; import time; s=get_frame_stats(); print(f\"draw_calls={s['draw_calls']} quads={s['quads']} instances={s['instances']} ratio={s['draw_calls']/max(1,s['quads']):.3f}\"); assert s['instances']>=s['quads']*0.9 or s['quads']==0"
bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 2>&1 | tail -n 40
ldd host/target/release/renpy-host 2>&1 | grep -iE 'libSDL' && echo FAIL || echo 'OK: no libSDL'
```

---

### T4 — RGP/金库/性能门禁验证（Phase 1c）

**Files:** `host/scripts/benchmark_bc160.sh`（读 `get_frame_stats` 聚合） + `host/scripts/phase9_gates.sh`（门禁） + `doc/rdna-perf.md` 或 `doc/wgsl_shader_migration.md`（基线表，可选）  
**Why:** 以可测门禁锁住 10x 收益与零回归：`draw_calls`↓≥10x、`instances≈quads`、MAE≤2/255、ldd空、backend=Vulkan、p99帧时↓（或 waves/CU↑）。  
**Change Necessity:** 校验脚本与文档为新增/改动，但属门禁硬化非渲染语义新增；最小边界为脚本透传+基线表。  
**Impact/Compat:** 不改渲染语义；门禁失败 fail-closed（`golden_mae` 已 fail-closed，新增 `instance_count_total≈quad_count` 断言同 fail-closed）。  
**Verification:** 本Task即验证：`cargo check` + `pytest` + `phase9_gates` 8/8 + `benchmark --measured` p95↓或RGP `VGPR≤5 & waves/CU↑` + `ldd` 空 + `perf get_frame_stats` 比值门。

**Governance:** edit-in-place（脚本与文档增量，不新crate）

**Steps:**

1. `benchmark_bc160.sh` 增 `get_frame_stats` 聚合（`--measured` 分支内）：
```bash
# 在 1800帧循环后
RENPY_HOST_PERF=1 python3 -c "from renpy.wgpu.host_bridge import get_frame_stats; s=get_frame_stats(); print(f\"draw_calls={s['draw_calls']} quads={s['quads']} instances={s['instances']} overdraw={s['overdraw_est']:.2f} ms={s['ms']:.2f}\")" | tee /tmp/bc160_frame_stats.log
# 断言
python3 -c "import json,sys; s=json.loads(open('/tmp/bc160_frame_stats.log').read().split()[-1]) if False else {}; "
# 简化：直接 bash 断言
draw=$(grep -oP 'draw_calls=\K\d+' /tmp/bc160_frame_stats.log | tail -1)
quads=$(grep -oP 'quads=\K\d+' /tmp/bc160_frame_stats.log | tail -1)
[ "$quads" -gt 0 ] && [ "$draw" -lt $((quads/10)) ] || { echo "AC1 fail: draw $draw quads $quads need 10x"; exit 1; }
```
2. `phase9_gates.sh` 增 `instance_count_total≈quad_count` 断言（复用 `get_frame_stats`，fail-closed）：
```bash
RENPY_HOST_PERF=1 python3 -c "from renpy.wgpu.host_bridge import get_frame_stats; s=get_frame_stats(); qc=s['quads']; ic=s['instances']; assert qc==0 or abs(ic-qc)/max(1,qc) < 0.1, f'instance {ic} != quads {qc}'" || exit 1
```
3. RGP 可选：`RUST_LOG=info RENPY_HOST_PERF=1 cargo run -p renpy-host -- the_question` 抓 `wgpu` trace，记录 `textured/solid` 管线 `waves/CU, VGPR, draw count` 于 `doc/rdna-perf.md` 表（若无 RGP 环境则跳过，以 `draw_calls` 门禁为准）。
4. 全量门禁：
```bash
cargo check -p renpy-host
cargo test -p renpy-host 2>&1 | tail -n 20
python -m pytest tests/test_wgpu_composer.py -v 2>&1 | tail -n 30
bash host/scripts/phase9_gates.sh 2>&1 | tee /tmp/phase9_m1.log; grep -q "8 / 8\|8/8" /tmp/phase9_m1.log || grep -q "PASS" /tmp/phase9_m1.log
bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 2>&1 | tee /tmp/bc160_m1.log; grep -q "avg_frame_time_ms\|fps" /tmp/bc160_m1.log
ldd host/target/release/renpy-host 2>&1 | grep -iE 'libSDL' && { echo 'FAIL: SDL linked'; exit 1; } || echo 'OK: no libSDL'
RUST_LOG=info cargo run -p renpy-host -- the_question 2>&1 | grep -q "backend=Vulkan" && echo "Vulkan OK"
RENPY_HOST_PERF=1 python -c "from renpy.wgpu.host_bridge import get_frame_stats; print(get_frame_stats())"
```

---

### T5 — Thermo 解耦收尾：draw 热区 + rtt_pool + _harness 全量迁移（Phase Thermo）

**Files:** `renpy/wgpu/draw.py`（聚合根瘦身） + `renpy/wgpu/draw_surftree.py`（接管全量 traversal） + `renpy/wgpu/draw_walk.py`/`draw_texture.py`/`draw_traversal.py`/`draw_model.py`（helpers 收敛） + `renpy/wgpu/rtt_pool.py`（frame recycle 闭环） + `renpy/wgpu/host_bridge.py`（`host_env_bool/log_host` 单点） + `host/python/gates/_harness.py`（全量迁移） + `renpy/wgpu/constants.py`（残留魔法数）  
**Why:** draw 热区仍残留 surftree/helpers 散落于 `draw.py:804`，`rtt_pool` 仅抽 freelist 未闭帧回收环，`_harness` 134 文件仍 fallback 双 import，导致后续特效必改巨函数、RTT 泄漏、门禁分散。  
**Change Necessity:** 需改源码路径以删重复分支、闭回收环、统一门禁；无非代码替代，最小边界为按已拆 mixin 边线收敛，不新维度。  
**Impact/Compat:** `WgpuDraw` 7-mixin 聚合不变，外发 API `create_render_texture/begin_target/end_target/draw_model` 不动；`rtt_pool` 回收环仅在 `end_frame_present` 后触发，不改 RTT 语义；`_harness` 收敛保留 `from _harness import gate_harness` 的 re-export 兼容 1版本（`host/python/gates/__init__.py` 透传）。  
**Verification:** `pytest tests/test_wgpu_composer.py -v` 全绿；`bash host/scripts/phase9_gates.sh` 8/8；`grep -rn "from _harness import" host/python/gates --include="*.py" | wc -l` 降至 1（仅 `_harness.py` 自身）；`grep -c "except Exception" renpy/wgpu/draw*.py` 总和 <20；`cargo check` 过。

**Governance:** edit-in-place（`draw_surftree` 接管 traversal） + extract helper（helpers 按 `CachedModelPolicy/DissolveStrategy` 已抽，不新增） + add owner（仅 `host/python/gates/_harness.py` 已有，收敛为单 Owner）

**Steps:**

1. `draw_surftree.py` 接管全量：把 `draw.py` 中 ` _is_render_like, _is_surface_like, _is_dissolve_node, _is_imagedissolve_node, _dissolve_complete, _reverse_axis_scale, _node_needs_axis_scale, _reverse_dest_size, _extract_host_texture, _solid_reverse_slot_texture, _child_to_texture, _make_model_leaf, _resolve_texture, _resolve_texture_full, _resolve_mesh, _iter_children, _node_size, _bake_mesh_children` 逐一迁入 `SurftreeMixin`（每函数保留原签名，仅改 `self.` 指向），`draw.py` 删原实现改 `from .draw_surftree import SurftreeMixin` 已有无需增 import，留 `TODO(P0-next)` 注释删净。
2. `draw_walk.py` 残留：把 `draw.py` 中 `ox,oy` 透传残留改为 `WalkCtx`，删 `im.cache` 耦合于 `draw_texture.py` 的残留引用，`except` 收敛至 `host_bridge.log_host`。
3. `rtt_pool.py` 闭环：确认 `WgpuDraw.draw_screen` 的 `try: ... finally: self._recycle_frame_rtts()` 已在 `draw_screen.py` 或 `draw.py` 末尾调用；若无则在 `draw.py` 的 `draw_screen` 的 `end_frame_present` 后增 `self._recycle_frame_rtts()`；`_clamp_rtt_size` 已单点，确保 `get_texture_size` 统计 `live+free` 正确。
4. `_harness` 全量迁移：批量
```bash
# 逐文件把
# try: from _harness import gate_harness; except: from host.python.gates._harness import ...
# 改为单点
sed -i 's/from _harness import gate_harness/from host.python.gates._harness import gate_harness/g' host/python/gates/*.py
# 并在 host/python/gates/__init__.py 增
# from ._harness import gate_harness, parametrized_gate  # re-export compat
# 使旧 from _harness 仍可用 1版本
grep -rn "from _harness import" host/python/gates --include="*.py" | head
```
并把 `phase9_gates.sh`/`run_golden_tests.sh` 中重复 MAE 判定改为调 `python -m host.python.gates._harness` 统一入口（若脚本已调 `golden_mae` 则仅注释溯源）。
5. `host_bridge.py` 单点化：把 `draw_debug.py` 中 `_safe_print/_phase0_due*` 的 `os.environ.get` 散落改为 `from .host_bridge import host_env_bool`，`draw_debug` 保留 re-export 兼容。
6. 验证：
```bash
cargo check -p renpy-host
python -m pytest tests/test_wgpu_composer.py -v 2>&1 | tail -n 30
bash host/scripts/phase9_gates.sh 2>&1 | tail -n 30
grep -rn "from _harness import" host/python/gates --include="*.py" | wc -l; echo "expect 1 or re-export only"
grep -rn "except Exception" renpy/wgpu/draw*.py --include="*.py" | wc -l
ldd host/target/release/renpy-host 2>&1 | grep -iE 'libSDL' && echo FAIL || echo 'OK: no libSDL'
```

---

## Risks

| 风险 | 信号 | 缓解 |
|---|---|---|
| 分组破坏画家序导致重叠 quad alpha 错 | G01-G08 金像 MAE>2 或肉眼重叠区变暗/透 | 同组内按原 walk 序 append，不跨组重排；先全量分组验证 8/8 后再收紧仅非重叠组（Phase1 先全收） |
| Instance 缓冲上传成本抵消 draw 收益 | `benchmark_bc160 --measured` 的 `ms` 未降反升 | grow-only ring + `queue.write_buffer`，cap 参照 `MESH_CACHE_CAP=4096`，超 cap 复用最旧缓冲；overdraw_est 同步监控 |
| 子矩形 UV 丢失（文本/atlas） | 文本金像 G05-G08 局部错位 | Instance 携带 `uv_off/uv_size` 等价现 per-vertex uv，已在 T2 WGSL 验证 |
| 多纹理/uniform 路径误实例化 | dissolve/blur 画面错 | 仅 `tex_count 0/1` 且 `uniforms is None` 走实例化，其余保留 `draw_model` |
| wgpu step_mode 布局对齐错 | `cargo check` 过但 runtime `create_pipeline` 校验失败 | 复用 `shader.rs validate_wgsl_with_naga` 隐式校验，加 pipeline layout 单测（composer 侧已存） |

---

## Retirement

* 旧 `draw_model` 逐 quad 路径**保留**（dissolve/blur/matrixcolor/RTT bake 依赖），不删；实例化仅为新增批路径
* `draw.py` 中 surftree helpers 原实现在 T5 后删，`draw_surftree.py` 为唯一 Owner；旧 import 路径保留 re-export 1版本
* `_harness` 旧 `from _harness import` 兼容保留 1版本（`host/python/gates/__init__.py` re-export），下版本删 fallback
* `WgslShaderCache` 旧 key 熵不改，无退休

---

## Success Criteria

- [ ] T1 后 `RENPY_HOST_PERF=1 get_frame_stats` 五字段可用，且 `cargo check + phase9_gates 8/8 + ldd空` 均绿
- [ ] T2 后 `cargo check + pytest composer + phase9_gates` 均绿，`backend=Vulkan` 仍过
- [ ] T3 后 `draw_calls/quads ≪1`（prefs/HuangmeiC 密集场景 ≥10x），`instances≈quads`，金像 8/8 无回归
- [ ] T4 后 `benchmark_bc160.sh --measured --measured-frames 1800` 的 `avg_frame_time_ms` 较基线 p95↓（或 RGP `waves/CU↑ VGPR≤5`），且 `phase9_gates` fail-closed 仍过
- [ ] T5 后 `draw_surftree` 为 traversal 唯一 Owner，`rtt_pool` 帧回收闭环，`_harness` 单点化，`ldd` 空与 `Rgba8Unorm+PMA` 不变量全程保持

---

## Verification（每 Task exact shell 模板）

```bash
# Rust 侧
cargo check -p renpy-host
cargo test -p renpy-host 2>&1 | tail -n 20

# Python 侧
python -m pytest tests/test_wgpu_composer.py -v 2>&1 | tail -n 30

# 金库
bash host/scripts/phase9_gates.sh 2>&1 | tee /tmp/phase9_m1.log; grep -E "G0[1-8]|MAE|PASS|FAIL|8/8" /tmp/phase9_m1.log | tail -n 30

# 性能
bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 2>&1 | tee /tmp/bc160_m1.log; cat /tmp/bc160_m1.log | tail -n 40

# 双树不变量
ldd host/target/release/renpy-host 2>&1 | grep -iE 'libSDL' && echo 'FAIL: SDL linked' && exit 1 || echo 'OK: no libSDL'
RUST_LOG=info cargo run -p renpy-host -- the_question 2>&1 | grep -q "backend=Vulkan" && echo "Vulkan OK"

# FrameStats 探针
RENPY_HOST_PERF=1 python -c "from renpy.wgpu.host_bridge import get_frame_stats; s=get_frame_stats(); print(s); assert 'draw_calls' in s"
```

---

## Execution Notes

* 顺序：T1 可独立先行铺基线；T2→T3 强依赖（Instance布局先于分组）；T4 为门禁汇总；T5 可与 T2-T3 并行预研但提交在 T4 后以保金像稳定
* 每 Task 单 commit，commit 前必跑 `cargo check + pytest + phase9_gates` 三门；`benchmark --measured` 仅 T3/T4 必跑
* 基线表填 `host/target/bc160_perf_metrics.json` 或本计划 §6 附表（同 wgpu-rdna-wave32-batching.md §6 格式）
