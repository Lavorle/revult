"""
Adversarial Challenger Gate: Comprehensive Viewport & Mesh Clipping Parity Stress Test.

Gate name: challenger_m3_clip_stress
  (RENPY_HOST_GATE=challenger_m3_clip_stress)

Adversarial Stress Scenarios:
1. Deeply Nested Geometries:
   - 3-level nested clip hierarchy:
     L1: [100, 100, 500, 400] (clip_w=400, clip_h=300)
     L2: offset (50, 50), clip (200, 200) -> [150, 150, 350, 350]
     L3: offset (-30, -30), clip (200, 200) -> [120, 120, 320, 320]
     Effective intersection: [150, 150, 320, 320]
   - Child inside L3 with negative offset (-100, -80) and size 500x500 (RED)
   - Probe center (inside intersection) -> RED
   - Probe outside L3/L2 bounds -> BG (no bleed in any direction)
2. Single-Axis Clipping:
   - xclipping=True, yclipping=False: X is cropped, Y overflows
   - xclipping=False, yclipping=True: Y is cropped, X overflows
3. Sibling Clipped Viewports:
   - Left viewport [100, 200, 300, 400] (YELLOW)
   - Right viewport [500, 200, 700, 400] (CYAN)
   - Both children have negative offsets (-80, -60) and 400x400 size
   - Gap [300..500] must remain pure BG with zero cross-talk
4. Extreme Boundary Overflow & Negative Offsets:
   - Child size 3000x3000 with offset (-1000, -1000) under a 150x150 clip box
   - Verify interior is solid and exterior is untouched
5. Empty / Disjoint Intersection Short-Circuit:
   - Parent clip [50, 50, 150, 150], child clip at relative [200, 200, 300, 300] (disjoint)
   - Must safely cull child with zero visual artifacts
6. Persistent WgpuDraw Multi-Frame Isolation & Contamination Check:
   - Frame A: Huge Red draw at [0, 0, 400, 400]
   - Frame B: Clean BG + Green draw at [800, 400, 1100, 650]
   - Verify Frame B top-left has 0% Red residual
   - 30-frame rapid randomized stress loop on same WgpuDraw instance
"""

import os
import sys
from pathlib import Path

import renpy_host  # type: ignore
from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-challenger_m3_clip_stress.txt"
out.parent.mkdir(parents=True, exist_ok=True)

# Test Colors
BG = (20, 20, 30, 255)
RED = (230, 30, 30, 255)
GREEN = (30, 230, 30, 255)
BLUE = (30, 30, 230, 255)
YELLOW = (230, 230, 30, 255)
CYAN = (30, 230, 230, 255)
MAGENTA = (230, 30, 230, 255)


def make_solid_ht(w, h, rgba):
    r, g, b, a = rgba
    pixels = bytes([r, g, b, a]) * (w * h)
    handle = renpy_host.create_texture_rgba(w, h, pixels)
    return HostTexture(handle, w, h)


class FakeRender:
    def __init__(self, width, height, xclipping=False, yclipping=False):
        self.width = int(width)
        self.height = int(height)
        self.children = []
        self.mesh = None
        self.texture = None
        self.textures = None
        self.color = None
        self.shaders = None
        self.pipeline = None
        self.vertices = None
        self.indices = None
        self.cached_model = None
        self.blits = None
        self.ndc = None
        self.uniforms = None
        self.loaded = False
        self.forward = None
        self.reverse = None
        self.xclipping = bool(xclipping)
        self.yclipping = bool(yclipping)

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self

    def get_size(self):
        return (self.width, self.height)


def _sample(rgba, w, h, x, y):
    x = max(0, min(w - 1, int(x)))
    y = max(0, min(h - 1, int(y)))
    o = (y * w + x) * 4
    return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]


def _near(c, target, tol=40):
    return all(abs(int(c[i]) - int(target[i])) <= tol for i in range(3))


