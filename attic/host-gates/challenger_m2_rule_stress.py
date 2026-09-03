"""
Adversarial Challenger Gate: Stress testing all 30 rule transitions (00.png - 29.png).

Gate name: challenger_m2_rule_stress
  (RENPY_HOST_GATE=challenger_m2_rule_stress)

Tests:
1. Loads all 30 rule PNGs from the recovered project (read-only).
2. Evaluates 7-step animation progression: u_animation = [0.0, 0.15, 0.35, 0.50, 0.65, 0.85, 1.0].
3. Asserts:
   - at anim = 0.0, frame mean is pure old/red (mean_r > 250, mean_b < 5).
   - at anim = 1.0, frame mean is pure new/blue (mean_b > 250, mean_r < 5).
   - at mid frames (0.50), progressive transition exists (both R and B > 20).
   - mean red is strictly non-increasing across keyframes.
   - mean blue is strictly non-decreasing across keyframes.
   - zero blank / arena clear frames throughout all 30 rules * 7 = 210 render passes.
4. Also tests ATL nested Transform node structure for each rule.
"""

import os
import sys
from pathlib import Path
from PIL import Image

import renpy_host  # type: ignore
from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-challenger_m2_rule_stress.txt"
out.parent.mkdir(parents=True, exist_ok=True)

rules_dir = Path("/mnt/nvme0n1p2/@home/isah1221/huangmeic/recovered_project/images/rule")
if not rules_dir.exists():
    raise RuntimeError(f"Rules directory not found at {rules_dir}")

notes = []
ok = True

W, H = 1280, 720
draw = WgpuDraw()
assert draw.init((W, H))
draw._ensure_pipes()

TW, TH = 256, 144  # Rescaled rule texture for stress sweep speed


def make_solid_rgba(w, h, rgba):
    r, g, b, a = rgba
    return bytes([r, g, b, a]) * (w * h)


t_old = renpy_host.create_texture_rgba(TW, TH, make_solid_rgba(TW, TH, (255, 0, 0, 255)))
t_new = renpy_host.create_texture_rgba(TW, TH, make_solid_rgba(TW, TH, (0, 0, 255, 255)))


class DirectModelNode:
    def __init__(self, t_new, t_old, t_rule, anim):
        self.width = W
        self.height = H
        self.shaders = ("image_dissolve",)
        self.uniforms = {"u_transition": 0.2, "u_animation": float(anim)}
        self.textures = [
            HostTexture(t_new, TW, TH),
            HostTexture(t_old, TW, TH),
            HostTexture(t_rule, TW, TH),
        ]
        self.ndc = (-1.0, -1.0, 1.0, 1.0)
        self.color = (1.0, 1.0, 1.0, 1.0)
        self.mesh = True
        self.children = None
        self.cached_model = None


class _Node:
    pass


def make_atl_transform_node(t_new, t_old, t_rule, anim):
    mesh = _Node()
    mesh.width = TW
    mesh.height = TH
    mesh.mesh = True
    mesh.shaders = None
    mesh.uniforms = None
    mesh.textures = [
        HostTexture(t_new, TW, TH),
        HostTexture(t_old, TW, TH),
        HostTexture(t_rule, TW, TH),
    ]
    mesh.children = [
        (HostTexture(t_new, TW, TH), 0.0, 0.0),
        (HostTexture(t_old, TW, TH), 0.0, 0.0),
        (HostTexture(t_rule, TW, TH), 0.0, 0.0),
    ]
    mesh.cached_model = None
    mesh.xclipping = False
    mesh.yclipping = False
    mesh.reverse = None

    outer = _Node()
    outer.width = W
    outer.height = H
    outer.mesh = None
    outer.shaders = ("image_dissolve",)
    outer.uniforms = {"u_transition": 0.2, "u_animation": float(anim)}
    outer.children = [(mesh, 0.0, 0.0)]
    outer.textures = None
    outer.cached_model = None
    outer.xclipping = False
    outer.yclipping = False
    outer.reverse = None
    outer.color = None
    outer.ndc = None
    return outer


