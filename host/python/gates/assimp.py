"""
Phase 8 assimp/model gate — buffer upload + draw_model sample.

Gate name: assimp  (RENPY_HOST_GATE=assimp → host/target/gate-assimp.txt)

MVP: procedural cube + triangle meshes (no system assimp / pyassimp).
Real assimp.pyx remains on the SDL tree; host ifdef is Phase 9 strip work.
iostream bridge is exercised via MeshData blob round-trip.

Note: no `from __future__` — host run_file prepends imports before this source.
"""

import os
import tempfile
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host

from renpy.wgpu import model as model_mod

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-assimp.txt"
out.parent.mkdir(parents=True, exist_ok=True)

# --- 1. Procedural meshes ---------------------------------------------------
cube = model_mod.procedural_cube_isometric(cx=-0.25, cy=0.05, size=0.32)
tri = model_mod.procedural_triangle(cx=0.55, cy=-0.15, size=0.4)
quad = model_mod.procedural_quad(x0=0.15, y0=0.25, x1=0.75, y1=0.75)

assert cube.vertex_count == 12, cube.vertex_count  # 3 faces × 4 verts
assert cube.index_count == 18, cube.index_count  # 3 faces × 6 idx
assert tri.vertex_count == 3 and tri.indices is None
assert quad.vertex_count == 4 and quad.index_count == 6

# --- 2. iostream bridge round-trip (memory + file) --------------------------
cube_rt = model_mod.mesh_via_iostream(cube)
assert cube_rt.vertex_count == cube.vertex_count
assert cube_rt.index_count == cube.index_count
assert abs(cube_rt.vertices[0] - cube.vertices[0]) < 1e-6

tmp = Path(tempfile.gettempdir()) / "renpy-host-phase8-mesh.rpym"
quad_rt = model_mod.mesh_via_iostream_file(quad, str(tmp))
assert quad_rt.vertex_count == quad.vertex_count
assert quad_rt.index_count == quad.index_count
try:
    tmp.unlink()
except OSError:
    pass

# --- 3. Upload buffers via create_mesh --------------------------------------
mesh_cube = model_mod.upload_mesh(cube_rt)
mesh_tri = model_mod.upload_mesh(tri)
mesh_quad = model_mod.upload_mesh(quad_rt)
assert mesh_cube > 0 and mesh_tri > 0 and mesh_quad > 0

tex = model_mod.make_checker_texture(4)
solid = renpy_host.solid_pipeline()
textured = renpy_host.textured_pipeline()

# --- 4. draw_model solid + textured sample ----------------------------------
frames = 0
for _ in range(24):
    renpy_host.begin_frame()
    # Solid multi-triangle cube (index buffer path)
    model_mod.draw_solid_model(mesh_cube, solid)
    # Solid triangle (non-indexed path)
    model_mod.draw_solid_model(mesh_tri, solid)
    # Textured quad
    model_mod.draw_textured_model(mesh_quad, tex, textured)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
    frames += 1

# Optional WgpuDraw helper path (does not require full renpy bootstrap).
try:
    from renpy.wgpu.draw import WgpuDraw

    d = WgpuDraw()
    assert d.init((1280, 720))
    renpy_host.begin_frame()
    d.draw_model_mesh(mesh_cube, texture=None)
    d.draw_model_mesh(mesh_quad, texture=tex)
    renpy_host.end_frame_present()
    wgpu_ok = True
except Exception as e:  # pragma: no cover - defensive
    wgpu_ok = False
    wgpu_err = repr(e)
else:
    wgpu_err = ""

ok = bool(wgpu_ok) and frames >= 1 and mesh_cube > 0 and mesh_tri > 0 and mesh_quad > 0
msg = (
    f"[assimp-gate] mode=procedural "
    f"cube_v={cube.vertex_count} cube_i={cube.index_count} "
    f"mesh_cube={mesh_cube} mesh_tri={mesh_tri} mesh_quad={mesh_quad} "
    f"tex={tex} solid={solid} textured={textured} "
    f"frames={frames} iostream=ok wgpudraw={wgpu_ok} "
    f"note=assimp.pyx_sdl_tree_deferred_phase9 "
    f"ok={ok}"
)
if not wgpu_ok:
    msg += f" wgpu_err={wgpu_err}"

out.write_text(msg + "\n", encoding="utf-8")
print(msg, flush=True)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
