"""
Adversarial Stress Gate: 50+ 9-slice Frames, Subsurface Sharing, Eviction Recovery on Vulkan.

Gate name: hmc_m1_multipiece_stress (RENPY_HOST_GATE=hmc_m1_multipiece_stress)

Tests:
1. 50 distinct 9-slice Frame instances rendered concurrently across a virtual canvas.
2. 450+ subsurfaces actively sharing HostTexture handles and reverse matrices.
3. Post-eviction recovery: kill_textures() and re-render of cached Frame trees,
   asserting 100% dead-handle recovery without black slabs or background holes.
"""

import os
from pathlib import Path

import renpy_host  # type: ignore
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-hmc_m1_multipiece_stress.txt"
out.parent.mkdir(parents=True, exist_ok=True)

VW, VH = 1280, 720
BG = (30, 45, 60, 255)


class Mat2:
    def __init__(self, a, b, c, d):
        self.xdx = float(a)
        self.xdy = float(b)
        self.ydx = float(c)
        self.ydy = float(d)


class R:
    def __init__(self, w, h):
        self.width = int(w)
        self.height = int(h)
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

    def blit(self, c, x=0, y=0):
        self.children.append((c, float(x), float(y), False, True))

    def get_size(self):
        return self.width, self.height


def samp(rt, rw, rh, x, y):
    x = max(0, min(rw - 1, int(x)))
    y = max(0, min(rh - 1, int(y)))
    o = (y * rw + x) * 4
    return rt[o], rt[o + 1], rt[o + 2], rt[o + 3]


def near_bg(c, tol=20):
    return all(abs(int(c[i]) - int(BG[i])) <= tol for i in range(3))


def piece(tex, sx0, sy0, csw, csh, cdw, cdh):
    sub = tex.subsurface((sx0, sy0, csw, csh))
    if csw == cdw and csh == cdh:
        return sub
    p = R(cdw, cdh)
    p.reverse = Mat2(cdw / float(csw), 0, 0, cdh / float(csh))
    p.forward = Mat2(csw / float(cdw), 0, 0, csh / float(cdh))
    p.blit(sub, 0, 0)
    return p


def build_frame(tex, sw, sh, dw, dh, borders):
    left, top, right, bottom = borders
    frame = R(dw, dh)

    def regions(x0, x1, y0, y1):
        dx0, sx0 = (x0, x0) if x0 >= 0 else (dw + x0, sw + x0)
        dx1, sx1 = (x1, x1) if x1 > 0 else (dw + x1, sw + x1)
        dy0, sy0 = (y0, y0) if y0 >= 0 else (dh + y0, sh + y0)
        dy1, sy1 = (y1, y1) if y1 > 0 else (dh + y1, sh + y1)
        return sx0, sy0, sx1 - sx0, sy1 - sy0, dx0, dy0, dx1 - dx0, dy1 - dy0

    def draw_slice(x0, x1, y0, y1):
        sx0, sy0, csw, csh, dx0, dy0, cdw, cdh = regions(x0, x1, y0, y1)
        if csw <= 0 or csh <= 0 or cdw <= 0 or cdh <= 0:
            return
        frame.blit(piece(tex, sx0, sy0, csw, csh, cdw, cdh), dx0, dy0)

    if top:
        if left:
            draw_slice(0, left, 0, top)
        draw_slice(left, -right, 0, top)
        if right:
            draw_slice(-right, 0, 0, top)
    if left:
        draw_slice(0, left, top, -bottom)
    draw_slice(left, -right, top, -bottom)
    if right:
        draw_slice(-right, 0, top, -bottom)
    if bottom:
        if left:
            draw_slice(0, left, -bottom, 0)
        draw_slice(left, -right, -bottom, 0)
        if right:
            draw_slice(-right, 0, -bottom, 0)
    return frame


def make_pattern_surf(w, h, border_color, center_color, border_sz=6):
    s = Surface((w, h))
    s.fill(border_color)
    inner = Surface((max(1, w - border_sz * 2), max(1, h - border_sz * 2)))
    inner.fill(center_color)
    s.blit(inner, (border_sz, border_sz))
    return s


