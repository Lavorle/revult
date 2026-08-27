# Implementation Plan: wgpu 去 slop 重写 + 垫片缺口闭环（P0/P1/P2）

**Date:** 2026-08-27  
**Spec Brief:** 本次调研报告（2026-08-27 revult 详细调研，5 scout 并行体检已完成，见 `BaselineScout/PythonWgpuReview/RustHostReview/PygameShimReview/MetricsScout` 交付） + `host-phase-gap-matrix.md` Phase 0-9 矩阵（approved: 10 行 Phase + 9 行 AC + 5 行 TQ 已定）  
**Scope:** **不做** 新游戏功能/ Live2D 真 SDK 选型/ in-process opus 解码选型/ 版本 bump；**只做** Python `renpy/wgpu` 去 slop 精炼 + `host_pygame` 5 垫片重写 + Rust 宿主精炼 + 统一缓存/常量/测试补齐。范围显式 fence：`host/renpy-host/src/arena.rs|python.rs|shader.rs|gpu.rs` 精炼 + `renpy/wgpu/draw_walk|draw_model|draw_texture|video|composer|shaders|rtt_pool|draw_debug|host_bridge + constants` + `host/python/host_pygame/rect|draw|locals|key|mouse|surface + _renpy_host` + `tests/` 新增；**禁动** SDL 参考树、已锁 WGSL 管线语义、双树隔离契约。
**Parent Plans:** `.omc/plans/consensus-wgpu-native-vulkan-rewrite.md`（AC1-9） + `docs/aegis/plans/2026-08-20-wgpu-host-productization-closeout.md`（productization 6/6） + `host/README.md` §4.9 构建矩阵

---

## Plan Basis

