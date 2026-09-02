import os
import sys
import traceback
from pathlib import Path

import renpy_host
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---


base = os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult"
out = Path(base) / "host" / "target" / "gate-diag_dissolve_path.txt"
lines=[]
def rec(m):
    lines.append(str(m))
    try:
        sys.__stdout__.write(f"[dpath] {m}\n"); sys.__stdout__.flush()
    except Exception:
        pass

class FakeRender:
    def __init__(self, width=1280, height=720, mesh=False):
        self.width=int(width); self.height=int(height)
        self.children=[]; self.mesh=mesh
        self.texture=None; self.textures=None; self.color=None
        self.shaders=None; self.pipeline=None; self.vertices=None; self.indices=None
        self.cached_model=None; self.cached_texture=None; self.blits=None; self.ndc=None
        self.uniforms=None; self.loaded=False
    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True)); return self
    def get_size(self):
        return (self.width, self.height)

try:
    w,h=1280,720
    draw=WgpuDraw(); draw.init((w,h))
    try: draw.physical_size=renpy_host.window_size()
    except Exception: pass

    # monkeypatch key methods
    hits={"mid":0,"extreme0":0,"extreme1":0,"leaf_draw":0,"tex_slots":0,"walk_both":0,"is_diss":0,"mesh_branch":0,"surface_draw":0}

    _orig_is=draw._is_dissolve_node
    def _is(n):
        r=_orig_is(n)
        if r: hits["is_diss"]+=1
        return r
    draw._is_dissolve_node=_is

    _orig_dml=draw._draw_model_like
    def _dml(node, ox=0.0, oy=0.0):
        shaders=getattr(node,"shaders",None)
        texs=getattr(node,"textures",None)
        hits["leaf_draw"]+=1
        rec("draw_model_like shaders={} texs={} uniforms={}".format(shaders, len(texs) if texs else None, getattr(node,"uniforms",None)))
        return _orig_dml(node, ox, oy)
    draw._draw_model_like=_dml

    _orig_ctt=draw._child_to_texture
    def _ctt(child):
        t=_orig_ctt(child)
        rec("child_to_texture type={} -> {} handle={}".format(type(child).__name__, type(t).__name__ if t else None, getattr(t,"handle",None)))
        return t
    draw._child_to_texture=_ctt

    # wrap mid logic by patching _draw_node_inner completely is hard; instead patch complete extremes via complete
    def solid(rgba):
        s=Surface((w,h)); s.fill(rgba); return s
    old=solid((255,0,0,255)); new=solid((0,0,255,255))
    root=FakeRender(w,h,mesh=True)
    root.shaders=("renpy.dissolve",)
    root.uniforms={"u_renpy_dissolve":0.5}
    root.blit(old,0,0); root.blit(new,0,0)

    rec("pre is_diss=%s complete=%s mesh=%s kids=%d"%(draw._is_dissolve_node(root), draw._dissolve_complete(root), root.mesh, len(root.children)))
    draw.draw_screen(root, flip=True)
    rw,rh,rgba=renpy_host.read_game_rt_rgba()
    n=rw*rh; step=max(1,n//20000); rs=gs=bs=cnt=0
    for i in range(0,n,step):
        o=i*4; rs+=rgba[o]; gs+=rgba[o+1]; bs+=rgba[o+2]; cnt+=1
    rec(f"ROOT mean=({rs/cnt:.1f},{gs/cnt:.1f},{bs/cnt:.1f}) hits={hits}")
except Exception as e:
    rec(f"EXC {e}"); rec(traceback.format_exc())
out.write_text("\n".join(lines)+"\n")
try:
    sys.__stdout__.write(f"WROTE {out}\n"); sys.__stdout__.flush()
except Exception:
    pass

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

