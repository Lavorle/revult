"""
AC5 — subsurface UV fidelity (host mesh UVs).

Gate name: tex_subsurface_uv  (RENPY_HOST_GATE=tex_subsurface_uv)

Creates a 2x1 texture [RED | BLUE] and draws a full-NDC quad sampling only the
BLUE half via UVs. Center pixel of game RT must be blue-dominant, not red.
"""

from pathlib import Path

import renpy_host

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

# 2x1: left red, right blue (opaque).
pix = bytes([255, 0, 0, 255, 0, 0, 255, 255])
tex = renpy_host.create_texture_rgba(2, 1, pix)

# Full NDC quad sampling only right texel: u in [0.5, 1.0], v in [0, 1]
# Vertex layout: pos.xy, uv.xy, color.rgba
u0, u1 = 0.5, 1.0
v0, v1 = 1.0, 0.0  # match WgpuDraw._mesh_quad_ndc v convention (bottom=1, top=0)
verts = [
    -1.0, -1.0, u0, v0, 1, 1, 1, 1,
     1.0, -1.0, u1, v0, 1, 1, 1, 1,
     1.0,  1.0, u1, v1, 1, 1, 1, 1,
    -1.0,  1.0, u0, v1, 1, 1, 1, 1,
]
idx = [0, 1, 2, 0, 2, 3]
mesh = renpy_host.create_mesh(verts, idx)
pipe = renpy_host.textured_pipeline()

for _ in range(4):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

cx, cy = w // 2, h // 2
i = (cy * w + cx) * 4
r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
# Blue-dominant (allow some linear filtering bleed from red neighbor).
ok = b > r + 20 and b > 100 and a > 200
msg = f"center=({r},{g},{b},{a}) ok={ok} (expect blue-dominant from u∈[0.5,1])"
out = Path("target/gate-tex_subsurface_uv.txt")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
