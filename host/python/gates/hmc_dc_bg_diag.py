"""Diagnose missing preferences panel background on dialog_config_1."""
import os
import sys
import threading
import time
import traceback
from pathlib import Path


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write(f"[dc_bg] {m}\n"); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_dc_bg_diag.log","a").write(m+"\n")  # noqa: SIM115

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

def _sample_rt():
    import renpy_host
    rw,rh,rt=renpy_host.read_game_rt_rgba()
    if not rw: return {"err":"empty"}
    rs=gs=bs=n=0; dark=0
    step_x=max(1,rw//32); step_y=max(1,rh//18)
    for y in range(step_y//2,rh,step_y):
        for x in range(step_x//2,rw,step_x):
            o=(y*rw+x)*4; r,g,b=rt[o],rt[o+1],rt[o+2]
            rs+=r; gs+=g; bs+=b; n+=1
            if r<25 and g<25 and b<30: dark+=1
    return {"mean":(rs/n,gs/n,bs/n),"dark":dark/n,"w":rw,"h":rh}

def _find_in_render(node, pred, acc=None, budget=None, depth=0, path=""):
    if acc is None: acc=[]
    if budget is None: budget=[5000]
    if node is None or budget[0]<=0 or depth>50: return acc
    budget[0]-=1
    try:
        if pred(node):
            acc.append((path, node))
            if len(acc)>=30: return acc
    except Exception:
        pass
    kids=[]
    try:
        ch=getattr(node,"children",None)
        if ch:
            for i,c in enumerate(ch):
                if isinstance(c,(list,tuple)) and c:
                    kids.append((c[0], "%s/c%d"%(path,i)))  # noqa: UP031
                else:
                    kids.append((c, "%s/c%d"%(path,i)))  # noqa: UP031
    except Exception: pass
    for attr in ("cached_texture","cached_model","texture"):
        try:
            v=getattr(node,attr,None)
            if v is not None: kids.append((v, f"{path}/{attr}"))
        except Exception: pass
    try:
        ts=getattr(node,"textures",None)
        if ts:
            for i,t in enumerate(ts):
                kids.append((t, "%s/tex%d"%(path,i)))  # noqa: UP031
    except Exception: pass
    for k,p in kids:
        _find_in_render(k, pred, acc, budget, depth+1, p)
        if len(acc)>=30: break
    return acc

def _ht_info(ht):
    from renpy.wgpu.draw import HostTexture
    if not isinstance(ht, HostTexture):
        return {"type":type(ht).__name__}
    h=int(getattr(ht,"handle",0) or 0)
    w=int(getattr(ht,"w",0) or 0); hh=int(getattr(ht,"h",0) or 0)
    alive=None
    try:
        import renpy_host
        alive=bool(renpy_host.texture_alive(h)) if h else False
    except Exception as e:
        alive=f"err:{e}"
    # sample pixel stash if any
    stash_ok=None
    try:
        import renpy
        draw=renpy.display.draw
        ent=getattr(draw,"_handle_pixels",{}).get(h)
        if ent:
            pixels=ent.get("pixels") if isinstance(ent,dict) else None
            if pixels is None and isinstance(ent,(list,tuple)) and len(ent)>=3:
                pixels=ent[2] if len(ent)>2 else None
            # stash format may vary
            if isinstance(ent, dict):
                pw=ent.get("w"); ph=ent.get("h"); px=ent.get("pixels")
            elif isinstance(ent, (list,tuple)):
                # try common (w,h,pixels) or similar
                pw=w; ph=hh; px=None
                for item in ent:
                    if isinstance(item,(bytes,bytearray,memoryview)):
                        px=bytes(item)
            else:
                pw=w; ph=hh; px=None
            if px is not None and pw and ph:
                # mean of a few pixels
                px=bytes(px)
                n=pw*ph
                if len(px)>=n*4:
                    step=max(1,n//64)
                    rs=gs=bs=as_=cnt=0
                    for i in range(0,n,step):
                        o=i*4
                        rs+=px[o]; gs+=px[o+1]; bs+=px[o+2]; as_+=px[o+3]; cnt+=1
                    stash_ok={"mean":(rs/cnt,gs/cnt,bs/cnt,as_/cnt),"len":len(px)}
    except Exception as e:
        stash_ok={"err":str(e)}
    return {"handle":h,"w":w,"h":hh,"alive":alive,"stash":stash_ok}

def _scan_displayables(root, needle_substrings):
    """Walk displayable tree looking for image filenames."""
    hits=[]
    budget=[8000]
    def walk(d, path, depth):
        if d is None or budget[0]<=0 or depth>40 or len(hits)>=40: return
        budget[0]-=1
        try:
            s=repr(d)
        except Exception:
            s=type(d).__name__
        for n in needle_substrings:
            if n in s:
                hits.append((path, type(d).__name__, s[:200]))
                break
        # children
        for attr in ("child","children","image","displayable","style"):
            try:
                v=getattr(d,attr,None)
            except Exception:
                continue
            if v is None: continue
            if attr=="children":
                try:
                    for i,c in enumerate(list(v)[:50]):
                        walk(c, path+"/ch%d"%i, depth+1)  # noqa: UP031
                except Exception:
                    pass
            elif attr=="style":
                try:
                    for k in ("background","idle_background","hover_background","selected_background","child"):
                        try:
                            getattr(v,k,None) if not callable(getattr(v,k,None)) else None
                        except Exception:
                            pass
                        # style properties often via index
                    # try style["background"]
                    try:
                        bg=v["background"]
                        walk(bg, path+"/style.bg", depth+1)
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                walk(v, path+"/"+attr, depth+1)
        # Focus/Fixed containers
        try:
            if hasattr(d,"visit"):
                for c in d.visit() or []:
                    walk(c, path+"/v", depth+1)
        except Exception:
            pass
    walk(root, "root", 0)
    return hits

def _show(kind):
    import renpy
    renpy.display.screen.show_screen("preferences", kind=kind)
    try: renpy.restart_interaction()
    except Exception: pass

def _hide():
    import renpy
    for n in ("preferences","confirm","load","save","appreciation","flowchart"):
        try: renpy.store.Hide(n)()
        except Exception:
            try: renpy.hide_screen(n)
            except Exception: pass

def _redraw():
    import interact_helpers as ih

    import renpy
    ready, why, iface = ih.interface_ready()
    if not ready: return "iface:"+why, None, None
    root=ih._rebuild_product_root(iface)
    w=int(getattr(renpy.config,"screen_width",1920) or 1920)
    h=int(getattr(renpy.config,"screen_height",1080) or 1080)
    st=renpy.display.render.render_screen(root,w,h)
    draw=renpy.display.draw
    # before load: find large HTs
    pre=[]
    try:
        from renpy.wgpu.draw import HostTexture
        def pred(n):
            return isinstance(n, HostTexture) and int(getattr(n,"w",0) or 0)>=1800
        pre=_find_in_render(st, pred)
    except Exception as e:
        pre=[("err",str(e))]
    try:
        if hasattr(draw,"load_all_textures"): draw.load_all_textures(st)
    except Exception as e:
        return "load:"+str(e), st, pre
    draw.draw_screen(st, flip=True)
    try: iface.surftree=st
    except Exception: pass
    return "ok", st, pre

def _worker():
    out=_base()/"host"/"target"/"gate-hmc_dc_bg_diag.txt"
    lines=[]
    try:
        import renpy
        from renpy.wgpu.draw import HostTexture
        deadline=time.time()+90
        while time.time()<deadline:
            try:
                if getattr(renpy.store,"main_menu",False): break
            except Exception: pass
            time.sleep(0.2)
        lines.append("main_menu={}".format(getattr(renpy.store,"main_menu",None)))

        # Force-load the background image through renpy.display.im and check
        try:
            img = renpy.display.im.Image("gui/preferences/common/background.png")
            surf = renpy.display.im.load_surface(img)
            sw,sh = surf.get_size()
            # sample center pixel
            try:
                px = surf.get_at((sw//2, sh//2))
            except Exception:
                px = None
            lines.append(f"im.load_surface bg size=({sw},{sh}) center={px}")
            # load_texture
            draw=renpy.display.draw
            ht=draw.load_texture(surf)
            lines.append(f"load_texture bg -> {_ht_info(ht)}")
        except Exception as e:
            lines.append(f"im.load_surface FAIL {e}")

        for kind in ("dialog_config_2","dialog_config_1","dialog_config_1"):
            _hide(); time.sleep(0.1)
            _show(kind); time.sleep(0.2)
            # screen layer displayables
            scr=None
            try:
                scr=renpy.display.screen.get_screen("preferences")
            except Exception as e:
                lines.append(f"get_screen fail {e}")
            d_hits=[]
            if scr is not None:
                try:
                    # screen widget tree
                    child=getattr(scr,"child",None) or getattr(scr,"screen",None) or scr
                    d_hits=_scan_displayables(child, ["background.png","mask.png","preferences/common"])
                except Exception as e:
                    d_hits=[("scan_err",str(e))]
            lines.append("KIND %s displayable_hits=%d"% (kind, len(d_hits)))  # noqa: UP031
            for h in d_hits[:12]:
                lines.append(f"  disp {h}")

            rd, st, pre = _redraw()
            lines.append("KIND %s redraw=%s pre_large_ht=%d"%(kind, rd, len(pre) if isinstance(pre,list) else -1))  # noqa: UP031
            if isinstance(pre,list):
                for p,n in pre[:8]:
                    lines.append(f"  pre {p} {_ht_info(n) if isinstance(n,HostTexture) else type(n).__name__}")

            # post: find 1849x846 and 1920x1080 HTs
            def pred_bg(n):
                return isinstance(n, HostTexture) and (int(getattr(n,"w",0) or 0), int(getattr(n,"h",0) or 0))==(1849,846)
            def pred_full(n):
                return isinstance(n, HostTexture) and (int(getattr(n,"w",0) or 0), int(getattr(n,"h",0) or 0))==(1920,1080)
            bgs=_find_in_render(st, pred_bg) if st is not None else []
            fulls=_find_in_render(st, pred_full) if st is not None else []
            lines.append("  post bg1849_count=%d full1920_count=%d"%(len(bgs), len(fulls)))  # noqa: UP031
            for p,n in bgs[:4]:
                lines.append(f"  bg {p} {_ht_info(n)}")
            for p,n in fulls[:4]:
                lines.append(f"  full {p} {_ht_info(n)}")

            # count all HT sizes histogram
            hist={}
            def pred_all(n):
                if isinstance(n, HostTexture):
                    key=(int(getattr(n,"w",0) or 0), int(getattr(n,"h",0) or 0))
                    hist[key]=hist.get(key,0)+1  # noqa: B023
                return False
            _find_in_render(st, pred_all)
            top=sorted(hist.items(), key=lambda kv: -kv[1])[:15]
            lines.append(f"  ht_hist_top={top}")

            # texture_cache size
            try:
                draw=renpy.display.draw
                lines.append("  texture_cache=%d handle_pixels=%d remap=%d"%(  # noqa: UP031
                    len(getattr(draw,"texture_cache",{}) or {}),
                    len(getattr(draw,"_handle_pixels",{}) or {}),
                    len(getattr(draw,"_handle_remap",{}) or {}),
                ))
                try:
                    import renpy_host
                    lines.append("  arena sample={} order={}".format(
                        renpy_host.sample_texture_count() if hasattr(renpy_host,"sample_texture_count") else "?",
                        renpy_host.texture_order_len() if hasattr(renpy_host,"texture_order_len") else "?",
                    ))
                except Exception as e:
                    lines.append(f"  arena err {e}")
            except Exception as e:
                lines.append(f"  cache err {e}")

            rt=_sample_rt()
            lines.append("  rt mean={} dark={:.3f}".format(
                tuple(round(x,1) for x in rt.get("mean",(0,0,0))) if rt.get("mean") else None,
                float(rt.get("dark") or 0)))
            _log(lines[-1])

            # Also try to force re-add: re-render only the layout background displayable
            try:
                d=renpy.easy.displayable("gui/preferences/common/background.png")
                r=renpy.display.render.render(d, 1849, 846, 0, 0)
                lines.append("  solo_bg_render size={} children={} ctex={}".format(
                    r.get_size() if hasattr(r,"get_size") else None,
                    len(getattr(r,"children",[]) or []),
                    type(getattr(r,"cached_texture",None)).__name__,
                ))
                # load
                draw=renpy.display.draw
                if hasattr(draw,"load_all_textures"):
                    draw.load_all_textures(r)
                ctex=getattr(r,"cached_texture",None)
                if ctex is not None:
                    lines.append(f"  solo_bg_ctex {_ht_info(ctex)}")
                else:
                    # walk for HT
                    found=_find_in_render(r, lambda n: isinstance(n, HostTexture))
                    lines.append("  solo_bg_hts=%s"%[(_ht_info(n)) for _,n in found[:3]])
            except Exception as e:
                lines.append(f"  solo_bg FAIL {e}")

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
    os.environ["RENPY_HOST_UI_TRACE"]="1"
    _clear_falsey("RENPY_SKIP_MAIN_MENU"); _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for p in (str(base/"host"/"python"/"gates"), str(base/"host"/"python")):
        if p not in sys.path: sys.path.insert(0,p)
    open("/tmp/hmc_dc_bg_diag.log","w").write("start\n")  # noqa: SIM115
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
    t=threading.Thread(target=_worker, daemon=True); t.start()
    import renpy.main as m
    try: m.main()
    except BaseException as e: _log(f"main exit {e}")

if __name__=="__main__":
    main()
else:
    try: main()
    except Exception:
        traceback.print_exc(); _quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
