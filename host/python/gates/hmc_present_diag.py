"""hmc_present_diag v12 — live RT dock mean while product draws after main_menu."""
import os
import sys
import threading
import time
import traceback
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(msg):
    try:
        sys.__stdout__.write(f"[present_diag] {msg}\n"); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_present_diag.log","a").write(msg+"\n")

def _request_quit():
    try:
        import renpy_host; renpy_host.request_quit()
    except Exception:
        pass

def _stubs():
    import types
    try:
        import renpy.audio as a
        import renpy.audio.renpysound_host as h
        sys.modules["renpy.audio.renpysound"]=h; a.renpysound=h
    except Exception as e: _log(f"rs {e}")
    try:
        import host_pygame
        import host_pygame.locals as L
        import host_pygame.scrap as S
        if not hasattr(host_pygame,"constants"): host_pygame.constants=L
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules.setdefault("pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"]=S; sys.modules["pygame.scrap"]=S
        import renpy.pygame as rpg
        if not hasattr(rpg,"constants"): rpg.constants=host_pygame.constants
        try: rpg.scrap=S
        except Exception: pass
        try: rpg.import_as_pygame()
        except Exception: pass
    except Exception as e: _log(f"pg {e}")
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu.uguu"]=u; sys.modules["renpy.uguu.gl"]=u
        pkg=sys.modules.get("renpy.uguu")
        if pkg is None:
            pkg=types.ModuleType("renpy.uguu"); pkg.__path__=[]; sys.modules["renpy.uguu"]=pkg
        for n in dir(u):
            if n.startswith("GL_") or n in ("clear_errors","get_error"): setattr(pkg,n,getattr(u,n))
        pkg.uguu = u; pkg.gl = u
        try:
            import renpy; renpy.uguu=pkg
        except Exception: pass
    except Exception as e: _log(f"u {e}")
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"]=e
        try:
            import renpy as r; r.ecsign = e
        except Exception: pass
    except Exception as e: _log(f"e {e}")

def _dock_mean():
    import renpy_host
    w,h,rgba=renpy_host.read_game_rt_rgba()
    if not w or not h or not rgba: return None
    y0=int(h*0.88); n=sr=sg=sb=0
    for y in range(y0,h,3):
        row=y*w*4
        for x in range(0,w,6):
            i=row+x*4; sr+=rgba[i]; sg+=rgba[i+1]; sb+=rgba[i+2]; n+=1
    # also full-frame nonclear
    nn=0; tot=0
    for y in range(0,h,4):
        row=y*w*4
        for x in range(0,w,4):
            i=row+x*4
            if rgba[i]>20 or rgba[i+1]>20 or rgba[i+2]>25: nn+=1
            tot+=1
    return (sr/n,sg/n,sb/n, nn/tot if tot else 0, w, h) if n else None

def _find_dissolve(draw, node, depth=0):
    if node is None or depth>12: return None
    try:
        from renpy.wgpu.draw import HostTexture
        if isinstance(node, HostTexture): return None
    except Exception:
        pass
    mesh=getattr(node,"mesh",None)
    shaders=list(getattr(node,"shaders",None) or ())
    op=getattr(node,"operation",None)
    if mesh and (op==1 or any(s in ("renpy.dissolve","dissolve") for s in shaders)):
        uniforms=getattr(node,"uniforms",None) or {}
        u=uniforms.get("u_renpy_dissolve") if isinstance(uniforms,dict) else None
        return {"op":op,"op_c":getattr(node,"operation_complete",None),"u":u,"n_kids":len(list(draw._iter_children(node)))}
    try:
        kids=list(draw._iter_children(node))
    except Exception:
        kids=[]
    for c,_,_ in kids:
        r=_find_dissolve(draw,c,depth+1)
        if r: return r
    return None

