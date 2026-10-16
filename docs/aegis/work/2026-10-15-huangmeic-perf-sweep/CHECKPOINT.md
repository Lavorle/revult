# 20261015 perf sweep — checkpoint

## Goal
`goal-b0ebba4f`：HuangmeiC 引擎大规模性能优化（设置页/标题视频不动不了 ~15fps → 显著提升），不留新 bug。

## Evidence snapshot (X11/Xvfb 口径, RADV Vega20)
- 用户症状在 X11 环境完整复现：主菜单(视频) S1 product_fps≈9.8–15.6；prefs idle ≈12；present_mode 三档 (mailbox/fifo/immediate) 无差异。
- Wayland 直连桌面同代码：S2 idle/hover 1400+fps(1280)、570–585fps(2560×1440)；视频 cadence p95≤35ms(后段).
- 视频序列（write_texture 间隔 & MOVIE_TIMING_EVENTS）：跳帧全部集中在**呈场转场段**（首 ~1.7–2s，dissolve 双全屏 RTT 重烘链）；此后 30fps 平滑。
- 用户 log.txt（19:40）止于 renderer init，无 FPS 数据；用户选择 Wayland。

## Code fixes this round (uncommitted)
1. arena.rs：`RENPY_HOST_DEBUG_SPARSE` 逐帧 getenv ×2 → OnceLock；rtt_self_draw 三迭代 → retain 合一。
2. draw_texture.py：`load_texture` 快路径 — 指纹先于 bytes 物化，缓存命中零拷贝（省 8.3MB/面/帧）。
3. draw_screen.py：`_dm` 每次 env read → import-time 常量；renpy_host/draw_models/end_frame_present_opts 缓存。
4. draw_walk.py / draw_model.py / draw_screen.py / draw_surftree.py：walkstat 参数解释与 frame 计数全部门控到 `_WALKSTAT_ON`（离线省每 node f-string/Newton tuple）。
5. rtt_pool.py：`_pool_handles` O(1) 集合替代逐 leaf 线性扫描（acquire/release/recycle/free_push/destroy 同步）。

## Validation
- `cargo check -p renpy-host` ✅
- pytest tests/ 171 passed（composer/clip/batching/clock 等）✅
- phase9_gates.sh（Xvfb）：G01–G08 + G02/03 增量 + dissolve/video/live2d/assimp/shader_break 全 OK，ldd 无 SDL ✅

## Pending
- 用户复测（同意）Wayland 官方 playtest 路径当前实际帧数 → 决定是否继续追 present 链/实例批化的重构。
- X11/Xvfb present 60–130ms 是 WSI/SHM 拷贝环境假象，不代表用户 Wayland 路径；不为此改 present 策略。

## 无窗测量操作规程（给下一轮）
- winit 0.30 在 `WAYLAND_DISPLAY` 存在时优先选 Wayland，**必须** `env -u WAYLAND_DISPLAY -u WAYLAND_SOCKET DISPLAY=:99 xvfb-run -a ...` 才能真正进 Xvfb。
- Xvfb 下 Vulkan 可能拿 llvmpipe（无 DRI3）；但实测多数入口拿到 RADV（adapter 日志为准）。
- headless=1（`RENPY_HOST_HEADLESS=1`）用于 CPU/FFI 口径（无 present，帧率虚高）；Xvfb 口径再说。
- 门禁解析仅支持 `host/python/gates/<name>.py`（相对仓库根 `.join("host"/..)`），别用相对路径穿越。
- gate 需完整 bootstrap + `renpy.main.main()`（参照 hmc_settings_fps_1440.py 的 main harness 才能进产品循环）。

## Round 2 (21:0x-22:0x CST)
- subagent 团队交付：python.rs `probe_touch` 批量 FFI（单锁 alive+touch，103 测试绿+探针绿）；draw_texture.py 批量探测接线（TTL/容量双界去重，FFI 缺失回退 legacy）；draw_traversal.py invalidate O(1) 代次+首触折叠（hermetic 35 断言 PASS）。
- 用户报「蓝屏/卡死」→ 根因两个我刚引入的低级错：`_END_OPTS` UnboundLocal（helper 读后写同名全局）+ `draw_walk.py` 缺 `_WALKSTAT_ON` import（draw 路径每帧 NameError 被吞→蓝屏；主循环停止排帧）。已修复；`RENPY_HOST_DRAW_RAISE=1` 复测 0 异常。
- 验证：pytest 171 passed；phase9 goldens 20 OK / 0 FAIL（post-traversal/probe_touch）；X11 after-fix：S2 12→18fps（+50%），S1 9.8→15.3fps；视频转场后 30fps 平滑。
- 待用户 Wayland 复测（--smoke=90 跑 mangohud 读数）。

