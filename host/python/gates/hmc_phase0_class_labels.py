"""Phase 0 class-label probe for HuangmeiC C2/C3 (engine-only).

Opens preferences kinds: dialog_config_1, dialog_config_2, text_config.
Samples RT mean/var and walks surftree for HostTexture / reverse / clipping.
Writes host/target/gate-hmc_phase0_class_labels.txt
"""
import os
import sys
import threading
import time
import traceback
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write(f"[hmc_p0] {m}\n"); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_phase0_class_labels.log","a").write(m+"\n")  # noqa: SIM115

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

def _sample_rt():
    import renpy_host
    rw,rh,rt=renpy_host.read_game_rt_rgba()
    if not rw: return {"ok":False,"err":"empty"}
    rs=gs=bs=n=0; step_x=max(1,rw//32); step_y=max(1,rh//18)
    dark=0
    for y in range(step_y//2,rh,step_y):
        for x in range(step_x//2,rw,step_x):
            o=(y*rw+x)*4; r,g,b=rt[o],rt[o+1],rt[o+2]
            rs+=r; gs+=g; bs+=b; n+=1
            if r<25 and g<25 and b<30: dark+=1
    mean=(rs/n,gs/n,bs/n)
    var=sum((rt[(y*rw+x)*4]-mean[0])**2+(rt[(y*rw+x)*4+1]-mean[1])**2+(rt[(y*rw+x)*4+2]-mean[2])**2
            for y in range(step_y//2,rh,step_y) for x in range(step_x//2,rw,step_x))/n
    # arena clear ~ (0.05,0.05,0.08)*255 ≈ (13,13,20)
    clear=abs(mean[0]-13)<10 and abs(mean[1]-13)<10 and abs(mean[2]-20)<14 and var<30
    return {"ok":not clear,"mean":mean,"var":var,"w":rw,"h":rh,"clear":clear,"dark_frac":dark/n}

def _walk(node, acc=None, depth=0, budget=None):
    if acc is None:
        acc={"n":0,"n_ht":0,"n_dead":0,"n_alive":0,"n_clip":0,"n_rev":0,"n_frame_like":0,
             "ht_sizes":[],"rev_samples":[],"clip_samples":[],"offered":[]}
    if budget is None: budget=[4000]
    if budget[0]<=0 or node is None or depth>40: return acc
    budget[0]-=1
    acc["n"]+=1
    try:
        from renpy.wgpu.draw import HostTexture
    except Exception:
        HostTexture=None
    # size
    try:
        w=int(getattr(node,"width",0) or 0); h=int(getattr(node,"height",0) or 0)
        if w and h and len(acc["offered"])<12:
            acc["offered"].append((w,h,type(node).__name__))
    except Exception:
        pass
    # clip flags
    try:
        xc=bool(getattr(node,"xclipping",False)); yc=bool(getattr(node,"yclipping",False))
        if xc or yc:
            acc["n_clip"]+=1
            if len(acc["clip_samples"])<8:
                acc["clip_samples"].append((xc,yc,getattr(node,"width",None),getattr(node,"height",None)))
    except Exception:
        pass
    # reverse
    try:
        rev=getattr(node,"reverse",None)
        if rev is not None:
            acc["n_rev"]+=1
            if len(acc["rev_samples"])<8:
                try:
                    xdx=float(getattr(rev,"xdx",getattr(rev,"xd",1)))
                    ydy=float(getattr(rev,"ydy",getattr(rev,"yd",1)))
                except Exception:
                    xdx=ydy=None
                acc["rev_samples"].append((xdx,ydy,getattr(node,"width",None),getattr(node,"height",None)))
    except Exception:
        pass
    # HostTexture
    try:
        if HostTexture is not None and isinstance(node, HostTexture):
            acc["n_ht"]+=1
            alive=True
            try:
                import renpy_host
                alive=bool(renpy_host.texture_alive(int(node.handle)))
            except Exception:
                pass
            if alive: acc["n_alive"]+=1
            else: acc["n_dead"]+=1
            if len(acc["ht_sizes"])<12:
                acc["ht_sizes"].append((getattr(node,"w",None),getattr(node,"h",None),alive,int(getattr(node,"handle",0) or 0)))
    except Exception:
        pass
    # children
    kids=[]
    try:
        ch=getattr(node,"children",None)
        if ch:
            for c in ch:
                if isinstance(c,(list,tuple)) and c:
                    kids.append(c[0])
                else:
                    kids.append(c)
    except Exception:
        pass
    try:
        ct=getattr(node,"cached_texture",None)
        if ct is not None: kids.append(ct)
    except Exception:
        pass
    try:
        cm=getattr(node,"cached_model",None)
        if cm is not None: kids.append(cm)
    except Exception:
        pass
    for k in kids:
        _walk(k, acc, depth+1, budget)
    return acc

def _redraw():
    import renpy
    try:
        import interact_helpers as ih
    except Exception as e:
        return f"no_ih:{e}"
    try:
        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            return f"iface:{why}"
        root = ih._rebuild_product_root(iface)
        if root is None:
            return "root_absent"
        w=int(getattr(renpy.config,"screen_width",1920) or 1920)
        h=int(getattr(renpy.config,"screen_height",1080) or 1080)
        surftree=renpy.display.render.render_screen(root,w,h)
        draw=getattr(renpy.display,"draw",None)
        if draw is None or not hasattr(draw,"draw_screen"):
            return "no_draw"
        try:
            if hasattr(draw,"load_all_textures"):
                draw.load_all_textures(surftree)
        except Exception as e:
            return f"load_fail:{e}"
        draw.draw_screen(surftree, flip=True)
        try: iface.surftree=surftree
        except Exception: pass
        return "ok", surftree
    except Exception as e:
        return f"exc:{e}"

def _show_prefs(kind):
    import renpy
    try:
        renpy.display.screen.show_screen("preferences", kind=kind)
        try: renpy.restart_interaction()
        except Exception: pass
        return True, "show_screen_preferences_kind"
    except Exception as e:
        try:
            renpy.store.ShowMenu("preferences")()
            # then replace kind
            renpy.display.screen.show_screen("preferences", kind=kind)
            try: renpy.restart_interaction()
            except Exception: pass
            return True, f"ShowMenu+kind:{e}"
        except Exception as e2:
            return False, f"fail {e} / {e2}"

def _hide_prefs():
    import renpy
    for n in ("preferences","confirm","load","save","appreciation","flowchart"):
        try: renpy.store.Hide(n)()
        except Exception:
            try: renpy.hide_screen(n)
            except Exception: pass

def _worker():
    out=_base()/"host"/"target"/"gate-hmc_phase0_class_labels.txt"
    lines=[]
    try:
        import renpy
        # wait main_menu
        deadline=time.time()+90
        while time.time()<deadline:
            try:
                if getattr(renpy.store,"main_menu",False):
                    break
            except Exception:
                pass
            time.sleep(0.2)
        lines.append("main_menu={}".format(getattr(renpy.store,"main_menu",None)))
        targets=["dialog_config_1","dialog_config_2","text_config","image_config"]
        results=[]
        for kind in targets:
            _hide_prefs()
            time.sleep(0.15)
            ok, via = _show_prefs(kind)
            time.sleep(0.2)
            rd=_redraw()
            if isinstance(rd,tuple):
                redraw_s, surftree=rd[0], rd[1]
            else:
                redraw_s, surftree=rd, None
            rt=_sample_rt()
            walk={}
            if surftree is not None:
                walk=_walk(surftree)
            rec={
                "kind":kind,"opened":ok,"via":via,"redraw":redraw_s,
                "rt":rt,"walk":{k:walk.get(k) for k in ("n","n_ht","n_dead","n_alive","n_clip","n_rev")},
                "ht_sizes":walk.get("ht_sizes",[])[:8],
                "rev_samples":walk.get("rev_samples",[])[:6],
                "clip_samples":walk.get("clip_samples",[])[:6],
                "offered":walk.get("offered",[])[:8],
            }
            results.append(rec)
            lines.append("RESULT kind={} opened={} via={} redraw={} clear={} mean={} var={:.1f} dark_frac={:.3f} n_ht={} n_dead={} n_clip={} n_rev={}".format(
                kind, ok, via, redraw_s,
                rt.get("clear"), tuple(round(x,1) for x in rt.get("mean",(0,0,0))) if rt.get("mean") else None,
                float(rt.get("var") or 0), float(rt.get("dark_frac") or 0),
                walk.get("n_ht"), walk.get("n_dead"), walk.get("n_clip"), walk.get("n_rev")))
            lines.append("  ht_sizes={}".format(walk.get("ht_sizes",[])[:8]))
            lines.append("  rev_samples={}".format(walk.get("rev_samples",[])[:6]))
            lines.append("  clip_samples={}".format(walk.get("clip_samples",[])[:6]))
            lines.append("  offered={}".format(walk.get("offered",[])[:8]))
            _log(lines[-5])
        # Class label heuristics
        dc1=next((r for r in results if r["kind"]=="dialog_config_1"),None)
        dc2=next((r for r in results if r["kind"]=="dialog_config_2"),None)
        tc=next((r for r in results if r["kind"]=="text_config"),None)
        # C2
        c2="other:insufficient"
        if dc1 and dc2:
            both_clear = dc1["rt"].get("clear") and dc2["rt"].get("clear")
            any_dead = (dc1["walk"].get("n_dead") or 0)+(dc2["walk"].get("n_dead") or 0)
            any_ht = (dc1["walk"].get("n_ht") or 0)+(dc2["walk"].get("n_ht") or 0)
            any_rev = (dc1["walk"].get("n_rev") or 0)+(dc2["walk"].get("n_rev") or 0)
            if not dc1["opened"] and not dc2["opened"] or both_clear and any_ht==0:
                c2="never_in_tree"
            elif both_clear and any_dead>0:
                c2="dead_present"
            elif both_clear and any_rev>0 and any_ht>0:
                c2="reverse_frame_miss"
            elif both_clear:
                c2=f"other:clear_with_ht_alive n_ht={any_ht} n_rev={any_rev}"
            else:
                c2="other:not_clear_mean_dc1={} dc2={}".format(dc1["rt"].get("mean"),dc2["rt"].get("mean"))
        # C3
        c3="other:insufficient"
        if tc:
            n_clip=tc["walk"].get("n_clip") or 0
            n_rev=tc["walk"].get("n_rev") or 0
            # text_config uses crop+zoom on 1920x1080 fixed; clip-class if clip flags present but still oversize visual
            # reverse-dest if reverse scale samples show full_oversample filling parent without crop
            if n_clip>0 and n_rev>0:
                c3="both"
            elif n_clip>0:
                c3="clip-class"
            elif n_rev>0:
                # crop may stamp clipping only on Render; if zero clip flags, still clip-class from screen source
                c3="reverse-dest-class"
            else:
                c3="clip-class"  # screen source uses crop; wgpu has zero xclipping hits historically
        lines.append(f"LABEL_C2={c2}")
        lines.append(f"LABEL_C3={c3}")
        lines.append("NOTE text_config source uses crop(0,825,1920,255)+zoom0.42 over 1920x1080 fixed; bg street day is 3840x2160 + full_fill")
        lines.append("NOTE dialog_config uses preferences_layout background 1849x846 Frame-less Image")
        lines.append("ok=True")
        out.write_text("\n".join(lines)+"\n")
        _log(f"wrote {out}")
        _log(f"LABEL_C2={c2} LABEL_C3={c3}")
    except Exception:
        tb=traceback.format_exc()
        lines.append(f"EXC {tb}")
        out.write_text("\n".join(lines)+"\n")
        _log(tb)
    finally:
        time.sleep(0.3)
        _quit()

def main():
    """Host-embed entry: stages + renpy.main.main() (not stock bootstrap; utf8_mode)."""
    base=_base()
    game=os.environ.get("RENPY_HOST_GAME") or str(base/"host"/"playtests"/"HuangmeiC")
    os.environ["RENPY_HOST_BASE"]=str(base)
    os.environ["RENPY_HOST_BUILD"]="1"
    os.environ["RENPY_HOST_GAME"]=game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST","0")
    os.environ["RENPY_HOST_UI_TRACE"]=os.environ.get("RENPY_HOST_UI_TRACE","1")
    _clear_falsey("RENPY_SKIP_MAIN_MENU")
    _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for p in (str(base/"host"/"python"/"gates"), str(base/"host"/"python")):
        if p not in sys.path:
            sys.path.insert(0,p)
    open("/tmp/hmc_phase0_class_labels.log","w").write("start\n")  # noqa: SIM115
    import bootstrap as boot
    import renpy_host  # noqa: F401
    for name,call in (
        ("import_renpy",boot.stage_import_renpy),
        ("import_all",boot.stage_import_all),
        ("set_game_dir",lambda: boot.stage_set_game_dir(base)),
    ):
        good,_miss,err,_extra=call()
        _log(f"stage {name} good={good} err={err!r}")
        if not good:
            out=base/"host"/"target"/"gate-hmc_phase0_class_labels.txt"
            out.write_text(f"ok=False\nerror=stage_{name} {err}\n")
            _quit()
            return
    import renpy
    renpy.host_build=True
    try:
        import renpy_main_host
        renpy_main_host.install(renpy)
    except Exception as e:
        _log(f"main_host {e}")
    try:
        import renpy.arguments
        basedir=getattr(renpy.config,"basedir",None) or game
        argv0=sys.argv[0] if sys.argv else "renpy-host"
        sys.argv=[argv0,basedir,"run"]
        if not getattr(renpy.arguments,"commands",None):
            try:
                renpy.arguments.register_command("run", renpy.arguments.run, True)
                renpy.arguments.register_command("quit", renpy.arguments.quit)
            except Exception:
                pass
        renpy.game.args=renpy.arguments.bootstrap()
    except Exception as e:
        _log(f"args {e}")
    _pre()
    _log(f"basedir={game}")
    t=threading.Thread(target=_worker, daemon=True)
    t.start()
    import renpy.main as m
    try:
        m.main()
    except BaseException as e:
        _log(f"main exit {e}")

if __name__=="__main__":
    main()
else:
    # host run_gate path: module-level side effect after imports prepended
    try:
        main()
    except Exception:
        traceback.print_exc()
        _quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
