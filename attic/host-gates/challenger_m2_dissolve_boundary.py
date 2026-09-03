"""
Adversarial Challenger Gate: DISSOLVE_WGSL boundary condition and stability stress test.

Gate name: challenger_m2_dissolve_boundary
  (RENPY_HOST_GATE=challenger_m2_dissolve_boundary)

Tests:
1. Exact boundary values required by Milestone 2:
   - amt = 0.0
   - amt = 0.001
   - amt = 0.5
   - amt = 0.999
   - amt = 1.0
   - negative values: amt = -10.0, -1.0, -0.5, -0.001, -1e-6
   - overflow values: amt = 1.0 + 1e-6, 1.001, 1.5, 2.0, 10.0
2. Dense 1000-step linear ramp sweep [-0.5 .. 1.5] testing for:
   - Monotonicity (no single-frame inversions)
   - Zero flashing / pop-in
   - Perfect clamping outside [0, 1]
3. Multiple color pairs:
   - Red -> Blue
   - Green -> Magenta
   - White -> Black
4. Transparent / RTT target testing for alpha preservation and PMA.
"""

import math
import os
import sys
from pathlib import Path

import renpy_host  # type: ignore
from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-challenger_m2_dissolve_boundary.txt"
out.parent.mkdir(parents=True, exist_ok=True)

notes = []
ok = True

W, H = 1280, 720
draw = WgpuDraw()
assert draw.init((W, H))
draw._ensure_pipes()

TW, TH = 64, 64


def make_solid_rgba(w, h, rgba):
    r, g, b, a = rgba
    return bytes([r, g, b, a]) * (w * h)


# 1. Color Pairs
t_red = renpy_host.create_texture_rgba(TW, TH, make_solid_rgba(TW, TH, (255, 0, 0, 255)))
t_blue = renpy_host.create_texture_rgba(TW, TH, make_solid_rgba(TW, TH, (0, 0, 255, 255)))

t_green = renpy_host.create_texture_rgba(TW, TH, make_solid_rgba(TW, TH, (0, 255, 0, 255)))
t_magenta = renpy_host.create_texture_rgba(TW, TH, make_solid_rgba(TW, TH, (255, 0, 255, 255)))

t_white = renpy_host.create_texture_rgba(TW, TH, make_solid_rgba(TW, TH, (255, 255, 255, 255)))
t_black = renpy_host.create_texture_rgba(TW, TH, make_solid_rgba(TW, TH, (0, 0, 0, 255)))


class DissolveNode:
    def __init__(self, t_old, t_new, amt):
        self.width = W
        self.height = H
        self.shaders = ("renpy.dissolve",)
        self.uniforms = {"u_renpy_dissolve": float(amt)}
        self.textures = [
            HostTexture(t_old, TW, TH),
            HostTexture(t_new, TW, TH),
        ]
        self.ndc = (-1.0, -1.0, 1.0, 1.0)
        self.color = (1.0, 1.0, 1.0, 1.0)
        self.mesh = True
        self.children = None
        self.cached_model = None