```text
Aegis Visibility:
  改的是 WgpuDraw 的 7-Mixin 聚合（draw_walk/draw_texture/video/composer）与 host_pygame 的 20 垫片（rect/locals/draw 等）以及 arena 的 encode/缓存主路径；
  错改会导致 G01-G08 像素回归、P23 6798 HOT 限阈、ldd SDL 逃逸或 TQ one-ending BAD_ENDING_REACHED 失效；先计划锁定文件边界与 gate 再动代码。

BaselineUsageDraft:
- Required baseline refs: AGENTS.md §0-8（双树铁律/强制 Vulkan Rgba8Unorm/BgCache/HuangmeiC 教训）, host/README.md §2-7（双图构建/ldd/Mechanism 1 Pump）, doc/wgsl_shader_migration.md, .omc/plans/consensus-wgpu-native-vulkan-rewrite.md §4-5, .omc/research/host-phase-gap-matrix.md
- Delivered context refs: 5 scout 体检已交付（PythonWgpuReview 88 except/680 行巨函数、RustHostReview 6.2/10、PygameShimReview 30-95% 完整度、MetricsScout 量化、BaselineScout Phase 矩阵）
- Acknowledged before plan refs: AGENTS.md（已读）, host/README.md（已读 411 行）, consensus-wgpu-native-vulkan-rewrite.md 全文（530 行已读）, host-phase-gap-matrix.md（已读）, docs/aegis/plans/2026-08-26-wave32-a2u.md（已读作格式参照）
- Cited in plan refs: 上述 5 + tests/test_wgpu_composer.py + host/scripts/phase9_gates.sh + renpy/wgpu/draw_walk:39/ draw_model:37/ draw_texture:115/ video:337
- Missing refs: BC-160 RGP 一帧（可选 evidence，非阻塞），HuangmeiC 全量 Prefs hover RGP（可选）
- Decision: continue

Requirement Ready Check:
- Requirement source refs: 2026-08-27 调研报告（P0 3 巨函数 + 5 垫片重写 + 7 dict 统一 + Rust 精炼）+ host-phase-gap-matrix.md §§4-5 缺口清单 14 gaps + ND-1..5
- Goals and scope refs: 去 slop 后可维护性从 4.4→6.5+，P0 静默 bug（rect 恒 True/polygon 空洞）清零，226 except 收敛至 <60，RTT/Mesh/BG 统一，测试补齐 20 垫片缺口
- User / scenario refs: 访谈锁定的 wgpu/Vulkan Linux MVP + the_question Path K one-ending 已通，新增特效不再堆于 _ensure_host_texture_alive/_draw_node_inner_body
- Requirement item refs: 下述 T1-T12 逐条映射调研 Findings（见 Tasks.Why 段）
- Acceptance / verification criteria refs: §6 Acceptance 9 条可勾；phase9_gates G01-G08 MAE≤2/255 max≤16 + cargo test 34 + pytest 5 + ruff 全过 + ldd 空 + backend=Vulkan 日志
- Open blocker questions: 无；Live2D 真 SDK 选型与音频解码选型显式 defer（本计划仅为去 slop 与垫片缺口，不含选型）
- Decision: ready

Change Necessity:
- User-visible need: 新增转场/遮罩/文本特性时，当前 680 行 _draw_node_inner_body、146 行 _ensure_host_texture_alive + 226 裸吞 + rect 恒 True/polygon 空洞 会导致越界 blit 热点误判与越垒补丁
- No-change / non-code option: 不改码仅加文档/配置无法消除恒真 stub 与巨函数圈复杂度
- Why code change is necessary: 必须改源码路径以删重复分支、抽状态机、统一 7 dict、补 120 常量，方能让后续功能线性扩展
- Minimum change boundary: renpy/wgpu 7 Mixin + composer/shaders/rtt_pool/constants + host_pygame 5 垫片 + arena/python.rs 精炼（不含 SDL 树、WGSL 语义、管线工厂新维度）
- Decision: code-change

Existence Check:
- Proposed new surface: renpy/wgpu/constants.py（唯一常量源） + tests/test_rect_draw_locals.py 等补测（测试资产）
- Existing owner / reuse candidate: 无统一常量源（分散于 draw.py/rtt_pool.py/video.py）；无 rect/locals 单测；composition 散于 3 文件
- Why existing surface is insufficient: 魔法数字 2048/4096/1920*1080/30.0/0.15/0.75/0.866 分散 5 文件无法单点改；rect 恒 True 无单测锁不住几何
- Creation proof: 通报前述 Findings P1-4/P2-4（魔法分散）及 PygameShimReview 30%/35% 完整度；新 constants 仅聚合不新增抽象，进位熵↓
- Entropy / retirement impact: 新增 1 常量文件替代 12 处 magic literal，净熵↓；退役触发：无
- Decision: add-with-proof（仅 constants + tests）

Architecture Integrity Lens:
- Invariant: WgpuDraw 单渲染器，Rgba8Unorm+PMA，Backends::VULKAN，Host 分支 host_build，不破 SDL 树
- Canonical owner / contract: renpy/wgpu/shaders.py Snippet IR + composer WgslShaderCache sha1[:16] + arena GpuArena LruSlotMap/RTT/BG；host_pygame/event 为 95% 真实现，其余 5 为重写面
- Responsibility overlap: composer vs composer_fallback 字节级重复、shaders 三处 _PIPELINE_KEYS、7 dict 缓存重复；本计划收敛至单 composer_core + 单 GpuHandleCache
- Higher-level simplification: 可否用 uber 管线收 4 key？否 — 本计划显式 defer A2-M（见 wave32-a2u），仅做去 slop，不增 uber 维度
- Retirement / falsifier: 若重写后 G01-G08 任一 MAE>2 即回滚单 Task；若新增特效仍需改 _ensure_host_texture_alive 则证明 HandleResolver 抽象失败
- Verdict: reuse-existing owners, add constants/tests, edit-in-place 其余

Plan Pressure Test:
- Owner / contract / retirement: shaders._PIPELINE_KEYS 单点 + composer 单实现 + arena 三段 blit 单函数，退休面可 revert；无新 owner 增熵
- Architecture integrity / higher-level path: 已验无更高层 Owner 可替代（HostState.composer 死存为占位，arena 仍用常量工厂，不提前打通）
- Verification scope: cargo check + cargo test + pytest + phase9_gates G01-08 + TQ Path K + ruff clippy 6 门齐全
- Task executability: 每 Task 2-5 min slice，文件边界独立，失败可单 Task revert
- Pressure result: proceed

Plan-Time Complexity Check:
- Target files: renpy/wgpu/draw_walk~720 + draw_model~557 + draw_texture~701 + video~898 + composer~528 + shaders~493 + rtt_pool~185 + host_pygame/*~800 + arena 2697 + python.rs 2376
- Existing size / shape signals: Python 3 巨函数>250 行、7 dict 并存、88 except/文件；Rust 11 处>80 行 2处>200 行，O(n²) 扫描
- Owner fit: draw_* Mixin 已 2026-08 拆分，owner 正确；仅需期内精炼不加新 owner
- Add-in-place risk: 在原文件直接加分支会使 680 行→800 行，触 over-budget
- Better file boundary: HandleResolver/Decoder Protocol/Policy 策略抽为私有 helper，同文件顶部或 constants.py，不新建独立 crate
- Recommendation: extract helper（同文件内） + add owner file（仅 constants.py + tests）

Execution Readiness View:
- Intent Lock: 仅去 slop 与垫片缺口（P0 恒真/空洞 + 巨函数）与统一层；Live2D/音频解码选型显式 defer
- Scope Fence: 上述 Files 表 15 文件可改；禁动 renpy/gl2/SDL 树、WGSL 管线语义、Swaphain format、_PIPELINE_KEYS key 维度、draw 批聚合层
- Baseline Lock: Rgba8Unorm+PMA，tex_count∈{0..3}，params16，MAE≤2/255，ldd 空，backend=Vulkan，host_build 分支不破 SDL
- Approved Behavior: 像素等价（G01-08 0 回归），TQ Bad Ending Path K 仍通（host/target/tq-bad-ending.log BAD_ENDING_REACHED），Prefs hover 不黑屏（HuangmeiC 教训）
- Owner / Contract Constraints: shaders._PIPELINE_KEYS 单点，WgslShaderCache key 熵不变，GpuArena handle 不复用ABA 靠永不复用规避（不改此假设）
- Compatibility Boundary: _PIPELINE_KEYS assert_pipeline_map_honest 仍过；WgslShaderCache 缓存命中不变；host_pygame 公共 API 仅增不减（缺口补齐为增量兼容）
- Retirement Boundary: 无旧路径删除；composer_fallback 收敛为 re-export，保留 import 路径 1 版本兼容
- Task Batches: B1 P0 垫片重写（T1-T2）→ B2 P0 巨函数拆（T3-T5）→ B3 P1 统一层（T6-T8）→ B4 P2/Rust 精炼（T9-T10）→ B5 验证收口（T11-T12）
- Test Obligations: 新增 tests/test_rect_draw_locals.py 等 5 文件；全量 cargo test + pytest + phase9_gates + TQ gate
- Review Gates: cargo clippy -W pedantic + ruff 全过（收窄豁免后）
- Drift / Rewind Rules: 单 Task 可独立 git revert；B1 任意 fail 即全 plan pause，不进入 B2
- Evidence Required Before Completion: phase9_gates 8/8 + TQ K + ldd 空 + backend 日志 + except 计数<60 截图
- Advisory Boundary: method-pack 执行指引；非 GateDecision，仅达标前置

```

### TDD Route

```text
TDD Route:
- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: post-change regression + 新增垫片/缓存单测（非 RED）
- Reason: 去 slop 为等价精炼，无新契约/新分支语义；风险在于巨函数等价性，需金像+单测事后锁而非预写 RED
- Verification: cargo check + cargo test 34 + pytest 5→10 + phase9_gates G01-G08 + TQ Path K + ruff + clippy
```

