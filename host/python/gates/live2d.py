"""
Phase 7 Live2D sample gate — multi-mesh + mask RTT + idle animation.

Gate name: live2d  (RENPY_HOST_GATE=live2d)
Writes: host/target/gate-live2d.txt

Pure host/ + renpy/wgpu sample — no Cubism Core / libLive2DCubismCore required.
Synthetic Cubism-like drawable meshes exercise:
  - live2d.mask (mask RTT via begin_target/end_target)
  - live2d.colors (time-varying multiply/screen)
  - live2d.flip_texture
  - multi-mesh draw_model for several idle frames

Real Cubism Core plug-in path (not exercised here):
  renpy.gl2.live2dmodel.Live2DModel already builds Mesh2 + Render +
  shader part names (live2d.mask / inverted_mask / colors / flip_texture)
  from Cubism Core math. Host path only needs to upload those meshes/
  textures and map part names via renpy.wgpu.shaders host_pipeline_key
  → renpy_host.live2d_*_pipeline(), then draw_model. See inventory:
  .omc/research/live2d-inventory-phase2.md
"""

import math
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

# Optional: register WGSL parts when renpy tree is importable.
try:
    import renpy

    renpy.host_build = True
    from renpy.wgpu import shaders

    shaders.register_builtin_core()
    parts = set(shaders.list_wgsl_parts())
    needed = {
        "live2d.mask",
        "live2d.inverted_mask",
        "live2d.colors",
        "live2d.flip_texture",
    }
    missing = sorted(needed - parts)
    if missing:
        raise RuntimeError(f"missing Live2D WGSL parts: {missing}")
    registry_ok = True
    registry_parts = sorted(needed)
except Exception as e:  # pragma: no cover - host may not have full renpy on path
    registry_ok = False
    registry_parts = []
    registry_err = repr(e)


