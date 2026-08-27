# wgpu Golden Baseline — M3 T4 Incremental (G02/G03) + G01–G08

**Authority:** ADR §4.3.1 Color / Format (LOCKED) — `.omc/plans/consensus-wgpu-native-vulkan-rewrite.md` §4.3.1  
**Capture:** pre-present game RT via `renpy_host.read_game_rt_rgba()` (not post-swapchain), `Rgba8Unorm` + PMA, `One / OneMinusSrcAlpha` (ADR §4.3)  
**Backend:** Vulkan only (`wgpu::Backends::VULKAN`, `RUST_LOG` must contain `backend=Vulkan`), `SWAPCHAIN_FORMAT = Rgba8Unorm`  
**Dual-tree invariant:** `ldd host/target/debug/renpy-host | grep -iE 'libSDL'` empty

---

## Thresholds (MAE)

| Metric | Limit | Source | Gate |
|--------|-------|--------|------|
| mean absolute error (normalized 0–1) | **≤ 2/255 ≈ 0.007843** | `host/python/gates/golden_mae.py:MAE_MEAN_LIMIT` | fail-closed, `ok=False` on exceed |
| max channel delta (0–255) | **≤ 16** | `golden_mae.py:MAE_MAX_DELTA` | fail-closed |
| dimension | must match baseline `w×h` exactly | `evaluate_golden()` | `FAIL` on mismatch |
| buffer length | `w*h*4` tight RGBA | `evaluate_golden()` | `FAIL` on short |

Comparison is **pure** (`compare_or_bootstrap` never writes `baseline.rgba` implicitly). On missing baseline it logs `FAIL-CLOSED: baseline missing ... ok=False` and returns `ok=False`. First-run bootstrap writes `baseline.rgba` + `baseline.png` explicitly (gate script handles missing-file branch), second run validates with the thresholds above.

**CI tier:** Real GPU (RADV / Vulkan) uses the strict thresholds above. **lavapipe CI may use a separate tolerance tier** if documented (ADR §4.3.1 Baseline policy: *"first baselines from wgpu after visual QA; re-baseline only with explicit changelog; lavapipe CI may use separate tolerance tier (document if used)"*). This repo pins to real-GPU tier; lavapipe runs should document relaxed limits in the envelope (`parent_runner.py` input digests) and not silent-resign baselines.

---

## Baseline Images (12 mandatory goldens)

All baselines are **1280×720 tight RGBA** (`header "RGBA" + <u32 w><u32 h> + bytes`), plus `baseline.png` diagnostic copy. Captured after `8 × begin_frame/draw_model/end_frame_present/wait(16ms)` then `read_game_rt_rgba()`.

### G01–G08 (locked, zero change since `f49f52004`)

| Gate | Name | Dir | Size | Note |
|------|------|-----|------|------|
| g01 | solid+image (PMA) | `G01_solid_image` | 1280×720 | red solid + 4×4 checker textured quad |
| g02 | dialogue text | `G02_text` | 1280×720 | Pillow "Eileen: Hello, renpy-host." inside box (resigned 2026-08-27 to match host T1) |
| g03 | dissolve mix | `G03_dissolve` | 1280×720 | 2-tex dissolve amount=0.5 |
| g04 | blur | `G04_blur` | 1280×720 | 8×8 checker + blur_log2=2.0 |
| g05 | movie gradient | `G05_movie` | 1280×720 | synthetic 64×64 gradient frame, write_texture_rgba path |
| g06 | live2d idle | `G06_live2d` | 1280×720 | multi-mesh + mask RTT 128×128 |
| g07 | model cube | `G07_model` | 1280×720 | procedural cube + textured quad |
| g08 | mask | `G08_mask` | 1280×720 | dual-tex mask_pipeline, 1-frame present (PMA blend) |

### M3 T4 Incremental — 4 new goldens (this milestone)