---

## Files

| 文件 | 动作 | 边界 |
|------|------|------|
| `renpy/wgpu/constants.py` | **新增** | 唯一常量源：`HANDLE_PIXELS_CAP=2048, MESH_CACHE_CAP=4096, RTT_FREELIST_CAP=8, RTT_POOL_MAX_PER_SIZE=16, GOLDEN_FALLBACK=(1920,1080), MAX_TEXTURE_SIZE=(7680,4320), PRESENT_LOCK_TIMEOUT=30.0, FFMPEG_CHUNK_FRAMES=20, FFMPEG_KICKSTART=8, FFMPEG_TIMEOUT_BASE=30.0, FFMPEG_TIMEOUT_PER_FRAME=0.15, AUTO_MIPMAP_THRESH=0.75, PIL_PADDING=4, ISO_BASIS_X/Y=(0.866,0.5)` 等具名，带注释溯源 |
| `host/python/host_pygame/rect.py` | **重写** | 真几何：`Rect(x,y,w,h)` 存 `_x/_y/_w/_h`，实现 `clip/union/inflate/move/colliderect/contains/collidepoint/normalize` 全 SDL-compatible；删 `恒 copy/恒 True` |
| `host/python/host_pygame/draw.py` | **重写** | 补齐 `polygon/ellipse/arc/aaline/aalines`，`polygon` 走 scanline 填充，`ellipse` 用 PIL `ImageDraw.ellipse` + blit，`arc` 走 `pieslice`；保留 `rect/circle/line` 的 bulk 优化（cast('I')） |
| `host/python/host_pygame/locals.py` | **重写** | 从 `renpy/pygame/__init__.py` 全量同步 120+ 常量：`LOCALECHANGED/SYSTEMTHEMECHANGED/FINGER*/PINCH*/CLIPBOARDUPDATE/DROP*/AUDIO*/SENSOR*/PEN*/GL_*/BLEND_*/SRC*/HWSURFACE/WINDOW_*/FULLSCREEN/OPENGL/NOFRAME/RESIZABLE/DOUBLEBUF/SCRAP_TEXT/BYTEORDER` 等；保留 NOEVENT..USEREVENT 段 |
| `host/python/host_pygame/key.py` | **改** | 增 `has_screen_keyboard_support/is_screen_keyboard_shown/get_mods 位运算`，`set_text_input_rect` 存 `_ime_rect` 并转 `host_bridge`；`get_mods` 按 `KMOD_*` 位或 |
| `host/python/host_pygame/mouse.py` | **改** | 增 `ColorCursor` class（`__init__(hotspot, surface)` 存 fields + `__hash__`），`get_pressed` 补 `rel` 累积（`_last_pos` 差值），`set_visible` 同步 `host_bridge` |
| `host/python/host_pygame/surface.py` | **改** | `get_bounding_rect` 全量扫描 fallback（采样仅作快路径，漏检时全扫），`blit` 透传 `special_flags` 至 `_renpy_host`，`get_flags/get_masks` 返真值 |
| `host/python/host_pygame/display.py` | **改** | 增 `Window` 最小桩（`__init__(title,size)` + `destroy`），`get_active/get_num_displays/set_gamma` 返合理值而非恒真；`Info` 返 `current_w/h` 真值 |
| `renpy/wgpu/draw_walk.py` | **重写核心** | 拆 `_draw_node_inner_body(680行)→ _walk_prelude + CachedModelPolicy + DissolveStrategy + ReverseScaler` 三策略类；`ox,oy` 打包 `WalkCtx` dataclass；`_ht_count` 递归预算显式 `budget:int` |
| `renpy/wgpu/draw_texture.py` | **重写核心** | 抽 `HandleResolver { Alive, Remapped, DeadRecover }` 状态机，删 `im.cache` 全遍历耦合，保留 1 次 `remap` + 1 次 `pixels recover` 显式分支；`except` 22→≤4 |
| `renpy/wgpu/video.py` | **重写核心** | 拆 `FfmpegCmdBuilder.build_chunk_cmd()` 单点构造 `-hide_banner -threads 0 -vf scale+fps`，`PipeReader`/`FilePoller` 实现 `Decoder Protocol`，`FrameBag` → `@dataclass(maxlen=live_cap)`；`except` 25→≤6 |
| `renpy/wgpu/composer.py` | **改** | `_try_host_compose_wgsl` 与 `_try_host_get_or_compile` 合并为 `_try_host_inner(fn)` 单实现；`_PIPELINE_KEYS` 删本地 copy，`from .shaders import _PIPELINE_KEYS` 单点引用 |
| `renpy/wgpu/composer_fallback.py` | **改** | `emit_wgsl/_uniform_binding/_cache_key/_normalize` 删本地 copy，`from .composer_core import ...`（若抽 core）或 `from .composer import` 复用；保留文件作 re-export 兼容 1 版本 |
| `renpy/wgpu/shaders.py` | **改** | `_PIPELINE_KEYS/_COMPOSITION_ONLY/_UNIFORM_*` 单点定义，删 composer 侧 copy；`host_pipeline_key` 防御 dead key 前置 `if key in _COMPOSITION_ONLY: return None` 显式化并加注释 |
| `renpy/wgpu/rtt_pool.py` | **改** | 导入 `constants.RTT_*`，`_acquire_rtt` 的 `min(lw,dw)` 与 `draw.py` 重复计算抽 `def _clamp_rtt_size(w,h)` 单点 |
| `renpy/wgpu/draw_debug.py + host_bridge.py` | **改** | `host_env_bool` 单点于 `host_bridge`，`draw_debug` `from .host_bridge import host_env_bool`；`_safe_print/_phase0_due*` 合并为 `host_bridge.log_host(msg, level)` |
| `renpy/wgpu/text.py` | **改** | `DEFAULT_FONT` 换 `fontconfig` 探针（`_find_system_font()` 试 `/usr/share/fonts` + `fc-match`），`_font` 仅留 1 次 `try TypeError` 分支；`PIL_PADDING` 命名常量 |
| `host/renpy-host/src/arena.rs` | **改** | `TextureHandle/MeshHandle/PipelineHandle(u64)` newtype + `From<u64>`，`blit_game_rt_to_swapchain()` 三合一，`HashSet` 替换 `Vec.contains` 去重，顶层 `const` 收 `BG_CACHE_SOFT_CAP/RING_INIT/MAX_RTTS_PER_SIZE/QueryResolve 16` |
| `host/renpy-host/src/python.rs` | **改** | 10 个 `*_pipeline()` 宏化 `define_pipeline_accessor!(solid, textured, ...)`，`host_state().lock().unwrap()` 27 处抽 `with_host_state(|st| ...)` helper |
| `host/renpy-host/src/shader.rs` | **改** | `register_part` 去 `let _=` 吞错，改为 `register_part_checked -> Result<_, ShaderError>` + warn 落盘；去 `#![allow(dead_code)]` 全局压制 |
| `tests/test_rect_draw_locals.py` | **新增** | rect/draw/locals 单测（见 T1） |
| `tests/test_handle_resolver.py` | **新增** | HandleResolver 3 状态单测 |
| `tests/test_rtt_pool.py` | **新增** | RTT freelist/clamp 单测 |
| `tests/test_video_decoder.py` | **新增** | FfmpegCmdBuilder/Decoder 单测 |
| `pyproject.toml` | **改** | 收窄 `host/python` 全文件 ruff 豁免，改为按文件/按行 `noqa: BLE001/TRY*` 显式 |

