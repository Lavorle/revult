
"""hmc_flowchart_diag — product ShowMenu flowchart RT + surftree dump."""
import os, sys, threading, time, traceback
from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write("[flow_diag] %s\n"%m); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_flowchart_diag.log","a").write(m+"\n")

def _quit():
    try:
        import renpy_host; renpy_host.request_quit()
    except Exception:
        pass

def _stubs():
    import types
    try:
        import renpy.audio.renpysound_host as h, renpy.audio as a
        sys.modules["renpy.audio.renpysound"]=h; a.renpysound=h
    except Exception as e: _log("rs %s"%e)
    try:
        import host_pygame, host_pygame.locals as L, host_pygame.scrap as S
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
    except Exception as e: _log("pg %s"%e)
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu.uguu"]=u; sys.modules["renpy.uguu.gl"]=u
        pkg=sys.modules.get("renpy.uguu")
        if pkg is None:
            pkg=types.ModuleType("renpy.uguu"); pkg.__path__=[]; sys.modules["renpy.uguu"]=pkg
        for n in dir(u):
            if n.startswith("GL_") or n in ("clear_errors","get_error"): setattr(pkg,n,getattr(u,n))
        setattr(pkg,"uguu",u); setattr(pkg,"gl",u)
        try:
            import renpy; renpy.uguu=pkg
        except Exception: pass
    except Exception as e: _log("u %s"%e)
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"]=e
        try:
            import renpy as r; setattr(r,"ecsign",e)
        except Exception: pass
    except Exception as e: _log("e %s"%e)

def _walk(n, depth=0, budget=[400], acc=None, ox=0.0, oy=0.0):
    if acc is None:
        acc={"nodes":0,"ht":0,"ints":0,"mesh":0,"kids_max":0,"leaves":[],"errors":[]}
    if n is None or budget[0]<=0 or depth>40:
        return acc
    budget[0]-=1
    acc["nodes"]+=1
    try:
        from renpy.wgpu.draw import HostTexture
        if isinstance(n, HostTexture):
            acc["ht"]+=1
            if len(acc["leaves"])<30:
                acc["leaves"].append(("HT",ox,oy,n.w,n.h,n.handle))
            return acc
    except Exception:
        pass
    if isinstance(n,int) and not isinstance(n,bool):
        if n>0:
            acc["ints"]+=1
            if len(acc["leaves"])<30:
                acc["leaves"].append(("INT",ox,oy,0,0,n))
        return acc
    mesh=getattr(n,"mesh",None)
    if mesh is not None:
        acc["mesh"]+=1
    tex=getattr(n,"texture",None)
    if tex is not None:
        try:
            from renpy.wgpu.draw import HostTexture
            if isinstance(tex, HostTexture):
                acc["ht"]+=1
                if len(acc["leaves"])<30:
                    acc["leaves"].append(("TEXHT",ox,oy,tex.w,tex.h,tex.handle))
            elif isinstance(tex,int) and tex>0:
                acc["ints"]+=1
                if len(acc["leaves"])<30:
                    acc["leaves"].append(("TEXINT",ox,oy,0,0,tex))
        except Exception:
            pass
    kids=list(getattr(n,"children",None) or [])
    acc["kids_max"]=max(acc["kids_max"], len(kids))
    for e in kids[:32]:
        try:
            if isinstance(e,(tuple,list)):
                ch=e[0]; cx=float(e[1]) if len(e)>1 else 0.0; cy=float(e[2]) if len(e)>2 else 0.0
            else:
                ch=e; cx=cy=0.0
            _walk(ch, depth+1, budget, acc, ox+cx, oy+cy)
        except Exception as ex:
            acc["errors"].append(str(ex)[:80])
    return acc

