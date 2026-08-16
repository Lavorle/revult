import os, sys, time, threading, traceback
from pathlib import Path

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write("[diag3] %s\n" % m); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_prefs_diag3.log","a").write(m+"\n")

def _quit():
    try:
        import renpy_host; renpy_host.request_quit()
    except Exception:
        pass

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

    # Monkeypatch RenderTransform to log exceptions
    import renpy_display_accelerator_host as acc
    _orig = acc.RenderTransform.render
    def _wrap(self, width, height, st, at):
        try:
            return _orig(self, width, height, st, at)
        except Exception:
            _log("RT outer exc: " + traceback.format_exc())
            raise
    acc.RenderTransform.render = _wrap

    # Also patch child render path by wrapping renpy.display.render.render temporarily in probe

    def probe():
        deadline=time.time()+40
        while time.time()<deadline:
            try:
                if getattr(renpy.store,"main_menu",None): break
            except Exception: pass
            time.sleep(0.1)
        try:
            renpy.store.Show("preferences", kind="sound_config")()
        except Exception as e:
            _log("Show %s"%e)
        time.sleep(0.5)
        import interact_helpers as ih
        for _ in range(5):
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

        scr = renpy.display.screen.get_screen("preferences")
        def find_dt(d, depth=0):
            if d is None or depth>15: return None
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
        raw = getattr(scr,"child",None)
        dt = find_dt(raw)
        _log("dt=%s child=%s" % (type(dt).__name__ if dt else None, type(getattr(dt,"child",None)).__name__ if dt and getattr(dt,"child",None) else None))
        if dt is None:
            _quit(); return
        child = dt.child
        # Direct Model.render
        try:
            _log("models=%s" % renpy.display.render.models)
            mrv = child.render(179, 64, 1.0, 1.0)
            _log("Model.render ok mesh=%s nch=%s sh=%s" % (
                type(getattr(mrv,"mesh",None)).__name__,
                len(getattr(mrv,"children",()) or ()),
                getattr(mrv,"shaders",None)))
        except Exception:
            _log("Model.render FAIL:\n" + traceback.format_exc())
        # Direct via renpy.display.render.render
        try:
            mrv = renpy.display.render.render(child, 179, 64, 1.0, 1.0)
            _log("render(Model) ok mesh=%s nch=%s size=%s" % (
                type(getattr(mrv,"mesh",None)).__name__ if getattr(mrv,"mesh",None) is not None else None,
                len(getattr(mrv,"children",()) or ()),
                (mrv.width, mrv.height)))
        except Exception:
            _log("render(Model) FAIL:\n" + traceback.format_exc())
        # Instrument host RT child path
        import renpy_display_accelerator_host as acc2
        rt = acc2.RenderTransform(dt)
        # manual steps
        try:
            from renpy.display.render import render as renpy_render
            cr = renpy_render(child, 179, 64, 1.0, 1.0)
            _log("manual cr type=%s w=%s h=%s mesh=%s nch=%s" % (
                type(cr).__name__, cr.width, cr.height,
                type(getattr(cr,"mesh",None)).__name__ if getattr(cr,"mesh",None) is not None else None,
                len(cr.children or ())))
        except Exception:
            _log("manual cr FAIL:\n" + traceback.format_exc())
        try:
            rv = rt.render(179, 64, 1.0, 1.0)
            _log("RT.render shaders=%s uni=%s nch=%s size=%s cr=%s" % (
                getattr(rv,"shaders",None),
                list((getattr(rv,"uniforms") or {}).keys()) if isinstance(getattr(rv,"uniforms",None),dict) else None,
                len(getattr(rv,"children",()) or ()),
                (rv.width, rv.height),
                type(getattr(rt,"cr",None)).__name__ if getattr(rt,"cr",None) else None,
            ))
            # state at render time
            stt = dt.state
            _log("state during: shader=%r alpha=%s u_anim=%s" % (
                getattr(stt,"shader",None), getattr(stt,"alpha",None), getattr(stt,"u_animation",None)))
        except Exception:
            _log("RT.render FAIL:\n" + traceback.format_exc())

        # Check _empty path: width of empty?
        try:
            # Force exception path by temporarily breaking Model
            pass
        except Exception:
            pass

        # Colorize full pack
        CM = renpy.store.ColorizeMatrix
        cm = CM("#ffde00","#ffde00")(None, 1.0)
        floats = renpy.display.draw._matrix_to_floats(cm)
        _log("Colorize full pack %s" % floats)
        # expected: bias in col3
        _log("cm fields xdx=%s ydx=%s zdx=%s wdx=%s xdy=%s ydy=%s xdw=%s ydw=%s zdw=%s wdw=%s" % (
            cm.xdx, cm.ydx, cm.zdx, cm.wdx, cm.xdy, cm.ydy, cm.xdw, cm.ydw, cm.zdw, cm.wdw))

        time.sleep(0.2)
        _quit()

    threading.Thread(target=probe, daemon=True).start()
    import renpy.main as m
    try:
        m.main()
    except BaseException as e:
        _log("main exit %s" % e)

run()
