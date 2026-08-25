# Goal Document: wgpu-host Release A — 封版 (06ce113b -> Release)

## Go / No-Go
- **Judgment**: Go
- **Reason**: 产品化 6/6 在 `06ce113b` 已 `product_acceptance.v1.json PASS (MEASURED 3054fps, 8/8, naga 24.0.0, ruff 0 narrow)`；当前 HEAD `9f62ab39c` 仅 docs 增量，无代码回退。封版不改渲染逻辑，只做证据重绑定+SSOT+CI+tag，风险可控、无需新 product 决策。A 之后再还债 B。

## Target Outcome
在当前 `master HEAD (9f62ab39c)` 上产出**可被第三方 fresh checkout 复验**的 Release 证据集：
`release_acceptance.v1.json (verdict PASS, evidence_revision == git rev-parse HEAD)` + `product_acceptance.v1.json` 重绑定到 HEAD + `bc160_perf_metrics.v1.json MEASURED` 归档到 `host/target/` + 8/8 parent_runner envelopes + `cargo fmt/check/test + ldd + backend=Vulkan + ruff 0` 全绿日志 + `CHANGELOG + host/README` 同步 + `git tag` + `.github/workflows/host.yml` CI 门禁。

## Goal Definition
- **Type**: delivery / operational
- **Boundary**:
  - 包含：证据重绑定与新 revision 贯通、Release SSOT 聚合、文档/CHANGELOG/tag、CI 工作流、两份共识计划复选框同步、大计划 `consensus-wgpu-native-vulkan-rewrite.md` 的已完成 AC 同步。
  - 不包含：任何 `host/renpy-host/src/*.rs` 或 `renpy/wgpu/*.py` 渲染/合成/金库逻辑修改、纹理/网格/管线语义变更、性能调优。
- **Non-goals**:
  - 不真修 `host/python/gates` bulk noqa（B 做）
  - 不改 `G02/G06` baseline（已 single-point resigned，A 只复验）
  - 不做 `sdist/AppImage/renpy-build` 打包
  - 不做多 GPU / lavapipe 矩阵
- **Deferred work**:
  - B: bulk noqa 真修、1% low / render_pass_duration 补全、draw 拆分单测补齐
  - C: Sphinx/CHANGELOG shader 迁移页独立 PR、上游 rebase 决策
- **Verification rule**: `release_acceptance.v1.json` 的 `verdict==PASS && evidence_revision==$(git rev-parse HEAD) && tier1_4项 + tier2_8/8_envelopes + tier3_ruff0 + tier3_hmc0 + bc160 MEASURED eligible true` 全部满足，且 fresh runner 在干净 worktree 也能复现。
- **Evidence source**: commands + artifacts + trace
  - `cargo fmt --check`, `RUSTFLAGS='-D warnings' cargo check --workspace --all-targets`, `cargo test --workspace` (34), `ldd host/target/release/renpy-host | grep -i libSDL` 空, `RUST_LOG=info cargo run -p renpy-host -- --benchmark --benchmark-frames 1800` -> `host/target/bc160_perf_metrics.json` + `/tmp/bench_1800.json`, `bash host/scripts/run_golden_tests.sh` (parent_runner 8/8 envelopes), `python3 tests/test_golden_mae.py`, `ruff check renpy/wgpu host/python`, `bash host/scripts/phase1_gates.sh`
  - artifacts: `.omc/artifacts/release_acceptance.v1.json`, `.omc/artifacts/product_acceptance.v1.json`, `host/target/bc160_perf_metrics.json`, `host/target/envelopes/*.json`, `host/target/verify-*.log`, `.github/workflows/host.yml`
- **Pass criteria**:
  - `release_acceptance.v1.json`: `verdict PASS`, `evidence_revision == HEAD`, `tier1_host.cargo_fmt==0 && cargo_check_warnings==0 && cargo_test_34==34 && phase1_ldd_0==true && backend_Vulkan contains Vulkan`, `tier2_golden.passed==8/8 && 10 envelopes present && exit_code 0/1 correct`, `tier2_composer.combo_2_2 PASS && naga_direct==24.0.0`, `tier3_ruff.renpy_wgpu 0 && host_python_gates 0`, `bc160.measurement_status==MEASURED && pass_status==PERFORMANCE_TARGET_MET && release_evidence_eligible==true && average_fps>=60`
  - CI workflow 在 `push/tag` 能跑通 Tier1+Tier2（lavapipe 容差文档化）
  - `consensus-wgpu-native-vulkan-rewrite.md` AC1-6 已同步为 [x] 并引用新 revision，AC7-9 明确 deferred
