"""diag dissolve dual-draw slots"""
import os
import sys
import traceback
from pathlib import Path

import renpy_host
from renpy.pygame.surface import Surface

from renpy.wgpu.draw import HostTexture, WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---


base = os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult"
out = Path(base) / "host" / "target" / "gate-diag_dissolve_slots.txt"
lines = []

def rec(m):
    lines.append(str(m))
    try:
        sys.__stdout__.write(f"[diag] {m}\n")
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
    except Exception as e: rec(f"phys: {e}")

    def solid(rgba):
        s=Surface((w,h)); s.fill(rgba); return s

    old=solid((255,0,0,255)); new=solid((0,0,255,255))
    root=FakeRender(w,h,mesh=True)
    root.shaders=("renpy.dissolve",)
    root.uniforms={"u_renpy_dissolve":0.5}
    root.blit(old,0,0); root.blit(new,0,0)

    rec(f"is_dissolve={draw._is_dissolve_node(root)} complete={draw._dissolve_complete(root)}")
    rec(f"surface_like old={draw._is_surface_like(old)} new={draw._is_surface_like(new)}")

    draw.load_all_textures(root)
    rec(f"after load cached_model={bool(root.cached_model)} loaded={root.loaded}")
    if root.cached_model is not None:
        cm=root.cached_model
        rec("cm.shaders={} uniforms={} textures={}".format(
            getattr(cm,"shaders",None), getattr(cm,"uniforms",None),
            len(getattr(cm,"textures",None) or [])))

    kids=list(draw._iter_children(root))
    rec("kids=%d" % len(kids))  # noqa: UP031
    textures=[]
    for i,(c,_,_) in enumerate(kids):
        tex=draw._child_to_texture(c)
        rec(" child%d type=%s tex=%s handle=%s size=%s" % (  # noqa: UP031
            i, type(c).__name__, type(tex).__name__ if tex else None,
            getattr(tex,"handle",None), (getattr(tex,"w",None), getattr(tex,"h",None))))
        if tex is not None: textures.append(tex)
    rec("textures=%d" % len(textures))  # noqa: UP031

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
        rec(f"DIRECT dual mean=({mr[0]:.1f},{mr[1]:.1f},{mr[2]:.1f}) pipe={pipe} handles={textures[0].handle},{textures[1].handle}")
        # also amount 0 and 1
        renpy_host.begin_frame()
        renpy_host.draw_model(pipe, int(m), textures[0].handle, textures[1].handle, [0.0]+[0.0]*15)
        renpy_host.end_frame_present()
        mr=mean_rt(); rec("DIRECT amount0 mean=({:.1f},{:.1f},{:.1f})".format(*mr))
        renpy_host.begin_frame()
        renpy_host.draw_model(pipe, int(m), textures[0].handle, textures[1].handle, [1.0]+[0.0]*15)
        renpy_host.end_frame_present()
        mr=mean_rt(); rec("DIRECT amount1 mean=({:.1f},{:.1f},{:.1f})".format(*mr))

    if len(textures)>=2:
        leaf=draw._make_model_leaf(w,h,textures[:2],shaders=("renpy.dissolve",),uniforms={"u_renpy_dissolve":0.5})
        rec(f"leaf.textures len={len(leaf.textures or [])} shaders={leaf.shaders} uniforms={leaf.uniforms}")
        draw.draw_screen(leaf, flip=True)
        mr=mean_rt(); rec("LEAF mean=({:.1f},{:.1f},{:.1f})".format(*mr))

    draw.draw_screen(root, flip=True)
    mr=mean_rt(); rec("ROOT mean=({:.1f},{:.1f},{:.1f})".format(*mr))

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
        rec(f"ht_count old={ht_count(kids[0][0])} new={ht_count(kids[-1][0])}")
except Exception as e:
    rec(f"EXC {e}")
    rec(traceback.format_exc())

out.write_text("\n".join(lines)+"\n")
try:
    sys.__stdout__.write(f"WROTE {out}\n"); sys.__stdout__.flush()
except Exception:
    pass

# ----------------------------------------------------------------------
# HARNESS MIGRATION (thin wrapper, original logic preserved above)
# ----------------------------------------------------------------------
# Migration path for diag_dissolve_slots:
#   1. Keep all helpers/classes above untouched (header license preserved).
#   2. Extract the body of main()/run()/probe into _harness_run_one(case):
#        def _harness_run_one(case):
#            # case: dict with {"amount": 0.5, "kind": "dual-draw"}
#            # ... reuse helpers above (WgpuDraw / FakeRender / _mean_rgb ...)
#            # w, h, rgba = renpy_host.read_game_rt_rgba()
#            # return w, h, rgba   # or (ok, msg)
#   3. Define golden_compare delegating to golden_mae or custom mean check:
#        def _harness_golden_compare(w, h, rgba):
#            from golden_mae import compare_or_bootstrap
#            return compare_or_bootstrap("diag_dissolve_slots", w, h, rgba)
#            # or custom: mr/mg/mb = _mean_rgb(rgba,w,h); return (ok,msg)
#   4. Wire via harness (opt-in via RENPY_HOST_HARNESS=1 to keep default run unchanged):
#        if parametrized_gate is not None:
#            @parametrized_gate("diag_dissolve_slots", [{"amount": 0.5, "kind": "dual-draw"}])
#            def _parametrized_case(case):
#                w, h, rgba = _harness_run_one(case)
#                return _harness_golden_compare(w, h, rgba)
#        def _harness_main():
#            import os as _os
#            if gate_harness is not None and _os.environ.get("RENPY_HOST_HARNESS") == "1":
#                cases = [{"amount": 0.5, "kind": "dual-draw"}]
#                ok = gate_harness("diag_dissolve_slots", cases, _harness_run_one, _harness_golden_compare)
#                raise SystemExit(0 if ok else 1)
#            else:
#                main()  # or run() — original path
#        if __name__ == "__main__":
#            _harness_main()
#
# Notes: 6th dissolve-family member (only 5 dissolve_* exist); diag of dual-draw slots via _is_dissolve / _child_to_texture / _make_model_leaf. Parameterize amount and leaf vs root path.
# Original code above is untouched; this block is documentation + ready-to-enable
# wrapper ensuring `python -m py_compile` stays green.
# To fully migrate, move the `main()`/`run()` call into `_harness_main` and
# gate on RENPY_HOST_HARNESS as shown.

