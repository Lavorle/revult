"""
Adversarial Stress Gate: Comprehensive MatrixColor & PMA Math Verification.
Tests DuskMatrix, SepiaMatrix, BrightnessMatrix, SaturationMatrix, ColorizeMatrix,
TintMatrix, OpacityMatrix, ContrastMatrix, InvertMatrix, HueMatrix and their combinations.
Verifies Premultiplied Alpha (PMA) math in straight vs premultiplied color spaces.
"""

import math
import os
import sys
import textwrap
from pathlib import Path

import renpy_host

def _repo_root():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path.cwd()

def _safe_write(msg):
    data = (msg if msg.endswith("\n") else msg + "\n").encode("utf-8", "replace")
    try:
        os.write(1, data)
    except Exception:
        try:
            sys.__stdout__.write(msg if msg.endswith("\n") else msg + "\n")
            sys.__stdout__.flush()
        except Exception:
            pass

root = _repo_root()
sys.path.insert(0, str(root))

import renpy
renpy.host_build = True

from renpy.display.matrix import Matrix
from renpy.color import Color
from renpy.wgpu import shaders as sh
from renpy.wgpu.composer import get_shader_cache
from renpy.wgpu.draw_pipeline import PipelineMixin

# Load 00matrixcolor.rpy definitions
_code = (root / "renpy" / "common" / "00matrixcolor.rpy").read_text(encoding="utf-8")
_pycode = _code[_code.find("init -1500 python:") + len("init -1500 python:"):]
_ns = {"Matrix": Matrix, "Color": Color, "DictEquality": object, "renpy": renpy, "math": math}
exec(textwrap.dedent(_pycode), _ns)

IdentityMatrix = _ns["IdentityMatrix"]
SaturationMatrix = _ns["SaturationMatrix"]
TintMatrix = _ns["TintMatrix"]
BrightnessMatrix = _ns["BrightnessMatrix"]
OpacityMatrix = _ns["OpacityMatrix"]
ContrastMatrix = _ns["ContrastMatrix"]
ColorizeMatrix = _ns["ColorizeMatrix"]
HueMatrix = _ns["HueMatrix"]
InvertMatrix = _ns["InvertMatrix"]
SepiaMatrix = _ns["SepiaMatrix"]

def DuskMatrix(tint="#ffeedd", brightness=-0.1, saturation=0.8):
    return TintMatrix(tint) * BrightnessMatrix(brightness) * SaturationMatrix(saturation)

pm = PipelineMixin()

def to_col_major_16(m):
    if hasattr(m, "__call__"):
        m = m(None, 1.0)
    return pm._matrix_to_floats(m)

def cpu_transform_pixel_pma(mat_16, r_u8, g_u8, b_u8, a_u8):
    """CPU reference oracle: transforms straight RGBA by 4x4 matrix and outputs PMA RGBA."""
    r_in = r_u8 / 255.0
    g_in = g_u8 / 255.0
    b_in = b_u8 / 255.0
    a_in = a_u8 / 255.0

    c_r = mat_16[0] * r_in + mat_16[4] * g_in + mat_16[8] * b_in + mat_16[12] * a_in
    c_g = mat_16[1] * r_in + mat_16[5] * g_in + mat_16[9] * b_in + mat_16[13] * a_in
    c_b = mat_16[2] * r_in + mat_16[6] * g_in + mat_16[10] * b_in + mat_16[14] * a_in
    c_a = mat_16[3] * r_in + mat_16[7] * g_in + mat_16[11] * b_in + mat_16[15] * a_in

    a_clamped = max(0.0, min(1.0, c_a))
    out_r = max(0.0, min(1.0, c_r * a_clamped))
    out_g = max(0.0, min(1.0, c_g * a_clamped))
    out_b = max(0.0, min(1.0, c_b * a_clamped))
    out_a = a_clamped

    return (
        out_r,
        out_g,
        out_b,
        out_a,
    )

def cpu_blend_pma(src_pma, dst_pma):
    """Hardware blend One / OneMinusSrcAlpha on PMA colors."""
    sr, sg, sb, sa = src_pma
    dr, dg, db, da = dst_pma
    out_r = sr + dr * (1.0 - sa)
    out_g = sg + dg * (1.0 - sa)
    out_b = sb + db * (1.0 - sa)
    out_a = sa + da * (1.0 - sa)
    return (
        round(max(0.0, min(1.0, out_r)) * 255.0),
        round(max(0.0, min(1.0, out_g)) * 255.0),
        round(max(0.0, min(1.0, out_b)) * 255.0),
        round(max(0.0, min(1.0, out_a)) * 255.0),
    )

notes = []
ok = True

print("[adversarial_matrixcolor_pma] Initializing test harness...")