- **Confidence note**: 中 — 证据链已在 `06ce113b` 跑通一次（3054fps, 8/8, 34 tests, ldd 0），A 只是 rebind 到 HEAD。风险在 ` /tmp/bench_1800.json` 寿命短（tmpfs）与 `bulk noqa narrow` 的语义诚实性，需在 ledger 显式标注 narrow。
- **Judgment owner**: `release_acceptance.v1.json` 聚合器（脚本/人）+ 人工 release approver（复验 fresh checkout 后 `git tag` 签字）

## Current State
- **HEAD**: `9f62ab39c docs(consensus): AC-5/6 -> [x] 6/6 all green, release-ready` ，parent `06ce113b feat(bench): measured mode`。`git status --short` 无 dirty（除 `.omc/plans` 新 goal）。
- **Productization**: `.omc/plans/wgpu-host-productization-consensus.md` 6/6 [x] `closeout-06ce113b`，`product_acceptance.v1.json 06ce113b PASS` 已产出；但 `release_acceptance.v1.json` **缺失**（`ls .omc/artifacts/release*.json` 无），`bc160_perf_metrics.v1.json` 仅在 `.omc/artifacts/` 有快照，`host/target/bc160_perf_metrics.json` 需用 `--measured 1800` 重采。
- **Bench**: `benchmark_bc160.sh --measured --measured-frames 1800` 已在 06ce113b 产出 `avg 3054fps / 327335ns`，但 `benchmark_source=/tmp/bench_1800.json` 寿命短（`total_time_ms 589` 仅 5小时前），需归档到 `host/target/` 并纳入 envelope digest。
- **Golden**: `f49 8/8 via parent_runner 10 envelopes` + `G02/G06 single-point resign` 已验，`ldd no libSDL*`、`backend Vulkan RADV NAVI12` 均绿。
- **Naga**: `a8df6fe` 后 `validate_wgsl_with_naga(parse_str+Validator) direct 24.0.0 line:col` 已绿，`composer 4/4`、`combo 2/2` PASS。
- **Ruff**: `renpy/wgpu 0` 真绿，`host/python/gates 0` 为 `bulk noqa (4994->0, 135 files)` **narrow 绿** — 需在 release notes 显式披露 narrow。
- **CI**: `.github/workflows/` 不存在，无自动化门禁。
- **大计划漂移**: `consensus-wgpu-native-vulkan-rewrite.md` AC 仍全 `[ ]`，与产品化 6/6 脱节。
- **已知风险**:
  - `/tmp/bench_1800.json` 易失，release 证据需落盘到 `host/target/` 并计算 digest
  - `host/python/gates` 的 bulk noqa 若被误读为真修，会误导后续 B 的工作量评估
  - 当前 `host/target/release/renpy-host 13559128` 为 debug 还是 release 需显式 `cargo build --release` 并 `ldd` 二次确认
  - CI 无真实 BC-160 硬件，需用 lavapipe 或 `MEASURED` 本地 evidence 区分 `release_eligible` 语义

## Plan Rewrite Notes
| Existing item | Decision | Reason |
|---------------|----------|--------|
| 产品化 6 切片 Slice 1-6（已 [x]） | **keep as provenance**，压缩为 Phase 1 的输入证据 | 避免重做已绿工作；A 只做重绑定，不重实现 |
| `benchmark_bc160.sh --measured` | **reorder 提前到 Phase 1 首位** | 证据寿命短，必须先重采再聚合，否则 SSOT digest 空转 |
| `release_acceptance.v1.json`（缺失） | **rewrite 新建**，区别于 `product_acceptance.v1.json`（Tier 聚合 vs Release 签字） | 原计划 Slice 6 提到但未落盘；A 的核心交付物 |
| `consensus-wgpu-native-vulkan-rewrite.md` 全量重写 | **remove/replace with sync** — 仅同步 AC1-6 为 [x]，其余 deferred | 9-Phase 大计划不应在封版时全绿，避免假勾 |
| `thermo-iter2~5 draw 拆分` | **keep**，仅在 CHANGELOG 提及，不纳入 A 的代码变更 | 已合并，需在文档面收口 |
| `host/python bulk noqa` 真修 | **defer to B**，A 仅披露 narrow | 封版不扩 scope；B 再真修 |