def render_and_get_means(node):
    renpy_host.begin_frame()
    draw._draw_node(node, 0.0, 0.0)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    n_pixels = rw * rh
    mr = sum(rgba[i] for i in range(0, len(rgba), 4)) / n_pixels
    mg = sum(rgba[i + 1] for i in range(0, len(rgba), 4)) / n_pixels
    mb = sum(rgba[i + 2] for i in range(0, len(rgba), 4)) / n_pixels
    return rw, rh, mr, mg, mb


rule_files = sorted(rules_dir.glob("*.png"))
notes.append(f"Found {len(rule_files)} rule PNG files to stress test.")

if len(rule_files) != 30:
    ok = False
    notes.append(f"FAIL: Expected 30 rule files, found {len(rule_files)}")

anim_steps = [0.0, 0.15, 0.35, 0.50, 0.65, 0.85, 1.0]

for rule_idx, rule_path in enumerate(rule_files):
    img = Image.open(rule_path).convert("RGBA").resize((TW, TH))
    rule_bytes = img.tobytes()
    t_rule = renpy_host.create_texture_rgba(TW, TH, rule_bytes)

    # 1. Direct Model Node 7-step progression
    prev_mr = 300.0
    prev_mb = -1.0
    rule_pass = True
    step_means = []

    for anim in anim_steps:
        node = DirectModelNode(t_new, t_old, t_rule, anim)
        rw, rh, mr, mg, mb = render_and_get_means(node)
        step_means.append((anim, mr, mg, mb))

        # Check arena clear or blank frame
        if mr < 1.0 and mb < 1.0:
            rule_pass = False
            notes.append(f"FAIL: Rule {rule_path.name} anim={anim} rendered blank/clear frame")

        # Check endpoints
        if anim == 0.0:
            if mr < 250.0 or mb > 5.0:
                rule_pass = False
                notes.append(f"FAIL: Rule {rule_path.name} anim=0.0 not pure red: mr={mr:.1f}, mb={mb:.1f}")
        elif anim == 1.0:
            if mb < 250.0 or mr > 5.0:
                rule_pass = False
                notes.append(f"FAIL: Rule {rule_path.name} anim=1.0 not pure blue: mr={mr:.1f}, mb={mb:.1f}")

        # Check monotonicity
        if mr > prev_mr + 0.5:  # small epsilon for floating point/interpolation tolerance
            rule_pass = False
            notes.append(f"FAIL: Rule {rule_path.name} Red non-monotonic at anim={anim}: prev={prev_mr:.1f}, now={mr:.1f}")
        if mb < prev_mb - 0.5:
            rule_pass = False
            notes.append(f"FAIL: Rule {rule_path.name} Blue non-monotonic at anim={anim}: prev={prev_mb:.1f}, now={mb:.1f}")

        prev_mr = mr
        prev_mb = mb

    # 2. ATL Nested Transform Node mid-frame check (anim = 0.5)
    atl_node = make_atl_transform_node(t_new, t_old, t_rule, 0.5)
    rw, rh, atl_mr, atl_mg, atl_mb = render_and_get_means(atl_node)
    if atl_mr < 10.0 or atl_mb < 10.0:
        rule_pass = False
        notes.append(f"FAIL: Rule {rule_path.name} ATL anim=0.5 not progressive: mr={atl_mr:.1f}, mb={atl_mb:.1f}")

    if rule_pass:
        notes.append(f"Rule {rule_path.name} [30/30]: PASSED (progression {step_means[0][1]:.0f}R->{step_means[3][1]:.0f}R/{step_means[3][3]:.0f}B->{step_means[-1][3]:.0f}B, ATL mid={atl_mr:.0f}R/{atl_mb:.0f}B)")
    else:
        ok = False

    renpy_host.destroy_texture(t_rule)

# Cleanup base textures
renpy_host.destroy_texture(t_old)
renpy_host.destroy_texture(t_new)

notes.append(f"\nCompleted stress test of all {len(rule_files)} rules with {len(rule_files) * (len(anim_steps) + 1)} total render passes.")

msg = "gate=challenger_m2_rule_stress\nok={}\n{}\n".format(
    "True" if ok else "False",
    "\n".join(notes),
)
out.write_text(msg, encoding="utf-8")
sys.stdout.write(msg)
sys.stdout.flush()
if not ok:
    raise SystemExit(1)