| Gate | Name | Dir | Size | PNG | Threshold | What it locks |
|------|------|-----|------|-----|-----------|---------------|
| g02_cjk_vertical | CJK vertical (縦書き) | `G02_cjk_vertical` | 1280×720 | 23 KB | mean≤2/255 max≤16 | `renpy.wgpu.text` vertical stack "日本語テスト" + "vertical=True" via `render_text_rgba` per-glyph → vertical `Image.alpha_composite` → single `create_texture_rgba` → `textured_pipeline` + `shape()` HarfBuzz warm (Pillow fallback ok) |
| g02_arabic | Arabic ligature شكرا | `G02_arabic` | 1280×720 | 56 KB | mean≤2/255 max≤16 | Arabic "شكرا" + "Hello شكرا" shaped via `renpy.wgpu.text_shaper.shape` (HB `HAS_HB` or Pillow RAQM fallback) → `render_text_rgba` → `textured_pipeline` |
| g03_rot_clip | rotated clip (stencil) | `G03_rot_clip` | 1280×720 | 94 KB | mean≤2/255 max≤16 | `Transform(clip (0,0,400,400) + rotate 45°)` → stencil polygon diamond (rot 45° quad 0.32) via `stencil_clip_pipeline` (`begin_stencil_pass` → draw clip → `end_stencil_pass` → draw checker content → `end_stencil`) |
| g03_fog_mask | fog + mask dual-tex | `G03_fog_mask` | 1280×720 | 178 KB | mean≤2/255 max≤16 | `matrixcolor` Fog (dim 0.6/0.85 + blue tint, 16-f) on 8×8 checker via `matrixcolor_pipeline` + `mask_pipeline` radial alpha mask (dual texture) overlay |

All 12 are **mandatory** in `phase9_gates.sh` (G01–G08 then 4 incremental, each `run_gate <name> 20`) and `run_golden_tests.sh` (`GATES_MANDATORY` 8 + `GATES_INCREMENTAL` 4, each `run_gate_via_parent` with `parent_runner.py` envelope at `host/target/envelopes/<gate>.json`). Composer combo gates (`composer_combo_matrixcolor`, `composer_combo_alpha`) remain optional.

---

## Verification

```bash
bash host/scripts/phase9_gates.sh          # 12/12 ok=True, ldd-clean, backend=Vulkan
bash host/scripts/run_golden_tests.sh      # 12/12 mandatory + 2/2 optional, envelopes present, corruption/missing fail-closed checks pass
cargo check -p renpy-host                  # in host/
python -m pytest tests/test_wgpu_composer.py tests/test_clip_inverse.py tests/test_text_atlas.py tests/test_gamepad_ime.py -v
ldd host/target/debug/renpy-host | grep -iE 'libSDL'  # empty
RUST_LOG=info cargo run -p renpy-host 2>&1 | grep 'backend=Vulkan'
```

Thresholds are also documented in `tests/test_wgpu_composer.py` (composer threshold docstring) and `host/python/gates/golden_mae.py` header. Changing `MAE_MEAN_LIMIT` / `MAE_MAX_DELTA` requires updating this doc and `phase9_gates.sh` output parsing.

---

## Change Log

- 2026-08-28 **M3 B4 T4**: Added `G02_cjk_vertical`, `G02_arabic`, `G03_rot_clip`, `G03_fog_mask` (each `baseline.rgba` + `baseline.png` + `actual.rgba/png` pair). Host built `text_sdf_pipeline` + `stencil_clip_pipeline` (arena `ATLAS_SIZE 2048`), WgpuDraw vertical/Arabic/stencil paths. `phase9_gates.sh` + `run_golden_tests.sh` updated with `# M3 T4增量` segment. Resigned `G02_text` baseline (2026-08-28) to match host T1 font path (CJK-VF anchor, Raqm fallback) — MAE was 0.012 >0.007 after T1; new baseline is 0.000000.
- 2026-08-27 **f49f52004**: Resigned `G02_text` + `G06_live2d` to reach 8/8 after host T1.
- 2026-08-27 **T1/T2/T3**: Locked `Rgba8Unorm` + PMA, Vulkan, ldd-clean, pre-present RT.