def _make_checker(w: int, h: int, c0, c1) -> bytes:
    pix = bytearray()
    for y in range(h):
        for x in range(w):
            c = c0 if ((x // 4) ^ (y // 4)) & 1 == 0 else c1
            pix.extend(c)
    return bytes(pix)


def _quad(cx: float, cy: float, hw: float, hh: float, alpha: float = 1.0):
    # pos.xy, uv.xy, color.rgba  (stride 8 floats)
    return [
        cx - hw, cy - hh, 0.0, 1.0, 1, 1, 1, alpha,
        cx + hw, cy - hh, 1.0, 1.0, 1, 1, 1, alpha,
        cx + hw, cy + hh, 1.0, 0.0, 1, 1, 1, alpha,
        cx - hw, cy + hh, 0.0, 0.0, 1, 1, 1, alpha,
    ]


# --- Textures -----------------------------------------------------------------
# Body / hair / face style parts (synthetic atlas tiles).
body = renpy_host.create_texture_rgba(
    32, 32, _make_checker(32, 32, (220, 180, 140, 255), (200, 150, 120, 255))
)
hair = renpy_host.create_texture_rgba(
    32, 32, _make_checker(32, 32, (80, 60, 160, 255), (120, 90, 200, 255))
)
face = renpy_host.create_texture_rgba(
    32, 32, _make_checker(32, 32, (255, 220, 200, 255), (255, 200, 180, 255))
)

# Mask content drawn into RTT: soft vertical band (alpha gradient).
mask_src_pix = bytearray()
for y in range(64):
    for x in range(64):
        # Opaque center column, transparent edges → live2d.mask samples .a
        d = abs(x - 32) / 32.0
        a = int(max(0.0, 1.0 - d * 1.4) * 255)
        mask_src_pix.extend([255, 255, 255, a])
mask_src = renpy_host.create_texture_rgba(64, 64, bytes(mask_src_pix))

# --- Meshes (multi-drawable Cubism-like stack) --------------------------------
idx = [0, 1, 2, 0, 2, 3]
mesh_body = renpy_host.create_mesh(_quad(0.0, -0.15, 0.35, 0.55), idx)
mesh_hair = renpy_host.create_mesh(_quad(0.0, 0.35, 0.40, 0.30), idx)
mesh_face = renpy_host.create_mesh(_quad(0.0, 0.15, 0.22, 0.22), idx)
mesh_full = renpy_host.create_mesh(_quad(0.0, 0.0, 0.55, 0.75), idx)
mesh_mask_quad = renpy_host.create_mesh(_quad(0.0, 0.0, 1.0, 1.0), idx)

# --- Pipelines ----------------------------------------------------------------
pipe_tex = renpy_host.textured_pipeline()
pipe_flip = renpy_host.live2d_flip_pipeline()
pipe_colors = renpy_host.live2d_colors_pipeline()
pipe_mask = renpy_host.live2d_mask_pipeline()
pipe_inv = renpy_host.live2d_inverted_mask_pipeline()

# Mask RTT (Phase 5 begin_target/end_target)
mask_rtt = renpy_host.create_render_texture(128, 128)

# live2d.mask uniforms: data0=(model_w, model_h, ppu, off_x), data1.x=off_y
# With ppu≈1 and offset mapping NDC pos into [0,1] mask UV roughly.
def mask_uniforms(t: float):
    # Slight idle sway of mask offset (time-varying).
    ox = 0.5 + 0.05 * math.sin(t * 2.0)
    oy = 0.5 + 0.03 * math.cos(t * 1.7)
    return [
        1.0, 1.0,  # model_size
        0.5,       # ppu — pos in NDC * 0.5 + offset → ~[0,1]
        ox, oy,
    ] + [0.0] * 11


def colors_uniforms(t: float):
    # Idle: gentle multiply pulse + slight screen tint.
    pulse = 0.85 + 0.15 * (0.5 + 0.5 * math.sin(t * 3.0))
    mult = [pulse, pulse, pulse, 1.0]
    screen = [0.05 * (0.5 + 0.5 * math.sin(t)), 0.02, 0.08, 0.0]
    return mult + screen + [0.0] * 8


FRAMES = 24
t0 = renpy_host.get_ticks_ms()

for i in range(FRAMES):
    t = i / 60.0  # synthetic idle clock (independent of wall)

    # 1) Build mask RTT
    renpy_host.begin_frame()
    renpy_host.begin_target(mask_rtt)
    renpy_host.draw_model(pipe_tex, mesh_mask_quad, mask_src)
    renpy_host.end_target()
    renpy_host.end_frame_present()

    # 2) Compose multi-mesh character with Live2D parts
    renpy_host.begin_frame()
    # body (flip texture orientation like Cubism)
    renpy_host.draw_model(pipe_flip, mesh_body, body)
    # hair with colors idle
    renpy_host.draw_model(pipe_colors, mesh_hair, hair, None, colors_uniforms(t))
    # face under live2d.mask using mask RTT
    renpy_host.draw_model(
        pipe_mask, mesh_face, face, mask_rtt, mask_uniforms(t)
    )
    # full-body inverted_mask accent (shows mask path works both ways)
    if i % 2 == 0:
        renpy_host.draw_model(
            pipe_inv, mesh_full, body, mask_rtt, mask_uniforms(t + 0.5)
        )
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

# Readback smoke — non-empty RT after multi-mesh present.
w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))

# Sample center-ish pixel for non-clear evidence (not strict golden).
cx, cy = w // 2, h // 2
off = (cy * w + cx) * 4
center = tuple(rgba[off : off + 4])
# Count non-near-clear pixels as crude "drew something" signal.
drawn = 0
step = max(1, (w * h) // 5000)
for p in range(0, w * h, step):
    o = p * 4
    r, g, b, a = rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]
    if a > 8 and (r > 20 or g > 20 or b > 20):
        drawn += 1

ok = drawn > 10 and all(
    x is not None
    for x in (pipe_mask, pipe_inv, pipe_colors, pipe_flip, mask_rtt, mesh_body)
)

try:
    from golden_mae import gate_result_path

    out = gate_result_path("live2d")
except Exception:
    base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
    out = Path(base) / "host" / "target" / "gate-live2d.txt"
    if not out.parent.is_dir():
        out = Path("target") / "gate-live2d.txt"
out.parent.mkdir(parents=True, exist_ok=True)

reg = (
    f"registry=ok parts={','.join(registry_parts)}"
    if registry_ok
    else f"registry=skip err={registry_err}"
)
msg = (
    f"[live2d-gate] frames={FRAMES} meshes=4 mask_rtt={mask_rtt} "
    f"pipes=mask:{pipe_mask},inv:{pipe_inv},colors:{pipe_colors},flip:{pipe_flip} "
    f"rt={w}x{h} center_rgba={center} drawn_samples={drawn} {reg} ok={ok}"
)
out.write_text(msg + "\n", encoding="utf-8")
print(msg)

if not ok:
    raise RuntimeError(msg)

renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
