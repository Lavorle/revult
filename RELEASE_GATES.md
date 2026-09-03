# RELEASE_GATES — 发布门禁（冻结清单）

本文件是 host 发行的**唯一**门禁定义。固定 7 项，只减不增。
新回归只允许进现有项的子 case；**禁止新增 `host/python/gates/*` 与
`tests/test_*` 文件**（确需新文件须先修订本文件并说明理由）。

通用环境：`WGPU_BACKEND` 保持未设置（宿主强制 Vulkan）；
`RENPY_HOST_BASE` 为仓库根；release 二进制为 `host/target/release/renpy-host`。

## G1 构建

```bash
cargo build -p renpy-host --release
```

通过：exit 0。

## G2 合成器与句柄单测

```bash
python -m pytest tests/test_wgpu_composer.py tests/test_handle_resolver.py -v
```

通过：全部 passed（含 `assert_pipeline_map_honest` 与 texture_alive 共存契约）。

## G3 金库 G01–G08

```bash
bash host/scripts/phase9_gates.sh
```

通过：尾行 `Phase 9 EXIT: PASS`。像素容差：MAE ≤ 2/255，单通道最大差 ≤ 16。
捕获点为 present 前游戏 RT（非交换链），见 ADR §4.3.1。

## G4 the_question 全程（smoke 代理）

```bash
bash host/scripts/run_the_question.sh --smoke 60
```

通过：exit 0；日志含 `backend=Vulkan`；无 Python traceback、无 `no suitable Vulkan adapter`。

## G5 HuangmeiC 菜单视频 soak

```bash
bash host/scripts/run_hmc_menu_video_soak_probe.sh  # 连续跑 2 次
```

通过：脚本 exit 0，且两次运行在 `host/target/soak-history/` 各留一对
`soak-<stamp>.{txt,json}` 归档，两对归档 JSON 均为 `ok/measured/pass=True`；
无 ≥2s stall、无 hang/crash。
（2026-09-03 修订理由：原“看文件尾行两次”不可观测——artifact 每轮开头被截断，
一次启动即退（run6）留下空文件而脚本仍 exit 0；探针脚本已加 fail-closed
裁决与逐轮归档，本条只是把观测方法写对，不增不减门禁项。
交互式全程 playtest 按版本发布前另行组织，不在本清单内加项。）

## G6 零 SDL 链接

```bash
ldd host/target/release/renpy-host | grep -iE 'libSDL' && echo FAIL || echo OK
```

通过：输出 `OK`（无 `libSDL*` 行）。

## G7 Vulkan 后端断言

```bash
RUST_LOG=info timeout -s KILL 60 ./host/target/debug/renpy-host the_question &>/tmp/g7.log; grep -q 'backend=Vulkan' /tmp/g7.log && echo OK
```

通过：60s 内输出 `OK`（适配器行 `wgpu adapter backend=Vulkan`）。
（2026-09-03 修订理由：原管道写法 shell 会等待 `cargo run` 的常驻游戏进程，
`grep -q` 命中后命令仍永不返回（实测 25s/40s 均需外部 kill，exit 124）；
语义不变——仍是同一适配器行断言，改用已构建的 debug 二进制直跑以便 kill，
`cargo run` 版等价但更慢。不增不减门禁项。）

## 存量特批（2026-09-03 修订）

以下 8 个 `tests/test_*` 文件为 M1–M4 期间已产生的回归覆盖，
按本文件“确需新文件须先修订本文件并说明理由”条款特批存量保留，
视为 G2 范围的一部分（全绿为通过条件，与 G2 同命运）：

- `tests/test_adversarial_m1_frame_stress.py`
- `tests/test_adversarial_m1_texture_stress.py`
- `tests/test_adversarial_matrixcolor_pma.py`
- `tests/test_challenger_m2_empirical.py`
- `tests/test_challenger_m3_empirical.py`
- `tests/test_challenger_m3_video_clock.py`
- `tests/test_m2_dissolve_matrixcolor.py`
- `tests/test_renpysound_host_pickle.py`

理由：其中两项曾红（`_is_imagedissolve_node` 单 uniform 漏检，
已在 `renpy/wgpu/draw_surftree.py` 属主修复并 51 全绿），
证明它们携带真实信号；删除绿覆盖而不替换是未定价风险。
同期 8 个 `host/python/gates/*` 孤儿探针已封存至 `attic/host-gates/`
（可逆，恢复命令见该目录 README）。
此后新增文件仍按冻结条款禁止，不再特批。

## 修订规则

- 回归修复优先加断言进 G2（单测）或对应 gate 的子 case，不建新文件。
- G4/G5 的 smoke 时长只允许上调，不允许为“更快通过”下调。
- 任何一项长期稳定（连续 4 周全绿）才可讨论降频，不可删除。
