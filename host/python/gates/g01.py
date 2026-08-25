"""
G01 golden — solid + textured image (geometry, texture, PMA).

Gate name: g01  (RENPY_HOST_GATE=g01)
Baseline: testcases/wgpu_golden/G01_solid_image/baseline.rgba

Note: loaded via py.run; host run_file injects RENPY_HOST_BASE + gates on sys.path.
"""

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

# Solid red rectangle (left) via solid pipeline + vertex color.
solid_verts = [
    -0.85, -0.55, 0.0, 0.0, 1.0, 0.15, 0.10, 1.0,
    -0.15, -0.55, 1.0, 0.0, 1.0, 0.15, 0.10, 1.0,
    -0.15,  0.55, 1.0, 1.0, 1.0, 0.15, 0.10, 1.0,
    -0.85,  0.55, 0.0, 1.0, 1.0, 0.15, 0.10, 1.0,
]
# 4x4 checker texture (right) — PMA-friendly opaque texels.
pix = []
for y in range(4):
    for x in range(4):
        if (x + y) & 1:
            pix.extend([40, 160, 255, 255])
        else:
            pix.extend([255, 220, 40, 255])
tex = renpy_host.create_texture_rgba(4, 4, bytes(pix))
tex_verts = [
     0.10, -0.55, 0.0, 1.0, 1, 1, 1, 1,
     0.85, -0.55, 1.0, 1.0, 1, 1, 1, 1,
     0.85,  0.55, 1.0, 0.0, 1, 1, 1, 1,
     0.10,  0.55, 0.0, 0.0, 1, 1, 1, 1,
]
idx = [0, 1, 2, 0, 2, 3]
mesh_solid = renpy_host.create_mesh(solid_verts, idx)
mesh_tex = renpy_host.create_mesh(tex_verts, idx)
pipe_solid = renpy_host.solid_pipeline()
pipe_tex = renpy_host.textured_pipeline()

for _ in range(8):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe_solid, mesh_solid, None)
    renpy_host.draw_model(pipe_tex, mesh_tex, tex)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

ok, msg = compare_or_bootstrap("G01_solid_image", w, h, rgba)
out = gate_result_path("g01")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
