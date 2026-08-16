import os, sys, time, threading, traceback
from pathlib import Path

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write("[diag2] %s\n" % m); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_prefs_diag2.log","a").write(m+"\n")

def _quit():
    try:
        import renpy_host; renpy_host.request_quit()
    except Exception:
        pass

def walk_rt(node, depth=0, path="", acc=None, budget=None):
    if acc is None: acc=[]
    if budget is None: budget=[500]
    if node is None or depth>30 or budget[0]<=0: return acc
    budget[0]-=1
    sh = getattr(node, "shaders", None)
    uni = getattr(node, "uniforms", None)
    mesh = getattr(node, "mesh", None)
    cm = getattr(node, "cached_model", None)
    kids = list(getattr(node, "children", None) or ())
    interesting = False
    if sh or (isinstance(uni, dict) and ("u_animation" in uni or "u_renpy_matrixcolor" in uni or "u_transition" in uni)):
        interesting = True
    if mesh is not None and mesh is not False:
        interesting = True
    if cm is not None:
        interesting = True
    if interesting:
        acc.append({
            "path": path,
            "type": type(node).__name__,
            "shaders": list(sh) if sh else None,
            "uni": sorted(list(uni.keys())) if isinstance(uni, dict) else None,
            "u_anim": uni.get("u_animation") if isinstance(uni, dict) else None,
            "mesh_t": type(mesh).__name__ if mesh is not None else None,
            "mesh_true": mesh is True,
            "has_points": hasattr(mesh, "get_points") if mesh is not None else False,
            "cm": type(cm).__name__ if cm is not None else None,
            "cm_shaders": list(getattr(cm,"shaders",None) or []) if cm is not None else None,
            "cm_ntex": len(getattr(cm,"textures",None) or []) if cm is not None else None,
            "nch": len(kids),
            "size": (getattr(node,"width",None), getattr(node,"height",None)),
        })
    for i, it in enumerate(kids):
        ch = it[0] if isinstance(it, tuple) else it
        walk_rt(ch, depth+1, path+"/%d"%i, acc, budget)
    return acc

