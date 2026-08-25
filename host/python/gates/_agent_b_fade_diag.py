"""Agent B diagnostic: dual-draw slots under product Solid Fade (hold=0)."""
import os
import struct
import zlib
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host

_base = Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")
out = _base / ".omc" / "artifacts" / "wgpu-vis-fade-fix-20260718" / "agent-b-diag-raw.txt"
out.parent.mkdir(parents=True, exist_ok=True)
lines = []

def log(m):
    lines.append(str(m))
    print("[diag]", m, flush=True)

VW, VH = 1280, 720

def png_rgba(path):
    data = path.read_bytes()
    pos = 8
    w = h = None
    raw = b""
    color_type = None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos+4])[0]
        ctype = data[pos+4:pos+8]
        chunk = data[pos+8:pos+8+length]
        pos += 12 + length
        if ctype == b"IHDR":
            w, h, _bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            raw += chunk
        elif ctype == b"IEND":
            break
    decomp = zlib.decompress(raw)
    bpp = 4 if color_type == 6 else 3
    stride = w * bpp + 1
    outb = bytearray(w * h * 4)
    prev = bytearray(w * bpp)
    for y in range(h):
        row = decomp[y*stride:(y+1)*stride]
        filt = row[0]
        scan = bytearray(row[1:])
        if filt == 1:
            for i in range(bpp, len(scan)):
                scan[i] = (scan[i] + scan[i-bpp]) & 0xFF
        elif filt == 2:
            for i in range(len(scan)):
                scan[i] = (scan[i] + prev[i]) & 0xFF
        elif filt == 3:
            for i in range(len(scan)):
                a = scan[i-bpp] if i >= bpp else 0
                b = prev[i]
                scan[i] = (scan[i] + ((a+b)//2)) & 0xFF
        elif filt == 4:
            for i in range(len(scan)):
                a = scan[i-bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i-bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                scan[i] = (scan[i] + pr) & 0xFF
        prev = scan
        for x in range(w):
            si = x*bpp
            di = (y*w + x)*4
            if bpp == 3:
                outb[di:di+4] = bytes([scan[si], scan[si+1], scan[si+2], 255])
            else:
                outb[di:di+4] = bytes(scan[si:si+4])
    return w, h, bytes(outb)

class _Surf:
    def __init__(self, w, h, pixels):
        self._w, self._h = int(w), int(h)
        need = self._w * self._h * 4
        raw = bytes(pixels)
        self._pixels = raw if len(raw) >= need else raw + bytes(need - len(raw))
    def get_size(self):
        return self._w, self._h

def fit(vw, vh, w, h, rgba):
    if w == vw and h == vh:
        return _Surf(vw, vh, rgba)
    buf = bytearray(bytes([0,0,0,255]) * (vw*vh))
    x0 = max(0, (vw-w)//2); y0 = max(0, (vh-h)//2)
    cw, ch = min(w,vw), min(h,vh)
    for y in range(ch):
        src = y*w*4
        dst = ((y0+y)*vw + x0)*4
        buf[dst:dst+cw*4] = rgba[src:src+cw*4]
    return _Surf(vw, vh, bytes(buf))

def mean_rgb(rgba, w, h):
    n = w*h
    rs=gs=bs=as_=0
    step = max(1, n//50000)
    count=0
    for i in range(0,n,step):
        o=i*4
        rs+=rgba[o]; gs+=rgba[o+1]; bs+=rgba[o+2]; as_+=rgba[o+3]
        count+=1
    inv=1.0/max(1,count)
    return rs*inv, gs*inv, bs*inv, as_*inv

def luma(m):
    return 0.2126*m[0]+0.7152*m[1]+0.0722*m[2]

# bootstrap
import bootstrap as boot

good, miss, err, extra = boot.stage_import_renpy()
log(f"import_renpy {good} {err}")
good, miss, err, extra = boot.stage_import_all()
log(f"import_all {good} {err}")

import renpy.display.render as render_mod
import renpy.style as style_mod
from renpy.display.render import Render

import renpy.display.displayable as disp_mod
from renpy import game
from renpy.display.displayable import Displayable
from renpy.display.imagelike import Solid
from renpy.display.transition import Dissolve, Fade
from renpy.wgpu.draw import HostTexture, WgpuDraw

render_mod.models = True
game.less_updates = False
if game.preferences is None:
    from renpy.preferences import Preferences
    game.preferences = Preferences()
game.preferences.transitions = 2

# Seed style like fade_live_st + size props for Solid
default = style_mod.Style(None, name=("default",))
default.properties.append({
    "xminimum": 0, "yminimum": 0, "xmaximum": None, "ymaximum": None,
    "xfill": False, "yfill": False, "minwidth": 0,
    "xpadding": 0, "ypadding": 0, "xmargin": 0, "ymargin": 0,
    "background": None, "color": (255, 255, 255, 255),
})
style_mod.styles[("default",)] = default
disp_mod.default_style = default
try:
    render_mod.render_ready()
except Exception as e:  # noqa: BLE001
    log(f"render_ready soft {e}")

# also try to set draw so Solid.solid_texture works
draw = WgpuDraw()
draw.init((VW, VH))
try:
    draw.physical_size = renpy_host.window_size()
except Exception:  # noqa: BLE001, S110
    pass
import renpy.display

renpy.display.draw = draw

class ProductImage(Displayable):
    def __init__(self, surf, tag="img"):
        super().__init__()
        self.surf = surf
        self.tag = tag
    def render(self, w, h, st, at):
        sw, sh = self.surf.get_size()
        rw = int(w) if w and w > 0 else sw
        rh = int(h) if h and h > 0 else sh
        rv = Render(rw, rh)
        rv.blit(self.surf, (0, 0))
        return rv
    def visit(self):
        return []

game_dir = _base / "the_question" / "game"
ow, oh, orgba = png_rgba(game_dir / "gui" / "main_menu.png")
old_surf = fit(VW, VH, ow, oh, orgba)
from PIL import Image

im = Image.open(game_dir / "images" / "bg lecturehall.jpg").convert("RGBA")
new_surf = fit(VW, VH, im.size[0], im.size[1], im.tobytes())
old = ProductImage(old_surf, "main_menu")
new = ProductImage(new_surf, "lecturehall")

# Probe Solid reverse path
solid = Solid((0, 0, 0, 255))
srv = solid.render(VW, VH, 0, 0)
log("Solid rv size={} reverse={} children={}".format(
    srv.get_size(),
    getattr(srv, "reverse", None),
    [(type(c).__name__ if not hasattr(c,'get_size') else ('surf/tex', c.get_size() if hasattr(c,'get_size') else None), xo, yo)
     for c, xo, yo, *rest in (list(srv.children) if hasattr(srv,'children') else [])]
))
# inspect children more carefully
for i, entry in enumerate(list(getattr(srv, 'children', []) or [])):
    ch = entry[0]
    log("  solid_child[%d] type=%s size=%s ht=%s reverse=%s mesh=%s" % (  # noqa: UP031
        i, type(ch).__name__,
        getattr(ch, 'get_size', lambda: None)() if hasattr(ch,'get_size') else (getattr(ch,'width',None), getattr(ch,'height',None)),
        isinstance(ch, HostTexture),
        getattr(ch, 'reverse', None),
        getattr(ch, 'mesh', None),
    ))

# Measure _child_to_texture on Solid reverse
tex = draw._child_to_texture(srv)
log("Solid _child_to_texture -> {} handle={} size={}x{}".format(
    type(tex).__name__ if tex else None,
    getattr(tex, 'handle', None),
    getattr(tex, 'width', None), getattr(tex, 'height', None),
))
# sample RTT content if possible via draw of solid alone
draw.draw_screen(srv, flip=True)
rw, rh, rgba = renpy_host.read_game_rt_rgba()
m = mean_rgb(rgba, rw, rh)
log("Solid direct draw mean_rgba=({:.1f},{:.1f},{:.1f},{:.1f})".format(*m))

# Now Fade with product Solid, hold=0 (product default)
for hold in (0.0, 0.1):
    log(f"=== Fade hold={hold} with product Solid ===")
    f = Fade(0.5, hold, 0.5, old_widget=old, new_widget=new)  # uses default Solid black
    log(f"Fade type={type(f).__name__} delay={f.delay} n_trans={len(f.transitions)}")
    for i, t in enumerate(f.transitions):
        log("  trans[%d]=%s delay=%s old=%s new=%s" % (  # noqa: UP031
            i, type(t).__name__, t.delay,
            type(getattr(t,'old_widget',None)).__name__,
            type(getattr(t,'new_widget',None)).__name__,
        ))
    # stage sts for hold=0: out 0..0.5, in 0.5..1.0
    if hold == 0.0:
        stages = [("st0",0.0),("out_mid",0.25),("out_late",0.49),("boundary",0.5),("in_early",0.51),("in_mid",0.75),("late",0.95)]
    else:
        stages = [("st0",0.0),("out_mid",0.25),("black_hold",0.55),("in_mid",0.85),("late",1.05)]
    prev_l = None
    for name, st in stages:
        rv = f.render(VW, VH, st, st)
        # unwrap MT blit to find dissolve
        kids = list(getattr(rv, 'children', []) or [])
        dissolve_node = kids[0][0] if kids else rv
        shaders = getattr(dissolve_node, 'shaders', None)
        uniforms = getattr(dissolve_node, 'uniforms', None)
        mesh = getattr(dissolve_node, 'mesh', None)
        op_c = getattr(dissolve_node, 'operation_complete', None)
        d_kids = list(getattr(dissolve_node, 'children', []) or [])
        log("stage=%s st=%.3f dissolve_shaders=%s uniforms=%s mesh=%s op_c=%s n_children=%d" % (  # noqa: UP031
            name, st, shaders, uniforms, mesh, op_c, len(d_kids)))
        # prepare and inspect slots
        try:
            draw._invalidate_prepared(rv)
        except Exception:  # noqa: BLE001, S110
            pass
        draw.load_all_textures(rv)
        cm = getattr(dissolve_node, 'cached_model', None)
        if cm is None and kids:
            # maybe prepare on dissolve directly
            draw.load_all_textures(dissolve_node)
            cm = getattr(dissolve_node, 'cached_model', None)
        slots = getattr(cm, 'textures', None) if cm else None
        nslots = len(slots) if slots else 0
        log("  cached_model=%s nslots=%d" % (cm is not None, nslots))  # noqa: UP031
        if slots:
            for si, s in enumerate(slots):
                log("  slot[%d] type=%s handle=%s %sx%s" % (  # noqa: UP031
                    si, type(s).__name__,
                    getattr(s,'handle',s),
                    getattr(s,'width',None), getattr(s,'height',None),
                ))
        # draw and mean
        draw.draw_screen(rv, flip=True)
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
        m = mean_rgb(rgba, rw, rh)
        L = luma(m)
        dL = None if prev_l is None else (L - prev_l)
        log(f"  mean=({m[0]:.1f},{m[1]:.1f},{m[2]:.1f},{m[3]:.1f}) luma={L:.1f} dL={dL}")
        prev_l = L

# Also pure Dissolve to Solid black mid
log("=== Dissolve old→Solid black complete sweep ===")
black = Solid((0,0,0,255))
d = Dissolve(0.5, old_widget=old, new_widget=black)
for st in (0.0, 0.125, 0.25, 0.375, 0.5):
    rv = d.render(VW, VH, st, st)
    draw.draw_screen(rv, flip=True)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    m = mean_rgb(rgba, rw, rh)
    u = None
    if getattr(rv,'uniforms',None):
        u = rv.uniforms.get('u_renpy_dissolve')
    # slots
    draw.load_all_textures(rv)
    cm = getattr(rv, 'cached_model', None)
    slots = getattr(cm, 'textures', None) if cm else None
    nslots = len(slots) if slots else 0
    sizes = []
    if slots:
        for s in slots:
            sizes.append("{}x{}".format(getattr(s,'width',None), getattr(s,'height',None)))
    log("st=%.3f u=%s nslots=%d sizes=%s mean=(%.1f,%.1f,%.1f,%.1f) luma=%.1f" % (  # noqa: UP031
        st, u, nslots, sizes, m[0],m[1],m[2],m[3], luma(m)))

# Dissolve Solid black → new
log("=== Dissolve Solid black→new complete sweep ===")
d2 = Dissolve(0.5, old_widget=black, new_widget=new)
for st in (0.0, 0.125, 0.25, 0.375, 0.5):
    rv = d2.render(VW, VH, st, st)
    draw.draw_screen(rv, flip=True)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    m = mean_rgb(rgba, rw, rh)
    u = rv.uniforms.get('u_renpy_dissolve') if getattr(rv,'uniforms',None) else None
    draw.load_all_textures(rv)
    cm = getattr(rv, 'cached_model', None)
    slots = getattr(cm, 'textures', None) if cm else None
    nslots = len(slots) if slots else 0
    sizes = []
    if slots:
        for s in slots:
            sizes.append("{}x{}".format(getattr(s,'width',None), getattr(s,'height',None)))
    log("st=%.3f u=%s nslots=%d sizes=%s mean=(%.1f,%.1f,%.1f,%.1f) luma=%.1f" % (  # noqa: UP031
        st, u, nslots, sizes, m[0],m[1],m[2],m[3], luma(m)))

out.write_text("\n".join(lines)+"\n")
log("WROTE "+str(out))
try:
    renpy_host.request_quit()
except Exception:  # noqa: BLE001, S110
    pass

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