def _virt_to_rt(vx, vy, vw, vh, rw, rh):
    return int(vx * rw / float(vw)), int(vy * rh / float(vh))


def _present(draw, tree):
    for _ in range(2):
        draw.draw_screen(tree, flip=True)
        try:
            renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
        except Exception:
            pass
    w, h, rgba = renpy_host.read_game_rt_rgba()
    assert w > 0 and h > 0 and len(rgba) == w * h * 4, (w, h, len(rgba))
    return w, h, rgba


def main():
    vw, vh = 1280, 720
    draw = WgpuDraw()
    draw.init((vw, vh))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    bg_solid = make_solid_ht(vw, vh, BG)
    tex_red_500 = make_solid_ht(500, 500, RED)
    tex_green_500 = make_solid_ht(500, 500, GREEN)
    tex_blue_500 = make_solid_ht(500, 500, BLUE)
    tex_yellow_400 = make_solid_ht(400, 400, YELLOW)
    tex_cyan_400 = make_solid_ht(400, 400, CYAN)
    tex_huge_3000 = make_solid_ht(3000, 3000, MAGENTA)

    results = []

    # =========================================================================
    # Test 1: 3-Level Deep Nested Clip Hierarchy with Negative Offset (-100, -80)
    # =========================================================================
    # L1: placed at (100, 100), size 400x300 => [100, 100, 500, 400]
    # L2: placed at (50, 50), size 200x200 => [150, 150, 350, 350]
    # L3: placed at (-30, -30), size 200x200 => [120, 120, 320, 320]
    # Intersected AABB = [max(100,150,120), max(100,150,120), min(500,350,320), min(400,350,320)]
    #                  = [150, 150, 320, 320] (width=170, height=170)
    l1 = FakeRender(400, 300, xclipping=True, yclipping=True)
    l2 = FakeRender(200, 200, xclipping=True, yclipping=True)
    l3 = FakeRender(200, 200, xclipping=True, yclipping=True)
    # Child inside L3 with negative offset (-100, -80)
    l3.blit(tex_red_500, -100, -80)
    l2.blit(l3, -30, -30)
    l1.blit(l2, 50, 50)
    root1 = FakeRender(vw, vh)
    root1.blit(bg_solid, 0, 0)
    root1.blit(l1, 100, 100)

    rw, rh, rgba1 = _present(draw, root1)

    # Center of intersection [150, 150, 320, 320] is at (235, 235)
    c_center = _sample(rgba1, rw, rh, *_virt_to_rt(235, 235, vw, vh, rw, rh))
    # Probe points strictly outside intersection:
    # 1. Left of intersection (x=130, y=235) -> BG
    c_left = _sample(rgba1, rw, rh, *_virt_to_rt(130, 235, vw, vh, rw, rh))
    # 2. Right of intersection (x=340, y=235) -> BG
    c_right = _sample(rgba1, rw, rh, *_virt_to_rt(340, 235, vw, vh, rw, rh))
    # 3. Top of intersection (x=235, y=130) -> BG
    c_top = _sample(rgba1, rw, rh, *_virt_to_rt(235, 130, vw, vh, rw, rh))
    # 4. Bottom of intersection (x=235, y=340) -> BG
    c_bottom = _sample(rgba1, rw, rh, *_virt_to_rt(235, 340, vw, vh, rw, rh))

    t1_center_ok = _near(c_center, RED, tol=40)
    t1_left_ok = not _near(c_left, RED, tol=60)
    t1_right_ok = not _near(c_right, RED, tol=60)
    t1_top_ok = not _near(c_top, RED, tol=60)
    t1_bottom_ok = not _near(c_bottom, RED, tol=60)
    t1_ok = t1_center_ok and t1_left_ok and t1_right_ok and t1_top_ok and t1_bottom_ok
    results.append(("3level_nested_clip", t1_ok, f"center={c_center} left={c_left} right={c_right} top={c_top} bottom={c_bottom}"))

    # =========================================================================
    # Test 2: Single-Axis Clipping (X-only and Y-only)
    # =========================================================================
    # Box at (100, 100), size 200x200. Child size 500x500 at (-100, -80).
    # Case A: xclipping=True, yclipping=False -> X clipped to [100..300], Y overflows [20..520]
    p_xonly = FakeRender(200, 200, xclipping=True, yclipping=False)
    p_xonly.blit(tex_green_500, -100, -80)
    root2a = FakeRender(vw, vh)
    root2a.blit(bg_solid, 0, 0)
    root2a.blit(p_xonly, 100, 100)
    rw, rh, rgba2a = _present(draw, root2a)

    c2a_in = _sample(rgba2a, rw, rh, *_virt_to_rt(200, 200, vw, vh, rw, rh))
    c2a_left = _sample(rgba2a, rw, rh, *_virt_to_rt(50, 200, vw, vh, rw, rh))    # outside X -> BG
    c2a_top = _sample(rgba2a, rw, rh, *_virt_to_rt(200, 50, vw, vh, rw, rh))     # overflow Y -> GREEN!
    c2a_bottom = _sample(rgba2a, rw, rh, *_virt_to_rt(200, 350, vw, vh, rw, rh)) # overflow Y -> GREEN!

    t2a_ok = _near(c2a_in, GREEN) and (not _near(c2a_left, GREEN, tol=60)) and _near(c2a_top, GREEN) and _near(c2a_bottom, GREEN)
    results.append(("xclipping_only", t2a_ok, f"in={c2a_in} left_bg={c2a_left} top_green={c2a_top} bottom_green={c2a_bottom}"))

    # Case B: xclipping=False, yclipping=True -> Y clipped to [100..300], X overflows [0..500]
    p_yonly = FakeRender(200, 200, xclipping=False, yclipping=True)
    p_yonly.blit(tex_blue_500, -100, -80)
    root2b = FakeRender(vw, vh)
    root2b.blit(bg_solid, 0, 0)
    root2b.blit(p_yonly, 100, 100)
    rw, rh, rgba2b = _present(draw, root2b)

    c2b_in = _sample(rgba2b, rw, rh, *_virt_to_rt(200, 200, vw, vh, rw, rh))
    c2b_left = _sample(rgba2b, rw, rh, *_virt_to_rt(50, 200, vw, vh, rw, rh))    # overflow X -> BLUE!
    c2b_top = _sample(rgba2b, rw, rh, *_virt_to_rt(200, 50, vw, vh, rw, rh))     # outside Y -> BG
    c2b_bottom = _sample(rgba2b, rw, rh, *_virt_to_rt(200, 350, vw, vh, rw, rh)) # outside Y -> BG

    t2b_ok = _near(c2b_in, BLUE) and _near(c2b_left, BLUE) and (not _near(c2b_top, BLUE, tol=60)) and (not _near(c2b_bottom, BLUE, tol=60))
    results.append(("yclipping_only", t2b_ok, f"in={c2b_in} left_blue={c2b_left} top_bg={c2b_top} bottom_bg={c2b_bottom}"))

    # =========================================================================
    # Test 3: Sibling Side-by-Side Clipped Viewports (Zero Inter-Viewport Bleed)
    # =========================================================================
    # Left: [100, 200, 300, 400] (YELLOW 400x400 at -80, -60)
    # Right: [500, 200, 700, 400] (CYAN 400x400 at -80, -60)
    # Gap: [300..500] should remain pure BG
    left_vp = FakeRender(200, 200, xclipping=True, yclipping=True)
    left_vp.blit(tex_yellow_400, -80, -60)
    right_vp = FakeRender(200, 200, xclipping=True, yclipping=True)
    right_vp.blit(tex_cyan_400, -80, -60)

    root3 = FakeRender(vw, vh)
    root3.blit(bg_solid, 0, 0)
    root3.blit(left_vp, 100, 200)
    root3.blit(right_vp, 500, 200)
    rw, rh, rgba3 = _present(draw, root3)

    c3_left_in = _sample(rgba3, rw, rh, *_virt_to_rt(200, 300, vw, vh, rw, rh))
    c3_right_in = _sample(rgba3, rw, rh, *_virt_to_rt(600, 300, vw, vh, rw, rh))
    c3_gap_mid = _sample(rgba3, rw, rh, *_virt_to_rt(400, 300, vw, vh, rw, rh))
    c3_gap_near_l = _sample(rgba3, rw, rh, *_virt_to_rt(320, 300, vw, vh, rw, rh))
    c3_gap_near_r = _sample(rgba3, rw, rh, *_virt_to_rt(480, 300, vw, vh, rw, rh))

    t3_ok = (_near(c3_left_in, YELLOW) and _near(c3_right_in, CYAN) and
             (not _near(c3_gap_mid, YELLOW, tol=60)) and (not _near(c3_gap_mid, CYAN, tol=60)) and
             (not _near(c3_gap_near_l, YELLOW, tol=60)) and (not _near(c3_gap_near_r, CYAN, tol=60)))
    results.append(("sibling_viewports", t3_ok, f"left={c3_left_in} right={c3_right_in} gap_mid={c3_gap_mid}"))

    # =========================================================================
    # Test 4: Extreme Boundary Overflow (3000x3000 child at -1000, -1000 in 150x150)
    # =========================================================================
    p_extreme = FakeRender(150, 150, xclipping=True, yclipping=True)
    p_extreme.blit(tex_huge_3000, -1000, -1000)
    root4 = FakeRender(vw, vh)
    root4.blit(bg_solid, 0, 0)
    root4.blit(p_extreme, 400, 300)
    rw, rh, rgba4 = _present(draw, root4)

    c4_in = _sample(rgba4, rw, rh, *_virt_to_rt(475, 375, vw, vh, rw, rh))
    c4_out_l = _sample(rgba4, rw, rh, *_virt_to_rt(350, 375, vw, vh, rw, rh))
    c4_out_r = _sample(rgba4, rw, rh, *_virt_to_rt(580, 375, vw, vh, rw, rh))
    c4_out_t = _sample(rgba4, rw, rh, *_virt_to_rt(475, 250, vw, vh, rw, rh))
    c4_out_b = _sample(rgba4, rw, rh, *_virt_to_rt(475, 480, vw, vh, rw, rh))

    t4_ok = (_near(c4_in, MAGENTA) and
             not _near(c4_out_l, MAGENTA, tol=60) and
             not _near(c4_out_r, MAGENTA, tol=60) and
             not _near(c4_out_t, MAGENTA, tol=60) and
             not _near(c4_out_b, MAGENTA, tol=60))
    results.append(("extreme_3000px_overflow", t4_ok, f"in={c4_in} out_l={c4_out_l} out_r={c4_out_r}"))

    # =========================================================================
    # Test 5: Empty / Disjoint Intersection Short-Circuit
    # =========================================================================
    # Parent clip at (100, 100) size 100x100 => [100, 100, 200, 200]
    # Child clip at relative (250, 250) size 100x100 => [350, 350, 450, 450] (completely disjoint)
    p_disjoint = FakeRender(100, 100, xclipping=True, yclipping=True)
    c_disjoint = FakeRender(100, 100, xclipping=True, yclipping=True)
    c_disjoint.blit(tex_red_500, 0, 0)
    p_disjoint.blit(c_disjoint, 250, 250)
    root5 = FakeRender(vw, vh)
    root5.blit(bg_solid, 0, 0)
    root5.blit(p_disjoint, 100, 100)
    rw, rh, rgba5 = _present(draw, root5)

    c5_p_in = _sample(rgba5, rw, rh, *_virt_to_rt(150, 150, vw, vh, rw, rh))
    c5_c_in = _sample(rgba5, rw, rh, *_virt_to_rt(375, 375, vw, vh, rw, rh))
    t5_ok = (not _near(c5_p_in, RED, tol=60)) and (not _near(c5_c_in, RED, tol=60))
    results.append(("disjoint_clip_short_circuit", t5_ok, f"parent_area={c5_p_in} child_area={c5_c_in}"))

    # =========================================================================
    # Test 6: Persistent WgpuDraw Multi-Frame Isolation & Contamination Stress
    # =========================================================================
    # Frame A: Large Red draw at [0, 0, 400, 400]
    # Frame B: Green draw at [800, 400, 1100, 650]
    # Verify: Frame B has ZERO Red residue in [0, 0, 400, 400]
    p6_a = FakeRender(400, 400, xclipping=True, yclipping=True)
    p6_a.blit(tex_red_500, 0, 0)
    root6_a = FakeRender(vw, vh)
    root6_a.blit(bg_solid, 0, 0)
    root6_a.blit(p6_a, 0, 0)
    _present(draw, root6_a)

    p6_b = FakeRender(300, 250, xclipping=True, yclipping=True)
    p6_b.blit(tex_green_500, 0, 0)
    root6_b = FakeRender(vw, vh)
    root6_b.blit(bg_solid, 0, 0)
    root6_b.blit(p6_b, 800, 400)
    rw, rh, rgba6_b = _present(draw, root6_b)

    c6_old_red = _sample(rgba6_b, rw, rh, *_virt_to_rt(200, 200, vw, vh, rw, rh))
    c6_new_green = _sample(rgba6_b, rw, rh, *_virt_to_rt(950, 525, vw, vh, rw, rh))
    t6_isolation_ok = (not _near(c6_old_red, RED, tol=60)) and _near(c6_new_green, GREEN)
    results.append(("frame_to_frame_isolation", t6_isolation_ok, f"old_red_area={c6_old_red} new_green_area={c6_new_green}"))

    # Rapid loop of 30 frames with varying clips and offsets on same draw instance
    colors = [RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA]
    texs = [
        make_solid_ht(300, 300, RED),
        make_solid_ht(300, 300, GREEN),
        make_solid_ht(300, 300, BLUE),
        make_solid_ht(300, 300, YELLOW),
        make_solid_ht(300, 300, CYAN),
        make_solid_ht(300, 300, MAGENTA),
    ]
    loop_ok = True
    for i in range(30):
        ox = (i * 37) % 600
        oy = (i * 23) % 400
        color = colors[i % 6]
        tex = texs[i % 6]
        p_loop = FakeRender(150, 150, xclipping=True, yclipping=True)
        p_loop.blit(tex, -50, -50)
        root_loop = FakeRender(vw, vh)
        root_loop.blit(bg_solid, 0, 0)
        root_loop.blit(p_loop, ox, oy)
        rw, rh, rgba_loop = _present(draw, root_loop)
        c_in_loop = _sample(rgba_loop, rw, rh, *_virt_to_rt(ox + 75, oy + 75, vw, vh, rw, rh))
        if not _near(c_in_loop, color, tol=50):
            loop_ok = False
            results.append((f"rapid_loop_frame_{i}", False, f"expected={color} got={c_in_loop}"))
            break
    if loop_ok:
        results.append(("rapid_30frame_clip_loop", True, "30/30 sequential frames rendered with 0 contamination"))

    # Summary
    all_ok = all(r[1] for r in results)
    lines = [
        f"ok={all_ok}",
        f"tests_total={len(results)} tests_passed={sum(1 for r in results if r[1])}",
    ]
    for name, passed, detail in results:
        lines.append(f"case.{name} ok={passed} detail={detail}")
    lines.append("contract=GL2 Parity Nested Viewport Clipping and Frame Isolation")

    msg = "\n".join(lines) + "\n"
    out.write_text(msg, encoding="utf-8")
    print(msg, flush=True)

    if not all_ok:
        raise RuntimeError(f"Challenger stress test failed: {msg}")

    renpy_host.request_quit()


if __name__ == "__main__":
    main()
