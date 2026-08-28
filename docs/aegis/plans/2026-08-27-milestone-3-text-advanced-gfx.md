# Implementation Plan: Milestone 3 — 文本与高级渲染（HarfBuzz/SDF + 逆变换裁剪 + 手柄/辅助功能）

**Date:** 2026-08-27  
**Goal:** `GPU 动态字形图集与复杂排版 + GPU 裁剪 + Gamepad/IME 完善，消除 Pillow 单行栅格与 AABB 裁剪债务`  
**Architecture:** `renpy-host (arena/gpu) + renpy/wgpu/text + host_pygame 输入垫片 + WGSL`  
**Tech Stack:** `Rust wgpu 24 + WGSL + FreeType/HarfBuzz(或 Pillow 渐进)+ host_pygame evdev/winit`  
**Baseline:** `AGENTS.md §4.7 文本/视频/模型 + renpy/wgpu/text.py + host/README.md 双树 + doc/wgsl_shader_migration.md + consensus AC4/AC7`  
**Compatibility:** `纯 Python→Rust host 字体授权与双树 Import 分流；保留 renpy.host_build 分支；文本 API 行为兼容老存档`  
**TDD Route:** `off / skipped / post-change regression`  
**Spec Brief:** 本 Milestone 重试 M3 文本与高级渲染债务，独立落盘不跨 M1/M2/M4 合并；前置调研为 `AGENTS.md §4.7` + `renpy/wgpu/text.py` Pillow 现状 + `host/renpy-host/src/arena.rs` GpuArena 资源模型 + `doc/wgsl_shader_migration.md` WGSL 合成器 + `host/README.md` 双树契约 + `consensus-wgpu-native-vulkan-rewrite.md` AC4/AC7  
**Scope:** 仅 M3 四子域（见 Tasks T1–T4），不含 M1 Host/WgpuDraw 拆分、M2 配置/常量收敛、M4 探针/门禁重整；禁动 SDL 参考树、SWAPCHAIN_FORMAT、Phase 9 G01–G08 已锁基线语义  
**Parent Plans:** `.omc/plans/consensus-wgpu-native-vulkan-rewrite.md` (AC1–AC9 / §4.7 / §4.3.1) + `host/README.md` 双图构建 + `doc/wgsl_shader_migration.md` + `renpy/wgpu/text.py` + `host/renpy-host/src/arena.rs`

> Route: fast-path — 纯文档计划产出（无源码改动），按 Template 要求落盘独立计划文件；Aegis 影响为计划结构与校验门禁约束。

---

## Plan Basis