def _sample():
    import renpy_host
    w,h,rt=renpy_host.read_game_rt_rgba()
    if not w or not rt:
        return {"ok":False,"err":"empty"}
    rs=gs=bs=n=pure=0
    stepx=max(1,w//32); stepy=max(1,h//18)
    samples=[]
    for y in range(stepy//2,h,stepy):
        for x in range(stepx//2,w,stepx):
            o=(y*w+x)*4
            r,g,b,a=rt[o],rt[o+1],rt[o+2],rt[o+3]
            samples.append((r,g,b)); rs+=r; gs+=g; bs+=b; n+=1
            if r+g+b<20: pure+=1
    mean=(rs/n,gs/n,bs/n)
    mr,mg,mb=mean
    var=sum((s[0]-mr)**2+(s[1]-mg)**2+(s[2]-mb)**2 for s in samples)/n
    return {"ok": var>=5 and pure/n<0.85, "mean":mean, "var":var, "pure":pure/n, "w":w,"h":h}

def _redraw():
    import renpy, interact_helpers as ih
    ready,why,iface=ih.interface_ready()
    if not ready: return {"err":why}
    root=ih._rebuild_product_root(iface)
    if root is None: return {"err":"no_root"}
    w=int(getattr(renpy.config,"screen_width",1920) or 1920)
    h=int(getattr(renpy.config,"screen_height",1080) or 1080)
    st=renpy.display.render.render_screen(root,w,h)
    draw=renpy.display.draw
    try:
        draw.load_all_textures(st)
    except Exception as e:
        _log("prepare_err %s"%e)
    draw.draw_screen(st, flip=True)
    try: iface.surftree=st
    except Exception: pass
    return {"st":st,"root":type(root).__name__}

def run():
    open("/tmp/hmc_flowchart_diag.log","w").write("start\n")
    base=_base()
    game=os.environ.get("RENPY_HOST_GAME") or str(base/"host/playtests/HuangmeiC")
    os.environ.update({"RENPY_HOST_BASE":str(base),"RENPY_HOST_BUILD":"1","RENPY_HOST_GAME":game})
    os.environ.setdefault("RENPY_PERFORMANCE_TEST","0")
    for k in ("RENPY_SKIP_MAIN_MENU","RENPY_SKIP_SPLASHSCREEN"):
        v=os.environ.get(k)
        if v is not None and str(v).strip().lower() in ("","0","false","no","off","n"):
            os.environ.pop(k,None)
    for p in (str(base/"host/python/gates"), str(base/"host/python")):
        if p not in sys.path: sys.path.insert(0,p)
    import renpy_host, bootstrap as boot
    for name,call in (("import_renpy",boot.stage_import_renpy),("import_all",boot.stage_import_all),("set_game_dir",lambda: boot.stage_set_game_dir(base))):
        good,miss,err,extra=call(); _log("stage %s good=%s err=%r"%(name,good,err))
        if not good: _quit(); return
    import renpy
    renpy.host_build=True
    try: renpy.config.performance_test=False
    except Exception: pass
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e: _log("mh %s"%e)
    try:
        import renpy.arguments
        basedir=getattr(renpy.config,"basedir",None) or game
        sys.argv=[sys.argv[0] if sys.argv else "renpy-host", basedir, "run"]
        if not getattr(renpy.arguments,"commands",None):
            try: renpy.arguments.register_command("run", renpy.arguments.run, True)
            except Exception: pass
        renpy.game.args=renpy.arguments.bootstrap()
    except Exception as e: _log("args %s"%e)
    _stubs()
    out=base/"host/target/gate-hmc_flowchart_diag.txt"
    state={"done":False}

    def inj():
        for i in range(400):
            try:
                if bool(getattr(renpy.store,"main_menu",False)):
                    _log("main_menu t=%d"%i); break
            except Exception: pass
            time.sleep(0.05)
        else:
            _log("timeout"); _quit(); return
        time.sleep(2.0)
        pre=_sample(); _log("PRE mean=%s var=%.1f ok=%s"%(tuple(round(x,1) for x in pre.get("mean",(0,0,0))), pre.get("var",0), pre.get("ok")))
        def show_and_present(name):
            try:
                renpy.store.ShowMenu(name)()
                try: renpy.restart_interaction()
                except Exception: pass
            except Exception as e:
                _log("ShowMenu %s fail %s"%(name,e)); return False
            for j in range(40):
                if renpy.display.screen.get_screen(name) is not None:
                    break
                time.sleep(0.05)
            time.sleep(0.2)
            info=_redraw()
            rt=_sample()
            _log("SEQ %s root=%s mean=%s var=%.1f ok=%s"%(name, info.get("root"), tuple(round(x,1) for x in rt.get("mean",(0,0,0))), rt.get("var",0), rt.get("ok")))
            try:
                renpy.store.Return()()
                try: renpy.restart_interaction()
                except Exception: pass
            except Exception:
                for n in ("load","preferences","appreciation","flowchart","confirm"):
                    try: renpy.display.screen.hide_screen(n)
                    except Exception: pass
            time.sleep(0.3)
            return True
        for nm in ("load","preferences","appreciation"):
            show_and_present(nm)
        try:
            renpy.store.ShowMenu("flowchart")()
            try: renpy.restart_interaction()
            except Exception: pass
        except Exception as e:
            _log("ShowMenu fail %s"%e); _quit(); return
        for j in range(40):
            if renpy.display.screen.get_screen("flowchart") is not None:
                _log("opened j=%d"%j); break
            time.sleep(0.1)
        time.sleep(0.5)
        # live iface before force
        try:
            import interact_helpers as ih
            ready,why,iface=ih.interface_ready()
            st=getattr(iface,"surftree",None) if iface else None
            acc=_walk(st)
            _log("LIVE_ST nodes=%s ht=%s ints=%s mesh=%s kids_max=%s leaves=%s"%(acc["nodes"],acc["ht"],acc["ints"],acc["mesh"],acc["kids_max"],acc["leaves"][:12]))
        except Exception as e:
            _log("live walk %s"%e)
        live=_sample(); _log("LIVE_PRE_FORCE mean=%s var=%.1f ok=%s"%(tuple(round(x,1) for x in live.get("mean",(0,0,0))), live.get("var",0), live.get("ok")))
        info=_redraw()
        _log("redraw root=%s err=%s"%(info.get("root"), info.get("err")))
        st=info.get("st")
        acc=_walk(st)
        _log("FORCE_ST nodes=%s ht=%s ints=%s mesh=%s kids_max=%s leaves=%s err=%s"%(acc["nodes"],acc["ht"],acc["ints"],acc["mesh"],acc["kids_max"],acc["leaves"][:15],acc["errors"][:5]))
        # also dump top-level child types
        try:
            kids=list(getattr(st,"children",None) or [])
            for i,e in enumerate(kids[:12]):
                ch=e[0] if isinstance(e,(tuple,list)) else e
                cx=e[1] if isinstance(e,(tuple,list)) and len(e)>1 else 0
                cy=e[2] if isinstance(e,(tuple,list)) and len(e)>2 else 0
                tw=getattr(ch,"width",None) or getattr(ch,"w",None)
                th=getattr(ch,"height",None) or getattr(ch,"h",None)
                mesh=getattr(ch,"mesh",None)
                tex=getattr(ch,"texture",None)
                nch=len(list(getattr(ch,"children",None) or []))
                _log("top[%d] type=%s pos=(%s,%s) size=(%s,%s) mesh=%s tex=%s nch=%s"%(i,type(ch).__name__,cx,cy,tw,th,bool(mesh),type(tex).__name__ if tex is not None else None,nch))
        except Exception as e:
            _log("top dump %s"%e)
        post=_sample(); _log("POST mean=%s var=%.1f ok=%s pure=%.3f"%(tuple(round(x,1) for x in post.get("mean",(0,0,0))), post.get("var",0), post.get("ok"), post.get("pure",0)))
        # try load appreciation first then flowchart for order effect
        body=[
            "gate=hmc_flowchart_diag",
            "pre_ok=%s pre_mean=%s"%(pre.get("ok"), pre.get("mean")),
            "live_ok=%s live_mean=%s"%(live.get("ok"), live.get("mean")),
            "post_ok=%s post_mean=%s post_var=%.1f"%(post.get("ok"), post.get("mean"), post.get("var",0)),
            "force_ht=%s force_ints=%s force_nodes=%s"%(acc["ht"],acc["ints"],acc["nodes"]),
        ]
        out.write_text("\n".join(body)+"\n")
        _log("wrote %s"%out)
        state["done"]=True
        _quit()

    threading.Thread(target=inj,daemon=True).start()
    try:
        import renpy.main as m; m.main()
    except Exception as e:
        _log("main %s: %s"%(type(e).__name__,e))
    _log("done")

run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