def run():
    open("/tmp/hmc_present_diag.log","w").write("start v12\n")
    base=_base()
    game=os.environ.get("RENPY_HOST_GAME") or str(base/"host/playtests/HuangmeiC")
    os.environ.update({"RENPY_HOST_BASE":str(base),"RENPY_HOST_BUILD":"1","RENPY_HOST_GAME":game})
    os.environ.setdefault("RENPY_PERFORMANCE_TEST","0")
    for k in ("RENPY_SKIP_MAIN_MENU","RENPY_SKIP_SPLASHSCREEN"):
        v=os.environ.get(k)
        if v is not None and str(v).strip().lower() in ("","0","false","no","off","n"): os.environ.pop(k,None)
    for p in (str(base/"host/python/gates"), str(base/"host/python")):
        if p not in sys.path: sys.path.insert(0,p)
    import bootstrap as boot
    for name,call in (("import_renpy",boot.stage_import_renpy),("import_all",boot.stage_import_all),("set_game_dir",lambda: boot.stage_set_game_dir(base))):
        good,_miss,err,_extra=call(); _log(f"stage {name} good={good} err={err!r}")
        if not good: _request_quit(); return
    import renpy
    renpy.host_build=True
    try: renpy.config.performance_test=False
    except Exception: pass
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e: _log(f"mh {e}")
    try:
        import renpy.arguments
        basedir=getattr(renpy.config,"basedir",None) or game
        sys.argv=[sys.argv[0] if sys.argv else "renpy-host", basedir, "run"]
        if not getattr(renpy.arguments,"commands",None):
            try: renpy.arguments.register_command("run", renpy.arguments.run, True)
            except Exception: pass
        renpy.game.args=renpy.arguments.bootstrap()
    except Exception as e: _log(f"args {e}")
    _stubs()

    # count product draw_model of dock-ish sizes via texture touch isn't available;
    # just sample mean after main_menu for several seconds without force.
    done={"v":False}
    def watcher():
        ticks=0
        saw_mm_at=None
        while ticks<200 and not done["v"]:
            time.sleep(0.2); ticks+=1
            try:
                mm=getattr(renpy.store,"main_menu",False)
                if not mm:
                    if ticks%15==0: _log(f"wait mm t={ticks}")
                    continue
                if saw_mm_at is None:
                    saw_mm_at=ticks
                    _log(f"main_menu at t={ticks}")
                iface=getattr(renpy.display,"interface",None)
                draw=renpy.display.draw
                st=getattr(iface,"surftree",None) if iface else None
                mean=_dock_mean()
                diss=_find_dissolve(draw, st) if (draw and st) else None
                ot=list((getattr(iface,"ongoing_transition",{}) or {}).keys()) if iface else []
                dict(getattr(iface,"transition_time",{}) or {}) if iface else {}
                ft=getattr(iface,"frame_time",None) if iface else None
                age=ticks-saw_mm_at
                _log(f"age={age*0.2:.1f}s mean={mean} diss={diss} ot={ot} ft={ft}")
                # after 3s of main_menu, force live present once and rebuild once
                if age==15:
                    if st is not None and draw is not None:
                        draw.draw_screen(st, flip=True)
                    _log("force_live mean={} diss={}".format(_dock_mean(), _find_dissolve(draw, getattr(iface,"surftree",None))))
                if age==18:
                    try:
                        import interact_helpers as ih
                        ready,_why,iface2=ih.interface_ready()
                        root=ih._rebuild_product_root(iface2) if ready else None
                        ww=int(getattr(renpy.config,"screen_width",1920) or 1920)
                        hh=int(getattr(renpy.config,"screen_height",1080) or 1080)
                        st2=renpy.display.render.render_screen(root, ww, hh)
                        if hasattr(draw,"load_all_textures"): draw.load_all_textures(st2)
                        draw.draw_screen(st2, flip=True)
                        _log(f"rebuild mean={_dock_mean()}")
                    except Exception as e:
                        _log(f"reb fail {e}\n{traceback.format_exc()[-300:]}")
                if age>=22:
                    done["v"]=True
                    _request_quit()
                    return
            except Exception as e:
                _log(f"watch {e}\n{traceback.format_exc()[-400:]}")
        _request_quit()
    threading.Thread(target=watcher,daemon=True).start()
    _log("waiting")
    try:
        import renpy.main as m; m.main()
    except Exception as e:
        _log(f"main {type(e).__name__}: {e}")
    _log("done")

run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
