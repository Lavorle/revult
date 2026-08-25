# Work Plan: Linux Vulkan/wgpu Host 产品化与原生着色器共识计划 (Consensus Plan)

## Requirements Summary & Context
本计划整合并固化了 `.omx/drafts/wgpu-host-productization-plan-v5.md`（经 Architect APPROVE 与 Critic 审议）以及 `.omc/plans/consensus-native-shader-composer.md` 的核心技术要求，形成 `revult` 项目 Linux Vulkan/wgpu Host 产品化的单一权威执行规范。

目标是将当前 Linux Vulkan/wgpu Host 从“可由旧 binary、共享结果、隐式 baseline bootstrap 或 ignored evidence 假绿”的实验状态，收敛为严格由源码构建、纯函数 Golden 检验、结构化 Runner 治理以及绑定 Intel BC-160 硬件证据的生产级产品。

---

## RALPLAN-DR Summary

### Principles
1. **Current bytes, not stale paths, define the product**：证据绑定源码/工具/二进制/制品 digest，严禁以旧路径或文件存在性充当通过证明。
2. **Fail closed with bounded claims**：未证明的输入、unsupported、not-run、丢失 envelope 均判定失败，不把 bounded provenance 夸大为 hermetic build。
3. **Single writer for every authoritative verdict**：Gate 仅写 provisional observations，Runner 独占 Gate final，Aggregate Builder 独占 Tier/Product/Release aggregate。
4. **One revision, explicit tier boundaries**：Tier 2 不冒充 Tier 3，普通 CI 不冒充产品验收，全量 Release-eligible 证据共享 approved `evidence_revision`。
5. **Preserve contracts before deleting diagnostics**：Gate 删除必须有完整 bad/good obligation replay、等价 detector 或批准的 contract retirement。

### Top Decision Drivers
1. 当前真实源码无法编译（`main.rs:237` 存在 `Duration / u64` 错误且有 22 项警告），而 stale release binary 仍存在。
2. `golden_mae.py` 与共享 Gate 结果存在 baseline 自动写盘与长度前缀比较等假绿路径。
3. 着色器架构需从 Python 动态拼接彻底升级为 Rust 原生 `NativeShaderComposer`（Naga 预校验 + 富 FFI 元数据返回）。
4. 硬件验收目标已从历史 AMD 7900XT 正式切换至 Intel BC-160，历史工单需受控规整归档。
5. 仓库内 134 个 Gates 与 ~55k 行 diagnostics 需通过声明式 Manifest 与分级 Promotion 治理。

### Viable Options
- **Option A (仅修复 Rust 编译错误)**：能最快恢复编译，但无法根除 stale binary、baseline bootstrap 与 gate 自报假绿风险。（Rejected）
- **Option B (Big-bang 批量重构 Runner/Gates)**：一次性全部重写，但失败域过大、破坏既有检测器且极易引入视觉回归。（Rejected）
- **Option C (分层递进式产品化改造 - Staged Clean in Current Repo)**：分 6 个 Slice 逐步推进，先恢复构建合同与 Shader 原生合成，再严谨化 Golden 与 Gate 治理，最终闭环 BC-160 硬件性能。（**Chosen**）

---

## Architecture Decision Record (ADR)

- **Decision**: 实施 6 个 Slice 的分层递进改造，将 Native WGSL Shader Composer 深度集成至 Slice 2，并将 Golden 比较纯函数化与 134 个 Gate 纳入分级治理。
- **Drivers**: 构建可复现性、渲染管线健壮性、Golden 严格 fail-closed、BC-160 硬件基准统一。
- **Alternatives Considered**: 独立拆仓、仅修复局部报错、全量 Big-bang 清理。
- **Why Chosen**: 在保留只读 upstream reference 和现有黄金样本的前提下，以最小破环性达成确定的生产级交付标准。
- **Consequences**:
  - Positive: 彻底杜绝假绿、实现 Rust 原生着色器预编译与错误行号定位、统一硬件证据。
  - Neutral: 改造期间需要维护 Gate inventory 映射，历史 7900XT 记录转为只读历史证据。
- **Follow-ups**: 在 Slice 1 完成后建立 GitHub CI 自动化门禁；在 Slice 5 完成 BC-160 终态压测。

---

## Acceptance Criteria (Testable)

