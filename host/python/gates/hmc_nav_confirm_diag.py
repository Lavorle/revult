"""Quick confirm present diagnostic (post-nav Step 2)."""
import os
import sys
import threading
import time
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write(f"[hmc_confirm_diag] {m}\n"); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_nav_confirm_diag.log","a").write(m+"\n")

def _quit():
    try:
        import renpy_host; renpy_host.request_quit()
    except Exception:
        pass

def _clear_falsey(n):
    v=os.environ.get(n)
    if v is not None and str(v).strip().lower() in ("","0","false","no","off","n"):
        os.environ.pop(n, None)

def _pre():
    import types
    try:
        import renpy.audio as a
        import renpy.audio.renpysound_host as h
        sys.modules["renpy.audio.renpysound"]=h; a.renpysound=h
    except Exception as e: _log(f"sound {e}")
    try:
        import host_pygame
        import host_pygame.locals as loc
        from host_pygame import scrap
        if not hasattr(host_pygame,"constants"): host_pygame.constants=loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"]=scrap; sys.modules["pygame.scrap"]=scrap
        import renpy.pygame as rpg
        if not hasattr(rpg,"constants"): rpg.constants=host_pygame.constants
        try: rpg.scrap=scrap
        except Exception: pass
        try: rpg.import_as_pygame()
        except Exception: pass
    except Exception as e: _log(f"pygame {e}")
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu.uguu"]=u; sys.modules["renpy.uguu.gl"]=u
        pkg=sys.modules.get("renpy.uguu") or types.ModuleType("renpy.uguu"); pkg.__path__=[]
        sys.modules["renpy.uguu"]=pkg
        for n in dir(u):
            if n.startswith("GL_") or n in ("clear_errors","get_error"): setattr(pkg,n,getattr(u,n))
        pkg.uguu=u; pkg.gl=u
        import renpy; renpy.uguu=pkg
    except Exception as e: _log(f"uguu {e}")
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"]=e
        import renpy; renpy.ecsign=e
    except Exception as e: _log(f"ecsign {e}")