```text
Aegis Visibility:
  M3 改的是 Pillow 单行位图→GPU 图集管线、AABB _clip_rect→GPU 裁剪管线、host_pygame TEXTINPUT→IME/gamepad 垫片三条主路径；
  错改会导致 G01–G08 任一 MAE>2/255、文本存档回放错位、旋转裁剪溢出、ldd SDL 逃逸或 INPUT gate 失活；先计划锁定文件边界与 Verification 再动代码。

BaselineUsageDraft:
- Required baseline refs: AGENTS.md §0–8（双树铁律/强制 Vulkan Rgba8Unorm/BgCache/HuangmeiC 教训 + §4.7 文本/视频/模型）、host/README.md §2–7（双图构建/ldd/Mechanism 1 Pump）、doc/wgsl_shader_migration.md（WGSL 合成器/HostShaderPart 诚实门）、.omc/plans/consensus-wgpu-native-vulkan-rewrite.md AC4/AC7 + §4.3.1 颜色/格式、host/renpy-host/src/arena.rs（GpuArena Slot/BG Cache/DrawCmd 16f blob）、renpy/wgpu/text.py（Pillow render_text_rgba 现状 122 行）、renpy/wgpu/draw_surftree.py:_clip_push_from_node、renpy/wgpu/draw_model.py:_clip_uv_frac、host/python/host_pygame/event.py + joystick.py/controller.py
- Delivered context refs: renpy/wgpu/text.py 已读（Pillow getbbox/draw.text + PIL_PADDING + anchor lt + even-align）、arena.rs 已读（TextureSlot/MeshSlot/PipelineSlot + DrawCmd uniform16 + BG Cache/BindGroupCache）、wgsl_shader_migration.md 已读（HostShaderPart soft-stub + composition_only + _PIPELINE_KEYS 诚实门）、host/README 双树表已读、phase1_gates.sh/phase9_gates.sh 已读、constants.py 已读（PIL_PADDING/ISO_BASIS 已解耦）
- Acknowledged before plan refs: AGENTS.md 全文（433 行已读）、host/README.md（411 行已读）、consensus 全文（530 行已读）、draw_surftree/draw_model clip 链路 grep 已读、host_pygame/event.py 100 行已读
- Cited in plan refs: 上述 7 + renpy/wgpu/draw_walk.py:43 WalkCtx.clip_rect + renpy/wgpu/draw_surftree.py:82 reverse-transform residual + renpy/wgpu/composer.py WgslShaderCache sha1[:16] + tests/test_wgpu_composer.py 78 行
- Missing refs: FreeType/HarfBuzz 具体 crate 选型基准测试（可选，非阻塞）、2K atlas SDF 半径调优 RGP 一帧（可选）、AT-SPI2 无障碍真实读屏器实体环境（显式 defer）
- Decision: continue

Requirement Ready Check:
- Requirement source refs: Prompt M3 合同（Goal/Architecture/Tech Stack/Baseline/Compatibility Header + 四覆盖域 + 四 Tasks 下限 + Verification 五门）
- Goals and scope refs: 消除 Pillow 单行栅格债务（全串每字形独立→texture quad 放大失真）与 AABB 裁剪债务（_clip_rect 仅轴对齐，逆变换/旋转溢出），补齐 Gamepad/evdev + IME + a11y 探针，扩展金库至多脚本/CJK 竖排/旋转裁剪/雾遮罩
- User / scenario refs: 上游存档文本回放兼容 + 多脚本/竖排游戏可跑通 + 旋转相框裁剪不漏光 + 手柄/输入法可打字通关 the_question
- Requirement item refs: 下述 T1–T4 逐条映射 Prompt 四覆盖域（见 Tasks.Why）
- Acceptance / verification criteria refs: §6 Acceptance 7 条可勾；Verification 五门 explicit cmd（cargo check / pytest / phase1_gates IME smoke / gamepad probe / MAE 文档化 / ldd/backend 断言）
- Open blocker questions: HarfBuzz vs Pillow RAQM 渐进阈值是否需 charter？—— 本计划显式定义三档渐进（见 T1），无阻塞
- Decision: ready

Change Necessity:
- User-visible need: 多脚本复杂排版（Arabic/Devanagari/Thai 连写+变形）当前 Pillow 单行 textbbox→RGBA 全串位图在放大/描边时锯齿且无字形复用；旋转裁剪（xclipping+zoom/reverse）当前 AABB 近似溢出 8–32px；手柄缺真 evdev 映射 + IME 无 TEXTEDITING 导致中文输入不可用
- No-change / non-code option: 仅改文档/配置无法消除单行栅格与 AABB 溢出—— 必须改渲染与输入代码路径
- Why code change is necessary: 必须新增 GPU 图集 atlas（二 K 级纹理 + 字形缓存驱逐）+ SDF/CDF 距离场 WGSL 采样 + GPU 裁剪（scissor/stencil）+ Gamepad/IME 垫片重写，方能让后续多脚本/竖排/特效线性扩展且不漂移双树契约
- Minimum change boundary: renpy/wgpu/text* + text_atlas + arena atlas 槽 + draw_surftree/draw_model clip 链路 + host_pygame joystick/controller/event + winit IME 桥 + goldens 增量（不含 SDL 树、SWAPCHAIN_FORMAT、WgslShaderCache 熵）
- Decision: code-change

Existence Check:
- Proposed new surface: renpy/wgpu/text_atlas.py（Atlas 管理器）+ renpy/wgpu/text_shaper.py（HarfBuzz 桥）+ renpy/wgpu/text_sdf.py（SDF 生成器）+ host/renpy-host/src/atlas.rs 或 arena.rs 内 AtlasPool + host/python/host_pygame/gamepad.py（可选聚合）+ tests/test_text_atlas.py/tests/test_clip_inverse.py/tests/test_gamepad_ime.py + testcases/wgpu_golden/G02↗/G03↗ 增量
- Existing owner / reuse candidate: renpy/wgpu/text.py（单文件 122 行 Pillow 栅格，无 atlas）、arena.rs（通用纹理/网格/RTT，无图集驱逐）、draw_surftree.py:_clip_push_from_node（AABB 互斥，残留逆变换）、host_pygame/joystick.py 478B stub（恒空设备）
- Why existing surface is insufficient: text.py 每串一次 create_texture_rgba（无字形复用、放大锯齿）；arena 无 2K atlas 驱逐/FIFO 复用；clip 链路无逆矩阵与 GPU 遮罩；joystick/controller 仅 stub 无 evdev/winit 真映射
- Creation proof: 通报 AGENTS.md §4.7 "文本 Pillow 位图→四边形，完整 WGSL 图集合成待后续" + draw_surftree.py:82 "we do **not** Documented residual — no half-implement" + text.py:5 "Full ftfont/atlas integration remains for later" 三处残留意图标注；新增 atlas/SDF 仅为该残留闭环，净债务↓
- Entropy / retirement impact: 新增 3 Python 文件 + 1 Rust 池替代每串大纹理 N 次分配，熵↓；退役触发：当 HarfBuzz 桥稳定且 goldens 零回归时，Pillow 单行路径退为 fallback（1 版本兼容后可移除）
- Decision: add-with-proof（仅 atlas/shaper/SDF + tests + goldens 增量）

Architecture Integrity Lens:
- Invariant: WgpuDraw 单渲染器，Rgba8Unorm+PMA，Backends::VULKAN，Host 分支 host_build，不破 SDL 树；WgslShaderCache sha1[:16] key 熵不变；ldd 空；MAE≤2/255
- Canonical owner / contract: renpy/wgpu/shaders.py Snippet IR + composer WgslShaderCache + arena.rs GpuArena LruSlotMap/RTT/BG；host_pygame/event 为 95% 真实现，joystick/controller 为待重写面
- Responsibility overlap: text 渲染（Python Pillow vs Rust atlas）双路径需显式 FeatureGate；clip（Python AABB 计算 vs GPU scissor/stencil 执行）需单 Owner：Python 算几何、Rust 执行遮罩；本计划明确所有权切分
- Higher-level simplification: 可否用单一 uber-atlas 管线合并文本/遮罩？否 — 本计划不引新 uber 维度，仅在既有 textured_pipeline 旁增 text_sdf_pipeline 专线，避免 cache_key 熵增
- Retirement / falsifier: 若 atlas 后 G01–G08 任一 MAE>2 即回滚单 Task；若新增 CJK 竖排仍需改 _ensure_host_texture_alive 则证明 AtlasResolver 抽象失败
- Verdict: reuse-existing owners, add atlas/shaper/SDF pool, edit-in-place 其余

Plan Pressure Test:
- Owner / contract / retirement: text_atlas Owner 独立 + clip 走 Surftree+Arena 双 Owner 协作（Python 几何→Rust 遮罩）、可 revert；无新 owner 增熵至全局
- Architecture integrity / higher-level path: 已验无更高层 Owner 可替代（arena 已为统一资源池，不另建 TextArena）
- Verification scope: cargo check + cargo test + pytest composer + phase1_gates IME + gamepad probe + phase9_gates G01–G08 + ldd/backend 全门齐全
- Task executability: 每 Task 2–7 天 slice（T1 最重），文件边界独立，失败可单 Task revert；T2 依赖 T1 的 atlas mesh 投递但可并行 stub（clip 几何独立于图集像素）
- Pressure result: proceed

Plan-Time Complexity Check:
- Target files: renpy/wgpu/text.py 122 + text_atlas~400 + text_shaper~300 + text_sdf~200 + draw_surftree~180 + draw_model~520 + draw_walk~490 + atlas.rs/arena.rs~300 + host_pygame/joystick 478B→~400 + controller 965B→~200 + event 463 行 + input.rs/event_queue.rs + shader.rs text_sdf WGSL 常量
- Existing size / shape signals: text.py 1 函数 60 行；clip 链路散 4 文件；arena 2691 行已近预算；host_pygame joystick 30% 完整度
- Owner fit: text_atlas 新 Owner 正确（隔离 Pillow/HarfBuzz 抉择）；clip 几何归 Surftree，执行归 Arena，owner 切分正确；gamepad 归 host_pygame + Rust input
- Add-in-place risk: 在 arena.rs 直接叠 atlas 会使 2691→3200 行触预算；本计划隔离 atlas.rs 独立文件，arena 仅聚合
- Better file boundary: atlas.rs 独立 crate-level 池 + text_atlas.py 统一上传/batch，复用 GpuHandleCache 模式
- Recommendation: extract helper（atlas helpers 同文件顶部） + add owner file（atlas.rs + text_atlas/shaper/sdf + tests）

Execution Readiness View:
- Intent Lock: 仅 M3 四子域（动态字形图集+SDF + 逆变换裁剪 + Gamepad/IME + 金库增量），不含 M1/M2/M4 范围
- Scope Fence: 下述 Files 表 14 文件可改；禁动 renpy/gl2/SDL 树、SWAPCHAIN_FORMAT/Mix 语义、WgslShaderCache key、Cython host 构建线
- Baseline Lock: Rgba8Unorm+PMA，tex_count∈{0..3, +1 text_sdf 显式新 key}，params16/matrixcolor16，MAE≤2/255 max≤16，ldd 空，backend=Vulkan，host_build 分支不破 SDL
- Approved Behavior: 像素等价增量（SDF 与 Pillow 位图在 1× 尺度 MAE≤2，放大时 SDF 更优）；旋转相框裁剪不漏光（±1px）；文本存档回放逐字对齐不漂
- Owner / Contract Constraints: shaders._PIPELINE_KEYS 单点（含新增 text_sdf_pipeline 诚实门），WgslShaderCache key 若增 text_sdf 维度需显式 bump 文档，assert_pipeline_map_honest() 仍过
- Compatibility Boundary: 纯 Python→Rust host 字体授权与双树 Import 分流——见 §Compatibility Frozen
- Retirement Boundary: Pillow 单行路径保留为 fallback 1 版本；AABB _clip_rect 保留为快路径（轴对齐时零开销），仅逆变换时走 GPU 遮罩
- Task Batches: B1 图集/SDF（T1）→ B2 逆裁剪（T2，可与 T1 并行几何层）→ B3 Gamepad/IME（T3）→ B4 金库增量与回归（T4，串行收口）
- Test Obligations: 新增 tests/test_text_atlas.py + tests/test_clip_inverse.py + tests/test_gamepad_ime.py；全量 cargo test + pytest + phase1/phase9 gates
- Review Gates: cargo clippy -W pedantic（atlas 新文件）+ ruff（text_* 新文件）+ naga validate（新 SDF/裁剪 WGSL）
- Drift / Rewind Rules: 单 Task 可独立 git revert；T1 任意 fail（MAE>2 或 atlas OOM）即全 plan pause，不进入 T2 GPU 开销验证
- Evidence Required Before Completion: phase9_gates 8/8 + G02/G03 增量 goldens MAE≤2 + ldd 空 + backend=Vulkan 日志 + IME smoke 日志 + gamepad 事件探针 JSON
- Advisory Boundary: method-pack 执行指引；非 GateDecision，仅达标前置
```

