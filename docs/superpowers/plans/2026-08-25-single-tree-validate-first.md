# Single-Tree Validate-First — Evidence & Verdict (2026-08-25)

> Companion to `geju` 格局判断 (单树 Clean) + `goudi` 落地压测。
> Scope: validate-first 探针，不合主分支；仅塌缩 `host_build` 16 处分支为 `True`。

## Method

- Worktree: `../revult-single-tree` (`-b single-tree-probe`) @ `cb727a3a1`，主分支零污染。
- Tool: `host/scripts/single_tree_probe.py` (`--check / --apply / --revert / --verify`)，幂等。
- Transform: `getattr(renpy, "host_build", False)` → `True`；外层 `not` 保留 → `not True == False` 即 SDL 分支死码。`renpy/__init__.py:145` `host_build: bool = ...` → `True`。

## Evidence (single-tree worktree, RADV NAVI12, DISPLAY=:0)

| Gate | Result | Evidence |
|------|--------|----------|
| cargo check `-D warnings` | PASS | workspace all-targets 0 警告 |
| cargo test --workspace | PASS | **34 passed** (14 lib + 11 main + 9 host_tests) |
| ldd | PASS | `no libSDL*`，`nm -D` 无 SDL 符号 |
| ruff `renpy/wgpu` | PASS | `All checks passed!` |
| import probe | PASS | `host_build True` + `gl2shadercache import ok` |
| **Golden G01–G08** | **PASS 8/8** | `MAE_mean=0 / max=0..1`，`parent_runner` 10 envelopes `ok=True`，corruption/missing fail-closed |
| **phase1 gates** | **PASS** | smoke + nested (depth_deltas=[0]×1000) + input (key/mouse/text) + periodic (count=1198 ≈1200, ±20%)；`backend=Vulkan`；`timestamp_query supported=true` |
| HMC --smoke 30s | **BLOCKED** | `ModuleNotFoundError: renpy.astsupport` — worktree 未编译 Cython `.so`（main 树有；worktree 无）。**非单树回归**：未改的 master worktree 同样失败。 |

## Verdict (goudi)

**GO — validate-first 通过。** 塌缩 `host_build` 不破坏 host 产品路径：像素 (8/8)、链接 (ldd 0)、交互 (input/periodic)、编译 (cargo/ruff) 全绿。`geju` 的 falsifier 全部未触发。

HMC 的 BLOCKED 是构建前置（worktree 缺 Cython），与本次 3 文件改动正交。要补齐需在本 worktree 跑完整 `setup.py packages=sdl3` 编译（即 dual-tree 构建），属 `C` 步范畴，不在 validate-first 内。

## 下一步（Staged Clean 节奏）

1. **合入塌缩**：将 worktree 的 3 文件改动提 PR 到 `single-tree-probe` 分支 → 主分支。这是最小不可逆但可 revert 的决策。
2. **B 真债**：`host/python/gates` 去 `bulk noqa` 真 ruff 0；`benchmark_bc160.sh` 接 `TIMESTAMP_QUERY` 真 `render_pass_duration_ns / 1%low`（probe 已显示 `timestamp_query supported=true`）。
3. **E 打包**：`build_appimage.sh` 从 `--check` 到真打包（Option B 单 AppImage 终态）。
4. **C 剥离**：`setup.py` 翻默认 host；`renpy/pygame/*.pyx` 删；`host/python/host_pygame` 合为 `renpy/pygame` SSOT；SDL 封存 `archive/sdl-reference` tag。
5. **HMC 收口**（可选）：在 C 步产物上跑 `run_huangmeic_playtest.sh --smoke 30s` 作终态验证。

## Revert

```bash
cd ../revult-single-tree && python host/scripts/single_tree_probe.py --revert
git worktree remove ../revult-single-tree --force
```
