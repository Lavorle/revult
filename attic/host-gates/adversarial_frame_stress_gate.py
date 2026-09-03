"""
Adversarial Stress Test Gate for Milestone 1:
- 9-Slice Color Integrity & Corner Quad Preservations
- Semi-transparent border Alpha / PMA Blending Accuracy
- Degenerate & Extreme Aspect Ratio 9-Slice Geometry
- High-churn Subsurface Texture Lifecycle & Handle Retention

Gate name: adversarial_frame_stress_gate
"""

import os
from pathlib import Path
import renpy_host  # type: ignore
from renpy.pygame.surface import Surface
from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-adversarial_frame_stress.txt"
out.parent.mkdir(parents=True, exist_ok=True)

VW, VH = 1280, 720
BG = (40, 60, 100, 255)  # Dark slate blue


class Mat2:
    def __init__(self, xdx, xdy, ydx, ydy):
        self.xdx = float(xdx)
        self.xdy = float(xdy)
        self.ydx = float(ydx)
        self.ydy = float(ydy)


class FakeRender:
    def __init__(self, width, height):
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
        self.cached_texture = None
        self.blits = None
        self.ndc = None
        self.uniforms = None
        self.loaded = False
        self.forward = None
        self.reverse = None

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


def _near(c, target, tol=30):
    return all(abs(int(c[i]) - int(target[i])) <= tol for i in range(3))


def _piece(tex, sx0, sy0, csw, csh, cdw, cdh):
    if csw <= 0 or csh <= 0 or cdw <= 0 or cdh <= 0:
        return None
    if isinstance(tex, HostTexture):
        sub = tex.subsurface((sx0, sy0, csw, csh))
    else:
        sub = tex
    if csw != cdw or csh != cdh:
        pr = FakeRender(cdw, cdh)
        pr.reverse = Mat2(cdw / float(csw), 0, 0, cdh / float(csh))
        pr.forward = Mat2(csw / float(cdw), 0, 0, csh / float(cdh))
        mid = FakeRender(csw, csh)
        mid.blit(sub, 0, 0)
        pr.blit(mid, 0, 0)
        return pr
    mid = FakeRender(cdw, cdh)
    mid.blit(sub, 0, 0)
    return mid


def _build_frame(tex, sw, sh, dw, dh, borders):
    left, top, right, bottom = borders
    frame = FakeRender(dw, dh)

    def regions(x0, x1, y0, y1):
        dx0, sx0 = (x0, x0) if x0 >= 0 else (dw + x0, sw + x0)
        dx1, sx1 = (x1, x1) if x1 > 0 else (dw + x1, sw + x1)
        dy0, sy0 = (y0, y0) if y0 >= 0 else (dh + y0, sh + y0)
        dy1, sy1 = (y1, y1) if y1 > 0 else (dh + y1, sh + y1)
        return sx0, sy0, sx1 - sx0, sy1 - sy0, dx0, dy0, dx1 - dx0, dy1 - dy0

    def draw(x0, x1, y0, y1):
        sx0, sy0, csw, csh, dx0, dy0, cdw, cdh = regions(x0, x1, y0, y1)
        if csw <= 0 or csh <= 0 or cdw <= 0 or cdh <= 0:
            return
        p = _piece(tex, sx0, sy0, csw, csh, cdw, cdh)
        if p is not None:
            frame.blit(p, dx0, dy0)

    if top:
        if left:
            draw(0, left, 0, top)
        draw(left, -right, 0, top)
        if right:
            draw(-right, 0, 0, top)
    if left:
        draw(0, left, top, -bottom)
    draw(left, -right, top, -bottom)
    if right:
        draw(-right, 0, top, -bottom)
    if bottom:
        if left:
            draw(0, left, -bottom, 0)
        draw(left, -right, -bottom, 0)
        if right:
            draw(-right, 0, -bottom, 0)
    return frame