# Test matrices list
test_cases = [
    ("Identity", IdentityMatrix()),
    ("DuskMatrix_default", DuskMatrix()),
    ("DuskMatrix_sunset", DuskMatrix(tint="#ff7733", brightness=-0.15, saturation=0.7)),
    ("DuskMatrix_twilight", DuskMatrix(tint="#8866aa", brightness=-0.2, saturation=0.6)),
    ("SepiaMatrix_default", SepiaMatrix()),
    ("SepiaMatrix_warm", SepiaMatrix(tint="#ffdd99")),
    ("BrightnessMatrix_pos", BrightnessMatrix(0.25)),
    ("BrightnessMatrix_neg", BrightnessMatrix(-0.3)),
    ("BrightnessMatrix_dark", BrightnessMatrix(-0.8)),
    ("SaturationMatrix_zero", SaturationMatrix(0.0)),
    ("SaturationMatrix_half", SaturationMatrix(0.5)),
    ("SaturationMatrix_boost", SaturationMatrix(2.0)),
    ("ColorizeMatrix_duotone", ColorizeMatrix("#1a0033", "#ffcc00")),
    ("ColorizeMatrix_bw", ColorizeMatrix("#000000", "#ffffff")),
    ("ColorizeMatrix_invert", ColorizeMatrix("#ffffff", "#000000")),
    ("TintMatrix_red", TintMatrix("#ff4444")),
    ("TintMatrix_blue", TintMatrix("#4488ff")),
    ("TintMatrix_semi_alpha", TintMatrix(Color((100, 200, 255, 128)))),
    ("OpacityMatrix_half", OpacityMatrix(0.5)),
    ("OpacityMatrix_low", OpacityMatrix(0.1)),
    ("ContrastMatrix_high", ContrastMatrix(1.8)),
    ("ContrastMatrix_low", ContrastMatrix(0.4)),
    ("InvertMatrix_full", InvertMatrix(1.0)),
    ("InvertMatrix_half", InvertMatrix(0.5)),
    ("HueMatrix_90", HueMatrix(90)),
    ("HueMatrix_180", HueMatrix(180)),
    ("Combo_Sepia_Brightness", SepiaMatrix() * BrightnessMatrix(0.1)),
    ("Combo_Brightness_Tint", BrightnessMatrix(-0.1) * TintMatrix("#ffaa55")),
    ("Combo_Saturation_Colorize", SaturationMatrix(0.5) * ColorizeMatrix("#002244", "#eedd88")),
    ("Combo_Colorize_Tint", ColorizeMatrix("#110022", "#ffeedd") * TintMatrix("#88ffff")),
    ("Combo_Contrast_Saturation", ContrastMatrix(1.4) * SaturationMatrix(1.2)),
    ("Combo_Triple_Sepia_Bright_Tint", SepiaMatrix() * BrightnessMatrix(-0.05) * TintMatrix("#ffeecc")),
]

# Set of test pixel inputs (straight RGBA)
test_pixels = [
    (255, 255, 255, 255, "white_opaque"),
    (255, 0, 0, 255, "red_opaque"),
    (0, 255, 0, 255, "green_opaque"),
    (0, 0, 255, 255, "blue_opaque"),
    (0, 0, 0, 255, "black_opaque"),
    (128, 128, 128, 255, "gray_opaque"),
    (255, 200, 100, 128, "orange_alpha_half"),
    (100, 150, 200, 64, "blue_alpha_quarter"),
    (255, 255, 255, 0, "white_alpha_zero"),
    (128, 64, 32, 0, "color_alpha_zero"),
]

# Setup mesh for rendering 1x1 quad spanning full NDC [-1, 1]
verts = [
    -1.0, -1.0, 0.0, 1.0, 1, 1, 1, 1,
     1.0, -1.0, 1.0, 1.0, 1, 1, 1, 1,
     1.0,  1.0, 1.0, 0.0, 1, 1, 1, 1,
    -1.0,  1.0, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])

pipe_prebaked = renpy_host.matrixcolor_pipeline()

if not sh.list_wgsl_parts():
    sh.register_builtin_core()
cache = get_shader_cache()
composed_res = cache.get(["renpy.texture", "renpy.matrixcolor"], hard_fail=True, has_texture=True)
pipe_composed = int(composed_res.pipeline)

pipes = [
    ("prebaked_matrixcolor", pipe_prebaked),
    ("composed_texture_matrixcolor", pipe_composed),
]

# First, establish a clean opaque baseline background on game_rt (e.g. dark blue)
bg_tex = renpy_host.create_texture_rgba(4, 4, bytes([20, 30, 40, 255] * 16))
pipe_tex = renpy_host.textured_pipeline()

total_checks = 0
failed_checks = 0
max_observed_delta = 0

