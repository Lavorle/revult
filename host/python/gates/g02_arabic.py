"""
G02 Arabic golden — Arabic شكرا ligature via HarfBuzz.

Gate name: g02_arabic  (RENPY_HOST_GATE=g02_arabic)
Baseline: testcases/wgpu_golden/G02_arabic/baseline.rgba

Uses renpy.wgpu.text bitmap path with Arabic string "شكرا" shaped
via renpy.wgpu.text_shaper (HarfBuzz when available, Pillow RAQM
fallback) → Pillow glyphs → texture → draw_model.
Validates HarfBuzz ligature shaping (or Pillow fallback) yields stable
MAE vs baseline. Pre-present game RT capture (ADR §4.3.1).
"""

import os

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path, golden_dir, write_raw_rgba, try_write_png

from renpy.wgpu.text import DEFAULT_FONT, render_text_rgba

try:
    from renpy.wgpu.text_shaper import shape as hb_shape, HAS_HB
except Exception:
    hb_shape = None  # type: ignore
    HAS_HB = False  # type: ignore

os.environ.setdefault("RENPY_HOST_FONT", DEFAULT_FONT)

# Warm shaping for Arabic ligature "شكرا" (thank you)
arabic = "شكرا"
try:
    if hb_shape is not None:
        glyphs = hb_shape(arabic, DEFAULT_FONT, 36)
        print(f"[g02_arabic] hb_shape glyphs={len(glyphs)} HAS_HB={HAS_HB}", flush=True)
        # Also shape mixed LTR/RTL to stress fallback
        _ = hb_shape("Hello شكرا world", DEFAULT_FONT, 30)
    else:
        print("[g02_arabic] hb_shape unavailable, Pillow fallback", flush=True)
except Exception as e:
    print(f"[g02_arabic] shape warm failed: {e}", flush=True)

# Render Arabic with Pillow path (MAE≤2). Pillow fallback without HarfBuzz
# still produces stable ligature via font's own GSUB (if font supports) or
# per-codepoint fallback — both accepted as golden (baseline locks whichever
# tier is present).
tw, th, trgba = render_text_rgba(
    arabic, size=36, color=(255, 255, 255, 255), bg=(20, 24, 48, 255), padding=10
)
# Also render a mixed line for visual richness (Latin + Arabic)
tw2, th2, trgba2 = render_text_rgba(
    "Hello  شكرا", size=28, color=(220, 240, 255, 255), bg=(20, 24, 48, 255), padding=8
)
tex_ar = renpy_host.create_texture_rgba(tw, th, trgba)
tex_mix = renpy_host.create_texture_rgba(tw2, th2, trgba2)

box_verts = [
    -0.92, -0.78, 0.0, 0.0, 0.08, 0.10, 0.20, 1.0,
     0.92, -0.78, 1.0, 0.0, 0.08, 0.10, 0.20, 1.0,
     0.92, -0.18, 1.0, 1.0, 0.08, 0.10, 0.20, 1.0,
    -0.92, -0.18, 0.0, 1.0, 0.08, 0.10, 0.20, 1.0,
]
# Arabic primary line centered upper
ar_verts = [
    -0.50, -0.62, 0.0, 1.0, 1, 1, 1, 1,
     0.50, -0.62, 1.0, 1.0, 1, 1, 1, 1,
     0.50, -0.30, 1.0, 0.0, 1, 1, 1, 1,
    -0.50, -0.30, 0.0, 0.0, 1, 1, 1, 1,
]
# Mixed line lower
mix_verts = [
    -0.70, -0.28, 0.0, 1.0, 1, 1, 1, 1,
     0.70, -0.28, 1.0, 1.0, 1, 1, 1, 1,
     0.70, -0.10, 1.0, 0.0, 1, 1, 1, 1,
    -0.70, -0.10, 0.0, 0.0, 1, 1, 1, 1,
]
idx = [0, 1, 2, 0, 2, 3]
mesh_box = renpy_host.create_mesh(box_verts, idx)
mesh_ar = renpy_host.create_mesh(ar_verts, idx)
mesh_mix = renpy_host.create_mesh(mix_verts, idx)
pipe_solid = renpy_host.solid_pipeline()
pipe_tex = renpy_host.textured_pipeline()

for _ in range(8):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe_solid, mesh_box, None)
    renpy_host.draw_model(pipe_tex, mesh_ar, tex_ar)
    renpy_host.draw_model(pipe_tex, mesh_mix, tex_mix)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

gdir = golden_dir("G02_arabic")
base_path = gdir / "baseline.rgba"
if not base_path.is_file():
    gdir.mkdir(parents=True, exist_ok=True)
    write_raw_rgba(base_path, w, h, rgba)
    try_write_png(gdir / "baseline.png", w, h, rgba)
    write_raw_rgba(gdir / "actual.rgba", w, h, rgba)
    try_write_png(gdir / "actual.png", w, h, rgba)
    msg = f"[G02_arabic] {w}x{h} baseline bootstrapped HAS_HB={HAS_HB} ok=True"
    print(msg, flush=True)
    ok = True
else:
    ok, msg = compare_or_bootstrap("G02_arabic", w, h, rgba)

out = gate_result_path("g02_arabic")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