def _sample():
    import renpy_host
    rw,rh,rt=renpy_host.read_game_rt_rgba()
    if not rw: return {"ok":False,"err":"empty"}
    rs=gs=bs=n=0; step_x=max(1,rw//32); step_y=max(1,rh//18)
    for y in range(step_y//2,rh,step_y):
        for x in range(step_x//2,rw,step_x):
            o=(y*rw+x)*4; r,g,b=rt[o],rt[o+1],rt[o+2]
            rs+=r; gs+=g; bs+=b; n+=1
    mean=(rs/n,gs/n,bs/n)
    var=sum((rt[(y*rw+x)*4]-mean[0])**2+(rt[(y*rw+x)*4+1]-mean[1])**2+(rt[(y*rw+x)*4+2]-mean[2])**2
            for y in range(step_y//2,rh,step_y) for x in range(step_x//2,rw,step_x))/n
    clear=abs(mean[0]-13)<8 and abs(mean[1]-13)<8 and abs(mean[2]-20)<12 and var<5
    return {"ok":not clear,"mean":mean,"var":var,"w":rw,"h":rh,"clear":clear}

def _frame_state():
    import renpy_host
    d={}
    for name in ("in_frame","frame_depth"):
        try:
            d[name]=getattr(renpy_host,name)()
        except Exception as e:
            d[name]=f"err:{e}"
    return d

def _present():
    import interact_helpers as ih

    import renpy
    ready,why,iface=ih.interface_ready()
    if not ready: return {"err":why}
    # prefer live surftree if rich
    getattr(iface,"surftree",None)
    root=ih._rebuild_product_root(iface)
    w=int(getattr(renpy.config,"screen_width",1920) or 1920)
    h=int(getattr(renpy.config,"screen_height",1080) or 1080)
    st=renpy.display.render.render_screen(root,w,h)
    draw=renpy.display.draw
    # report frame before
    before=_frame_state()
    try:
        if hasattr(draw,"load_all_textures"): draw.load_all_textures(st)
    except Exception as e:
        _log(f"prepare_err {e}")
    mid=_frame_state()
    try:
        draw.draw_screen(st, flip=True)
        iface.surftree=st
        path="rebuild"
    except Exception as e:
        _log(f"draw_err {e}")
        path="err"
    after=_frame_state()
    return {"path":path,"before":before,"mid":mid,"after":after,"st":type(st).__name__}

def _show_confirm():
    import renpy
    renpy.display.screen.show_screen(
        "confirm",
        message="确认要退出游戏吗",
        yes_action=[renpy.store.Hide("confirm")],
        no_action=[renpy.store.Hide("confirm")],
        confirm_type="quit",
    )
    try: renpy.restart_interaction()
    except Exception: pass

def _show_flow():
    import renpy
    renpy.store.ShowMenu("flowchart")()
    try: renpy.restart_interaction()
    except Exception: pass

def _hide_all():
    import renpy
    for n in ("load","preferences","appreciation","flowchart","confirm","save"):
        try: renpy.display.screen.hide_screen(n)
        except Exception: pass
    try: renpy.restart_interaction()
    except Exception: pass

def run():
    base=_base()
    out=base/"host"/"target"/"gate-hmc_nav_confirm_diag.txt"
    game=os.environ.get("RENPY_HOST_GAME") or str(base/"host"/"playtests"/"HuangmeiC")
    os.environ["RENPY_HOST_BASE"]=str(base)
    os.environ["RENPY_HOST_BUILD"]="1"
    os.environ["RENPY_HOST_GAME"]=game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST","0")
    _clear_falsey("RENPY_SKIP_MAIN_MENU"); _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for p in (str(base/"host"/"python"/"gates"), str(base/"host"/"python")):
        if p not in sys.path: sys.path.insert(0,p)
    import bootstrap as boot
    for name,call in (("import_renpy",boot.stage_import_renpy),("import_all",boot.stage_import_all),
                      ("set_game_dir",lambda: boot.stage_set_game_dir(base))):
        good,_miss,err,_extra=call(); _log(f"stage {name} good={good} err={err!r}")
        if not good:
            out.write_text(f"ok=False\nerror={err}\n"); _quit(); return
    import renpy
    renpy.host_build=True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e: _log(f"main_host {e}")
    try:
        import renpy.arguments
        basedir=getattr(renpy.config,"basedir",None) or game
        argv0=sys.argv[0] if sys.argv else "renpy-host"
        sys.argv=[argv0,basedir,"run"]
        if not getattr(renpy.arguments,"commands",None):
            try:
                renpy.arguments.register_command("run", renpy.arguments.run, True)
                renpy.arguments.register_command("quit", renpy.arguments.quit)
            except Exception: pass
        renpy.game.args=renpy.arguments.bootstrap()
    except Exception as e: _log(f"args {e}")
    _pre()
    results=[]
    def injector():
        for i in range(400):
            try:
                if bool(getattr(renpy.store,"main_menu",False)):
                    _log("main_menu tick=%d"%i); break
            except Exception: pass
            time.sleep(0.05)
        time.sleep(2.0)
        # Case A: confirm alone
        _log("=== A confirm alone ===")
        _show_confirm(); time.sleep(0.5)
        for j in range(20):
            if renpy.display.screen.get_screen("confirm") is not None: break
            time.sleep(0.1)
        pre=_sample(); _log(f"A pre_rt {pre}")
        _log(f"A frame {_frame_state()}")
        p=_present(); _log("A present %s"%{k:v for k,v in p.items() if k!='st'})
        post=_sample(); _log(f"A post_rt {post}")
        results.append(("confirm_alone", post))
        _hide_all(); time.sleep(0.5)
        # Case B: flowchart then confirm
        _log("=== B flowchart then confirm ===")
        _show_flow(); time.sleep(0.8)
        p=_present(); _log("B flow present %s"%{k:v for k,v in p.items() if k!='st'})
        frt=_sample(); _log(f"B flow rt {frt}")
        _hide_all(); time.sleep(0.4)
        _show_confirm(); time.sleep(0.5)
        pre=_sample(); _log(f"B conf pre_rt {pre}")
        _log(f"B frame {_frame_state()}")
        p=_present(); _log("B conf present %s"%{k:v for k,v in p.items() if k!='st'})
        post=_sample(); _log(f"B conf post_rt {post}")
        results.append(("confirm_after_flow", post))
        # Case C: confirm alone again
        _hide_all(); time.sleep(0.4)
        _log("=== C confirm alone again ===")
        _show_confirm(); time.sleep(0.5)
        p=_present(); _log("C present %s"%{k:v for k,v in p.items() if k!='st'})
        post=_sample(); _log(f"C post_rt {post}")
        results.append(("confirm_alone_2", post))
        lines=["gate=hmc_nav_confirm_diag"]
        ok=all(r[1].get("ok") for r in results)
        lines.append(f"ok={ok}")
        for name,rt in results:
            lines.append("case.{} ok={} mean={} var={} clear={}".format(name,rt.get("ok"),rt.get("mean"),rt.get("var"),rt.get("clear")))
        out.write_text("\n".join(lines)+"\n")
        art=os.environ.get("ARTIFACT_DIR")
        if art:
            Path(art).mkdir(parents=True,exist_ok=True)
            (Path(art)/"gate-hmc_nav_confirm_diag.txt").write_text(out.read_text())
        _log(f"wrote {out} ok={ok}")
        _quit()
    threading.Thread(target=injector,daemon=True).start()
    import renpy.main as m
    try: m.main()
    except BaseException as e: _log(f"main exit {e}")

run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
