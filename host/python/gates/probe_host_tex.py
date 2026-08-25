import os, sys, types
from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

# Full renpy.config stub BEFORE any draw that might log
import renpy
class _Cfg:
    log_to_stdout = False
    log_enable = False
    log_basedir = None
    developer = False
    debug = False
    profile = False
    log = None
renpy.config = _Cfg()  # type: ignore
# also stub renpy.log.write to print
class _Log:
    def write(self, *a, **k):
        print("RENLOG", a, k, flush=True)
    def exception(self, *a, **k):
        print("RENLOGEXC", a, k, flush=True)
    def open(self):
        return False
renpy.log = _Log()  # type: ignore

import renpy_host
from renpy.wgpu.draw import WgpuDraw, HostTexture
from host_pygame import image as himg

base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
surf = himg.load(str(base / "the_question/game/gui/main_menu.png"))
print("surf", surf.get_size(), flush=True)
d = WgpuDraw()
print("init", d.init((1280,720)), flush=True)
ht = d.load_texture(surf)
print("ht", ht.handle, ht.w, ht.h, flush=True)

def mean():
    w,h,rgba = renpy_host.read_game_rt_rgba()
    n=w*h
    mr=sum(rgba[i] for i in range(0,n*4,4))/n
    mg=sum(rgba[i+1] for i in range(0,n*4,4))/n
    mb=sum(rgba[i+2] for i in range(0,n*4,4))/n
    i=(h//2*w+w//2)*4
    thr=30; non=0
    for j in range(0,n*4,64):
        if abs(rgba[j]-13)>thr or abs(rgba[j+1]-13)>thr or abs(rgba[j+2]-20)>thr:
            non += 1
    return f"mean=({mr:.1f},{mg:.1f},{mb:.1f}) center={tuple(rgba[i:i+4])} nonclear={non}"

renpy_host.begin_frame()
d._draw_model_like(ht, 0.0, 0.0)
renpy_host.end_frame_present()
print("manual", mean(), flush=True)

d.draw_screen(ht, flip=True)
print("bareHT", mean(), flush=True)

class FR:
    def __init__(self,w,h):
        self.width=w; self.height=h; self.children=[]; self.mesh=None
        self.texture=None; self.textures=None; self.cached_model=None; self.blits=None
        self.loaded=False; self.cached_texture=None
    def get_size(self): return (self.width,self.height)

root2=FR(1280,720); root2.mesh=True; root2.children.append((ht,0.0,0.0,False,True))
d.load_all_textures(root2)
print("cached_model", root2.cached_model, getattr(root2.cached_model,'texture',None), getattr(root2.cached_model,'width',None), flush=True)
d.draw_screen(root2, flip=True)
print("mesh+HT", mean(), flush=True)

inner=FR(1280,720); inner.children.append((ht,0,0,False,True)); inner.cached_texture=ht
outer=FR(1280,720); outer.mesh=True; outer.children.append((inner,0,0,False,True))
d.draw_screen(outer, flip=True)
print("nested", mean(), flush=True)

surf2 = himg.load(str(base / "the_question/game/gui/main_menu.png"))
root3=FR(1280,720); root3.children.append((surf2,0,0,False,True))
d.draw_screen(root3, flip=True)
print("surface_child", mean(), flush=True)

print("ok=True", flush=True)
(base/"host"/"target"/"gate-probe-host-tex.txt").write_text("ok=True\n")

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
