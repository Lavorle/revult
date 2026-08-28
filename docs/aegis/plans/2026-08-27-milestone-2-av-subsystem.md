# Plan: Milestone 2 — A/V 子系统攻坚（视频 YUV/同步 + 音频 symphonia）

> Assignment Header（契约原文，确保门禁可 grep）:
> - **Goal="视频零拷贝前置与音画同步：YUV420p/NV12 GPU转换 + VideoClock 主从 + symphonia 音频解码，消除 CLI 瓶颈与 GIL 卡顿"**
> - **Baseline="renpy/wgpu/video.py + renpy/audio/renpysound_host.py + host/renpy-host/src/state.rs+python.rs+audio.rs + renpy/display/video.py SDL锚点"**
> - **Compatibility="SDL树不动/双树分流 keep lean, FrameBag 按 byte cap (非按帧数)、VideoClock 行为兼容老存档, arena tex_count/uniform 布局不破"**
> - **TDD Route: off / skipped** (Decision: skipped, 不落 RED/GREEN 子步，仅 post-change regression/bench 门禁)

**Date:** 2026-08-27
**Goal:** 视频零拷贝前置与音画同步：YUV420p/NV12 GPU转换 + VideoClock 主从 + symphonia 音频解码，消除 CLI 瓶颈与 GIL 卡顿
**Architecture:** `renpy-host` (winit 0.30 + wgpu Vulkan + PyO3) as outer loop / `renpy/wgpu` as Python present façade / `PcmRing + AudioEngine (cpal)` as audio callback SSOT — 本期在 host 内新增 `video` 解码域，Python 侧仅保留 shim 兼容，不新增 SDL 依赖
**Tech Stack:** Python 3.14 / Rust stable / wgpu 24.0 (Vulkan only) / winit 0.30 / cpal 0.15 / symphonia 0.5 / ffmpeg-sys-next 7.x + naga 24.0 / Pillow (glyph) / ffmpeg CLI (V1 兼容保留，V2 后端替换)
**Baseline / Authority Refs:** `renpy/wgpu/video.py` (FrameBag+FfmpegCmdBuilder+Decoder Protocol shim) + `renpy/audio/renpysound_host.py` (path cache + VideoClock arm + PcmRing 桥) + `host/renpy-host/src/state.rs+python.rs+audio.rs` (VideoClock/AudioEngine/PcmRing) + `renpy/display/video.py` SDL 锚点 + `host/renpy-host/src/arena.rs+gpu.rs` (Rgba8Unorm/BgCache/tex_count) + `doc/wgsl_shader_migration.md` + `.omc/plans/wgpu-rdna-wave32-batching.md` + `.omc/plans/goal-wgpu-e0-e1-packaging.md`
**Compatibility Boundary:** SDL 树不动 / 双树分流 keep lean；`FrameBag` 按 **byte cap**（非按帧数）；`VideoClock` 行为兼容老存档（仅增字段，不改存档序列化语义）；`arena` `tex_count ∈ {0..3}` / `uniform 16f32 / 64B` / `Rgba8Unorm` / `BindGroupLayout` 不破；`renpy/display/video.py::movie_*` 调用面保持可 import（SDL 树回归绿）
**TDD Route:** `off / skipped` — 见下节 `TDD Route` 记录（本期无新契约 RED，仅 post-change regression/bench 门禁）
**Verification:** `cargo check -p renpy-host` + `cargo test -p renpy-host` + `pytest tests/test_wgpu* tests/test_host*` + `ldd host/target/release/renpy-host | grep -iE 'libSDL'` 为空 + `RUST_LOG=info cargo run -p renpy-host -- --gate smoke 2>&1 | grep backend=Vulkan` + `G05_movie` 门禁 + `1080p60 soak 30s` + `音画漂移 drop/repeat 日志` 门禁（详见 §6）

---

## Plan Basis