不改：`renpy/gl2/*` SDL 树、`renpy/display/core.py:host_build` 分支语义、`host/renpy-host/src/gpu.rs` 除 `QueryResolve` 常量外、`shader.rs emit_wgsl` 文本、`WgslShaderCache` key 熵。

---

## Compatibility Boundary（冻结）

* `SWAPCHAIN_FORMAT = Rgba8Unorm` + PMA + `One / OneMinusSrcAlpha` 混合不改；金像捕获仍为 **pre-present game RT**
* `WgpuDraw` 单渲染器；`renpy.host_build` 分支不破 SDL 树；`host_pygame` 公共 API 仅增不减（缺口补齐为增量兼容，旧 import 仍过）
* `WgslShaderCache` 以排序 part 集 `sha1[:16]` 为 `cache_key = composed:<hex>` 不变；`tex_count/layout/has_uniforms` 仍由 Rust SSOT 校验；`assert_pipeline_map_honest()` 仍过
* `ldd host/target/{debug,release}/renpy-host | grep -iE 'libSDL'` 为空；`RUST_LOG=info` 必含 `adapter backend=Vulkan`
* `composer_fallback` 重构为 re-export，`from renpy.wgpu.composer_fallback import emit_wgsl` 仍可用 1 版本（deprecation 警告）

---

## Tasks

### T1 — `rect.py` 真几何重写（P0，最急）

**Files:** `host/python/host_pygame/rect.py`（重写全文件 96→~120 行） + `tests/test_rect_draw_locals.py` 新增  
**Why:** `clip 恒 copy / colliderect 恒 True` 导致 `render.pyx subsurface clip` 越界与热点全命中，为最高风险静默 bug（PygameShimReview 恒真判定）。  
**Change Necessity:** 无非代码路径；几何恒真无法靠配置绕过，最小边界为单文件重写。  
**Impact/Compat:** 补齐 SDL-compatible 几何，旧 `Rect(x,y,w,h)` 构造签名不变；`renpy.display.render` 的 `clip/union` 调用立即正确。  
**Verification:** `pytest tests/test_rect_draw_locals.py -v` 6 用例过；`pytest tests/test_all.py` 仍过。

**Steps:**
1. 创建 `renpy/wgpu/constants.py` 空壳（为 T6 占位，先放 `PIL_PADDING=4`）
2. 重写 `host/python/host_pygame/rect.py`：
```python
from __future__ import annotations
class Rect:
    __slots__ = ("_x","_y","_w","_h")
    def __init__(self, *args):
        if len(args)==1 and isinstance(args[0], Rect): self._x,self._y,self._w,self._h = args[0]._x,args[0]._y,args[0]._w,args[0]._h
        elif len(args)==4: self._x,self._y,self._w,self._h = map(int, args)
        elif len(args)==2: (self._x,self._y),(self._w,self._h) = args  # type: ignore
        else: raise TypeError(f"Rect expects 4 ints or 2 tuples, got {args!r}")
    @property
    def x(self): return self._x
    @property
    def y(self): return self._y
    @property
    def w(self): return self._w
    @property
    def h(self): return self._h
    def copy(self): return Rect(self._x,self._y,self._w,self._h)
    def clip(self, other):
        if isinstance(other, tuple): other = Rect(*other)
        x1 = max(self._x, other._x); y1 = max(self._y, other._y)
        x2 = min(self._x+self._w, other._x+other._w); y2 = min(self._y+self._h, other._y+other._h)
        if x2<=x1 or y2<=y1: return Rect(x1,y1,0,0)
        return Rect(x1,y1,x2-x1,y2-y1)
    def union(self, other):
        if isinstance(other, tuple): other = Rect(*other)
        x1 = min(self._x, other._x); y1 = min(self._y, other._y)
        x2 = max(self._x+self._w, other._x+other._w); y2 = max(self._y+self._h, other._y+other._h)
        return Rect(x1,y1,x2-x1,y2-y1)
    def colliderect(self, other):
        if isinstance(other, tuple): other = Rect(*other)
        return not (self._x+self._w <= other._x or other._x+other._w <= self._x or self._y+self._h <= other._y or other._y+other._h <= self._y)
    def contains(self, other):
        if isinstance(other, tuple): other = Rect(*other)
        return self._x<=other._x and self._y<=other._y and self._x+self._w>=other._x+other._w and self._y+self._h>=other._y+other._h
    def collidepoint(self, x:int,y:int): return self._x<=x<self._x+self._w and self._y<=y<self._y+self._h
    def inflate(self, dx:int,dy:int): return Rect(self._x-dx//2,self._y-dy//2,self._w+dx,self._h+dy)
    def move(self, dx:int,dy:int): return Rect(self._x+dx,self._y+dy,self._w,self._h)
    def normalize(self):
        if self._w<0: self._x+=self._w; self._w=-self._w
        if self._h<0: self._y+=self._h; self._h=-self._h
    def __iter__(self): return iter((self._x,self._y,self._w,self._h))
    def __repr__(self): return f"Rect({self._x},{self._y},{self._w},{self._h})"
    def __eq__(self, other): return isinstance(other, Rect) and tuple(self)==tuple(other)
```
3. 新增 `tests/test_rect_draw_locals.py` 6 用例：`test_clip_intersect/empty`, `test_union`, `test_colliderect_true/false`, `test_contains`
4. 运行 `python -m pytest tests/test_rect_draw_locals.py -v` 预期 6 passed