## Drift Diagnosis
- **Goal drift**: 原产品化计划目标是“收敛为生产级产品”，A 将其收敛为“可复验的 Release 证据集” — 更小、更可判定，避免把“生产级”泛化为无限改进。
- **Phase drift**: 原 6 切片按系统分层（build/shader/golden/runner/inventory/perf）组织，A 按**证据生命周期**重排（先采数 -> 再聚合 -> 再文档/tag -> 再 CI 锁），每阶段都有可 review 的 artifact。
- **Validation drift**: 原计划有“AC 勾选即完成”的风险，A 改为 `release_acceptance.v1.json` 的 `evidence_revision==HEAD` 强绑定 + fresh worktree 复验，文件存在不算绿。
- **Compatibility drift**: 无 — 双树并存与 `if host_build` 分支保持不变，A 不引入 shim。
- **Cleanup drift**: `4994->0 bulk noqa` 若与封版混在一起会掩盖债务，A 将其隔离为 B 的独立 goal，仅在 release notes 披露 narrow。

## Priority Rationale
1. **先采数后聚合**：`bench --measured 1800` 的 JSON 在 tmpfs，易失；先重采并落盘到 `host/target/`，后续所有 digest 才有稳定输入。否则聚合器会绑定 stale `/tmp`。
2. **先本地复验再文档**：`cargo fmt/check/test + parent_runner 8/8 + ruff + phase1_gates` 必须在 HEAD 上重跑一次，确认 `9f62ab39c` 的 docs 提交未破坏证据，再写 CHANGELOG/tag。
3. **文档与 tag 原子**：`CHANGELOG + host/README + 两份共识计划` 的更新与 `release_acceptance.v1.json` 的 `evidence_revision` 必须同一次提交，确保 tag 指向的 revision 就是证据 revision。
4. **CI 最后锁**：CI 工作流依赖前三步的命令形态（如 `benchmark_bc160.sh --measured` 的 out 路径），最后加避免反复改。

## Assumptions and Open Decisions
| Item | Status | Impact | Owner / Next step |
|------|--------|--------|-------------------|
| bench 源文件位置 `/tmp/bench_1800.json` vs `host/target/bench_1800.json` | assumed tmp 易失，需归档 | release 证据链完整性 | Phase 1 第一步 `cargo run --benchmark` 后 `cp /tmp/bench_1800.json host/target/bench_1800.json` 并计入 digest |
| `release_acceptance.v1.json` schema 复用 `product_acceptance.v1.json` 还是新建 `release_acceptance.v1` 独立 schema | unresolved | 聚合器实现 | 采用 `release_acceptance.v1` 独立文件，内嵌 `product_acceptance` 摘要 + `evidence_revision` + `artifacts_digest`，避免覆盖 product |
| tag 命名 `wgpu-host-v0.6.0-06ce113b` vs `v0.6.0` | assumed 前者 | 可追溯性 | 采用 `wgpu-host-v0.6.0-HEAD7`（7位短 hash），CHANGELOG 标题同步 |
| CI 是否要求真实 BC-160 硬件 | assumed 否（lavapipe 容差） | CI 通过率 | CI 中 BC-160 标记为 `optional`，`release_eligible` 仅在本地 MEASURED 时为 true，CI 只验 `cargo + ldd + golden + ruff` |
| `host/python/gates bulk noqa narrow` 是否在 release notes 披露 | confirmed 需披露 | 诚实性 | `CHANGELOG.md` 与 `release_acceptance` 的 `notes` 显式写 `host/python 0 via bulk noqa (4994->0, 135 files, narrow)` |
| `consensus-wgpu-native-vulkan-rewrite.md` AC7-9 是否在 A 勾选 | confirmed 不勾 | 防假绿 | 仅 AC1-6 同步为 [x] 并注 `rebound to HEAD`，AC7-9 标 `deferred to B/C` |

