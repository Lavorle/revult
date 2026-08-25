"""
G03 golden — dissolve scene, pre-present RT readback, MAE vs baseline.

Gate name: g03  (RENPY_HOST_GATE=g03)
Baseline: testcases/wgpu_golden/G03_dissolve/baseline.rgba

True 2-tex renpy.dissolve mix (old/red + new/blue, amount=0.5).
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

RED = bytes([255, 0, 0, 255] * 4)
BLUE = bytes([0, 0, 255, 255] * 4)
tex0 = renpy_host.create_texture_rgba(2, 2, RED)
tex1 = renpy_host.create_texture_rgba(2, 2, BLUE)
verts = [
    -0.75, -0.75, 0.0, 1.0, 1, 1, 1, 1,
     0.75, -0.75, 1.0, 1.0, 1, 1, 1, 1,
     0.75,  0.75, 1.0, 0.0, 1, 1, 1, 1,
    -0.75,  0.75, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.dissolve_pipeline()
u = [0.5] + [0.0] * 15

for _ in range(8):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex0, tex1, u)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

ok, msg = compare_or_bootstrap("G03_dissolve", w, h, rgba)
out = gate_result_path("g03")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