### T2 — `draw.py + locals.py + key/mouse/display` 垫片缺口补齐（P0）

**Files:** `host/python/host_pygame/draw.py` `locals.py` `key.py` `mouse.py` `display.py` `surface.py`  
**Why:** `draw polygon/ellipse 恒 None` 导致 `Canvas.polygon` 空洞；`locals` 缺 120+ 常量导致 `renpy/display/core.py` 分支失效；`key/mouse` 缺类型致 `isinstance(ColorCursor)` 误判。  
**Verification:** `pytest tests/test_rect_draw_locals.py` 扩展 8 用例；`cargo run -p renpy-host -- --gate hmc_chrome_residual` 仍 `ok=True`；`grep -r "ColorCursor" renpy/display/core.py:984` 路径不抛 `AttributeError`。

**Steps:**
1. `read renpy/pygame/__init__.py` 全量导出表，复制缺失 120 常量至 `locals.py`（`LOCALECHANGED`…`SCRAP_TEXT` 等，见 §3.3 表），末尾加 `# synced from renpy/pygame/__init__.py @ <commit>`
2. `draw.py`：保留 `rect/circle/line` 的 `cast('I')` bulk，增加：
   - `polygon(surface, color, points, width=0)`: `width==0` 走 scanline 填充（按 y 排序边交点），`width>0` 复用 `lines(..., closed=True)`
   - `ellipse(surface, color, rect, width=0)`: `from PIL import ImageDraw; ImageDraw.Draw(Image.frombuffer(...)).ellipse(...)`
   - `arc/aaline/aalines`: `arc` 走 `pieslice`，`aaline` 退化 `line`（注释 `AA fallback`）
3. `key.py`：增 `K_AC_* 5` alias 已有，新增 `has_screen_keyboard_support=lambda: False`, `is_screen_keyboard_shown=lambda: False`, `get_mods()->int` 按 `KMOD_SHIFT|CTRL|ALT` 位或
4. `mouse.py`：增 `class ColorCursor: def __init__(self, hotspot, surf): self.hotspot=hotspot; self.surface=surf`
5. `display.py`：增 `class Window: def __init__(self,title,size): self.title=title; self.size=size; def destroy(self): pass`；`get_active=lambda: True`, `get_num_displays=lambda: 1`
6. `surface.py`：`get_bounding_rect` 改 `采样快路径→若命中透明区则全扫 fallback`，`blit` 签名增 `special_flags=0` 透传

### T3 — `draw_walk.py` 巨函数拆解（P0，最高债务）

**Files:** `renpy/wgpu/draw_walk.py`（720→3 helper + 瘦主函数 ~120 行） `renpy/wgpu/constants.py`（引 `ISO_BASIS` 等）  
**Why:** `_draw_node_inner_body 680行 4层嵌套` 是 Python 层最大债务，后续任一特效必在此堆分支。  
**Verification:** `pytest tests/test_wgpu_draw_split.py` 仍过；`phase9_gates.sh G05-G08` 仍过；`grep -c "except Exception" renpy/wgpu/draw_walk.py` 从 14→≤4。

**Steps:**
1. 文件顶增加 `from dataclasses import dataclass` + `from .constants import ISO_BASIS`
2. 抽 `@dataclass class WalkCtx: ox:int; oy:int; budget:int; clip_rect:tuple|None`
3. 抽 `class CachedModelPolicy: @staticmethod def is_cached(node)->bool` + `class DissolveStrategy: @staticmethod def needs_mid(node)->bool` + `class ReverseScaler: @staticmethod def apply(node, w,h)->tuple`
4. 主函数 `def _draw_node_inner_body(self, node, ctx: WalkCtx)` 仅做 `if CachedModelPolicy.is_cached(node): return self._draw_cached(node, ctx)` 等 3 分支 dispatch，原 680 行体按 `cached/dissolve/reverse` 切为 `_draw_cached/_draw_dissolve/_draw_reverse` 私有方法（每方法≤80 行）
5. 删重复参数 `ox,oy` 透传，改为 `ctx`

### T4 — `draw_texture.py` HandleResolver 状态机（P0）

**Files:** `renpy/wgpu/draw_texture.py`（701行，`_ensure_host_texture_alive 146行` →状态机） `renpy/wgpu/host_texture.py`（增 `AliveState` 枚举） `tests/test_handle_resolver.py` 新增  
**Why:** 6 层 try 嵌套 + `im.cache` 全遍历为典型自救式 slop，错误吞没。  
**Verification:** `pytest tests/test_handle_resolver.py` 3 用例（Alive/Remapped/DeadRecover）过；`hmc_prefs_hover_thrash` 不再触发 `arena clear 黑洞`；`except` 54→≤4。