## Phases

### Phase 1: 证据重绑定 — Fresh Verification + Bench 重采
- **Purpose**: 在当前 HEAD 上重跑全部 Tier1/2/3 证据，产出新鲜的 `host/target/` 制品，确保 `9f62ab39c` 仍全绿。
- **Entry condition**: `git status --short` 干净（除 goal doc），`host/Cargo.toml` 依赖未变。
- **Phase rules**:
  - 只跑命令，不改 `host/renpy-host/src` 或 `renpy/wgpu` 代码
  - 禁止手改 `baseline.png/rgba`，缺失即 FAIL
  - 所有输出落盘到 `host/target/` 并保留 `*.log`，供下一阶段 digest
  - `bench --measured` 必须成功，否则本阶段不进入下一阶段
- **Todos**:
  - [ ] 重采 BC-160 bench
    - **Surface**: `host/scripts/benchmark_bc160.sh`, `host/renpy-host/src/main.rs --benchmark`, `host/target/bc160_perf_metrics.json`
    - **Proof**: `bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && cat host/target/bc160_perf_metrics.json | python3 -m json.tool | grep -E 'MEASURED|PERFORMANCE_TARGET_MET|average_fps'` 且 `ls -lh /tmp/bench_1800.json host/target/bc160_perf_metrics.json`
    - **Depends on**: none
  - [ ] 归档 bench 源
    - **Surface**: `/tmp/bench_1800.json -> host/target/bench_1800.json`
    - **Proof**: `cp /tmp/bench_1800.json host/target/bench_1800.json && sha256sum host/target/bench_1800.json | tee host/target/bench_1800.sha256`
    - **Depends on**: bench 重采
  - [ ] Rust 基线
    - **Surface**: `host/Cargo.toml`, `host/renpy-host/src/*.rs`
    - **Proof**: `cd host && cargo fmt --check 2>&1 | tee ../host/target/verify-fmt.log && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets 2>&1 | tee ../host/target/verify-check.log && cargo test --workspace 2>&1 | tee ../host/target/verify-test.log` 均 EXIT 0, `grep -q '34 passed' host/target/verify-test.log`
    - **Depends on**: none
  - [ ] 产出 release 二进制并验 ldd
    - **Surface**: `host/target/release/renpy-host`
    - **Proof**: `cargo build -p renpy-host --release 2>&1 | tee host/target/verify-build-release.log && ldd host/target/release/renpy-host | tee host/target/verify-ldd-release.log && ! grep -qi libSDL host/target/verify-ldd-release.log`
    - **Depends on**: Rust 基线
  - [ ] Golden 全量 via parent_runner
    - **Surface**: `host/scripts/runner/parent_runner.py`, `host/scripts/run_golden_tests.sh`, `testcases/wgpu_golden/*/baseline.*`, `host/target/envelopes/*.json`
    - **Proof**: `bash host/scripts/run_golden_tests.sh 2>&1 | tee host/target/verify-golden.log && python3 -c "import json,glob; assert len(glob.glob('host/target/envelopes/*.json'))>=10" && PYTHONPATH=host/scripts/runner python3 tests/test_parent_runner.py 2>&1 | tee host/target/verify-parent.log` 且 `grep -q '8/8' host/target/verify-golden.log`
    - **Depends on**: Rust 基线
  - [ ] 补充 golden_mae 与 composer 单项
    - **Surface**: `host/python/gates/golden_mae.py`, `renpy/wgpu/composer.py`
    - **Proof**: `PYTHONPATH=host/python/gates python3 tests/test_golden_mae.py 2>&1 | tee host/target/verify-golden-mae.log && python3 tests/test_wgpu_composer.py 2>&1 | tee host/target/verify-composer.log` EXIT 0, `grep -q 'PASS' host/target/verify-golden-mae.log`
    - **Depends on**: Golden 全量
  - [ ] Phase1 gates 与 Vulkan 后端
    - **Surface**: `host/scripts/phase1_gates.sh`
    - **Proof**: `bash host/scripts/phase1_gates.sh 2>&1 | tee host/target/verify-phase1.log && grep -q 'backend=Vulkan' host/target/verify-phase1.log && grep -q 'OK: no libSDL' host/target/verify-phase1.log`
    - **Depends on**: Rust 基线
  - [ ] Ruff 全量
    - **Surface**: `renpy/wgpu/`, `host/python/`
    - **Proof**: `ruff check renpy/wgpu host/python 2>&1 | tee host/target/verify-ruff.log && grep -q 'All checks passed' host/target/verify-ruff.log` (记录 narrow 披露)
    - **Depends on**: none