### TDD Route

```text
TDD Route:
- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: post-change regression（pytest/gates/goldens + 新增 atlas/clip/gamepad 单测；非 RED）
- Reason: M3 为渲染与输入管线增量（图集/SDF 早退、裁剪遮罩、Gamepad/IME），核心风险为像素等价性与输入时序，需金像+探针事后锁而非预写 RED；新契约（text API 兼容、clip 几何正确）以兼容性单测后补
- Verification: cargo check -p renpy-host + cargo test -p renpy-host + python -m pytest tests/test_wgpu_composer.py -v + bash host/scripts/phase1_gates.sh（IME smoke）+ gamepad 事件探针 + bash host/scripts/phase9_gates.sh（G01–G08 + G02/G03 增量）+ ldd/backend 断言
```

---

## Files

| 文件 | 动作 | 边界 |
|------|------|------|
| `renpy/wgpu/text.py` | **重构** | 保留 `render_text_rgba(text,size,color,bg,padding)->(w,h,bytes)` 签名作兼容入口；内部改走 `text_shaper`→`text_atlas`→`text_sdf` 链，Pillow 降为无 HarfBuzz/FreeType 时的 fallback；`_find_system_font()` 保留，增 `_shape_and_upload()` |
| `renpy/wgpu/text_atlas.py` | **新增** | 唯一 Atlas Owner：`AtlasManager { atlas_tex: u64, size=2048, glyph_cache: GpuHandleCache, lru: list, sdf_radius: int, padding: int }` + `alloc_glyph(glyph_id)->(u,v,w,h)` + `evict_lru()` + `upload_glyph_rgba()`；复用 `constants.HANDLE_PIXELS_CAP` 驱逐阈值，FIFO 驱逐与 `arena.rs texture_deferred_destroy` 对齐 |
| `renpy/wgpu/text_shaper.py` | **新增** | HarfBuzz 桥（`uharfbuzz` 或 `harfbuzz`），备援 Pillow RAQM：`shape(text, font, size, features)->list[GlyphPos]`；Arabic/Devanagari/Thai 连写+变形 + fallback 栈；无 HarfBuzz 时回退 `text.py:_font` RAQM 路径，保持行为兼容 |
| `renpy/wgpu/text_sdf.py` | **新增** | SDF/CDF 生成器：`render_sdf_glyph(bitmap, radius=8)->bytes`（8-bit 距离场）；同步 `shader.rs TEXT_SDF_WGSL` 采样半径与阈值；`PIL_PADDING` 与 `SDF_RADIUS` 分离常量 |
| `renpy/wgpu/constants.py` | **改** | 新增 `ATLAS_SIZE=2048 ATLAS_MAX_GLYPHS=4096 SDF_RADIUS=8 SDF_THRESHOLD=0.5 CLIP_STENCIL_BITS=8` 等具名常量（带 `# src:` 溯源），收敛 text/clip 魔法数 |
| `host/renpy-host/src/arena.rs` | **改** | 增 `text_sdf_pipeline` 工厂、`AtlasPool { texture: TextureSlot, free_rects: Vec<Rect> }` 聚合（或独立 `atlas.rs` 被 arena 持有）；`DrawCmd` 复用 `texture` 指向 atlas，`uniforms[0..4]` 承载 SDF 阈值/描边参数；BG Cache 不触 atlas 纹理 |
| `host/renpy-host/src/atlas.rs` | **新增（可选隔离）** | 若 arena 已超预算则独立文件：`AtlasTexture { id, size, glyph_slots: HashMap<GlyphKey, UvRect>, lru }`，暴露 `create_atlas_rgba/destroy_atlas/write_atlas_subrect` 给 `python.rs`；由 `arena.rs` 组合持有 |
| `host/renpy-host/src/shader.rs` | **改** | 新增 `TEXT_SDF_WGSL: &str`（`fs_main` 采样 atlas SDF + `smoothstep(threshold±aa)` 抗锯齿 + 描边/阴影可选）；`validate_wgsl_syntax` 覆盖新常量 |
| `renpy/wgpu/shaders.py` | **改** | 注册 `renpy.text_sdf`：`register_wgsl_shader("renpy.text_sdf", tex_count=1, uniform_layout_id="params16", ...)`；`_PIPELINE_KEYS["renpy.text_sdf"]="text_sdf_pipeline"`；`assert_pipeline_map_honest()` 同步 |
| `renpy/wgpu/draw_surftree.py` | **重构** | `_clip_push_from_node` 增逆矩阵分支：当 `node.reverse` 非单位矩阵时，返回裁剪多边形（`list[tuple[float,float]]` 四点）而非 AABB；保留 AABB 快路径；`_clip_intersect` 增多边形路径 |
| `renpy/wgpu/draw_model.py` | **改** | `draw_model` 增 `_clip_polygon` 探针：若 `self._clip_rect` 为 `None` 且 `_clip_poly` 非空，改走 GPU 遮罩路径（stencil 或 scissor polygon）；`_clip_uv_frac` 与多边形互斥，错时抛 `ComposerError` |
| `renpy/wgpu/draw_walk.py` | **改** | `WalkCtx.clip_rect` 增 `clip_poly: object = None` 字段；`DrawWalk` 透传；`_clip_push_from_node` 结果同时写入 `clip_rect` 与 `clip_poly` |
| `host/python/host_pygame/joystick.py` | **重写** | 40 行 stub→~400 行真实现：`Joystick(id).init/get_axis/get_button/get_hat` 走 `renpy_host.gamepad_*` FFI（evdev/winit GamepadEvent）；`get_count/quit` 真值；`JOY*` 事件经 `event.py` 注入 |
| `host/python/host_pygame/controller.py` | **改** | 同步 `Controller` 映射（`evdev`→`SDL_GameController` 轴/键语义）；`get_count`/`rumble` 桩转真（若硬件可） |
| `host/python/host_pygame/event.py` | **改** | 增 `JOYAXISMOTION/JOYBUTTONDOWN/JOYBUTTONUP/JOYHATMOTION/CONTROLLER*` 分发；`TEXTINPUT/TEXTEDITING` 已有通路保持，增 `TEXTEDITING` composition 长度校验 |
| `host/renpy-host/src/input.rs` | **改** | 增 `GamepadState { axes:[f32;6], buttons:[bool;16], hats }` + `poll_gamepad()`；`WindowEvent::Gamepad*` 译为 `HostEvent::Joy*`→`event_queue.rs`→`host_pygame.event` |
| `host/renpy-host/src/event_queue.rs` | **改** | 增 `JoyAxis/JoyButton/JoyHat/Controller` 队列变体；与 `PERIODIC/REDRAW/TIMEEVENT/TEXTINPUT` 同优先级注入 |
| `host/renpy-host/src/python.rs` | **改** | 暴露 `renpy_host.gamepad_count/gamepad_axis/gamepad_button/create_atlas_rgba?` 等 FFI；`10 个 *_pipeline()` 宏化若尚未（沿用 M1 宏），增 `text_sdf_pipeline()` accessor |
| `renpy/display/core.py` | **改（最小）** | `host_build` 分支内 `event_wait` 周边：`TEXTEDITING` composition 透传 + `JOY*` 事件免过滤（复用 AGENTS.md §5.1 分支风格，禁动 SDL 路径） |
| `tests/test_text_atlas.py` | **新增** | Atlas LRU 驱逐、2K 满驱逐、超大字形 fallback、dead-handle 恢复单测 |
| `tests/test_clip_inverse.py` | **新增** | 逆变换裁剪单测：单位矩阵 AABB 快路径等价性、90°/45° 旋转相框四点多边形、reverse+clip 嵌套、空交集短路 |
| `tests/test_gamepad_ime.py` | **新增** | Gamepad 轴位归一化、按钮位掩码、TEXTINPUT unicode 透传、TEXTEDITING compositionLen、IME rect 探针 |
| `testcases/wgpu_golden/G02* / G03*` | **增量** | G02 增 CJK 竖排+多脚本混排、G03 增旋转裁剪+雾/遮罩（雾为 `matrixcolor` 叠加，遮罩复用 `mask/alpha_mask`） |
| `host/scripts/run_golden_tests.sh` | **改** | 增 G02/G03 增量用例的 `run_gate g02_inc / g03_inc` 调度（或并入既有 G02/G03） |
| `docs/aegis/plans/2026-08-27-milestone-3-text-advanced-gfx.md` | **新增** | 本文 |