def run():
    base = _base()
    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD","1")
    os.environ.setdefault("RENPY_HOST_GAME", str(base/"host/playtests/HuangmeiC"))
    os.environ["RENPY_SKIP_SPLASHSCREEN"]="1"
    gates = str(base/"host/python/gates")
    if gates not in sys.path: sys.path.insert(0, gates)
    import renpy_host, bootstrap as boot
    for call in (boot.stage_import_renpy, boot.stage_import_all, lambda: boot.stage_set_game_dir(base)):
        good, miss, err, extra = call()
        _log("stage %s %s" % (good, err))
        if not good: _quit(); return
    import renpy
    renpy.host_build = True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e:
        _log("main_host %s"%e)
    import renpy.arguments
    basedir = str(base/"host/playtests/HuangmeiC")
    sys.argv = [sys.argv[0] if sys.argv else "x", basedir, "run"]
    try:
        if not getattr(renpy.arguments, "commands", None):
            renpy.arguments.register_command("run", renpy.arguments.run, True)
        renpy.game.args = renpy.arguments.bootstrap()
    except Exception as e:
        _log("args %s"%e)
    try:
        import renpy.audio.renpysound_host as h
        sys.modules["renpy.audio.renpysound"]=h; renpy.audio.renpysound=h
    except Exception: pass
    try:
        import renpy_uguu_host as u; sys.modules["renpy.uguu.uguu"]=u
    except Exception: pass
    try:
        import renpy_ecsign_host as e; sys.modules["renpy.ecsign"]=e
    except Exception: pass
    try:
        import host_pygame, host_pygame.locals as loc
        if not hasattr(host_pygame,"constants"): host_pygame.constants=loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        import renpy.pygame as rpg
        if not hasattr(rpg,"constants"): rpg.constants=host_pygame.constants
        try: rpg.import_as_pygame()
        except Exception: pass
    except Exception: pass

    def probe():
        deadline=time.time()+40
        while time.time()<deadline:
            try:
                if getattr(renpy.store,"main_menu",None): break
            except Exception: pass
            time.sleep(0.1)
        _log("main_menu models=%s" % getattr(renpy.display.render,"models",None))
        try:
            renpy.store.Show("preferences", kind="sound_config")()
        except Exception as e:
            _log("Show %s"%e)
        time.sleep(0.6)
        # redraw
        import interact_helpers as ih
        for _ in range(10):
            try:
                ready,why,iface=ih.interface_ready()
                if ready and iface:
                    root=ih._rebuild_product_root(iface)
                    w=int(getattr(renpy.config,"screen_width",1920) or 1920)
                    h=int(getattr(renpy.config,"screen_height",1080) or 1080)
                    st=renpy.display.render.render_screen(root,w,h)
                    renpy.display.draw.draw_screen(st, flip=True)
                    iface.surftree=st
            except Exception as e:
                _log("redraw %s"%e)
            time.sleep(0.05)

        iface = renpy.display.interface
        st = getattr(iface, "surftree", None)
        hits = walk_rt(st)
        _log("surftree hits n=%d" % len(hits))
        for h in hits:
            if (h.get("shaders") and any("dissolve" in str(s) for s in h["shaders"])) or h.get("u_anim") is not None or (h.get("mesh_t") and h["mesh_t"]!="NoneType") or (h.get("cm_ntex") or 0)>=2:
                _log("HIT %s" % h)

        # Find selected button ATL and render it alone
        scr = renpy.display.screen.get_screen("preferences")
        def find_sel(d, path=""):
            if d is None: return None
            if type(d).__name__=="Button" and getattr(d,"selected",None):
                return d, path
            kids=[]
            for attr in ("child","children"):
                v=getattr(d,attr,None)
                if v is None: continue
                if attr=="child": kids.append(v)
                elif isinstance(v,(list,tuple)):
                    for it in v:
                        kids.append(it[0] if isinstance(it,tuple) else it)
            for i,k in enumerate(kids):
                r=find_sel(k, path+"/%d"%i)
                if r: return r
            return None
        raw = getattr(scr,"child",None) or getattr(scr,"raw_child",None)
        found = find_sel(raw)
        if not found:
            _log("no selected button")
        else:
            btn, path = found
            _log("selected button path=%s" % path)
            # find dissolve ATL child
            def find_dt(d, depth=0):
                if d is None or depth>10: return None
                st=getattr(d,"state",None)
                if st is not None and getattr(st,"shader",None)=="image_dissolve":
                    return d
                kids=[]
                for attr in ("child","children"):
                    v=getattr(d,attr,None)
                    if v is None: continue
                    if attr=="child": kids.append(v)
                    elif isinstance(v,(list,tuple)):
                        for it in v:
                            kids.append(it[0] if isinstance(it,tuple) else it)
                for k in kids:
                    r=find_dt(k, depth+1)
                    if r: return r
                return None
            dt = find_dt(btn)
            _log("dt=%s" % (type(dt).__name__ if dt else None))
            if dt is not None:
                stt = dt.state
                _log("dt.state shader=%r u_anim=%s child=%s" % (
                    getattr(stt,"shader",None), getattr(stt,"u_animation",None),
                    type(getattr(dt,"child",None)).__name__ if getattr(dt,"child",None) else None))
                try:
                    rv = renpy.display.render.render(dt, 179, 64, 1.0, 1.0)
                    _log("dt.rv shaders=%s uniforms=%s mesh=%s nch=%s size=%s" % (
                        getattr(rv,"shaders",None),
                        list((getattr(rv,"uniforms") or {}).keys()) if isinstance(getattr(rv,"uniforms",None),dict) else None,
                        type(getattr(rv,"mesh",None)).__name__ if getattr(rv,"mesh",None) is not None else None,
                        len(getattr(rv,"children",None) or ()),
                        (rv.width, rv.height),
                    ))
                    for i,(ch,x,y) in enumerate(getattr(rv,"children",None) or ()):
                        _log("  ch%d type=%s mesh=%s sh=%s nch=%s size=%s at=%.1f,%.1f" % (
                            i, type(ch).__name__,
                            type(getattr(ch,"mesh",None)).__name__ if getattr(ch,"mesh",None) is not None else getattr(ch,"mesh",None),
                            getattr(ch,"shaders",None),
                            len(getattr(ch,"children",None) or ()),
                            (getattr(ch,"width",None), getattr(ch,"height",None)),
                            x,y))
                        for j,(gc,gx,gy) in enumerate(list(getattr(ch,"children",None) or ())[:5]):
                            _log("    gc%d type=%s mesh=%s size=%s" % (
                                j, type(gc).__name__,
                                type(getattr(gc,"mesh",None)).__name__ if getattr(gc,"mesh",None) is not None else getattr(gc,"mesh",None),
                                (getattr(gc,"width",None), getattr(gc,"height",None))))
                    # Draw ONLY this rv via WgpuDraw
                    draw = renpy.display.draw
                    draw.load_all_textures(rv)
                    # clear-ish: draw into game RT via draw_screen of a synthetic root?
                    # Use _draw_node after begin_frame
                    import renpy_host
                    renpy_host.begin_frame()
                    try:
                        draw._draw_node(rv, 462+179*4, 47)  # sound_config pos
                    except Exception as e:
                        _log("draw_node exc %s" % e)
                        _log(traceback.format_exc())
                    renpy_host.end_frame_present()
                    rw,rh,rt = renpy_host.read_game_rt_rgba()
                    # sample sound_config band
                    x0,y0,x1,y1 = 462+179*4, 47, 462+179*5, 47+64
                    ys=n=0; rs=gs=bs=0
                    for y in range(y0,y1,1):
                        for x in range(x0,x1,1):
                            o=(y*rw+x)*4
                            r,g,b,a=rt[o],rt[o+1],rt[o+2],rt[o+3]
                            if a<40: continue
                            n+=1; rs+=r; gs+=g; bs+=b
                            if r>180 and g>140 and b<90: ys+=1
                    _log("solo_draw tab sound mean=(%.1f,%.1f,%.1f) yfrac=%.4f n=%d" % (
                        rs/n if n else 0, gs/n if n else 0, bs/n if n else 0,
                        ys/float(n) if n else -1, n))
                except Exception as e:
                    _log("render/draw FAIL %s" % e)
                    _log(traceback.format_exc())

        # matrixcolor pack check for Identity
        try:
            from renpy.common import _matrixcolor
        except Exception:
            pass
        try:
            # IdentityMatrix
            IM = renpy.store.IdentityMatrix
            CM = renpy.store.ColorizeMatrix
            im = IM()(None, 1.0)
            cm = CM("#ffde00","#ffde00")(None, 1.0)
            _log("Identity matrix fields xdx=%s ydy=%s xdw=%s" % (im.xdx, im.ydy, im.xdw))
            _log("Colorize matrix xdx=%s ydy=%s xdw=%s ydw=%s" % (cm.xdx, cm.ydy, cm.xdw, cm.ydw))
            draw = renpy.display.draw
            _log("pack Identity %s" % draw._matrix_to_floats(im)[:8])
            _log("pack Colorize %s" % draw._matrix_to_floats(cm)[:8])
        except Exception as e:
            _log("matrix pack %s" % e)
            _log(traceback.format_exc())

        time.sleep(0.2)
        _quit()

    threading.Thread(target=probe, daemon=True).start()
    import renpy.main as m
    try:
        m.main()
    except BaseException as e:
        _log("main exit %s" % e)

run()
