"""
AC-S3 splash_cover_fit — 3840×2160 under full_fill cover → 1920×1080.

Gate name: splash_cover_fit  (RENPY_HOST_GATE=splash_cover_fit)

Proves host RenderTransform (renpy_display_accelerator_host) implements
fit "cover" + xsize/ysize like stock accelerator.pyx:

  child 3840×2160 + xsize 1920 ysize 1080 fit cover
    → mul = max(1920/3840, 1080/2160) = 0.5
    → render size 1920×1080
    → reverse Matrix2D(0.5, 0, 0, 0.5)

Also probes WgpuDraw._reverse_dest_size for a full 3840×2160 HostTexture
under reverse 0.5 → dest parent box 1920×1080 (not child*scale wrong path).

Note: no from __future__; host run_file prepends imports.
"""

import os
from pathlib import Path

from host.python.gates._harness import gate_harness, parametrized_gate

import renpy_host  # type: ignore

from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-splash_cover_fit.txt"
out.parent.mkdir(parents=True, exist_ok=True)

lines = []
ok = True


def log(msg):
    lines.append(msg)
    try:
        os.write(1, (f"[splash_cover_fit] {msg}\n").encode("utf-8", "replace"))
    except Exception:
        pass


class Mat2:
    def __init__(self, xdx, xdy, ydx, ydy):
        self.xdx = float(xdx)
        self.xdy = float(xdy)
        self.ydx = float(ydx)
        self.ydy = float(ydy)

    def inverse(self):
        ix = 1.0 / self.xdx if abs(self.xdx) > 1e-12 else 1.0
        iy = 1.0 / self.ydy if abs(self.ydy) > 1e-12 else 1.0
        return Mat2(ix, 0.0, 0.0, iy)


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
        self.reverse = None
        self.forward = None

    def blit(self, source, pos):
        x, y = pos if pos is not None else (0, 0)
        self.children.append((source, x, y))

    def subpixel_blit(self, source, pos):
        self.blit(source, pos)


class FakeState:
    def __init__(self, **kw):
        self.xsize = kw.get("xsize")
        self.ysize = kw.get("ysize")
        self.fit = kw.get("fit")
        self.zoom = kw.get("zoom", 1.0)
        self.xzoom = kw.get("xzoom", 1.0)
        self.yzoom = kw.get("yzoom", 1.0)
        self.rotate = kw.get("rotate")
        self.rotate_pad = kw.get("rotate_pad", True)
        self.maxsize = kw.get("maxsize")
        self.crop = kw.get("crop")
        self.crop_relative = kw.get("crop_relative", False)
        self.corner1 = kw.get("corner1")
        self.corner2 = kw.get("corner2")
        self.alpha = kw.get("alpha", 1.0)
        self.subpixel = kw.get("subpixel", False)
        self.mesh = None
        self.blur = None
        self.perspective = None
        self.mesh_pad = None
        self.xtile = 1
        self.ytile = 1
        self.xpan = None
        self.ypan = None
        self.events = True
        self.last_events = True


class FakeChild:
    def __init__(self, w, h):
        self.w = w
        self.h = h

    def render(self, width, height, st, at):
        return FakeRender(self.w, self.h)


class FakeTransform:
    def __init__(self, child, state):
        self.child = child
        self.state = state
        self.child_st_base = 0
        self.reverse = None
        self.forward = None
        self.offsets = None
        self.render_size = None
        self.child_size = None