不改：`renpy/gl2/*` SDL 树、`renpy/pygame` SDL Cython 源、`host/renpy-host/src/gpu.rs` 除 `TEXT_SDF` 常量外、`WgslShaderCache` key 熵（除新增 `text_sdf` 独立 key 不混入旧 key）、`SWAPCHAIN_FORMAT`。

---

## Compatibility Boundary（冻结）

* `SWAPCHAIN_FORMAT = Rgba8Unorm` + PMA + `One / OneMinusSrcAlpha` 混合不改；金像捕获仍为 **pre-present game RT**；MAE≤2/255 max≤16 容差文档化于 `host/scripts/phase9_gates.sh` 输出与 `docs/aegis/baseline/`（若建基线）
* `WgpuDraw` 单渲染器；`renpy.host_build` 分支不破 SDL 树；`host_pygame` 公共 API 仅增不减（`import renpy.pygame.joystick` 旧路径仍过，缺口补齐为增量兼容）
* `WgslShaderCache` 以排序 part 集 `sha1[:16]` 为 `cache_key = composed:<hex>`；新增 `text_sdf_pipeline` 为独立 pipeline key，不改变既有 key 熵；旧 key 缓存命中不变；`assert_pipeline_map_honest()` 仍过
* 文本 API：`renpy/wgpu/text.py::render_text_rgba` 签名与返回值 `(w,h,bytes)` 冻结，老存档的 `Text("…")` 渲染路径逐帧字节等价（1× 尺度 MAE≤2，SDF 仅提升放大抗锯齿）；字体授权：自带字体优先，系统字体 `DEFAULT_FONT` 经 `_find_system_font()` 探针，Rust 侧不自带未授权字体文件
* 双树 Import 分流：`renpy.host_build is True` 时走 `host/python/host_pygame` + `renpy_host` FFI，`False` 时走 `renpy/pygame` SDL Cython；`python.rs` 的 `sys.modules["renpy.pygame.*"]` 别名安装不变
* `ldd host/target/{debug,release}/renpy-host | grep -iE 'libSDL'` 为空；`RUST_LOG=info` 必含 `adapter backend=Vulkan`；`phase1_gates.sh` 无 `libSDL` 符号表逃逸
* 字体与 SDF 阈值参数属兼容面：改 `ATLAS_SIZE/SDF_RADIUS` 仅影响 atlas 命中与羽化半径，不改变文本布局坐标系

