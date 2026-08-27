"""
G05 golden — movie frame (video texture path).

Gate name: g05  (RENPY_HOST_GATE=g05)
Baseline: testcases/wgpu_golden/G05_movie/baseline.rgba

Uses a fixed synthetic gradient frame (deterministic for MAE).
FFmpeg path is covered by the non-golden `video` regression gate.
"""

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path

from renpy.wgpu.video import _gradient_frame

from host.python.gates._harness import gate_harness, parametrized_gate

# Fixed t=0.5 gradient frame → textured full-window-ish quad.
W, H = 64, 64
frame = _gradient_frame(W, H, 0.5)
tex = renpy_host.create_texture_rgba(W, H, frame)
# Also exercise write_texture_rgba (in-place update) with the same deterministic
# frame so the golden path matches the video upload contract.
renpy_host.write_texture_rgba(tex, frame)

verts = [
    -0.80, -0.80, 0.0, 1.0, 1, 1, 1, 1,
     0.80, -0.80, 1.0, 1.0, 1, 1, 1, 1,
     0.80,  0.80, 1.0, 0.0, 1, 1, 1, 1,
    -0.80,  0.80, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.textured_pipeline()

# A/V clock smoke (pos advances) — not part of pixel golden.
renpy_host.video_clock_start(0)
for _ in range(8):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
pos = float(renpy_host.video_clock_pos(0) or 0.0)
renpy_host.video_clock_stop(0)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

ok, msg = compare_or_bootstrap("G05_movie", w, h, rgba)
msg = msg + f" clock_pos={pos:.4f}"
out = gate_result_path("g05")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