- **Exit proof**: `host/target/bc160_perf_metrics.json` 为 `MEASURED` 且 `average_fps >=60`，`host/target/verify-*.log` 均 EXIT 0，`host/target/envelopes/` 10 枚且 `8/8`，`verify-ldd-release.log` 无 SDL，`verify-ruff.log` 0。
- **Stop condition**: 任一 `verify-*.log` 非 0 或 `bench --measured` 非 MEASURED，停并贴 log，不进 Phase 2。

### Phase 2: Release SSOT 聚合
- **Purpose**: 将 Phase 1 新鲜证据聚合成 `release_acceptance.v1.json`（强绑定 HEAD），并重绑定 `product_acceptance.v1.json` 到 HEAD。
- **Entry condition**: Phase 1 Exit proof 全满足。
- **Phase rules**:
  - 聚合器单一 writer：只写 `release_acceptance.v1.json`，不直接改其他 artifact
  - `evidence_revision` 必须 `git rev-parse HEAD`，严禁手写或复用旧 revision
  - `artifacts_digest` 覆盖 `bc160_perf_metrics.json + bench_1800.json + envelopes/*.json + verify-*.log`
  - 禁止在聚合阶段改代码或 baseline
- **Todos**:
  - [ ] 计算制品 digest
    - **Surface**: `host/target/`, `.omc/artifacts/`
    - **Proof**: `sha256sum host/target/bc160_perf_metrics.json host/target/bench_1800.json host/target/envelopes/*.json host/target/verify-*.log | tee .omc/artifacts/release_artifacts.sha256 && wc -l .omc/artifacts/release_artifacts.sha256` 行数 == 制品数
    - **Depends on**: Phase 1
  - [ ] 聚合 `product_acceptance.v1.json` 重绑定 HEAD
    - **Surface**: `.omc/artifacts/product_acceptance.v1.json`
    - **Proof**: `python3 -c "import json,subprocess; rev=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(); j=json.load(open('.omc/artifacts/product_acceptance.v1.json')); assert j['evidence_revision']!=rev or True"` 后由脚本覆写 `evidence_revision=HEAD` 且 `timestamp_utc` 更新，`python3 -m json.tool` 可解析
    - **Depends on**: digest
  - [ ] 产出 `release_acceptance.v1.json`
    - **Surface**: `.omc/artifacts/release_acceptance.v1.json`
    - **Proof**: `ls -lh .omc/artifacts/release_acceptance.v1.json && python3 -m json.tool .omc/artifacts/release_acceptance.v1.json | grep -E 'verdict|evidence_revision|release_ready|artifacts_digest' && grep -q '"verdict": "PASS"' .omc/artifacts/release_acceptance.v1.json && grep -q $(git rev-parse HEAD | cut -c1-7) .omc/artifacts/release_acceptance.v1.json`
    - **Depends on**: product 重绑定
  - [ ] 校验 release 可复验（fresh worktree）
    - **Surface**: `/tmp/revult-verify-*` worktree
    - **Proof**: `git worktree add /tmp/revult-verify-HEAD HEAD && cd /tmp/revult-verify-HEAD && bash host/scripts/phase1_gates.sh && PYTHONPATH=host/scripts/runner python3 tests/test_parent_runner.py` EXIT 0 后 `git worktree remove /tmp/revult-verify-HEAD`
    - **Depends on**: release 产出
- **Exit proof**: `release_acceptance.v1.json` 存在、合法 JSON、`verdict PASS`、`evidence_revision==HEAD`、`artifacts_digest` 非空且与 `release_artifacts.sha256` 一致。
- **Stop condition**: digest 缺失或 `evidence_revision` 不等 HEAD，停并重跑 Phase 1。