## Round 3 (22:0x-22:4x CST)
- subagent 三任务全交付：A= ordered-stream 批量提交恢复（draw_screen/host_bridge；mock 彩排 6 项 + goldens PASS）；C= intro 热路径收尾（renpysound memo/_time 上提、core.py fast-path import 折叠 + redraw_queue 空短路 + _draw_schedule_cm cell 锁）；B= 转场带归因（/tmp/hmc_transAttrib.txt）：转场带 draw_ms 29.9% / present 69.1%（llvmpipe 口径），稳态全走 _try_host_re_present_fast（不再进 draw_screen）；RENPY_HOST_HEADLESS 已是死变量。
- 用户环境关键发现：SDK 参考进程 HuangmeiC_Demo_Restored 占 5.8 核 × 67 min + load 11.75 + 8GB swap → 用户与测量侧帧率全部被污染。经用户同意 kill。
- 干净机重测（X11+llvmpipe, RADV 不可用因 DRI3 缺失）：S1 9.8→21 fps；S2 prefs 12→24 fps（约 2x）。全程无异常、无卡死。
- 状态：等待用户 Wayland playtest 复测回报结束本轮。

## Round 4 (22:5x-23:3x CST)
- subagent 双任务交付：RENPY_HOST_HEADLESS 复活（hidden window + skip present，renpy loop 不再外泵 1 帧；g01/g03/g05 headless OK）；PHASE0 计时表（T_re_present_ms present_ms/efp_ms bake_ms bake_count/rp_ms；转场带 efp 45.9 bake 29.4）。
- bake 归因仪表（draw_debug bake_attn + 5 文件打点）：逐帧真值 bake=2（旧/新子位），100% `dissolve_mid|Render|1920x1080`；efp_count=2×bake+1 present。旧估「每帧 16 bake」为窗口聚合误读。
- 用户新反馈：「帧数满天飞但动画抽帧」→ 定位即转场带 present-rate 高但内容 10fps 更新；下一刀=old 位跨帧缓存（省一半 bake），先做内容稳定性测量（agent ff534841）。
- post-A X11 settings: S1 21 fps / S2 24 fps（llvmpipe 口径，稳定无退化）。

## Round 5 (23:1x-00:1x CST)
- old-slot 转场 RTT 内容缓存落地（agent 67df95d8）：key=深度≤3 结构指纹 + movie seq 否决；自留非池 RTT + LRU(4) + 驱逐/resize/kill_textures 清理 + TRANS_BAKE_CACHE 回退阀；hermetic 契约 gate（miss→hit→movie 变 miss→LRU/destroy 全过）；goldens 20/20 PASS（g03 dissolve MAE=0）；headless 实测 cache_hits=590/miss=20=96.7%、bakes 2→1。
- 前置：旧位可行性硬证据（agent ff534841）：指纹 3 depths、95.7-99.7% 帧内恒等、3 处真实结构剪=失效点、movie 需全局 seq 否决（caveat 正确实现）。
- Ada-in-flight：d肆=fa7a4e1 全量验证收尾（7 项 PASS/FAIL 清单到 /tmp/hmc_final_verify.txt）。
- 待用户 Wayland playtest 复测回报（round 4 提请）。目标 objective 保持 active。

## Round 6 (00:0x-01:5x CST)
- 六面 diff 审查（agent 257bb2ae）出 H3/M4 + M1-M4 与 CLEAN 面3/面5；TOP3 已全修（8707bb8a + 5bc23b8f）并 commit fd6aa6449：pinned 非池 RTT FFI（arena rtt_live/freelist 排除）、指纹 depth≤5/512 预算（uncacheable 方向保守）、_grp push/pop finally、pool_handles 补 add、stencil 批 flush。
- 深度预算裁决：b 放宽（>5 带子 → uncacheable），命中 96.5%、bakes=1 主导 96.3%、带内 37→54fps（llvmpipe headless），key 成本 188.5µs（<4% bake）。
- g03/g05 ON==OFF 逐像素一致；hermetic 契约 gate 全 OK；cargo test 103 pass。
- 用户回报「主界面动画仍卡顿」——时点或落在修复中半套树；等修复版 commit 后的复测。目标保持 active。

## Round 7 (01:2x-02:0x CST)
- 真机（Wayland/RADV，隐藏窗+skip present）完整修复版全谱测量：转场带零全量绘制阻塞（draw_frame p50 2.8ms；host_frames 0.5s +500），movie idx >60ms 事件 21/6868=0.3%（顺滑），老位缓存真机命中率 96.4%（cache_hit=211 vs miss=1 per window，uncache=0），efp+walk ~7% 墙钟。
- 用户对当前全修复版本的体感复测仍未见回报；round 6 的「主界面动画还是卡顿」（01:21 前后）出现在 mid-flight 修复树上的可能性大，等 clean 复测。
- 目标 step：等用户复测回报后决定继续往哪层（new 位动画源代次直绘 or 直接收尾）。

## Round 8 (02:0x-02:5x CST)
- 转 场带「动画 ~10fps」调度根因落定并修复：Movie 自重绘节拍 env 化（RENPY_HOST_MOVIE_REDRAW_S，默认 0.1 与上游逐字节等价；0.03 = 产品推荐档）。
  实测三档（headless HMC）：0.1 → 9.03 steps/s（+3 源帧跳步）；0.0167 → 满 30.4 unique idx/s；0.03 → 29.97/s；带外 rp_ms/draw/efp/STALL/循环帧率三档一致（清洁率零退化）；g03/g05 金库三档 PASS。
