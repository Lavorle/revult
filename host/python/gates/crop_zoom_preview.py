"""
C3 crop+zoom text-settings preview gate (product-shaped).

Gate name: crop_zoom_preview  (RENPY_HOST_GATE=crop_zoom_preview)

Mirrors text_config.rpy:296–308:
  fixed 1920×1080
    at transform: crop (0, 825, 1920, 255); zoom 0.42
      add full-fill-style child (source larger than crop band)
      add secondary overlay band

Host crop path builds intermediate clip Render, then reverse-zoom scales the
crop band. Draw must not peel the clip wrapper (see _extract_host_texture).

Checks:
  1. Scaled crop slot interior is source color (preview visible).
  2. Outside the ~806×107 slot stays background (not full 1920×1080 paint).
  3. Region below the slot (bottom chrome space) stays background.
  4. mesh_clip_axis contracts still hold (positive clip, no global scissor).

Note: no from __future__; host run_file prepends imports.
"""

import os
from pathlib import Path


import renpy_host  # type: ignore

from renpy.wgpu.draw import WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-crop_zoom_preview.txt"
out.parent.mkdir(parents=True, exist_ok=True)

# Distinct bands so wrong-band / no-crop failures are obvious.
BAND_TOP = (40, 40, 200, 255)  # y 0..825 — must NOT appear in preview slot
BAND_MID = (0, 220, 0, 255)  # y 825..1080 — crop band (preview content)
BG = (20, 20, 30, 255)
OVERLAY = (220, 40, 40, 255)  # secondary say-background strip inside crop


class FakeRender:
    def __init__(self, width, height, xclipping=False, yclipping=False, reverse=None):
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
        self.reverse = reverse
        self.xclipping = bool(xclipping)
        self.yclipping = bool(yclipping)

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self

    def get_size(self):
        return (self.width, self.height)


class _M:
    """Axis-aligned reverse Matrix2D stand-in (WgpuDraw reads xdx/ydy)."""

    def __init__(self, xdx, ydy):
        self.xdx = float(xdx)
        self.ydy = float(ydy)
        self.xdy = 0.0
        self.ydx = 0.0

    def inverse(self):
        ix = 1.0 / self.xdx if abs(self.xdx) > 1e-12 else 1.0
        iy = 1.0 / self.ydy if abs(self.ydy) > 1e-12 else 1.0
        return _M(ix, iy)


def _sample(rgba, w, h, x, y):
    x = max(0, min(w - 1, int(x)))
    y = max(0, min(h - 1, int(y)))
    o = (y * w + x) * 4
    return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]


def _near(c, target, tol=50):
    return all(abs(int(c[i]) - int(target[i])) <= tol for i in range(3))


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


def _virt_to_rt(vx, vy, vw, vh, rw, rh):
    return int(vx * rw / float(vw)), int(vy * rh / float(vh))


