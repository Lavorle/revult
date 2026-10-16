# HuangmeiC 引擎性能优化 — 验收报告（更新至 commit b7aa5a3de）

> 本文档随每轮实测更新。当前 HEAD 见 `git log`。

## 一、原始投诉 → 实测结论

| 投诉 | 起点 | 现在（真机 Wayland + Vega20 实测） |
|---|---|---|
| 「进入设置界面直接只有十几帧」 | 特效窗口帧间隔 p50 ~17–20ms / p90 45–66ms，单帧 draw 22–82ms | 帧间隔 **p50 1.3ms / p90 25ms / p99 55ms**；**4060 帧里仅 6 帧（0.15%）≥20ms**，全是切换后的冷启动帧（走树 700–1600 节点）；其余帧走树 **23 节点 / <5ms** |
| 「标题界面视频更是离谱」 | 转场带 ~9.5Hz 跳步、写帧间隙双峰 | 全程 **30.0 帧/s（满源）**、>60ms 跳档 **0.05%（3/5695）**；产品档 `RENPY_HOST_MOVIE_REDRAW_S=0.03` |
| 「打字机文字界面 14fps」（追加反馈） | 14fps | **~115fps**（draw_frame p50 8.7ms）；每 14s pinned RTT 创建 **376 → 113**、单帧 max 47.8 → **22.8ms** |
| 「按钮按下后特效很卡」（追加反馈） | 被容器重绘丢弃 + 全屏重烘 | 严格容器语义修复 + 快照缓存：走树 **694–833 → 23 节点** |
| 不引入其他 bug | — | 每轮改动 phase9 金库 **20/20 PASS 且 MAE 逐位一致**；12 项 actual.rgba 多次**逐字节一致**；半透明 UI 与旧版 MAE ≤0.44/255 |

## 二、根因与修复（按发现顺序）

1. **环境**：SDK 参考进程自旋 575% CPU × 67min + 8GB swap 污染全部观察 → 清除。
2. **动画节拍**：上游 Movie 自重绘固定 0.1s → 转场带 ~9.5Hz 跳步；改为 env 可控、产品档 0.03（默认仍 0.1）。
3. **容器重绘被吞**：fast re-present 在视频播放期丢弃 `Container` 到期重绘（`ImageButton`/`TextButton` 均属此类）→ 动画只能等视频节拍；默认严格。
4. **烘焙缓存否决过粗**：全局 movie 序号 → **按子树**否决（`display.video` 记录被原地改写的纹理句柄）。
5. **预乘 alpha 二次乘法**（引擎既有缺陷）：RTT 内容已预乘，采样管线再乘一次（50% 白块 140 → 77）；新增直通 alpha 采样路径（`renpy.pma_texture` + 句柄级预乘登记 + `_quad_pipe_for`），复测 **140**。
6. **纯 Python 走树**（对上游 Cython 的结构性差距）→ **稳定子树模型缓存**：结构指纹跨帧稳定即以 pinned RTT 一个四边形代替递归；走树 694–833 → 23 节点。
7. **缓存自身账目**（资格计数/指纹）在点击后爆发 → 跨帧记忆 + 每帧硬预算 + 特化指纹体（账目 p50 3.19→1.42ms、p90 4.20→2.79ms）。
8. **key 白走**：95–96.5% 派生即弃 → 懒派生（thunk 只在廉价路径全落空后调用），白走 **0%**。
9. **churn 候选反复烘焙**（打字机/药丸动画内容每帧变）→ 退避阶梯 + 三次后本次交互退役（pins −70%、单帧 max −52%）。

## 三、提交清单

`6afb27287` · `fd6aa6449` · `a028f2bb7` · `eb0dcf597` · `e603f0388` · `87ff40442` · `6478b777f` · `1f26e8f71` · `daae4d1ff` · `3fa594099` · `e170c677d` · `52837ba8b` · `612b3c646` · `b7aa5a3de`

## 四、验收证据（可复现）

- pytest `tests/`：**171 passed, 1 skipped**（每轮改动前后一致）。
- cargo test `renpy-host`：**103 passed / 0 failed**；`ldd` 无 libSDL（AC2）。
- phase9 金库：**20/20 PASS × 每轮**；G01 0.000243 / G02 0.000086 / G03 0.000000 / G04 0.000328（与基线逐位一致）。
- 真机 headless 逐帧遥测（WALKSTAT/PHASE0）：`/tmp/hmcsv/`、`/tmp/revkey/`、`/tmp/tw/`、`/tmp/smprobe/`。
- 回退/对照开关：`RENPY_HOST_STATIC_MODEL_CACHE=0`（关快照缓存）、`RENPY_HOST_SUBTREE_MOVIE_VETO=0`（回到全局否决）、`RENPY_HOST_REVERSE_BAKE_CACHE=0`、`RENPY_HOST_MOVIE_REDRAW_S`（节拍）、`RENPY_HOST_MAX_REPRESENT_HZ`（re-present 上限）。