- playtest 脚本产品档定为 0.03（commit a028f2bb）；引擎 default 保持 0.1 parity。
- 待用户复测回报。

## Round 9 (02:3x-03:0x CST)
- 终版验收CE真机测量（RENPY_HOST_MOVIE_REDRAW_S=0.03 产品档 + headless + 真 RADV/Wayland）：
  movie unique idx **30.0 帧/s 全程**、>60ms 跳档 3/5695=**0.05%**、band efp p50 36.5ms/window（7% 墙钟）、老位缓存命中（LLvmpipe同群 96%）。
- 收尾依赖：用户复测主观确认（mangohud + 体感）。goal 仍 active。

## Round 11 (03:0x-04:0x CST)
- 用户指向两个具体场景：打字机界面 14fps + 设置顶部按钮按下特效卡。静测（/tmp/hmc_typewr.txt）+ 动态真机隐藏归因（/tmp/hmc_dyn_report.txt）锁定：
  per-frame 70ms 主构成 = Transform(zoom 217/1080) 的 1920x1080 全帧 RTT bake + text churn（id 键缓存全 miss）+ cached_model 每帧清 + IdentityMatrix 不短路；动态真机 headless 数据：打字机 dwell 帧仅 8.1ms p50（walk/Python 不慢）→ 可见窗加价来自 visible present 侧（三档 present 对照尚未做）。
- 修复三件（agent f89f4cd7，commit 87ff40442）：F1 Identity-matrixcolor 短路；F2 reverse bake 走 dest 尺寸（Transform zoom bake 1920x1080 → 385x217）；F3 反向 bake 通用内容缓存（rev hit 99.7%，帧内 bake 2→0..1）。cargo/pytest/goldens 全绿。
- 剩：可见窗 present 侧的 ~70ms/帧（Wayland）→ 下一轮做 REAL-VISIBILITY 的 present-mode A/B（mailbox/fifo/immediate/latency），需弹一次窗口（用户跑或我跑）。

## Rounds 1-3 (reset goal; 04:0x-05:0x CST)
- 真机可见窗口全 phase 验收（user Wayland desktop，mailbox present，全部 fix 树 HEAD e603f0388+gate fix）：
  S1 主菜单 present 196/renpy 309 fps；S2 各页（idle/hover/切换 TRANSITION fully）：present 196-207 fps、renpy 395-427 fps、min_product_fps=196——「切页十几帧」已 10x+ 地不存在。
  re-present cap 生效（189/s attempts、walker p95 减半、CPU -62%）；g03/g05 goldens PASS；卡门禁的 S2TRANS format 转义 bug 已修（gate 内）。

## Round 1-2（新 goal，聚焦「切页/特效卡」）
用户实况：真机可见窗 mangohud = 34 FPS / frametime 29.2ms（min24.6 max33.9）/ GPU31% / CPU13% —— 帧时间稳定、资源空 → 说明是「内容更新节拍」而非算力问题。
两处修复（commit 6478b777f）：
1. fast re-present 不再丢弃「视频播放中」的 Container/MultiBox 到期重绘（ImageButton/TextButton=Window->Container，其 hover/press redraw(0) 曾被吞，动画只能等视频节拍）；默认严格，RENPY_HOST_REPRESENT_DROP_CONTAINER=1 回退。
2. 转场/反向烘焙缓存的 movie 否决改为「按子树」：display.video 记录被原地改写像素的纹理句柄（有界集），只有真的采样这些句柄的子树才 uncacheable；RENPY_HOST_SUBTREE_MOVIE_VETO=0 回退旧全局序号否决。
真机 A/B（隐藏窗）：
- 老位烘焙缓存 hit 3444 / miss 5（旧全局否决必 0 命中）。
- 空闲 draws/s 40.6（两组一致）→ 无额外开销/无 CPU 回退；悬停 ~67 draws/s（两组相当，非容器分支主导）。
- 切页特效窗口：严格 17.3 vs 旧 16.9 draws/s；「仅容器重绘」窗口旧行为 0–5 vs 严格 106.5 → 丢弃确实会冻动画。
- 结论：容器丢弃与全局否决都是真缺陷（已修），但特效窗口 ~17-25fps 的剩余瓶颈是**每帧成本**（特效帧内仍有烘焙/walk 开销），下一轮目标。