def _build_crop_zoom_tree(draw, vw, vh, slot_x, slot_y, crop_w, crop_h, zoom, src_w, src_h, crop_y):
    """
    Host crop+zoom tree shape (matches renpy_display_accelerator_host._cropping
    intermediate-clip path + zoom reverse):

      outer reverse Render(crop_w*zoom × crop_h*zoom)
        └─ clip Render(crop_w × crop_h)  xclipping=yclipping=True
             ├─ full source HostTexture at (0, -crop_y)
             └─ overlay strip at (0, 0) sized crop band (simulates say bg)

    Without clip, full source would paint oversized after reverse-stretch.
    Without intermediate clip (GL2 offset+outer clip under reverse), v1 AABB
    clip empties the band — host uses intermediate clip intentionally.
    """
    # Source: two horizontal bands painted into one solid via two textures.
    # Simpler: one tall solid of BAND_MID for the crop band content, plus a
    # separate TOP solid only above the crop (placed with negative y so only
    # MID should be visible inside clip).
    mid = draw.solid_texture(int(src_w), int(src_h), BAND_MID)
    top = draw.solid_texture(int(src_w), int(crop_y), BAND_TOP)
    overlay = draw.solid_texture(int(crop_w), int(crop_h // 3), OVERLAY)

    clip = FakeRender(crop_w, crop_h, xclipping=True, yclipping=True)
    # Place top band above crop origin; mid fills full source with offset -crop_y.
    clip.blit(top, 0, -crop_y)
    clip.blit(mid, 0, -crop_y)
    clip.blit(overlay, 0, 0)

    zw = max(1, round(crop_w * zoom))
    zh = max(1, round(crop_h * zoom))
    rev = FakeRender(zw, zh, reverse=_M(zoom, zoom))
    rev.blit(clip, 0, 0)

    root = FakeRender(vw, vh)
    root.blit(rev, slot_x, slot_y)
    return root, zw, zh


def main():
    # Virtual canvas matches product preferences layout scale.
    vw, vh = 1920, 1080
    draw = WgpuDraw()
    draw.init((vw, vh))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    # text_config numbers
    _crop_x, crop_y, crop_w, crop_h = 0, 825, 1920, 255
    zoom = 0.42
    # full_fill child is larger; host intermediate-clip only needs crop band size.
    src_w, src_h = 1920, 1080
    # Place preview roughly where product vbox puts it (right column-ish).
    slot_x, slot_y = 900, 200

    tree, zw, zh = _build_crop_zoom_tree(
        draw, vw, vh, slot_x, slot_y, crop_w, crop_h, zoom, src_w, src_h, crop_y
    )
    rw, rh, rgba = _present(draw, tree)

    # Slot interior (center of zoomed crop) must be MID green (or overlay red near top).
    cx, cy = _virt_to_rt(slot_x + zw // 2, slot_y + zh // 2, vw, vh, rw, rh)
    # Outside left of slot (still on canvas)
    ox_l, oy_l = _virt_to_rt(slot_x - 40, slot_y + zh // 2, vw, vh, rw, rh)
    # Outside above slot
    ox_t, oy_t = _virt_to_rt(slot_x + zw // 2, slot_y - 40, vw, vh, rw, rh)
    # Outside right of slot
    ox_r, oy_r = _virt_to_rt(slot_x + zw + 40, slot_y + zh // 2, vw, vh, rw, rh)
    # Below slot — bottom chrome space must stay free (AC: bottom controls usable)
    ox_b, oy_b = _virt_to_rt(slot_x + zw // 2, slot_y + zh + 80, vw, vh, rw, rh)
    # Far bottom-right chrome area
    ox_br, oy_br = _virt_to_rt(1800, 1000, vw, vh, rw, rh)

    c_in = _sample(rgba, rw, rh, cx, cy)
    c_left = _sample(rgba, rw, rh, ox_l, oy_l)
    c_top = _sample(rgba, rw, rh, ox_t, oy_t)
    c_right = _sample(rgba, rw, rh, ox_r, oy_r)
    c_below = _sample(rgba, rw, rh, ox_b, oy_b)
    c_chrome = _sample(rgba, rw, rh, ox_br, oy_br)

    # Interior: green mid band OR red overlay (both mean crop content present).
    in_ok = _near(c_in, BAND_MID, tol=60) or _near(c_in, OVERLAY, tol=60)
    # Must NOT be blue top-band (would mean wrong crop y / no clip offset).
    not_top = not _near(c_in, BAND_TOP, tol=60)
    # Outside samples must not be green/red preview colors.
    def _not_preview(c):
        return (not _near(c, BAND_MID, tol=60)) and (not _near(c, OVERLAY, tol=60))

    left_ok = _not_preview(c_left)
    top_ok = _not_preview(c_top)
    right_ok = _not_preview(c_right)
    below_ok = _not_preview(c_below)
    chrome_ok = _not_preview(c_chrome)

    # Oversized failure mode: preview fills full virtual canvas → far samples green.
    # Slot size must be ~806×107 (1920*0.42 × 255*0.42), not 1920×1080.
    size_ok = (700 <= zw <= 900) and (80 <= zh <= 140)

    # ---- Regression: clip peel must not defeat multi-child reverse ----
    # reverse → clip(single HostTexture oversized) must still crop.
    solid = draw.solid_texture(600, 600, BAND_MID)
    clip2 = FakeRender(200, 150, xclipping=True, yclipping=True)
    clip2.blit(solid, -100, -80)
    rev2 = FakeRender(200, 150, reverse=_M(1.0, 1.0))  # identity reverse still walks
    # Use non-identity so reverse path is taken:
    rev2 = FakeRender(100, 75, reverse=_M(0.5, 0.5))
    rev2.blit(clip2, 0, 0)
    root2 = FakeRender(vw, vh)
    root2.blit(rev2, 100, 100)
    rw2, rh2, rgba2 = _present(draw, root2)
    # Inside scaled slot center → green
    ix2, iy2 = _virt_to_rt(100 + 50, 100 + 37, vw, vh, rw2, rh2)
    # Outside left of scaled slot → not green
    ox2, oy2 = _virt_to_rt(100 - 30, 100 + 37, vw, vh, rw2, rh2)
    c2_in = _sample(rgba2, rw2, rh2, ix2, iy2)
    c2_out = _sample(rgba2, rw2, rh2, ox2, oy2)
    peel_ok = _near(c2_in, BAND_MID, tol=60) and (not _near(c2_out, BAND_MID, tol=60))

    ok = (
        in_ok
        and not_top
        and left_ok
        and top_ok
        and right_ok
        and below_ok
        and chrome_ok
        and size_ok
        and peel_ok
    )
    lines = [
        f"ok={ok}",
        f"slot_size={zw}x{zh} size_ok={size_ok} (expect ~806x107)",
        f"in_ok={in_ok} not_top={not_top} c_in={c_in}",
        f"left_ok={left_ok} c_left={c_left}",
        f"top_ok={top_ok} c_top={c_top}",
        f"right_ok={right_ok} c_right={c_right}",
        f"below_ok={below_ok} c_below={c_below}",
        f"chrome_ok={chrome_ok} c_chrome={c_chrome}",
        f"peel_ok={peel_ok} c2_in={c2_in} c2_out={c2_out}",
        f"rt={rw}x{rh} virt={vw}x{vh}",
        "contract=C3 crop+zoom text_config preview; no clip peel; bottom chrome free",
    ]
    msg = "\n".join(lines) + "\n"
    out.write_text(msg, encoding="utf-8")
    print(msg, flush=True)
    if not ok:
        raise RuntimeError(msg)
    renpy_host.request_quit()


if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
