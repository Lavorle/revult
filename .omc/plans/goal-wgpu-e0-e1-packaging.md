# Goal Document: wgpu-host E0封口 + E1分发硬化

## Go / No-Go
- **Judgment**: Go
- **Reason**: 基线已就绪：`wgpu-host v0.6.0 9f62ab39c` 6/6绿 + `78b21d7b4 HEAD` TIMESTAMP_QUERY真值 `2262fps/830 1%low/5098ns render_pass` 已落地，`renpy/wgpu + host/python/gates ruff 0` 已绿，`host.yml` Tier1+2 全链存在，仅剩证据重签、sphinx校验、AppImage/sdist干跑三件收口事，无新增阻塞决策。

## Target Outcome
交付 **E0封口 + E1分发硬化** 的可验证产物，让 `master HEAD` 的证据链自洽、`sdist + renpy-host binary` 分发路径可复现，`AppImage --check` 干跑绿，为 Phase 9 strip 提供删除清单依据。

完成时：
- `release_acceptance.v1.json` / `product_acceptance.v1.json` / `bc160_perf_metrics.v1.json` 的 `evidence_revision` 同步到 HEAD，`ldd no SDL + backend=Vulkan + G01-08 8/8` 仍绿
- `ruff check renpy/wgpu + host/python/gates` 真 0（非 bulk noqa 假绿语义澄清，`per-file-ignores` 已显式化于 `pyproject.toml`）
- `sphinx/source/changelog.rst 8.99.99` 与 `CHANGELOG.md wgpu-host v0.6.0` 一致且 `sphinx-build -b html` 不报错
- `host/scripts/build_appimage.sh --check` 干跑绿（`ldd`/`Vulkan`/`libpython`/`体积预算`校验，无网络下载）
- `host/scripts/build_sdist_manifest.sh --check`（或等效清单校验）确认 `host/python shims + renpy/wgpu` 纳入 sdist、`RENPY_HOST_BUILD=1` 时 `sdl3` 排除路径可验证

## Goal Definition
- **Type**: delivery + quality
- **Boundary**: 含 E0/E1 范围内文档、脚本、CI校验、证据重签；不含 Phase 9 真删文件
- **Non-goals**:
  - 不真删 `renpy/gl2` / `renpy/pygame` SDL源码（仅预演清单）
  - 不真发 `AppImage` 二进制（含 `appimagetool` 下载/打包，仅 `--check`）
  - 不改 `host/renpy-host/src/*.rs` 渲染管线（除非校验脚本需新增只读探针）
- **Deferred work**:
  - Phase 9 真 strip + `setup.py host extra` 落地 → C
  - 真 `AppImage` 打包 + 签名 → E1之后 opt-in
  - `host/python` 全量 `4362` ruff 全仓清零（仅 `renpy/wgpu + gates` 为本期门禁）
- **Verification rule**: 证据链自洽 + 门禁脚本绿
- **Evidence source**: `release_acceptance.v1.json` / `bc160_perf_metrics.json` / `host.yml` artifacts / `sphinx-build` / `phase9_gates.sh --check`
- **Pass criteria**:
  - `evidence_revision == git rev-parse HEAD`
  - `ruff check renpy/wgpu` + `ruff check host/python/gates` 均为 `All checks passed`
  - `sphinx-build -b html sphinx/source /tmp/sphinx_out` 0 exit
  - `host/scripts/build_appimage.sh --check` 0 exit 且日志含 `OK: no libSDL*` + `backend=Vulkan` 关键词
  - `host/scripts/build_sdist_manifest.sh --check` 0 exit（或等效 `python -m build --sdist --dry-run` 清单校验）
- **Confidence note**: 复用既有 `verify-*` 日志链 + `parent_runner 6-field envelope` + `golden_mae fail-closed`，`TIMESTAMP_QUERY true` 后 `render_pass_cpu_proxy=false` 不再是代理值
- **Judgment owner**: `release_acceptance.v1.json verdict PASS` + 本地 `phase9_gates.sh` + CI `host.yml tier1-tier2`

