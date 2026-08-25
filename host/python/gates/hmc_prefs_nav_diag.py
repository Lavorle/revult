"""Inline diagnostic via renpy-host gate path."""
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


def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write(f"[diag] {m}\n"); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_prefs_diag.log","a").write(m+"\n")

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
    os.environ.setdefault("RENPY_PERFORMANCE_TEST","0")
    gates = str(base/"host/python/gates")
    if gates not in sys.path: sys.path.insert(0, gates)
    import bootstrap as boot
    import renpy_host
    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, _miss, err, _extra = call()
        _log(f"stage {name} {good} {err}")
        if not good:
            _quit(); return
    import renpy
    renpy.host_build = True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e:
        _log(f"main_host {e}")
    import renpy.arguments
    basedir = str(base/"host/playtests/HuangmeiC")
    sys.argv = [sys.argv[0] if sys.argv else "renpy-host", basedir, "run"]
    try:
        if not getattr(renpy.arguments, "commands", None):
            renpy.arguments.register_command("run", renpy.arguments.run, True)
        renpy.game.args = renpy.arguments.bootstrap()
    except Exception as e:
        _log(f"args {e}")
    # stubs
    try:
        import renpy.audio.renpysound_host as h
        sys.modules["renpy.audio.renpysound"]=h
        renpy.audio.renpysound=h
    except Exception: pass
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu.uguu"]=u
    except Exception: pass
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"]=e
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

    def dump_disp(d, depth=0, path="", acc=None, budget=None):
        if acc is None: acc=[]
        if budget is None: budget=[800]
        if d is None or depth>20 or budget[0]<=0: return acc
        budget[0]-=1
        name = type(d).__name__
        extra = ""
        # Transform / ATL
        st = getattr(d, "state", None)
        if st is not None:
            sh = getattr(st, "shader", None)
            mc = getattr(st, "matrixcolor", None)
            ua = getattr(st, "u_animation", None)
            ut = getattr(st, "u_transition", None)
            if sh or mc is not None or ua is not None or ut is not None:
                extra = f" shader={sh!r} mc={type(mc).__name__ if mc is not None else None} u_anim={ua} u_trans={ut}"
        # Model
        if name == "Model" or "Model" in name:
            texs = getattr(d, "textures", None)
            extra += " ntex={} shaders={}".format(len(texs) if texs else 0, getattr(d,"shaders",None))
        # selected flag on button
        if name == "Button" or name.endswith("Button"):
            extra += " selected={} focus={}".format(getattr(d,"selected",None), getattr(d,"focusable",None))
        if extra or name in ("Model","Transform","ATLTransform","Button","ImageReference","Image"):
            acc.append("{}{} {}{}".format("  "*depth, path, name, extra))
        # children
        kids = []
        for attr in ("child","children","offset_children","in_current_store"):
            v = getattr(d, attr, None)
            if v is None: continue
            if attr=="child" and v is not None:
                kids.append(v)
            elif isinstance(v, (list,tuple)):
                for it in v:
                    if isinstance(it, tuple):
                        kids.append(it[0] if it else None)
                    else:
                        kids.append(it)
        # displayable-specific
        if hasattr(d, "displayable") and d.displayable is not None:
            kids.append(d.displayable)
        # screen displayable
        if hasattr(d, "child") and d.child is not None and d.child not in kids:
            kids.append(d.child)
        for i,k in enumerate(kids):
            if k is not None and k is not d:
                dump_disp(k, depth+1, path+"/%d"%i, acc, budget)
        return acc

    def probe():
        deadline=time.time()+40
        while time.time()<deadline:
            try:
                if getattr(renpy.store,"main_menu",None):
                    break
            except Exception: pass
            time.sleep(0.1)
        _log("main_menu ok models={}".format(getattr(renpy.display.render,"models",None)))
        # Check dissolve_transform exists in store
        try:
            dt = getattr(renpy.store, "dissolve_transform", None)
            _log(f"store.dissolve_transform={dt!r} type={type(dt).__name__ if dt else None}")
        except Exception as e:
            _log(f"store.dissolve_transform err {e}")
        # uniforms registered?
        try:
            import renpy.display.transform as tf
            _log("transform.uniforms has u_animation={} u_transition={} all={}".format(
                "u_animation" in tf.uniforms, "u_transition" in tf.uniforms, sorted(tf.uniforms)[:30]))
        except Exception as e:
            _log(f"uniforms err {e}")
        # shader_part
        try:
            from renpy.gl2.gl2shadercache import shader_part
            _log("shader_part image_dissolve={} keys_sample={}".format(
                "image_dissolve" in shader_part, [k for k in shader_part if "dissolve" in k or "matrix" in k][:20]))
        except Exception as e:
            _log(f"shader_part err {e}")

        # Show preferences
        try:
            renpy.store.Show("preferences", kind="sound_config")()
            renpy.restart_interaction()
        except Exception as e:
            _log(f"Show fail {e}")
            try:
                renpy.display.screen.show_screen("preferences", kind="sound_config")
            except Exception as e2:
                _log(f"show_screen fail {e2}")
        time.sleep(0.5)
        for _ in range(5):
            try:
                import interact_helpers as ih
                ready,_why,iface=ih.interface_ready()
                if ready and iface:
                    root=ih._rebuild_product_root(iface)
                    if root is not None:
                        w=int(getattr(renpy.config,"screen_width",1920) or 1920)
                        h=int(getattr(renpy.config,"screen_height",1080) or 1080)
                        st=renpy.display.render.render_screen(root,w,h)
                        renpy.display.draw.draw_screen(st, flip=True)
                        iface.surftree=st
            except Exception as e:
                _log(f"redraw {e}")
            time.sleep(0.05)

        scr = renpy.display.screen.get_screen("preferences")
        _log("prefs screen=%s" % (type(scr).__name__ if scr else None))
        # screen scope kind
        try:
            scope = getattr(scr, "scope", None) or {}
            _log("scope.kind={!r} keys={}".format(scope.get("kind"), list(scope.keys())[:20] if isinstance(scope,dict) else None))
        except Exception as e:
            _log(f"scope err {e}")
        try:
            raw = getattr(scr, "raw_child", None) or getattr(scr, "child", None)
            _log("screen.child type=%s" % (type(raw).__name__ if raw else None))
            lines = dump_disp(raw)
            for ln in lines[:120]:
                _log("D "+ln)
            _log("displayable dump n=%d" % len(lines))
        except Exception as e:
            _log(f"dump err {e}")
            _log(traceback.format_exc())

        # Try calling dissolve_transform and rendering it
        try:
            dt = renpy.store.dissolve_transform(
                old="gui/preferences/common/navigation_idle.png",
                new="gui/preferences/common/navigation_selected.png",
                rule="images/rule/00.png",
            )
            _log("dt instance type={} child={}".format(type(dt).__name__, type(getattr(dt,"child",None)).__name__ if getattr(dt,"child",None) else None))
            st = getattr(dt, "state", None)
            _log("dt.state shader={!r} u_anim={} u_trans={}".format(
                getattr(st,"shader",None), getattr(st,"u_animation",None), getattr(st,"u_transition",None)))
            # force update
            try:
                dt._update(0.5, 0.5, 179, 64)
            except Exception as e:
                _log(f"dt._update {e}")
            try:
                rv = renpy.display.render.render(dt, 179, 64, 0.5, 0.5)
                _log("dt.render type={} size={} shaders={} uniforms={} mesh={} children={}".format(
                    type(rv).__name__,
                    (getattr(rv,"width",None), getattr(rv,"height",None)),
                    getattr(rv,"shaders",None),
                    list((getattr(rv,"uniforms",None) or {}).keys()) if isinstance(getattr(rv,"uniforms",None),dict) else getattr(rv,"uniforms",None),
                    type(getattr(rv,"mesh",None)).__name__ if getattr(rv,"mesh",None) is not None else None,
                    len(getattr(rv,"children",None) or ()),
                ))
                # walk children
                for i,(ch,x,y) in enumerate(getattr(rv,"children",None) or ()):
                    _log("  child%d type=%s mesh=%s shaders=%s nch=%s at=%s,%s" % (
                        i, type(ch).__name__,
                        type(getattr(ch,"mesh",None)).__name__ if getattr(ch,"mesh",None) is not None else getattr(ch,"mesh",None),
                        getattr(ch,"shaders",None),
                        len(getattr(ch,"children",None) or ()),
                        x,y))
            except Exception as e:
                _log(f"dt.render FAIL {e}")
                _log(traceback.format_exc())
        except Exception as e:
            _log(f"dt call FAIL {e}")
            _log(traceback.format_exc())

        # Full RT yellow scan (scale-aware)
        try:
            rw,rh,rt = renpy_host.read_game_rt_rgba()
            yellow=0; n=0; 
            for y in range(0,rh,2):
                for x in range(0,rw,2):
                    o=(y*rw+x)*4
                    r,g,b,a=rt[o],rt[o+1],rt[o+2],rt[o+3]
                    if a<40: continue
                    n+=1
                    if r>180 and g>140 and b<90:
                        yellow+=1
            _log("full_rt %dx%d yellow_frac=%.5f yellow=%d n=%d" % (rw,rh, yellow/float(n) if n else -1, yellow, n))
            # sample scaled tab0 (image_config is default selected if kind failed)
            sx,sy = rw/1920.0, rh/1080.0
            for ti, name in enumerate(["image_config","game_config_1","text_config","sound_config"]):
                x0=int((462+ti*179)*sx); y0=int(47*sy)
                x1=int((462+(ti+1)*179)*sx); y1=int((47+64)*sy)
                ys=ns=0; rs=gs=bs=0
                for y in range(y0,y1,1):
                    for x in range(x0,x1,1):
                        o=(y*rw+x)*4
                        r,g,b,a=rt[o],rt[o+1],rt[o+2],rt[o+3]
                        if a<40: continue
                        ns+=1; rs+=r; gs+=g; bs+=b
                        if r>180 and g>140 and b<90: ys+=1
                _log("tab[%s] mean=(%.1f,%.1f,%.1f) yfrac=%.4f n=%d rect=%s" % (
                    name,
                    rs/ns if ns else 0, gs/ns if ns else 0, bs/ns if ns else 0,
                    ys/float(ns) if ns else -1, ns, (x0,y0,x1,y1)))
        except Exception as e:
            _log(f"rt scan {e}")

        time.sleep(0.2)
        _quit()

    threading.Thread(target=probe, daemon=True).start()
    import renpy.main as m
    try:
        m.main()
    except BaseException as e:
        _log(f"main exit {e}")

