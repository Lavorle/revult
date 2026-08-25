"""
Host pure-Python stand-in for the `_renpy` C extension.

SDL `_renpy` (src/core.c + IMG_savepng) is Class B (links libSDL/libpng).
Host MVP provides software RGBA ops on host_pygame.Surface so
`renpy.display.scale` / `module` can import under renpy-host.

Installed as `sys.modules["_renpy"]` by the host embed preamble.
"""

from __future__ import annotations

from typing import Any


def _pixels(surf: Any) -> bytearray:
    px = getattr(surf, "_pixels", None)
    if px is None:
        raise TypeError(f"host _renpy expects Surface with _pixels, got {type(surf)!r}")
    return px


def _size(surf: Any) -> tuple[int, int]:
    if hasattr(surf, "get_size"):
        w, h = surf.get_size()
        return int(w), int(h)
    return int(getattr(surf, "width", 0)), int(getattr(surf, "height", 0))


def _copy_rect(src: Any, dst: Any) -> None:
    sp, dp = _pixels(src), _pixels(dst)
    n = min(len(sp), len(dp))
    dp[:n] = sp[:n]


def pixellate(src, dst, avgwidth, avgheight, outwidth, outheight):
    """Box-average downsample then nearest upsample (good enough for host)."""
    sw, sh = _size(src)
    dw, dh = _size(dst)
    sp, dp = _pixels(src), _pixels(dst)
    aw = max(1, int(avgwidth))
    ah = max(1, int(avgheight))
    # outwidth/outheight kept for API compat; virtual grid derives from avgwidth/avgheight
    # virtual grid
    vw = max(1, (sw + aw - 1) // aw)
    vh = max(1, (sh + ah - 1) // ah)
    virtual = bytearray(vw * vh * 4)
    for vy in range(vh):
        for vx in range(vw):
            r = g = b = a = c = 0
            for yy in range(ah):
                sy = vy * ah + yy
                if sy >= sh:
                    break
                for xx in range(aw):
                    sx = vx * aw + xx
                    if sx >= sw:
                        break
                    i = (sy * sw + sx) * 4
                    r += sp[i]
                    g += sp[i + 1]
                    b += sp[i + 2]
                    a += sp[i + 3]
                    c += 1
            if c:
                j = (vy * vw + vx) * 4
                virtual[j] = r // c
                virtual[j + 1] = g // c
                virtual[j + 2] = b // c
                virtual[j + 3] = a // c
    for y in range(dh):
        vy = min(vh - 1, (y * vh) // max(1, dh))
        for x in range(dw):
            vx = min(vw - 1, (x * vw) // max(1, dw))
            si = (vy * vw + vx) * 4
            di = (y * dw + x) * 4
            dp[di : di + 4] = virtual[si : si + 4]


def transform(src, dst, *args, **kwargs):
    """Affine transform stub: nearest-neighbor copy/scale to dst size.

    Uses u32 pixel views (Task #24 F1: per-byte slice scale was multi-10ms
    and stacked into multi-second main-menu freezes during predict).
    """
    sw, sh = _size(src)
    dw, dh = _size(dst)
    if sw == dw and sh == dh:
        _copy_rect(src, dst)
        return
    if dw <= 0 or dh <= 0 or sw <= 0 or sh <= 0:
        return
    sp = memoryview(_pixels(src)).cast("I")
    dp = memoryview(_pixels(dst)).cast("I")
    xs = [min(sw - 1, (x * sw) // dw) for x in range(dw)]
    for y in range(dh):
        sy = min(sh - 1, (y * sh) // dh)
        si_row = sy * sw
        di_row = y * dw
        for x, sx in enumerate(xs):
            dp[di_row + x] = sp[si_row + sx]


def linmap(src, dst, rmap, gmap, bmap, amap):
    """Fixed-point linear map (1.0 == 256) per channel."""
    sw, sh = _size(src)
    sp, dp = _pixels(src), _pixels(dst)
    maps = (int(rmap), int(gmap), int(bmap), int(amap))
    for i in range(0, sw * sh * 4, 4):
        for c in range(4):
            v = (sp[i + c] * maps[c]) >> 8
            dp[i + c] = 255 if v > 255 else (max(v, 0))


def map(src, dst, rmap, gmap, bmap, amap):
    """256-entry LUT map per channel (bytes/bytearray/str)."""

    def _lut(m):
        if m is None:
            return bytes(range(256))
        if isinstance(m, (bytes, bytearray)):
            return m
        if isinstance(m, str):
            return m.encode("latin-1")
        return bytes(m)

    rl, gl, bl, al = _lut(rmap), _lut(gmap), _lut(bmap), _lut(amap)
    sw, sh = _size(src)
    sp, dp = _pixels(src), _pixels(dst)
    for i in range(0, sw * sh * 4, 4):
        dp[i] = rl[sp[i]]
        dp[i + 1] = gl[sp[i + 1]]
        dp[i + 2] = bl[sp[i + 2]]
        dp[i + 3] = al[sp[i + 3]]


def blur(src, wrk, dst, xrad, yrad=None):
    """Separable box blur approximation."""
    if yrad is None:
        yrad = xrad
    xr = max(0, int(xrad))
    yr = max(0, int(yrad))
    sw, sh = _size(src)
    sp = _pixels(src)
    tmp = bytearray(sp)
    out = _pixels(dst)
    # horizontal
    for y in range(sh):
        for x in range(sw):
            r = g = b = a = c = 0
            for dx in range(-xr, xr + 1):
                xx = x + dx
                if 0 <= xx < sw:
                    i = (y * sw + xx) * 4
                    r += sp[i]
                    g += sp[i + 1]
                    b += sp[i + 2]
                    a += sp[i + 3]
                    c += 1
            j = (y * sw + x) * 4
            if c:
                tmp[j] = r // c
                tmp[j + 1] = g // c
                tmp[j + 2] = b // c
                tmp[j + 3] = a // c
    # vertical
    for y in range(sh):
        for x in range(sw):
            r = g = b = a = c = 0
            for dy in range(-yr, yr + 1):
                yy = y + dy
                if 0 <= yy < sh:
                    i = (yy * sw + x) * 4
                    r += tmp[i]
                    g += tmp[i + 1]
                    b += tmp[i + 2]
                    a += tmp[i + 3]
                    c += 1
            j = (y * sw + x) * 4
            if c:
                out[j] = r // c
                out[j + 1] = g // c
                out[j + 2] = b // c
                out[j + 3] = a // c


def alpha_munge(src, dst, red, alpha, amap):
    sw, sh = _size(src)
    sp, dp = _pixels(src), _pixels(dst)
    lut = amap if isinstance(amap, (bytes, bytearray)) else bytes(amap)
    for i in range(sw * sh):
        base = i * 4
        v = sp[base + int(red)]
        dp[base + int(alpha)] = lut[v]


def bilinear(src, dst, sx=0, sy=0, sw=None, sh=None, dx=0, dy=0, dw=None, dh=None, precise=0):
    """Nearest-neighbor stand-in for bilinear (host MVP). u32 pixel path."""
    src_w, src_h = _size(src)
    dst_w, dst_h = _size(dst)
    if sw is None:
        sw = src_w - sx
    if sh is None:
        sh = src_h - sy
    if dw is None:
        dw = dst_w - dx
    if dh is None:
        dh = dst_h - dy
    sw = max(1, int(sw))
    sh = max(1, int(sh))
    dw = max(1, int(dw))
    dh = max(1, int(dh))
    sx = int(sx)
    sy = int(sy)
    dx = int(dx)
    dy = int(dy)
    sp = memoryview(_pixels(src)).cast("I")
    dp = memoryview(_pixels(dst)).cast("I")
    xs = [sx + min(sw - 1, (x * sw) // dw) for x in range(dw)]
    for y in range(dh):
        syi = sy + min(sh - 1, (y * sh) // dh)
        if not (0 <= syi < src_h):
            continue
        dyi = dy + y
        if not (0 <= dyi < dst_h):
            continue
        si_row = syi * src_w
        di_row = dyi * dst_w
        for x, sxi in enumerate(xs):
            if not (0 <= sxi < src_w):
                continue
            dxi = dx + x
            if not (0 <= dxi < dst_w):
                continue
            dp[di_row + dxi] = sp[si_row + sxi]


def blend(a, b, dst, alpha=None):
    """Lerp a→b by alpha (0..255) or per-pixel if alpha is None (use a.a)."""
    aw, ah = _size(a)
    ap, bp, dp = _pixels(a), _pixels(b), _pixels(dst)
    for i in range(0, aw * ah * 4, 4):
        t = int(alpha) if alpha is not None else ap[i + 3]
        inv = 255 - t
        for c in range(4):
            dp[i + c] = (ap[i + c] * inv + bp[i + c] * t) // 255


def imageblend(a, b, dst, img, alpha_offset, amap):
    aw, ah = _size(a)
    ap, bp, dp, ip = _pixels(a), _pixels(b), _pixels(dst), _pixels(img)
    lut = amap if isinstance(amap, (bytes, bytearray)) else bytes(amap)
    off = int(alpha_offset)
    for i in range(0, aw * ah * 4, 4):
        t = lut[ip[i + off]]
        inv = 255 - t
        for c in range(4):
            dp[i + c] = (ap[i + c] * inv + bp[i + c] * t) // 255


def colormatrix(src, dst, *coeffs):
    """
    Apply 4x5 color matrix. `coeffs` is 20 floats in row-major
    (as passed by renpy.display.module.colormatrix after reordering).
    """
    if len(coeffs) < 20:
        _copy_rect(src, dst)
        return
    m = [float(x) for x in coeffs[:20]]
    sw, sh = _size(src)
    sp, dp = _pixels(src), _pixels(dst)
    for i in range(0, sw * sh * 4, 4):
        r, g, b, a = sp[i], sp[i + 1], sp[i + 2], sp[i + 3]
        vals = [r, g, b, a]
        out = [0, 0, 0, 0]
        for row in range(4):
            base = row * 5
            v = (
                m[base] * vals[0]
                + m[base + 1] * vals[1]
                + m[base + 2] * vals[2]
                + m[base + 3] * vals[3]
                + m[base + 4] * 255.0
            )
            out[row] = 0 if v < 0 else (255 if v > 255 else int(v))
        dp[i : i + 4] = bytes(out)


def subpixel(src, dst, x, y, shift=0):
    """Integer blit offset (subpixel ignored on host MVP).

    Opaque rows bulk-copy; partial alpha still per-pixel (common UI chrome
    is fully opaque so this kills the main-menu stall).
    """
    sw, sh = _size(src)
    dw, dh = _size(dst)
    sp_b = _pixels(src)
    dp_b = _pixels(dst)
    ox, oy = int(x), int(y)
    # Clip destination span.
    src_x0 = 0
    src_y0 = 0
    if ox < 0:
        src_x0 = -ox
        ox = 0
    if oy < 0:
        src_y0 = -oy
        oy = 0
    copy_w = min(sw - src_x0, dw - ox)
    copy_h = min(sh - src_y0, dh - oy)
    if copy_w <= 0 or copy_h <= 0:
        return
    spitch = sw * 4
    dpitch = dw * 4
    nbytes = copy_w * 4
    sp = memoryview(sp_b)
    dp = memoryview(dp_b)
    for row in range(copy_h):
        sy = src_y0 + row
        dy = oy + row
        si = sy * spitch + src_x0 * 4
        di = dy * dpitch + ox * 4
        # Fast path: if whole span is opaque (sample first+last alpha), bulk copy.
        if sp[si + 3] == 255 and sp[si + nbytes - 1] == 255:
            # Sparse check a few alphas in the row; fall back if any translucent.
            step = max(4, nbytes // 16)
            opaque = True
            for aoff in range(3, nbytes, step):
                if sp[si + aoff] != 255:
                    opaque = False
                    break
            if opaque:
                dp[di : di + nbytes] = sp[si : si + nbytes]
                continue
        # Per-pixel over for mixed-alpha rows.
        for col in range(copy_w):
            p = si + col * 4
            q = di + col * 4
            sa = sp[p + 3]
            if sa == 0:
                continue
            if sa == 255:
                dp[q : q + 4] = sp[p : p + 4]
            else:
                inv = 255 - sa
                dp[q] = (sp[p] * sa + dp[q] * inv) // 255
                dp[q + 1] = (sp[p + 1] * sa + dp[q + 1] * inv) // 255
                dp[q + 2] = (sp[p + 2] * sa + dp[q + 2] * inv) // 255
                dp[q + 3] = (sp[p + 3] * sa + dp[q + 3] * inv) // 255


def premultiply_alpha(src, dst):
    sw, sh = _size(src)
    sp, dp = _pixels(src), _pixels(dst)
    for i in range(0, sw * sh * 4, 4):
        a = sp[i + 3]
        dp[i] = (sp[i] * a) // 255
        dp[i + 1] = (sp[i + 1] * a) // 255
        dp[i + 2] = (sp[i + 2] * a) // 255
        dp[i + 3] = a


def save_png(surf, filename, compress=3):
    """Best-effort PNG via Pillow if available; else raw dump."""
    try:
        from PIL import Image  # type: ignore

        w, h = _size(surf)
        img = Image.frombytes("RGBA", (w, h), bytes(_pixels(surf)))
        img.save(filename, format="PNG", compress_level=int(compress))
    except Exception:
        with open(filename, "wb") as f:
            w, h = _size(surf)
            f.write(b"RAW0")
            f.write(w.to_bytes(4, "little"))
            f.write(h.to_bytes(4, "little"))
            f.write(bytes(_pixels(surf)))