## Current State
- 产物：`host/target/release/renpy-host 13MB`，`libpython3.14 + libvulkan`，无 `libSDL*`；`renpy/wgpu` 拆分 `draw_*.py + rtt_pool.py`，`naga 24.0.0` 直验，`composer 4/4 + combo 2/2`
- 金库：`G01-08 8/8` 单点辞典 `G02/G06`，`golden_mae` 严格阈 `MAE≤2/255 max≤16`，`parent_runner 10 envelopes`
- 性能：`78b21d7 MEASURED 2262fps (1800帧) 1%low 830 render_pass 5098ns TIMESTAMP_QUERY true`（`bc160_perf_metrics.v1.json`），`verify-bench.log` 有 `period via queue.get_timestamp_period()`
- 文档：`CHANGELOG.md wgpu-host v0.6.0` + `doc/packaging-investigation.md D2` + `doc/wgsl_shader_migration.md` 已齐，`sphinx/source/changelog.rst 8.99.99` 已写入封口（需校验构建不破）
- 配置：`pyproject.toml [tool.ruff.per-file-ignores] host/python/**/*.py = [BLE001,S110,...]` 显式豁免 11 码，`renpy/wgpu` 0 真绿，`host/python/gates` 0 真绿，全仓 `4362` 含 `launcher/host/python(non-gates)` 非本期门禁
- 风险：`evidence_revision` 仍指 `935fceb` 非 HEAD；`build_appimage.sh` 尚未存在；`sdist` 清单未脚本化

## Priority Rationale
先让证据自洽（E0），再让分发可复现（E1），最后才碰破坏性删除。E0 的 `evidence_revision` 重签是所有后续 `AppImage/sdist` 产物命名的前提（`renpy-host-<rev>-bc160-measured.tar.gz`），放最前；分发脚本均为只读 `--check` 不动管线，可并行但依赖 E0 的一致版本号。

## Assumptions and Open Decisions
| Item | Status | Impact | Owner / Next step |
|------|--------|--------|-------------------|
| `sphinx 8.99.99` 已是最终文案，无需重写章节 | assumed | 若需重写则 E0 工期+1 | 校验 `sphinx-build` 即可确认 |
| `AppImage --check` 不下载 `appimagetool` | confirmed | 避免网络依赖，E1 可离线绿 | `build_appimage.sh` 内 `if --check then dry-run` 分支 |
| `sdist` 用 `python -m build --sdist` 清单校验而非真发布 | assumed | 不改 `pyproject.toml packages` | 脚本内 `--dry-run` + `tar tzf` 清单 grep |
| `host/python/gates` 的 `per-file-ignores` 视为真绿语义，非 bulk noqa | confirmed | 避免重开 4994 修复 | 保留 `pyproject.toml` 现状，仅校验 |
| Phase 9 仅预演清单，不真删 | confirmed | 控制爆破面 | 产出 `doc/strip-phase9-inventory.md` 即可 |

## Phases

### Phase 1: E0 证据封口
- **Purpose**: 让 HEAD 的三件证据自洽、`sphinx` 可构建
- **Entry condition**: `git status --short` 无未提交渲染管线改动（当前满足）
- **Phase rules**:
  - 仅改文档/脚本/证据 JSON，不改 `host/renpy-host/src/*.rs` 与 `renpy/wgpu/*.py` 渲染逻辑
  - `ldd no SDL` + `backend=Vulkan` 不变量不得破
  - 每步产出可 `cat` 的 JSON/log
- **Todos**:
  - [ ] 重签三件证据到 HEAD
    - **Surface**: `.omc/artifacts/release_acceptance.v1.json`, `product_acceptance.v1.json`, `host/target/bc160_perf_metrics.json` + `host/scripts/build_release_acceptance.py`
    - **Proof**: `jq .evidence_revision host/target/bc160_perf_metrics.json == git rev-parse HEAD` 且 `release_acceptance verdict PASS`
    - **Depends on**: none
  - [ ] 校验 sphinx 可构建
    - **Surface**: `sphinx/source/changelog.rst`, `sphinx/source/wgsl_shaders.rst` (若缺则补镜像)
    - **Proof**: `sphinx-build -b html sphinx/source /tmp/sphinx_out 2>&1 | tee /tmp/sphinx.log; test $? -eq 0`
    - **Depends on**: 重签
  - [ ] 固化 ruff 门禁语义
    - **Surface**: `pyproject.toml`, `host/target/verify-ruff*.log`
    - **Proof**: `ruff check renpy/wgpu` + `ruff check host/python/gates` 均为 `All checks passed` 且 `host.yml` 同步
    - **Depends on**: none
- **Exit proof**: `release_acceptance evidence_revision == HEAD` 且 `sphinx-build` 0 且 `ruff 2x All checks passed`
- **Stop condition**: 任一证据重签后 `golden 8/8` 回归失败 → 停，查 `G02/G06` 辞典是否需再签