- [x] **AC-1 (Host Build Contract - Slice 1)**:
  - 修复 `host/renpy-host/src/main.rs:237` 的 `Duration / u64` 错误；
  - 彻底清除 `host/` 目录下全部 22 项 Rust 警告，`cargo check` 与 `cargo test --workspace` 退出码为 0；
  - 落实 Rust 单元测试套件（覆盖 benchmark 纳秒计算、原子写、Shader 合成）；
  - `./host/scripts/phase1_gates.sh` 通过且静态验证 `libSDL* = 0`（零 SDL 动态依赖）。
  - *Closeout 2026-08-19: verifier GREEN — see `.omc/artifacts/productization-verifier-report.md`.*
- [ ] **AC-2 (Native WGSL Shader Composer & Naga Validation - Slice 2)**:
  - `host/renpy-host/src/shader.rs` 实现 `ShaderPart`, `ShaderPartRegistry`, `NativeShaderComposer`，挂载于 `HostState`；
  - Naga 语法预校验生效，带行号精确报错，严格执行 `tex_count <= 3` 与 uniform 布局互斥检查；
  - PyO3 桥接导出富元数据元组 `ComposedPipelineInfo` (`pipeline_handle`, `key`, `tex_count`, `uniform_layout_id`, `has_uniforms`, `wgsl_source`)；
  - Python `renpy/wgpu/draw.py` 与 `composer.py` 正确对接 L1 缓存与 uniform 打包。
  - *Closeout residual: Naga not implemented (custom `validate_wgsl_syntax` only); composer_combo_alpha MAE vs HEAD baseline fails. FFI 6-tuple + shader tests + other composer gates green. Ledger: R-AC2-NAGA, R-AC2-ALPHA-MAE.*
- [x] **AC-3 (Pure Strict Golden System - Slice 2)**:
  - `golden_mae.py` 改造为纯函数：baseline 缺失时必须退出非 0 并输出明确错误，严禁隐式写盘；
  - 图像比较必须进行精确尺寸匹配与像素级 MAE 判定，禁止前缀截断比较。
  - *Closeout 2026-08-19: verifier GREEN — authoritative path `host/python/gates/golden_mae.py`.*
- [x] **AC-4 (Structured Runner & Tier 2 Vulkan/G01-G08 - Slice 3)**:
  - 实现结构化 Parent Runner，独占 Process Envelope 与最终判定；
  - 完成 G01–G08 黄金用例（包含 MatrixColor, Blur, Dissolve 2-tex, ImageDissolve 3-tex）的 100% 通过。
  - *Closeout 2026-08-25 f49: **8/8 PASS** via parent_runner (evidence_revision f49f520045eb3615, 10 envelopes 6-field), G02/G06 single-point resign with verifier (/tmp/diag-g02/g06), exit_code 1 for g02/g06 (host+runner), corruption/missing PASS. Ledger: R-AC4-G02/G06/Baseline **resolved** via single-point.*
- [ ] **AC-5 (Gate Inventory & Tier 3 Correctness - Slice 4)**:
  - 建立 134 个 Python Gate 的 Inventory / Promotion 声明清单；
  - 修复 Python production scope 下的全部 Ruff 阻断项；
  - HuangmeiC 核心业务路径无崩溃且渲染正常，`recovered_project` 保持 100% 只读。
  - *Progress 2026-08-25 f49: inventory 134 (T1 11/T2 13/T3 110) green; **renpy/wgpu ruff 0** (539→0, 16 files, All checks passed), HMC **smoke 30s EXIT 0** (3 runs, Vulkan RADV NAVI12, RO probe, envelope f49 smoke 10s) → **narrow green on production core, but host/python/gates ruff 1282 (E702/E701/F401) remains → parent still OPEN**. Ledger: R-AC5-RUFF, R-AC5-HMC-SMOKE resolved narrow.*
- [ ] **AC-6 (BC-160 Performance & Release SSOT - Slice 5 & 6)**:
  - 历史 7900XT 遗留记录归档至 `.omx/context/historical-7900xt/`；

---

## 6 Slices Implementation Roadmap

### Slice 1: Host Build Contract & Rust Baseline
- **Touched Files**:
  - `host/renpy-host/src/main.rs` (修复行 237 的 benchmark 纳秒除法，防 0 除保护)
  - `host/renpy-host/src/state.rs` (清理未使用的 mut 绑定与 dead code)
  - `host/renpy-host/src/lib.rs` / `host/renpy-host/tests/` (添加 Host 单元测试)
  - `host/scripts/phase1_gates.sh` (集成 zero-SDL 检查)