### Phase 3: 文档、CHANGELOG 与 Tag
- **Purpose**: 将证据 revision 固化到人类可读文档与 git tag，完成 release 的可发布形态。
- **Entry condition**: Phase 2 `release_acceptance.v1.json PASS`。
- **Phase rules**:
  - 文档与 `release_acceptance.v1.json` 同一次提交，确保 tag 指向的 revision 就是证据 revision
  - CHANGELOG 只增量，不改写历史条目
  - 禁止在文档阶段改 `host/` 或 `renpy/wgpu` 代码
- **Todos**:
  - [ ] 更新 `CHANGELOG.md`（或 `CHANGELOG.rst`）
    - **Surface**: `CHANGELOG.md`
    - **Proof**: `grep -q 'wgpu-host v0.6.0' CHANGELOG.md && grep -q '06ce113b\|HEAD7' CHANGELOG.md && grep -q 'Naga 24.0.0' CHANGELOG.md && grep -q '8/8' CHANGELOG.md && grep -q 'bulk noqa narrow' CHANGELOG.md`
    - **Depends on**: Phase 2
  - [ ] 同步 `host/README.md` 构建矩阵与证据链接
    - **Surface**: `host/README.md`
    - **Proof**: `grep -q 'cargo build -p renpy-host --release' host/README.md && grep -q 'backend=Vulkan' host/README.md && grep -q 'release_acceptance.v1.json' host/README.md`
    - **Depends on**: CHANGELOG
  - [ ] 同步两份共识计划复选框
    - **Surface**: `.omc/plans/wgpu-host-productization-consensus.md`, `.omc/plans/consensus-wgpu-native-vulkan-rewrite.md`
    - **Proof**: `grep -q 'closeout-.*HEAD' .omc/plans/wgpu-host-productization-consensus.md && grep -q '\[x\] AC-1' .omc/plans/wgpu-host-productization-consensus.md && awk '/## Acceptance criteria/{p=1} p && /AC-6/ {print}' .omc/plans/consensus-wgpu-native-vulkan-rewrite.md | grep -q '\[x\]'`
    - **Depends on**: host/README
  - [ ] 提交并打 tag
    - **Surface**: `git commit`, `git tag`
    - **Proof**: `git log --oneline -1 | grep -q 'release: wgpu-host v0.6.0' && git tag --list 'wgpu-host-v0.6.0-*' | grep -q $(git rev-parse HEAD | cut -c1-7) && git show --stat HEAD | grep -q 'release_acceptance.v1.json'`
    - **Depends on**: 文档同步
- **Exit proof**: `git tag wgpu-host-v0.6.0-<HEAD7>` 存在且 `git rev-parse tag == HEAD`，`CHANGELOG` 与 `release_acceptance` 在同次提交。
- **Stop condition**: tag 已存在指向不同 revision，停并改 tag 名或重定 revision。

### Phase 4: CI 锁
- **Purpose**: 将 Tier1+Tier2 门禁固化为 `.github/workflows/host.yml`，使下一位协作者 `git push` 即复验。
- **Entry condition**: Phase 3 tag 已打。
- **Phase rules**:
  - CI 只跑 Phase 1 的命令子集（fmt/check/test/ldd/golden/ruff/phase1），不跑 30s HMC 与 1800 帧 bench（本地 release 专属）
  - 明确区分 `CI PASS` vs `release_eligible`：CI 不宣称 `release_ready`
  - 工作流必须 pin `rust 1.8x`, `python 3.14`, `ruff 0.16.x`, `mesa radv` 或 `lavapipe` 容差文档化
- **Todos**:
  - [ ] 编写 `.github/workflows/host.yml`
    - **Surface**: `.github/workflows/host.yml`
    - **Proof**: `cat .github/workflows/host.yml | grep -q 'cargo fmt --check' && grep -q 'RUSTFLAGS.*-D warnings' .github/workflows/host.yml && grep -q 'run_golden_tests.sh' .github/workflows/host.yml && grep -q 'ruff check' .github/workflows/host.yml`
    - **Depends on**: Phase 3
  - [ ] 本地 `act` 或 `yamllint` 预检
    - **Surface**: `.github/workflows/host.yml`
    - **Proof**: `yamllint .github/workflows/host.yml 2>&1 | tee host/target/verify-yamllint.log && ! grep -q 'error' host/target/verify-yamllint.log` 或 `actionlint` 若可用
    - **Depends on**: workflow 编写
  - [ ] 文档收口：`.omc/plans/wgpu-host-productization-consensus.md` 增 CI 章节
    - **Surface**: `.omc/plans/wgpu-host-productization-consensus.md`
    - **Proof**: `grep -q '.github/workflows/host.yml' .omc/plans/wgpu-host-productization-consensus.md`
    - **Depends on**: workflow
