"""
G08 golden — alpha mask (dual-texture mask pipeline).

Gate name: g08  (RENPY_HOST_GATE=g08)
Baseline: testcases/wgpu_golden/G08_mask/baseline.rgba
"""

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path


# Green source + left-opaque / right-transparent mask.
src = renpy_host.create_texture_rgba(2, 2, bytes([0, 255, 0, 255] * 4))
mask_pix = bytes([255, 255, 255, 255, 0, 0, 0, 0] * 2)
mask = renpy_host.create_texture_rgba(2, 2, mask_pix)
verts = [
    -0.70, -0.70, 0.0, 1.0, 1, 1, 1, 1,
     0.70, -0.70, 1.0, 1.0, 1, 1, 1, 1,
     0.70,  0.70, 1.0, 0.0, 1, 1, 1, 1,
    -0.70,  0.70, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.mask_pipeline()
# mult=1, offset=0
u = [1.0, 0.0] + [0.0] * 14

# Single present: multi-frame Load-preserve re-blends partial mask α under PMA
# and drives coverage toward opaque vs the single-frame bilinear baseline.
for _ in range(1):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, src, mask, u)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

ok, msg = compare_or_bootstrap("G08_mask", w, h, rgba)
out = gate_result_path("g08")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
