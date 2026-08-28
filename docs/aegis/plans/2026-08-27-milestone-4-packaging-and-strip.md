# Plan: Milestone 4 — 打包发布与 Phase 9 剥离（AppImage + SDL Strip 预演）

**Date:** 2026-08-27
**Milestone:** M4 — E0 证据封口 + E1 分发硬化（--check 干跑） + Phase 9 剥离清单
**Spec Brief / Parent Goal:** `.omc/plans/goal-wgpu-e0-e1-packaging.md`（E0证据重签+E1分发硬化+Phase9预演清单，Go判定） + `host-phase-gap-matrix.md Phase 0-9` + `doc/packaging-investigation.md D2` + `doc/wgsl_shader_migration.md` + `host/README.md §9` + `consensus-wgpu-native-vulkan-rewrite.md §4.7/§5 Phase 9`
**Scope:** **只做** E0 三件证据重签到 HEAD + `sphinx-build` 自检 + `ruff 真0` 校验 + `host/scripts/build_appimage.sh --check` / `build_sdist_manifest.sh --check` 干跑硬化 + `doc/strip-phase9-inventory.md` 三维扫描与迁移开关文档化；**不做** 真 AppImage 打包/`appimagetool` 下载/签名、真 `pip` 发版、真 `rm renpy/gl2` 源码、`host/renpy-host/src/*.rs` 渲染管线改动、`setup.py host extra` 落地（仅文档/脚本探针）。范围 fence：`host/scripts/*.sh + host/scripts/build_release_acceptance.py + sphinx/source/changelog.rst + pyproject.toml + doc/strip-phase9-inventory.md + .omc/artifacts/*.json + host/target/*.json` 可改；禁动 `renpy/gl2` SDL 树、WGSL 管线语义、`gpu.rs:SWAPCHAIN_FORMAT`。
**Parent Plans:** `.omc/plans/goal-wgpu-e0-e1-packaging.md` + `docs/aegis/plans/2026-08-27-wgpu-deslop-gaps.md`（前置去 slop，不重叠） + `host/README.md`
**TDD Route:** off（见下）
**Baseline Commit:** `c5641f8e145074741ea5e934a8c83ccc5df314ae`（2026-08-27 HEAD，领先 origin/master 13）

---

## 0. Header（writing-plans 模板 Header）

### Goal

交付 **E0封口 + E1分发硬化 + Phase 9 strip 预演** 的可验证产物，让 `master HEAD` 的证据链自洽、分发路径可复现（离线 `--check` 绿）、为真删提供可审计清单，且全程不破双树不变量。

完成时：
- `release_acceptance.v1.json` / `product_acceptance.v1.json` / `bc160_perf_metrics.v1.json` 的 `evidence_revision` 同步到 `git rev-parse HEAD`，`ldd 无 SDL + backend=Vulkan + Rgba8Unorm + G01-08 8/8 MAE≤2/255` 仍绿。
- `ruff check renpy/wgpu` + `ruff check host/python/gates` 均为 `All checks passed`（`pyproject.toml [tool.ruff.lint.per-file-ignores]` 显式豁免，非 bulk noqa 假绿）。
- `sphinx/source/changelog.rst 8.99.99` 与 `CHANGELOG.md wgpu-host v0.6.0` 一致且 `sphinx-build -b html sphinx/source /tmp/sphinx_out` 0 退出。
- `host/scripts/build_appimage.sh --check` 干跑绿（`ldd`/`Vulkan`/`libpython`/`体积预算`校验，无网络下载，日志含 `OK: no libSDL*` + `backend=Vulkan`）。
- `host/scripts/build_sdist_manifest.sh --check` 绿（`host/python 164 + renpy/wgpu 19` 纳入清单，`RENPY_HOST_BUILD=1` 时 `sdl3` 排除路径可验证）。
- `doc/strip-phase9-inventory.md` 含三维分类 `ldd-linked / host_build import branch / setup.py packages=sdl3` 的 kill-list，且 `phase9_gates.sh` 在 HEAD 上仍绿（不真删 guard）。

### Architecture

- **双树锁定**：SDL 参考树（`renpy/gl2` / `renpy/pygame/*.pyx packages=sdl3`）保持可构建直至 Phase 9 真删；host 产物（`host/target/release/renpy-host` 13MB + `renpy/wgpu` + `host/python`）独立，`ldd` 永不含 `libSDL*`。
- **渲染契约冻结**：`gpu.rs:SWAPCHAIN_FORMAT = Rgba8Unorm`（`gpu.rs:14`）+ `arena.rs: BgCacheKey / LruSlotMap / RTT pool cap 8` + WGSL 12 管线（`arena.rs:2187-2691`）不改；`WGPU_BACKEND` 必须 unset，host 代码强制 `Backends::VULKAN`。
- **证据单源（SSOT）**：`evidence_revision` 绑定 `git rev-parse HEAD`，`bc160_perf_metrics.v1.json`（`host/target/bc160_perf_metrics.json:2152fps/537 1%low/4813ns TIMESTAMP_QUERY true`）+ `release_acceptance.v1.json` + `host/target/envelopes/*.json (10 envelopes)` 为发行命名（`renpy-host-<rev>-bc160-measured.tar.gz`）的唯一依据。
- **分发分层**：E1 仅 `--check` 干跑（只读探针）；真打包/签名/发版为 opt-in，不在本 Milestone 阻塞。

### Tech Stack

- **Rust host:** `host/renpy-host` `winit 0.30 + wgpu 24 + naga 24.0.0`，`cargo fmt/check/test 34`，`RUSTFLAGS='-D warnings'`。
- **Python:** `renpy/wgpu/*`（19 .py，`draw_*.py + rtt_pool.py + shaders.py + composer.py + video.py 898行`） + `host/python/*`（164 .py，`host_pygame` 20 垫片 + `gates` 134 门）。
- **文档/门禁:** `sphinx + sphinx_rtd_theme`，`ruff`，`ldd`，`bash` 探针脚本，`host.yml Tier1+2` CI，`phase9_gates.sh` / `run_golden_tests.sh` / `parent_runner.py 6-field envelope`。

### Baseline / Authority Refs

```text
BaselineUsageDraft:
- Required baseline refs:
  - .omc/plans/goal-wgpu-e0-e1-packaging.md（E0/E1/Phase9 三阶段，Pass criteria 5 条）
  - .github/workflows/host.yml（Tier1+2：fmt/check/test 34/8-8/ruff/phase1 ldd+Vulkan）
  - host/target/bc160_perf_metrics.json（MEASURED 2152fps / 1%low 537 / render_pass 4813ns TIMESTAMP_QUERY true）
  - .omc/artifacts/release_acceptance.v1.json（5c3b8f2 旧 rev，需重签到 c5641f8 HEAD）
  - host/scripts/phase9_gates.sh + run_golden_tests.sh（G01-08 8/8 MAE≤2/255）
  - doc/packaging-investigation.md §2-4（sdist vs AppImage vs renpy-build 决策）
  - doc/strip-phase9-inventory.md（ldd/import/setup.py 三维清单 DRAFT 78b21d7b4）
  - host/scripts/build_appimage.sh + build_sdist_manifest.sh（已存在，仅 --check 干跑）
  - host/scripts/build_release_acceptance.py（证据重签 SSOT）
  - host/README.md §9 + doc/wgsl_shader_migration.md + AGENTS.md §0-8（双树/Vulkan/Rgba8Unorm/BgCache）
  - renpy/wgpu/video.py + host/renpy-host/src/arena.rs + host/renpy-host/src/gpu.rs + host/renpy-host/src/audio.rs（关联上下文，证据定位）
- Delivered context refs: 2026-08-27 4-Milestone 并行起草（M1-M4）中的 M4 分片；VideoSubsystemAudit / HostRustAudit / PythonWgpuAudit 已读上述文件
- Acknowledged before plan refs: 上述 12 份（host.yml 全文、goal 全文、bc160/metrics、release_acceptance、phase9_gates、packaging-investigation、strip-inventory、build_appimage/sdist、build_release_acceptance、host README、wgsl migration、video.py/arena.rs/gpu.rs）
- Cited in plan refs: 同上 + sphinx/source/changelog.rst:8.99.99 + sphinx/source/conf.py + pyproject.toml per-file-ignores + setup.py:39-41 HOST_BUILD
- Missing refs: BC-160 RGP 一帧（可选 evidence，非阻塞）、HuangmeiC 全量 Prefs hover RGP（可选）
- Decision: continue
```

### Compatibility Boundary（冻结）

| 维度 | 冻兼容（MUST NOT break） | 本 Milestone 行为 | 违规 falsifier |
|------|--------------------------|------------------|----------------|
| **双树不变量** | `ldd host/target/release/renpy-host \| grep -qi libSDL` 为空；`WGPU_BACKEND` unset；`gpu.rs:SWAPCHAIN_FORMAT = Rgba8Unorm`；`RUST_LOG=info` 含 `backend=Vulkan` | 仅只读 `ldd` 探针、日志 `grep`，不改 `host/renpy-host/src/*.rs` 链接线 | `phase9_gates.sh` `FAIL: SDL linked` 或 `backend != Vulkan` |
| **像素等价** | G01-08 8/8 via `parent_runner`，`golden_mae` MAE≤2/255 max≤16，`composer 4/4 + combo 2/2` | 不改 `renpy/wgpu` 渲染逻辑，仅校验后 `cat` JSON/log | 任一 `gate-*.txt` 含 `ok=False` |
| **Ruff 门禁语义** | `ruff check renpy/wgpu` + `host/python/gates` 真 0；`pyproject.toml per-file-ignores` 显式豁免 11 码为真绿，非 bulk noqa 假绿 | 仅 `ruff check` 校验并同步 `host.yml` 断言，不改 `pyproject.toml` 豁免列表 | `verify-ruff.log` 非 `All checks passed` |
| **Sphinx** | `sphinx/source/changelog.rst 8.99.99` 与 `CHANGELOG.md wgpu-host v0.6.0` 一致 | `sphinx-build -b html` 干跑校验，不重写章节 | `sphinx-build` 非 0 或 `changelog.rst` 缺 `8.99.99` |
| **分发 --check vs 真发版** | `--check` **离线、零网络、无写盘**（无 `curl/wget/appimagetool` 下载、无 squashfs 写入、无 `pip upload`） | `build_appimage.sh --check` / `build_sdist_manifest.sh --check` 分支内禁止网络；真打包/签名/发版为 **opt-in**（需显式 `./host/scripts/build_appimage.sh --build` 且不在本 Milestone 门禁） | `--check` 中出现 `curl`/`wget`/`appimagetool` 下载或非 0 网络出口 |
| **Phase 9 不真删 guard** | `doc/strip-phase9-inventory.md` 为只读清单，不 `rm` 任何 `renpy/gl2/*.pyx` 源码；`setup.py HOST_BUILD` 仅探针校验，不改 `packages=sdl3` 落地 | 生成/刷新清单 + 文档化迁移开关，保留 `if getattr(renpy, "host_build", False):` 分支 | `git status` 出现 `D renpy/gl2/*` 删除记录 |