def test_color_matrix_9slice(draw):
    """Test 1: 9 distinct colors across 9 slices — verify no quad dropped and no bleed."""
    SW, SH = 60, 60
    DW, DH = 400, 300
    BORDERS = (20, 20, 20, 20)
    OX, OY = 80, 80

    C_TL = (255, 0, 0, 255)       # Red
    C_TM = (0, 255, 0, 255)       # Green
    C_TR = (0, 0, 255, 255)       # Blue
    C_ML = (255, 255, 0, 255)     # Yellow
    C_C  = (0, 255, 255, 255)     # Cyan
    C_MR = (255, 0, 255, 255)     # Magenta
    C_BL = (255, 255, 255, 255)   # White
    C_BM = (255, 128, 0, 255)     # Orange
    C_BR = (128, 0, 128, 255)     # Purple

    src = Surface((SW, SH))
    for y in range(SH):
        for x in range(SW):
            col_x = 0 if x < 20 else (1 if x < 40 else 2)
            row_y = 0 if y < 20 else (1 if y < 40 else 2)
            grid = [
                [C_TL, C_TM, C_TR],
                [C_ML, C_C,  C_MR],
                [C_BL, C_BM, C_BR],
            ]
            src.set_at((x, y), grid[row_y][col_x])

    tex = draw.load_texture(src)
    frame = _build_frame(tex, SW, SH, DW, DH, BORDERS)

    root = FakeRender(VW, VH)
    bg = Surface((VW, VH))
    bg.fill(BG)
    root.blit(bg, 0, 0)
    root.blit(frame, OX, OY)

    draw.draw_screen(root, flip=True)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    sx, sy = rw / float(VW), rh / float(VH)

    samples = {
        "TL": (OX + 10, OY + 10, C_TL),
        "TM": (OX + DW // 2, OY + 10, C_TM),
        "TR": (OX + DW - 10, OY + 10, C_TR),
        "ML": (OX + 10, OY + DH // 2, C_ML),
        "Center": (OX + DW // 2, OY + DH // 2, C_C),
        "MR": (OX + DW - 10, OY + DH // 2, C_MR),
        "BL": (OX + 10, OY + DH - 10, C_BL),
        "BM": (OX + DW // 2, OY + DH - 10, C_BM),
        "BR": (OX + DW - 10, OY + DH - 10, C_BR),
    }

    results = {}
    all_ok = True
    for name, (px, py, exp_c) in samples.items():
        c = _sample(rgba, rw, rh, px * sx, py * sy)
        ok = _near(c, exp_c, tol=15)
        results[name] = (ok, c[:3], exp_c[:3])
        if not ok:
            all_ok = False

    return all_ok, results


def test_translucent_border_pma(draw):
    """Test 2: Semi-transparent green border (a=128) + transparent center over BG."""
    SW, SH = 60, 60
    DW, DH = 300, 200
    BORDERS = (15, 15, 15, 15)
    OX, OY = 150, 150

    src = Surface((SW, SH))
    # Border: translucent green (0, 200, 50, 128)
    # Center: transparent (0, 0, 0, 0)
    for y in range(SH):
        for x in range(SW):
            if 15 <= x < 45 and 15 <= y < 45:
                src.set_at((x, y), (0, 0, 0, 0))
            else:
                src.set_at((x, y), (0, 200, 50, 128))

    tex = draw.load_texture(src)
    frame = _build_frame(tex, SW, SH, DW, DH, BORDERS)

    root = FakeRender(VW, VH)
    bg = Surface((VW, VH))
    bg.fill(BG)  # (40, 60, 100, 255)
    root.blit(bg, 0, 0)
    root.blit(frame, OX, OY)

    draw.draw_screen(root, flip=True)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    sx, sy = rw / float(VW), rh / float(VH)

    # Center must remain BG
    c_center = _sample(rgba, rw, rh, (OX + DW // 2) * sx, (OY + DH // 2) * sy)
    center_ok = _near(c_center, BG, tol=10)

    # Border: blended result
    # expected: R = 0*0.502 + 40*0.498 = 20
    #           G = 200*0.502 + 60*0.498 = 100.4 + 29.9 = 130
    #           B = 50*0.502 + 100*0.498 = 25.1 + 49.8 = 75
    exp_border = (20, 130, 75)
    c_top = _sample(rgba, rw, rh, (OX + DW // 2) * sx, (OY + 7) * sy)
    c_left = _sample(rgba, rw, rh, (OX + 7) * sx, (OY + DH // 2) * sy)
    top_ok = _near(c_top, exp_border, tol=15)
    left_ok = _near(c_left, exp_border, tol=15)

    all_ok = center_ok and top_ok and left_ok
    return all_ok, {
        "center_ok": center_ok, "center_rgb": c_center[:3],
        "top_ok": top_ok, "top_rgb": c_top[:3],
        "left_ok": left_ok, "left_rgb": c_left[:3],
        "exp_border": exp_border,
    }


def test_extreme_aspect_ratios(draw):
    """Test 3: Extreme aspect ratios and squashed dimensions."""
    SW, SH = 40, 40
    src = Surface((SW, SH))
    src.fill((220, 100, 30, 255))
    tex = draw.load_texture(src)

    # 1. Very wide (600x16) with border (10, 4, 10, 4)
    frame_wide = _build_frame(tex, SW, SH, 600, 16, (10, 4, 10, 4))
    # 2. Very tall (16x400) with border (4, 10, 4, 10)
    frame_tall = _build_frame(tex, SW, SH, 16, 400, (4, 10, 4, 10))
    # 3. Squashed dest smaller than borders (10x10 with border 15,15,15,15)
    frame_squash = _build_frame(tex, SW, SH, 10, 10, (15, 15, 15, 15))

    root = FakeRender(VW, VH)
    bg = Surface((VW, VH))
    bg.fill(BG)
    root.blit(bg, 0, 0)
    root.blit(frame_wide, 50, 50)
    root.blit(frame_tall, 50, 80)
    root.blit(frame_squash, 100, 80)

    draw.draw_screen(root, flip=True)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    sx, sy = rw / float(VW), rh / float(VH)

    # Sample wide frame center
    c_wide = _sample(rgba, rw, rh, (50 + 300) * sx, (50 + 8) * sy)
    wide_ok = _near(c_wide, (220, 100, 30), tol=25)

    # Sample tall frame center
    c_tall = _sample(rgba, rw, rh, (50 + 8) * sx, (80 + 200) * sy)
    tall_ok = _near(c_tall, (220, 100, 30), tol=25)

    all_ok = wide_ok and tall_ok
    return all_ok, {"wide_ok": wide_ok, "wide_rgb": c_wide[:3], "tall_ok": tall_ok, "tall_rgb": c_tall[:3]}


def test_subsurface_handle_churn_stress(draw):
    """Test 4: Churning texture cache with 100 unique textures and verifying handle survival."""
    textures = []
    for i in range(100):
        s = Surface((32, 32))
        s.fill((i * 2 % 256, (i * 5 + 50) % 256, (i * 7 + 100) % 256, 255))
        t = draw.load_texture(s)
        textures.append(t)

    # Build 10 9-slice frames from a subset of churned textures
    root = FakeRender(VW, VH)
    bg = Surface((VW, VH))
    bg.fill(BG)
    root.blit(bg, 0, 0)

    for idx, t in enumerate(textures[-10:]):
        frame = _build_frame(t, 32, 32, 80, 40, (4, 4, 4, 4))
        root.blit(frame, 50 + (idx % 5) * 100, 50 + (idx // 5) * 60)

    draw.draw_screen(root, flip=True)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    sx, sy = rw / float(VW), rh / float(VH)

    # Verify each frame center is NOT clear background (still_bg) or black slab
    frame_ok_count = 0
    for idx, t in enumerate(textures[-10:]):
        fx = 50 + (idx % 5) * 100 + 40
        fy = 50 + (idx // 5) * 60 + 20
        c = _sample(rgba, rw, rh, fx * sx, fy * sy)
        if not _near(c, BG, tol=15) and not (c[0] < 10 and c[1] < 10 and c[2] < 10):
            frame_ok_count += 1

    all_ok = (frame_ok_count == 10)
    return all_ok, {"frames_alive": frame_ok_count, "total": 10}


def main():
    draw = WgpuDraw()
    draw.init((VW, VH))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    print("[adversarial_frame_stress] Starting comprehensive stress tests...", flush=True)

    t1_ok, t1_res = test_color_matrix_9slice(draw)
    print(f"  Test 1 (9-Slice Color Matrix & Corner Quads): ok={t1_ok} details={t1_res}", flush=True)

    t2_ok, t2_res = test_translucent_border_pma(draw)
    print(f"  Test 2 (Translucent Border & PMA Math): ok={t2_ok} details={t2_res}", flush=True)

    t3_ok, t3_res = test_extreme_aspect_ratios(draw)
    print(f"  Test 3 (Extreme Aspect Ratios & Squashed Geometries): ok={t3_ok} details={t3_res}", flush=True)

    t4_ok, t4_res = test_subsurface_handle_churn_stress(draw)
    print(f"  Test 4 (Texture Churn & Handle Retention): ok={t4_ok} details={t4_res}", flush=True)

    overall_ok = t1_ok and t2_ok and t3_ok and t4_ok
    summary = f"ok={overall_ok} t1_color_matrix={t1_ok} t2_translucent_pma={t2_ok} t3_extreme_aspect={t3_ok} t4_churn_stress={t4_ok}"
    out.write_text(summary + "\n")
    print(f"[adversarial_frame_stress] SUMMARY {summary}", flush=True)

    if not overall_ok:
        raise RuntimeError(f"adversarial_frame_stress failed: {summary}")


main()