### Slice 2: Native WGSL Shader Composer & Pure Strict Golden
- **Touched Files**:
  - `host/renpy-host/src/shader.rs` (实现原生着色器合成、Naga 预编译、Uniform 布局推导)
  - `host/renpy-host/src/python.rs` (导出富元数据 PyO3 FFI)
  - `renpy/wgpu/composer.py` & `renpy/wgpu/draw.py` (接入原生 Composer，对接 `_pack_uniforms`)
  - `testcases/golden_mae.py` (改造为纯函数 strict fail-closed 比较)

### Slice 3: Structured Runner & Tier 2 Vulkan/G01-G08
- **Touched Files**:
  - `host/scripts/runner/` (实现 Parent Runner，管理 declared input manifest 与 nonce temp)
  - `testcases/wgpu_golden/` (固化 G01–G08 黄金基准，覆盖多纹理与变换)

### Slice 4: Gate Inventory & Tier 3 HuangmeiC Correctness
- **Touched Files**:
  - `.omx/gates-inventory.json` (建立 134 个 Gate 分级治理清单)
  - `renpy/wgpu/` (清理生产路径 Ruff findings，确保类型与接口健壮)
  - `host/scripts/run_huangmeic_playtest.sh` (挂载只读保护验证)

### Slice 5: BC-160 Hardware Performance Closure
- **Touched Files**:
  - `.omx/context/historical-7900xt/` (归档历史 7900XT 调试日志与工单)
  - `host/scripts/benchmark_bc160.sh` (采集 BC-160 真实硬件渲染帧率与延迟)

### Slice 6: Docs, Aggregates & Release Evidence SSOT
- **Touched Files**:
  - `README.md` & `host/README.md` (更新构建说明、环境要求与架构图)
  - `.omx/artifacts/release_acceptance.v1.json` (聚合全链 clean evidence)

---

## Pre-Mortem (Failure Scenarios & Mitigations)
1. **Scenario 1: Python bootstrap 阶段调用着色器注册早于 GpuState 初始化导致崩溃**
   - *Mitigation*: 将 `NativeShaderComposer` 解耦挂载于 `HostState`，使 `register_shader_part` 允许在无窗/无 GPU 上下文时完成静态注册。
2. **Scenario 2: Golden 测试因平台浮点精度微差产生偶发 False Red**
   - *Mitigation*: 严格采用 MAE（平均绝对误差）阈值比对结合结构化像素判据，禁止使用简单 byte diff 或容易截断的前缀比对。
3. **Scenario 3: 硬件迁移导致历史优化在 BC-160 上出现显存带宽瓶颈**
   - *Mitigation*: 建立 BC-160 专属的 Tier 3 Performance suite，隔离 7900XT 遗留参数，以 BC-160 实测纳秒级数据为唯一调优准据。

---

## Expanded Test Plan
- **Unit Tests**: Rust 端测试 Naga WGSL 语法检验、`ShaderPart` 优先级拓扑排序、Uniform 结构体字节对齐推导。
- **Integration Tests**: PyO3 FFI 数据传输校验、Python 端 L1 缓存命中与 L2 GpuArena 管线复用、`golden_mae.py` 纯函数边界测试。
- **E2E / Golden Regression**: `phase1_gates.sh` 全流程、G01–G08 黄金视觉用例、HuangmeiC 交互回放与冒烟测试。
- **Observability**: 结构化 Parent Runner 输出 JSON Envelope（六字段规范：时间、修订、输入摘要、命令、观测、退出码）。

---

## Plan Status & State
- **Status**: `closeout-f49` (AC-1, AC-3, AC-4 checked; AC-2/5/6 residual/narrow)
- **Normative Source**: `.omc/plans/wgpu-host-productization-consensus.md`
- **Closeout artifacts**: `.omc/artifacts/productization-residual-matrix.md`, `productization-verifier-report.md`, `productization-residual-ledger.md`, `.omc/artifacts/wave3.5-verifier-delta.md`, `.omc/artifacts/wave3.5-verifier-delta.md` (f49 8/8)
- **Release-ready**: **no** (AC-6 residual; AC-2/5 narrow)
- **Ralplan**: do not set `consensus_complete: true` while AC-2/5/6 open