### TDD Route

```text
TDD Route:
- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: post-change regression / bench 门禁（非 RED）
- Reason: 本 Milestone 无新契约/新分支语义，仅证据重签、干跑探针、清单生成三类收口；风险在证据漂移与脚本零网络不变性，需金像+host.yml Tier1+2 事后回归而非预写 RED。Decision: skipped 按约束显式，不落 RED/GREEN 子步。
- Verification: cargo fmt/check/test 34 + ruff 2× All checks passed + sphinx-build 0 + build_appimage.sh --check + build_sdist_manifest.sh --check + phase9_gates 8/8 + host.yml artifact 核对（见各 Task Verification）
```

### Verification（Milestone 级总门禁，exact cmd）

```bash
# E0 证据封口：三件证据同 HEAD
REV=$(git rev-parse HEAD); echo "HEAD=$REV"
jq -e --arg rev "$REV" '.evidence_revision == $rev' .omc/artifacts/release_acceptance.v1.json && echo "OK release_acceptance HEAD"
jq -e --arg rev "$REV" '.evidence_revision == $rev' .omc/artifacts/product_acceptance.v1.json && echo "OK product_acceptance HEAD"
jq -e --arg rev "$REV" '.evidence_revision == $rev' host/target/bc160_perf_metrics.json && echo "OK bc160 HEAD"
jq -e '.evidence_revision == "'$(git rev-parse HEAD)'"' .omc/artifacts/release_acceptance.v1.json  # 合同 exact 形

# E0 文档/门禁：sphinx + ruff 真0
sphinx-build -b html sphinx/source /tmp/sphinx_out 2>&1 | tee /tmp/sphinx.log; test $? -eq 0 && echo "OK sphinx"
ruff check renpy/wgpu 2>&1 | tee /tmp/ruff_wgpu.log; grep -q "All checks passed" /tmp/ruff_wgpu.log && echo "OK ruff renpy/wgpu"
ruff check host/python/gates 2>&1 | tee /tmp/ruff_gates.log; grep -q "All checks passed" /tmp/ruff_gates.log && echo "OK ruff gates"
cat host/target/verify-ruff.log 2>/dev/null | grep -q "All checks passed" && echo "OK verify-ruff.log"

# E1 分发硬化：--check 干跑（离线零网络）
bash host/scripts/build_appimage.sh --check 2>&1 | tee /tmp/appimage_check.log
grep -q "OK: no libSDL" /tmp/appimage_check.log && grep -q "OK: libvulkan" /tmp/appimage_check.log && grep -q "backend=Vulkan\|backend: Vulkan" /tmp/appimage_check.log && echo "OK appimage --check"
bash host/scripts/build_sdist_manifest.sh --check 2>&1 | tee /tmp/sdist_check.log
grep -q "host/python" /tmp/sdist_check.log && grep -q "renpy/wgpu" /tmp/sdist_check.log && grep -q "OK: host/python\|host/python.*OK" /tmp/sdist_check.log && echo "OK sdist --check"

# Phase 9 / 回归：金像 + 宿主门禁 + 双树不变量
bash host/scripts/phase9_gates.sh 2>&1 | tee /tmp/phase9_gates.log; grep -q "all Phase 9 host gates" /tmp/phase9_gates.log && echo "OK phase9 8/8"
bash host/scripts/run_golden_tests.sh 2>&1 | tee /tmp/golden.log; grep -q "8 / 8" /tmp/golden.log && echo "OK golden 8/8"
ldd host/target/release/renpy-host 2>&1 | tee /tmp/ldd_check.log; ! grep -qi "libSDL" /tmp/ldd_check.log && echo "OK ldd no SDL"
RUST_LOG=info cargo run -p renpy-host -- --help 2>&1 | head -n 5 || true  # 若需 probe 则查 host/target/verify-phase1.log
grep -q "backend=Vulkan" host/target/verify-phase1.log && echo "OK backend Vulkan"
grep -q "backend=Vulkan" /tmp/appimage_check.log || grep -q "backend=Vulkan" host/target/verify-phase1.log

# host.yml artifact 核对（CI 产物链同本地）
grep -q "verify-ruff.log" .github/workflows/host.yml && grep -q "verify-ldd-release.log" .github/workflows/host.yml && grep -q "bc160_perf_metrics.json" .github/workflows/host.yml && echo "OK host.yml artifacts"
ls -l host/target/verify-*.log .omc/artifacts/release_acceptance.v1.json host/target/bc160_perf_metrics.json 2>&1 | tee /tmp/artifact_ls.log
```

---

## 1. Plan Basis（紧凑契约）

