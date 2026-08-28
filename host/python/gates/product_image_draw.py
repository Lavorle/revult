"""
AC-P3 micro-gate: product-like Render(mesh=True) + Image surface through WgpuDraw.

Gate name: product_image_draw  (RENPY_HOST_GATE=product_image_draw)

Constructs a mesh container with a full-window surface of gui/main_menu.png
(or solid fallback), runs draw_screen (load_all_textures + prefer cached_model),
reads RT; requires nonblank far from arena clear.

Note: host run_file prepends import preamble — do not use from __future__ or __file__.
"""
import os
import struct
import zlib
from pathlib import Path

import renpy_host  # type: ignore

from renpy.wgpu.draw import HostTexture, WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---


def _png_rgba(path):
    """Minimal PNG decoder for 8-bit RGBA/RGB (no interlacing). Returns (w,h,rgba)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"not png: {path}"
    pos = 8
    w = h = None
    raw = b""
    color_type = None
    bit_depth = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            raw += chunk
        elif ctype == b"IEND":
            break
    if not w or not h:
        raise RuntimeError("bad png header")
    if bit_depth != 8 or color_type not in (2, 6):
        raise RuntimeError(f"unsupported png ct={color_type} bd={bit_depth}")
    decomp = zlib.decompress(raw)
    bpp = 4 if color_type == 6 else 3
    stride = w * bpp + 1
    out = bytearray(w * h * 4)
    prev = bytearray(w * bpp)
    for y in range(h):
        row = decomp[y * stride : (y + 1) * stride]
        filt = row[0]
        scan = bytearray(row[1:])
        if filt == 1:  # Sub
            for i in range(bpp, len(scan)):
                scan[i] = (scan[i] + scan[i - bpp]) & 0xFF
        elif filt == 2:  # Up
            for i in range(len(scan)):
                scan[i] = (scan[i] + prev[i]) & 0xFF
        elif filt == 3:  # Average
            for i in range(len(scan)):
                a = scan[i - bpp] if i >= bpp else 0
                b = prev[i]
                scan[i] = (scan[i] + ((a + b) // 2)) & 0xFF
        elif filt == 4:  # Paeth
            for i in range(len(scan)):
                a = scan[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                scan[i] = (scan[i] + pr) & 0xFF
        elif filt != 0:
            raise RuntimeError(f"bad filter {filt}")
        prev = scan
        for x in range(w):
            si = x * bpp
            di = (y * w + x) * 4
            if bpp == 3:
                out[di : di + 4] = bytes([scan[si], scan[si + 1], scan[si + 2], 255])
            else:
                out[di : di + 4] = bytes(scan[si : si + 4])
    return w, h, bytes(out)


class _Surf:
    """Minimal surface with get_size + _pixels for WgpuDraw.load_texture."""

    def __init__(self, w, h, pixels):
        self._w = int(w)
        self._h = int(h)
        need = self._w * self._h * 4
        self._pixels = pixels if len(pixels) >= need else pixels + bytes(need - len(pixels))

    def get_size(self):
        return self._w, self._h


class _Render:
    """Minimal Render-like node: children + mesh attrs (duck-typed by WgpuDraw)."""

    def __init__(self, w, h, mesh=True, children=None):
        self.width = int(w)
        self.height = int(h)
        self.mesh = mesh
        self.children = list(children or [])
        self.blits = []
        self.loaded = False
        self.cached_model = None
        self.cached_texture = None
        self.texture = None
        self.textures = None
        self.vertices = None
        self.indices = None
        self.shaders = None
        self.pipeline = None
        self.uniforms = None
        self.color = None
        self.ndc = None

    def get_size(self):
        return self.width, self.height


def _arena_clear_like(mean_rgb, tol=8.0):
    # Host clear ≈ (13,13,20) from synthetic gates.
    cr, cg, cb = 13.0, 13.0, 20.0
    r, g, b = mean_rgb
    return abs(r - cr) < tol and abs(g - cg) < tol and abs(b - cb) < tol


base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
png = base / "the_question" / "game" / "gui" / "main_menu.png"
src = "main_menu.png"
try:
    if png.is_file():
        w, h, rgba = _png_rgba(png)
    else:
        raise FileNotFoundError(str(png))
except Exception as e:
    # Fallback: solid non-clear magenta full window
    w, h = 1280, 720
    rgba = bytes([200, 40, 180, 255]) * (w * h)
    src = f"solid_fallback({e})"

surf = _Surf(w, h, rgba)
# Product-like: outer container + mesh=True node with surface child (Image leaf).
mesh_node = _Render(w, h, mesh=True, children=[(surf, 0, 0, True, True)])
root = _Render(w, h, mesh=False, children=[(mesh_node, 0, 0, True, True)])

draw = WgpuDraw()
draw.init((1280, 720))
draw._ensure_pipes()

# Frame 1: full prepare + draw path
draw.draw_screen(root, flip=True)
for _ in range(2):
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

rw, rh, rt = renpy_host.read_game_rt_rgba()
mean = (0.0, 0.0, 0.0)
center = (0, 0, 0, 0)
n_pix = max(1, rw * rh) if rw and rh else 1
if rw and rh and rt and len(rt) >= rw * rh * 4:
    rs = sum(rt[i] for i in range(0, n_pix * 4, 4)) / n_pix
    gs = sum(rt[i + 1] for i in range(0, n_pix * 4, 4)) / n_pix
    bs = sum(rt[i + 2] for i in range(0, n_pix * 4, 4)) / n_pix
    mean = (rs, gs, bs)
    cx, cy = rw // 2, rh // 2
    i = (cy * rw + cx) * 4
    center = (rt[i], rt[i + 1], rt[i + 2], rt[i + 3])

# Re-prepare once and inspect cached_model on mesh node without invalidate.
draw.load_all_textures(mesh_node)
cached = getattr(mesh_node, "cached_model", None)
cached_ht = False
cached_handle = 0
if cached is not None:
    ct = getattr(cached, "texture", None)
    if isinstance(ct, HostTexture) and ct.handle > 0:
        cached_ht = True
        cached_handle = ct.handle
    elif getattr(cached, "textures", None):
        for t in cached.textures:
            if isinstance(t, HostTexture) and t.handle > 0:
                cached_ht = True
                cached_handle = t.handle
                break

clear_like = _arena_clear_like(mean)
center_clear = (
    abs(center[0] - 13) < 10
    and abs(center[1] - 13) < 10
    and abs(center[2] - 20) < 12
)
nonblank = (not clear_like) and (not center_clear) and (mean[0] + mean[1] + mean[2] > 30.0)

ok = nonblank and cached_ht
msg = (
    f"src={src} size={w}x{h} rt={rw}x{rh} "
    f"mean=({mean[0]:.1f},{mean[1]:.1f},{mean[2]:.1f}) "
    f"center={center} clear_like={clear_like} "
    f"cached_ht={cached_ht} handle={cached_handle} "
    f"nonblank={nonblank} ok={ok}"
)
out = Path("target/gate-product_image_draw.txt")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg + "\n", encoding="utf-8")
print(f"[product_image_draw] {msg}", flush=True)
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