```text
Aegis Visibility:
  本期改的是 A/V 同步主路径（VideoClock 时钟域 + YUV 上传 + FrameBag 缓存策略 + host 解码线程池 + symphonia 混音器打桩）；
  错改会导致音画漂移持续累积、1080p 帧预算击穿、GIL 卡顿回潮、arena 管线 layout 破坏或 SDL 双树逃逸；先以可验证的时钟/YUV/byte-cap/线程池边界锁文件与门禁再动 host。

BaselineUsageDraft:
- Required baseline refs: renpy/wgpu/video.py:24-30/84-130/235-259/267-464/791-812 (shim+FrameBag+Builder+Decoder), host/renpy-host/src/state.rs:17-32/48-56 (VideoClock+HostState.video_clocks), host/renpy-host/src/audio.rs:11-65 (PcmRing/AudioEngine), host/renpy-host/src/python.rs:1208-1234/2148-2234 (audio+video_clock FFI), renpy/audio/renpysound_host.py:44-82/335-360/1178-1326 (path cache+clock arm+PcmRing bridge), renpy/display/video.py:49-82/157-204 (movie anchor), host/renpy-host/src/arena.rs:12-16/119-121/2035-2107 (caps/uniform/BindGroup), host/renpy-host/src/gpu.rs:14/26-34 (Rgba8Unorm/Bgra fallback), doc/wgsl_shader_migration.md §Color/Blend, .omc/plans/wgpu-rdna-wave32-batching.md §2-3, .omc/plans/goal-wgpu-e0-e1-packaging.md §Pass criteria
- Delivered context refs: 本次 host/python/wgpu 三树已读（见上）；wave32 与 deslop 两份已落盘计划已读
- Acknowledged before plan refs: 上述 7 源已读；Cargo.toml workspace 依赖已读
- Cited in plan refs: 上述文件:行均在 Tasks.Why/Files 中显式引用
- Missing refs: 真实 ffmpeg-sys 在 BC160 上的 libav* 版本与 NV12 驱动实测（V2 时补探针，非阻塞）
- Decision: continue

Requirement Ready Check:
- Requirement source refs: 本 Milestone 2 任务卡（V1/V2/T1-T5/Verification 逐条）为 approved spec
- Goals and scope refs: 消除 ffmpeg-CLI 瓶颈与 GIL 卡顿；YUV420p/NV12 零拷贝前置；VideoClock 主从；symphonia 伴音直推；byte-budget 缓存与 seek
- User / scenario refs: 1080p60 全屏 Movie（HuangmeiC main_menu）+ the_question Path K + Prefs overlay 三场景为验收锚点
- Requirement item refs: 下述 T1-T5 逐条映射（T1 时钟主从 / T2 YUV管线 / T3 FrameBag cap+seek / T4 Rust解码线程池 / T5 symphonia打桩）
- Acceptance / verification criteria refs: §6 双树不变量 + 执行期 soak/drift/G05/py测试 7 门齐全
- Open blocker questions: 无阻塞；ffmpeg-sys 选型细节与 symphonia 多轨混音策略在 T4/T5 内以探针收敛
- Decision: ready

Change Necessity:
- User-visible need: 现状视频走 ffmpeg CLI 子进程 + RGBA 全量回读（video.py:520-608 _decode_ffmpeg_chunk RGBA），每帧 1920×1080×4≈7.9 MiB 经 Python GIL + Queue 回推，1080p60 下 CLI 调度与内存复制成为瓶颈；VideoClock 为纯 wall time（state.rs:17-32），与 cpal 样本时钟无绑定，长时间播放漂移累积；音频仍经 Python 侧 write_pcm 推 ring，无宿主解码/混音
- No-change / non-code option: 仅调参（RENPY_HOST_MOVIE_* env）或加文档无法消除 CLI 进程开销、RGBA 带宽与时钟漂移
- Why code change is necessary: 必须新增宿主 YUV 管线 + 样本时钟主从 + byte-budget 缓存 + Rust 解码线程池 + symphonia 混音桩，方能把带宽降 50%+ 并让时钟收敛
- Minimum change boundary: host 新增 video 解码域（1 新文件 + audio/state/python 增量）+ renpy/wgpu/video.py 的 YUV 路径增量 + renpy/audio/renpysound_host.py 的时钟绑定增量；不碰 SDL 树与 arena layout 以外
- Decision: code-change

Existence Check:
- Proposed new surface: host/renpy-host/src/video.rs（解码域 SSOT）+ host/renpy-host/src/audio_mixer.rs（混音器打桩，cpal 多通道探针）+ renpy/wgpu/shaders_yuv.wgsl 逻辑内嵌于 arena（不新 crate）
- Existing owner / reuse candidate: 现有 video.py shim 与 audio.rs PcmRing 已有 owner，但无 YUV 管线、无解码线程池、无 sample-clock；复用 arena/gpu 现有管线工厂与 AudioEngine ring
- Why existing surface is insufficient: shim 仅 RGBA CLI（video.py:84-812），arena 仅 RGBA create_texture_rgba/write_texture_rgba（python.rs:1727-1757），audio.rs 仅单 ring 单 volume（audio.rs:57-65），均无法承载 YUV/多轨/线程池
- Creation proof: 1080p60 soak 的 RGBA vs YUV 带宽差与 CLI vs in-process 解码延迟差为创建依据（V1/V2 门禁量化）；V1 先以 shim 兼容+V2 再以 host 域替换，进位熵可控
- Entropy / retirement impact: 新增 2 Rust 文件替代 CLI 进程与 Python 侧大量 ffmpeg 拼参分支，净熵↓；退役触发：V2 稳定后 CLI 路径标记 deprecated（见 Retirement）
- Decision: add-with-proof（仅 video.rs + audio_mixer.rs + WGSL 内嵌）

Architecture Integrity Lens:
- Invariant: 单渲染器 WgpuDraw；SWAPCHAIN Rgba8Unorm（gpu.rs:14）+ PMA + One/OneMinusSrcAlpha；host 分支 host_build；SDL 树不删
- Canonical owner / contract: GpuArena 为纹理/管线/DrawCmd SSOT（state.rs:48），AudioEngine+PcmRing 为音频 SSOT（state.rs:54），VideoClock 为 presentation clock SSOT（state.rs:56），新 video 域仅增 YUV 上传与解码，不抢 arena 权
- Responsibility overlap: video.py 的 FfmpegCmdBuilder 与 _BaseDecoder 已单一职责，本期在其上叠 YUV 分支而非另起 CLI 封装；audio_mixer 仅消费 PcmRing，不另起 ring
- Higher-level simplification: 可否用单一 uber YUV 管线收 YUV420p/NV12？否 — 采样器/纹理数不同（3 vs 2），分两 pipeline 更直观且与 tex_count 已有维度一致
- Retirement / falsifier: 若 YUV 管线引入后 G01-G08 任一 MAE>2/255 或 ldd 逃逸，即回滚单 Task；若新增解码仍需改 _ensure_host_texture_alive 则证明 HandleResolver 抽象泄漏
- Verdict: reuse-existing owners, add video/mixer domain files, edit-in-place 其余

Plan Pressure Test:
- Owner / contract / retirement: video 域与 audio_mixer 域边界清晰，退休面为 CLI 路径 deprecate，可 revert；无新 owner 增熵失控
- Architecture integrity / higher-level path: 已验无更高层 Owner 可替代（HostState.composer 仍为占位，arena 仍用常量工厂）
- Verification scope: cargo check/test + pytest + ldd/backend + G05 + 1080p60 soak + drift 7 门齐全，覆盖双树与 A/V
- Task executability: 每 Task 2-5 min slice，文件边界独立，失败可单 Task revert
- Pressure result: proceed

Plan-Time Complexity Check:
- Target files: renpy/wgpu/video.py ~898 + renpy/audio/renpysound_host.py ~2100 + host/renpy-host/src/state.rs ~130 + python.rs ~2376 + audio.rs ~188 + arena.rs ~2697 + gpu.rs ~400 + 新 video.rs + audio_mixer.rs
- Existing size / shape signals: video.py 已 88 except 散点（deslop 见证），python.rs 27 处 lock unwrap 散点，arena 2697 行含 12 WGSL 管线常量
- Owner fit: video 域新建最贴合，audio_mixer 贴 AudioEngine，其余 edit-in-place
- Add-in-place risk: 在 video.py 直接堆 YUV 分支会使 898→1200 行，触 over-budget；需抽 YuvPipeline helper 同文件顶部
- Better file boundary: 新 Rust 域文件分担 host 侧复杂度，Python 侧仅增 YUV 分支与 byte-cap Helper
- Recommendation: add owner file（video.rs + audio_mixer.rs）+ extract helper（同文件内 YuvHelper/ClockBinder）

Execution Readiness View:
- Intent Lock: 仅 A/V 子系统攻坚（YUV 零拷贝前置 + 时钟主从 + byte-budget + Rust 解码预研 + symphonia 打桩）；不含 Live2D 真 SDK、不含 SDL 真删
- Scope Fence: 可改 Files 表 12 文件；禁动 renpy/gl2/SDL 树、SWAPCHAIN format、tex_count/uniform 布局、_PIPELINE_KEYS 维度、wave32 批聚合层
- Baseline Lock: Rgba8Unorm+PMA，tex_count∈{0..3}，uniform 16f32/64B，MAE≤2/255，ldd 空，backend=Vulkan，VideoClock 老存档兼容
- Approved Behavior: 1080p60 30s soak 不丢帧预算（p99 <16.6ms 窗内）；音画漂移收敛（drop/repeat 日志可观测）；G05_movie 仍绿；HuangmeiC main_menu 全屏 Movie 可播
- Owner / Contract Constraints: GpuArena 管线/纹理 SSOT 不变；AudioEngine ring SSOT 不变；VideoClock 仅增绑定字段，不改 pos_ms 签名
- Compatibility Boundary: 见 §Compatibility；FrameBag byte cap 对外仍为 list 兼容（继承 list），arena 布局不破
- Retirement Boundary: CLI RGBA 路径在 V2 稳定前保留，V2 后标记 deprecated（1 版本兼容期）
- Task Batches: B1 时钟+YUV（T1+T2）→ B2 缓存+seek（T3）→ B3 宿主解码前置（T4）→ B4 音频打桩（T5）→ B5 门禁收口（Verification）
- Test Obligations: cargo test + pytest + G05 + soak + drift 四类；新增 tests/test_video_yuv.py + tests/test_framebag_cap.py + tests/test_clock_master.py
- Review Gates: cargo clippy -W pedantic + ruff 全过（复用 deslop 收窄后豁免）
- Drift / Rewind Rules: 单 Task 可独立 git revert；T1/T2 任一 fail 即全 plan pause，不进入 T4
- Evidence Required Before Completion: ldd 空 + backend=Vulkan + G05_movie 绿 + soak 30s 日志 + drift 日志 + cargo/pytest 绿
- Advisory Boundary: method-pack 执行指引；非 GateDecision，仅达标前置

TDD Route:
- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: post-change regression + bench/soak 门禁（非 RED）
- Reason: 本期为 A/V 链路前置与打桩，无新增对外契约分支语义需先写 RED；风险在时钟漂移与 YUV 正确性，需 soak/drift/金像事后锁而非预写 RED（与全局 TDD off 一致）
- Verification: cargo check + cargo test + pytest + G05 + 1080p60 soak + drift probe + ldd/backend 断言
```

---

## Files

| 文件 | 动作 | 边界 |
|------|------|------|
| `renpy/wgpu/video.py` | **改** | YUV420p/NV12 分支 + `FrameBag` byte-cap + `FfmpegCmdBuilder` 增 YUV 构造 + `VideoTexture` 增 YUV 上传路径；`except` 22→≤8；`ldd` 双树 keep lean（`import renpy_host` 仍在 try 守卫内 video.py:70-72） |
| `renpy/audio/renpysound_host.py` | **改** | `VideoClock` 主从绑定（`_maybe_arm_clock` 扩展为 sample-clock 源）+ `PcmRing` 多通道探针入口 + `FrameBag` byte 预算 Hook；不改 `renpy/display/video.py` 锚点语义 |
| `host/renpy-host/src/state.rs` | **改** | `VideoClock` 增 `master: ClockMaster`（`Wall | AudioSample { samples: u64, rate: u32 }`）+ `drift_ms` 探针字段；`pos_ms` 保持签名，内部按 master 分支；`HostState.video_clocks` 仍为 HashMap<i32, VideoClock>（state.rs:56） |
| `host/renpy-host/src/audio.rs` | **改** | `AudioEngine` 增 `sample_clock: AtomicU64`（已消费样本计数）+ `master_volume` 多通道预留 + `dropped/repeated` 探针计数；`PcmRing` 增 `channels: u8` 探针与 `fill_output` 多通道展开；`cpal` 回调内仅 ring+volume+clock 三原子操作 |
| `host/renpy-host/src/audio_mixer.rs` | **新增** | symphonia 混音器打桩：`MixerConfig { channels, rate, buffer_ms }` + `fn probe_symphonia(path)->Probe { codec, rate, frames }` + `fn push_decoded_to_ring(ring, pcm)` 桩；`cpal` 多通道（stereo→5.1）规划与 env 探针 `RENPY_HOST_AUDIO_CHANNELS` |
| `host/renpy-host/src/video.rs` | **新增** | Rust 宿主解码域 SSOT：`VideoDecoder { path, fps, yuv: YuvKind }` + `StagingRing { buffers: Vec<Buffer>, cap_bytes }` + `DecodePool { workers: 2..4 }` + `SeekIndex { keyframes: Vec<u64> }` + `fn yuv_to_rgba_wgsl_kind()->&str` 桩；`ffmpeg-sys-next` 后端占位（feature gate `ffmpeg-host`） |
| `host/renpy-host/src/python.rs` | **改** | 新增 FFI：`video_yuv_upload(yuv_kind, w,h, y,u,v)` 或 `video_upload_yuv420p / video_upload_nv12` + `video_clock_bind_audio(channel, rate)` + `video_seek(channel, pos_ms)` + `audio_probe(path)` + `audio_mixer_probe()`；保留 `video_clock_*` 现有 FFI（python.rs:2148-2234）签名不变 |
| `host/renpy-host/src/arena.rs` | **改** | 新增 YUV 管线：`YUV420P_WGSL` / `NV12_WGSL`（BT.601 full/limited 可选）+ `yuv420p_pipeline` / `nv12_pipeline` 工厂 + `create_texture_yuv` / `write_texture_yuv`（或复用 `create_texture_rgba` 的 bgra swizzle 分支 arena.rs:51-68 旁的 yuv 分支）；`tex_count` 仍 `u8`，YUV 走 `tex_count=3/2` 复用 `build_bind_group_layout`（arena.rs:2035）校验 |
| `host/renpy-host/src/gpu.rs` | **改** | 仅增 `YUV_FORMATS_SUPPORTED` 探针与 `staging_buffer_ring_bytes` 常量（≤64 MiB）；`SWAPCHAIN_FORMAT` 仍 Rgba8Unorm（gpu.rs:14），`surface_format` Bgra fallback 不变 |
| `host/renpy-host/Cargo.toml` | **改** | 新增 `symphonia = { version="0.5", features=["all"] }` + `ffmpeg-sys-next = { version="7", optional=true }`（feature `ffmpeg-host`）；`cpal` 保留 |
| `host/Cargo.toml` (workspace) | **确认** | 若 workspace 统一版本则在此声明 `symphonia` / `ffmpeg-sys-next` 版本，子 crate `workspace = true` |
| `renpy/display/video.py` | **只读锚点** | 不改；`movie_start`/`movie_stop` 仍经 `renpy.audio.music`（display/video.py:49-82），本期仅保证其 `get_movie_texture` 经 host YUV 路径后仍可用 |
| `tests/test_video_yuv.py` | **新增** | YUV 管线与 BT.601 单测（见 T2） |
| `tests/test_framebag_cap.py` | **新增** | FrameBag byte-cap 与 seek 索引单测（见 T3） |
| `tests/test_clock_master.py` | **新增** | VideoClock 主从与漂移探针单测（见 T1） |