try:
    # --- Unit: fit cover math via RenderTransform ----------------------------

    from renpy_display_accelerator_host import RenderTransform

    # Patch renpy.display.render.render used inside RenderTransform to call child.render

    # Ensure display.accelerator module is our host one (host already aliases it).
    child = FakeChild(3840, 2160)
    state = FakeState(xsize=1920, ysize=1080, fit="cover")
    t = FakeTransform(child, state)
    rt = RenderTransform(t)
    rv = rt.render(1920, 1080, 0.0, 0.0)

    rw = float(getattr(rv, "width", -1))
    rh = float(getattr(rv, "height", -1))
    log(f"render_size={rw:.1f}x{rh:.1f} expect=1920x1080")
    if abs(rw - 1920) > 1 or abs(rh - 1080) > 1:
        ok = False
        log(f"FAIL cover render size wrong (got {rw:.1f}x{rh:.1f})")
    else:
        log("PASS cover render size")

    rev = getattr(rv, "reverse", None) or getattr(t, "reverse", None)
    xdx = float(getattr(rev, "xdx", 1.0) or 1.0) if rev is not None else 1.0
    ydy = float(getattr(rev, "ydy", 1.0) or 1.0) if rev is not None else 1.0
    log(f"reverse xdx={xdx:.4f} ydy={ydy:.4f} expect≈0.5")
    if abs(xdx - 0.5) > 1e-3 or abs(ydy - 0.5) > 1e-3:
        ok = False
        log("FAIL reverse scale not 0.5 (double-scale or no-scale)")
    else:
        log("PASS reverse 0.5 cover")

    # --- WgpuDraw reverse-dest for full 3840×2160 under reverse 0.5 ----------
    draw = WgpuDraw()
    # Full HostTexture of physical 3840×2160
    # create tiny real texture then wrap as full 3840×2160 for size logic only
    # (dest size path only needs HostTexture size + full UV, not pixel content)
    try:
        handle = renpy_host.create_texture_rgba(4, 4, bytes([255, 0, 0, 255] * 16))
    except Exception:
        handle = 1
    ht = HostTexture(handle, 3840, 2160)  # full rect by default
    parent = FakeRender(1920, 1080)
    parent.reverse = Mat2(0.5, 0.0, 0.0, 0.5)
    parent.children = [(ht, 0, 0)]

    dw, dh = draw._reverse_dest_size(parent, ht, (1920, 1080))
    log("reverse_dest=%dx%d expect=1920x1080" % (dw, dh))  # noqa: UP031
    if dw != 1920 or dh != 1080:
        ok = False
        log("FAIL reverse_dest full 2x under 0.5 not parent box")
    else:
        log("PASS reverse_dest parent box for full 2x")

    # Partial UV must still map child*scale (typewriter contract).
    ht_part = HostTexture(handle, 3840, 2160, x=0, y=0, w=400, h=48)
    dw2, dh2 = draw._reverse_dest_size(parent, ht_part, (1920, 1080))
    # child size for partial HostTexture is (w,h)=(400,48) via _node_size path —
    # if resolution fails, may fall back to parent; assert not balloon to full if partial known.
    log("partial reverse_dest=%dx%d (typewriter must not balloon)" % (dw2, dh2))  # noqa: UP031
    # 400*0.5=200, 48*0.5=24 expected when partial detected
    if dw2 == 1920 and dh2 == 1080:
        ok = False
        log("FAIL partial ballooned to full parent (AC-T regression risk)")
    else:
        if abs(dw2 - 200) <= 2 and abs(dh2 - 24) <= 2:
            log("PASS partial child*scale")
        else:
            ok = False
            log("FAIL partial dest unexpected %dx%d expect≈200x24" % (dw2, dh2))  # noqa: UP031

    # --- logo@2 reverse dest (main menu automatic oversample) ---------------
    # logo@2.png is 1370×2132; with oversample=2 virtual size is 685×1066 and
    # reverse 0.5. Full HostTexture must fill that virtual parent, not 1370.
    logo_parent_w, logo_parent_h = 685, 1066
    logo_tex_w, logo_tex_h = 1370, 2132
    ht_logo = HostTexture(handle, logo_tex_w, logo_tex_h)
    logo_node = FakeRender(logo_parent_w, logo_parent_h)
    logo_node.reverse = Mat2(0.5, 0.0, 0.0, 0.5)
    logo_node.children = [(ht_logo, 0, 0)]
    ldw, ldh = draw._reverse_dest_size(logo_node, ht_logo, (logo_parent_w, logo_parent_h))
    log(
        "logo@2 reverse_dest=%dx%d expect=%dx%d (not double %dx%d)"  # noqa: UP031
        % (ldw, ldh, logo_parent_w, logo_parent_h, logo_tex_w, logo_tex_h)
    )
    if ldw != logo_parent_w or ldh != logo_parent_h:
        ok = False
        log("FAIL logo@2 reverse_dest double-scale or wrong shrink")
    else:
        log("PASS logo@2 reverse_dest virtual size")

    # --- Real 3840×2160 pixel draw: cover reverse fills 1920×1080 ----------
    # Patterned corners + white center so we can prove cover fill (no letterbox
    # dark hole, no half-size speck, no double-scale crop of only TL quarter).
    from renpy.pygame.surface import Surface

    VW, VH = 1920, 1080
    TW, TH = 3840, 2160
    draw.init((VW, VH))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    surf = Surface((TW, TH))
    surf.fill((20, 20, 40, 255))
    # Stamp 200px corners + center white (set_at loops limited to small regions).
    for y in range(200):
        for x in range(200):
            surf.set_at((x, y), (255, 0, 0, 255))
            surf.set_at((TW - 1 - x, y), (0, 255, 0, 255))
            surf.set_at((x, TH - 1 - y), (0, 0, 255, 255))
            surf.set_at((TW - 1 - x, TH - 1 - y), (255, 255, 0, 255))
    for y in range(TH // 2 - 80, TH // 2 + 80):
        for x in range(TW // 2 - 80, TW // 2 + 80):
            surf.set_at((x, y), (255, 255, 255, 255))

    full_tex = draw.load_texture(surf)
    assert isinstance(full_tex, HostTexture)
    assert full_tex.w == TW and full_tex.h == TH
    assert draw._host_tex_is_full(full_tex)

    # Product-like tree: reverse cover node sized 1920×1080, child full 3840 tex.
    piece = FakeRender(VW, VH)
    piece.reverse = Mat2(0.5, 0.0, 0.0, 0.5)
    piece.forward = Mat2(2.0, 0.0, 0.0, 2.0)
    piece.children = [(full_tex, 0, 0)]
    piece.cached_texture = full_tex

    dest_draw = draw._reverse_dest_size(piece, full_tex, (VW, VH))
    log("pixel reverse_dest=%dx%d expect=%dx%d" % (dest_draw[0], dest_draw[1], VW, VH))  # noqa: UP031
    if dest_draw != (VW, VH):
        ok = False
        log("FAIL pixel-path reverse_dest not virtual cover box")

    bg = Surface((VW, VH))
    bg.fill((8, 8, 8, 255))
    root = FakeRender(VW, VH)
    root.children = [(bg, 0, 0), (piece, 0, 0)]

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        ok = False
        log(f"FAIL read_game_rt_rgba: {e}")
        rw = rh = 0
        rgba = b""

    def _sample(x, y):
        x = max(0, min(rw - 1, int(x)))
        y = max(0, min(rh - 1, int(y)))
        o = (y * rw + x) * 4
        return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]

    def _near(c, target, tol=50):
        return all(abs(int(c[i]) - int(target[i])) <= tol for i in range(3))

    if rw > 0 and rh > 0 and rgba:
        sx = rw / float(VW)
        sy = rh / float(VH)
        c_c = _sample(VW // 2 * sx, VH // 2 * sy)
        c_tl = _sample(8 * sx, 8 * sy)
        c_tr = _sample((VW - 9) * sx, 8 * sy)
        c_bl = _sample(8 * sx, (VH - 9) * sy)
        c_br = _sample((VW - 9) * sx, (VH - 9) * sy)
        log(
            f"pixel samples center={c_c} tl={c_tl} tr={c_tr} bl={c_bl} br={c_br}"
        )
        # Cover fill: corners of virtual screen map to corners of 3840 tex.
        center_ok = _near(c_c, (255, 255, 255))
        tl_ok = _near(c_tl, (255, 0, 0))
        tr_ok = _near(c_tr, (0, 255, 0))
        bl_ok = _near(c_bl, (0, 0, 255))
        br_ok = _near(c_br, (255, 255, 0))
        # Not black hole / letterbox (would be ~bg 8,8,8 at corners or center).
        not_letterbox = not (
            c_tl[0] < 30 and c_tl[1] < 30 and c_tl[2] < 30
        ) and not (c_c[0] < 30 and c_c[1] < 30 and c_c[2] < 30)
        # Not double-scale TL-quarter-only: if dest were 3840 virtual, NDC would
        # clip to only top-left of texture and TR/BL/BR would be bg or wrong.
        fill_ok = center_ok and tl_ok and tr_ok and bl_ok and br_ok and not_letterbox
        log(
            f"pixel fill center_ok={center_ok} tl={tl_ok} tr={tr_ok} bl={bl_ok} br={br_ok} not_letterbox={not_letterbox}"
        )
        if fill_ok:
            log("PASS pixel cover fill 3840→1920 (no double-scale / letterbox)")
        else:
            ok = False
            log("FAIL pixel cover fill wrong (double-scale or letterbox)")
    else:
        ok = False
        log("FAIL no RT pixels for cover fill probe")

    try:
        if handle and handle > 1:
            renpy_host.destroy_texture(handle)
    except Exception:
        pass

except Exception as e:
    ok = False
    log(f"FAIL exception: {type(e).__name__}: {e}")
    import traceback

    log(traceback.format_exc())

lines.append(f"ok={ok}")
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
log(f"WROTE {out} ok={ok}")
try:
    renpy_host.request_quit()
except Exception:
    pass
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