**Steps:**
1. 新增 `class HandleState(Enum): ALIVE=1; REMAPPED=2; DEAD_RECOVER=3`
2. 改 `_ensure_host_texture_alive(self, ht, w,h)`：
```python
def _ensure_host_texture_alive(self, ht, w,int,h:int):
    # 1. alive probe
    try:
        if renpy_host.mesh_alive(ht.handle): return ht.handle
    except Exception: pass
    # 2. remap
    remapped = self._handle_remap.get(ht.handle)
    if remapped is not None:
        try:
            if renpy_host.mesh_alive(remapped): return remapped
        except Exception: pass
    # 3. dead recover（单次 pixels）
    pix = self._handle_pixels.get(ht.handle)  # type: ignore
    if pix is not None:
        try: return renpy_host.create_texture_rgba(w,h, pix)
        except Exception as e: self._log_once("dead_recover", e); return None
    return None  # 不再遍历 im.cache
```
3. 删 `for cache in im.cache.caches: ...` 全遍历块
4. 单测：`test_alive_hit`, `test_remapped_hit`, `test_dead_recover_create`

### T5 — `video.py` Decoder 策略化（P0）

**Files:** `renpy/wgpu/video.py`（898→ Decoder Protocol） `tests/test_video_decoder.py` `renpy/wgpu/constants.py`（引 FFMPEG_*）  
**Why:** pipe/file 双路径 + 3 处 `-hide_banner -threads 0 -vf` 重复 + `FrameBag` hack + `deque+Queue` 混用。  
**Verification:** `pytest tests/test_video_decoder.py` 4 用例过；`phase9_gates.sh` `video` gate 仍 `ok=True frames=8`。

**Steps:**
1. 抽 `class FfmpegCmdBuilder: @staticmethod def build(w,h,fps, use_file:bool)->list[str]` 单点构造 `["ffmpeg","-hide_banner","-threads","0","-vf",f"scale={w}:{h},fps={fps}"] + (["-f","rawvideo"] if not use_file else [])`
2. 抽 `class Decoder(Protocol): def read_chunk(self)->bytes|None; def publish(self, bag): ...` + `PipeReader`/`FilePoller` 两实现
3. `FrameBag` 改 `@dataclass class FrameBag: frames:deque; live_cap:int; def __init__(self, live_cap): self.frames=deque(maxlen=live_cap)`
4. 主函数 `_stream_ffmpeg_remaining` 仅做 `builder=...; decoder= PipeReader(...) if not use_file else FilePoller(...); while ...: decoder.publish(bag)`

### T6 — `constants.py` 统一常量源 + `composer/shaders` 单点化（P1）

**Files:** `renpy/wgpu/constants.py`（新增，~40 行） `renpy/wgpu/draw.py` `rtt_pool.py` `draw_surftree.py` `renpy/wgpu/composer.py` `composer_fallback.py` `shaders.py` `draw_debug.py` `host_bridge.py` `text.py`  
**Why:** 魔法数字分散 5 文件 + `_PIPELINE_KEYS` 三 copy + `host_env_bool` 双定义。  
**Verification:** `grep -r "1920\|7680\|30\.0\|0\.75\|0\.866" renpy/wgpu --include="*.py" | wc -l` 从 18→≤2（仅 constants 定义）；`assert_pipeline_map_honest()` 仍空。

**Steps:**
1. 填 `constants.py`：
```python
HANDLE_PIXELS_CAP=2048; MESH_CACHE_CAP=4096; RTT_FREELIST_CAP=8; RTT_POOL_MAX_PER_SIZE=16
GOLDEN_FALLBACK_W=1920; GOLDEN_FALLBACK_H=1080; MAX_TEX_W=7680; MAX_TEX_H=4320
PRESENT_LOCK_TIMEOUT=30.0; FFMPEG_CHUNK_FRAMES=20; FFMPEG_KICKSTART_FRAMES=8
FFMPEG_TIMEOUT_BASE=30.0; FFMPEG_TIMEOUT_PER_FRAME=0.15; AUTO_MIPMAP_THRESH=0.75; PIL_PADDING=4; ISO_BASIS=0.866
```
2. 各文件顶 `from .constants import ...` 替换字面量
3. `composer.py`：删本地 `_PIPELINE_KEYS`，改 `from .shaders import _PIPELINE_KEYS, _COMPOSITION_ONLY, _UNIFORM_NONE`；`_try_host_*` 二合一 `def _try_host_inner(self, fn, *a): try: return fn(*a); except (ComposerError,ValueError) as e: self._log_residual(...); except Exception as e: raise AttributeError(...)`
4. `composer_fallback.py`：全量 `from .composer import emit_wgsl, _uniform_binding, _cache_key`；文件保留 `from .composer import *  # re-export compat`
5. `host_bridge.py` 单点 `def host_env_bool(k, d=False): ...`，`draw_debug.py` 改 `from .host_bridge import host_env_bool`

### T7 — 7-dict 缓存统一为 `GpuHandleCache`（P1）

**Files:** `renpy/wgpu/draw.py`（`_handle_pixels/_handle_remap/_rtt_*/_mesh_cache/_mesh_deferred`） `renpy/wgpu/rtt_pool.py`  
**Why:** 7 dict 各自 cap/alive 探测/deferred 驱逐分散，`draw.py:88-130` 7 缓存为最高重复债。  
**Verification:** `tests/test_rtt_pool.py` 4 用例（hit/miss/evict/clamp）过；`hmc_prefs_hover_thrash` `inter_present_gaps_ms p99` 不退化；`pytest tests/test_wgpu_draw_split.py` 仍过。

