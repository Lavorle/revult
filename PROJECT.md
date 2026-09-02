# Project: AMD Instinct MI50 (Vega 20 / GFX906 / Vega 7nm) Vulkan & WGSL Optimization for revult

## Architecture
`revult` is a dual-tree Linux Vulkan host implementation for Ren'Py, designed to deliver high-performance rendering on Linux with native Vulkan acceleration while preserving the legacy SDL3/GL2 reference tree.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        Python WgpuDraw Engine                          │
│   (renpy/wgpu/draw.py, draw_screen.py, draw_model.py, draw_walk.py)    │
├──────────────────┬───────────────────┬─────────────────────────────────┤
│  Batching / Inst │   Mesh / RTT Pool │  WGSL Shader Composer & Snippet │
│  _InstanceGroup  │   GpuHandleCache  │  WgslShaderCache                │
│  _draw_batch     │   RttPoolMixin    │  assert_pipeline_map_honest     │
└─────────┬────────┴─────────┬─────────┴────────────────┬────────────────┘
          │ (PyO3 FFI)       │ (Handles: u64)           │ (WGSL Pipelines)
┌─────────▼──────────────────▼──────────────────────────▼────────────────┐
│                       Rust Host (renpy-host)                           │
│   (app.rs, gpu.rs, arena.rs, shader.rs, python.rs, pump.rs)            │
├────────────────────────────────────────────────────────────────────────┤
│   - GpuArena: Dynamic UBO Ring (256B stride), BindGroup LRU Cache      │
│   - Vulkan 1.4 Context (RADV VEGA20 / GFX906), Rgba8Unorm Swapchain    │
│   - Timestamp Query Metrics, 1% Low FPS, FrameStats Performance        │
│   - Zero-SDL linkage pure host binary                                  │
└────────────────────────────────────────────────────────────────────────┘
```

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | WGSL Shader Pre-normalized Weights & Constant Folding | Fold dynamic normalizations in `blur` and transition shaders into constants | M1 | Survey (ISA Explorer) |
| 2 | Vector Memory Clause Clustering in Shaders | Cluster contiguous texture sampling instructions in `imagedissolve`, `blur`, and multi-texture shaders | M1 | Survey (ISA Explorer) |
| 3 | Scalar Uniform Alignment & Column-Vector Math | Optimize uniform layouts and matrix transformations for SGPR scalar loading and VALU efficiency | M1 | Survey (ISA Explorer) |
| 4 | Snippet IR & Pipeline Map Honesty | Preserve Snippet IR contract and pass `assert_pipeline_map_honest()` | M1 | Survey (Wgpu Explorer) |
| 5 | Dynamic Uniform Ring Buffer (256B Alignment) | Refactor `GpuArena.uniform_ring` to a single contiguous dynamic uniform buffer with 256-byte stride | M2 | Survey (Host Explorer) |
| 6 | Decoupled BindGroup Caching | Remove `ubuf_slot` from `BgCacheKey` using dynamic uniform offsets to prevent cache thrashing | M2 | Survey (Host Explorer) |
| 7 | PyO3 FFI Zero-Copy & GIL Detach | Release GIL during `end_frame_present` presentation and avoid unnecessary heap copies in texture upload | M2 | Survey (Host Explorer) |
| 8 | Pipeline State Redundancy Filtering | Filter out redundant `set_pipeline` and `set_bind_group` commands in `encode_pass_into` | M2 | Survey (Host Explorer) |
| 9 | Dual-Tree Zero-SDL Purity | Ensure `renpy-host` has zero dynamic dependency on `libSDL*` or `libGL*` via `ldd` check | M2 | User Request R3 |
| 10 | Quad Batch Collapsing & 10x Gate | Enforce `_InstanceGroup` quad collapsing so `draw_calls < quads / 10` for dense scenes | M3 | User Request R4 |
| 11 | Authentic Benchmark Metrics Suite | Validate real-time FPS, 1% Low FPS, render pass latency with GPU timestamp queries | M3 | User Request R4 |
| 12 | Golden Visual Regression Validation | Verify G01–G08 golden suite satisfying MAE <= 2/255 and max channel delta <= 16 | M3 | User Request R4 |
| 13 | End-to-End Integration & Forensic Audit | Pass all build, unit, golden, and benchmark gates with clean Forensic Integrity Audit | M4 | System Gate |

## Milestones
| # | Name | Scope | Dependencies | Status | Key Deliverables & Evidence |
|---|------|-------|-------------|--------|-----------------------------|
| M1 | WGSL Shader & ISA Optimization | WGSL shader snippets, constant folding, clause clustering, matrix math, Snippet IR honesty | None | DONE | Pre-normalized blur weights, vector clause clustering, 82 cargo tests & 7 pytest tests passed |
| M2 | Host Vulkan Engine & Memory Optimization | Dynamic uniform ring (256B), decoupled BindGroup cache, GIL detach, state filtering, zero-SDL | M1 | DONE | 256B stride dynamic uniform ring, decoupled BgCacheKey with dynamic offsets, zero-copy FFI, 0 SDL linkage |
| M3 | Python WgpuDraw Batching & Benchmark Suite | `WgpuDraw` quad batch collapsing (10x gate), benchmark runner hardening, G01-G08 golden tests | M2 | DONE | 10x quad batch collapsing verified (`draw_calls < quads/10`), 1800-frame benchmark >2000 FPS, G01-G08 golden suite passed |
| M4 | Final Integration, Validation & Forensic Audit | Full suite pass, adversarial stress testing, Forensic Integrity Audit (CLEAN) | M3 | DONE | All 93 cargo tests + 130 pytest tests passed; 2 Reviewers APPROVE, 2 Challengers APPROVE, Auditor CLEAN |

## Interface Contracts

### Python WgpuDraw ↔ Rust Host FFI (`renpy_host`)
- **`draw_models(cmds: Vec<...>)`**: Accepts batched draw model tuples `(pipe, mesh, tex0, tex1, uniforms, tex2)`.
- **`draw_instances(pipe: u64, tex0: u64, tex1: u64, tex2: u64, instances: Vec<f32>)`**: Accepts batched 12-float packed quad instances.
- **`end_frame_present() -> ()`**: Encodes command buffer with dynamic uniform offsets, submits to Vulkan queue, releases GIL during present.
- **`get_frame_stats() -> FrameStats`**: Returns authentic counters for `draw_calls`, `quads`, `instances`, `render_pass_ns`.

### Code Layout
- `host/renpy-host/src/gpu.rs`: Vulkan context, device/adapter initialization (RADV VEGA20), surface configuration.
- `host/renpy-host/src/arena.rs`: `GpuArena`, dynamic uniform buffer, bind group cache, shader pipelines, render pass encoding.
- `host/renpy-host/src/shader.rs`: Native WGSL shader parser and composer.
- `host/renpy-host/src/python.rs`: PyO3 FFI bindings and module interface.
- `renpy/wgpu/draw.py`, `draw_screen.py`: Python `WgpuDraw` scene rendering engine and batch dispatcher.
- `renpy/wgpu/shaders.py`: Snippet IR definitions, built-in shader hooks, pipeline honesty mapping.
- `renpy/wgpu/composer.py`: Python WGSL shader synthesis and caching (`WgslShaderCache`).
- `host/scripts/benchmark_bc160.sh`: Authentic hardware performance measurement harness.
- `host/scripts/phase9_gates.sh`: Golden visual regression test suite runner.