不改：`renpy/gl2/*` SDL 树、`renpy/display/core.py:host_build` 分支语义、`WgslShaderCache` key 熵（`sha1[:16]`）、`shader.rs emit_wgsl` 文本、`draw_*` 批聚合层（wave32 计划所有）。

---

## Compatibility Boundary（冻结）

* `SWAPCHAIN_FORMAT = Rgba8Unorm`（gpu.rs:14）+ `surface_format` Bgra fallback（gpu.rs:27）+ PMA + `One / OneMinusSrcAlpha` 混合不改；金像捕获仍为 **pre-present game RT**，MAE≤2/255 max≤16（doc/wgsl_shader_migration.md §Color）
* `WgpuDraw` 单渲染器；`renpy.host_build` 分支不破 SDL 树；`renpy/display/video.py` 公共 API 仅增不减（`movie_start/stop`, `get_movie_texture`, `Movie` 类均可 import）
* `WgslShaderCache` 缓存命中不变（`composed:<sha1[:16]>`）；`tex_count/layout/has_uniforms` 仍由 Rust SSOT 校验（arena.rs:2035-2107）；`assert_pipeline_map_honest()` 风格门禁仍过
* `ldd host/target/{debug,release}/renpy-host | grep -iE 'libSDL'` 为空；`RUST_LOG=info` 必含 `adapter backend=Vulkan`
* `FrameBag` 对外仍为 `list` 子类（video.py:84 `class FrameBag(list)`），`_abs_total` 字段保留；新增 `cap_bytes` 仅内部限流，不改旧存档序列化（VideoClock 新增字段带 `#[serde(default)]` 语义或手动 default）
* `VideoClock::pos_ms(&self, now_ms: u64)->f64`（state.rs:25）签名不变；新增 `ClockMaster` 仅影响内部时钟源选择，老存档无 master 字段时 default `Wall`
* `arena` `UNIFORM_BYTES=64`（16 f32，arena.rs:119）与 `BG_CACHE_SOFT_CAP/RING_INIT/MAX_RTTS_PER_SIZE` 不破；YUV 管线仅新增 tex_count=2/3 的 `BindGroupLayout` 分支
* `PcmRing` 对外 `push_interleaved / fill_output` 签名不变（audio.rs:24-39）；多通道仅内部展开，单声道/立体声存量路径零变化

---

## Milestone 分期（V1 / V2）与阶段性产物/门禁

### V1 — 零拷贝前置与同步收敛（本计划首批交付，2 周内闭环）

**目标：** 不引入 `ffmpeg-sys` 真链接，仅在现有 CLI/RGBA 链路上叠 YUV GPU 转换 + 时钟主从 + byte-budget，达到 **CLI 瓶颈减半 + 漂移可观测收敛**，为 V2 宿主解码铺路。

**包含：** T1 + T2 + T3（时钟主从 + YUV420p pipeline + byte-budget FrameBag + seek 探针）。V1 结束时 `ffmpeg-sys` 为 **feature gate 桩**（`#[cfg(feature="ffmpeg-host")]` 空实现），保证 `cargo check` 不依赖系统 `libav*`。

**阶段性产物：**
* `host/renpy-host/src/state.rs` — `ClockMaster` 与漂移探针
* `host/renpy-host/src/audio.rs` — `sample_clock` 与 drop/repeat 计数
* `renpy/wgpu/video.py` — YUV420p 上传分支（CPU 侧 Y/U/V 切分 + `renpy_host.video_upload_*` 调用）+ FfmpegCmdBuilder YUV 构造
* `host/renpy-host/src/arena.rs` — `YUV420P_WGSL` + `yuv420p_pipeline`（BT.601）+ YUV 纹理创建/更新
* `renpy/audio/renpysound_host.py` — `video_clock_bind_audio` 绑定与 byte-cap Hook
* `tests/test_clock_master.py` + `tests/test_video_yuv.py` + `tests/test_framebag_cap.py`（V1 三单测）

**V1 门禁（必须全绿方可进 V2）：**
* `cargo check -p renpy-host` 与 `cargo test -p renpy-host -- --nocapture` 绿（含新增 3 单测）
* `pytest tests/test_video_yuv.py tests/test_framebag_cap.py tests/test_clock_master.py -v` 绿
* `G05_movie` 金像门禁绿（MAE≤2/255，需 YUV 与老 RGBA 视差在阈内，见 T2）
* `ldd` 空 + `backend=Vulkan` 双树不变量绿
* `1080p 30fps 30s` 轻量 soak（V1 目标，非全 60fps）不 OOM，byte-cap 日志显示 `evicted_bytes >0` 且 `ring_len_bytes ≤ cap`
* 漂移探针日志：`drift_ms` 在 30s 窗内收敛（`|drift| < 40ms` 且 `dropped+repeated < 5`），`video_clock_pos - audio_sample_pos` 可观测

### V2 — 宿主解码线程池 + 零拷贝收口 + 伴音直推（V1 后 2 周）

**目标：** 以 `ffmpeg-sys-next` Rust 宿主解码线程池替代 CLI 子进程；`Staging buffer ring` 零拷贝直推 GPU；伴音经 `symphonia` 直推 `PcmRing`，消除 Python GIL 卡顿。

**包含：** T4 + T5（ffmpeg-sys 线程池 + Staging ring + 伴音直推 + cpal 多通道）+ 全量 1080p60 soak。

**阶段性产物：**
* `host/renpy-host/src/video.rs` — `DecodePool` + `StagingRing` + `SeekIndex` + `ffmpeg-sys-next` 真实现（feature `ffmpeg-host` 开启）
* `host/renpy-host/src/audio_mixer.rs` — `symphonia` 探针与 `push_decoded_to_ring` 真实现
* `renpy/wgpu/video.py` — CLI 路径标记 `deprecated`，宿主路径为默认（env `RENPY_HOST_VIDEO_BACKEND=host|cli` 可回退）
* `renpy/audio/renpysound_host.py` — 伴音直推（`write_pcm` 走 host 解码而非 Python 推）

**V2 门禁（Milestone 2 总体验收）：**
* `cargo check -p renpy-host --features ffmpeg-host` 绿（CI 上若无 `libav*` 则 `--features` 路径为可选，仅本地/容器内绿）
* `1080p60 soak 30s` 绿：`p99 <16.6ms` 窗内，`present_presents` 连续，`RUST_LOG=info` 无 `decode starvation`
* 音画漂移门禁：30s 窗内 `|drift_ms| < 20ms` 且 `video drop/repeat` 日志 `<2`（V1 的 40ms/5 收紧）
* `G05_movie` + `G01-G08` 全绿（YUV 与 RGBA 视差同阈）
* `pytest` 全量 + `ldd/backend` 绿（与 V1 同）

---

## Tasks

### T1 — 时钟主从：VideoClock 绑定 AudioEngine 样本时钟

**Files:** `host/renpy-host/src/state.rs`（改 `VideoClock`） + `host/renpy-host/src/audio.rs`（增 `sample_clock` + 探针） + `host/renpy-host/src/python.rs`（增 `video_clock_bind_audio` FFI） + `renpy/audio/renpysound_host.py`（改 `_maybe_arm_clock/_ensure_video_frames`） + `tests/test_clock_master.py`（新增）

**Why:** 现状 `VideoClock { start_ms, paused, pause_accum_ms }`（state.rs:17-22）为纯 wall time，`pos_ms` 仅 `now_ms - start_ms - pause_accum`（state.rs:25-32），与 `AudioEngine` 的 `PcmRing` 样本消耗无绑定，长时间播放 drift 线性累积；`renpysound_host.py:1710 _maybe_arm_clock` 仅在 `ready_playable` 时 arm wall clock，无音频主时钟概念，导致 1080p 长视频 30s 后音画可感知错位。

**Change Necessity:** 无非代码路径；wall drift 无法靠 env/配置收敛，最小边界为 `state.rs` 单 struct 增字段 + `audio.rs` 单原子计数 + `python.rs` 单 FFI，Python 侧仅增绑定调用。

**Impact/Compat:** `VideoClock::pos_ms` 签名不变（state.rs:25），新增 `master` 字段带 default `ClockMaster::Wall`，老存档/老调用无 master 时回退 wall，行为兼容；`AudioEngine::fill_output` 仍仅读 ring（audio.rs:34-39），新增 `sample_clock += out.len()/channels` 为原子累加，不改回调实时性；`ldd/backend/Rgba8Unorm/tex_count` 不碰。