## 五、已知残余（如实）

1. **冷启动帧**：每个新页面/新内容的首帧仍要完整走树（Python）→ 20–50ms 一次性顿挫，占全部帧的 0.15%。彻底解决需走树 Cython 化或引擎侧变更见证（Ren'Py render_cache dirty 位）。
2. **预烘焙 dissolve/imagedissolve/blur/matrixcolor 管线**仍对 RTT 输入二次预乘 → `_draw_dissolve_mid` 的半透明双槽合成仍偏暗（G03 金库基线记录的就是该行为；收口需新增 pma 变体管线并重采金库基线，属「会移动基准」的改动，需确认后执行）。
3. 评测口径：Xvfb/llvmpipe 的 present 成本是环境假象，不作为产品结论；产品结论一律取真机（RADV/Wayland、隐藏窗）数据。

## 六、第二轮验收（2026-10-17，用户复测驱动的新症状归因）

用户复测症状：①视频仍卡 ②设置页长驻后点按钮响应延迟乃至卡死。

### 新增根因与修复（按发现顺序）

10. **音频通道雪崩（`783153c70`）**：宿主垫片把一行 UI 音效的 `playing` 永久保持 True（beep 播完无回调）→ `get_channel("audio")` 的 auto_channels 空闲寻找永远失败 → **每个音效新建一条永久通道（实测 14/s，2 分钟 1600+）** → 主线程每帧 O(#channels)（get_playing/queue_depth/RLock FFI）→ 「越站越卡 / 点击打不开 / 卡死」。修法：非视频通道记录 beep 寿命（120ms+settle × 音量），过期即上报 idle → 空闲通道复用；实测通道数 **13–15 封顶**。修复后 45s vs 180s cProfile 切片：热点增长全部 ≤1.07×。
11. **字形合成裁剪窗（`8bf89af92`）**：`FTFont.draw` 原实现每次调用分配**整幅表面** Image + **纯 Python 逐像素** alpha 合成 → 占设置帧 Python 预算 ~40%（~13ms）。改为字形包围盒**整数裁剪窗**光栅化（subpixel 小数偏移相对裁剪原点保持不变 → Pillow 位图逐位一致）+ 窗内合成（同整数式，numpy 向量化位等价，纯 Python 回退只扫窗）。**每帧 Python 预算 31.5ms → 19.3ms（−39%）**；同窗产品帧数 479 → 778（+62%）。
12. **到期 Movie 节拍被 fast path 吞掉（`63e49cb5f`）**：`_try_host_re_present_fast` 保留到期 `_Movie` 直接 re-present 上一帧商品；影片解码/发布只发生在 Movie.render 全量路径 → idle 主菜单内容推进仅 ~10Hz（0.03s 产品档节拍被浪费）→ **「视频一段一段跳」**。修法：到期的 `_Movie` 且视频在播 → `ui_due` 强制全量路径发布新帧（`RENPY_HOST_REPRESENT_MOVIE_DUE=0` 回退）。headless 发布窗 p50 **5 → 16（10fps → 32fps 内容节拍）**。

### 输入取证基建（`fb0e8f07c`）

`RENPY_HOST_INPUT_TRACE=2`：逐事件 `[ITRACE] EVT t=… kind=…`（quit/key/motion 30ms 抑流/btn/wheel）与每个 swapchain present 的 `[ITRACE] PRES t=… kind=（full/lastrt_fresh/lastrt/repres）n_cmds=…`，进程单调时钟锚定 → 点击→画面响应精确配对、事件风暴归因。用户复现日志 22 连真实点击：**click→present 全部 20–37ms（≤3 帧）**，证明历史「响应慢」不是渲染而是通道雪崩的调度膨胀。

### 测量门（入库可复现）

- `host/python/gates/hmc_dyn_gate.py`：26 场景动态门（设置页签/打字机 dwell/hover thrash/视频带）。
- `host/python/gates/hmc_dwell_gate.py`：长驻退化复现门（可选 cProfile 双切片 pstats dump + RSS/通道计数序列）。
- rpyt 探针/回放 overlay：`host/playtests/HuangmeiC/host_overlay/zz_*.rpy`（未入库）。

### 第二轮验收证据（HEAD 63e49cb5f 整包）

- pytest 171 passed / 1 skipped；cargo test 103 passed / 0 failed；ldd 无 SDL。
- phase9 金库 20/20，**MAE 与基线逐位一致**（G01 0.000243 / G02 0.000086 / G03 0 / G04 0.000328 / G05 0.000019 / G02 arabic 0.000173 / G02 cjk vertical 0.000630 / G03 fog 0.000432 / G03 rot 0.000767 / G06 0.000063 / G07 0.000023 / G08 0.000070）。
- 可见窗 RADV 全屏（2560×1440）dyn 门（合成输入）：S0 视频带 present **210 fps**、设置各页 **222–227 fps**、present_iv_p95 **5.4ms**（三笔修复前 S0 154 / 各页 176–190 / p95 7.5ms）。
- 用户真实点击 click→present：20–37ms（≤3 帧）。
- /tmp 清理纪律：测量产物即时删除；**单实例**运行（用户内存护栏）。

### 第二轮已知残余（如实）

1. 冷启动帧（0.15%，20–50ms）仍存在：Cython 化走树或引擎 dirty-bit 见证是独立立项。
2. `_draw_dissolve_mid` 半透明转场仍偏暗（引擎既有缺陷，收口需 pma dissolve 变体 + 金库重采，待用户点头）。
3. `renpy_text_ftfont_host.draw` 裁剪后仍 ~1.3s/15s（Pillow `draw.text` 内部字形光栅 + 着色主导）——** glyph-mask 缓存**可再省 ~4ms/帧，但涉及像素路径，需金库对照后落地。

### 回退开关总表（第二轮新增）

`RENPY_HOST_REPRESENT_MOVIE_DUE=0`（movie-due 放行回退）；首批开关见上表（STATIC_MODEL_CACHE / SUBTREE_MOVIE_VETO / REVERSE_BAKE_CACHE / MOVIE_REDRAW_S / MAX_REPRESENT_HZ / REPRESENT_DROP_CONTAINER 等）。

### 三、同窗口径对照（45–52s settings-idle，hmc_dwell gate，headless）

| 口径 | skin OFF（d443d8c01 修正前的实测） | skin ON（默认形态实测） |
|---|---|---|
| per-frame python 预算 | ~38.4ms（text 链重光栅路径） | **12.54ms** |
| end_frame/15s | 388 | **1202**（≈80fps headless） |
| text.__init__ ct/15s | 6.97s | **3.38s** |
| FTFont.draw ct/15s | 6.40s | **2.58s** |
| GLYPHSKIN 命中率 | n/a | **100%**（cache 稳定 254、~26k stamps/10s） |

含义：**设置页（含打字机预览）的每帧 Python 预算从整页 31.5ms（预热裁剪前）压到同窗 12.54ms（三连优化后）**，戛然而止于「audio 通道 + movie 节拍 + 字形链」三层独立验证。真机可见（RADV 全屏同窗 dyn 门）为 210–227fps present、iv_p95 5.4ms（63e49cb5f 后）。

## 四、收官状态（HEAD bfca3546a，2026-10-17）

全谱终验（全部本机单独实例执行）：
- cargo check ✅ / cargo test **103 passed / 0 failed** ✅
- pytest **177 passed / 1 skipped**（含 6 例 glyph-skin 单元 ON/OFF 逐位）✅
- phase9 金库 **20/20 PASS**（MAE 与基线逐位一致）✅
- `ldd` 无 libSDL（AC2）✅
- 真机 RADV 全屏可见（dyn 门 26 场景）：S0 视频带 present **237.31 fps**、设置各页 **237–252 fps**、iv_p95 ≤4.9ms、movie 内容 32fps 稳定 ✅
- 系统状态：/tmp 1.5G/16G；空闲内存 ~16Gi；无残留引擎实例 ✅

goal 数量级结论（相对报障基线）：
1. 设置界面：~15fps（感知）→ 237–252fps present（同口径合成输入全屏直测）；
2. 标题视频：内容 10fps 步进 → 32fps 发布、present 237；
3. 设置页长驻恶化曲线 → 断根（音频通道 13–15 封顶、无随时间增长项，4–180s slice 对照全部 ≤1.07×）；
4. 设置按钮响应：click→present 全程 20–37ms；通道雪崩消除后不再出现「点不进/卡死」；
5. 引擎退出：--smoke 准时收（看门狗 10s 兜底，69 分钟空转类别消除）。

待用户主观复测：`bash host/scripts/run_huangmeic_playtest.sh --smoke=180`。回退旋钮总表见上文；提交索引 f31e13269 / 783153c70 / 8bf89af92 / 63e49cb5f / 23bbe437c / d443d8c01 / a86301536 / b3540f784 / bfca3546a。
