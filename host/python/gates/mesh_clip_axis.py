"""
C1 mesh-crop axis-aligned clip gate (positive + negative).

Gate name: mesh_clip_axis  (RENPY_HOST_GATE=mesh_clip_axis)

Positive:
  Parent Render with xclipping=yclipping=True sized (clip_w, clip_h) at
  (clip_x, clip_y) holds an oversized solid HostTexture child. Pixels outside
  the parent box must stay background; interior must be the solid color.

Negative:
  Same oversized child under a parent WITHOUT xclipping/yclipping must paint
  outside the parent box (no global scissor).

Also covers crop+zoom-style offset child (C3 unblock shape).

Note: no from __future__; host run_file prepends imports.
"""

import os
from pathlib import Path


import renpy_host  # type: ignore

from renpy.wgpu.draw import WgpuDraw

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-mesh_clip_axis.txt"
out.parent.mkdir(parents=True, exist_ok=True)

GREEN = (0, 220, 0, 255)
BG = (20, 20, 30, 255)


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
    """Map virtual-pixel coords to game RT pixels (top-left origin both)."""
    return int(vx * rw / float(vw)), int(vy * rh / float(vh))


def main():
    vw, vh = 1280, 720
    draw = WgpuDraw()
    draw.init((vw, vh))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    # Oversized solid: 600x600 green.
    child_w, child_h = 600, 600
    solid = draw.solid_texture(child_w, child_h, GREEN)

    # Clip parent box at (200,150) size 200x150 — child larger and offset negative.
    clip_x, clip_y = 200, 150
    clip_w, clip_h = 200, 150
    child_ox, child_oy = -100, -80  # protrudes left/top of clip box

    # ---- Positive: clipping parent must crop overflow ----
    parent_pos = FakeRender(clip_w, clip_h, xclipping=True, yclipping=True)
    parent_pos.blit(solid, child_ox, child_oy)
    root_pos = FakeRender(vw, vh)
    root_pos.blit(parent_pos, clip_x, clip_y)

    rw, rh, rgba = _present(draw, root_pos)

    # Inside clip box center → green.
    ix, iy = _virt_to_rt(
        clip_x + clip_w // 2, clip_y + clip_h // 2, vw, vh, rw, rh
    )
    # Outside left of clip box (still in child AABB) → background.
    ox_l, oy_l = _virt_to_rt(clip_x - 40, clip_y + clip_h // 2, vw, vh, rw, rh)
    # Outside above clip box → background.
    ox_t, oy_t = _virt_to_rt(clip_x + clip_w // 2, clip_y - 40, vw, vh, rw, rh)
    # Outside right of clip box → background.
    ox_r, oy_r = _virt_to_rt(clip_x + clip_w + 40, clip_y + clip_h // 2, vw, vh, rw, rh)

    c_in = _sample(rgba, rw, rh, ix, iy)
    c_left = _sample(rgba, rw, rh, ox_l, oy_l)
    c_top = _sample(rgba, rw, rh, ox_t, oy_t)
    c_right = _sample(rgba, rw, rh, ox_r, oy_r)

    pos_in = _near(c_in, GREEN)
    _near(c_left, BG) or (c_left[1] < 80)  # not green
    _near(c_top, BG) or (c_top[1] < 80)
    _near(c_right, BG) or (c_right[1] < 80)
    # Stricter: outside samples must NOT be green-dominant.
    pos_left_ok = not _near(c_left, GREEN, tol=60)
    pos_top_ok = not _near(c_top, GREEN, tol=60)
    pos_right_ok = not _near(c_right, GREEN, tol=60)
    positive_ok = pos_in and pos_left_ok and pos_top_ok and pos_right_ok

    # ---- Negative: unclipped parent still paints overflow (no global scissor) ----
    parent_neg = FakeRender(clip_w, clip_h, xclipping=False, yclipping=False)
    parent_neg.blit(solid, child_ox, child_oy)
    root_neg = FakeRender(vw, vh)
    root_neg.blit(parent_neg, clip_x, clip_y)

    rw2, rh2, rgba2 = _present(draw, root_neg)
    c2_left = _sample(rgba2, rw2, rh2, ox_l, oy_l)
    c2_in = _sample(rgba2, rw2, rh2, ix, iy)
    # Outside-left must now be green (overflow drawn).
    neg_overflow = _near(c2_left, GREEN, tol=60)
    neg_in = _near(c2_in, GREEN, tol=60)
    negative_ok = neg_overflow and neg_in

    # ---- Crop+zoom-shaped slot (C3 unblock): clip parent + offset child ----
    # Mimics crop(0,825,1920,255) style: tall source placed with negative y under
    # a short clipped viewport.
    slot_w, slot_h = 400, 100
    slot_x, slot_y = 100, 500
    tall = draw.solid_texture(400, 400, GREEN)
    slot = FakeRender(slot_w, slot_h, xclipping=True, yclipping=True)
    # Source content starts 200px above slot → only lower band should show.
    slot.blit(tall, 0, -200)
    root_slot = FakeRender(vw, vh)
    root_slot.blit(slot, slot_x, slot_y)
    rw3, rh3, rgba3 = _present(draw, root_slot)
    sx_in, sy_in = _virt_to_rt(slot_x + slot_w // 2, slot_y + slot_h // 2, vw, vh, rw3, rh3)
    sx_out, sy_out = _virt_to_rt(slot_x + slot_w // 2, slot_y - 40, vw, vh, rw3, rh3)
    c3_in = _sample(rgba3, rw3, rh3, sx_in, sy_in)
    c3_out = _sample(rgba3, rw3, rh3, sx_out, sy_out)
    slot_ok = _near(c3_in, GREEN, tol=60) and (not _near(c3_out, GREEN, tol=60))

    ok = positive_ok and negative_ok and slot_ok
    lines = [
        f"ok={ok}",
        f"positive_ok={positive_ok} in={c_in} left={c_left} top={c_top} right={c_right}",
        f"negative_ok={negative_ok} overflow_left={c2_left} in={c2_in}",
        f"slot_ok={slot_ok} in={c3_in} above={c3_out}",
        f"rt={rw}x{rh} virt={vw}x{vh}",
        "contract=GL2 xclipping/yclipping mesh-crop axis-aligned v1",
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