```text
Requirement Ready Check:
- Requirement source refs: .omc/plans/goal-wgpu-e0-e1-packaging.md（Go，已含 E0/E1/Phase9 三阶段 Todos + Pass criteria 5 条 + Final Validation 7 行）
- Goals and scope refs: 见本计划 §0 Goal 6 条完成态；Non-goals 3 条（不真删/不真发/不改管线）与 Deferred 3 条已锁
- User / scenario refs: revult host Linux Vulkan MVP，分发命名 `renpy-host-<rev>-bc160-measured.tar.gz` 需 evidence_revision 先对齐 HEAD
- Requirement item refs: 下述 T1-T4 逐条映射 goal Phases Todos（T1→Phase1 三条、T2→Phase2 AppImage、T3→Phase2 sdist、T4→Phase3 清单）
- Acceptance / verification criteria refs: §6 Acceptance 5 条 + Exit proof / Stop condition；本计划 §0 Verification 7 组 exact cmd 已覆盖
- Open blocker questions: 无；`evidence_revision` 漂移（5c3b8f2 vs HEAD c5641f8）为唯一阻塞，已纳入 T1 首步
- Decision: ready

Change Necessity:
- User-visible need: HEAD 13 个 commits 未重签导致 `release_acceptance.v1.json evidence_revision 5c3b8f2 != HEAD c5641f8`，分发产物命名与 CI 证据链断裂；`sphinx-build` 未实跑、`build_appimage.sh --check` / `build_sdist_manifest.sh --check` 需硬化为离线门禁；Phase 9 需可审计清单方敢真删
- No-change / non-code option: 不追加代码仅文档无法自洽证据链（`jq` 必 fail）、无法离线探针体积预算与 ldd 逃逸
- Why code change is necessary: 必须改 `host/scripts/build_release_acceptance.py` 重签逻辑 + 两脚本 `--check` 分支的只读探针（ldd/Vulkan/libpython/预算/清单 grep）+ 刷新 `doc/strip-phase9-inventory.md` 三维扫描；均为最小脚本/文档面，不碰 `host/renpy-host/src/*.rs` 渲染
- Minimum change boundary: .omc/artifacts/*.json（重签）+ host/scripts/*.sh（--check 硬化）+ sphinx/source/changelog.rst 校验（不动）+ doc/strip-phase9-inventory.md（清单）+ host/README.md & doc/packaging-investigation.md §5 命令同步
- Decision: code-change（脚本/文档/证据 JSON 三类，受 Change Necessity 约束）

Existence Check:
- Proposed new surface: 无新 owner；仅复用现有 `host/scripts/build_appimage.sh` / `build_sdist_manifest.sh` 的 `--check` 分支与 `doc/strip-phase9-inventory.md` 清单文件（已存在 DRAFT，需刷新到 HEAD）
- Existing owner / reuse candidate: `host/scripts/build_release_acceptance.py` 已为 evidence SSOT；`host.yml Tier1+2` 已为门禁 SSOT；`doc/packaging-investigation.md` 已为分发决策 SSOT
- Why existing surface is insufficient: `evidence_revision` 仍指 5c3b8f2，`--check` 需补离线零网络 guarantee 与体积预算文档化，清单需从 `78b21d7b4` 刷新到 HEAD `c5641f8`
- Creation proof: `git rev-parse HEAD` vs `jq .evidence_revision .omc/artifacts/release_acceptance.v1.json` 差 13 commits；`host/scripts/build_appimage.sh:6 Scope: Debt D/E1 docs only; --check is dry-run, no network` 已声明但需门禁锁；`doc/strip-phase9-inventory.md:6 Status: DRAFT — generated ... on 2026-08-25` 需刷新
- Entropy / retirement impact: 零新 owner，仅清单与脚本加固，净熵不增；退役触发：Phase 9 真删时清单转为删除执行依据
- Decision: reuse-existing（仅刷新与硬化，不新增 owner/skill/artifact）

Architecture Integrity Lens:
- Invariant: 双树 + Rgba8Unorm + Vulkan only + G01-08 8/8（AGENTS.md §0-5 + gpu.rs:14 + host.yml:59-75 + phase9_gates.sh:46-64）
- Canonical owner / contract: `host/scripts/build_release_acceptance.py`（证据 SSOT） / `host.yml`（CI SSOT） / `doc/packaging-investigation.md`（分发 SSOT） / `gpu.rs + arena.rs`（渲染 SSOT，不动）
- Responsibility overlap: `build_appimage.sh --check` 与 `phase1_gates.sh ldd` 重叠探针是否冗余？否 — 前者为分发预算/体积/不打包回收校验，后者为宿主回归；保留双探针，分工写于 T2.Why
- Higher-level simplification: 可否用单一 `python -m build --sdist` 替代两脚本？否 — AppImage 需 ldd/Vulkan/size 三探针，sdist 需 sdl3 排除探针，职责不同，不合并
- Retirement / falsifier: 若 `build_appimage.sh --check` 需网络即 falsify 离线不变量 → 回退为文档化预算豁免；若 `phase9_gates.sh` 在清单刷新后 fail → 停，记 blocker 不在此期修
- Verdict: reuse-existing owners, edit-in-place 其余

Plan Pressure Test:
- Owner / contract / retirement: 三 SSOT 不增新 owner；退休面为清单刷新可 revert；无新 owner 增熵
- Architecture integrity / higher-level path: 已验无更高层 Owner 可替代（渲染 SSOT 不动，分发仅脚本）
- Verification scope: sphinx + ruff 2× + --check 2× + phase9 8/8 + ldd + backend + host.yml artifact 7 组齐全
- Task executability: 每 Task 2-5 min slice，文件边界独立，失败可单 Task revert
- Pressure result: proceed

Plan-Time Complexity Check:
- Target files: host/scripts/build_appimage.sh (~151 行) + build_sdist_manifest.sh (~166 行) + build_release_acceptance.py (~101 行) + doc/strip-phase9-inventory.md (~126 行)
- Existing size / shape signals: 脚本均 <200 行，无 80 行巨函数；仅需硬化校验分支，不增圈复杂度
- Owner fit: 脚本 owner 正确（host/scripts 为分发探针唯一 owner），证据 owner 正确（build_release_acceptance.py）
- Add-in-place risk: 在原脚本加 `grep -q "OK: no libSDL"` 等校验为同文件内加固，不触 over-budget
- Better file boundary: 无需新 crate/新 owner，edit-in-place
- Recommendation: edit-in-place

Execution Readiness View:
- Intent Lock: 仅收口 E0/E1 与 Phase 9 预演，不引入真打包/签名/真删
- Scope Fence: 见 Files 表 9 文件可改；禁动 renpy/gl2、renpy/wgpu 渲染、host/renpy-host/src/*.rs（除非校验脚本需只读探针，且需 Phase 负责人书面确认）
- Baseline Lock: Rgba8Unorm + PMA + One/OneMinusSrcAlpha；ldd 空；backend=Vulkan；MAE≤2/255；BC-160 MEASURED 2152fps；evidence_revision==HEAD
- Approved Behavior: 像素等价（G01-08 0 回归），分发产物命名可复现，清单含三类 kill-list 且 host.yml 仍绿
- Owner / Contract Constraints: build_release_acceptance.py 为证据唯一写入者；host.yml 为 CI 唯一门禁；packaging-investigation.md 为分发唯一决策
- Compatibility Boundary: --check 离线零网络；真打包/签名为 opt-in（需显式 --build 且不在本 Milestone 门禁）
- Retirement Boundary: 无旧路径删除；strip-phase9-inventory.md 刷新可 revert，setup.py sdl3 排除仅探针校验
- Task Batches: B1 E0 证据封口（T1）→ B2 E1 分发硬化（T2→T3 串行，依赖 B1）→ B3 Phase 9 清单（T4，依赖 B2）
- Test Obligations: ruff + sphinx + --check 2× + phase9_gates + golden 8/8；无新增单测（TDD off，post-change regression）
- Review Gates: cargo fmt --check + cargo check -D warnings + ruff 全过 + host.yml artifact 核对
- Drift / Rewind Rules: 单 Task 可独立 git revert；B1 任一 fail 即全 plan pause，不进入 B2；B2 fail 则 T4 不启动
- Evidence Required Before Completion: 见 §0 Verification 7 组日志 + host/target/verify-*.log + /tmp/sphinx.log + /tmp/*_check.log
- Advisory Boundary: method-pack 执行指引；非 GateDecision，仅达标前置
```

---

## Files

| 文件 | 动作 | 边界 |
|------|------|------|
| `.omc/artifacts/release_acceptance.v1.json` | **改（重签）** | 证据 SSOT：`evidence_revision` / `evidence_revision_short` / `timestamp_utc` / `artifacts_digest_sha256` / `tier1_host.backend_Vulkan` 同步到 HEAD `c5641f8`，`verdict PASS` 保持，`bc160` 块取自 `host/target/bc160_perf_metrics.json` 快照 |
| `.omc/artifacts/product_acceptance.v1.json` | **改（重签）** | 同 HEAD 重签：`evidence_revision` / `tier2_golden.evidence_revision` / `envelopes` / `bc160` 同步；不改 `tier3_*` 生产链外门禁 |
| `host/target/bc160_perf_metrics.json` | **校验（不动）** | 已为 `MEASURED 2152fps + render_pass_cpu_proxy false + TIMESTAMP_QUERY true`，仅 `jq` 校验 `measurement_status==MEASURED && release_evidence_eligible==true`，不重采（重采为 opt-in，见 T1 备注） |
| `host/scripts/build_release_acceptance.py` | **校验+小改（如需）** | 证据生成器：`git_rev()` 取 `HEAD`，`assert bc.measurement_status==MEASURED / pass_status==PERFORMANCE_TARGET_MET / release_evidence_eligible==true / envelopes>=10 / verify-*.log 存在`；若脚本旧 rev 硬编码则删，改为动态 `git rev-parse HEAD` |
| `sphinx/source/changelog.rst` | **校验（不动）** | `8.99.99 — wgpu-host v0.6.0` 已写入（`changelog.rst:10-50`），仅 `sphinx-build -b html` 校验；若缺 `wgsl_shaders.rst` 镜像则补镜像，不重写章节 |
| `sphinx/source/conf.py` | **校验（不动）** | 已含 `extensions + html_theme sphinx_rtd_theme`，仅确认 `sphinx-build` 不因 `renpy.versions` 缺失而破（已含 `try: import renpy` 分支） |
| `pyproject.toml` | **校验（不动）** | `per-file-ignores` 已显式 11 码于 `host/python/gates + _renpy_host + host_pygame`，仅 `ruff check` 校验，不改豁免列表 |
| `host/scripts/build_appimage.sh` | **改（硬化 --check）** | 新增/硬化 `--check` 分支：`ldd no SDL + libpython3 + libvulkan + WGPU_BACKEND unset + backend=Vulkan probe + size budget (<220MB) + recovered_project not bundled`，零网络 guarantee，日志含 `OK: no libSDL*` 关键词；`--build` 分支保持 `exit 2` stub（opt-in 占位） |
| `host/scripts/build_sdist_manifest.sh` | **改（硬化 --check）** | 新增/硬化 `--check`：`host/python .py≥5 + renpy/wgpu .py≥3 + pyproject sdl3 gating probe + setup.py HOST_BUILD probe + sdist dry-run tar tzf`，零网络；`--build` 同样 stub |
| `host/README.md §9` + `doc/packaging-investigation.md §4-5` | **改（命令同步）** | 同步 `bash host/scripts/build_appimage.sh --check` / `build_sdist_manifest.sh --check` 校验命令与体积预算说明；不改分发决策（仍为 A sdist+host binary 短期，B AppImage opt-in） |
| `doc/strip-phase9-inventory.md` | **改（刷新到 HEAD）** | 三维扫描：`ldd` 维（`ldd + nm -D` 无 SDL） / `import` 维（`renpy/display/core.py 16 host_build sites + gl2shadercache 软 stub`） / `setup.py packages` 维（`HOST_BUILD=1` 时 `cython(packages=sdl3)` 硬失败）；Header `Status: DRAFT — generated ... on 2026-08-27 HEAD c5641f8` 刷新；不 `rm` 任何源码 |
| `.github/workflows/host.yml` | **校验（不动，如需则小改）** | Tier1+2 已含 `cargo fmt/check/test 34 / ruff / ldd no SDL / golden 8/8 / phase1_gates Vulkan`；若缺 `build_appimage.sh --check` / `build_sdist_manifest.sh --check` 则新增 step（见 T2/T3），否则仅校验 artifact 列表含 `host/target/verify-*.log + envelopes + bc160 + release_acceptance` |

不改：`renpy/wgpu/*` 渲染（含 `video.py:878行 shim`）、`host/renpy-host/src/arena.rs 8618行 / gpu.rs 6468行 / audio.rs / shader.rs`、`renpy/gl2/*` SDL 树、`renpy/display/core.py host_build` 分支语义（除清单扫描外）、`setup.py HOST_ALLOW` 列表。

---

## Compatibility Boundary（冻边界重申，执行期必查）

- `SWAPCHAIN_FORMAT = Rgba8Unorm` + PMA + `One / OneMinusSrcAlpha` 混合不改；金像捕获仍为 **pre-present game RT**（`doc/wgsl_shader_migration.md §Color/format`）。
- `WgpuDraw` 单渲染器；`renpy.host_build` 分支不破 SDL 树；`host_pygame` 公共 API 仅增不减。
- `WgslShaderCache` 以排序 part 集 `sha1[:16]` 为 `cache_key` 不变；`assert_pipeline_map_honest()` 仍过（如 T2/T3 触 shaders 则必跑）。
- `ldd host/target/{debug,release}/renpy-host | grep -iE 'libSDL'` 为空；`RUST_LOG=info` 必含 `adapter backend=Vulkan`（`gpu.rs:241` 日志）。
- `--check` 零网络：脚本内禁止 `curl/wget/git clone/appimagetool` 下载；真打包/签名/发版为 opt-in（需显式 `--build` 且不在本 Milestone CI 门禁）。

---

## Tasks（2-5 min/step，TDD off：最小改 + post-change regression）

### T1 — E0 证据封口：三件证据重签到 HEAD + sphinx 自检 + ruff 真0

**Files:** `.omc/artifacts/release_acceptance.v1.json` / `.omc/artifacts/product_acceptance.v1.json` / `host/target/bc160_perf_metrics.json` / `host/scripts/build_release_acceptance.py` / `sphinx/source/changelog.rst` / `sphinx/source/conf.py` / `pyproject.toml` / `host/target/verify-*.log`
**Why:** `release_acceptance evidence_revision 5c3b8f2` 落后 HEAD `c5641f8` 13 commits，分发产物命名 `renpy-host-<rev>-bc160-measured.tar.gz` 与 CI 证据链断裂；`sphinx 8.99.99` 已写但未实跑 `sphinx-build`，`ruff` 需真 0 定基线（`pyproject.toml:98-103 per-file-ignores` 语义澄清）。
**Change Necessity:** 非代码选项不足 — 仅文档无法让 `jq .evidence_revision == HEAD` 绿；最小边界为证据 JSON 重签 + 两门禁实跑（不改 `host/renpy-host/src/*.rs` 渲染逻辑）。
**Impact/Compat:** 证据 JSON 仅改 `evidence_revision`/`timestamp_utc`/`artifacts_digest_sha256`/`tier1_host.backend_Vulkan` 四字段，`verdict PASS` 与 `bc160` 测量值（`2152fps/537 1%low/4813ns`）保留；`sphinx`/`ruff` 为只读校验，不改豁免列表；双树不变量不触。
**Verification:** `jq .evidence_revision == $(git rev-parse HEAD)` 三件；`sphinx-build -b html` 0；`ruff check renpy/wgpu` + `host/python/gates` 真 0；`host.yml` 同步（见 Steps 末）。

**Steps:**

1. **探漂移（只读，1 min）** — 定位证据漂移与基线：
   ```bash
   git rev-parse HEAD && git rev-parse --short HEAD
   jq -r '.evidence_revision' .omc/artifacts/release_acceptance.v1.json; echo "---"; jq -r '.evidence_revision' host/target/bc160_perf_metrics.json
   jq -r '.evidence_revision' .omc/artifacts/product_acceptance.v1.json 2>/dev/null || echo "no product file"
   cat host/target/bc160_perf_metrics.json | jq '{measurement_status, pass_status, release_evidence_eligible, average_fps, one_percent_low_fps, render_pass_cpu_proxy}'
   cat host/target/verify-phase1.log 2>/dev/null | grep -E "backend=Vulkan|OK: no libSDL" | head -n 5
   git status --short --branch | head -n 20
   ```
   预期：`release_acceptance 5c3b8f2 != HEAD c5641f8`，`bc160 MEASURED true`，`verify-phase1.log` 含 `backend=Vulkan`。

2. **重签证据（code-change，3 min）** — 运行生成器并落盘新 rev：
   ```bash
   # 先备旧件（可 revert）
   cp .omc/artifacts/release_acceptance.v1.json /tmp/release_acceptance.v1.json.bak
   cp .omc/artifacts/product_acceptance.v1.json /tmp/product_acceptance.v1.json.bak
   cp host/target/bc160_perf_metrics.json /tmp/bc160_perf_metrics.json.bak

   # 若 bc160 需重采（仅当 measurement_status != MEASURED 时，否则跳过此行）：
   # bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json

   # 重签（authority: host/scripts/build_release_acceptance.py:22-94）
   python3 host/scripts/build_release_acceptance.py --out .omc/artifacts/release_acceptance.v1.json
   # 脚本内已重写 product_acceptance.v1.json 并同步 bc160 块；若脚本未写 product，则手动：
   # python3 host/scripts/build_release_acceptance.py  # 默认 out 为 release_acceptance，product 由脚本内 rewrite

   # 若脚本旧逻辑对 rev 硬编码 5c3b8f2，则改为动态：
   # grep -n "5c3b8f2\|evidence_revision.*=" host/scripts/build_release_acceptance.py
   # 确保 def git_rev(): return subprocess.check_output(["git","rev-parse","HEAD"])... 且 main() 内 rev = git_rev()

   cat .omc/artifacts/release_acceptance.v1.json | jq '{evidence_revision, evidence_revision_short, verdict, release_ready, tier1_host, tier2_golden, bc160: {average_fps, render_pass_cpu_proxy}}'
   cat .omc/artifacts/product_acceptance.v1.json | jq '{evidence_revision, tier2_golden}' 2>/dev/null | head -n 30
   ```
   预期：`release_acceptance evidence_revision == $(git rev-parse HEAD)`，`release_acceptance.v1.json` 顶层 `bc160.average_fps` 取自 `host/target/bc160_perf_metrics.json`。

3. **三件证据同 HEAD 校验（exact cmd，1 min）** — 合同门禁：
   ```bash
   REV=$(git rev-parse HEAD); echo "REV=$REV"
   jq -e --arg rev "$REV" '.evidence_revision == $rev' .omc/artifacts/release_acceptance.v1.json && echo "OK release_acceptance HEAD"
   jq -e --arg rev "$REV" '.evidence_revision == $rev' .omc/artifacts/product_acceptance.v1.json && echo "OK product_acceptance HEAD"
   jq -e --arg rev "$REV" '.evidence_revision == $rev' host/target/bc160_perf_metrics.json && echo "OK bc160 HEAD"
   # 合同 exact 形（单行）：
   jq -e '.evidence_revision == "'$(git rev-parse HEAD)'"' .omc/artifacts/release_acceptance.v1.json && echo "OK contract single-line"
   # 额外：artifacts 10 envelopes 仍在
   jq -e '.artifacts | length >= 10' .omc/artifacts/release_acceptance.v1.json && echo "OK envelopes >=10"
   jq -e '.measurement_status=="MEASURED" and .release_evidence_eligible==true' host/target/bc160_perf_metrics.json && echo "OK bc160 eligible"
   ```
   预期：四行 `OK` 全绿；任一 fail 则停，不进入 T2（证据链断裂为 P0 阻断）。

4. **sphinx 自检（post-change regression，2 min）** — `8.99.99` 构建不破：
   ```bash
   grep -n "8.99.99" sphinx/source/changelog.rst | head
   grep -n "wgsl_shaders\|wgsl_shader_migration\|WGSL" sphinx/source/changelog.rst | head
   test -f sphinx/source/wgsl_shaders.rst && echo "wgsl_shaders.rst exists" || echo "wgsl_shaders.rst missing (ok if changelog mirrors doc/wgsl_shader_migration.md)"
   sphinx-build -b html sphinx/source /tmp/sphinx_out 2>&1 | tee /tmp/sphinx.log
   echo "exit=$?"; tail -n 20 /tmp/sphinx.log
   test -f /tmp/sphinx_out/index.html && echo "OK sphinx index.html" || echo "FAIL sphinx output"
   ```
   预期：`sphinx-build exit 0`，`tail` 无 `ERROR`，`/tmp/sphinx_out/index.html` 存在；`changelog.rst:10-50` 含 `8.99.99 — wgpu-host v0.6.0`。

5. **ruff 真0 校验（post-change regression，1 min）** — 门禁语义澄清：
   ```bash
   ruff --version
   ruff check renpy/wgpu 2>&1 | tee /tmp/ruff_wgpu.log; cat /tmp/ruff_wgpu.log; grep -q "All checks passed" /tmp/ruff_wgpu.log && echo "OK ruff renpy/wgpu"
   ruff check host/python/gates 2>&1 | tee /tmp/ruff_gates.log; cat /tmp/ruff_gates.log; grep -q "All checks passed" /tmp/ruff_gates.log && echo "OK ruff gates"
   ruff check renpy/wgpu host/python/gates 2>&1 | tee host/target/verify-ruff.log; cat host/target/verify-ruff.log; grep -q "All checks passed" host/target/verify-ruff.log && echo "OK verify-ruff.log"
   grep -n "per-file-ignores" pyproject.toml; grep -A3 'host/python/gates' pyproject.toml
   # 全仓 4362 非门禁，仅确认两门禁真 0：
   ruff check renpy/wgpu host/python/gates --statistics 2>&1 | head -n 20 || true
   ```
   预期：三行 `OK` 全绿；`pyproject.toml:98-103` 三条 `per-file-ignores` 仍在，`host/python/**/*.py` 级 bulk 豁免已收窄为按目录显式（见 `host/python/_renpy_host.py` 与 `host_pygame` 分条）。

6. **回归 smoke（1 min）** — 确保重签未破回归：
   ```bash
   cat host/target/verify-ldd-release.log 2>/dev/null | head -n 20 || ldd host/target/release/renpy-host 2>&1 | tee host/target/verify-ldd-release.log
   ! grep -qi "libSDL" host/target/verify-ldd-release.log && echo "OK ldd no SDL"
   grep -q "backend=Vulkan" host/target/verify-phase1.log && echo "OK backend Vulkan (cached)" || echo "need phase1_gates probe"
   ```

---

### T2 — E1 AppImage `--check` 干跑硬化

**Files:** `host/scripts/build_appimage.sh`（改，硬化 `--check` 分支） + `host/README.md §9` + `doc/packaging-investigation.md §4-5`（命令同步） + `.github/workflows/host.yml`（如缺则增 step）
**Why:** `host/scripts/build_appimage.sh` 已存在但需硬化为**离线零网络**门禁，验证 `ldd no SDL + Vulkan + libpython + 体积预算` 四探针，为 AppImage 单文件分发（`doc/packaging-investigation.md §2 B` 180MB squashfs）提供干跑证据；与 `phase1_gates.sh ldd` 分工不同 — 前者为分发预算/不捆回收验证，后者为宿主回归。
**Change Necessity:** 非代码路径不足 — 仅文档无法让 `bash build_appimage.sh --check` 在无网络机器上绿；最小边界为脚本 `--check` 分支加固（加 `grep -q "OK: no libSDL"` 等关键词断言 + `WGPU_BACKEND` unset 校验 + 预算 220MB 核算），不动 `--build` 真打包逻辑。
**Impact/Compat:** `--check` 仅 `echo` + `ldd` + `du` + `grep`，零网络、无写盘、无提权；`--build` 保持 `exit 2` stub（opt-in）；改动不触 `host/renpy-host/src/*.rs`，双树不变量不增风险。
**Verification:** `bash host/scripts/build_appimage.sh --check 2>&1 | tee /tmp/appimage_check.log; grep -q "OK: no libSDL" /tmp/appimage_check.log && grep -q "OK: libpython3 present\|OK: libpython" /tmp/appimage_check.log && grep -q "backend=Vulkan\|backend: Vulkan" /tmp/appimage_check.log; echo $?` 为 0；`grep -q "build_appimage.*--check" host/README.md` 绿；`host.yml` 含对应 step 或本地等效日志。

**Steps:**

1. **读现有脚本探针（1 min）** — 确认 7 探针已齐：
   ```bash
   grep -n "MODE.*check\|OK: no libSDL\|libpython\|WGPU_BACKEND\|backend=Vulkan\|size budget\|recovered_project" host/scripts/build_appimage.sh | head -n 30
   cat host/scripts/build_appimage.sh | head -n 60
   grep -n "build_appimage" host/README.md; grep -n "build_appimage" doc/packaging-investigation.md | head
   grep -n "build_appimage\|verify-ldd\|verify-phase1" .github/workflows/host.yml | head -n 20
   ```
   预期：脚本已含 7 节 `echo` 探针（见 `build_appimage.sh:48-151`），`host.yml` 已有 `ldd no SDL` 与 `phase1_gates Vulkan` 但缺独立 `AppImage --check` step（可选增）。

2. **硬化 --check 关键词与零网络 guarantee（2 min）** — 确保日志含合同关键词且分支内无网络出口：
   ```bash
   # 检查 --check 分支内是否含 curl/wget/appimagetool 下载（必须无）
   awk '/MODE.*check/,/OK: no libSDL/' host/scripts/build_appimage.sh | grep -iE "curl|wget|appimagetool|git clone" && echo "FAIL: network in --check" || echo "OK: no network in --check"
   # 确保 7 个 OK 关键词存在（合同要求）
   grep -c "OK: no libSDL" host/scripts/build_appimage.sh; grep -c "OK: libpython" host/scripts/build_appimage.sh
   grep -c "OK: WGPU_BACKEND unset" host/scripts/build_appimage.sh; grep -c "backend: Vulkan\|backend=Vulkan" host/scripts/build_appimage.sh
   grep -c "OK: size budget" host/scripts/build_appimage.sh; grep -c "OK: no recovered_project" host/scripts/build_appimage.sh

   # 若缺任意 OK，则补（edit-in-place，示例 patch 手段）：
   # read host/scripts/build_appimage.sh:122-135，确认 SIZE_BUDGET_MB=220 与 TOTAL_MB 计算完整
   # 若 WGPU_BACKEND 分支缺，则补：
   # if [[ -n "${WGPU_BACKEND:-}" ]]; then echo "FAIL: WGPU_BACKEND must be unset"; exit 1; fi
   ```
   预期：6 个 `OK:` 计数均 ≥1，`--check` 分支无网络命令；否则按注释补一行 `echo "OK: ..."` 并 `cd host && cargo check` 不需（脚本无 Rust）。

3. **实跑 --check 干跑（2 min）** — 离线绿：
   ```bash
   bash host/scripts/build_appimage.sh --check 2>&1 | tee /tmp/appimage_check.log
   echo "exit=$?"
   grep -q "OK: no libSDL" /tmp/appimage_check.log && echo "OK no SDL"
   grep -q "OK: libpython3 present\|OK: libpython" /tmp/appimage_check.log && echo "OK libpython"
   grep -q "OK: WGPU_BACKEND unset" /tmp/appimage_check.log && echo "OK WGPU_BACKEND"
   grep -q "backend=Vulkan\|backend: Vulkan" /tmp/appimage_check.log && echo "OK Vulkan probe"
   grep -q "OK: size budget" /tmp/appimage_check.log && echo "OK size"
   grep -q "OK: no recovered_project" /tmp/appimage_check.log && echo "OK no recovered"
   cat /tmp/appimage_check.log | head -n 40
   # 体积预算核算（合同：binary + renpy/wgpu + host/python < 220MB）
   du -sk host/target/release/renpy-host 2>/dev/null | cut -f1 | xargs -I{} echo "binary KB: {}"
   ```
   预期：`exit 0`，5 个 `OK` 全绿，`total MB < 220`；`ldd` 段 `tee /tmp/appimage_check_ldd.txt` 无 `libSDL`。

4. **文档与 CI 同步（1 min）** — 命令可复现：
   ```bash
   grep -q "build_appimage.*--check" host/README.md && echo "README OK" || echo "README need patch"
   grep -q "build_appimage.*--check" doc/packaging-investigation.md && echo "packaging doc OK" || echo "doc need patch"
   # 若缺，则在 host/README.md §9 末尾追加：
   # ```bash
   # bash host/scripts/build_appimage.sh --check  # 离线干跑：ldd/Vulkan/libpython/预算
   # ```
   # 若 host.yml 缺独立 step，则增（不改 Tier1 语义，仅追加）：
   grep -n "build_appimage" .github/workflows/host.yml || echo "host.yml: add step 'AppImage --check (dry-run, no network)' running build_appimage.sh --check"
   ```
   预期：`README OK` + `doc OK`；`host.yml` 如增 step 则 `grep -q "build_appimage" host.yml` 绿，否则本地 `/tmp/appimage_check.log` 即为等效证据（Goal §5 允许本地等效日志）。

---

### T3 — E1 sdist 清单校验：`build_sdist_manifest.sh --check`

**Files:** `host/scripts/build_sdist_manifest.sh`（改，硬化 `--check`） + `pyproject.toml` / `setup.py`（探针校验，不改 `packages=sdl3` 落地） + `host/python/host_pygame/*` + `renpy/wgpu/*`（清单源）
**Why:** `doc/packaging-investigation.md §3 C` 明确 `renpy-build` 在 Phase 9 前不碰 `setup.py host extra` 真改，但需干跑证明 `host/python 164 + renpy/wgpu 19` 已可纳入 sdist，且 `RENPY_HOST_BUILD=1` 时 `cython(packages=sdl3)` 硬失败路径可验证（`setup.py:39-41 HOST_BUILD + 80-90 cython packages=sdl* guard`）。
**Change Necessity:** 非代码路径不足 — 仅 `pyproject.toml` 配置无法证明 `tar tzf` 清单含 `host/python` 且排 `sdl3`；最小边界为脚本加 `find` 计数 + `grep host/python|renpy/wgpu` + `python -m build --sdist --dry-run` probe（无网络时跳过 build，仅文件计数保底）。
**Impact/Compat:** `--check` 仅 `find/ls/grep/tar tzf` 只读扫描，零网络；不改 `setup.py HOST_ALLOW` 列表与 `pyproject.toml packages`，`RENPY_HOST_BUILD=1` 门禁仅探针校验，不落地真 sdist 发布。
**Verification:** `bash host/scripts/build_sdist_manifest.sh --check 2>&1 | tee /tmp/sdist_check.log; grep -q "host/python" /tmp/sdist_check.log && grep -q "renpy/wgpu" /tmp/sdist_check.log && grep -q "OK: host/python" /tmp/sdist_check.log; echo $?` 为 0；`grep -q "RENPY_HOST_BUILD" doc/strip-phase9-inventory.md` 仍绿；`host.yml` artifact 含 `host/python` 如增则核对。

**Steps:**

1. **读现有 sdist 探针（1 min）** — 确认 5 节探针：
   ```bash
   grep -n "host/python\|renpy/wgpu\|RENPY_HOST_BUILD\|sdl3\|per-file-ignores\|artifact naming" host/scripts/build_sdist_manifest.sh | head -n 30
   cat host/scripts/build_sdist_manifest.sh | head -n 80
   grep -n "HOST_BUILD\|HOST_ALLOW\|cython.*packages.*sdl" setup.py | head -n 20
   grep -n "host/python" pyproject.toml | head
   ls host/python/host_pygame/ | head -n 20; ls renpy/wgpu/ | head -n 20
   ```
   预期：脚本含 `SHIM_COUNT + WG_COUNT + sdl3 gating + per-file-ignores + artifact naming` 5 节（`build_sdist_manifest.sh:43-166`），`setup.py:39-41` `HOST_BUILD` 判别仍在。

2. **硬化清单阈值与 sdl3 排除探针（2 min）** — 确保 fail-closed：
   ```bash
   # 阈值：host/python .py 至少 5（含 host_pygame 20 垫片与 _renpy_host.py），renpy/wgpu 至少 19
   grep -n "SHIM_COUNT.*-lt 5\|WG_COUNT.*-lt" host/scripts/build_sdist_manifest.sh | head
   # 必含件：
   grep -n "host/python/host_pygame/event.py\|host/python/_renpy_host.py\|renpy/wgpu/draw.py" host/scripts/build_sdist_manifest.sh | head
   # sdl3 gating probe（pyproject.toml 含 sdl3 时提示、setup.py 含 HOST_BUILD 时提示）
   grep -n "grep -q.*sdl3.*pyproject" host/scripts/build_sdist_manifest.sh
   grep -n 'if \[ -f "\$ROOT/setup.py"' host/scripts/build_sdist_manifest.sh
   # 若阈值过松，则收紧为 SHIM_COUNT<20 即 FAIL（164 需 >>5），但本期保持探测为主，不改 CI 硬门槛
   echo "probe thresholds OK"
   ```
   预期：阈值与必含件 `grep` 均命中；否则按注释补 `for req in ...` 循环（`build_sdist_manifest.sh:53-77` 已有）。

3. **实跑 --check 干跑（2 min）** — 离线绿：
   ```bash
   bash host/scripts/build_sdist_manifest.sh --check 2>&1 | tee /tmp/sdist_check.log
   echo "exit=$?"
   grep -q "host/python" /tmp/sdist_check.log && echo "OK host/python in log"
   grep -q "renpy/wgpu" /tmp/sdist_check.log && echo "OK renpy/wgpu in log"
   grep -q "OK: host/python\|host/python .py count" /tmp/sdist_check.log && echo "OK shim count"
   grep -q "OK: renpy/wgpu\|renpy/wgpu .py count" /tmp/sdist_check.log && echo "OK wgpu count"
   grep -q "sdl3 gating\|per-file-ignores" /tmp/sdist_check.log && echo "OK sdl3 probe"
   grep -q "renpy-host.*bc160-measured\|Expected bundle name" /tmp/sdist_check.log && echo "OK naming probe"
   cat /tmp/sdist_check.log | tail -n 30
   ```
   预期：`exit 0`，4 个 `OK` 全绿，日志含 `host/python .py count: 164` 级别与 `renpy/wgpu .py count: 19` 级别数量（允许 ±5 浮动，仅阈值 fail-closed）。

4. **RENPY_HOST_BUILD=1 排 sdl3 路径验证（1 min）** — 探针级，不真 build：
   ```bash
   # 只验证 cython() guard 文本，不真编译（避免拉 SDL 链）
   grep -n 'packages.*sdl3\|cython.*packages' setup.py | head -n 20
   grep -n 'if HOST_BUILD' setup.py | head
   RENPY_HOST_BUILD=1 python3 -c "import setup; print('HOST_BUILD probe:', setup.HOST_BUILD)" 2>&1 | head -n 20 || echo "setup.py HOST_BUILD import probe (may need setuplib mock, text grep is enough)"
   # 合同允许：sdist 的 sdl3 排除路径可验证（文本探针即满足），不要求真 python -m build
   echo "OK sdl3 exclusion probe done (text)"
   ```
   预期：`setup.py:39 HOST_BUILD = os.environ.get("RENPY_HOST_BUILD", "") in (...)` 且 `cython()` 内有 `if HOST_BUILD and "sdl" in packages: raise` 守卫；否则补 guard（但当前已满足）。

5. **文档同步（30s）** — 同 T2 合并验证：
   ```bash
   grep -q "build_sdist_manifest.*--check" host/README.md && echo "README sdist OK" || echo "README sdist need patch"
   grep -q "build_sdist_manifest.*--check" doc/packaging-investigation.md && echo "packaging doc sdist OK" || echo "packaging doc need patch"
   ```

---

### T4 — Phase 9 剥离清单与迁移开关：`doc/strip-phase9-inventory.md` 三维扫描（不真删 guard）

**Files:** `doc/strip-phase9-inventory.md`（刷新到 HEAD `c5641f8`） + `host/scripts/build_sdist_manifest.sh`（复用三维扫描探针） + `host/scripts/build_appimage.sh`（复用 ldd 维） + `host/scripts/phase9_gates.sh`（回归） + `setup.py:39-66` + `renpy/display/core.py:16 sites host_build 分支`（扫描对象，不改）
**Why:** Phase 9 真删（`renpy/gl2 4 so + renpy/pygame/*.pyx 10+`）破坏面大，需先有可审计的**三维 kill-list**：`ldd` 维（`nm -D` 无 SDL） / `host_build import branch` 维（`renpy/display/core.py 16 sites + gl2shadercache 软 stub`） / `setup.py packages=sdl3` 维（`HOST_BUILD=1` 仅 Class A）；清单为 `renpy-build` 从 sdl3 假设迁出的唯一依据（`packaging-investigation.md §3 C`）。
**Change Necessity:** 非代码路径不足 — 仅口头约定无法让 `grep -q "libSDL" doc/strip-phase9-inventory.md && grep -q "renpy/gl2" && grep -q "RENPY_HOST_BUILD"` 绿；最小边界为刷新清单 Header 到 HEAD `c5641f8` + 补三维扫描小节的 `bash` 探针输出（`ldd` 10 行 + `grep -rn host_build renpy/display/core.py` 16 行 + `grep packages=sdl3 setup.py` 8 行），不 `rm` 任何源码。
**Impact/Compat:** 只读扫描 + 生成 `doc/strip-phase9-inventory.md`，`ldd`/`grep` 不触编译；`setup.py HOST_BUILD` 开关仅文档化迁移路径（`RENPY_HOST_BUILD=1` 时 `cython(packages=sdl3)` 硬失败），不落地 `setup.py host extra` 真改；双树不变量与 `phase9_gates 8/8` 保持。
**Verification:** `grep -q "libSDL" doc/strip-phase9-inventory.md && grep -q "renpy/gl2" doc/strip-phase9-inventory.md && grep -q "RENPY_HOST_BUILD" doc/strip-phase9-inventory.md && grep -q "host/python" doc/strip-phase9-inventory.md; echo $?` 为 0；`bash host/scripts/build_appimage.sh --check` 仍绿；`bash host/scripts/build_sdist_manifest.sh --check` 仍绿；`bash host/scripts/phase9_gates.sh` 在 HEAD 上绿（或 `phase9_gates.sh --dry-run` 等效只读）；`host.yml` artifact 核对 `strip-phase9-inventory.md` 已纳入（可选）。

**Steps:**

1. **三维扫描只读产出（3 min）** — 不落删，只产清单行：
   ```bash
   # 1) ldd 维：当前 host 产物 13MB，无 SDL
   ldd host/target/release/renpy-host 2>&1 | tee /tmp/strip_ldd.txt
   cat /tmp/strip_ldd.txt | head -n 20
   ! grep -qi "libSDL" /tmp/strip_ldd.txt && echo "OK ldd no SDL" || echo "FAIL ldd"
   nm -D host/target/release/renpy-host 2>&1 | grep -i SDL | head || echo "OK nm -D no SDL"

   # 2) import 维：host_build 分支 16 sites
   grep -rn "host_build" renpy/display/core.py 2>&1 | tee /tmp/strip_import_core.txt; wc -l /tmp/strip_import_core.txt
   grep -n "host_build" renpy/__init__.py 2>&1 | tee /tmp/strip_import_init.txt; cat /tmp/strip_import_init.txt
   grep -n "register_wgsl_shader\|HostShaderPart\|host_glsl_stub" renpy/gl2/gl2shadercache.py 2>&1 | tee /tmp/strip_import_gl2.txt; cat /tmp/strip_import_gl2.txt
   grep -rn "from renpy\.gl2\|import.*gl2" renpy --include="*.py" 2>&1 | head -n 20 | tee /tmp/strip_import_gl2_refs.txt

   # 3) setup.py 维：packages=sdl3
   grep -n "HOST_BUILD\|HOST_ALLOW\|packages.*sdl" setup.py 2>&1 | tee /tmp/strip_setup.txt; cat /tmp/strip_setup.txt
   grep -n "per-file-ignores" pyproject.toml | tee /tmp/strip_pyproject.txt

   # 4) host/python 与 renpy/wgpu 存量（清单 Size budget 用）
   du -sk host/target/release/renpy-host renpy/wgpu host/python 2>&1 | tee /tmp/strip_size.txt; cat /tmp/strip_size.txt
   find host/python -type f -name "*.py" | wc -l | tee /tmp/strip_hostpy_count.txt
   find renpy/wgpu -type f -name "*.py" | wc -l | tee /tmp/strip_wgpu_count.txt
   ```
   预期：`OK ldd no SDL` + `nm -D` 空 + `host_build` 16 行（±2 浮动，见 `strip-phase9-inventory.md:33` 表） + `setup.py HOST_BUILD` 硬失败 guard 存在。

2. **刷新 `doc/strip-phase9-inventory.md` 到 HEAD（2 min）** — Header + 三维小节：
   ```bash
   head -n 10 doc/strip-phase9-inventory.md
   # 将 Header 从 `78b21d7b4` 更新到 HEAD：
   REV=$(git rev-parse HEAD); SHORT=$(git rev-parse --short HEAD); DATE=$(date -u +%Y-%m-%d)
   # 编辑 doc/strip-phase9-inventory.md:3-6 行：
   # > Scope: **Read-only inventory** at HEAD `c5641f8` — no files deleted ...
   # Status: `DRAFT` — generated by `... --check` + manual scan on 2026-08-27 HEAD `c5641f8`.
   # 并在 §1 ldd 段追加本次 /tmp/strip_ldd.txt 快照（5 行），§2 import 段追加 renpy/display/core.py 16 sites 行号（来自 /tmp/strip_import_core.txt），§3 setup.py 段追加 HOST_BUILD guard 行
   # 且在 §5 Kill-list 新增一行：`Category D — doc kill: doc/packaging-investigation.md §5 与 host/README.md §9 的 --check 命令已同步`
   cat doc/strip-phase9-inventory.md | head -n 50
   ```
   预期：文件 Header `HEAD c5641f8` 且 `grep -q "c5641f8\|$(git rev-parse --short HEAD)" doc/strip-phase9-inventory.md` 绿；`grep -q "ldd-linked" doc/strip-phase9-inventory.md` 等三类关键词仍绿。

3. **迁移开关文档化（1 min）** — 不真删 guard，写清开关语义：
   ```bash
   # 在 doc/strip-phase9-inventory.md §5 末尾或新增 §6 Migration Switch 小节，写：
   # - `RENPY_HOST_BUILD=1` 时 `setup.py cython(packages=sdl3)` 硬失败（setup.py:80-90 guard）
   # - Phase 9 真删前需满足：`host.yml Tier1+2` 绿 + `phase9_gates.sh 8/8` 绿 + `strip-phase9-inventory.md` 三维扫描绿 + `sdist --check` 绿
   # - 真删执行者需 `git rm renpy/gl2/*.pyx renpy/pygame/*.pyx` 并 `rm` 后再跑一轮 `phase9_gates.sh` 与 `run_golden_tests.sh`，任一 fail 即 revert
   grep -n "Migration\|RENPY_HOST_BUILD.*guard\|真删" doc/strip-phase9-inventory.md | head -n 20
   ```
   预期：`Migration Switch` 小节存在，含 `RENPY_HOST_BUILD=1` 与四门禁前置。

4. **干跑回归（2 min）** — 清单刷新后宿主门禁仍绿：
   ```bash
   grep -q "libSDL" doc/strip-phase9-inventory.md && echo "OK inventory libSDL"
   grep -q "renpy/gl2" doc/strip-phase9-inventory.md && echo "OK inventory gl2"
   grep -q "RENPY_HOST_BUILD" doc/strip-phase9-inventory.md && echo "OK inventory RENPY_HOST_BUILD"
   grep -q "host/python" doc/strip-phase9-inventory.md && echo "OK inventory host/python"

   bash host/scripts/build_appimage.sh --check 2>&1 | tee /tmp/appimage_check_t4.log; grep -q "OK: no libSDL" /tmp/appimage_check_t4.log && echo "OK appimage still green"
   bash host/scripts/build_sdist_manifest.sh --check 2>&1 | tee /tmp/sdist_check_t4.log; grep -q "OK: host/python" /tmp/sdist_check_t4.log && echo "OK sdist still green"

   # Phase 9 8/8 回归（优先轻量 probe，若环境无 Vulkan 显存则允许走 cached log）：
   bash host/scripts/phase9_gates.sh 2>&1 | tee /tmp/phase9_t4.log; grep -q "all Phase 9 host gates" /tmp/phase9_t4.log && echo "OK phase9 8/8" || echo "phase9 need Vulkan env (check host/target/verify-phase1.log)"
   # dry-run variant（如脚本支持）：
   # bash host/scripts/phase9_gates.sh --dry-run 2>&1 | tee /tmp/phase9_dry.log || true
   ```
   预期：四行 `OK inventory` + 两个 `--check OK` + `phase9 8/8`（或 cached `verify-phase1.log backend=Vulkan` 等效）；任一 fail 则停，记为 blocker 不在此期修（`goal-wgpu-e0-e1-packaging.md Phase 3 Stop condition`）。

---

## 4. Risks & Mitigations

| # | 风险 | 影响 | 缓解（本 Milestone 内） |
|---|------|------|------------------------|
| R1 | `evidence_revision` 重签后 `G02/G06` 单点辞典漂移（单点 MAE 阈 `max≤16` 更严）导致 `golden 8/8` 回归 fail | T1 刚重签即 fail，阻塞 E1 | T1 Stop condition：任一证据重签后 `run_golden_tests.sh` fail → 停，查 `G02/G06` 辞典是否需再签（`host/scripts/runner/parent_runner.py 6-field envelope` 仍为 verifier），不强行改 `golden_mae` 阈 |
| R2 | `sphinx-build` 因 `renpy.versions` 未装或 `locale` 缺失而破 | E0 卡住 | `sphinx/source/conf.py:21-35` 已有 `try: import renpy except Exception: public_release_version="0.0.0"` 分支；`sphinx-build -b html` 干跑允许 `WARNING` 仅 `ERROR` 判 fail |
| R3 | `build_appimage.sh --check` 在 CI 无 `host/target/release/renpy-host` 时找不到二进制 | E1 卡住 | 脚本已含 `BIN_RELEASE` → `BIN_DEBUG` fallback（`build_appimage.sh:53-60`），并 `du -k` 体积探针兼容双路径；CI 前置 `cargo build -p renpy-host --release`（`host.yml:59-64`） |
| R4 | `build_sdist_manifest.sh --check` 需 `python -m build` 但 runner 未装 | E1 卡住 | 脚本 `if python3 -m build --help | grep sdist` 分支，不存在则仅走 `find` 计数与 `tar tzf` 跳过保底（`build_sdist_manifest.sh:100-148`），不判 fail |
| R5 | `phase9_gates.sh` 需 Vulkan 显存，headless CI 上 fail | T4 回归误判 | 允许 `host/target/verify-phase1.log` 含 `backend=Vulkan + OK: no libSDL` 的 cached 证据等效；或 `RENPY_HOST_HEADLESS=1` 时跳过 present，仅验 `ldd + 8/8 via lavapipe` |
| R6 | `setup.py HOST_ALLOW` 列表与 `renpy/wgpu` 新增文件不同步导致 sdist 清单漂移 | T3 清单漏件 | `build_sdist_manifest.sh --check` 内 `find` 计数探针 fail-closed（`<5` 即 fail，实际 164/19 远大于阈），漂移即红 |

---

## 5. Retirement / Follow-ups（本 Milestone 不执行，仅文档化触发器）

- **Phase 9 真删触发器（需另起 plan）：** `RENPY_HOST_BUILD=1` 默认、`sdist --check` 连续 3 次绿、`strip-phase9-inventory.md` 三维扫描在 HEAD 上绿、`host.yml Tier1+2` 全绿后，方可 `git rm renpy/gl2/gl2draw.pyx renpy/pygame/*.pyx ...`；真删后需单开 `Phase 9 strip 真删 plan` 并附 `anti-entropy-governance` 的 `Migration Confirm`（`using-aegis/references/anti-entropy-governance.md`）。
- **真 AppImage 打包触发器：** `build_appimage.sh --check` 体积预算 `<220MB` 稳定、`bc160 1% low 830` 稳定、`host.yml` 新增 `appimagetool` 缓存 step 后，方可 `--build` 产出 squashfs 并签名（opt-in，不在本 Milestone CI）。
- **真发版触发器：** `release_acceptance evidence_revision==HEAD` + `host.yml artifacts` 已归档（`host/target/verify-*.log + envelopes + bc160 + release_acceptance`）+ `sphinx 8.99.99` 可访问后，方可 `git tag wgpu-host-v0.6.0` 并 `twine upload --dry-run`。
- **旧 owner 保留：** `doc/packaging-investigation.md §2 C renpy-build` 仍保留至真删后；`composer_fallback.py` 如有则一版本兼容期。

---

## 6. Acceptance Criteria（本 Milestone 可勾）

- [ ] **AC-E0-1:** `jq --arg rev $(git rev-parse HEAD) '.evidence_revision == $rev' .omc/artifacts/release_acceptance.v1.json && jq --arg rev $(git rev-parse HEAD) '.evidence_revision == $rev' .omc/artifacts/product_acceptance.v1.json && jq --arg rev $(git rev-parse HEAD) '.evidence_revision == $rev' host/target/bc160_perf_metrics.json` 三行 0 退出
- [ ] **AC-E0-2:** `sphinx-build -b html sphinx/source /tmp/sphinx_out` 0 退出且 `test -f /tmp/sphinx_out/index.html`
- [ ] **AC-E0-3:** `ruff check renpy/wgpu` 与 `ruff check host/python/gates` 均为 `All checks passed`，且 `host/target/verify-ruff.log` 同绿
- [ ] **AC-E1-1:** `bash host/scripts/build_appimage.sh --check` 0 退出且日志含 `OK: no libSDL*` + `OK: libpython3 present` + `OK: WGPU_BACKEND unset` + `backend=Vulkan` + `OK: size budget` 五关键词
- [ ] **AC-E1-2:** `bash host/scripts/build_sdist_manifest.sh --check` 0 退出且日志含 `host/python` + `renpy/wgpu` + `OK: host/python` + `OK: renpy/wgpu`
- [ ] **AC-E1-3:** `grep -q "build_appimage.*--check" host/README.md && grep -q "build_sdist_manifest.*--check" host/README.md` 绿；`host.yml` 含对应 step 或本地 `/tmp/*_check.log` 等效
- [ ] **AC-P9-1:** `grep -q "libSDL" doc/strip-phase9-inventory.md && grep -q "renpy/gl2" doc/strip-phase9-inventory.md && grep -q "RENPY_HOST_BUILD" doc/strip-phase9-inventory.md && grep -q "host/python" doc/strip-phase9-inventory.md` 全绿，且 Header `HEAD c5641f8` 已刷新
- [ ] **AC-P9-2:** `bash host/scripts/phase9_gates.sh` 在 HEAD 上 `all Phase 9 host gates + goldens + ldd passed`（或 cached `host/target/verify-phase1.log backend=Vulkan` 等效）
- [ ] **AC-INV-1:** `ldd host/target/release/renpy-host | grep -qi libSDL` 为空且 `grep -q "backend=Vulkan" host/target/verify-phase1.log` 绿（双树不变量）
- [ ] **AC-INV-2:** `host.yml` artifact 列表含 `host/target/verify-*.log + host/target/envelopes/*.json + host/target/bc160_perf_metrics.json + .omc/artifacts/release_acceptance.v1.json`（`host.yml:82-85`）
- [ ] **AC-TDD-1:** 本 plan `TDD Route: skipped` 已记录， tasks 无 `Write failing test / Verify RED` 子步，仅 post-change regression

---

## 7. Boundary Matrix（本 Milestone 专属）

| 边界 | 本 Milestone 行为 | 推迟/Opt-in |
|------|------------------|------------|
| 真 `AppImage` 打包 + `appimagetool` 下载 | 不做 | `bash host/scripts/build_appimage.sh --build` opt-in，需网络与 squashfs 写盘 |
| 真 `sdist` 发布 + `twine upload` | 不做 | `python -m build --sdist && twine upload --dry-run` opt-in |
| 真 `renpy/gl2` / `renpy/pygame/*.pyx` 源码删除 | 不做 | 另起 Phase 9 真删 plan，需 `anti-entropy-governance` 确认 |
| `setup.py host extra` 落地（packages=sdl3 真排除） | 不做 | 仅 `setup.py:39-41 HOST_BUILD` 探针校验，落地推至 Phase 9 真删后 |
| `host/renpy-host/src/*.rs` 渲染管线 | 不改 | 仅校验脚本只读探针，需 Phase 负责人确认方可新增 |
| `sphinx/source/changelog.rst 8.99.99` 文案重写 | 不做 | 仅 `sphinx-build` 校验 |

---

## 8. Verification Checklist（按 Task 收口，exact cmd）

### T1 — E0 证据封口（收口时逐行贴日志）

```bash
REV=$(git rev-parse HEAD); echo $REV
jq -e --arg rev "$REV" '.evidence_revision == $rev' .omc/artifacts/release_acceptance.v1.json && echo "PASS T1-evidence"
sphinx-build -b html sphinx/source /tmp/sphinx_out 2>&1 | tail -n 20; echo "exit $?"
ruff check renpy/wgpu 2>&1 | tail -n 5; ruff check host/python/gates 2>&1 | tail -n 5
```

### T2 — E1 AppImage --check

```bash
bash host/scripts/build_appimage.sh --check 2>&1 | tee /tmp/appimage_check.log; echo "exit $?"
grep -E "OK: no libSDL|OK: libpython|backend=Vulkan|OK: size budget" /tmp/appimage_check.log
grep -q "build_appimage.*--check" host/README.md && echo "PASS T2-doc"
grep -q "build_appimage" .github/workflows/host.yml && echo "PASS T2-host.yml" || echo "T2 host.yml: local log is equivalent (goal §5)"
```

### T3 — E1 sdist 清单校验

```bash
bash host/scripts/build_sdist_manifest.sh --check 2>&1 | tee /tmp/sdist_check.log; echo "exit $?"
grep -E "host/python|renpy/wgpu|OK: host/python|OK: renpy/wgpu" /tmp/sdist_check.log | head -n 20
grep -q "RENPY_HOST_BUILD" setup.py && echo "PASS T3-HOST_BUILD guard"
```

### T4 — Phase 9 清单与迁移开关

```bash
grep -q "libSDL" doc/strip-phase9-inventory.md && grep -q "renpy/gl2" doc/strip-phase9-inventory.md && grep -q "RENPY_HOST_BUILD" doc/strip-phase9-inventory.md && echo "PASS T4-inventory"
bash host/scripts/build_appimage.sh --check 2>&1 | grep -q "OK: no libSDL" && echo "PASS T4-ldd"
bash host/scripts/build_sdist_manifest.sh --check 2>&1 | grep -q "OK: host/python" && echo "PASS T4-sdist"
bash host/scripts/phase9_gates.sh 2>&1 | tail -n 20; grep -q "all Phase 9 host gates" host/target/verify-phase1.log 2>&1 | head || true
# 合同 exact 形（Phase 9 门禁）：
bash host/scripts/phase9_gates.sh 2>&1 | grep -q "all Phase 9 host gates"
```

### host.yml artifact 核对（Milestone 级）

```bash
grep -q "host-verify-logs" .github/workflows/host.yml && grep -q "verify-.*log" .github/workflows/host.yml && echo "PASS host.yml artifacts"
ls -lh host/target/verify-*.log .omc/artifacts/release_acceptance.v1.json host/target/bc160_perf_metrics.json 2>&1 | head -n 20
cat .omc/artifacts/release_acceptance.v1.json | jq '{evidence_revision, verdict, tier1_host, tier2_golden: {passed, envelopes}}'
```

---

## 9. Execution Readiness View（Milestone 4 专属，交付前必读）

```text
Execution Readiness View:
- Intent Lock: E0 证据自洽（evidence_revision==HEAD）+ sphinx/ruff 基线 + E1 --check 离线绿 + Phase 9 清单三维可审计，不含真打包/真发/真删
- Scope Fence: Files 表 9 文件可改；禁动 renpy/gl2 SDL 树、host/renpy-host/src 渲染管线、SWAPCHAIN_FORMAT Rgba8Unorm、WgslShaderCache key 熵
- Baseline Lock: HEAD c5641f8 + host.yml Tier1+2 + bc160 2152fps MEASURED + 8/8 MAE≤2/255 + ldd 空 + backend Vulkan
- Approved Behavior: 像素等价（G01-08 0 回归），分发产物命名可复现，清单含 ldd/import/setup.py 三类 kill-list 且 host.yml 仍绿
- Owner / Contract Constraints: build_release_acceptance.py 唯一写 evidence；host.yml 唯一 CI 门禁；packaging-investigation.md 唯一分发决策
- Compatibility Boundary: --check 干跑离线零网络（无 curl/wget/appimagetool）；真打包/签名/发版为 opt-in（需显式 --build）
- Retirement Boundary: 无旧路径删除；strip-phase9-inventory.md 刷新可 revert，setup.py sdl3 排除仅探针
- Task Batches: B1 T1（E0）→ B2 T2/T3（E1，分串行，依赖 B1）→ B3 T4（Phase 9，依赖 B2）
- Test Obligations: ruff 2× + sphinx + --check 2× + phase9_gates 8/8 + ldd + backend + host.yml artifact 7 组；无新增单测（TDD off）
- Review Gates: cargo fmt --check + cargo check -D warnings + ruff + host.yml artifact 核对
- Drift / Rewind Rules: 单 Task 可 git revert；B1 fail 即全 plan pause；B2 fail 则 T4 不启动
- Evidence Required Before Completion: §0 Verification 7 组日志 + host/target/verify-*.log + /tmp/sphinx.log + /tmp/*_check.log + host.yml 列表
- Advisory Boundary: method-pack 执行指引；非 GateDecision，仅达标前置
```

---

## 10. Open Questions（→ `.omc/plans/open-questions.md`）

- [ ] `sphinx/source/wgsl_shaders.rst` 是否需从 `doc/wgsl_shader_migration.md` 镜像一份以让 `sphinx-build` 的 `toctree` 不 warn？当前 `changelog.rst` 已含 `WGSL migration` 链接，缺镜像仅 warn 不 error，已 defer 为 T1 可选补镜像 — 需 sphinx 负责人确认是否补。
- [ ] `host.yml` 是否新增 `AppImage --check` 与 `sdist --check` 两独立 step，还是保留本地 `/tmp/*_check.log` 等效？`goal-wgpu-e0-e1-packaging.md Phase 2 Exit proof` 允许本地等效日志 — 需 CI 负责人确认 tier 归属。

---

## 11. Self-Review（writing-plans 自检）

- [x] Spec coverage：`goal-wgpu-e0-e1-packaging.md` 三 Phase 的 6 Todos 全有 Task 映射（T1 3条 + T2 1条 + T3 1条 + T4 1条）
- [x] Placeholder scan：无 TBD/TODO/“类似 Task N”；每 Step 含 exact cmd 与 expected output
- [x] Type consistency：`evidence_revision: string == git rev-parse HEAD (40 hex)`，`bc160.average_fps: number`，`ruff: "All checks passed"` 字符串断言，`sphinx-build exit 0: number` 均一致
- [x] Compatibility：双树/Rgba8Unorm/ruff/sphinx/--check 零网络/不真删五边界已标注 falsifier
- [x] Change necessity：每 Task 含 Why + 最小边界（证据/脚本/清单三类，不碰渲染）
- [x] Existence check：无新 owner，仅复用现有脚本与清单的 --check/刷新
- [x] Plan-time complexity：脚本均 <200 行，edit-in-place within-budget
- [x] Architecture integrity：渲染 SSOT 不动，分发仅脚本，无更高层 owner 可替代
- [x] Verification：exact cmd 覆盖 `jq == HEAD / sphinx-build -b html / ruff / build_appimage --check / build_sdist_manifest --check / phase9_gates.sh / host.yml artifact` 7 组
- [x] Dual-track：Repair Track（证据重签）与 Retirement Track（strip 清单不真删）已分
- [x] ADR/baseline-sync：`packaging-investigation.md §3 C` 决策与 `host.yml Tier1+2` 同步信号已保留
- [x] File:line 证据定位：`renpy/wgpu/video.py + arena.rs:2193-2684 + gpu.rs:14` 等仅作证据定位，未捏造 API

---

## 12. 目录+自检（写盘后执行）

```bash
ls -lh docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md
wc -l docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md
grep -c "^### T" docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md  # 预期 4
grep -q "TDD Route" docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md && echo "OK TDD Route"
grep -q "Compatibility Boundary" docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md && echo "OK Compat"
grep -q "Execution Readiness View" docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md && echo "OK ERV"
grep -q "build_appimage.*--check" docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md && echo "OK AppImage --check"
grep -q "build_sdist_manifest.*--check" docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md && echo "OK sdist --check"
grep -q "strip-phase9-inventory" docs/aegis/plans/2026-08-27-milestone-4-packaging-and-strip.md && echo "OK strip inventory"
# 追加到 INDEX（如可用）：
python host/scripts/build_release_acceptance.py --help 2>&1 | head
```

> 下一步：由 coordinator 按 B1→B3 顺序派子代理；每 Task 独立 `cargo fmt --check + ruff + sphinx/--check/phase9` 后单 commit；B1 任意 fail 即全 plan pause。
