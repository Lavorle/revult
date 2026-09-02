# Original User Request

## Initial Request — 2026-09-02T00:33:16+08:00

针对 AMD Instinct MI50 (Vega 20, GFX906, Vega 7nm GCN5 架构) 的硬件与指令集特性（参考 ISA 规范 /home/Laouver/Downloads/vega-7nm-shader-instruction-set-architecture.pdf），深度优化 revult 的 Vulkan 宿主渲染管线、WGSL 着色器合成与显存数据流动效率。必须特别注意本架构的硬件特征（如 Wavefront64、Packed FP16 Rapid Packed Math、VGPR 寄存器压力控制与 HBM2 显存带宽特性）。

Working directory: /mnt/nvme1n1p2/revult
Integrity mode: development
Reference specification: /home/Laouver/Downloads/vega-7nm-shader-instruction-set-architecture.pdf

## Requirements

### R1. Vega 7nm / GFX906 架构适配与着色器指令级优化
依据 AMD Vega 7nm ISA 规范（包括 Wavefront64 执行模型、Packed FP16 / Rapid Packed Math、标量/向量寄存器分配与本地数据共享 LDS 特性），优化 Python/Rust WGSL 合成器生成的着色器代码结构与数据打包方式，降低 VGPR 压力并提升 SIMD 占有率 (Occupancy)。

### R2. Vulkan 宿主资源管理与吞吐调度优化
针对 MI50 的 HBM2 高带宽显存层次结构、Uniform 缓冲区对齐、Staging Buffer 上传与 BindGroup 缓存机制进行性能调优，减少 CPU 端的 FFI 锁竞争与 GPU 端不必要的管线屏障及重绑定开销。

### R3. 双图构建约束与零 SDL 纯净性保持
所有优化必须严格维护 Dual-Tree 双图构建合约：SDL3/GL2 参考树保持不受破坏；Rust 宿主产物编译链接中不得引入任何 SDL 依赖（`ldd` 纯净度检查）；所有新增的着色器与管线逻辑必须遵守 Snippet IR 与 `WgpuDraw` 协议。

### R4. 架构专项基准测试与自动化性能度量
扩展并完善针对 MI50 / GFX906 的基准测试与性能门禁机制，提供真实客观的帧率 (FPS)、1% Low FPS、RenderPass 耗时与帧资源统计 (draw calls, quads, instances)，杜绝硬编码伪造数据。

## Acceptance Criteria

### 1. 编译与纯净性验证 (Build & Purity)
- [ ] `cargo check -p renpy-host` 与 `cargo build -p renpy-host --release` 在无警告/报错下顺利完成。
- [ ] 执行 `ldd host/target/release/renpy-host | grep -iE 'libSDL'` 输出必须为空，确认无 SDL 动态链接。

### 2. 着色器合成与单元测试 (Shaders & Unit Tests)
- [ ] 运行 `python3 -m pytest tests/test_wgpu_composer.py -v` 全部通过。
- [ ] 着色器管线注册表通过诚实性校验 (`assert_pipeline_map_honest()`)，无悬空或未映射的 Pipeline 声明。

### 3. 视觉回归与金库校验 (Golden Visual Regression)
- [ ] 运行既有金库门禁脚本 (`host/scripts/phase9_gates.sh` 或对应 G01–G08 测试套件)，像素比对满足最大绝对误差 (MAE ≤ 2/255) 且无渲染破损。

### 4. 性能度量与基准门禁 (Performance & Benchmarking)
- [ ] 运行 `host/scripts/benchmark_bc160.sh --measured`（或 MI50 对应基准采集脚本）生成格式合规的度量 JSON 文件，`measurement_status` 显式为 `MEASURED` 且输出真实的平均 FPS 与帧耗时。
- [ ] 密集四边形与纹理绘制场景下的 Draw Call 批量折叠符合性能门禁标准（`draw_calls < quads / 10`）。