**Steps:**
1. 新增 `class GpuHandleCache[K,V]: def __init__(self, cap, alive_fn=None): self._map={}; self._cap=cap; ... def get/set/evict_if_needed/deferred_destroy`
2. `draw.py:88-130` 7 dict 合并为 `self._tex_cache = GpuHandleCache(cap=constants.HANDLE_PIXELS_CAP, alive_fn=renpy_host.mesh_alive)` 等 2 实例（纹理/RTT 共用，Mesh 独立）
3. `rtt_pool.py` 每帧 `epoc_pin` 去重改为 `set` 而非 `Vec.contains`

### T8 — `rtt_pool` 与 `text.py` 精炼（P1）

**Files:** `renpy/wgpu/rtt_pool.py` `renpy/wgpu/text.py`  
**Why:** `rtt_pool._acquire_rtt` `min(lw,dw)` 在 draw 与 pool 重复；`text DEFAULT_FONT` 硬编码。  
**Verification:** `pytest tests/test_rtt_pool.py` 仍过；`text.py` 在无 `NotoSans` 容器仍能 `fc-match` 回退并 `render_text_rgba("Hi",32) -> (w>0,h>0,rgba)`。

**Steps:**
1. `rtt_pool.py` 抽 `def _clamp_rtt_size(w,h, lw,dw): return min(w, lw or w), min(h, dw or h)` 单点
2. `text.py` 增加 `def _find_system_font(): for p in ["/usr/share/fonts/...", fc-match ...]: if os.path.exists(p): return p; return None`；`_font` 仅留 `try: ImageFont.truetype(path, size, layout_engine=...) except TypeError: ImageFont.truetype(path,size)`

### T9 — Rust `arena.rs` 精炼（P1）

**Files:** `host/renpy-host/src/arena.rs`（2697 行） `host/renpy-host/src/gpu.rs`  
**Why:** 裸 `u64` 句柄可跨域混用、`blit` 三 copy 重复、O(n²) 扫描。  
**Verification:** `cargo check -p renpy-host` + `cargo test -p renpy-host` 34 过；`cargo clippy -p renpy-host -- -W clippy::pedantic` 0 新增 warn。

**Steps:**
1. 顶层新增：
```rust
#[derive(Copy,Clone,Hash,PartialEq,Eq,Debug)] struct TextureHandle(u64);
#[derive(Copy,Clone,Hash,PartialEq,Eq,Debug)] struct MeshHandle(u64);
#[derive(Copy,Clone,Hash,PartialEq,Eq,Debug)] struct PipelineHandle(u64);
impl From<u64> for TextureHandle { fn from(v:u64)->Self{Self(v)} }
const BG_CACHE_SOFT_CAP: usize = 4096; const RING_INIT: usize = 256; const MAX_RTTS_PER_SIZE: usize = 16; const QUERY_RESOLVE_SIZE: usize = 16;
```
2. `fn blit_game_rt_to_swapchain(&self, encoder:&mut CommandEncoder)` 抽三处 `copy_texture_to_texture`（仅 label 不同），三调用点替换为单函数调用
3. `epoch_pin_*` 的 `Vec<u64>` 去重改 `HashSet<u64>`；`evict_*` 旋转用 `IndexMap` 或 `HashSet` 判重
4. `gpu.rs` 两处 `16` 硬编码改为 `arena::QUERY_RESOLVE_SIZE`

### T10 — Rust `python.rs`/`shader.rs`/`state.rs` 精炼（P2）

**Files:** `host/renpy-host/src/python.rs` `shader.rs` `state.rs` `config.rs`  
**Why:** 10 pipeline 访问器重复、`let _=` 吞错、`composer` 死存、`host_state().lock().unwrap()` 27 处重复。  
**Verification:** `cargo check` + `cargo test` 34 过；`grep -c "host_state().lock().unwrap()" host/renpy-host/src/python.rs` 从 27→≤3。

**Steps:**
1. `python.rs` 宏：
```rust
macro_rules! define_pipeline_accessor { ($name:ident, $field:ident) => { fn $name(&self)->u64 { with_host_state(|st| st.$field) } }; }
define_pipeline_accessor!(solid_pipeline, solid); define_pipeline_accessor!(textured_pipeline, textured); // ... 10 个
fn with_host_state<R>(f: impl FnOnce(&HostState)->R)->R { let st = host_state().lock().expect("host_state poisoned"); f(&st) }
```
2. `shader.rs:197` 改 `pub fn register_part_checked(&mut self, part: ShaderPart)->Result<(), ShaderError> { self.validate(&part)?; self.registry.insert(part.name.clone(), part); Ok(()) }` 并在 `python.rs` 调用处 `if let Err(e)=... { log::warn!("shader part {} rejected: {}", name, e) }`
3. `state.rs:49` `composer` 字段加 `#[allow(dead_code)] // SSA: arena仍用常量工厂，composer路径零使用，择机打通` 注释，或删 dead 并加 `#[deprecated]`
4. 去 `#![allow(dead_code)]` 全局压制，改为按项 `#[allow(dead_code)]`

### T11 — 测补强 + ruff 收窄（P1）

**Files:** `tests/test_rect_draw_locals.py` `tests/test_handle_resolver.py` `tests/test_rtt_pool.py` `tests/test_video_decoder.py` `tests/test_constants_single_source.py` `pyproject.toml`  
**Why:** 20 垫片零单测 + ruff 全文件豁免 10 规则为质量 gate 空洞。  
**Verification:** `python -m pytest tests -k "rect or handle or rtt or video or constants" -v` 12+ 用例过；`python -m ruff check renpy/wgpu host/python` 仍全过（但已收窄）。

