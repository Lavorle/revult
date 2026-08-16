"""
G02 golden — say-dialogue style bitmap text atlas upload.

Gate name: g02  (RENPY_HOST_GATE=g02)
Baseline: testcases/wgpu_golden/G02_text/baseline.rgba

Uses renpy.wgpu.text bitmap path (Pillow glyphs → texture → draw_model).
Full ftfont/atlas Cython path remains deferred.
"""

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path
from renpy.wgpu.text import render_text_rgba

# Fixed dialogue string + layout so the golden is deterministic.
line = "Eileen: Hello, renpy-host."
tw, th, trgba = render_text_rgba(
    line, size=36, color=(255, 255, 255, 255), bg=(20, 24, 48, 255), padding=8
)
tex = renpy_host.create_texture_rgba(tw, th, trgba)

# Dialogue box background (solid) + text textured quad (stable NDC).
box_verts = [
    -0.90, -0.75, 0.0, 0.0, 0.08, 0.10, 0.20, 1.0,
     0.90, -0.75, 1.0, 0.0, 0.08, 0.10, 0.20, 1.0,
     0.90, -0.20, 1.0, 1.0, 0.08, 0.10, 0.20, 1.0,
    -0.90, -0.20, 0.0, 1.0, 0.08, 0.10, 0.20, 1.0,
]
# Text sits inside the box; UVs standard.
text_verts = [
    -0.85, -0.68, 0.0, 1.0, 1, 1, 1, 1,
     0.85, -0.68, 1.0, 1.0, 1, 1, 1, 1,
     0.85, -0.28, 1.0, 0.0, 1, 1, 1, 1,
    -0.85, -0.28, 0.0, 0.0, 1, 1, 1, 1,
]
idx = [0, 1, 2, 0, 2, 3]
mesh_box = renpy_host.create_mesh(box_verts, idx)
mesh_text = renpy_host.create_mesh(text_verts, idx)
pipe_solid = renpy_host.solid_pipeline()
pipe_tex = renpy_host.textured_pipeline()

for _ in range(8):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe_solid, mesh_box, None)
    renpy_host.draw_model(pipe_tex, mesh_text, tex)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

ok, msg = compare_or_bootstrap("G02_text", w, h, rgba)
out = gate_result_path("g02")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()