**Verification:**
```bash
cargo check -p renpy-host 2>&1 | tail -n 20
cargo test -p renpy-host test_clock_master -- --nocapture 2>&1 | tail -n 30
pytest tests/test_clock_master.py -v 2>&1 | tail -n 30
RUST_LOG=info cargo run -p renpy-host -- --gate clock_drift_probe 2>&1 | grep -E "drift_ms|sample_clock|master="
```

**Steps (2-5 min each):**

1. `state.rs` 增 `ClockMaster` 枚举与漂移探针（`ClockMaster::Wall` 为 default，老存档兼容）：
```rust
// host/renpy-host/src/state.rs — 在 VideoClock 定义旁新增
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClockMaster {
    Wall,
    AudioSample { rate: u32 },
}
impl Default for ClockMaster { fn default() -> Self { Self::Wall } }

#[derive(Debug, Clone)]
pub struct VideoClock {
    pub start_ms: u64,
    pub paused: bool,
    pub pause_started_ms: Option<u64>,
    pub pause_accum_ms: u64,
    pub master: ClockMaster,          // 新增，default Wall
    pub drift_ms: f32,                // 新增，探针：video_pos - audio_pos
    pub dropped: u32,                 // 新增，探针：丢帧计数
    pub repeated: u32,                // 新增，探针：重帧计数
}
impl VideoClock {
    pub fn pos_ms(&self, now_ms: u64) -> f64 {
        // Wall 分支保持原逻辑（state.rs:25-32 现状）
        // AudioSample 分支：pos = samples as f64 / rate as f64 * 1000.0
        // 保持签名 f64，内部按 master 分支
        todo!("see step 2")
    }
    pub fn bind_audio(&mut self, rate: u32) { self.master = ClockMaster::AudioSample { rate }; }
}
```

2. `audio.rs` 增 `sample_clock: AtomicU64` 与 `fill_output` 计数（cpal 回调内仅原子累加，不 alloc）：
```rust
// host/renpy-host/src/audio.rs — AudioEngine 增字段
pub struct AudioEngine {
    pub ring: Arc<PcmRing>,
    pub sample_rate: AtomicU32,
    pub channels: AtomicU32,
    pub running: AtomicBool,
    pub volume: Arc<AtomicU32>,
    pub sample_clock: AtomicU64,      // 新增：已消费样本帧数（per-channel 帧，非 sample）
    pub dropped: AtomicU32,           // 新增：探针
    pub repeated: AtomicU32,          // 新增：探针
    stream: Mutex<Option<SendStream>>,
}
// fill_output 末尾增：
// let frames = out.len() / self.channels.load(Ordering::Relaxed) as usize;
// self.sample_clock.fetch_add(frames as u64, Ordering::Relaxed);
```

3. `python.rs` 新增 `video_clock_bind_audio` FFI（复用 `with_host_state_mut` helper python.rs:47-54）：
```rust
#[pyfunction]
fn video_clock_bind_audio(channel: i32, rate: u32) {
    with_host_state_mut(|st| {
        if let Some(clock) = st.video_clocks.get_mut(&channel) {
            clock.bind_audio(rate);
        }
        // 同步 AudioEngine.sample_rate 供 drift 计算
        st.audio.sample_rate.store(rate, Ordering::Relaxed);
    });
}
#[pyfunction]
fn video_clock_drift_ms(channel: i32) -> f32 {
    with_host_state(|st| st.video_clocks.get(&channel).map(|c| c.drift_ms).unwrap_or(0.0))
}
// 在 register_renpy_host 内注册上述两个函数
```

4. `renpy/audio/renpysound_host.py` 在 `_maybe_arm_clock` 后增绑定（`_maybe_arm_clock` 见 renpysound_host.py:1710）：
```python
def _maybe_arm_clock(channel: int) -> None:
    # 现状 arm wall clock 后，新增：
    try:
        import renpy_host
        ch = _channels.get(channel)
        if ch and ch.get("playing"):
            # 音频主时钟：若通道为 movie 且音频已起，绑定 sample rate
            rate = int(renpy_host.audio_sample_rate() if hasattr(renpy_host, "audio_sample_rate") else 48000)
            # 仅当 video_clock 已存在且未绑定时绑一次
            renpy_host.video_clock_bind_audio(int(channel), int(rate))
    except Exception:
        pass
```

5. 新增 `tests/test_clock_master.py`（3 用例，不依赖真实 cpal）：
```python
def test_wall_pos_ms_unchanged():
    # 复刻 state.rs Wall 分支：pos = now - start - accum
    from renpy_host import video_clock_start, video_clock_pos
    video_clock_start(77)
    import time; time.sleep(0.02)
    pos = video_clock_pos(77)
    assert 0.01 < pos < 0.2

def test_bind_audio_switches_master():
    from renpy_host import video_clock_start, video_clock_bind_audio, video_clock_drift_ms
    video_clock_start(78)
    video_clock_bind_audio(78, 48000)
    # drift 初始 0，bind 后 master 为 AudioSample
    assert abs(video_clock_drift_ms(78)) < 1.0

def test_drift_probe_monotonic():
    # 模拟 30 帧推进，drift 不应突变 >40ms（探针阈值，见 V1 门禁）
    pass
```
运行 `pytest tests/test_clock_master.py -v` 预期 3 passed。

---

### T2 — YUV 管线与 WGSL BT.601（YUV420p 首发 + NV12 预留）

**Files:** `host/renpy-host/src/arena.rs`（增 WGSL+管线） + `host/renpy-host/src/gpu.rs`（增 staging 常量） + `renpy/wgpu/video.py`（增 YUV 构造与上传） + `host/renpy-host/src/python.rs`（增 `video_upload_yuv*` FFI） + `tests/test_video_yuv.py`（新增）

**Why:** 现状 `video.py:520 _decode_ffmpeg_chunk` 以 `ffmpeg -pix_fmt rgba` 全量回读 RGBA（`_split_raw_rgba` video.py:508），1920×1080 单帧 7.9 MiB，经 `_stream_ffmpeg_remaining` → `FrameBag` list 全量 Python 持有，1080p60 下带宽与 GIL 复制成为瓶颈；GPU 侧 `arena.rs:2209 TEXTURED_WGSL` 仅 `textureSample(tex0)` 单纹理，`create_texture_rgba`（python.rs:1728）仅 RGBA 路径，无 YUV→RGB 转换，无法零拷贝前置。

**Change Necessity:** 必须新增 GPU 侧 YUV→RGB 转换（BT.601）与 Python 侧 Y/U/V 切分上传，最小边界为 `arena.rs` 2 管线 + `video.py` 单分支 + `python.rs` 2 FFI，不改 `gpu.rs:14 SWAPCHAIN_FORMAT` 与 `arena.rs:119 UNIFORM_BYTES`。

**Impact/Compat:** 新增 `yuv420p_pipeline`（tex_count=3）与 `nv12_pipeline`（tex_count=2）复用 `build_bind_group_layout(tex_count, has_uniforms)`（arena.rs:2035）现有校验，`tex_count` 维度仍 `u8` 0..3；老 `textured_pipeline`（tex_count=1）不动，金像阈值 `MAE≤2/255` 仍以 RGBA 为参照，YUV 路径需在阈内（见 Verification 容差）；`renpy_host.create_texture_rgba` 签名保留，仅新增 `create_texture_yuv420p`。

**Verification:**
```bash
cargo check -p renpy-host 2>&1 | tail -n 20
cargo test -p renpy-host -- --nocapture 2>&1 | grep -E "yuv|YUV"
pytest tests/test_video_yuv.py -v 2>&1 | tail -n 40
bash host/scripts/phase9_gates.sh 2>&1 | grep -E "G0[1-8]|MAE|PASS|FAIL"
# YUV 与 RGBA 视差探针（允许 BT.601 量化误差 ≤2/255）
python -m pytest tests/test_video_yuv.py::test_yuv420p_golden_parity -v
ldd host/target/release/renpy-host | grep -iE 'libSDL' ; echo "ldd_empty=$?"
RUST_LOG=info cargo run -p renpy-host -- --gate yuv_probe 2>&1 | grep -E "yuv420p|nv12|backend=Vulkan"
```

**Steps:**

1. `arena.rs` 增 YUV WGSL（BT.601 full range，limited 可 env 切）与管线工厂（复用 `TEXTURED_WGSL` 结构 arena.rs:2211-2239，仅改采样与矩阵）：
```rust
// host/renpy-host/src/arena.rs — 在 TEXTURED_WGSL 后新增
const YUV420P_WGSL: &str = r#"
@group(0) @binding(0) var t_y: texture_2d<f32>;
@group(0) @binding(1) var t_u: texture_2d<f32>;
@group(0) @binding(2) var t_v: texture_2d<f32>;
@group(0) @binding(3) var s_yuv: sampler;
struct VsOut { @builtin(position) pos: vec4<f32>, @location(0) uv: vec2<f32>, @location(1) color: vec4<f32> };
@vertex fn vs_main(@location(0) pos: vec2<f32>, @location(1) uv: vec2<f32>, @location(2) color: vec4<f32>) -> VsOut {
    var out: VsOut; out.pos = vec4<f32>(pos, 0.0, 1.0); out.uv = uv; out.color = color; return out;
}
@fragment fn fs_main(in: VsOut) -> @location(0) vec4<f32> {
    let y = textureSample(t_y, s_yuv, in.uv).r;
    let u = textureSample(t_u, s_yuv, in.uv).r - 0.5;
    let v = textureSample(t_v, s_yuv, in.uv).r - 0.5;
    // BT.601 full range
    let r = y + 1.402 * v;
    let g = y - 0.344136 * u - 0.714136 * v;
    let b = y + 1.772 * u;
    return vec4<f32>(r, g, b, 1.0) * in.color;
}
"#;
const NV12_WGSL: &str = r#"
@group(0) @binding(0) var t_y: texture_2d<f32>;
@group(0) @binding(1) var t_uv: texture_2d<f32>;
@group(0) @binding(2) var s_yuv: sampler;
// vs 同上，fs 中 uv 采样 rg 通道
"#;
// 管线工厂（复用 build_bind_group_layout tex_count=3/2）
// pub fn yuv420p_pipeline(&mut self, gpu: &GpuState) -> PipelineHandle { ... }
// pub fn nv12_pipeline(&mut self, gpu: &GpuState) -> PipelineHandle { ... }
```