**Steps:**
1. 新增 `tests/test_constants_single_source.py`：`assert constants.HANDLE_PIXELS_CAP==2048` 且 `grep -r "2048" renpy/wgpu --include="*.py"` 仅命中 constants 定义（约束散落回潮）
2. `pyproject.toml` 将
```toml
[tool.ruff.lint.per-file-ignores]
"host/python/**/*.py" = ["BLE001","TRY*","B018","F821","F811"]
```
改为仅对 `host/python/gates/**/*.py` 豁免，`host/python/host_pygame/*.py` 保留 `BLE001` 但要求每处 `except Exception:  # noqa: BLE001 -- wgpu host must not abort frame` 显式

### T12 — 全量验证收口（P0 门）

**Why:** 像素零回归 + TQ one-ending + ldd + 计数收敛 5 门齐全方算交付。  
**Steps:**
1. `cargo check -p renpy-host` 通过
2. `cargo test -p renpy-host` 34 预期全过
3. `python -m pytest tests/test_wgpu_composer.py tests/test_rect_draw_locals.py tests/test_handle_resolver.py tests/test_rtt_pool.py tests/test_video_decoder.py -v` 全过
4. `bash host/scripts/phase9_gates.sh` → 8/8 `ok=True`（MAE mean=0 max=0）+ `dissolve/video/live2d/assimp/shader_break` 全过
5. `bash host/scripts/phase1_gates.sh` → `input/periodic` 仍过（本次已复验 `periodic 1190/1200`）
6. `RENPY_HOST_GATE=tq_bad_ending cargo run -p renpy-host 2>&1 | grep BAD_ENDING_REACHED` 仍通（矩阵 P0-2 证据）
7. 量化：`grep -r "except Exception" renpy/wgpu --include="*.py" | wc -l` 预期 **<60**（从 226 收敛），`grep -r "BLE001" renpy --include="*.py" | wc -l` 从 70+→≤15
8. `ldd host/target/debug/renpy-host | grep -iE 'libSDL' || echo ldd-clean` + `RUST_LOG=info cargo run -p renpy-host 2>&1 | grep -q "backend=Vulkan"` 均过
9. `doc/wgsl_shader_migration.md` 内建表补 `renpy.geometry/ftl/alpha` 的 `composition_only` 释义

---

## Risks

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 拆 `draw_walk` 遗漏 `dissolve_mid` 的 `uniforms[0]` 布局 | 中 | 像素回归 | 单测 + `composer_fallback` 字节相等校验（已有 `cargo test shader 14`） |
| `HandleResolver` 删 `im.cache` 遍历后某冷门纹理失联 | 低 | 黑块 | 保留 `_handle_pixels` 单次 recover + `_log_once`，灰度 `hmc_prefs_hover_thrash` |
| `GpuHandleCache` 统一后 cap 误配致 OOM | 低 | X OOM | cap 取 `constants` 单点，`hmc_prefs_hover_thrash` p99 对比 |
| Rust newtype 句柄改动面大 | 低 | 编译期报错全暴露 | `cargo check` 全量 + `clippy pedantic` |
| ruff 收窄后历史 `BLE001` 爆 warn 阻塞 CI | 中 | CI 红 | 按行 `noqa` 显式，已在 T11 列白名单 |

**Rollback：** 单 Task 可 `git revert`；B1 任意 fail 则全 plan pause，不进入 B2；`composer_fallback` 保留 re-export 1 版本兼容，无需立即删。

---

## Retirement

* 旧分散 magic literal：被 `constants.py` 替代后，旧字面量视为 **deleted**，不再维护；若 `grep -r "1920\*1080" renpy/wgpu` 再出现即视为回归（T11 单测捕）。
* 旧 `im.cache` 全遍历路径：T4 后 **deleted**，无回退开关。
* 旧 `composer_fallback` 本地 copy：T6 后 **deleted**（转 re-export），保留文件 1 版本后可删（deprecation 警告）。
* `host python 全文件 ruff 豁免`：T11 后 **retired** 为按行豁免。

---

## Verification（汇总）

* **命令级：**
```bash
cargo check -p renpy-host
cargo test -p renpy-host          # 预期 34 passed
cargo clippy -p renpy-host -- -W clippy::pedantic
python -m pytest tests/test_wgpu_composer.py tests/test_rect_draw_locals.py tests/test_handle_resolver.py tests/test_rtt_pool.py tests/test_video_decoder.py -v
bash host/scripts/phase9_gates.sh  # G01-G08 全过
bash host/scripts/phase1_gates.sh  # input/periodic 均 OK
RENPY_HOST_GATE=tq_bad_ending cargo run -p renpy-host 2>&1 | grep BAD_ENDING_REACHED
grep -r "except Exception" renpy/wgpu --include="*.py" | wc -l  # <60
ldd host/target/debug/renpy-host | grep -iE 'libSDL' || echo ldd-clean
RUST_LOG=info cargo run -p renpy-host 2>&1 | grep "backend=Vulkan"
```
* **Self-review（已做）：** spec 覆盖✓（T1-T12 映射 P0/P1/P2 Findings 全条）、无 TBD✓、签名一致✓（WalkCtx/HandleState/Decoder Protocol）、兼容边界冻结✓、变更必要性已述✓、Existence 已判 add-with-proof✓、复杂度精炼为 extract helper✓、完整度可验证✓（金像+ldd+计数）。

---

## Execution Route

```text
Execution Route:
- Decision: subagent-driven
- Evidence: 12 Tasks 分 5 批次（B1-B5）文件边界独立（rect/draw/locals vs walk/texture/video vs constants/composer vs arena/python.rs），并行度高且需跨 Task 保持 gate 复验
- Fallback: 若 subagent 不可用则 inline 串行 B1→B5，仍按批次 checkpoint
- User confirmation required: no（无新外部依赖/付费/不可逆发布面；SDL 树不删）
```

> 下一步：执行 `T1` 时先由 coordinator 记录 `TaskStartSnapshot`（当前 `master` 进度领先 3 落后 4），按 B1→B5 顺序派子代理；每 Task 独立 `cargo check + pytest` 后单 commit。