---

## Tasks

### T1 — 动态字形图集 + SDF 距离场 + Pillow→FreeType/HarfBuzz 桥梁（最重）

**Files:** `renpy/wgpu/text.py`（重构）、`renpy/wgpu/text_atlas.py`（新增 ~400 行）、`renpy/wgpu/text_shaper.py`（新增 ~300 行）、`renpy/wgpu/text_sdf.py`（新增 ~200 行）、`renpy/wgpu/constants.py`（增常量）、`host/renpy-host/src/arena.rs`（增 `text_sdf_pipeline` + `AtlasPool` 聚合）、`host/renpy-host/src/atlas.rs`（可选新增 ~300 行隔离）、`host/renpy-host/src/shader.rs`（增 `TEXT_SDF_WGSL`）、`renpy/wgpu/shaders.py`（注册 `renpy.text_sdf`）、`host/python/renpy_text_ftfont_host.py`（垫片对齐）、`tests/test_text_atlas.py`（新增）、`tests/test_wgpu_composer.py`（增量断言）  
**Why:** `renpy/wgpu/text.py:5` 显式残留 `Full ftfont/atlas integration remains for later`；当前每串一次 `Pillow Image.new(RGBA,w,h) → create_texture_rgba → textured quad` 无字形复用，放大/描边锯齿且每帧大纹理分配触发 `HuangmeiC 教训` 的 atlas thrash；Arabic/Thai 需 HarfBuzz 连写变形。  
**Change Necessity:** 必须新增 GPU 侧 `2048×2048` atlas（单纹理复用，字形槽 LRU 驱逐，cap 4096）+ 8px SDF 阈值抗锯齿 WGSL + HarfBuzz 塑形，否则无法在 Rgba8Unorm+PMA 契约下达成 CJK/多脚本 60fps。Pillow 保留为 fallback，无非代码路径可绕。  
**Impact/Compat:** 1× 尺度像素等价（SDF 与位图 MAE≤2），放大时 SDF 更优；`render_text_rgba` 签名冻结，老存档回放对齐；`_find_system_font()`+`RENPY_HOST_FONT` 字体授权链不变；双树分流：`host_build` 走 atlas，SDl 走原 Pillow。  
**Verification:** `cargo check -p renpy-host` 过（`text_sdf_pipeline` 工厂诚实门） + `cargo test -p renpy-host` 无回归 + `python -m pytest tests/test_wgpu_composer.py -v -k text_sdf`（新注册可合成） + `python -m pytest tests/test_text_atlas.py -v`（LRU/驱逐/恢复 6 用例） + `bash host/scripts/phase9_gates.sh`（G01–G08 零回归）  

**Steps:**
1. `constants.py` 增 `ATLAS_SIZE=2048 ATLAS_MAX_GLYPHS=4096 ATLAS_MAX_ATLASES=2 SDF_RADIUS=8 SDF_THRESHOLD=0.5 SDF_AA=0.02`（`# src: text_atlas.py:new`）。
2. `text_shaper.py`：
   ```python
   # harfbuzz bridge, Pillow fallback
   try:
       import uharfbuzz as hb
       HAS_HB = True
   except ImportError:
       HAS_HB = False
   def shape(text: str, font_path: str, size: int, features=None):
       if HAS_HB:
           # hb.Font(hb.Face(open(font_path,'rb').read())) + hb.shape → glyphs
           ...
       else:
           # fallback: Pillow RAQM per-codepoint bbox → single-glyph list
           ...
   ```
3. `text_sdf.py`：`render_sdf_glyph(bitmap, radius=8)` 用 8 邻域距离变换（`scipy` 无则纯 Python BFS）产 8-bit SDF；同步 `shader.rs` 阈值。
4. `text_atlas.py`：`AtlasManager.alloc(glyph_key)` 首次 `create_texture_rgba(2048,2048)` 单例，`write_atlas_subrect(x,y,w,h,rgba)` 子矩更新；满时 `evict_lru()` 按 `GpuHandleCache` 模式；与 `arena.texture_deferred_destroy` 对齐跨帧 pin。
5. `shader.rs` 增：
   ```rust
   const TEXT_SDF_WGSL: &str = r#"
   @group(0) @binding(0) var t_atlas: texture_2d<f32>;
   @group(0) @binding(1) var s_atlas: sampler;
   @group(0) @binding(2) var<uniform> u: Uniforms; // data0.x = threshold, data0.y = aa
   @fragment
   fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
       let d = textureSample(t_atlas, s_atlas, v.uv).r;
       let thr = clamp(u.data0.x, 0.0, 1.0);
       let aa = max(u.data0.y, 0.002);
       let a = smoothstep(thr - aa, thr + aa, d);
       let c = vec4<f32>(v.color.rgb * a, a * v.color.a);
       let ca = clamp(c.a, 0.0, 1.0);
       return vec4<f32>(c.rgb * ca, ca);
   }"#;
   ```
6. `shaders.py`：`register_wgsl_shader("renpy.text_sdf", tex_count=1, uniform_layout_id="params16", fragment_hooks=[(500," // SDF handled by pipeline")], pipeline="text_sdf_pipeline")` 并入 `_PIPELINE_KEYS`。
7. `text.py` 重构：`render_text_rgba` 改走 `shape→alloc→batch quads`，保留 Pillow `textbbox` 分支作为 `HAS_HB is False` fallback；`RENPY_HOST_FONT` 与 `DEFAULT_FONT` 不变。

---

### T2 — 逆变换/旋转裁剪（GPU scissor 或 stencil 多边形，替代 AABB _clip_rect）