### Phase 2: E1 分发硬化 --check
- **Purpose**: 让 `sdist + AppImage` 分发路径可复现、可门禁
- **Entry condition**: Phase 1 证据已对齐 HEAD
- **Phase rules**:
  - 仅新增 `host/scripts/build_appimage.sh` / `build_sdist_manifest.sh` 的 `--check` 干跑分支，不真打包
  - 不引入 `setup.py` host extra 真改（仅文档/脚本探针）
  - `--check` 必须离线可跑（无 `curl/wget`）
- **Todos**:
  - [ ] 新增 `build_appimage.sh --check` 干跑
    - **Surface**: `host/scripts/build_appimage.sh` (new)
    - **Proof**: `bash host/scripts/build_appimage.sh --check 2>&1 | tee /tmp/appimage_check.log; grep -q "OK: no libSDL" /tmp/appimage_check.log`
    - **Depends on**: Phase1
  - [ ] 新增 `build_sdist_manifest.sh --check` 清单校验
    - **Surface**: `host/scripts/build_sdist_manifest.sh` (new)
    - **Proof**: `bash host/scripts/build_sdist_manifest.sh --check 2>&1 | tee /tmp/sdist_check.log; grep -q "host/python" /tmp/sdist_check.log`
    - **Depends on**: Phase1
  - [ ] 更新 `host/README.md §9` 与 `doc/packaging-investigation.md §5` 校验命令
    - **Surface**: `host/README.md`, `doc/packaging-investigation.md`
    - **Proof**: `grep -q "build_appimage.*--check" host/README.md`
    - **Depends on**: 前两者
- **Exit proof**: 两个 `--check` 均为 0 且 CI `host.yml` 新增对应 step（或本地等效日志）
- **Stop condition**: 校验脚本需网络或体积超预算 → 停，改为文档化预算与豁免

### Phase 3: Phase 9 strip 预演清单
- **Purpose**: 为真删提供可审计的删除清单，不真删
- **Entry condition**: Phase 2 --check 绿
- **Phase rules**:
  - 只读扫描 + 生成 `doc/strip-phase9-inventory.md`，不 `rm` 任何 `renpy/gl2` 源码
  - 产出按 `ldd` / `import` / `setup.py packages` 三维分类
- **Todos**:
  - [ ] 生成 strip 预演清单
    - **Surface**: `doc/strip-phase9-inventory.md` (new)
    - **Proof**: `grep -q "libSDL" doc/strip-phase9-inventory.md && grep -q "renpy/gl2" doc/strip-phase9-inventory.md`
    - **Depends on**: Phase2
- **Exit proof**: 清单含三类：`ldd-linked` / `host_build import branch` / `setup.py sdl3 packages`，且 `phase9_gates.sh --dry-run` 仍对 HEAD 绿
- **Stop condition**: 扫描发现 `renpy/gl2` 仍被 `host_build` 间接导入 → 记录为 blocker，不在此期修

## Dry-Run Findings
- `evidence_revision` 漂移是唯一阻塞分发命名的点，必须先收
- `sphinx/source/changelog.rst` 实际已含 `8.99.99`，只需 `sphinx-build` 实跑确认而非重写，避免重复
- `ruff` 全仓 `4362` 非门禁，门禁仅 `renpy/wgpu + gates` 两条，`per-file-ignores` 已是显式豁免，不应再 bulk 改
- `build_appimage.sh` 不存在需新建，但 `--check` 可完全复用现有 `ldd + RUST_LOG + libpython` 探针，无需新 Rust 代码
- `Phase 9` 真删风险高于收益，预演清单即可满足本期 `Doc Necessity Gate`

## Final Validation
```bash
# E0
jq .evidence_revision .omc/artifacts/release_acceptance.v1.json
jq .evidence_revision host/target/bc160_perf_metrics.json
ruff check renpy/wgpu && ruff check host/python/gates
sphinx-build -b html sphinx/source /tmp/sphinx_out 2>&1 | tail -n 20

# E1
bash host/scripts/build_appimage.sh --check
bash host/scripts/build_sdist_manifest.sh --check

# 回归
bash host/scripts/run_golden_tests.sh 2>&1 | grep "8 / 8"
bash host/scripts/phase1_gates.sh 2>&1 | grep "backend=Vulkan"
```

## First Execution Step
重签证据：`python host/scripts/build_release_acceptance.py --rev $(git rev-parse HEAD) --out .omc/artifacts/release_acceptance.v1.json` 等效路径，核对 `evidence_revision` 后跑 `ruff + sphinx-build` 定基线。
