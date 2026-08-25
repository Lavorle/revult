"""Check if live dock HostTexture handles are still alive in arena."""
import os, sys, time, threading
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---
try:
    from _harness import gate_harness, parametrized_gate  # type: ignore
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore
    except ImportError:
        gate_harness = None  # type: ignore
        parametrized_gate = None  # type: ignore
# fallback

def _log(m):
    print("[alive_diag] "+m, flush=True)
    open("/tmp/hmc_alive_diag.log","a").write(m+"\n")

def main():
    open("/tmp/hmc_alive_diag.log","w").write("start\n")
    base = Path("/mnt/nvme1n1p2/revult")
    game = base/"host/playtests/HuangmeiC"
    os.environ["RENPY_HOST_BASE"]=str(base)
    os.environ["RENPY_HOST_BUILD"]="1"
    os.environ["RENPY_HOST_GAME"]=str(game)
    os.environ.setdefault("RENPY_PERFORMANCE_TEST","0")
    for k in ("RENPY_SKIP_MAIN_MENU","RENPY_SKIP_SPLASHSCREEN"):
        v=os.environ.get(k)
        if v is not None and str(v).strip().lower() in ("","0","false","no","off"):
            os.environ.pop(k,None)
    sys.path[:0]=[str(base/"host/python"), str(base/"host/python/gates")]
    import renpy_host, bootstrap as boot
    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good,_,_,err = call() if name!="set_game_dir" else call()
        # bootstrap returns (good, miss, err, extra)
        res = call() if name=="set_game_dir" else call()
        good = res[0]; err=res[2]
        _log("stage %s good=%s err=%r"%(name,good,err))
        if not good:
            renpy_host.request_quit(); return
    import renpy
    renpy.host_build=True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e:
        _log("main_host %s"%e)
    try:
        import renpy.arguments
        basedir=str(game)
        sys.argv=[sys.argv[0] if sys.argv else "renpy-host", basedir, "run"]
        if not getattr(renpy.arguments,"commands",None):
            try: renpy.arguments.register_command("run", renpy.arguments.run, True)
            except Exception: pass
        renpy.game.args = renpy.arguments.bootstrap()
    except Exception as e:
        _log("args %s"%e)
    # stubs
    try:
        import renpy.audio.renpysound_host as _rs
        import renpy.audio as _ra
        sys.modules["renpy.audio.renpysound"]=_rs; _ra.renpysound=_rs
    except Exception: pass
    try:
        import host_pygame, host_pygame.locals as _loc, host_pygame.scrap as _hs
        if not hasattr(host_pygame,"constants"): host_pygame.constants=_loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"]=_hs; sys.modules["pygame.scrap"]=_hs
        import renpy.pygame as rpg
        if not hasattr(rpg,"constants"): rpg.constants=host_pygame.constants
        try: rpg.scrap=_hs
        except Exception: pass
        try: rpg.import_as_pygame()
        except Exception: pass
    except Exception as e:
        _log("pygame %s"%e)
    try:
        import types, renpy_uguu_host as _u
        sys.modules["renpy.uguu.uguu"]=_u; sys.modules["renpy.uguu.gl"]=_u
        pkg=sys.modules.get("renpy.uguu")
        if pkg is None:
            pkg=types.ModuleType("renpy.uguu"); pkg.__path__=[]; sys.modules["renpy.uguu"]=pkg
        for n in dir(_u):
            if n.startswith("GL_") or n in ("clear_errors","get_error"):
                setattr(pkg,n,getattr(_u,n))
        pkg.uguu=_u; pkg.gl=_u; renpy.uguu=pkg
    except Exception: pass
    try:
        import renpy_ecsign_host as _e
        sys.modules["renpy.ecsign"]=_e; renpy.ecsign=_e
    except Exception: pass

    # wrap draw_model to count
    counts={"draw_model":0,"draw_model_tex":0,"draw_model_dead":0,"draw_screen":0}
    _odm = renpy_host.draw_model
    def wrap_dm(pipeline, mesh, texture=None, texture1=None, uniforms=None, texture2=None):
        counts["draw_model"]+=1
        if texture:
            counts["draw_model_tex"]+=1
            try:
                if hasattr(renpy_host,"texture_alive") and not renpy_host.texture_alive(int(texture)):
                    counts["draw_model_dead"]+=1
            except Exception:
                pass
        return _odm(pipeline, mesh, texture, texture1, uniforms, texture2)
    renpy_host.draw_model = wrap_dm

    from renpy.wgpu.draw import WgpuDraw
    _ods = WgpuDraw.draw_screen
    def wrap_ds(self, surftree, flip=True):
        counts["draw_screen"]+=1
        before=counts["draw_model"]
        r=_ods(self, surftree, flip=flip)
        after=counts["draw_model"]
        if counts["draw_screen"]<=3 or counts["draw_screen"]%60==0:
            _log("draw_screen n=%s cmds_this=%s total_dm=%s dead=%s"%(
                counts["draw_screen"], after-before, after, counts["draw_model_dead"]))
        return r
    WgpuDraw.draw_screen = wrap_ds

    state={"done":False}
    def watcher():
        ticks=0
        while ticks<200 and not state["done"]:
            time.sleep(0.2); ticks+=1
            try:
                if not getattr(renpy.store,"main_menu",False):
                    continue
                iface=getattr(renpy.display,"interface",None)
                if iface is None: continue
                if ticks<20: continue
                st=getattr(iface,"surftree",None)
                from renpy.wgpu.draw import HostTexture
                def walk(node,acc,budget=[200]):
                    if node is None or budget[0]<=0: return
                    budget[0]-=1
                    if isinstance(node, HostTexture):
                        acc.append(int(node.handle))
                        return
                    kids=getattr(node,"children",None) or []
                    for e in kids:
                        ch=e[0] if isinstance(e,(tuple,list)) else e
                        walk(ch,acc,budget)
                handles=[]
                walk(st,handles)
                alive=dead=0
                samples=[]
                for h in handles:
                    ok=renpy_host.texture_alive(h) if hasattr(renpy_host,"texture_alive") else None
                    if ok: alive+=1
                    else: dead+=1
                    if len(samples)<12:
                        samples.append((h,ok))
                w,h,rgba=renpy_host.read_game_rt_rgba()
                y0=int(h*0.88); n=sr=sg=sb=nc=0
                for y in range(y0,h,3):
                    row=y*w*4
                    for x in range(0,w,6):
                        i=row+x*4
                        r,g,b=rgba[i],rgba[i+1],rgba[i+2]
                        sr+=r; sg+=g; sb+=b; n+=1
                        if abs(r-13)>12 or abs(g-13)>12 or abs(b-20)>12: nc+=1
                mean=(sr/n,sg/n,sb/n) if n else None
                _log("SNAP alive=%s dead=%s samples=%s dock_mean=%s nonclear=%.3f counts=%s"%(
                    alive,dead,samples,mean,(nc/n if n else 0),counts))
                # Force draw live surftree
                draw=renpy.display.draw
                c0=counts["draw_model"]
                d0=counts["draw_model_dead"]
                draw.draw_screen(st, flip=True)
                c1=counts["draw_model"]; d1=counts["draw_model_dead"]
                w2,h2,rgba2=renpy_host.read_game_rt_rgba()
                y0=int(h2*0.88); n=sr=sg=sb=nc=0
                for y in range(y0,h2,3):
                    row=y*w2*4
                    for x in range(0,w2,6):
                        i=row+x*4
                        r,g,b=rgba2[i],rgba2[i+1],rgba2[i+2]
                        sr+=r; sg+=g; sb+=b; n+=1
                        if abs(r-13)>12 or abs(g-13)>12 or abs(b-20)>12: nc+=1
                _log("AFTER force live draw cmds=%s dead_delta=%s dock_mean=%s nonclear=%.3f"%(
                    c1-c0, d1-d0, (sr/n,sg/n,sb/n) if n else None, nc/n if n else 0))
                # recheck alive after force
                handles2=[]; walk(st,handles2)
                a2=sum(1 for h in handles2 if renpy_host.texture_alive(h))
                d2=len(handles2)-a2
                _log("AFTER force handles alive=%s dead=%s"%(a2,d2))
                # now force rebuild
                import interact_helpers as ih
                root=ih._rebuild_product_root(iface)
                surftree=renpy.display.render.render_screen(root,1920,1080)
                c0=counts["draw_model"]
                draw.draw_screen(surftree, flip=True)
                c1=counts["draw_model"]
                w3,h3,rgba3=renpy_host.read_game_rt_rgba()
                y0=int(h3*0.88); n=sr=sg=sb=nc=0
                for y in range(y0,h3,3):
                    row=y*w3*4
                    for x in range(0,w3,6):
                        i=row+x*4
                        r,g,b=rgba3[i],rgba3[i+1],rgba3[i+2]
                        sr+=r; sg+=g; sb+=b; n+=1
                        if abs(r-13)>12 or abs(g-13)>12 or abs(b-20)>12: nc+=1
                _log("AFTER rebuild draw cmds=%s dock_mean=%s nonclear=%.3f"%(
                    c1-c0,(sr/n,sg/n,sb/n) if n else None, nc/n if n else 0))
                state["done"]=True
                renpy_host.request_quit()
                return
            except Exception as e:
                import traceback
                _log("watch err %s\n%s"%(e,traceback.format_exc()[-600:]))
        renpy_host.request_quit()
    threading.Thread(target=watcher,daemon=True).start()
    try:
        import renpy.main as m
        m.main()
    except Exception as e:
        _log("main exit %s:%s"%(type(e).__name__,e))
    _log("done counts=%s"%counts)

if __name__=="__main__" or True:
    try: main()
    except Exception as e:
        import traceback
        _log("top %s\n%s"%(e,traceback.format_exc()[-800:]))

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