if __name__=="__main__":
    run()

run()

# ----------------------------------------------------------------------
# HARNESS MIGRATION (thin wrapper, original logic preserved above)
# ----------------------------------------------------------------------
# Migration path for hmc_prefs_nav_diag:
#   1. Keep all helpers/classes above untouched (header license preserved).
#   2. Extract the body of main()/run()/probe into _harness_run_one(case):
#        def _harness_run_one(case):
#            # case: dict with {"tab": "text_config"}
#            # ... reuse helpers above (WgpuDraw / FakeRender / _mean_rgb ...)
#            # w, h, rgba = renpy_host.read_game_rt_rgba()
#            # return w, h, rgba   # or (ok, msg)
#   3. Define golden_compare delegating to golden_mae or custom mean check:
#        def _harness_golden_compare(w, h, rgba):
#            from golden_mae import compare_or_bootstrap
#            return compare_or_bootstrap("hmc_prefs_nav_diag", w, h, rgba)
#            # or custom: mr/mg/mb = _mean_rgb(rgba,w,h); return (ok,msg)
#   4. Wire via harness (opt-in via RENPY_HOST_HARNESS=1 to keep default run unchanged):
#        if parametrized_gate is not None:
#            @parametrized_gate("hmc_prefs_nav_diag", [{"tab": "text_config"}])
#            def _parametrized_case(case):
#                w, h, rgba = _harness_run_one(case)
#                return _harness_golden_compare(w, h, rgba)
#        def _harness_main():
#            import os as _os
#            if gate_harness is not None and _os.environ.get("RENPY_HOST_HARNESS") == "1":
#                cases = [{"tab": "text_config"}]
#                ok = gate_harness("hmc_prefs_nav_diag", cases, _harness_run_one, _harness_golden_compare)
#                raise SystemExit(0 if ok else 1)
#            else:
#                main()  # or run() — original path
#        if __name__ == "__main__":
#            _harness_main()
#
# Notes: product prefs nav diag uses bootstrap stages + probe thread sampling RT regions; _harness_run_one would wrap _base() + bootstrap + main_thread/probe split.
# Original code above is untouched; this block is documentation + ready-to-enable
# wrapper ensuring `python -m py_compile` stays green.
# To fully migrate, move the `main()`/`run()` call into `_harness_main` and
# gate on RENPY_HOST_HARNESS as shown.

