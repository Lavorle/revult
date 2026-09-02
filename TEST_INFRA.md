# E2E Test Infra: AMD Instinct MI50 (GFX906 / Vega 7nm) Vulkan & WGSL Optimization for revult

## Test Philosophy
- Opaque-box, requirement-driven, empirical hardware measurement.
- Multi-tier validation: Unit & Shader Tests (Tier 1), Boundary & Corner Cases (Tier 2), Visual Regression Golden Suite (Tier 3), Hardware Benchmarks & Quad Collapsing Gates (Tier 4), Adversarial Coverage & Forensic Integrity Audit (Tier 5).
- Zero tolerance for simulated/hardcoded benchmark values or dummy facades.

## Feature Inventory & Test Mapping
| # | Feature | Requirement Source | Tier 1 Unit | Tier 2 Boundary | Tier 3 Golden | Tier 4 Benchmark |
|---|---------|-------------------|:-----------:|:---------------:|:-------------:|:----------------:|
| 1 | WGSL Shader Pre-normalized Weights & Constant Folding | R1 (Vega 7nm ISA) | ✓ (`test_wgpu_composer`) | ✓ | ✓ (G04 Blur) | ✓ |
| 2 | Vector Memory Clause Clustering in Shaders | R1 (Vega 7nm ISA) | ✓ (`test_wgpu_composer`) | ✓ | ✓ (G03 Dissolve) | ✓ |
| 3 | Scalar Uniform Alignment & Column-Vector Math | R1 (Vega 7nm ISA) | ✓ (`test_wgpu_composer`) | ✓ | ✓ (G01 Solid) | ✓ |
| 4 | Snippet IR & Pipeline Map Honesty | R1, R3 | ✓ (`assert_pipeline_map_honest`) | ✓ | ✓ (G01–G08) | ✓ |
| 5 | Dynamic Uniform Ring Buffer (256B Alignment) | R2 (Memory & Vulkan) | ✓ (`cargo test -p renpy-host`) | ✓ | ✓ (G01–G08) | ✓ |
| 6 | Decoupled BindGroup Caching | R2 (Memory & Vulkan) | ✓ (`cargo test -p renpy-host`) | ✓ | ✓ (G01–G08) | ✓ |
| 7 | PyO3 FFI Zero-Copy & GIL Detach | R2 (Throughput) | ✓ (`cargo test -p renpy-host`) | ✓ | ✓ (G05 Movie) | ✓ |
| 8 | Pipeline State Redundancy Filtering | R2 (Vulkan Command Stream) | ✓ (`cargo test -p renpy-host`) | ✓ | ✓ (G01–G08) | ✓ |
| 9 | Dual-Tree Zero-SDL Purity | R3 (Dual-Tree) | ✓ (`ldd` check) | ✓ | ✓ (Clean link) | ✓ |
| 10 | Quad Batch Collapsing & 10x Gate | R4 (Performance) | ✓ (`test_wgpu_composer`) | ✓ | ✓ (G02 Text) | ✓ (`draw_calls < quads/10`) |
| 11 | Authentic Benchmark Metrics Suite | R4 (Performance) | ✓ | ✓ | ✓ | ✓ (`benchmark_bc160.sh --measured`) |
| 12 | Golden Visual Regression Validation | R4 (Visual Correctness) | ✓ | ✓ | ✓ (MAE <= 2/255) | ✓ |

## Test Architecture
- **Host Unit Tests**: `cargo test -p renpy-host` (Vulkan context, dynamic uniform buffer, bindgroup cache, shader parser).
- **Shader Unit & Honesty Tests**: `python3 -m pytest tests/test_wgpu_composer.py -v` and `assert_pipeline_map_honest()`.
- **Pure Dynamic Linkage Test**: `ldd host/target/release/renpy-host | grep -iE 'libSDL'` (Must be empty).
- **Golden Visual Regression Suite**: `host/scripts/phase9_gates.sh` or `host/scripts/run_golden_tests.sh` running G01–G08 with strict fail-closed MAE threshold ($\le 2/255$) and max delta ($\le 16$).
- **Authentic Performance Measurement**: `host/scripts/benchmark_bc160.sh --measured --measured-frames 1800 --out host/target/bc160_perf_metrics.json`.
  - Must report `measurement_status == "MEASURED"`.
  - Must enforce `draw_calls < quads / 10`.
  - Must report authentic Average FPS and 1% Low FPS using Vulkan GPU timestamp queries.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | High-Density UI & Text Dialogue | F4, F5, F6, F10 | High |
| 2 | Fullscreen Dissolve & Imagedissolve Transitions | F1, F2, F5, F6, F12 | High |
| 3 | Matrixcolor & Gaussian Blur Compositing | F1, F3, F5, F6, F12 | High |
| 4 | Live2D Multi-Texture Masking & Color Blend | F4, F5, F6, F12 | High |
| 5 | High-FPS 1800-Frame Stress Benchmark | F5, F6, F7, F8, F10, F11 | High |

## Coverage Thresholds
- Unit Tests: 100% pass (all Rust & Python pytest test suites).
- Golden Suite: 100% pass across all 12 mandatory gates, MAE <= 2/255.
- Purity: 0 dynamic SDL references.
- Benchmark: Status == `MEASURED`, FPS >= 60.0, `draw_calls < quads / 10`.
