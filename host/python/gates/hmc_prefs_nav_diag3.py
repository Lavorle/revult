import os
import sys
import threading
import time
import traceback
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


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write(f"[diag3] {m}\n"); sys.__stdout__.flush()
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
    import bootstrap as boot
    for call in (boot.stage_import_renpy, boot.stage_import_all, lambda: boot.stage_set_game_dir(base)):
        good, _miss, _err, _extra = call()
        if not good: _quit(); return
    import renpy
    renpy.host_build = True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e:
        _log(f"main_host {e}")
    import renpy.arguments
    basedir = str(base/"host/playtests/HuangmeiC")
    sys.argv = [sys.argv[0] if sys.argv else "x", basedir, "run"]
    try:
        if not getattr(renpy.arguments, "commands", None):
            renpy.arguments.register_command("run", renpy.arguments.run, True)
        renpy.game.args = renpy.arguments.bootstrap()
    except Exception as e:
        _log(f"args {e}")
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
        import host_pygame
        import host_pygame.locals as loc
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
            _log(f"Show {e}")
        time.sleep(0.5)
        import interact_helpers as ih
        for _ in range(5):
            try:
                ready,_why,iface=ih.interface_ready()
                if ready and iface:
                    root=ih._rebuild_product_root(iface)
                    w=int(getattr(renpy.config,"screen_width",1920) or 1920)
                    h=int(getattr(renpy.config,"screen_height",1080) or 1080)
                    st=renpy.display.render.render_screen(root,w,h)
                    renpy.display.draw.draw_screen(st, flip=True)
                    iface.surftree=st
            except Exception as e:
                _log(f"redraw {e}")
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
        _log("dt={} child={}".format(type(dt).__name__ if dt else None, type(getattr(dt,"child",None)).__name__ if dt and getattr(dt,"child",None) else None))
        if dt is None:
            _quit(); return
        child = dt.child
        # Direct Model.render
        try:
            _log(f"models={renpy.display.render.models}")
            mrv = child.render(179, 64, 1.0, 1.0)
            _log("Model.render ok mesh={} nch={} sh={}".format(
                type(getattr(mrv,"mesh",None)).__name__,
                len(getattr(mrv,"children",()) or ()),
                getattr(mrv,"shaders",None)))
        except Exception:
            _log("Model.render FAIL:\n" + traceback.format_exc())
        # Direct via renpy.display.render.render
        try:
            mrv = renpy.display.render.render(child, 179, 64, 1.0, 1.0)
            _log("render(Model) ok mesh={} nch={} size={}".format(
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
            _log("manual cr type={} w={} h={} mesh={} nch={}".format(
                type(cr).__name__, cr.width, cr.height,
                type(getattr(cr,"mesh",None)).__name__ if getattr(cr,"mesh",None) is not None else None,
                len(cr.children or ())))
        except Exception:
            _log("manual cr FAIL:\n" + traceback.format_exc())
        try:
            rv = rt.render(179, 64, 1.0, 1.0)
            _log("RT.render shaders={} uni={} nch={} size={} cr={}".format(
                getattr(rv,"shaders",None),
                list((rv.uniforms or {}).keys()) if isinstance(getattr(rv,"uniforms",None),dict) else None,
                len(getattr(rv,"children",()) or ()),
                (rv.width, rv.height),
                type(getattr(rt,"cr",None)).__name__ if getattr(rt,"cr",None) else None,
            ))
            # state at render time
            stt = dt.state
            _log("state during: shader={!r} alpha={} u_anim={}".format(
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
        _log(f"Colorize full pack {floats}")
        # expected: bias in col3
        _log(f"cm fields xdx={cm.xdx} ydx={cm.ydx} zdx={cm.zdx} wdx={cm.wdx} xdy={cm.xdy} ydy={cm.ydy} xdw={cm.xdw} ydw={cm.ydw} zdw={cm.zdw} wdw={cm.wdw}")

        time.sleep(0.2)
        _quit()

    threading.Thread(target=probe, daemon=True).start()
    import renpy.main as m
    try:
        m.main()
    except BaseException as e:
        _log(f"main exit {e}")

run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

