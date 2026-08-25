"""
AC4 — texture cache invalidation / fingerprint (WgpuDraw.load_texture path).

Gate name: tex_cache_mutate  (RENPY_HOST_GATE=tex_cache_mutate)

Uses a minimal Surface-like object + WgpuDraw without full renpy product boot.
Verifies:
  1) load_texture caches by surface identity
  2) mutating pixels + mutated_surface forces re-upload (new handle or new GPU pixels)
"""

from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host

# Import WgpuDraw without full renpy.display boot when possible.
import sys
import os

base = os.environ.get("RENPY_HOST_BASE", "")
if base and base not in sys.path:
    sys.path.insert(0, base)

from renpy.wgpu.draw import WgpuDraw  # noqa: E402


class FakeSurf:
    """Minimal surface for load_texture: get_size + _pixels."""

    def __init__(self, w, h, rgba: bytes):
        self._w = w
        self._h = h
        self._pixels = bytearray(rgba)

    def get_size(self):
        return (self._w, self._h)

    def get_buffer(self):
        return bytes(self._pixels)


def solid(w, h, r, g, b, a=255):
    return bytes([r, g, b, a] * (w * h))


draw = WgpuDraw()
draw.init((64, 64))

surf = FakeSurf(2, 2, solid(2, 2, 255, 0, 0, 255))
tex1 = draw.load_texture(surf, transient=False)
h1 = int(tex1.handle)

# Same surface, same pixels → cache hit (same handle).
tex1b = draw.load_texture(surf, transient=False)
h1b = int(tex1b.handle)
same_handle = h1 == h1b and h1 > 0

# Mutate pixels in place WITHOUT mutated_surface — fingerprint must still re-upload.
surf._pixels[:] = solid(2, 2, 0, 255, 0, 255)
tex2 = draw.load_texture(surf, transient=False)
h2 = int(tex2.handle)
# Either new handle or same handle with rewritten pixels is OK if fingerprint works.
# Require handle change OR explicit destroy path: fingerprint mismatch → re-upload new id.
fp_reupload = h2 > 0 and (h2 != h1)

# Mutate again + mutated_surface should definitely invalidate.
surf._pixels[:] = solid(2, 2, 0, 0, 255, 255)
draw.mutated_surface(surf)
tex3 = draw.load_texture(surf, transient=False)
h3 = int(tex3.handle)
mut_ok = h3 > 0 and h3 != h2

# Draw tex3 full screen and sample center → blue-dominant.
pipe = renpy_host.textured_pipeline()
verts = [
    -1.0, -1.0, 0.0, 1.0, 1, 1, 1, 1,
     1.0, -1.0, 1.0, 1.0, 1, 1, 1, 1,
     1.0,  1.0, 1.0, 0.0, 1, 1, 1, 1,
    -1.0,  1.0, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
for _ in range(3):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, h3)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
cx, cy = max(0, w // 2), max(0, h // 2)
i = (cy * w + cx) * 4 if w and h else 0
r = g = b = a = 0
if w and h and len(rgba) >= i + 4:
    r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
pixel_ok = b > r + 20 and b > 80

ok = same_handle and fp_reupload and mut_ok and pixel_ok
msg = (
    f"same_handle={same_handle} h1={h1} h1b={h1b} "
    f"fp_reupload={fp_reupload} h2={h2} "
    f"mut_ok={mut_ok} h3={h3} "
    f"center=({r},{g},{b},{a}) pixel_ok={pixel_ok} ok={ok}"
)
out = Path("target/gate-tex_cache_mutate.txt")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
