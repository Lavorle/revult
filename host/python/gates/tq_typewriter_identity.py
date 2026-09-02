"""Agent-A regression probe: reverse scale must not balloon mid-st typewriter.

Cases:
1. IDENTITY reverse (draw_per_virt=1): needs_axis_scale=False; partial stays partial.
2. Oversample reverse 1/os (draw_per_virt>1 after maximize): dest = child * reverse
   (virtual partial), NOT full parent box — user bug after enlarge.

Pass = mid-st coverage ≈ 0.25 for both paths, not ~1.0.
"""
import os
import traceback
from pathlib import Path

import renpy_host
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import HostTexture, WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---


_base = Path(os.environ.get("RENPY_HOST_BASE") or ".")
out = _base / "host" / "target" / "gate-tq_typewriter_identity.txt"
out.parent.mkdir(parents=True, exist_ok=True)

VW, VH = 1280, 720
TW, TH = 400, 48
OX, OY = 100, 200
BG = (40, 80, 120, 255)
INK = (255, 255, 255, 255)
EXPECTED_W = 100


class Mat2:
    def __init__(self, xdx=1.0, xdy=0.0, ydx=0.0, ydy=1.0):
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

    def absolute_blit(self, child, pos):
        xo, yo = pos
        self.children.append((child, float(xo), float(yo), False, True))

    def get_size(self):
        return (self.width, self.height)


def sample(rgba, w, h, x, y):
    x = max(0, min(w - 1, int(x)))
    y = max(0, min(h - 1, int(y)))
    o = (y * w + x) * 4
    return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]


def is_ink(c):
    return int(c[0]) > 180 and int(c[1]) > 180 and int(c[2]) > 180


def ink_scan(rgba, rw, rh, ox, oy, tw, th):
    sx = rw / float(VW)
    sy = rh / float(VH)
    cy = int((oy + th // 2) * sy)
    ink_cols = 0
    max_x = -1
    for vx in range(tw):
        px = int((ox + vx) * sx)
        c = sample(rgba, rw, rh, px, cy)
        if is_ink(c):
            ink_cols += 1
            max_x = vx
    return ink_cols, max_x, ink_cols / float(tw)


def main():
    lines = []
    ok = False
    try:
        draw = WgpuDraw()
        draw.init((VW, VH))
        s = Surface((TW, TH))
        s.fill((0, 0, 0, 0))
        for y in range(TH):
            for x in range(TW):
                s.set_at((x, y), INK)
        full = draw.load_texture(s)
        assert isinstance(full, HostTexture) and full.handle > 0

        bg = Surface((VW, VH))
        bg.fill(BG)
        root = FakeRender(VW, VH)
        root.blit(bg, 0, 0)

        # 1) IDENTITY reverse mid-st
        text = FakeRender(TW, TH)
        text.forward = Mat2(1, 0, 0, 1)
        text.reverse = Mat2(1, 0, 0, 1)
        sub_w = EXPECTED_W
        sub = full.subsurface((0, 0, sub_w, TH))
        text.absolute_blit(sub, (0, 0))
        root.blit(text, OX, OY)

        kids = list(draw._iter_children(text))
        needs = draw._node_needs_axis_scale(text, kids)
        lines.append(f"needs_axis_scale={needs} (expect False)")

        draw.draw_screen(root, flip=True)
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
        ink_cols, max_x, coverage = ink_scan(rgba, rw, rh, OX, OY, TW, TH)
        not_stretched = coverage < 0.55 and max_x < EXPECTED_W + 30
        partial_ok = ink_cols > 5
        width_ok = (
            abs(max_x + 1 - EXPECTED_W) <= max(20, int(0.3 * EXPECTED_W))
            if max_x >= 0
            else False
        )
        ok_id = (not needs) and not_stretched and partial_ok and width_ok
        lines.append(
            "sub_w=%d ink_cols=%d coverage=%.3f max_ink_x=%d"
            % (sub_w, ink_cols, coverage, max_x)
        )
        lines.append(
            f"not_stretched={not_stretched} partial_ok={partial_ok} width_ok={width_ok} ok_id={ok_id}"
        )

        # 2) Oversample reverse (maximize / draw_per_virt>1)
        os_factor = 1.5
        inv = 1.0 / os_factor
        draw_w = round(TW * os_factor)
        draw_h = round(TH * os_factor)
        mid_draw_w = round(EXPECTED_W * os_factor)
        s2 = Surface((draw_w, draw_h))
        s2.fill((0, 0, 0, 0))
        for y in range(draw_h):
            for x in range(draw_w):
                s2.set_at((x, y), INK)
        full2 = draw.load_texture(s2)
        assert isinstance(full2, HostTexture) and full2.handle > 0

        bg2 = Surface((VW, VH))
        bg2.fill(BG)
        root2 = FakeRender(VW, VH)
        root2.blit(bg2, 0, 0)
        text2 = FakeRender(TW, TH)
        text2.forward = Mat2(os_factor, 0, 0, os_factor)
        text2.reverse = Mat2(inv, 0, 0, inv)
        sub2 = full2.subsurface((0, 0, mid_draw_w, draw_h))
        text2.absolute_blit(sub2, (0, 0))
        root2.blit(text2, OX, OY)

        kids2 = list(draw._iter_children(text2))
        needs2 = draw._node_needs_axis_scale(text2, kids2)
        dest = draw._reverse_dest_size(text2, sub2, (TW, TH))
        lines.append(
            "oversample needs_axis_scale=%s (expect True) dest=%s expect≈(%d,%d)"
            % (needs2, dest, EXPECTED_W, TH)
        )
        dest_ok = (
            abs(dest[0] - EXPECTED_W) <= 2
            and abs(dest[1] - TH) <= 2
            and needs2 is True
        )

        draw.draw_screen(root2, flip=True)
        rw2, rh2, rgba2 = renpy_host.read_game_rt_rgba()
        ink2, max2, cov2 = ink_scan(rgba2, rw2, rh2, OX, OY, TW, TH)
        os_not_stretched = cov2 < 0.55 and max2 < EXPECTED_W + 40
        os_partial = ink2 > 5
        os_width_ok = (
            abs(max2 + 1 - EXPECTED_W) <= max(25, int(0.35 * EXPECTED_W))
            if max2 >= 0
            else False
        )
        ok_os = dest_ok and os_not_stretched and os_partial and os_width_ok
        lines.append(
            "oversample ink_cols=%d coverage=%.3f max_ink_x=%d dest_ok=%s"
            % (ink2, cov2, max2, dest_ok)
        )
        lines.append(
            f"os_not_stretched={os_not_stretched} os_partial={os_partial} os_width_ok={os_width_ok} ok_os={ok_os}"
        )

        ok = bool(ok_id and ok_os)
        lines.append(f"ok={ok}")
    except Exception as e:
        ok = False
        lines.append(f"EXCEPTION {e!r}")
        lines.append(traceback.format_exc())

    body = (f"ok={ok}\n") + "\n".join(lines) + "\n"
    out.write_text(body)
    try:
        import sys

        sys.__stdout__.write(body)
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        renpy_host.request_quit()
    except Exception:
        pass
    if not ok:
        raise RuntimeError(f"tq_typewriter_identity failed; see {out}")
    return 0


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

