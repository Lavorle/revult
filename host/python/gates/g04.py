"""
G04 golden — blur scene, pre-present RT readback, MAE vs baseline.

Gate name: g04  (RENPY_HOST_GATE=g04)
Baseline: testcases/wgpu_golden/G04_blur/baseline.rgba

Note: loaded via py.run; host run_file injects RENPY_HOST_BASE + gates on sys.path.
"""

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path


# 8x8 checkerboard → more stable blur sample than 4x4.
pix = []
for y in range(8):
    for x in range(8):
        if (x + y) & 1:
            pix.extend([255, 255, 255, 255])
        else:
            pix.extend([0, 0, 0, 255])
tex = renpy_host.create_texture_rgba(8, 8, bytes(pix))
verts = [
    -0.8, -0.8, 0.0, 1.0, 1, 1, 1, 1,
     0.8, -0.8, 1.0, 1.0, 1, 1, 1, 1,
     0.8,  0.8, 1.0, 0.0, 1, 1, 1, 1,
    -0.8,  0.8, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.blur_pipeline()
# uniforms[0] = blur_log2
u = [2.0] + [0.0] * 15

for _ in range(8):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex, None, u)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

ok, msg = compare_or_bootstrap("G04_blur", w, h, rgba)
out = gate_result_path("g04")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
