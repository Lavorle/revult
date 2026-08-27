"""
G02 CJK vertical golden — CJK纵排 vertical text atlas path.

Gate name: g02_cjk_vertical  (RENPY_HOST_GATE=g02_cjk_vertical)
Baseline: testcases/wgpu_golden/G02_cjk_vertical/baseline.rgba

Walks WgpuDraw text path for CJK vertical layout: "日本語テスト"
rendered vertically (one glyph per row, stacked) via Pillow bitmap
→ texture → textured_pipeline → pre-present game RT (ADR §4.3.1).
HarfBuzz shape warming included; Pillow fallback remains valid.
"""

import os
from pathlib import Path

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path, golden_dir, write_raw_rgba, try_write_png

from renpy.wgpu.text import DEFAULT_FONT, render_text_rgba

try:
    from renpy.wgpu.text_shaper import shape as hb_shape, HAS_HB
except Exception:
    hb_shape = None  # type: ignore
    HAS_HB = False  # type: ignore

# One-shot determinism pin (R-AC4-G02 style). Documented host default font path.
os.environ.setdefault("RENPY_HOST_FONT", DEFAULT_FONT)

# Warm HarfBuzz shaping for CJK (best-effort, fallback to Pillow RAQM)
try:
    if hb_shape is not None:
        _ = hb_shape("日本語テスト", DEFAULT_FONT, 36)
except Exception:
    pass

# Build vertical column image: stack each CJK char vertically
# Reuse render_text_rgba per-glyph then compose into one vertical texture
# to exercise atlas/text path with CJK glyphs in vertical flow.
chars = list("日本語テスト")
# Render each char with transparent bg, then composite onto opaque bg
from PIL import Image  # type: ignore

char_images = []
for ch in chars:
    tw, th, trgba = render_text_rgba(
        ch, size=34, color=(255, 255, 255, 255), bg=(0, 0, 0, 0), padding=6
    )
    im = Image.frombytes("RGBA", (tw, th), trgba)
    char_images.append(im)

# Also add a small Latin annotation below to prove mixed-script
tw_a, th_a, trgba_a = render_text_rgba(
    "vertical=True", size=20, color=(200, 220, 255, 255), bg=(0, 0, 0, 0), padding=4
)
im_a = Image.frombytes("RGBA", (tw_a, th_a), trgba_a)

max_w = max(max(im.width for im in char_images), im_a.width)
total_h = sum(im.height for im in char_images) + 8 + th_a + 12
bg_color = (20, 24, 48, 255)
vert_im = Image.new("RGBA", (max_w + 16, total_h), bg_color)
y = 6
for im in char_images:
    x = (vert_im.width - im.width) // 2
    # Paste with alpha composite
    vert_im.alpha_composite(im, dest=(x, y))
    y += im.height
y += 8
x_a = (vert_im.width - im_a.width) // 2
vert_im.alpha_composite(im_a, dest=(x_a, y))

tw, th = vert_im.size
trgba = vert_im.tobytes()
tex_vert = renpy_host.create_texture_rgba(tw, th, trgba)

# Background box (solid) — stable NDC like G02
box_verts = [
    -0.90, -0.85, 0.0, 0.0, 0.08, 0.10, 0.20, 1.0,
     0.90, -0.85, 1.0, 0.0, 0.08, 0.10, 0.20, 1.0,
     0.90,  0.85, 1.0, 1.0, 0.08, 0.10, 0.20, 1.0,
    -0.90,  0.85, 0.0, 1.0, 0.08, 0.10, 0.20, 1.0,
]
# Vertical text quad centered, size ~0.35 NDC width
# Preserve aspect: vert_im is tall; map to NDC rect 0.35 wide, ~0.7 tall
text_verts = [
    -0.18, -0.70, 0.0, 1.0, 1, 1, 1, 1,
     0.18, -0.70, 1.0, 1.0, 1, 1, 1, 1,
     0.18,  0.70, 1.0, 0.0, 1, 1, 1, 1,
    -0.18,  0.70, 0.0, 0.0, 1, 1, 1, 1,
]
idx = [0, 1, 2, 0, 2, 3]
mesh_box = renpy_host.create_mesh(box_verts, idx)
mesh_text = renpy_host.create_mesh(text_verts, idx)
pipe_solid = renpy_host.solid_pipeline()
pipe_tex = renpy_host.textured_pipeline()

for _ in range(8):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe_solid, mesh_box, None)
    renpy_host.draw_model(pipe_tex, mesh_text, tex_vert)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

# Bootstrap-aware compare: first run writes baseline if missing, second run validates.
# Keeps MAE thresholds mean≤2/255 max≤16 fail-closed (ADR §4.3.1) after bootstrap.
gdir = golden_dir("G02_cjk_vertical")
base_path = gdir / "baseline.rgba"
if not base_path.is_file():
    gdir.mkdir(parents=True, exist_ok=True)
    write_raw_rgba(base_path, w, h, rgba)
    try_write_png(gdir / "baseline.png", w, h, rgba)
    # Also write actual for parity
    write_raw_rgba(gdir / "actual.rgba", w, h, rgba)
    try_write_png(gdir / "actual.png", w, h, rgba)
    msg = f"[G02_cjk_vertical] {w}x{h} baseline bootstrapped (first run) ok=True"
    print(msg, flush=True)
    ok = True
else:
    ok, msg = compare_or_bootstrap("G02_cjk_vertical", w, h, rgba)

out = gate_result_path("g02_cjk_vertical")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
