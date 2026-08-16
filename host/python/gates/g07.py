"""
G07 golden — Model mesh (assimp procedural cube + textured quad).

Gate name: g07  (RENPY_HOST_GATE=g07)
Baseline: testcases/wgpu_golden/G07_model/baseline.rgba

Procedural MVP (no system assimp). Real assimp.pyx remains on SDL tree.
"""

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path
from renpy.wgpu import model as model_mod

cube = model_mod.procedural_cube_isometric(cx=-0.25, cy=0.05, size=0.32)
quad = model_mod.procedural_quad(x0=0.15, y0=0.25, x1=0.75, y1=0.75)
mesh_cube = model_mod.upload_mesh(cube)
mesh_quad = model_mod.upload_mesh(quad)
tex = model_mod.make_checker_texture(4)
solid = renpy_host.solid_pipeline()
textured = renpy_host.textured_pipeline()

for _ in range(8):
    renpy_host.begin_frame()
    model_mod.draw_solid_model(mesh_cube, solid)
    model_mod.draw_textured_model(mesh_quad, tex, textured)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

ok, msg = compare_or_bootstrap("G07_model", w, h, rgba)
out = gate_result_path("g07")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()