## Round 2（本轮）：静态子树模型缓存（gated）+ 判据实验
- 逐帧归因（真机 A 组日志）：特效窗口 bakes=0–1（缓存已生效）但**每帧全树走 722–1651 节点 → 22–82ms/帧**，即瓶颈=纯 Python 走树（上游 GL2 走树是 Cython）。已排除隐藏悬崖：renpy/display/render 有 3.14 .so；4 个未编译 .pyx 都是 host shim/已退役模块。
- 实现「稳定子树模型缓存」（子树指纹跨 2 帧稳定 → pinned RTT 复用，跳过递归）：走树 869–1651 → **~131 节点**，特效窗口最高 56–60 fps（ab2.txt）。
- 默认 **OFF**（实现者 PMA 判据：50% 白块经 pinned RTT 由 140 → 77，疑二次预乘；已核实 pinned 与池 RTT 共用 _alloc_rtt，配置相同 → 不是 pinned 特有，需判据实验定性）。
- 已提交 1f26e8f71（gated、默认关、pytest 171 + goldens 20/20 且 MAE 逐位一致）。
- 下一刀：RTT 往返采样 PMA 判据实验（a 直接 / b 池 RTT / c pinned RTT 三路径像素对比）→ 决定能否把该缓存默认打开（把特效帧从 40ms 降到 ~15ms）。
- 另记录（待修）：每帧派生的烘焙 key 有 95–96.5% 未被消费即丢弃（= 白走一遍指纹子树）；反向缓存 key 逐帧不稳定（每帧 pin 一个 RTT 又销毁）。

## Round 3（续）：PMA 往返缺陷定位 + 直通采样 + SM 缓存转正（commit daae4d1ff）
- 定性（三路径判据）：RTT 内容已预乘，采样管线末尾再乘一次 → 直接 140 / 经池或 pinned RTT 77（Δ63）；alpha==1 无损（所以既有产品路径多数不显形，但 `_draw_effect_container` 的 474×80 半透明 bake 本来就在变暗 = 引擎既有缺陷）。
- 修复：句柄级预乘登记表（RTT 池 acquire/reuse/create + pinned 烘焙登记；表面上传不登记；destroy 同步移除）+ `renpy.pma_texture` 直通 finalize part（atomic 不可合并；经既有 create_pipeline_wgsl 建管线；无新 FFI/无新 arena 管线）+ `_quad_pipe_for` 选择器（_draw_texture_at / model-like 叶 / stencil / 单槽 dissolve）。
- 效果：PMA 往返复测 77 → **140**；SM 缓存（`RENPY_HOST_STATIC_MODEL_CACHE` 默认 ON）特效窗口走树节点 p50 **694→119**、帧间隔 p50 **15.8→13.5ms**、nodes_ms 10.0→7.9；UI 类像素 OFF-vs-ON med 0.39–0.44（≤2/255），OFF-vs-OFF 噪声底 0.000。
- 独立复核：pytest 171；phase9 金库 20 OK / Phase 9 PASS，MAE 与基线逐位一致（G01 0.000243 / G02 0.000086 / G03 0 / G04 0.000328）；12 项金库 actual.rgba 逐字节同哈希（agent 记录）；cargo check/test OK；ldd 无 SDL。
- 刻意保留：预烘焙 dissolve/imagedissolve/blur/matrixcolor WGSL 仍对 RTT 再乘 → `_draw_dissolve_mid` 半透明合成仍偏暗（G03 基线即该行为；要收口需 pma_dissolve 变体 + 金库重采基线，另立任务）。

## Round 4：浪费消除（commit 3fa594099）
- 反向 key 抖动根因（REVKEY_TRACE 差异分量）：`body.kids[0].fp.kids[2].fp.kids[0].fp.kids.n prev=0 now=4` —— 深层某节点子数逐帧变（0→4），导致每帧 pin+烘焙+销毁一个 RTT（pins/frame 0.78–1.12、命中≈0）。
- 修复：① key **懒派生**（thunk 只在廉价快路径全部落空后调用，消除 95–96.5% 白走指纹）；② 反向 pin 稳定（`RENPY_HOST_REV_PIN_STABLE`，跨短代次窗口比较 key，churn 候选直接跳过而非烘焙）；③ 遥测 keys_derived/wasted/used/dedup + rev hit/miss/defer。
- 独立验证（我跑）：pytest 171；金库 20/20 PASS 且 MAE 与基线逐位一致（G01 0.000243 / G02 0.000086 / G03 0 / G04 0.000328）。
- 该 agent 自身的 A/B 战役（keys_wasted→0、rev 命中、特效窗口帧间隔）仍在进行，结论到后补记；未据此改动任何默认值。

## Round 5：SM 缓存账目开销定位（进行中）
- agent hmc_fix4 报告（/tmp/revkey/report_hmc_fix4.md）证实：反向 key 逐帧变化是**真实内容变化**（子树里有逐帧重绘/重传的打字机预览 PreviewSayText），剥离 key 会缓存陈旧像素（禁止）；正确修法=churn 候选跳过 pin（已实现并提交）。
  数字（产品默认配置）：特效窗口 keys_derived 418→1.5（−99.6%）、白走 83.2%→0%、rev pins/frame 0.055→0.000、走树节点 p50 142→121.5、全运行间隔 p50/p90/p99 1.99/45.9/120 → 1.48/34.8/95.7ms、>50ms 停顿 216→160.5、交付 walk 行 +32%；12 金库逐字节一致；RT 摘要 ≤0.012/255。
