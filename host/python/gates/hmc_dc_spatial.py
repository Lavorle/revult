"""Spatial RT probe for dialog_config_1/2 black background."""
import os
import sys
import threading
import time
import traceback
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
        sys.__stdout__.write(f"[dc_spatial] {m}\n"); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_dc_spatial.log","a").write(m+"\n")  # noqa: SIM115

def _quit():
    try:
        import renpy_host; renpy_host.request_quit()  # noqa: I001
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
        import renpy; renpy.uguu=pkg  # noqa: I001
    except Exception as e: _log(f"uguu {e}")
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"]=e
        import renpy; renpy.ecsign=e  # noqa: I001
    except Exception as e: _log(f"ecsign {e}")

def _sample_regions():
    import renpy_host
    rw,rh,rt=renpy_host.read_game_rt_rgba()
    if not rw: return {"err":"empty"}
    # regions in virtual 1920x1080 mapped to physical
    # panel center ~ (960, 540) for 1849x846 centered
    # mask full screen
    # top nav bar y~47
    # bottom buttons y~999
    regions = {
        "full": (0.0,0.0,1.0,1.0),
        "panel_c": (0.4,0.35,0.6,0.65),   # center of prefs panel
        "panel_tl": (0.1,0.2,0.3,0.4),
        "panel_tr": (0.7,0.2,0.9,0.4),
        "nav": (0.25,0.03,0.75,0.12),
        "title": (0.0,0.0,0.25,0.12),
        "bottom": (0.7,0.9,0.98,0.99),
        "edge": (0.0,0.0,0.05,0.05),
    }
    out={}
    for name,(x0,y0,x1,y1) in regions.items():
        xs=int(x0*rw); xe=max(xs+1,int(x1*rw))
        ys=int(y0*rh); ye=max(ys+1,int(y1*rh))
        rs=gs=bs=n=0; dark=0
        step_x=max(1,(xe-xs)//16); step_y=max(1,(ye-ys)//12)
        for y in range(ys+step_y//2, ye, step_y):
            for x in range(xs+step_x//2, xe, step_x):
                o=(y*rw+x)*4
                r,g,b=rt[o],rt[o+1],rt[o+2]
                rs+=r; gs+=g; bs+=b; n+=1
                if r<25 and g<25 and b<30: dark+=1
        mean=(rs/n, gs/n, bs/n)
        out[name]={"mean":tuple(round(v,1) for v in mean),"dark":round(dark/n,3),"n":n}
    # also save a downscaled PNG for inspection
    try:
        from PIL import Image
        im=Image.frombytes("RGBA",(rw,rh),bytes(rt))
        im=im.resize((480,270), Image.BILINEAR)
        path="/tmp/hmc_dc_spatial_{}.png".format(os.environ.get("DC_KIND","x"))
        im.save(path)
        out["png"]=path
    except Exception as e:
        out["png_err"]=str(e)
    return out

def _redraw():
    import interact_helpers as ih

    import renpy
    ready, why, iface = ih.interface_ready()
    if not ready: return "iface:"+why, None
    root=ih._rebuild_product_root(iface)
    w=int(getattr(renpy.config,"screen_width",1920) or 1920)
    h=int(getattr(renpy.config,"screen_height",1080) or 1080)
    st=renpy.display.render.render_screen(root,w,h)
    draw=renpy.display.draw
    try:
        if hasattr(draw,"load_all_textures"): draw.load_all_textures(st)
    except Exception as e:
        return "load:"+str(e), st
    draw.draw_screen(st, flip=True)
    try: iface.surftree=st
    except Exception: pass
    return "ok", st

def _walk_bg(node, budget=None, acc=None, depth=0):
    from renpy.wgpu.draw import HostTexture
    if acc is None: acc={"n":0,"n_ht":0,"bg_1849":0,"mask_1920":0,"sizes":[]}
    if budget is None: budget=[3000]
    if node is None or budget[0]<=0 or depth>40: return acc
    budget[0]-=1; acc["n"]+=1
    if isinstance(node, HostTexture):
        acc["n_ht"]+=1
        w,h=int(getattr(node,"w",0) or 0), int(getattr(node,"h",0) or 0)
        if len(acc["sizes"])<20: acc["sizes"].append((w,h,int(node.handle)))
        if (w,h)==(1849,846): acc["bg_1849"]+=1
        if (w,h)==(1920,1080): acc["mask_1920"]+=1
    kids=[]
    try:
        ch=getattr(node,"children",None)
        if ch:
            for c in ch:
                kids.append(c[0] if isinstance(c,(list,tuple)) and c else c)
    except Exception: pass
    for attr in ("cached_texture","cached_model","texture"):
        try:
            v=getattr(node,attr,None)
            if v is not None: kids.append(v)
        except Exception: pass
    try:
        ts=getattr(node,"textures",None)
        if ts:
            for t in ts: kids.append(t)  # noqa: PERF402
    except Exception: pass
    for k in kids: _walk_bg(k, budget, acc, depth+1)
    return acc

def _show(kind):
    import renpy
    try:
        renpy.display.screen.show_screen("preferences", kind=kind)
        try: renpy.restart_interaction()
        except Exception: pass
        return True
    except Exception as e:
        _log(f"show fail {e}"); return False

def _hide():
    import renpy
    for n in ("preferences","confirm","load","save","appreciation","flowchart"):
        try: renpy.store.Hide(n)()
        except Exception:
            try: renpy.hide_screen(n)
            except Exception: pass

def _worker():
    out=_base()/"host"/"target"/"gate-hmc_dc_spatial.txt"
    lines=[]
    try:
        import renpy
        deadline=time.time()+90
        while time.time()<deadline:
            try:
                if getattr(renpy.store,"main_menu",False): break
            except Exception: pass
            time.sleep(0.2)
        lines.append("main_menu={}".format(getattr(renpy.store,"main_menu",None)))
        # order: dialog_config_2 FIRST, then dialog_config_1, then dialog_config_1 again
        order=["dialog_config_2","dialog_config_1","image_config","dialog_config_1"]
        for kind in order:
            _hide(); time.sleep(0.15)
            ok=_show(kind); time.sleep(0.25)
            os.environ["DC_KIND"]=kind
            rd, st=_redraw()
            walk=_walk_bg(st) if st is not None else {}
            reg=_sample_regions()
            line="RESULT kind={} opened={} redraw={} walk_n_ht={} bg1849={} mask1920={} sizes={}".format(
                kind, ok, rd, walk.get("n_ht"), walk.get("bg_1849"), walk.get("mask_1920"), walk.get("sizes",[])[:12])
            lines.append(line); _log(line)
            for rn,rv in (reg.items() if isinstance(reg,dict) else []):
                if rn in ("png","png_err"): continue
                lines.append("  region {} mean={} dark={}".format(rn, rv.get("mean"), rv.get("dark")))
            lines.append("  png={}".format(reg.get("png")) or reg.get("png_err"))
            _log("  full={} panel_c={}".format(reg.get("full"), reg.get("panel_c")))
        out.write_text("\n".join(lines)+"\n")
        _log(f"wrote {out}")
    except Exception:
        tb=traceback.format_exc(); lines.append(tb); out.write_text("\n".join(lines)+"\n"); _log(tb)
    finally:
        time.sleep(0.3); _quit()

def main():
    base=_base()
    game=os.environ.get("RENPY_HOST_GAME") or str(base/"host"/"playtests"/"HuangmeiC")
    os.environ["RENPY_HOST_BASE"]=str(base)
    os.environ["RENPY_HOST_BUILD"]="1"
    os.environ["RENPY_HOST_GAME"]=game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST","0")
    _clear_falsey("RENPY_SKIP_MAIN_MENU"); _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for p in (str(base/"host"/"python"/"gates"), str(base/"host"/"python")):
        if p not in sys.path: sys.path.insert(0,p)
    # install ourselves as gate by writing into gates path and using product path
    # simpler: run like phase0 gate
    open("/tmp/hmc_dc_spatial.log","w").write("start\n")  # noqa: SIM115
    import bootstrap as boot
    for name,call in (
        ("import_renpy",boot.stage_import_renpy),
        ("import_all",boot.stage_import_all),
        ("set_game_dir",lambda: boot.stage_set_game_dir(base)),
    ):
        good,_miss,err,_extra=call()
        _log(f"stage {name} good={good} err={err!r}")
        if not good:
            _quit(); return
    import renpy
    renpy.host_build=True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)  # noqa: I001
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
    t=threading.Thread(target=_worker, daemon=True); t.start()
    import renpy.main as m
    try: m.main()
    except BaseException as e: _log(f"main exit {e}")

if __name__=="__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
