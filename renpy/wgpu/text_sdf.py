"""
SDF generator for glyph bitmaps.

def render_sdf_glyph(bitmap:bytes,w,h,radius=8)->bytes
Uses 8-neighbor distance transform to produce 8-bit SDF (scipy if
available, else pure Python BFS). Synchronized with shader.rs threshold:
PIL_PADDING and SDF_RADIUS are separate constants (constants.py).
"""

from __future__ import annotations

import math

from .constants import SDF_RADIUS, SDF_THRESHOLD  # noqa: F401 — provenance re-export

# Try scipy for accelerated EDT; fallback pure Python BFS is fine for glyph-sized bitmaps.
try:
    import scipy.ndimage as _ndi  # type: ignore

    _HAS_SCIPY = True
except Exception:
    _ndi = None  # type: ignore
    _HAS_SCIPY = False


def _extract_alpha(bitmap: bytes | bytearray, w: int, h: int) -> list[int]:
    """Extract alpha channel as flat list w*h ints 0..255."""
    n = w * h
    lb = len(bitmap)
    if lb >= n * 4:
        # RGBA — alpha is every 4th byte starting at 3
        return [bitmap[i + 3] for i in range(0, n * 4, 4)]
    if lb >= n:
        # Grayscale/L or first channel
        # If lb == n*4 we handled; now lb == n => treat as alpha/luminance
        return [bitmap[i] for i in range(n)]
    # Truncated — pad with zeros
    out = [0] * n
    for i in range(min(lb, n)):
        out[i] = bitmap[i]
    return out


def _sdf_via_scipy(alpha: list[int], w: int, h: int, radius: int) -> bytes:
    """SDF via scipy.ndimage.distance_transform_edt if available."""
    import numpy as np  # type: ignore

    # Foreground where alpha > 127 (inside glyph)
    a = np.array(alpha, dtype=np.uint8).reshape(h, w)
    # Binary masks: 0 background, 1 foreground
    inside = a > 127
    outside = ~inside
    # distance to opposite: for inside pixels, distance to nearest outside;
    # for outside pixels, distance to nearest inside. EDT on inverted.
    # _ndi.distance_transform_edt returns float distances in pixels.
    # Compute signed distance: outside positive, inside negative? We want
    # signed = dist_outside - dist_inside? Choose outside - inside.
    # Actually for threshold pipeline we encode: 0.5 at edge.
    # So signed = dist_out - dist_in where dist_out = distance to foreground?
    # Simpler: compute both.
    if np.any(inside) and np.any(outside):
        dist_out = _ndi.distance_transform_edt(outside)  # distance to inside for outside pixels
        dist_in = _ndi.distance_transform_edt(inside)  # distance to outside for inside pixels
        # For inside pixels: negative distance; outside: positive
        signed = np.where(inside, -dist_in, dist_out).astype(np.float32)
    else:
        # Fully empty or fully filled — degenerate glyph
        signed = np.zeros((h, w), dtype=np.float32)
        if np.all(inside):
            signed[:, :] = -radius
        else:
            signed[:, :] = radius
    # Normalize to 0..255 with threshold 0.5 => 128 at signed=0, AA across radius.
    # Map: sdf = clamp(0.5 + signed/(2*radius), 0,1)*255  => 128 +/- signed*(127.5/radius)/? Actually 0.5 +/-.
    # Simpler: sdf = clamp(128 + signed * (127 / radius))
    # Ensure radius>0
    r = max(1, float(radius))
    # For consistency with shader: sample .r then smoothstep(thr-aa, thr+aa, d) where d is sdf/255
    # We encode so that threshold 0.5 = edge. So sdf = 0.5 + signed/(2*r) ??? But then inside negative -> <0.5.
    # Choose: sdf_norm = 0.5 - signed/(2*r) ?? Need inside to be 1, outside 0.
    # Let's define: inside negative signed => want high value (1). So sdf_norm = 0.5 - signed/(2*r)
    # Wait test.
    # For signed negative (inside), -signed positive => 0.5 + |signed|/(2r) >0.5 => good (opaque)
    # For signed positive (outside), 0.5 - signed/(2r) <0.5 => transparent.
    # So invert.
    # Alternative simple linear: sdf = 128 + signed * (-127/r) ??? Let's compute norm = 0.5 - signed/(2*r)
    # But then scale to 0..1 clamp.
    norm = 0.5 - signed / (2.0 * r)  # type: ignore
    # Clamp 0..1
    norm = np.clip(norm, 0.0, 1.0)
    out = (norm * 255.0).astype(np.uint8)
    return out.tobytes()