**Files:** `renpy/wgpu/draw_surftree.py`（重构 clip 几何）、`renpy/wgpu/draw_model.py`（增 stencil/scissor 分发）、`renpy/wgpu/draw_walk.py`（透传 clip_poly）、`host/renpy-host/src/arena.rs`（增 `stencil_clip_pipeline` 或复用 `mask_pipeline` + scissor 矩形快路径）、`host/renpy-host/src/shader.rs`（可选 `CLIP_STENCIL_WGSL`）、`renpy/wgpu/shaders.py`（若增 clip part）、`tests/test_clip_inverse.py`（新增）、`renpy/wgpu/constants.py`（增 `CLIP_STENCIL_BITS`）  
**Why:** `draw_surftree.py:82` 残留 `reverse-transformed clips are residual — no half-implement` + `draw.py:334` `reverse-transformed clips are residual`；`_clip_rect` 仅轴对齐四元组，遇 `reverse(zoom/scale) → clip → full child at negative offset`（Host crop+zoom 真实构造）时溢出 8–32px（G02 增量待验）。  
**Change Necessity:** 必须在 Python 侧算逆矩阵多边形（4 点）并在 Rust 侧以 GPU 遮罩执行；scissor 仅覆盖轴对齐快路径，旋转/逆变换必须走 stencil 多边形，否则溢出无法靠 CPU 裁 UV 弥补（UV  remap 仅对全纹理有效，mesh 子区漏光）。最小边界为四文件，不引入通用 CSG。  
**Impact/Compat:** 轴对齐时零开销（`scissor_rect` 快路径，与现 `_clip_uv_frac` 等价）；非轴对齐时一遍 stencil 多边形填充 + 一遍 content（stencil test `equal 1`）；无 API 破坏，`Render.xclipping/yclipping` 语义不变；RTT 局部坐标仍如 `draw_screen.py:430` 不继承 product 绝对裁剪。  
**Verification:** `cargo check -p renpy-host` 过 + `python -m pytest tests/test_clip_inverse.py -v`（6 用例：单位/90°/45°/嵌套/空交集/逆矩阵） + `python -m pytest tests/test_wgpu_composer.py -v` 仍过 + `bash host/scripts/phase9_gates.sh`（G02/G03 增量 MAE≤2）  

**Steps:**
1. `draw_surftree.py:_clip_push_from_node` 增：
   ```python
   def _clip_push_from_node(self, node, ox, oy):
       # existing AABB ...
       rev = getattr(node, "reverse", None)
       if rev is not None and not _is_identity(rev):
           poly = _transform_quad(local_quad, rev, ox, oy)  # 4 points in virtual px
           # intersect with current poly or AABB
           return _intersect_poly(poly)
       # fallback AABB
   ```
   `_is_identity` 容差 1e-6；`_transform_quad` 复用 `draw_walk.ReverseScaler` 逆矩阵。
2. `draw_walk.WalkCtx` 增 `clip_poly: Optional[list[tuple[float,float]]]`；`DrawWalk` 同时写 `clip_rect` 与 `clip_poly`，互斥约定（`clip_poly is not None` 优先）。
3. `draw_model.py:draw_model` 分发：
   ```python
   if self._clip_poly is not None:
       self._draw_with_stencil(clip_poly, mesh, texture, uniforms)
   elif self._clip_rect is not None:
       # existing _clip_uv_frac scissor/UV crop
   ```
   `_draw_with_stencil` 序列：`begin_stencil_pass → draw_clip_polygon(mask) → draw_content(stencil_test=Equal) → end_stencil`。
4. `arena.rs` 增 `stencil_clip_pipeline`（`depth_stencil: Stencil { front: { compare: Always, fail_op: Replace, ... } }`，无色写）或复用既有 `mask_pipeline` 的 dual-RTT 路径作 stencil 替代；scissor 快路径直接 `render_pass.set_scissor_rect(x,y,w,h)`。
5. `tests/test_clip_inverse.py` 6 用例锁几何；goldens 新增 45° 相框裁剪图。

---

### T3 — Gamepad/evdev + TEXTINPUT/IME 完善 + 可选无障碍探针

**Files:** `host/python/host_pygame/joystick.py`（重写 ~478B→~400 行）、`host/python/host_pygame/controller.py`（改 ~200 行）、`host/python/host_pygame/event.py`（改 JOY 分发 + TEXTEDITING 校验）、`host/python/host_pygame/display.py`（增 `Window` IME rect 透传）、`host/python/host_pygame/key.py`（增 `set_text_input_rect`→`host_bridge`）、`host/renpy-host/src/input.rs`（增 GamepadState + poll）、`host/renpy-host/src/event_queue.rs`（增 Joy/Controller 变体）、`host/renpy-host/src/pump.rs`（winit GamepadEvent 桥）、`host/renpy-host/src/python.rs`（暴露 `gamepad_*` FFI + IME FFI）、`renpy/display/core.py`（最小 host_build 分支）、`tests/test_gamepad_ime.py`（新增）、`host/python/gates/input_gate.py`（若需探针）  
**Why:** `host/python/host_pygame/joystick.py:478B` + `controller.py:965B` 为 stub（`get_count()→0`，空设备），`readme` 诚实标注 `joystick / controller / scrap / power | Stub (import OK, empty devices) | 1`；`event.py` 仅有 `KEY/MOUSE/TEXTINPUT` 真通路，缺 `JOY*/CONTROLLER*` 分发；`TEXTEDITING` composition 无长度校验，Wayland 组合输入时 `core.py` compositionLen 溢出。a11y 探针为可选但需插槽。  
**Change Necessity:** 必须重写 joystick/controller 走 `winit::event::WindowEvent::Gamepad*` 或 `gilrs/evdev`（Linux 选 evdev 直读备援 winit），将 `axis ∈ [-1,1]` 归一化、`button` 位掩码、`hat` 离散化注入 `host_pygame.event`；IME 需 `winit Window::set_ime_allowed/set_ime_cursor_area` 桥 + `TEXTEDITING` 长度上限；无非代码路径可绕。  
**Impact/Compat:** `import renpy.pygame.joystick` 旧 import 仍过，`get_count()` 从 0→真值（增量兼容）；`pygame.event.get()` 新增 `JOY*` 类型不破既有 `KEY/MOUSE` 消费者；`TEXTINPUT/TEXTEDITING` 已有消费者（`renpy.display.behavior`）自动获益；`host_build` 分支外 SDL 路径不变。  
**Verification:** `cargo check -p renpy-host` 过 + `python -m pytest tests/test_gamepad_ime.py -v`（轴/按钮/hat/IME 4 用例） + `bash host/scripts/phase1_gates.sh`（`input` gate 10s + `periodic` 60s，观察 `TEXTINPUT` 日志） + 手动/CI gamepad 探针 `RENPY_HOST_GATE=gamepad RENPY_HOST_SMOKE_SECS=5 cargo run -p renpy-host` 输出 `gamepad_count/axis/button` JSON + `python -m pytest tests/test_wgpu_composer.py -v` 仍过  

