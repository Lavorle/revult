"""
G-WgpuDraw smoke — exercise WgpuDraw RTT + screenshot + shader registry.

Gate name: g_wgpudraw  (optional; validates Python layer for task #20)
Loaded via py.run; host run_file injects RENPY_HOST_BASE.
"""

import os
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host

import renpy

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())

# Force host_build so register_builtin_core installs Phase 5 parts.
renpy.host_build = True
import renpy.wgpu.draw as draw_mod
from renpy.wgpu import shaders

shaders.register_builtin_core()
parts = shaders.list_wgsl_parts()
needed = {
    "renpy.blur",
    "renpy.dissolve",
    "renpy.imagedissolve",
    "renpy.matrixcolor",
    "renpy.alpha_mask",
    "renpy.mask",
}
missing = sorted(needed - set(parts))
if missing:
    raise RuntimeError(f"missing WGSL builtins: {missing}")

d = draw_mod.WgpuDraw()
assert d.init((1280, 720))

# RTT path: solid surface → render_to_texture → handle
from renpy.pygame.surface import Surface

surf = Surface((32, 32))
surf.fill((0, 128, 255, 255))
rtt = d.render_to_texture(surf)
assert isinstance(rtt, int) and rtt > 0, rtt

# Draw dissolve + blur via helpers onto game RT, then screenshot.
red = Surface((4, 4))
red.fill((255, 0, 0, 255))
tex = d.load_texture(red, transient=True)

renpy_host.begin_frame()
d.draw_dissolve(tex)
renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

renpy_host.begin_frame()
d.draw_blur(tex, blur_log2=1.5)
renpy_host.end_frame_present()

shot = d.screenshot()
assert shot is not None, "screenshot returned None"
sw, sh = shot.get_size()
assert sw > 0 and sh > 0

w, h, rgba = d.screenshot_rgba()
assert len(rgba) == w * h * 4

msg = (
    f"[g_wgpudraw] parts={len(parts)} rtt={rtt} shot={sw}x{sh} "
    f"rgba={w}x{h} ok=True"
)
out = Path(_base) / "host" / "target" / "gate-g_wgpudraw.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
print(msg, flush=True)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
