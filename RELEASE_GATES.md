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

通过：`host/target/gate-hmc_menu_video_soak_probe.txt` 尾行连续两次
`pass=True` 且 `ok=True`；无 ≥2s stall、无 hang/crash。
（交互式全程 playtest 按版本发布前另行组织，不在本清单内加项。）

## G6 零 SDL 链接

```bash
ldd host/target/release/renpy-host | grep -iE 'libSDL' && echo FAIL || echo OK
```

通过：输出 `OK`（无 `libSDL*` 行）。

## G7 Vulkan 后端断言

```bash
RUST_LOG=info cargo run -p renpy-host -- the_question 2>&1 | grep -q "backend=Vulkan" && echo OK
```

通过：输出 `OK`（适配器行 `wgpu adapter backend=Vulkan`）。

## 修订规则

- 回归修复优先加断言进 G2（单测）或对应 gate 的子 case，不建新文件。
- G4/G5 的 smoke 时长只允许上调，不允许为“更快通过”下调。
- 任何一项长期稳定（连续 4 周全绿）才可讨论降频，不可删除。
