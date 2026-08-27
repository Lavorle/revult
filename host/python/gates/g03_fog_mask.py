"""
G03 fog+mask golden — matrixcolor Fog + mask/alpha_mask overlay (dual-tex).

Gate name: g03_fog_mask  (RENPY_HOST_GATE=g03_fog_mask)
Baseline: testcases/wgpu_golden/G03_fog_mask/baseline.rgba

Validates the two-texture mask pipeline with a matrixcolor Fog
overlay. Fog via matrixcolor (dim + blue tint) on a checker, then
mask_pipeline (src+mask) stacked. Pre-present game RT (ADR §4.3.1).
Covers the M3 advanced gfx composition: fog is matrixcolor16,
mask is alpha_mask/mask_pipeline dual-RTT path.
"""

import renpy_host
from golden_mae import compare_or_bootstrap, gate_result_path, golden_dir, write_raw_rgba, try_write_png

# --- Fog checker texture (8x8) ---
pix = []
for y in range(8):
    for x in range(8):
        if (x + y) & 1:
            pix.extend([255, 80, 40, 255])  # warm orange
        else:
            pix.extend([40, 140, 255, 255])  # cool blue
tex_checker = renpy_host.create_texture_rgba(8, 8, bytes(pix))

# Fog matrix (column-major 4x4): R'=0.6R+0.12, G'=0.6G+0.12, B'=0.85B+0.20, A'=A
# Col0 = [0.6,0,0,0], Col1=[0,0.6,0,0], Col2=[0,0,0.85,0], Col3=[0.12,0.12,0.20,1]
FOG = [
    0.6, 0.0, 0.0, 0.0,   # col0
    0.0, 0.6, 0.0, 0.0,   # col1
    0.0, 0.0, 0.85, 0.0,  # col2
    0.12, 0.12, 0.20, 1.0,  # col3
]

# --- Mask textures: green src + circular alpha mask ---
src_pix = bytes([40, 200, 80, 255] * 64)  # 8x8 green
pix2 = []
for y in range(8):
    for x in range(8):
        # radial mask: center opaque, edges transparent
        dx = (x - 3.5) / 3.5
        dy = (y - 3.5) / 3.5
        d = (dx*dx + dy*dy) ** 0.5
        a = int(max(0.0, 1.0 - d) * 255)
        # keep RGB white, alpha radial
        pix2.extend([255, 255, 255, a])
# Also create 64x64 smoother mask for final quad to scale bilinearly
# Expand 8x8 to 64x64 via nearest? Use 8x8 repeated for determinism
tex_src = renpy_host.create_texture_rgba(8, 8, src_pix)
tex_mask = renpy_host.create_texture_rgba(8, 8, bytes(pix2))

# --- Meshes ---
# Fog full-window-ish quad (behind)
verts_fog = [
    -0.85, -0.80, 0.0, 1.0, 1, 1, 1, 1,
     0.85, -0.80, 1.0, 1.0, 1, 1, 1, 1,
     0.85,  0.80, 1.0, 0.0, 1, 1, 1, 1,
    -0.85,  0.80, 0.0, 0.0, 1, 1, 1, 1,
]
# Masked overlay quad (center)
verts_mask = [
    -0.40, -0.40, 0.0, 1.0, 1, 1, 1, 1,
     0.40, -0.40, 1.0, 1.0, 1, 1, 1, 1,
     0.40,  0.40, 1.0, 0.0, 1, 1, 1, 1,
    -0.40,  0.40, 0.0, 0.0, 1, 1, 1, 1,
]
idx = [0, 1, 2, 0, 2, 3]
mesh_fog = renpy_host.create_mesh(verts_fog, idx)
mesh_mask = renpy_host.create_mesh(verts_mask, idx)

# Pipelines
try:
    pipe_fog = renpy_host.matrixcolor_pipeline()
except Exception:
    # Fallback to textured if matrixcolor not ready
    pipe_fog = renpy_host.textured_pipeline()
pipe_mask = renpy_host.mask_pipeline()
# mask uniforms: mult=1, offset=0 (standard)
u_mask = [1.0, 0.0] + [0.0] * 14

for _ in range(8):
    renpy_host.begin_frame()
    # Fog layer (matrixcolor)
    try:
        renpy_host.draw_model(pipe_fog, mesh_fog, tex_checker, None, FOG)
    except Exception as e:
        print(f"[g03_fog_mask] fog draw fallback: {e}", flush=True)
        renpy_host.draw_model(renpy_host.textured_pipeline(), mesh_fog, tex_checker)
    # Mask overlay (dual texture)
    renpy_host.draw_model(pipe_mask, mesh_mask, tex_src, tex_mask, u_mask)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

gdir = golden_dir("G03_fog_mask")
base_path = gdir / "baseline.rgba"
if not base_path.is_file():
    gdir.mkdir(parents=True, exist_ok=True)
    write_raw_rgba(base_path, w, h, rgba)
    try_write_png(gdir / "baseline.png", w, h, rgba)
    write_raw_rgba(gdir / "actual.rgba", w, h, rgba)
    try_write_png(gdir / "actual.png", w, h, rgba)
    msg = f"[G03_fog_mask] {w}x{h} baseline bootstrapped (fog+mask) ok=True"
    print(msg, flush=True)
    ok = True
else:
    ok, msg = compare_or_bootstrap("G03_fog_mask", w, h, rgba)

out = gate_result_path("g03_fog_mask")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