- 我独立复测（CUR1，真机 headless）：keys_derived 2872 / wasted 0 / consulted 2872；rev pins/frame 0.000；走树节点 p50 124。
- **新定位**：慢帧不是绘制走树——nodes>500 的帧仅 0.4%，而 `nodes=112 bakes=0` 的帧要 28–37ms（≈260µs/节点，均值 ~11.8µs/节点）。成本在 **SM 缓存自身每帧账目**：`_sm_count_nodes`（`_SM_COUNT_FRAME_BUDGET=20000`/帧）与 `_sm_fp_render`（8192/帧）在点击后的候选扫描帧集中爆发（0.5s 窗口 fp_nodes 22–24k、cnt_nodes 11–22k）。
- 已派 agent 优化该账目（跨帧记忆资格计数、降低每帧预算并摊分、指纹复用、backoff 免费化），要求账目 p90 ≤2ms 且金库逐字节不变。

## Round 5 收尾：SM 账目成本削减（commit e170c677d）
- 改动：资格计数按稳定 slot 跨帧记忆 + 每帧预算大幅下调并轮转发现候选（单帧账目成本有硬上限）+ 结构未变时指纹复用 + churn 候选 backoff 免费。
- 我独立验证：pytest 171；金库 20/20 PASS 且 MAE 与基线逐位一致（G01 0.000243 / G02 0.000086 / G03 0 / G04 0.000328）。
- 作者 agent 的前后 A/B（每帧账目 p50/p90/max、特效窗口帧间隔、RT 摘要一致性）仍在跑，结论到后补记；本次未新增任何默认行为开关。

## Round 6：账目优化回归的发现与修复（52837ba8b / 612b3c646）
- ⚠️ 教训：我上一轮提交的 `e170c677d` 里含 agent 引入的**静默 NameError**（`_sm_key` 漏传 node）→ SM 缓存被完全关闭（走树 134→874–1651）。**pytest 与金库都抓不到**（金库不覆盖该缓存命中路径）。
- 本轮我通过「SM 是否真的命中」判据抓到：`sm_hit=0 sm_fp_nodes=0 sm_bytes=0`；随后 agent 自行修复并提交 `52837ba8b`（2 行）+ `612b3c646`（回退旋钮注释）。
- 修复后复测（真机 headless）：`sm_hit 14–17 / nodes_saved 24–29k / sm_bytes 24MB`、走树 **nodes p50 = 23**、`nodes_ms p50 1.1 / p90 7.2 / max 35.9`、账目 `sm_ms p50 0.0 / p90 1.53`。
- 干净口径（无 RT 回读探针）特效测量：帧间隔 p50 **1.3ms** / p90 **25ms** / p99 **55ms**，>50ms 仅 **46/4060（1.1%）**。
- 账目优化效果（agent A/B，3+3 轮）：账目 p50 3.19→**1.42ms**（−55%）、p90 4.20→**2.79ms**（−34%）；`sm_cnt_ms` p90 0.49→0.06（跨帧负面记忆 + 内联遍历）；指纹 2.14→1.67µs/节点（特化体 + 自校验 0 不一致 + 每帧 3072 节点硬上限）。
- 未达成项（如实）：账目 p90 ≤2ms；实测下限（纯遍历 0.82µs/节点=1.40ms；最小 5 字段体 1.20µs/节点）说明要到 2ms 需引擎侧变更见证或 Cython 化；agent 拒绝用「强压账目预算」造指标（会把大候选饿死、单帧更慢）。

## Round 7：churn 候选退役（commit b7aa5a3de）
- 现象：打字机页（`PreviewSayText` 每帧重绘）每 14s 创建 **376 个 pinned RTT**（含 1849×846≈6.2MB 大块）→ 反复烘焙/分配/销毁。
- 修法：churn 阶梯升级 —— 首轮 45 帧退避 → 复发 600 帧 → 3 次后**本次交互内永久退役**（行走便宜，重烘昂贵）。
- 实测（同 14s、吞吐不变 1253→1252）：pins **376→113（−70%）**、`nodes_ms` max **46.5→21.6ms**、`draw_frame_ms` p90 **11.13→9.54ms** / max **47.76→22.84ms（−52%）**。
- 验证：pytest 171；金库 20/20 PASS 且 MAE 与基线逐位一致（G01 0.000243 / G02 0.000086 / G03 0 / G04 0.000328）。
- 打字机页现状：`nodes p50 23`、`draw_frame p50 8.7ms`（≈115fps），对应用户最初报的「14fps」已解决。