**Steps:**
1. `input.rs` 增：
   ```rust
   pub struct GamepadState { pub axes: [f32; 6], pub buttons: u16, pub hats: [(i8,i8); 2] }
   pub fn poll_gamepad(&mut self) -> Option<GamepadState> { /* winit GamepadEvent or gilrs */ }
   ```
   Wayland/X11 兼容：优先 `winit` GamepadEvent，回退 `gilrs` crate（features `evdev`）。
2. `event_queue.rs` 增 `HostEvent::JoyAxis{id, axis, value} | JoyButton{id, button, pressed} | JoyHat{id, hat, value} | ControllerAxis/Button`，注入 `host_pygame.event` 队列。
3. `joystick.py` 重写：
   ```python
   import renpy_host
   class Joystick:
       def __init__(self, id): self.id=int(id)
       def init(self): pass
       def get_axis(self, axis): return float(renpy_host.gamepad_axis(self.id, axis))
       def get_button(self, btn): return bool(renpy_host.gamepad_button(self.id, btn))
       def get_hat(self, hat): v=renpy_host.gamepad_hat(self.id, hat); return (int(v[0]),int(v[1]))
       def get_numaxes(self): return 6
   def get_count(): return int(renpy_host.gamepad_count())
   ```
4. `event.py` 增 JOY 分发：`_inject_host_event(ev)` 中 `if type.startswith("JOY"): post(Event(JOY*))`；`TEXTEDITING` 时 `composition = composition[:64]` 截断。
5. `key.py: set_text_input_rect(x,y,w,h)` 存 `_ime_rect` 并 `renpy_host.set_text_input_rect(x,y,w,h)`；`display.py: Window` 透传 `set_ime_cursor_area`。
6. `tests/test_gamepad_ime.py` 4 用例 + `gates/input_gate.py` 输出 `{"gamepad_count":n,"axis":[..],"textinput":"ok"}`。
7. 可选 a11y 探针：`host/python/host_pygame/a11y.py` 桩（`get_screen_reader_active() -> bool`），`input.rs` 预留 `a11y::probe_orca()` AT-SPI2 插槽（defer 真连接，仅探针 JSON）。

---

### T4 — 增量金库与回归（多脚本、CJK 竖排、旋转裁剪、雾/遮罩 G02/G03）

**Files:** `testcases/wgpu_golden/G02*`（增量）、`testcases/wgpu_golden/G03*`（增量）、`host/scripts/run_golden_tests.sh`（调度）、`host/scripts/phase9_gates.sh`（收口）、`host/python/gates/*`（若新增金库探针）、`docs/aegis/baseline/wgpu-golden-baseline.md`（若建基线文档）、`tests/test_wgpu_composer.py`（可选阈值文档化断言）  
**Why:** 现金库 G01–G08 已锁 `MAE≤2/255 max≤16`，但未覆盖 HarfBuzz 塑形（连写）、CJK 竖排（`writing-mode: vertical`）、旋转裁剪（45° stencil）、雾/遮罩叠加（`matrixcolor + mask`）四类 M3 产出；不增量则 T1–T3 等价性无像素锁。  
**Change Necessity:** 必须以 `pre-present game RT` 捕获（ADR §4.3.1）新增 4 子用例，基线由 wgpu 经过视觉 QA 后 bootstrap（`compare_or_bootstrap logs 'baseline written'`），后续 CI fail-closed；无非代码路径可证像素。  
**Impact/Compat:** 新增 `G02_vertical_cjk.png` + `G02_arabic_shaping.png` + `G03_rotated_clip.png` + `G03_fog_mask.png` 4 基线；旧 G01–G08 零改动；`phase9_gates.sh` 新增 4 gate 或并入既有 G02/G03（显式文档）；`MAE≤2/255` 容差文档化于 gate 输出与本计划 Verification。  
**Verification:** `bash host/scripts/phase9_gates.sh` 全过（G01–G08 + 4 增量，`ok=True` 且无 `ok=False`） + `cargo check -p renpy-host` 过 + `python -m pytest tests/test_wgpu_composer.py -v` 过 + `ldd host/target/release/renpy-host | grep -iE 'libSDL' && echo FAIL || echo OK` + `RUST_LOG=info cargo run -p renpy-host -- --benchmark --benchmark-frames 10 2>&1 | grep -q "backend=Vulkan" && echo OK`  

**Steps:**
1. 新增 `testcases/wgpu_golden/` 子用例脚本：`gates/g02_cjk_vertical.py`（竖排 `Text("…", vertical=True)` 或 `renpy.text` 竖排分支）、`g02_arabic.py`（`Arabic شكرا` 连写）、`g03_rot_clip.py`（`Transform(clip + rotate 45°) → Image`）、`g03_fog_mask.py`（`matrixcolor Fog + mask alpha_mask`）。
2. `run_golden_tests.sh` 增：
   ```bash
   run_gate g02_cjk_vertical 20
   run_gate g02_arabic 20
   run_gate g03_rot_clip 20
   run_gate g03_fog_mask 20
   ```
   或并入 `g02/g03` 既有 gate 的多帧捕获（显式注释）。
3. 基线 bootstrap：首轮 `phase9_gates.sh` 写 `testcases/wgpu_golden/baseline/*.png`，次轮 `compare` MAE 校验 `mean≤2/255 && max≤16`。
4. 文档化容差：`docs/aegis/baseline/wgpu-golden-baseline.md` 记录 4 增量基线 + 阈值说明（` lavapipe CI may use separate tolerance tier` 同 ADR §4.3.1）。
5. 收口门禁：`phase9_gates.sh` 输出 `target/gate-*.txt` 全 `ok=True`；`phase1_gates.sh periodic 60s` 仍过（防 T3 引入输入抖）。

---