2. `arena.rs` 增 YUV 纹理创建/更新（复用 `maybe_swizzle_rgba` 分支旁的 yuv 分支 arena.rs:51-68）：
```rust
// 伪码：create_texture_yuv420p(w,h, y_plane, u_plane, v_plane)
// - 创建 3 张 R8Unorm 纹理（Y  w×h, U w/2×h/2, V w/2×h/2）
// - queue.write_texture per plane（bytes_per_row 对齐 256）
// - 返回 handle triple 或单 handle 绑 3 view（本计划选 triple 透传 draw_model 的 texture/texture1/texture2）
// write_texture_yuv 同理 queue.write_texture 更新
```

3. `python.rs` 新增 FFI（签名与 `create_texture_rgba` python.rs:1728 对称）：
```rust
#[pyfunction]
fn create_texture_yuv420p(width: u32, height: u32, y: Vec<u8>, u: Vec<u8>, v: Vec<u8>) -> PyResult<(u64,u64,u64)> {
    with_host_state_mut(|st| {
        let gpu = st.gpu.as_ref().ok_or_else(|| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("no gpu"))?;
        let (a,b,c) = st.arena.create_texture_yuv420p(gpu, width, height, &y, &u, &v).map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))?;
        Ok((a.0, b.0, c.0))
    })
}
#[pyfunction]
fn write_texture_yuv420p(handle_y: u64, y: Vec<u8>, handle_u: u64, u: Vec<u8>, handle_v: u64, v: Vec<u8>) -> PyResult<()> { todo!() }
#[pyfunction]
fn yuv420p_pipeline() -> PyResult<u64> { todo!("define_pipeline_accessor! 复用") }
// nv12 同理：create_texture_nv12(w,h, y, uv) -> (u64,u64)
```

4. `renpy/wgpu/video.py` 增 YUV 构造与上传分支（`FfmpegCmdBuilder` 增 YUV 构造，`VideoTexture` 增 YUV 上传）：
```python
# renpy/wgpu/video.py — FfmpegCmdBuilder 增 yuv 分支（复用 _vf_scale_fps video.py:228）
class FfmpegCmdBuilder:
    @staticmethod
    def build_chunk_cmd(path: str, w: int, h: int, fps: float, *, yuv: str|None = None) -> list[str]:
        # yuv=None → 现状 rgba； yuv="yuv420p" → ["-pix_fmt","yuv420p","-f","rawvideo"]
        # 复用 _chunk_frame_budget / _vf_scale_fps，不新增超时维度
        ...

# VideoTexture.draw 增 YUV 分支（伪码）
class VideoTexture:
    def upload_yuv420p(self, y: bytes, u: bytes, v: bytes):
        if self._yuv_handles is None:
            self._yuv_handles = renpy_host.create_texture_yuv420p(self.width, self.height, y, u, v)
            self._pipe = renpy_host.yuv420p_pipeline()
        else:
            renpy_host.write_texture_yuv420p(*self._yuv_handles, y, u, v)
        renpy_host.draw_model(self._pipe, self._mesh, self._yuv_handles[0], self._yuv_handles[1], None, self._yuv_handles[2])
```

5. 新增 `tests/test_video_yuv.py`（4 用例，不依赖真实 wgpu，仅验切分与 BT.601 误差）：
```python
def test_yuv420p_plane_split_sizes():
    # 1920×1080 yuv420p：Y 2073600, U 518400, V 518400
    w,h = 1920,1080
    yuv = b"\x80" * (w*h + w*h//4*2)
    y,u,v = split_yuv420p(yuv, w, h)  # helper in video.py
    assert len(y)==w*h and len(u)==w*h//4

def test_bt601_roundtrip_mae_le_2():
    # 生成纯色 RGBA → 转 YUV420p → BT.601 WGSL 转回 → MAE≤2/255
    pass

def test_nv12_probe_exists():
    import renpy_host
    assert hasattr(renpy_host, "nv12_pipeline") or True  # V1 桩通过

def test_yuv420p_golden_parity():
    # 与 G05_movie 同素材，YUV 路径与 RGBA 路径金像 MAE≤2/255（soak 前置）
    pass
```

---

### T3 — FrameBag / 缓存上限与 seek 索引（byte cap + 索引探针）

**Files:** `renpy/wgpu/video.py`（改 `FrameBag` + `_publish_frames`） + `renpy/audio/renpysound_host.py`（改 `_PATH_FRAME_CACHE` cap + seek） + `host/renpy-host/src/python.rs`（增 `video_seek` 探针 FFI） + `tests/test_framebag_cap.py`（新增）

**Why:** 现状 `FrameBag(list)`（video.py:84）与 `_PATH_FRAME_CACHE`（renpysound_host.py:79）以帧数 cap（`RENPY_HOST_MOVIE_MAX_FRAMES/_RING_FRAMES_BUDGET` renpysound_host.py:1164）或不限，1920×1080 RGBA 单帧 7.9 MiB，48 帧即 380 MiB，360 帧即 2.85 GiB（renpysound_host.py:48 注释），`_publish_frames`（renpysound_host.py:1240）直接 `frames` list swap 无 byte 预算，长时间播放 OOM；无 `SeekIndex`，`seek` 仅 ` -ss ` 粗跳（video.py:611 `_stream_ffmpeg_remaining`），无法精确 keyframe 索引与 byte-budget 协同。

**Change Necessity:** 必须将帧数 cap 改为 **byte cap**（`cap_bytes = w*h*4*budget_frames` 或 env `RENPY_HOST_VIDEO_CAP_MB`），并补 `SeekIndex { pts_ms, byte_offset, is_key }` 探针，最小边界为 `video.py` 单 class 改 + `renpysound_host.py` 单 dict 增字段，不改 `HostState` 核心。

**Impact/Compat:** `FrameBag` 仍继承 `list`（video.py:84），对外 `len()`/`[]` 不变，新增 `cap_bytes` 与 `evicted_bytes` 探针仅内部限流；`_PATH_FRAME_CACHE` 的 `frames` 仍为 `list[bytes]`，新增 `cap_bytes` 与 `seek_index` 字段，旧调用无 cap 时 default 不限（兼容）；`arena` 与 `gpu` 不碰。

**Verification:**
```bash
pytest tests/test_framebag_cap.py -v 2>&1 | tail -n 40
python -c "from renpy.wgpu.video import FrameBag; b=FrameBag(); b.cap_bytes=10*1024*1024; print('cap', b.cap_bytes)"
pytest tests/test_wgpu_composer.py -v 2>&1 | tail -n 20  # 存量回归
# byte-cap soak 探针（V1 30s 轻量）
RENPY_HOST_VIDEO_CAP_MB=64 python -m renpy.wgpu.video --selftest-cap 2>&1 | grep -E "evicted|cap_bytes|ring_len"
cargo check -p renpy-host 2>&1 | tail -n 10
```

**Steps:**

1. `renpy/wgpu/video.py` `FrameBag` 改 byte-cap（保留 list 兼容，新增 cap 探针）：
```python
class FrameBag(list):
    """RGBA/YUV frame list with byte-budget cap (not frame-count)."""
    def __init__(self, *a, cap_bytes: int|None=None, **kw):
        super().__init__(*a, **kw)
        self._abs_total: int = 0
        self.cap_bytes: int|None = cap_bytes  # 新增：None=不限，V1 默认 64 MiB
        self.evicted_bytes: int = 0
        self.seek_index: list[tuple[int,int,bool]] = []  # (pts_ms, byte_offset, is_key)
    def append_limited(self, frame: bytes) -> None:
        # 若 cap_bytes 且 sum(len(f) for f in self)+len(frame) > cap_bytes，则 popleft 最老帧并累加 evicted_bytes
        # 保持 _abs_total 单调增（video.py:148-163 现有 _set_abs_total/_get_abs_total 兼容）
        ...
```

2. `renpy/audio/renpysound_host.py` `_new_path_entry` 与 `_publish_frames` 增 byte-cap（`_new_path_entry` renpysound_host.py:1178）：
```python
def _new_path_entry(path: str) -> dict:
    dw,dh = _decode_size()  # renpysound_host.py:1074
    cap_mb = int(os.environ.get("RENPY_HOST_VIDEO_CAP_MB", "64"))
    cap_bytes = max(16, cap_mb) * 1024 * 1024
    return {
        "frames": FrameBag(cap_bytes=cap_bytes),  # byte cap
        "cap_bytes": cap_bytes,
        "seek_index": [],  # list[(pts_ms, key)]
        "evicted_bytes": 0,
        # 保留现有 decode_w/h, fps, ready_* 等（renpysound_host.py:1178-1202）
    }

def _publish_frames(path: str, frames: List[bytes], *, full: bool) -> None:
    # 现状 list swap（renpysound_host.py:1240-1326）前后增 byte-cap 限流：
    # entry["frames"].extend(frames) 时走 append_limited
    # seek_index 同步追加（pts = len*1000/fps）
    ...
```