def _sdf_pure_bfs(alpha: list[int], w: int, h: int, radius: int) -> bytes:
    """Pure Python BFS / brute-force 8-neighbor EDT for small glyph bitmaps."""
    n = w * h
    # binary inside?
    inside = [a > 127 for a in alpha]
    # If degenerate -> constant
    if all(inside):
        return bytes([255] * n)
    if not any(inside):
        return bytes([0] * n)
    # For efficiency, collect coords of edge-adjacent? Brute search within radius window per pixel.
    r = int(radius)
    r2 = r * r
    out = bytearray(n)
    # Precompute inside list for speed
    # For each pixel, find min euclidean distance to opposite class within radius.
    for y in range(h):
        row_off = y * w
        for x in range(w):
            idx = row_off + x
            is_in = inside[idx]
            # Search radius window for opposite pixel with minimal distance
            best = r + 1.0  # > radius means clamp to opposite side
            # Limit search bounds
            y0 = max(0, y - r)
            y1 = min(h, y + r + 1)
            x0 = max(0, x - r)
            x1 = min(w, x + r + 1)
            found = False
            min_d2 = r2 + 1
            for yy in range(y0, y1):
                yy_off = yy * w
                dy = yy - y
                dy2 = dy * dy
                if dy2 > r2:
                    continue
                for xx in range(x0, x1):
                    j = yy_off + xx
                    if inside[j] == is_in:
                        continue
                    dx = xx - x
                    d2 = dx * dx + dy2
                    if d2 < min_d2:
                        min_d2 = d2
                        found = True
                        if min_d2 == 0:
                            break
                        if min_d2 == 1 and r >= 1:
                            # can't get much smaller than 1 unless diagonal 2 etc.
                            pass
                if min_d2 == 0:
                    break
            if found:
                d = math.sqrt(float(min_d2))
                # Clamp to radius
                if d > r:
                    d = float(r)
                # For inside, signed negative -> high sdf; outside positive -> low.
                # signed = +d if outside, -d if inside.
                signed = d if not is_in else -d
            else:
                # No opposite within radius → far field -> clamp to radius direction.
                signed = float(r) if not is_in else -float(r)
            # norm = 0.5 - signed/(2*r) => see scipy path for consistency
            norm = 0.5 - signed / (2.0 * max(1, r))
            if norm < 0:
                norm = 0
            elif norm > 1:
                norm = 1
            v = int(round(norm * 255))
            if v < 0:
                v = 0
            elif v > 255:
                v = 255
            out[idx] = v
    return bytes(out)


def render_sdf_glyph(bitmap: bytes | bytearray, w: int, h: int, radius: int = SDF_RADIUS) -> bytes:
    """
    Produce 8-bit SDF from glyph bitmap.

    Input ``bitmap`` may be RGBA (w*h*4) or L (w*h) bytes. Alpha >127 is
    considered inside. Returns w*h bytes 0..255 SDF where 128 ≈ edge
    (threshold 0.5), >128 inside, <128 outside, with smooth radius.

    Uses scipy.ndimage EDT if available, else pure Python 8-neighbor BFS
    within ``radius``. Keeps PIL_PADDING (text.py) and SDF_RADIUS separate.
    """
    if w <= 0 or h <= 0:
        return b""
    if not bitmap:
        return bytes([0] * (w * h))
    # Clamp radius to reasonable
    if radius <= 0:
        # degenerate -> simple threshold 0/255
        alpha = _extract_alpha(bitmap, w, h)
        return bytes([255 if a > 127 else 0 for a in alpha])
    alpha = _extract_alpha(bitmap, w, h)
    if _HAS_SCIPY:
        try:
            return _sdf_via_scipy(alpha, w, h, int(radius))
        except Exception:
            # fall through to pure
            pass
    return _sdf_pure_bfs(alpha, w, h, int(radius))


def decode_sdf_alpha(sdf_bytes: bytes | bytearray, threshold: float = SDF_THRESHOLD, aa: float = 0.02) -> bytes:
    """
    Decode SDF bytes to alpha bytes 0..255 via smoothstep, mirroring shader.

    shader: alpha = smoothstep(thr - aa, thr + aa, d) where d = sdf/255
    For testing MAE at 1x: decode then compare to original alpha.
    """
    if not sdf_bytes:
        return b""
    thr = float(threshold)
    half = float(aa)
    lo = thr - half
    hi = thr + half
    span = hi - lo if hi != lo else 1e-6
    out = bytearray(len(sdf_bytes))
    for i, b in enumerate(sdf_bytes):
        d = b / 255.0
        if d <= lo:
            a = 0.0
        elif d >= hi:
            a = 1.0
        else:
            t = (d - lo) / span
            # smoothstep 3t^2 -2t^3
            a = t * t * (3.0 - 2.0 * t)
        out[i] = int(round(a * 255))
    return bytes(out)


__all__ = ["render_sdf_glyph", "decode_sdf_alpha", "SDF_RADIUS", "SDF_THRESHOLD"]