for pipe_name, pipe_id in pipes:
    print(f"[adversarial_matrixcolor_pma] Testing pipeline {pipe_name} (id={pipe_id})...")
    for mat_name, mat_obj in test_cases:
        mat_16 = to_col_major_16(mat_obj)
        for r, g, b, a, px_name in test_pixels:
            # 1. First draw a known solid background to clear the game RT state
            renpy_host.begin_frame()
            renpy_host.draw_model(pipe_tex, mesh, bg_tex, None, None)
            renpy_host.end_frame_present()
            renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

            # Read back background
            w, h, bg_rgba = renpy_host.read_game_rt_rgba()
            cx, cy = w // 2, h // 2
            idx = (cy * w + cx) * 4
            bg_dst = (
                bg_rgba[idx] / 255.0,
                bg_rgba[idx + 1] / 255.0,
                bg_rgba[idx + 2] / 255.0,
                bg_rgba[idx + 3] / 255.0,
            )

            # 2. Draw test quad with matrixcolor
            tex_data = bytes([r, g, b, a] * 16)
            tex = renpy_host.create_texture_rgba(4, 4, tex_data)

            renpy_host.begin_frame()
            renpy_host.draw_model(pipe_id, mesh, tex, None, mat_16)
            renpy_host.end_frame_present()
            renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

            # Readback rendered pixel
            w, h, rgba = renpy_host.read_game_rt_rgba()
            gpu_r = rgba[idx]
            gpu_g = rgba[idx + 1]
            gpu_b = rgba[idx + 2]
            gpu_a = rgba[idx + 3]

            # Compute theoretical result via CPU PMA transform + hardware blend
            src_pma = cpu_transform_pixel_pma(mat_16, r, g, b, a)
            expected_r, expected_g, expected_b, expected_a = cpu_blend_pma(src_pma, bg_dst)

            dr = abs(gpu_r - expected_r)
            dg = abs(gpu_g - expected_g)
            db = abs(gpu_b - expected_b)
            da = abs(gpu_a - expected_a)
            delta = max(dr, dg, db, da)
            if delta > max_observed_delta:
                max_observed_delta = delta

            total_checks += 1
            # Allowed delta is 3 (out of 255) for float roundtrip across 8-bit unorm rasterizer
            if delta > 3:
                failed_checks += 1
                ok = False
                notes.append(
                    f"FAIL: {pipe_name} {mat_name} {px_name} (in={r},{g},{b},{a}) -> "
                    f"GPU=({gpu_r},{gpu_g},{gpu_b},{gpu_a}) vs EXP=({expected_r},{expected_g},{expected_b},{expected_a}) delta={delta}"
                )

            renpy_host.destroy_texture(tex)

notes.append(
    f"total_checks={total_checks} failed_checks={failed_checks} max_observed_delta={max_observed_delta}"
)
print(f"[adversarial_matrixcolor_pma] Completed {total_checks} checks. Failed: {failed_checks}. Max delta: {max_observed_delta}")

# Test PMA transparent zero-RGB guarantee:
# When alpha=0, src_pma must be (0, 0, 0, 0), so blending onto background must leave background 100% UNCHANGED.
print("[adversarial_matrixcolor_pma] Testing PMA transparent zero-RGB guarantee against background...")
# Draw black background
renpy_host.begin_frame()
renpy_host.draw_model(pipe_tex, mesh, bg_tex, None, None)
renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, bg_rgba = renpy_host.read_game_rt_rgba()
idx = (h // 2 * w + w // 2) * 4
orig_bg = (bg_rgba[idx], bg_rgba[idx+1], bg_rgba[idx+2], bg_rgba[idx+3])

# Draw alpha=0 quad with max brightness (1.0)
tex_trans = renpy_host.create_texture_rgba(4, 4, bytes([255, 255, 255, 0] * 16))
high_bright_16 = to_col_major_16(BrightnessMatrix(1.0))

renpy_host.begin_frame()
renpy_host.draw_model(pipe_composed, mesh, tex_trans, None, high_bright_16)
renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
idx = (h // 2 * w + w // 2) * 4
new_pixel = (rgba[idx], rgba[idx+1], rgba[idx+2], rgba[idx+3])

if new_pixel != orig_bg:
    ok = False
    notes.append(f"FAIL: Transparent pixel under BrightnessMatrix(1.0) corrupted background! Orig={orig_bg} New={new_pixel}")
else:
    notes.append(f"PMA transparent zero-RGB guarantee: PASS (Background preserved exactly as {orig_bg})")

renpy_host.destroy_texture(bg_tex)
renpy_host.destroy_texture(tex_trans)
renpy_host.destroy_mesh(mesh)

msg = "gate=adversarial_matrixcolor_pma\nok={}\n{}\n".format(
    "True" if ok else "False",
    "\n".join(notes),
)
out_path = root / "host" / "target" / "gate-adversarial_matrixcolor_pma.txt"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(msg, encoding="utf-8")
_safe_write(msg)
renpy_host.request_quit()
if not ok:
    raise SystemExit(1)