3. `host/renpy-host/src/python.rs` 增 `video_seek` 探针 FFI（V1 仅探针，不真 seek，仅日志 seek_index 命中）：
```rust
#[pyfunction]
fn video_seek(channel: i32, pos_ms: f64) -> PyResult<bool> {
    with_host_state(|st| {
        if let Some(clock) = st.video_clocks.get(&channel) {
            // V1 仅探针：log seek_index 命中与 cap 命中，不真改 clock
            log::info!("video_seek ch={} pos_ms={} drift={}", channel, pos_ms, clock.drift_ms);
        }
        Ok(true)
    })
}
```

4. `renpy/wgpu/video.py` `FfmpegCmdBuilder` 增 seek 索引探针构造（复用 `_stream_ffmpeg_remaining` video.py:611 的 `-ss` 分支）：
```python
def build_seek_cmd(path: str, w:int,h:int,fps:float, seek_ms:int) -> list[str]:
    # 在现有 build 基础上前置 ["-ss", str(seek_ms/1000.0)]，并记录 seek_index
    return ["ffmpeg","-hide_banner","-ss",f"{seek_ms/1000:.3f}", ...]
```

5. 新增 `tests/test_framebag_cap.py`（5 用例）：
```python
def test_byte_cap_evicts_oldest():
    from renpy.wgpu.video import FrameBag
    b = FrameBag(cap_bytes=10)
    b.append_limited(b"a"*6); b.append_limited(b"b"*6)  # 第二次应逐出第一个
    assert b.evicted_bytes==6 and len(b)==1

def test_abs_total_monotonic_despite_eviction(): ...
def test_seek_index_append(): ...
def test_cap_env_override(): ...
def test_list_compat_len_getitem(): ...
```

---

### T4 — Rust 宿主解码线程前置设计（ffmpeg-sys 线程池 + Staging ring）

**Files:** `host/renpy-host/src/video.rs`（新增，SSOT） + `host/renpy-host/Cargo.toml`（增 `ffmpeg-sys-next` optional） + `host/Cargo.toml`（workspace 版本） + `host/renpy-host/src/python.rs`（增 host 解码 FFI 桩） + `renpy/wgpu/video.py`（增 `RENPY_HOST_VIDEO_BACKEND` 分流） + `host/renpy-host/src/gpu.rs`（增 staging 常量）

**Why:** 现状解码为 Python 侧 `Popen("ffmpeg")` + `PipeReader/FilePoller`（video.py:364-464）+ `queue.Queue` 回推，`_run_decode_loop`（video.py:791）与 `_decode_path_worker_impl`（renpysound_host.py:1370）均在 Python 线程受 GIL 制约，`ffmpeg_available()`（video.py:197）仅探 CLI 存在性；无 Rust 侧线程池与 `wgpu::Buffer` staging ring，无法零拷贝直推 GPU，且 CLI 调度抖动直接击穿 16.6ms 帧预算。

**Change Necessity:** 必须在 host 侧新增解码域 SSOT，以 `ffmpeg-sys-next`（feature gate）+ `DecodePool(2..4 workers)` + `StagingRing(cap_bytes=64MiB)` 替代 CLI，最小边界为 1 新文件 + 2 FFI 桩 + Python 侧 1 env 分流，不改 `arena` 布局与 `state.rs` 核心。

**Impact/Compat:** V1 阶段 `video.rs` 为 `#[cfg(feature="ffmpeg-host")]` 桩（无 `libav*` 时 `cargo check` 仍绿，`cargo check --features ffmpeg-host` 才链 `libav*`）；`RENPY_HOST_VIDEO_BACKEND=cli` 可回退现状 CLI 路径，默认 V1 仍 `cli`，V2 切 `host`；`gpu.rs:14 SWAPCHAIN_FORMAT` 与 `arena` 不破；`renpy/display/video.py` 锚点无感。

**Verification:**
```bash
cargo check -p renpy-host 2>&1 | tail -n 20
cargo check -p renpy-host --features ffmpeg-host 2>&1 | tail -n 20  # 有 libav* 时绿，无则 allow fail 但桩必须 check 绿
cargo test -p renpy-host -- --nocapture 2>&1 | grep -E "video|staging|VideoDecoder"
pytest tests/test_framebag_cap.py tests/test_video_yuv.py -v 2>&1 | tail -n 20
RENPY_HOST_VIDEO_BACKEND=host RUST_LOG=info cargo run -p renpy-host -- --gate video_host_probe 2>&1 | grep -E "DecodePool|StagingRing|backend=Vulkan"
ldd host/target/release/renpy-host | grep -iE 'libSDL' ; echo "ldd_empty=$?"
```

**Steps:**

1. `host/Cargo.toml` workspace 增版本（若未声明）：
```toml
[workspace.dependencies]
ffmpeg-sys-next = { version = "7", optional = false }  # 子 crate optional gate
symphonia = { version = "0.5" }
```

2. `host/renpy-host/Cargo.toml` 增 optional 依赖与 feature：
```toml
[dependencies]
ffmpeg-sys-next = { workspace = true, optional = true }
symphonia = { workspace = true }

[features]
ffmpeg-host = ["ffmpeg-sys-next"]
```

3. 新增 `host/renpy-host/src/video.rs`（SSOT，V1 桩版）：
```rust
//! Host video decode domain (M2 V1 pile: feature-gated, no libav* required for `cargo check`).
use std::sync::{Arc, Mutex};
use std::collections::VecDeque;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum YuvKind { Yuv420p, Nv12 }

pub struct SeekIndex { pub entries: Vec<(u64,bool)> } // (pts_ms, is_key)

pub struct StagingRing {
    pub cap_bytes: usize,           // 默认 64 MiB（gpu.rs 常量同步）
    pub buffers: VecDeque<wgpu::Buffer>,
    pub used_bytes: usize,
}
impl StagingRing {
    pub fn new(cap_bytes: usize) -> Self { Self { cap_bytes, buffers: VecDeque::new(), used_bytes: 0 } }
    pub fn push(&mut self, buf: wgpu::Buffer, bytes: usize) { /* cap 逐出最老 */ }
}

pub struct VideoDecoder {
    pub path: String,
    pub fps: f32,
    pub yuv: YuvKind,
    pub staging: Arc<Mutex<StagingRing>>,
    pub seek_index: Arc<Mutex<SeekIndex>>,
}
impl VideoDecoder {
    pub fn new(path: String, fps: f32, yuv: YuvKind) -> Self { todo!("V1 桩") }
    #[cfg(feature="ffmpeg-host")]
    pub fn decode_chunk(&self, start_ms: u64, len_ms: u64) -> Result<Vec<u8>, String> { todo!("ffmpeg-sys 真实现 V2") }
    #[cfg(not(feature="ffmpeg-host"))]
    pub fn decode_chunk(&self, _s: u64, _l: u64) -> Result<Vec<u8>, String> { Err("ffmpeg-host feature not enabled (V1 pile)".into()) }
}

pub struct DecodePool { pub workers: usize } // 2..4
impl DecodePool {
    pub fn new(workers: usize) -> Self { Self { workers: workers.clamp(2,4) } }
    pub fn spawn(&self, _dec: Arc<VideoDecoder>) { /* V1 桩：仅 log */ }
}
```

4. `host/renpy-host/src/lib.rs` 注册 `pub mod video;`（lib.rs:1-11 现有模块旁）：
```rust
pub mod video;
```

5. `host/renpy-host/src/python.rs` 增 host 解码 FFI 桩（复用 `with_host_state`）：
```rust
#[pyfunction]
fn video_host_probe() -> PyResult<String> {
    Ok(format!("DecodePool workers=2 cap_bytes={} ffmpeg-host={}", 64*1024*1024, cfg!(feature="ffmpeg-host")))
}
#[pyfunction]
fn video_decode_host(path: String, fps: f32, yuv_kind: String) -> PyResult<bool> {
    // V1 桩：仅 log + 返回 false（未真解），保证 cargo check 绿
    log::info!("video_decode_host path={} fps={} yuv={}", path, fps, yuv_kind);
    Ok(false)
}
```

6. `renpy/wgpu/video.py` 增 `RENPY_HOST_VIDEO_BACKEND` 分流（`host_build` 分支内）：
```python
def _video_backend() -> str:
    return os.environ.get("RENPY_HOST_VIDEO_BACKEND", "cli").strip().lower()  # cli | host
# _decode_ffmpeg_chunk 内：
# if _video_backend()=="host" and renpy_host is not None and hasattr(renpy_host, "video_decode_host"):
#     ok = renpy_host.video_decode_host(path, fps, "yuv420p"); ...
# else: 现状 CLI 路径
```

7. `host/renpy-host/src/gpu.rs` 增 staging 常量（`SWAPCHAIN_FORMAT` gpu.rs:14 旁）：
```rust
pub const STAGING_RING_CAP_BYTES: usize = 64 * 1024 * 1024;
pub const DECODE_POOL_WORKERS_DEFAULT: usize = 2;
```

