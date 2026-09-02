"""Simulate main_menu overlay: full 1280x720 tex, opaque left 280, reverse oversample."""
import os
from pathlib import Path

import renpy_host
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import HostTexture, WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---


base = Path(os.environ.get("RENPY_HOST_BASE", "."))
out = base / "host" / "target" / "gate-overlay_fulltex_probe.txt"
VW, VH = 1280, 720
lines = []
ok = True

class Mat2:
    def __init__(self, xdx=1.0, ydy=1.0):
        self.xdx = float(xdx); self.ydy = float(ydy); self.xdy = 0.0; self.ydx = 0.0

class R:
    def __init__(self, w, h, rev=None):
        self.width = int(w); self.height = int(h)
        self.children = []; self.mesh = None
        self.reverse = rev; self.forward = None
        self.cached_texture = None; self.cached_model = None
        self.loaded = False; self.blits = None; self.ndc = None
        self.texture = None; self.textures = None; self.shaders = None
        self.uniforms = None; self.color = None; self.vertices = None; self.indices = None
        self.pipeline = None
    def blit(self, c, xo=0, yo=0):
        self.children.append((c, float(xo), float(yo), False, True))
    def get_size(self):
        return (self.width, self.height)

def left_mean(rgba, w, h, x0=20, x1=100):
    rs=gs=bs=n=0
    for y in range(h//4, 3*h//4, 4):
        for x in range(x0, min(x1, w), 2):
            i=(y*w+x)*4
            rs+=rgba[i]; gs+=rgba[i+1]; bs+=rgba[i+2]; n+=1
    return (rs/n, gs/n, bs/n) if n else (0,0,0)

def scenic_mean(rgba, w, h):
    # center of screen
    return left_mean(rgba, w, h, w//2-40, w//2+40)

try:
    draw = WgpuDraw()
    draw.init((VW, VH))
    # scenic green bg
    bg = Surface((VW, VH)); bg.fill((40, 120, 40, 255))
    # overlay: left 280 dark semi, rest transparent — like main_menu.png
    ov = Surface((VW, VH)); ov.fill((0, 0, 0, 0))
    for y in range(VH):
        for x in range(280):
            ov.set_at((x, y), (0, 0, 0, 204))
    tex = draw.load_texture(ov)
    assert isinstance(tex, HostTexture)

    # Case A: identity reverse, full tex as child of 1280 render (product image cache)
    root = R(VW, VH)
    root.blit(bg, 0, 0)
    img = R(VW, VH, Mat2(1, 1))  # identity
    img.blit(tex, 0, 0)
    root.blit(img, 0, 0)
    draw.draw_screen(root, flip=True)
    w,h,rgba = renpy_host.read_game_rt_rgba()
    mL = left_mean(rgba,w,h); mC = scenic_mean(rgba,w,h)
    lines.append(f"A identity left={tuple(round(x,1) for x in mL)} center={tuple(round(x,1) for x in mC)}")
    # left should be darker than center
    if sum(mL)/3 >= sum(mC)/3 - 10:
        ok=False; lines.append("FAIL A left not darker")
    else:
        lines.append("PASS A")

    # Case B: reverse 1/1.5, full tex (oversample-style), parent virtual full canvas
    # Texture still 1280 (not re-uploaded) — bug case that shrank overlay before
    root2 = R(VW, VH)
    root2.blit(bg, 0, 0)
    img2 = R(VW, VH, Mat2(1/1.5, 1/1.5))
    img2.blit(tex, 0, 0)  # full 1280 tex, reverse scale-down
    root2.blit(img2, 0, 0)
    dest = draw._reverse_dest_size(img2, tex, (VW, VH))
    lines.append("B dest=%s (expect full %dx%d)" % (dest, VW, VH))
    if dest != (VW, VH):
        ok=False; lines.append("FAIL B dest")
    draw.draw_screen(root2, flip=True)
    w,h,rgba = renpy_host.read_game_rt_rgba()
    mL = left_mean(rgba,w,h); mC = scenic_mean(rgba,w,h)
    lines.append(f"B reverse left={tuple(round(x,1) for x in mL)} center={tuple(round(x,1) for x in mC)}")
    if sum(mL)/3 >= sum(mC)/3 - 10:
        ok=False; lines.append("FAIL B left not darker (overlay incomplete)")
    else:
        lines.append("PASS B")

    # Case C: typewriter partial still partial
    # reuse full white line
    s = Surface((600, 72)); s.fill((255,255,255,255))
    full = draw.load_texture(s)
    parent = R(400, 48, Mat2(1/1.5, 1/1.5))
    sub = full.subsurface((0,0,150,72))
    parent.blit(sub, 0, 0)
    destc = draw._reverse_dest_size(parent, sub, (400, 48))
    lines.append(f"C tw dest={destc} expect ~100x48")
    if abs(destc[0]-100)>2:
        ok=False; lines.append("FAIL C")
    else:
        lines.append("PASS C")

except Exception as e:
    ok=False
    import traceback
    lines.append(f"EXCEPTION {e!r}")
    lines.append(traceback.format_exc())

body = (f"ok={ok}\n") + "\n".join(lines) + "\n"
out.write_text(body)
print(body)
renpy_host.request_quit()
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