## Round（阻塞段）：/tmp EDQUOT 持续 + 内存护栏（用户指令）
- 用户提示：**同机并发多实例会内存爆炸**（ reloadData：MemFree 撑爆过 8GB swap）；此后测量一律**单实例**、绝不并行开多个 renpy-host / 用大 subagent 组实测。
- 用户已清理 /tmp 大目录（实测验证不存在了），但 harness 写 /tmp 仍 `error -122`（EDQUOT）：bash 无法 spawn（临时脚本写不进）、glob/grep 的 ripgrep 起不来；**只有工作区（btrfs）文件 read/write/edit 正常**。
- 判定（shared-diagnosis）：/tmp 是 tmpfs usrquota，已删除但仍被某进程持有的文件继续占额；或 quota 记账未随 unlink 落账。待用户终端跑 `sudo lsof -w +L1 /tmp` / `pgrep -af 'renpy-host|huangmeic|ffmpeg|xvfb'` 定 holder 后 kill 该进程。**不要**再往 /tmp 写任何东西。
- uncommitted walker 优化（draw_model/draw_walk/draw_surftree，cProfile 冷帧 135.95→92.86ms、g01 MAE 0.000243 / g03 0.000000 同基线、快门 20 门未跑全）：冻结在变更树里，等 /tmp 恢复后按顺序：`git checkout -- testcases`（清误产金库 actuals）→ `cargo check -p renpy-host` → `pytest tests/ -q` → `phase9_gates.sh` 全 20 门 MAE 逐位一致 → 单实例效果/打字机 A/B → 再提交。
## Round：/tmp 解封 + walker 优化验证提交（commit f31e13269）
- /tmp 根因改判：非「已删除打开文件」，而是 `usrquota`（无值）tmpfs 的**每用户默认限额 = size/2 (8G)**；13G 存量里含 3 个多 GB 的 `renpy_host_movie_*.rgba`（都删）与旧测量目录。现 16G 池 / 1.5G 占用。
- 残留进程核查干净（无 renpy-host/HuangmeiC/ffmpeg/Xvfb；drkonqi-c 崩溃处理器 8 个可选杀）。用户指令固化：**测量一律单实例串行**，防内存爆炸。
- 解封后验证链全绿：`git checkout -- testcases`（误产金库 actual 已清）→ cargo check OK → pytest **171 passed/1 skipped** → phase9 金库 **20/20 PASS 且 MAE 与基线逐位一致**（G01 0.000243 / G02 0.000086 / G03 0.000000 / G04 0.000328 / G05 0.000019，G02 arabic 0.000173 / G02 cjk vertical 0.000630 / G03 fog 0.000432 / G03 rot 0.000767）→ ldd 无 SDL。
- 真机单实例 A/B（RADV Vega20 headless，hmc_dyn_gate，26 场景，产品档 0.03）：walker ON vs OFF —— present_fps_avg 平均 **+6.6%**（max +13.6%，最差 −1.5% 噪声）、renpy_fps_p50 +3.2%、present_iv_p50 **−4.8%**；走树 nodes p50 均为 52（稳态 SM 缓存主导，walker 收益体现在单节点派发成本）。报告存 `logs/dyn_report_pos137_{A_base,B_walker}.txt`。
- **已提交 f31e13269**（draw_model/draw_walk/draw_surftree + 可复现测量门 hmc_dyn_gate.py）。cProfile 冷帧 135.95→92.86ms（−32%）。
- 剩余待办：①用户 Wayland 可见窗 mangohud 主观复测确认；②可选（需用户点头，移动金库基线）：pma_dissolve 变体收口 `_draw_dissolve_mid` 半透明变暗；③冷启动帧（0.15%，700–1600 节点）Cython 化/引擎 dirty-bit 见证。