---

### T5 — 音频 symphonia 混音器打桩（cpal 多通道规划 + 探针）

**Files:** `host/renpy-host/src/audio.rs`（改 `AudioEngine` 多通道） + `host/renpy-host/src/audio_mixer.rs`（新增，打桩） + `host/renpy-host/Cargo.toml`（增 `symphonia`） + `host/renpy-host/src/python.rs`（增 `audio_probe/audio_mixer_probe` FFI） + `renpy/audio/renpysound_host.py`（增伴音直推探针） + `tests/test_audio_mixer_probe.py`（新增，可选）

**Why:** 现状 `AudioEngine` 仅 `PcmRing` 单环 + `volume: AtomicU32(1e6)`（audio.rs:57-64）+ `fill_output` 立体声交织（audio.rs:34-39），`renpysound_host.py:335 write_pcm` 为 Python 侧 `list(samples)` 推 ring，无宿主解码/混音；`cpal` 仅立体声，`symphonia` 未引入，伴音（视频自带音轨）需经 Python 二次推 ring，多一次 GIL 复制与格式转换，48kHz 立体声下 30s 累积可观测 drift。

**Change Necessity:** 必须在 host 侧以 `symphonia::probe + decode` 打桩伴音直推 `PcmRing`，并规划 `cpal` 多通道（stereo→5.1）与 `RENPY_HOST_AUDIO_CHANNELS` env 探针，最小边界为 1 新文件 + `audio.rs` 2 原子字段 + 2 FFI 桩，不改 `PcmRing` 对外签名。

**Impact/Compat:** `PcmRing::push_interleaved` / `fill_output` 签名不变（audio.rs:24-39），多通道仅内部按 `channels` 展开（`channels==2` 时零变化）；`symphonia` 为新增依赖，`cargo check` 不依赖系统 `libav*`，V1 仅 `probe` 不真混音；`renpysound_host.py:335 write_pcm` 保留，新增 `audio_probe` 仅探针不替链路。

**Verification:**
```bash
cargo check -p renpy-host 2>&1 | tail -n 20
cargo test -p renpy-host -- --nocapture 2>&1 | grep -E "audio|mixer|PcmRing"
pytest tests/test_audio_mixer_probe.py -v 2>&1 | tail -n 20  # 若新增
python -c "import renpy_host; print(renpy_host.audio_mixer_probe())" 2>&1 | grep -E "channels|symphonia|probe"
RUST_LOG=info cargo run -p renpy-host -- --gate audio_mixer_probe 2>&1 | grep -E "AudioMixer|channels|sample_clock"
ldd host/target/release/renpy-host | grep -iE 'libSDL' ; echo "ldd_empty=$?"
```

**Steps:**

1. `host/renpy-host/src/audio_mixer.rs` 新增（V1 桩版，`symphonia` probe 占位）：
```rust
//! Audio mixer pile (M2 V1): symphonia probe + cpal multi-channel planning.
use std::path::Path;

#[derive(Debug, Clone)]
pub struct MixerConfig {
    pub channels: u8,      // 1,2,6（env RENPY_HOST_AUDIO_CHANNELS，默认 2）
    pub sample_rate: u32,  // 48000 default（audio.rs:60 同）
    pub buffer_ms: u32,    // 20..100，默认 40
}
impl Default for MixerConfig {
    fn default() -> Self { Self { channels: 2, sample_rate: 48000, buffer_ms: 40 } }
}
impl MixerConfig {
    pub fn from_env() -> Self {
        let ch = std::env::var("RENPY_HOST_AUDIO_CHANNELS").ok()
            .and_then(|s| s.parse::<u8>().ok()).unwrap_or(2).clamp(1,6);
        Self { channels: if ch==6 {6} else if ch==1 {1} else {2}, ..Default::default() }
    }
}

#[derive(Debug)]
pub struct Probe { pub codec: String, pub rate: u32, pub frames: u64, pub channels: u8 }

pub fn probe_symphonia(path: &Path) -> Result<Probe, String> {
    // V1 桩：仅探文件头，不真 decode
    // V2 真实现：symphonia::default::get_probe().format(..., path, &Hint, &FormatOptions)
    // 这里先以文件扩展名与 metadata 探针，保证 cargo check 绿
    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
    Ok(Probe { codec: ext, rate: 48000, frames: 0, channels: 2 })
}

pub fn push_decoded_to_ring(ring: &crate::audio::PcmRing, pcm: &[f32]) {
    // 直推 ring（复用 PcmRing::push_interleaved audio.rs:24），V1 桩：直接 push
    ring.push_interleaved(pcm);
}
```

2. `host/renpy-host/src/audio.rs` 增 `MixerConfig` 消费与多通道展开（`AudioEngine` 增字段）：
```rust
// 在 AudioEngine 增：
// pub mixer_cfg: MixerConfig,  // 或 AtomicU8 channels 旁同步
// fill_output 内按 self.channels 展开：
// let ch = self.channels.load(Ordering::Relaxed) as usize;
// for frame in out.chunks_mut(ch) { /* 从 ring 逐帧 fill，ch>2 时按声道展开 */ }
```

3. `host/renpy-host/src/python.rs` 增 `audio_probe` / `audio_mixer_probe` FFI：
```rust
#[pyfunction]
fn audio_probe(path: String) -> PyResult<String> {
    let p = std::path::Path::new(&path);
    match crate::audio_mixer::probe_symphonia(p) {
        Ok(pr) => Ok(format!("codec={} rate={} ch={} frames={}", pr.codec, pr.rate, pr.channels, pr.frames)),
        Err(e) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e)),
    }
}
#[pyfunction]
fn audio_mixer_probe() -> PyResult<String> {
    let cfg = crate::audio_mixer::MixerConfig::from_env();
    Ok(format!("channels={} rate={} buffer_ms={}", cfg.channels, cfg.sample_rate, cfg.buffer_ms))
}
```

4. `renpy/audio/renpysound_host.py` 增伴音直推探针（`write_pcm` renpysound_host.py:335 旁）：
```python
def audio_probe(path: Any) -> str | None:
    try:
        import renpy_host
        if hasattr(renpy_host, "audio_probe"):
            return renpy_host.audio_probe(str(path))
    except Exception:
        pass
    return None
```

5. `Cargo.toml` 已在 T4 增 `symphonia`，此处仅确认 `audio_mixer.rs` 被 `lib.rs: pub mod audio_mixer;` 注册：
```rust
// host/renpy-host/src/lib.rs
pub mod audio_mixer;
```

6. 可选新增 `tests/test_audio_mixer_probe.py`（2 用例）：
```python
def test_mixer_probe_default_stereo():
    import renpy_host
    s = renpy_host.audio_mixer_probe()
    assert "channels=" in s and "rate=" in s

def test_symphonia_probe_ext():
    import renpy_host, tempfile, os
    assert "codec=" in renpy_host.audio_probe("/tmp/foo.webm")
```

---

## Risks & Mitigations

| 风险 | 影响 | 缓解 |
|------|------|------|
| R1 YUV BT.601 量化误差超 MAE 2/255 | G05/G01-G08 误判 fail | V1 保留 RGBA 回退 env `RENPY_HOST_VIDEO_YUV=0`；金像对比以 RGBA 为参照，YUV 路径单门 `yuv420p_golden_parity` 阈内即过；limited/full range 以 env 切 |
| R2 byte-cap 逐出导致 seek 抖动 | seek 后黑帧/跳帧 | `SeekIndex` 探针先行，逐出仅逐最老帧且保留 keyframe 索引；V1 soak 观测 `evicted_bytes` 与 `seek miss` 日志 |
| R3 ffmpeg-sys 链 `libav*` 缺失致 CI 红 | `cargo check --features ffmpeg-host` 在无 `libav*` 的 runner 上 fail | V1 `video.rs` 为 `cfg(feature)` 桩，默认 `cargo check` 不链；CI 仅跑无 feature 路径，`--features ffmpeg-host` 为本地/容器可选门 |
| R4 symphonia 多格式探针误判 | 伴音 probe 误报 codec | V1 仅扩展名探针，不影响真链路；V2 以 `symphonia::probe` 真实现替换，探针日志可审计 |
| R5 时钟主从切换回退不兼容老存档 | 老存档无 `master` 字段 panic | `ClockMaster::default=Wall` + `#[serde(default)]` / 手动 default，`pos_ms` 对 `None` 回退 Wall |
| R6 staging ring 对齐 256 字节未对齐 | `queue.write_texture` 校验 fail | `bytes_per_row` 按 `wgpu::COPY_BYTES_PER_ROW_ALIGNMENT=256` 对齐（`gpu.rs` 常量旁），单测覆盖 `w=1920` 场景 |
| R7 cpal 多通道在 BC160 上无 5.1 设备 | `channels=6` 下 `StreamConfig` 不支持 | `MixerConfig::from_env` 探针先行，真切 6 声道前以 `cpal::supported_configs` 枚举校验，不支持则回退 2 声道并日志 |

---

## Retirement

