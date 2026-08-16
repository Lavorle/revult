"""diag dissolve dual-draw slots"""
import os, sys, traceback
from pathlib import Path
import renpy_host
from renpy.pygame.surface import Surface
from renpy.wgpu.draw import WgpuDraw, HostTexture

base = os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult"
out = Path(base) / "host" / "target" / "gate-diag_dissolve_slots.txt"
lines = []

def rec(m):
    lines.append(str(m))
    try:
        sys.__stdout__.write("[diag] %s\n" % m)
        sys.__stdout__.flush()
    except Exception:
        pass

try:
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

    w,h=1280,720
    draw=WgpuDraw(); draw.init((w,h))
    try: draw.physical_size=renpy_host.window_size()
    except Exception as e: rec("phys: %s"%e)

    def solid(rgba):
        s=Surface((w,h)); s.fill(rgba); return s

    old=solid((255,0,0,255)); new=solid((0,0,255,255))
    root=FakeRender(w,h,mesh=True)
    root.shaders=("renpy.dissolve",)
    root.uniforms={"u_renpy_dissolve":0.5}
    root.blit(old,0,0); root.blit(new,0,0)

    rec("is_dissolve=%s complete=%s" % (draw._is_dissolve_node(root), draw._dissolve_complete(root)))
    rec("surface_like old=%s new=%s" % (draw._is_surface_like(old), draw._is_surface_like(new)))

    draw.load_all_textures(root)
    rec("after load cached_model=%s loaded=%s" % (bool(root.cached_model), root.loaded))
    if root.cached_model is not None:
        cm=root.cached_model
        rec("cm.shaders=%s uniforms=%s textures=%s" % (
            getattr(cm,"shaders",None), getattr(cm,"uniforms",None),
            len(getattr(cm,"textures",None) or [])))

    kids=list(draw._iter_children(root))
    rec("kids=%d" % len(kids))
    textures=[]
    for i,(c,_,_) in enumerate(kids):
        tex=draw._child_to_texture(c)
        rec(" child%d type=%s tex=%s handle=%s size=%s" % (
            i, type(c).__name__, type(tex).__name__ if tex else None,
            getattr(tex,"handle",None), (getattr(tex,"w",None), getattr(tex,"h",None))))
        if tex is not None: textures.append(tex)
    rec("textures=%d" % len(textures))

    def mean_rt():
        rw,rh,rgba=renpy_host.read_game_rt_rgba()
        n=rw*rh; step=max(1,n//20000); rs=gs=bs=cnt=0
        for i in range(0,n,step):
            o=i*4; rs+=rgba[o]; gs+=rgba[o+1]; bs+=rgba[o+2]; cnt+=1
        return rs/cnt, gs/cnt, bs/cnt

    if len(textures)>=2:
        pipe=draw._dissolve_pipe
        m=draw._mesh_quad_ndc(0,0,w,h,(1,1,1,1),0.0,1.0,1.0,0.0)
        renpy_host.begin_frame()
        renpy_host.draw_model(pipe, int(m), textures[0].handle, textures[1].handle, [0.5]+[0.0]*15)
        renpy_host.end_frame_present()
        mr=mean_rt()
        rec("DIRECT dual mean=(%.1f,%.1f,%.1f) pipe=%s handles=%s,%s" % (mr[0],mr[1],mr[2],pipe,textures[0].handle,textures[1].handle))
        # also amount 0 and 1
        renpy_host.begin_frame()
        renpy_host.draw_model(pipe, int(m), textures[0].handle, textures[1].handle, [0.0]+[0.0]*15)
        renpy_host.end_frame_present()
        mr=mean_rt(); rec("DIRECT amount0 mean=(%.1f,%.1f,%.1f)"%mr)
        renpy_host.begin_frame()
        renpy_host.draw_model(pipe, int(m), textures[0].handle, textures[1].handle, [1.0]+[0.0]*15)
        renpy_host.end_frame_present()
        mr=mean_rt(); rec("DIRECT amount1 mean=(%.1f,%.1f,%.1f)"%mr)

    if len(textures)>=2:
        leaf=draw._make_model_leaf(w,h,textures[:2],shaders=("renpy.dissolve",),uniforms={"u_renpy_dissolve":0.5})
        rec("leaf.textures len=%s shaders=%s uniforms=%s" % (len(leaf.textures or []), leaf.shaders, leaf.uniforms))
        draw.draw_screen(leaf, flip=True)
        mr=mean_rt(); rec("LEAF mean=(%.1f,%.1f,%.1f)"%mr)

    draw.draw_screen(root, flip=True)
    mr=mean_rt(); rec("ROOT mean=(%.1f,%.1f,%.1f)"%mr)

    def ht_count(n, budget=None):
        if budget is None: budget=[120]
        if n is None or budget[0]<=0: return 0
        budget[0]-=1
        if isinstance(n, HostTexture): return 1
        if isinstance(n, int) and not isinstance(n,bool): return 1 if n>0 else 0
        total=0
        for ch,_,_ in draw._iter_children(n):
            total += ht_count(ch, budget)
            if total>8: return total
        return total
    if kids:
        rec("ht_count old=%s new=%s" % (ht_count(kids[0][0]), ht_count(kids[-1][0])))
except Exception as e:
    rec("EXC %s"%e)
    rec(traceback.format_exc())

out.write_text("\n".join(lines)+"\n")
try:
    sys.__stdout__.write("WROTE %s\n"%out); sys.__stdout__.flush()
except Exception:
    pass
