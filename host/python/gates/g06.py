"""
G06 golden — Live2D idle sample (fixed pose, multi-mesh + mask RTT).

Gate name: g06  (RENPY_HOST_GATE=g06)
Baseline: testcases/wgpu_golden/G06_live2d/baseline.rgba

Fixed t=0 uniforms so MAE is stable. Full animated sample remains
RENPY_HOST_GATE=live2d (non-golden regression).
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


def _make_checker(w, h, c0, c1):
    pix = bytearray()
    for y in range(h):
        for x in range(w):
            c = c0 if ((x // 4) ^ (y // 4)) & 1 == 0 else c1
            pix.extend(c)
    return bytes(pix)


def _quad(cx, cy, hw, hh, alpha=1.0):
    return [
        cx - hw, cy - hh, 0.0, 1.0, 1, 1, 1, alpha,
        cx + hw, cy - hh, 1.0, 1.0, 1, 1, 1, alpha,
        cx + hw, cy + hh, 1.0, 0.0, 1, 1, 1, alpha,
        cx - hw, cy + hh, 0.0, 0.0, 1, 1, 1, alpha,
    ]


body = renpy_host.create_texture_rgba(
    32, 32, _make_checker(32, 32, (220, 180, 140, 255), (200, 150, 120, 255))
)
hair = renpy_host.create_texture_rgba(
    32, 32, _make_checker(32, 32, (80, 60, 160, 255), (120, 90, 200, 255))
)
face = renpy_host.create_texture_rgba(
    32, 32, _make_checker(32, 32, (255, 220, 200, 255), (255, 200, 180, 255))
)
mask_src_pix = bytearray()
for y in range(64):
    for x in range(64):
        d = abs(x - 32) / 32.0
        a = int(max(0.0, 1.0 - d * 1.4) * 255)
        mask_src_pix.extend([255, 255, 255, a])
mask_src = renpy_host.create_texture_rgba(64, 64, bytes(mask_src_pix))

idx = [0, 1, 2, 0, 2, 3]
mesh_body = renpy_host.create_mesh(_quad(0.0, -0.15, 0.35, 0.55), idx)
mesh_hair = renpy_host.create_mesh(_quad(0.0, 0.35, 0.40, 0.30), idx)
mesh_face = renpy_host.create_mesh(_quad(0.0, 0.15, 0.22, 0.22), idx)
mesh_mask_quad = renpy_host.create_mesh(_quad(0.0, 0.0, 1.0, 1.0), idx)

pipe_tex = renpy_host.textured_pipeline()
pipe_flip = renpy_host.live2d_flip_pipeline()
pipe_colors = renpy_host.live2d_colors_pipeline()
pipe_mask = renpy_host.live2d_mask_pipeline()
mask_rtt = renpy_host.create_render_texture(128, 128)

# Fixed idle pose (t=0) — no time variation.
mask_u = [1.0, 1.0, 0.5, 0.5, 0.5] + [0.0] * 11
colors_u = [1.0, 1.0, 1.0, 1.0, 0.05, 0.02, 0.08, 0.0] + [0.0] * 8

for _ in range(8):
    # Bake mask into RTT: present WHILE target is active (host RTT contract).
    # end_target before end_frame_present dumps the mask quad onto game RT.
    renpy_host.begin_target(mask_rtt)
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe_tex, mesh_mask_quad, mask_src)
    renpy_host.end_frame_present()
    renpy_host.end_target()

    renpy_host.begin_frame()
    renpy_host.draw_model(pipe_flip, mesh_body, body)
    renpy_host.draw_model(pipe_colors, mesh_hair, hair, None, colors_u)
    renpy_host.draw_model(pipe_mask, mesh_face, face, mask_rtt, mask_u)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

ok, msg = compare_or_bootstrap("G06_live2d", w, h, rgba)
out = gate_result_path("g06")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