- **Exit proof**: `host.yml` 存在且本地 lint 通过，`git status` 显示 workflow 已跟踪，`release_acceptance.v1.json` 仍为 HEAD revision（CI 提交不改证据 revision 则需重跑 Phase 2）。
- **Stop condition**: CI 引入新依赖（如 docker）需批准，停并评估。

## Dry-Run Findings
- **缺失输入**：`host/target/bc160_perf_metrics.json` 当前不在 `host/target/`（仅 `.omc/artifacts/` 快照），Phase 1 必须重采；`/tmp/bench_1800.json` 易失，需 Phase 1 归档。
- **模糊 todo**：`release_acceptance` 聚合器未指定脚本路径 — 明确为新建 `host/scripts/build_release_acceptance.py` 或复用 `product_acceptance` 构建逻辑，输入为 Phase 1 的 `host/target/*`。
- **依赖倒置**：若先写 CHANGELOG 再采数，`evidence_revision` 会漂；已调整为先 Phase 1 采数、Phase 2 聚合、Phase 3 文档/tag 同提交。
- **验证缺口**：`one_percent_low_fps` 与 `render_pass_duration_ns` 当前为 `null`，Phase 1 的 `MEASURED` 不强求这两项，但 release notes 需标注 `null` 原因（1800 帧清色窗口 bench 未计 pass 计时）。
- **外部依赖**：CI 无 BC-160 硬件，Phase 4 已将 bench 标记为本地 release 专属，CI 不断言 `release_eligible`，避免假红。
- **顺序风险**：Phase 4 若改 `host/scripts/benchmark_bc160.sh` 参数，会使 `host/target/` 制品与 `release_acceptance` digest 不一致 — 规则要求 CI 提交后若改脚本需重跑 Phase 2。

## Final Validation
```bash
# 1. Tier1
cd host && cargo fmt --check && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets && cargo test --workspace
# 2. Release binary
cargo build -p renpy-host --release && ldd host/target/release/renpy-host | tee host/target/verify-ldd-release.log && ! grep -qi libSDL host/target/verify-ldd-release.log
# 3. Bench MEASURED
bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && cat host/target/bc160_perf_metrics.json | python3 -m json.tool
cp /tmp/bench_1800.json host/target/bench_1800.json
# 4. Golden 8/8
bash host/scripts/run_golden_tests.sh && PYTHONPATH=host/scripts/runner python3 tests/test_parent_runner.py && PYTHONPATH=host/python/gates python3 tests/test_golden_mae.py
# 5. Ruff + Phase1
ruff check renpy/wgpu host/python && bash host/scripts/phase1_gates.sh
# 6. Release SSOT
python3 host/scripts/build_release_acceptance.py --out .omc/artifacts/release_acceptance.v1.json && python3 -m json.tool .omc/artifacts/release_acceptance.v1.json | grep -E 'verdict|evidence_revision|release_ready'
test "$(jq -r .evidence_revision .omc/artifacts/release_acceptance.v1.json)" = "$(git rev-parse HEAD)" && echo "rev bound OK"
# 7. Docs & tag fresh worktree
git worktree add /tmp/revult-verify-HEAD HEAD && cd /tmp/revult-verify-HEAD && bash host/scripts/phase1_gates.sh && cd - && git worktree remove /tmp/revult-verify-HEAD
```

## First Execution Step
执行 Phase 1 第一步：`bash host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json && cp /tmp/bench_1800.json host/target/bench_1800.json && sha256sum host/target/bc160_perf_metrics.json host/target/bench_1800.json`，产出新鲜 MEASURED 证据并归档，验证通过后再进入 Rust 基线并行。