def main():
    print("[hmc_m1_multipiece_stress] Starting 50-frame stress test...", flush=True)
    draw = WgpuDraw()
    draw.init((VW, VH))

    colors = [
        ((255, 100, 50, 255), (200, 200, 200, 255)),
        ((50, 200, 100, 255), (220, 240, 220, 255)),
        ((80, 120, 255, 255), (240, 220, 240, 255)),
        ((230, 200, 40, 255), (100, 50, 150, 255)),
        ((220, 50, 200, 255), (50, 180, 180, 255)),
        ((240, 140, 30, 255), (30, 140, 240, 255)),
        ((120, 230, 180, 255), (180, 80, 80, 255)),
        ((200, 80, 140, 255), (80, 200, 140, 255)),
        ((160, 160, 240, 255), (240, 160, 160, 255)),
        ((255, 180, 120, 255), (120, 180, 255, 255)),
    ]

    # Create 10 source textures
    source_textures = []
    for i, (bc, cc) in enumerate(colors):
        sw, sh = 40 + i * 2, 30 + i * 2
        surf = make_pattern_surf(sw, sh, bc, cc, border_sz=6)
        ht = draw.load_texture(surf)
        source_textures.append((ht, sw, sh, bc, cc))

    # Phase 1: Build 50 Frame instances (5 rows x 10 cols)
    frames_meta = []
    root = R(VW, VH)
    bg_surf = Surface((VW, VH))
    bg_surf.fill(BG)
    root.blit(bg_surf, 0, 0)

    grid_cols = 10
    grid_rows = 5
    cell_w = VW // grid_cols  # 128
    cell_h = VH // grid_rows  # 144

    frame_trees = []

    for row in range(grid_rows):
        for col in range(grid_cols):
            tex_idx = col
            ht, sw, sh, bc, cc = source_textures[tex_idx]
            dw = cell_w - 16  # 112
            dh = cell_h - 16  # 128
            borders = (6, 6, 6, 6)
            f_tree = build_frame(ht, sw, sh, dw, dh, borders)
            pos_x = col * cell_w + 8
            pos_y = row * cell_h + 8
            root.blit(f_tree, pos_x, pos_y)
            frames_meta.append({
                "col": col, "row": row, "pos": (pos_x, pos_y), "dest": (dw, dh),
                "bc": bc, "cc": cc,
            })
            frame_trees.append((f_tree, pos_x, pos_y))

    print(f"[hmc_m1_multipiece_stress] Rendering 50 Frames (total 450 subsurfaces)...", flush=True)
    draw.draw_screen(root, flip=True)

    rw, rh, rt = renpy_host.read_game_rt_rgba()
    print(f"[hmc_m1_multipiece_stress] Game RT size: {rw}x{rh}, analyzing samples...", flush=True)
    sx = rw / float(VW)
    sy = rh / float(VH)

    p1_failures = []
    for meta in frames_meta:
        px, py = meta["pos"]
        dw, dh = meta["dest"]
        cx, cy = (px + dw // 2) * sx, (py + dh // 2) * sy
        # sample center
        c_center = samp(rt, rw, rh, cx, cy)
        # sample border top (inside top border slice: py + 3)
        c_top = samp(rt, rw, rh, (px + dw // 2) * sx, (py + 3) * sy)

        if near_bg(c_center) or near_bg(c_top):
            p1_failures.append(f"fail_bg:frame({meta['col']},{meta['row']}) center={c_center} top={c_top}")
        elif c_top[0] < 30 and c_top[1] < 30 and c_top[2] < 30 and meta["bc"][0] > 100:
            p1_failures.append(f"fail_black:frame({meta['col']},{meta['row']}) top={c_top}")

    p1_ok = len(p1_failures) == 0
    print(f"[hmc_m1_multipiece_stress] Phase 1 (50 concurrent frames): ok={p1_ok} failures={len(p1_failures)}", flush=True)

    # Phase 2: Post-eviction recovery test
    # Call kill_textures() to destroy all host textures and clear caches
    print("[hmc_m1_multipiece_stress] Phase 2: Triggering kill_textures() to simulate GPU handle eviction...", flush=True)
    draw.kill_textures()

    # Re-render the exact same frame trees without re-creating HostTextures manually
    # draw_screen -> _ensure_host_texture_alive must recover from _handle_pixels
    root2 = R(VW, VH)
    root2.blit(bg_surf, 0, 0)
    for f_tree, px, py in frame_trees:
        root2.blit(f_tree, px, py)

    print("[hmc_m1_multipiece_stress] Re-rendering cached Frame trees after eviction...", flush=True)
    draw.draw_screen(root2, flip=True)

    rw2, rh2, rt2 = renpy_host.read_game_rt_rgba()
    p2_failures = []
    for meta in frames_meta:
        px, py = meta["pos"]
        dw, dh = meta["dest"]
        cx, cy = (px + dw // 2) * sx, (py + dh // 2) * sy
        c_center = samp(rt2, rw2, rh2, cx, cy)
        c_top = samp(rt2, rw2, rh2, (px + dw // 2) * sx, (py + 3) * sy)

        if near_bg(c_center) or near_bg(c_top):
            p2_failures.append(f"fail_bg_post_evict:frame({meta['col']},{meta['row']}) center={c_center} top={c_top}")

    p2_ok = len(p2_failures) == 0
    print(f"[hmc_m1_multipiece_stress] Phase 2 (post-eviction recovery): ok={p2_ok} failures={len(p2_failures)}", flush=True)

    overall_ok = p1_ok and p2_ok
    msg = f"ok={overall_ok} phase1_ok={p1_ok} phase2_ok={p2_ok} total_frames=50 total_subsurfaces=450 p1_failures={len(p1_failures)} p2_failures={len(p2_failures)}"
    out.write_text(msg + "\n")
    print(f"[hmc_m1_multipiece_stress] RESULT: {msg}", flush=True)
    if not overall_ok:
        raise RuntimeError(f"hmc_m1_multipiece_stress failed: P1={p1_failures[:5]} P2={p2_failures[:5]}")


main()