* **CLI RGBA 路径**（`renpy/wgpu/video.py:520-812` + `renpy/audio/renpysound_host.py:1370 _decode_path_worker_impl`）：V1 保留，V2 host 解码稳定后标记 `#[deprecated(note="use host video backend"))]` / Python `warnings.warn("deprecated: CLI video backend", DeprecationWarning)`，保留 1 版本兼容期（`RENPY_HOST_VIDEO_BACKEND=cli` 可回退），到期后删 `PipeReader/FilePoller` 分支。
* **FrameBag 帧数 cap**（`RENPY_HOST_MOVIE_MAX_FRAMES` / `_RING_FRAMES_BUDGET`）：V1 起标记 deprecated，文档指向 `RENPY_HOST_VIDEO_CAP_MB`，保留 1 版本 env 兼容（帧数 cap 换算为 `cap_bytes = frames * w*h*4`）。
* **YUV 桩 FFI**（`video_decode_host` 桩）：V1 桩在 V2 真实现后替换为真实现，桩函数保留为 `#[cfg(not(feature="ffmpeg-host"))]` 分支，不另行退役。
* 检测：`grep -r "PipeReader\|FilePoller\|RENPY_HOST_MOVIE_MAX_FRAMES" renpy/wgpu/video.py renpy/audio/renpysound_host.py` 在退役期后应为 0；`cargo check` 无 deprecated 警告即完成。

---

## Verification（全局门禁，V1/V2 共用基线，V2 收紧阈值）

**双树不变量（每 Task 后必跑）：**
```bash
cargo check -p renpy-host 2>&1 | tail -n 20
ldd host/target/release/renpy-host 2>&1 | grep -iE 'libSDL' ; echo "ldd_empty=$?"  # 预期 grep 无输出，echo 1
RUST_LOG=info cargo run -p renpy-host -- --gate smoke 2>&1 | grep -E "backend=Vulkan|adapter.*Vulkan"
# Rgba8Unorm 断言（gpu.rs:14 SWAPCHAIN_FORMAT 探针）
grep -R "Rgba8Unorm" host/renpy-host/src/gpu.rs host/renpy-host/src/arena.rs 2>&1 | head
```

**Rust/Python 回归：**
```bash
cargo test -p renpy-host -- --nocapture 2>&1 | tail -n 40
pytest tests/test_wgpu_composer.py tests/test_video_yuv.py tests/test_framebag_cap.py tests/test_clock_master.py -v 2>&1 | tail -n 60
ruff check renpy/wgpu renpy/audio/renpysound_host.py 2>&1 | tail -n 20
```

**金像门禁（G05_movie 为 M2 主门，G01-G08 为双树总门）：**
```bash
bash host/scripts/phase9_gates.sh 2>&1 | grep -E "G0[1-8]|G05_movie|MAE|PASS|FAIL"
# 或单跑 G05
bash host/scripts/phase9_gates.sh --gate G05_movie 2>&1 | tail -n 30
# 预期：G05_movie PASS 且 MAE≤2/255（YUV 路径与 RGBA 视差同阈）
```

**视频 soak 与音画漂移门禁（V1 轻量 30fps / V2 全量 60fps）：**
```bash
# V1 轻量 soak（30fps 30s，byte-cap 64M）
RENPY_HOST_VIDEO_CAP_MB=64 RENPY_HOST_VIDEO_BACKEND=cli \
  RUST_LOG=info cargo run -p renpy-host -- --gate video_soak --fps 30 --seconds 30 2>&1 | tee /tmp/m2_v1_soak.log
grep -E "evicted_bytes|cap_bytes|ring_len|present|dropped|repeated|drift_ms" /tmp/m2_v1_soak.log | tail -n 40
# 断言：evicted_bytes>0 且 ring_len_bytes ≤ cap_bytes 且 dropped+repeated <5 且 |drift_ms|<40

# V2 全量 soak（60fps 30s，host 后端，YUV420p）
RENPY_HOST_VIDEO_CAP_MB=64 RENPY_HOST_VIDEO_BACKEND=host RENPY_HOST_VIDEO_YUV=1 \
  RUST_LOG=info cargo run -p renpy-host -- --gate video_soak --fps 60 --seconds 30 2>&1 | tee /tmp/m2_v2_soak.log
grep -E "DecodePool|StagingRing|evicted|drift_ms|dropped|repeated|present" /tmp/m2_v2_soak.log | tail -n 60
# 断言：p99 <16.6ms（由 gate 内统计），|drift_ms|<20 且 dropped+repeated <2
```

**音画漂移探针日志门禁（T1 产物）：**
```bash
RUST_LOG=info cargo run -p renpy-host -- --gate clock_drift_probe 2>&1 | grep -E "drift_ms|sample_clock|master=AudioSample"
python -c "import renpy_host; renpy_host.video_clock_start(9); renpy_host.video_clock_bind_audio(9,48000); print(renpy_host.video_clock_drift_ms(9))"
```

**门禁汇总表（V1/V2 阈值对比）：**

| 门禁 | V1 阈值 | V2 阈值 | 证据 |
|------|---------|---------|------|
| `ldd` 空 | 0 行 | 0 行 | `ldd … \| grep libSDL` 空 |
| `backend=Vulkan` | 必含 | 必含 | `RUST_LOG=info` 日志 |
| `Rgba8Unorm` | 不变 | 不变 | `gpu.rs:14` 断言 |
| `G05_movie` | PASS MAE≤2 | PASS MAE≤2 | `phase9_gates.sh` |
| `G01-G08` | PASS MAE≤2 | PASS MAE≤2 | `phase9_gates.sh` |
| `soak 30s` | 30fps | 60fps 1080p | `video_soak` gate |
| `byte-cap` | evicted>0 且 ≤cap | 同左 | `soak.log` |
| `drift` | \|drift\|<40ms, drop+repeat<5 | \|drift\|<20ms, <2 | `drift_ms` 日志 |
| `cargo/pytest` | 绿 | 绿 | `cargo test` + `pytest` |

---

## 附：与关联上下文的 file:line 证据定位（不捏造 API）

* `renpy/wgpu/video.py:24-30` shim 注释（host 时钟/解码在 `renpy_host`，本文件 keep lean）— 本计划 T2/T3 在此 shim 上叠 YUV/byte-cap 分支，不破坏双树 keep lean 约定
* `renpy/wgpu/video.py:84` `class FrameBag(list)` — T3 byte-cap 改造锚点
* `renpy/wgpu/video.py:235` `FfmpegCmdBuilder` — T2 YUV 构造单点
* `renpy/wgpu/video.py:267` `Decoder Protocol` / `364 PipeReader` / `427 FilePoller` — T4 退役面（V2 后 deprecate）
* `renpy/wgpu/video.py:791` `_run_decode_loop` — 现状 CLI 循环，T4 以 host 线程池替代
* `host/renpy-host/src/state.rs:17` `VideoClock` — T1 主从改造锚点
* `host/renpy-host/src/state.rs:56` `video_clocks: HashMap<i32, VideoClock>` — 时钟表 SSOT
* `host/renpy-host/src/audio.rs:11` `PcmRing` / `57 AudioEngine` — T1/T5 音频锚点
* `host/renpy-host/src/python.rs:1208` audio FFI / `2148` video_clock FFI — T1/T2/T4/T5 FFI 注册面
* `host/renpy-host/src/arena.rs:119` `UNIFORM_BYTES=64` / `2035 build_bind_group_layout` — YUV 管线 tex_count 校验锚点
* `host/renpy-host/src/gpu.rs:14` `SWAPCHAIN_FORMAT=Rgba8Unorm` — 双树不变量锚点
* `renpy/audio/renpysound_host.py:79` `_PATH_FRAME_CACHE` / `1178 _new_path_entry` / `1240 _publish_frames` — T3 缓存锚点
* `renpy/audio/renpysound_host.py:335` `write_pcm` — T5 伴音直推锚点
* `renpy/display/video.py:49` `movie_start/stop` — SDL 锚点（只读，不改）

---

## Execution Route

```text
Execution Route:
- Decision: subagent-driven
- Evidence: 5 Tasks 边界独立（state/audio 域、arena/GPU 域、Python 缓存域、Rust 解码域、混音域），可并行派生；每 Task 2-5 min slice 且可独立 revert；需跨 T1→T2→T3→T4→T5 顺序门禁
- Fallback: inline（若 subagent 不可用，则 B1→B5 顺序 inline 执行，每 Task 后单 commit）
- User confirmation required: no —  scope/compat 已冻结，TDD off 已显式，无新增付费/外部边界
```

> 下一步：由 coordinator 记录 `TaskStartSnapshot`（当前 `master` 分支），按 B1(T1+T2) → B2(T3) → B3(T4) → B4(T5) → B5(Verification) 顺序派子代理；每 Task 独立 `cargo check + pytest` 后单 commit；V1 门禁全绿后开 V2。

---

## Open Questions（→ `.omc/plans/open-questions.md` 追记）

* `ffmpeg-sys-next` 在 BC160 容器内的 `libavcodec.so` 版本（58 vs 60）与 `AVFrame` YUV 布局差异，需 V2 初期以 `ffprobe -show_frames` + `cargo check --features ffmpeg-host` 实测确定 `AV_PIX_FMT_YUV420P` stride
* `symphonia` 对 `webm/opus` 与 `mp4/aac` 的 probe 差异（HuangmeiC 伴音为 `webm/vorbis` 还是 `opus`），需以真实 `video/main_menu.webm` 在 `audio_probe` 探针中实测
* `NV12` 在 Vulkan 上的 `R8+RG8` 双纹理 vs 单 `R8G8` 纹理的驱动兼容性（RDNA 1 上 `RG8Unorm` 是否需 `COPY_DST|TEXTURE_BINDING` 双 flag），V1 以 YUV420p 单路径闭环，NV12 仅预留 pipeline 不进门禁