## Round：用户复测归因 → 音频通道雪崩修复 + 输入取证探针（fb0e8f07c / 783153c70）
- 用户复测：视频仍卡；设置按钮响应延迟 / 长驻后点不动甚至卡死。
- 证据 A——合成输入可见窗（HEAD 全谱）三连测（720p 窗 / `--fullscreen` 2560x1440）：present 154–190fps、iv_max ≤16ms → 调度侧健康。
- 证据 B——输入取证（fb0e8f07c：`RENPY_HOST_INPUT_TRACE=2` 逐事件/逐 present `t=` 行，PRES 挂点 full/lastrt_fresh/lastrt/repres 四路径）：用户回复日志 22 次真实点击 click→present 全部 20–37ms（≤3 帧）——**渲染不是响应慢**。
- 证据 C——用户 21.8MB 复现日志时间窗：**nodes 恒 115、nodes_ms p50 3.7→15.6ms 线性爬升**（每 100s +4.7ms）、pres/s 122→23 = 随时间累积的开销。
- 根因（cProfile slice A 45–60s vs 330–345s）：`audio.py get_context/Channel.periodic/renpysound playing_name` 4–6× 递增；`get_channel("audio")` auto_channels 空闲查询链永远找不到空闲 → **每个 UI 音效新建一条永久通道（14/s，2 分钟 1600+）** → 主线程每帧 O(#channels)（get_playing/queue_depth/RLock FFI 全链）→ 越站越卡、点击后被迫排队、最终卡死。
- 修复（783153c70）：renpysound_host 记录非视频 beep 寿命（120ms+settle × 音量），过期即 `queue_depth/playing_name` 报 idle → 空闲通道复用。实测通道数 13–15 封顶（此前 135s 涨到 1629）；修复后 45s vs 180s 切片热点增长全部 ≤1.07×。
- 验证：pytest 171/1skip；金库 20/20 且 MAE 与基线逐位一致；ldd 干净。测量门 `hmc_dwell_gate.py` 一并入库。
- 下一热点（已量出、单独立项）：`renpy_text_ftfont_host.draw` 占设置帧 Python 预算 ~40%（Pillow 字形光栅化 ~13ms/帧）→ 字形缓存是下一刀。
- 操作纪律（用户指令固化）：每轮跑完清理 /tmp 测量产物；单实例；控制内存，避免 dsh 也被拖崩。

## Round：字形合成裁剪窗优化（commit 8bf89af92）
- 锁定热点：`renpy_text_ftfont_host.draw` 占设置帧 Python 预算 ~40%（31.5ms/帧总预算中 ~13ms 为该函数自耗时；全表面 Image.new + 纯 Python 逐像素合成整幅）。
- 改法：改为**字形包围盒整数裁剪窗**内光栅化（subpixel 小数偏移相对裁剪原点保持不变 → Pillow 位图逐位一致），合成只作用于窗内像素、同一整数式，numpy 可用则向量化（位等价），不可用回退纯 Python 只扫窗。outline 环 / underline / strikethrough 全保留。
- 量化（hmc_dwell slice A 15s，修复后 vs 修复前）：每帧 Python 预算 **31.5ms → 19.3ms（−39%）**；`FTFont.draw` 自耗时 6.04s → 1.32s（4.6×）；同窗产品帧数 479 → 778（+62%）。
- 验证：pytest 171/1skip；金库 20/20 且 **MAE 与基线逐位一致**（含 G02 0.000086 / G02 cjk vertical 0.000630 / G02 arabic 0.000173 等）→ 位等价成立；ldd 干净。
- 设置页帧预算已从 ⩾31.5ms（≈31fps 上限）降到 ~19.3ms（≈52fps 上限），再叠加 SM 缓存/走廊摊薄后交互帧应显著流畅。
- 剩余待用户复测：设置页长驻 + 页签切换（上一轮的音频通道雪崩修复 783153c70）+ 新一轮字形裁剪（8bf89af92）后的整体体感。

## Round：视频内容节拍定位 + movie-due 放行修复（commit 63e49cb5f）
- 用户复测反馈（不全量覆盖）：视频仍卡。从用户 21.8MB 日志量出 `movie_redraw band_frames`（0.5s 窗口内 `-MOVIE_PUBLISH_SEQ` 增量）：p50=5（**内容 10fps**）、p95=14（转场带 28fps 是快段）——idle 主菜单带内容推进只有 10Hz。
- 根因：`_try_host_re_present_fast` 的 redraw 队列遍历把到期的 `_Movie` 规格**保留在队列里并直接 re-present**（重新呈现上一帧商品）；Movie 解码→发布只发生在 `Movie.render` 全量路径里 ⇒ 影片到期节拍被 fast path 吞掉，只有偶发的其它 ui_due 才顺带推进影片内容 —— 和 present 计数器 200fps 反差的「视频一段一段跳」。
- 修复（63e49cb5f）：队列遍历中「到期 _Movie 且 videos 在播」→ `ui_due=True`（强制走全量路径发布新帧）；回退旋钮 `RENPY_HOST_REPRESENT_MOVIE_DUE=0`。prefs 无视频通道时行为不变（movies_playing=False）。
- 量化：headless 发布窗 p50 5→16（内容 10fps → **32fps**，产品档 0.03s 节拍兑现）；可见窗 2560x1440 dyn 门同为 15/0.5s → 30fps；全屏可见门 present_fps：S0 band 154→**210**、设置各页 176-190→**222-227**、iv p95 7.5→**5.4ms**（叠加 783153c70 + 8bf89af92 的复合增益）。
- 验证：pytest 171/1skip；金库 20/20 且 MAE 与基线逐位一致；ldd 干净。/tmp 清理（1.5G/16G）。
- 待用户最终复测：菜单视频带、设置页切换与长驻、打字机页。如「视频还卡图」再报则用取档（INPUT_TRACE=2 + PHASE0）继续拆。

## Round：81MB 长跑取证（旧栈）+ should_exit 看门狗（commit 23bbe437c）
- 用户把原来那条取证命令跑了 ~69 分钟（应 4 分钟）：content/s 从 101 → 5.8 单调衰减（p50 缺口 7ms→172ms）→ **与音频通道雪崩同源的老栈长驻退化**（该运行发生在 783153c70/8bf89af92/63e49cb5f 之前，`re_present_block ui_due type=Movie` = 0 证实）。可作为修复收益的「自然对照」：修复栈的对照实验速查以本轮 hmc_dwell 台账为准。
- 顺带挖出 **真 bug：`smoke deadline` 触发 `should_exit` 后从未 unwind**（4 分钟的 smoke 空转 69 分钟烧 CPU/显存；`event loop exit` 与 `QuitException` 记录 0 条）。修法：宿主层看门狗——`should_exit` 锁存后 10s 内产品仍活着 → dump input-trace + `process::exit(0)`（只攻击「概率性卡死退出」的路径；正常 SIGTERM/窗口 X 路径不动）。
- 验证：cargo test 103 pass；phase9 金库 20/20 + ldd 干净。

## Round：字形皮肤缓存（commit c2892491e）
- `renpy_text_ftfont_host.draw` 裁剪进窗后剩余 ~6.4ms/帧全在 Pillow `draw.text` 内部 C 光栅（同字体×字×填色×outline×subpixel 相位的三四万次/切片）；与 text_config 打字机/UI hover 逐帧重绘耦合。
- **字形皮肤缓存**（`RENPY_HOST_TEXT_GLYPH_SKIN`，默认 **ON**；`=0` 回退）：key=(font-face 稳定身份, ch, fill, outline_fill, r, 量化 subpixel 相位 /256) —— 皮肤一次光栅化，ring 在皮肤内部先画（z 序与 live 相同），下划线/删除线仍逐帧现画；`Image.paste` 与 `draw.text` 的 draw_bitmap 同为逐通道线性混合；cap 2048 清空。
- **位等价门禁**：SKIN=1 vs OFF 基线金库 **所有 MAE 逐位一致**（G02 0.000086 / G02 cjk vertical 0.000630 / G02 arabic 0.000173 / G03 0 / G04 0.000328 / G05 0.000019...）。
- **收益**（hmc_dwell slice A 15s）：每帧 Python 预算 19.3 → **12.0 ms（再 −38%）**；同窗产品帧数 778→1250（+61%）；`FTFont.draw` ct 6.29→2.67s。
- 累计三级（表象 31.5ms→19.3→12.0ms/帧，全谱逐段验证）。
- 验证：pytest 171/1skip；金库 20/20 两向（SKIN=0 与 =1）。
- 待命：字下划线包内相位键行为、vertical 字体路径已含；下一步若要再压：`draw_screen` 链管束（ct 14.78/635帧＝walk 管径）已是下一刀。

## Round：skin 键阶段网格 /64 精修 + 命中率遥测（commit 0b397c7a7）
- 依据 Pillow 10.4 `ImageDraw.text`：frac 完整传入 `getmask2 start` → freetype 子像素精度为 26.6 (/64)；/256 键对同一光栅结果制造 4× 无意义应分 key → 键改为 /64。金库 A/B（SKIN=1）**依旧所有 MAE 逐位相等**。
- 新增 PHASE0 门控 `[GLYPHSKIN] t=.. stamp/hit/miss/live cache=..` 10 秒频闪计数器；hmc_dwell settings-idle 实测：**cache 稳定 254、~26k stamp/10s、hit=100%（miss=0）** —— 无任何 re-raster churn。
- 表现基线：每帧 Python 预算 12.0ms（skin）/ 19.3ms（仅裁剪）/ 31.5ms（裁剪前）；全谱三级独立验证。

## Round：默认值更正（commit d443d8c01）
- fresh profile 抓到 text 预算 ~38ms/帧的旧路径在跑 → skin 代码里的 env **默认其实还是 0**（c2892491e 提交信息误写 Default ON）→ 助手已把默认翻 ON（=0 仍可回退 A/B）。pytest 171 / 金库 20/20 且 MAE 逐位一致（此时运行默认即 ON 等价变体）。
- 教训：**每笔提交的默认行为开关要跟代码实际值对账**；新的 fresh profile（切到 45–52s 窗）显示 settings text 链预算结构为 text.py render/init 聚合 ~18ms + FTFont.draw ~16ms（关 skin 时），与「整页 12ms」的口径按窗不同 —— **后续汇报统一以 45–52s settings-idle 窗为准**。
- 挂起：等用户 HEAD 复测（当前栈 d443d8c01）。

## Round：skin 缓存正确性实锤 + 单元护栏（commit a86301536）
- 新增 `tests/test_ftfont_skin.py` 6 例，立刻抓出**skin piepaste 与 PIL `draw_bitmap` 在负相位/贴边处的双向差**：PIL `ImageDraw.text` `start=math.modf(xy)` 对负 xy 给出**带符号 fraction** (modf(-7.3)=-0.3)，正向 pad 表达式（modf(3.7)=0.7）不能复现 → freetype 光栅 ±1px（97 LSB）——金库没炸是因为产品字形恰好都在正相位窗。
- 修正原则：**不可复现的一律回退 live `draw.text`**（永远与 pre-cache 行为逐位一致）：①相位 key = 精确 fraction（不再任何量化桶）；②fx/fy 任一 <0 → live；③paste 锚点接近/越过表面边界 → live。
- 新验证：pytest **177**（171 + 6 例，含 ON/OFF 逐位、重复命中纯 hit、上限 2048 边界、无字体回退）；phase9 金库 20/20 与基线 MAE 逐位一致。
- 性能锚点重审：settings-idle 稳态窗 100% 真 hit、cache 254 条稳定（安全修正后 product 帧不以负相位为主，成本口径 12.5ms/帧不变；负相位为主（拥挤页首帧等）自动退化为 live 老成本，无回归）。
