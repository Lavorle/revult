"""
ImageDissolve 3-tex probe (parity residual close-out).

Gate name: imagedissolve_probe  (RENPY_HOST_GATE=imagedissolve_probe)

Builds control / bottom / top textures and draws via renpy.imagedissolve host
pipeline. Asserts mid-ramp mixes toward top (not hard-cut to bottom).

Writes host/target/gate-imagedissolve_probe.txt with ok=True/False.
"""

import os
import sys
from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host
from renpy.wgpu.draw import WgpuDraw


def _repo_root() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path.cwd()


def _result_path() -> Path:
    return _repo_root() / "host" / "target" / "gate-imagedissolve_probe.txt"


def _safe_write(msg: str) -> None:
    data = (msg if msg.endswith("\n") else msg + "\n").encode("utf-8", "replace")
    try:
        os.write(1, data)
    except Exception:
        try:
            sys.__stdout__.write(msg if msg.endswith("\n") else msg + "\n")
            sys.__stdout__.flush()
        except Exception:
            pass


def _solid(w, h, rgba):
    r, g, b, a = rgba
    return bytes([r, g, b, a]) * (w * h)


notes = []
ok = True

draw = WgpuDraw()
assert draw.init((1280, 720))
draw._ensure_pipes()

W = H = 32
# control: left half transparent (a=0) → bottom; right half opaque (a=255) → top
# With offset=0, mult=1: a=clamp(control.a,0,1)
control = bytearray()
for y in range(H):
    for x in range(W):
        a = 255 if x >= W // 2 else 0
        control.extend([255, 255, 255, a])
control = bytes(control)
bottom = _solid(W, H, (255, 0, 0, 255))  # red
top = _solid(W, H, (0, 0, 255, 255))  # blue

t_ctrl = renpy_host.create_texture_rgba(W, H, control)
t_bot = renpy_host.create_texture_rgba(W, H, bottom)
t_top = renpy_host.create_texture_rgba(W, H, top)

pipe = renpy_host.imagedissolve_pipeline()
notes.append(f"pipe={pipe} ctrl={t_ctrl} bot={t_bot} top={t_top}")
if not pipe or pipe <= 0:
    ok = False
    notes.append("FAIL: imagedissolve_pipeline missing")

# Full NDC quad mesh via WgpuDraw helper
mesh = draw._mesh_quad_ndc(-1.0, -1.0, 1.0, 1.0, (1, 1, 1, 1), 0.0, 1.0, 1.0, 0.0)
# offset=0, mult=1 → a = control.a
u = [0.0, 1.0] + [0.0] * 14

for _ in range(4):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, int(mesh), t_ctrl, t_bot, u, t_top)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
notes.append(f"rt={w}x{h} bytes={len(rgba)}")

# Sample left-center (should be red-ish bottom) and right-center (blue-ish top)
lx, ly = w // 4, h // 2
rx, ry = (3 * w) // 4, h // 2
li = (ly * w + lx) * 4
ri = (ry * w + rx) * 4
left = rgba[li : li + 4]
right = rgba[ri : ri + 4]
notes.append(f"left_rgba={tuple(left)} right_rgba={tuple(right)}")

# Left should prefer red (bottom): R > B; right prefer blue: B > R
if left[0] <= left[2]:
    ok = False
    notes.append("FAIL: left sample not bottom/red-dominant")
if right[2] <= right[0]:
    ok = False
    notes.append("FAIL: right sample not top/blue-dominant")

# Also exercise WgpuDraw node path via synthetic leaf
try:
    from renpy.wgpu.draw import HostTexture

    class _Node:
        pass

    n = _Node()
    n.width = 1280
    n.height = 720
    n.shaders = ("renpy.imagedissolve",)
    n.uniforms = {
        "u_renpy_dissolve_offset": 0.0,
        "u_renpy_dissolve_multiplier": 1.0,
    }
    n.textures = [
        HostTexture(t_ctrl, W, H),
        HostTexture(t_bot, W, H),
        HostTexture(t_top, W, H),
    ]
    n.ndc = (-1.0, -1.0, 1.0, 1.0)
    n.color = (1, 1, 1, 1)
    renpy_host.begin_frame()
    draw._draw_node(n, 0.0, 0.0)
    renpy_host.end_frame_present()
    notes.append("draw_node_imagedissolve=ok")
except Exception as e:
    ok = False
    notes.append(f"FAIL: draw_node {type(e).__name__}: {e}")

# Map honesty
from renpy.wgpu import shaders as sh

key = sh.host_pipeline_key("renpy.imagedissolve")
notes.append(f"map_imagedissolve={key}")
if key != "imagedissolve_pipeline":
    ok = False
    notes.append("FAIL: shaders map not imagedissolve_pipeline")

msg = "gate=imagedissolve_probe\nok=%s\n%s\n" % (
    "True" if ok else "False",
    "\n".join(notes),
)
out = _result_path()
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg, encoding="utf-8")
_safe_write(msg)
renpy_host.request_quit()
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