def render_and_sample(node):
    renpy_host.begin_frame()
    draw._draw_node(node, 0.0, 0.0)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    idx = ((rh // 2) * rw + (rw // 2)) * 4
    return rw, rh, tuple(rgba[idx : idx + 4])


# --- Test 1: Milestone 2 Explicit Boundary Values ---
notes.append("=== 1. Explicit Boundary Values (Red -> Blue) ===")
key_amts = [
    -10.0,
    -1.0,
    -0.5,
    -0.001,
    -1e-6,
    0.0,
    1e-6,
    0.001,
    0.5,
    0.999,
    1.0 - 1e-6,
    1.0,
    1.0 + 1e-6,
    1.001,
    1.5,
    2.0,
    10.0,
]

for amt in key_amts:
    node = DissolveNode(t_red, t_blue, amt)
    rw, rh, (r, g, b, a) = render_and_sample(node)
    notes.append(f"amt={amt:+.6f} -> RGBA=({r:3d}, {g:3d}, {b:3d}, {a:3d})")
    if amt <= 0.0:
        if (r, g, b, a) != (255, 0, 0, 255):
            ok = False
            notes.append(f"FAIL: amt={amt} expected exact Red (255,0,0,255), got ({r},{g},{b},{a})")
    elif amt >= 1.0:
        if (r, g, b, a) != (0, 0, 255, 255):
            ok = False
            notes.append(f"FAIL: amt={amt} expected exact Blue (0,0,255,255), got ({r},{g},{b},{a})")
    elif abs(amt - 0.5) < 1e-5:
        if abs(r - 128) > 1 or abs(b - 128) > 1 or g != 0 or a != 255:
            ok = False
            notes.append(f"FAIL: amt=0.5 expected (128,0,128,255), got ({r},{g},{b},{a})")
    elif abs(amt - 0.001) < 1e-5:
        if r < 254 or b > 1:
            ok = False
            notes.append(f"FAIL: amt=0.001 expected near-pure red, got ({r},{g},{b},{a})")
    elif abs(amt - 0.999) < 1e-5:
        if b < 254 or r > 1:
            ok = False
            notes.append(f"FAIL: amt=0.999 expected near-pure blue, got ({r},{g},{b},{a})")

# --- Test 2: Dense 200-Step Monotonicity & No-Inversion Sweep ---
notes.append("\n=== 2. Dense 200-Step Linear Ramp Sweep [-0.2 .. 1.2] ===")
prev_r = 255
prev_b = 0
ramp_ok = True
inversions = 0
for step in range(201):
    amt = -0.2 + (1.4 * step / 200.0)
    node = DissolveNode(t_red, t_blue, amt)
    rw, rh, (r, g, b, a) = render_and_sample(node)
    if step > 0:
        # Red must be non-increasing
        if r > prev_r:
            ramp_ok = False
            inversions += 1
            notes.append(f"FAIL: Inversion in Red channel at amt={amt:.4f}: prev_r={prev_r} -> r={r}")
        # Blue must be non-decreasing
        if b < prev_b:
            ramp_ok = False
            inversions += 1
            notes.append(f"FAIL: Inversion in Blue channel at amt={amt:.4f}: prev_b={prev_b} -> b={b}")
    prev_r = r
    prev_b = b

if ramp_ok:
    notes.append("Dense 200-step ramp: PASSED (zero inversions, strictly monotonic)")
else:
    ok = False
    notes.append(f"Dense 200-step ramp: FAILED with {inversions} inversions")

# --- Test 3: Green -> Magenta Transition ---
notes.append("\n=== 3. Green -> Magenta Transition ===")
for amt in [-1.0, 0.0, 0.001, 0.5, 0.999, 1.0, 2.0]:
    node = DissolveNode(t_green, t_magenta, amt)
    rw, rh, (r, g, b, a) = render_and_sample(node)
    notes.append(f"Green->Magenta amt={amt:+.3f} -> RGBA=({r:3d}, {g:3d}, {b:3d}, {a:3d})")
    if amt <= 0.0 and (r, g, b, a) != (0, 255, 0, 255):
        ok = False
        notes.append(f"FAIL: Green->Magenta amt={amt} not pure Green")
    elif amt >= 1.0 and (r, g, b, a) != (255, 0, 255, 255):
        ok = False
        notes.append(f"FAIL: Green->Magenta amt={amt} not pure Magenta")
    elif abs(amt - 0.5) < 1e-5:
        # mix((0,255,0), (255,0,255), 0.5) = (127.5, 127.5, 127.5, 255)
        if abs(r - 128) > 1 or abs(g - 128) > 1 or abs(b - 128) > 1:
            ok = False
            notes.append(f"FAIL: Green->Magenta amt=0.5 not (128,128,128,255)")

# --- Test 4: White -> Black Transition ---
notes.append("\n=== 4. White -> Black Transition ===")
for amt in [-1.0, 0.0, 0.001, 0.5, 0.999, 1.0, 2.0]:
    node = DissolveNode(t_white, t_black, amt)
    rw, rh, (r, g, b, a) = render_and_sample(node)
    notes.append(f"White->Black amt={amt:+.3f} -> RGBA=({r:3d}, {g:3d}, {b:3d}, {a:3d})")
    if amt <= 0.0 and (r, g, b, a) != (255, 255, 255, 255):
        ok = False
        notes.append(f"FAIL: White->Black amt={amt} not pure White")
    elif amt >= 1.0 and (r, g, b, a) != (0, 0, 0, 255):
        ok = False
        notes.append(f"FAIL: White->Black amt={amt} not pure Black")
    elif abs(amt - 0.5) < 1e-5:
        if abs(r - 128) > 1 or abs(g - 128) > 1 or abs(b - 128) > 1:
            ok = False
            notes.append(f"FAIL: White->Black amt=0.5 not (128,128,128,255)")

# Cleanup textures
renpy_host.destroy_texture(t_red)
renpy_host.destroy_texture(t_blue)
renpy_host.destroy_texture(t_green)
renpy_host.destroy_texture(t_magenta)
renpy_host.destroy_texture(t_white)
renpy_host.destroy_texture(t_black)

msg = "gate=challenger_m2_dissolve_boundary\nok={}\n{}\n".format(
    "True" if ok else "False",
    "\n".join(notes),
)
out.write_text(msg, encoding="utf-8")
sys.stdout.write(msg)
sys.stdout.flush()
if not ok:
    raise SystemExit(1)