## Verification（精确命令）

```bash
# 0. 计划自检（落盘后）
ls -l docs/aegis/plans/2026-08-27-milestone-3-text-advanced-gfx.md && wc -l docs/aegis/plans/2026-08-27-milestone-3-text-advanced-gfx.md

# 1. Rust 侧（backend/ldd 断言前置）
cargo check -p renpy-host
cargo test -p renpy-host   # 若有
cargo check --workspace --all-targets   # 全工作区零警告（phase1_gates 首门）

# 2. Python 合成器（WgslShaderCache + text_sdf 诚实门）
python -m pytest tests/test_wgpu_composer.py -v

# 3. M3 新增单测（T1–T3 后补）
python -m pytest tests/test_text_atlas.py -v
python -m pytest tests/test_clip_inverse.py -v
python -m pytest tests/test_gamepad_ime.py -v
python -m pytest tests/ -v   # 全量无回归

# 4. 输入/IME 烟雾（AC7）
bash host/scripts/phase1_gates.sh
# 等价单门（CI 快路径）：
RENPY_HOST_GATE=input RENPY_HOST_SMOKE_SECS=10 cargo run -p renpy-host
RENPY_HOST_GATE=periodic RENPY_HOST_SMOKE_SECS=60 cargo run -p renpy-host
# 观察：TEXTINPUT 日志 + gamepad 探针 JSON

# 5. Gamepad 事件探针（T3）
RENPY_HOST_GATE=gamepad RENPY_HOST_SMOKE_SECS=5 cargo run -p renpy-host 2>&1 | tee /tmp/gamepad_probe.json
cat /tmp/gamepad_probe.json   # 期望 {"gamepad_count": n, "axis":[...], "button":[...]} 无 KeyError

# 6. 金库回归（AC6 + T4 增量，MAE 容差文档化）
bash host/scripts/phase9_gates.sh
# 等价单门：
bash host/scripts/run_golden_tests.sh  # G01–G08 + G02/G03 增量 4 子用例
# 输出：target/gate-g*.txt 均为 ok=True；MAE mean≤2/255 max≤16，goldens 捕获为 pre-present game RT

# 7. 双树不变量（AC2/AC3/§4.3.1）
cargo build -p renpy-host --release
ldd host/target/release/renpy-host | tee /tmp/renpy-host.ldd
ldd host/target/release/renpy-host | grep -iE 'libSDL' && echo FAIL || echo OK
RUST_LOG=info cargo run -p renpy-host -- --benchmark --benchmark-frames 10 2>&1 | grep -q "backend=Vulkan" && echo OK || echo FAIL
# 可选：仅 CI smoke 冒烟 present
RENPY_HOST_PHASE0_SMOKE=1 RENPY_HOST_SMOKE_SECS=2 cargo run -p renpy-host
```

补充断言（文档化于 gate 输出）：
- `MAE≤2/255 mean, max channel delta ≤16` 写于 `target/gate-*.txt` 与 `docs/aegis/baseline/wgpu-golden-baseline.md`
- `ldd` 输出归档为 CI artifact（`host/README.md §5` 要求）
- `RUST_LOG=info` 适配器行 `wgpu adapter backend=Vulkan name="…"` 入 CI 日志

---

## Execution Notes

* **并行与其它 Milestone：** M3 与并行 M1/M2/M4**不跨 Milestone 合并**，独立落盘本文；M1 负责 Host/WgpuDraw 拆分与快读、M2 负责配置/常量收敛、M4 负责 host/python gates 计数，本文仅触文本/裁剪/输入/金库增量，冲突面为 `constants.py` 与 `arena.rs` 时以本文为准并通知 peer。
* **渐进策略：** T1 三档 `HarfBuzz(uharfbuzz)→FreeType+RAQM→Pillow fallback`，首迭代可用 Pillow 渐进（不阻塞 T2/T3）；`uharfbuzz` 引入前 `cargo check` 不应因缺 `harfbuzz-sys` 而失败（feature gate `with-harfbuzz`）。
* **Atlas 驱逐：** 复用 `GpuHandleCache` 计数 cap + `texture_deferred_destroy` 跨帧 pin；Movie 大纹理禁入 `_handle_pixels` / atlas（沿用 AGENTS.md §4.6 规则）；RTT `min(layout, drawable)` 上限不触 atlas。
* **裁剪性能：** AABB 快路径 `set_scissor_rect` 零 stencil 开销；仅非轴对齐时一遍 stencil，预算 1 draw/polygon；pref hover 密集时复用 `BG Cache` 思想缓存 stencil 多边形网格（`_mesh_cache` 模式）。
* **IME 平台：** winit `Window::set_ime_allowed(true)` + `set_ime_cursor_area` 仅在 `RENPY_HOST_BUILD=1` 时调用；Wayland 组合窗口下 `TEXTEDITING` 长度截断防 `core.py` compositionLen 溢出。
* **无障碍：** `a11y` 探针为可选，不阻塞 T1–T4；真 AT-SPI2 接入显式 defer，仅留 `get_screen_reader_active()` 插槽与 `input.rs:probe_orca()` 桩。

---

## Open Questions（→ .omc/plans/open-questions.md）

- [ ] `uharfbuzz` vs `harfbuzz-sys` crate 选型：binder 轻量 vs 系统库一致性，是否需 `pkg-config` 探针？—— 建议 `uharfbuzz` 首迭代，defer 系统库
- [ ] 2K atlas 是否需双缓冲（`ATLAS_MAX_ATLASES=2`）以容纳 CJK 全集瞬时峰值，或单 2K+LRU 已够？—— 首迭代单 2K，压测后定
- [ ] Stencil 多边形 vs 双 RTT mask 复用：BC-160 上 stencil 1 次 clear 开销是否可接受，或沿用 `mask_pipeline` 双纹理路径？—— 首迭代 stencil，RGP 后评估
- [ ] Gamepad 后端：`winit GamepadEvent` 覆盖度（Wayland）是否足，或必须 `gilrs/evdev` 直读？—— 探测先行，defer gilrs 依赖

---

> 下一步：由 coordinator 记录 `TaskStartSnapshot` 后，按 B1(T1)→B2(T2)→B3(T3)→B4(T4) 顺序派子代理；每 Task 独立 `cargo check -p renpy-host + pytest` 后单 commit；任一 Task 触发 `MAE>2` 或 `ldd 逃逸` 即回滚该 Task 并 pause 全 Milestone。

