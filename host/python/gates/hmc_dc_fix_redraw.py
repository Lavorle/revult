"""Targeted: Fixed displayable children vs Render children for preferences_layout.

Also tests process_redraws before render_screen.
"""
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
        sys.__stdout__.write("[dc_fix] %s\n" % m); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_dc_fix_redraw.log", "a").write(m + "\n")

def _quit():
    try:
        import renpy_host; renpy_host.request_quit()
    except Exception:
        pass

def _clear_falsey(n):
    v = os.environ.get(n)
    if v is not None and str(v).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(n, None)

def _pre():
    import types
    try:
        import renpy.audio.renpysound_host as h, renpy.audio as a
        sys.modules["renpy.audio.renpysound"] = h; a.renpysound = h
    except Exception as e:
        _log("sound %s" % e)
    try:
        import host_pygame, host_pygame.locals as loc, host_pygame.scrap as scrap
        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"] = scrap
        sys.modules["pygame.scrap"] = scrap
        import renpy.pygame as rpg
        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        try: rpg.scrap = scrap
        except Exception: pass
        try: rpg.import_as_pygame()
        except Exception: pass
    except Exception as e:
        _log("pygame %s" % e)
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu.uguu"] = u; sys.modules["renpy.uguu.gl"] = u
        pkg = sys.modules.get("renpy.uguu") or types.ModuleType("renpy.uguu"); pkg.__path__ = []
        sys.modules["renpy.uguu"] = pkg
        for n in dir(u):
            if n.startswith("GL_") or n in ("clear_errors", "get_error"):
                setattr(pkg, n, getattr(u, n))
        pkg.uguu = u; pkg.gl = u
        import renpy; renpy.uguu = pkg
    except Exception as e:
        _log("uguu %s" % e)
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"] = e
        import renpy; renpy.ecsign = e
    except Exception as e:
        _log("ecsign %s" % e)

def _find_fixed_1849(d, acc=None, budget=None, depth=0, path=""):
    if acc is None:
        acc = []
    if budget is None:
        budget = [3000]
    if d is None or budget[0] <= 0 or depth > 30 or len(acc) >= 8:
        return acc
    budget[0] -= 1
    try:
        t = type(d).__name__
        style = getattr(d, "style", None)
        xs = ys = None
        if style is not None:
            try:
                xs = style["xsize"]
            except Exception:
                try:
                    xs = style.xsize
                except Exception:
                    pass
            try:
                ys = style["ysize"]
            except Exception:
                try:
                    ys = style.ysize
                except Exception:
                    pass
        ch = list(getattr(d, "children", None) or [])
        # match layout fixed by size or by having background-like child
        is_layout = False
        if xs == 1849 or ys == 846:
            is_layout = True
        for c in ch:
            try:
                r = repr(c)
                if "background.png" in r:
                    is_layout = True
            except Exception:
                pass
        if is_layout or (t in ("Fixed", "MultiBox") and len(ch) >= 2 and depth >= 1):
            kids = []
            for i, c in enumerate(ch[:12]):
                try:
                    kids.append((i, type(c).__name__, repr(c)[:120]))
                except Exception as e:
                    kids.append((i, "?", str(e)))
            if xs == 1849 or ys == 846 or any("background" in (k[2] or "") for k in kids):
                acc.append({"path": path, "type": t, "xsize": xs, "ysize": ys, "nch": len(ch), "kids": kids, "id": id(d)})
    except Exception as e:
        pass
    # visit
    try:
        if hasattr(d, "visit"):
            for i, c in enumerate(d.visit() or []):
                _find_fixed_1849(c, acc, budget, depth + 1, path + "/v%d" % i)
    except Exception:
        pass
    try:
        for i, c in enumerate(list(getattr(d, "children", None) or [])[:40]):
            _find_fixed_1849(c, acc, budget, depth + 1, path + "/c%d" % i)
    except Exception:
        pass
    for attr in ("child",):
        try:
            v = getattr(d, attr, None)
            if v is not None:
                _find_fixed_1849(v, acc, budget, depth + 1, path + "/" + attr)
        except Exception:
            pass
    return acc

def _render_children_brief(node):
    from renpy.wgpu.draw import HostTexture
    ch = getattr(node, "children", None) or []
    out = []
    for i, c in enumerate(list(ch)[:10]):
        child = c[0] if isinstance(c, (list, tuple)) else c
        off = (c[1], c[2]) if isinstance(c, (list, tuple)) and len(c) > 2 else None
        w = getattr(child, "width", None) or getattr(child, "w", None)
        h = getattr(child, "height", None) or getattr(child, "h", None)
        try:
            if hasattr(child, "get_size"):
                w, h = child.get_size()
        except Exception:
            pass
        ht = None
        if isinstance(child, HostTexture):
            ht = (int(child.handle), int(child.w), int(child.h))
        ct = getattr(child, "cached_texture", None)
        if isinstance(ct, HostTexture):
            ht = ("ct", int(ct.handle), int(ct.w), int(ct.h))
        out.append({"i": i, "off": off, "t": type(child).__name__, "w": w, "h": h, "ht": ht, "nch": len(getattr(child, "children", None) or [])})
    return out

def _sample_panel():
    import renpy_host
    rw, rh, rt = renpy_host.read_game_rt_rgba()
    if not rw:
        return None
    # panel center
    x0, y0 = rw // 2 - 40, rh // 2 - 40
    rs = gs = bs = n = 0
    dark = 0
    for y in range(y0, y0 + 80, 4):
        for x in range(x0, x0 + 80, 4):
            o = (y * rw + x) * 4
            r, g, b = rt[o], rt[o + 1], rt[o + 2]
            rs += r; gs += g; bs += b; n += 1
            if r < 25 and g < 25 and b < 30:
                dark += 1
    return {"mean": (rs / n, gs / n, bs / n), "dark": dark / n}

def _force_update_screens():
    import renpy
    # Clear updated_screens so ScreenDisplayable.update rebuilds
    try:
        renpy.display.screen.updated_screens.clear()
    except Exception:
        pass
    # Force each shown screen to update
    try:
        sls = renpy.display.core.scene_lists()
    except Exception:
        try:
            sls = renpy.game.context().scene_lists
        except Exception:
            sls = None
    updated = []
    if sls is not None:
        for layer in list(getattr(renpy.config, "layers", []) or []):
            try:
                # get all displayables on layer
                for d in list(getattr(sls, "layers", {}).get(layer, []) or []):
                    # scene list entries may be tuples
                    disp = d
                    if isinstance(d, (list, tuple)) and d:
                        disp = d[0]
                    # walk for ScreenDisplayable
                    try:
                        from renpy.display.screen import ScreenDisplayable
                        if isinstance(disp, ScreenDisplayable):
                            try:
                                renpy.display.screen.updated_screens.discard(disp)
                            except Exception:
                                pass
                            disp.update()
                            updated.append(getattr(disp, "screen_name", None) or getattr(disp, "tag", None))
                    except Exception as e:
                        pass
            except Exception:
                pass
    # Also get_screen preferences
    try:
        scr = renpy.display.screen.get_screen("preferences")
        if scr is not None:
            renpy.display.screen.updated_screens.discard(scr)
            scr.update()
            updated.append("preferences_direct")
    except Exception as e:
        updated.append("pref_err:%s" % e)
    return updated

def _redraw(do_process_redraws=True, do_update=True):
    import renpy, interact_helpers as ih
    ready, why, iface = ih.interface_ready()
    if not ready:
        return "iface:" + why, None, {}
    meta = {}
    if do_update:
        meta["updated"] = _force_update_screens()
    if do_process_redraws:
        try:
            meta["process_redraws"] = bool(renpy.display.render.process_redraws())
        except Exception as e:
            meta["process_redraws_err"] = str(e)
    # Dump Fixed displayable before render
    try:
        scr = renpy.display.screen.get_screen("preferences")
        meta["scr_child"] = type(getattr(scr, "child", None)).__name__ if scr else None
        if scr is not None:
            if getattr(scr, "child", None) is None:
                try:
                    renpy.display.screen.updated_screens.discard(scr)
                    scr.update()
                except Exception as e:
                    meta["scr_update_err"] = str(e)
            fixes = _find_fixed_1849(scr)
            meta["fixed_disp"] = fixes[:4]
    except Exception as e:
        meta["fixed_disp_err"] = str(e)

    root = ih._rebuild_product_root(iface)
    w = int(getattr(renpy.config, "screen_width", 1920) or 1920)
    h = int(getattr(renpy.config, "screen_height", 1080) or 1080)
    st = renpy.display.render.render_screen(root, w, h)
    # Find 1849 render nodes
    found = []
    budget = [4000]
    def walk(n, depth=0):
        if n is None or budget[0] <= 0 or depth > 40:
            return
        budget[0] -= 1
        ww = getattr(n, "width", None)
        hh = getattr(n, "height", None)
        try:
            if hasattr(n, "get_size"):
                ww, hh = n.get_size()
        except Exception:
            pass
        try:
            if ww is not None and hh is not None and abs(float(ww) - 1849) < 1 and abs(float(hh) - 846) < 1:
                found.append(n)
        except Exception:
            pass
        try:
            for c in getattr(n, "children", None) or []:
                child = c[0] if isinstance(c, (list, tuple)) else c
                walk(child, depth + 1)
        except Exception:
            pass
        for attr in ("cached_texture",):
            try:
                v = getattr(n, attr, None)
                if v is not None:
                    walk(v, depth + 1)
            except Exception:
                pass
    walk(st)
    meta["n_1849_renders"] = len(found)
    if found:
        meta["r1849_children"] = _render_children_brief(found[0])
        meta["r1849_nch"] = len(getattr(found[0], "children", None) or [])

    draw = renpy.display.draw
    try:
        if hasattr(draw, "load_all_textures"):
            draw.load_all_textures(st)
    except Exception as e:
        return "load:" + str(e), st, meta
    # Count draw cmds if possible
    try:
        import renpy_host
        meta["in_frame_before"] = renpy_host.in_frame() if hasattr(renpy_host, "in_frame") else None
    except Exception:
        pass
    draw.draw_screen(st, flip=True)
    try:
        import renpy_host
        meta["in_frame_after"] = renpy_host.in_frame() if hasattr(renpy_host, "in_frame") else None
        meta["sample"] = renpy_host.sample_texture_count() if hasattr(renpy_host, "sample_texture_count") else None
    except Exception:
        pass
    meta["panel"] = _sample_panel()
    try:
        iface.surftree = st
    except Exception:
        pass
    return "ok", st, meta

def _hide():
    import renpy
    for n in ("preferences", "confirm", "load", "save", "appreciation", "flowchart", "dialog_config_1", "dialog_config_2"):
        try:
            renpy.display.screen.hide_screen(n)
        except Exception:
            try:
                renpy.store.Hide(n)()
            except Exception:
                pass

def _show(kind):
    import renpy
    renpy.display.screen.show_screen("preferences", kind=kind)
    try:
        renpy.restart_interaction()
    except Exception:
        pass

def _worker():
    out = _base() / "host" / "target" / "gate-hmc_dc_fix_redraw.txt"
    lines = []
    try:
        import renpy
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                if getattr(renpy.store, "main_menu", False):
                    break
            except Exception:
                pass
            time.sleep(0.2)
        lines.append("main_menu=%s" % getattr(renpy.store, "main_menu", None))

        for kind, proc, upd in (
            ("dialog_config_2", True, True),
            ("dialog_config_1", True, True),
            ("dialog_config_1", False, True),  # no process_redraws
            ("dialog_config_1", True, False),  # no force update
            ("dialog_config_2", True, True),
        ):
            _hide()
            time.sleep(0.12)
            try:
                _show(kind)
            except Exception as e:
                lines.append("SHOW fail kind=%s %s" % (kind, e))
                continue
            time.sleep(0.2)
            rd, st, meta = _redraw(do_process_redraws=proc, do_update=upd)
            panel = meta.get("panel")
            pmean = tuple(round(x, 1) for x in panel["mean"]) if panel else None
            pdark = round(panel["dark"], 3) if panel else None
            lines.append("KIND=%s proc=%s upd=%s redraw=%s panel_mean=%s dark=%s n1849=%s r_nch=%s" % (
                kind, proc, upd, rd, pmean, pdark, meta.get("n_1849_renders"), meta.get("r1849_nch")))
            lines.append("  updated=%s process_redraws=%s" % (meta.get("updated"), meta.get("process_redraws", meta.get("process_redraws_err"))))
            for f in (meta.get("fixed_disp") or [])[:3]:
                lines.append("  fixed_disp path=%s nch=%d kids=%s" % (f.get("path"), f.get("nch"), f.get("kids")))
            lines.append("  r1849_children=%s" % meta.get("r1849_children"))
            lines.append("  sample=%s in_frame=%s/%s" % (meta.get("sample"), meta.get("in_frame_before"), meta.get("in_frame_after")))
            _log(lines[-4])

        out.write_text("\n".join(lines) + "\n")
        _log("wrote %s" % out)
    except Exception:
        tb = traceback.format_exc()
        lines.append(tb)
        out.write_text("\n".join(lines) + "\n")
        _log(tb)
    finally:
        time.sleep(0.3)
        _quit()

def main():
    base = _base()
    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ["RENPY_HOST_UI_TRACE"] = "1"
    _clear_falsey("RENPY_SKIP_MAIN_MENU")
    _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for p in (str(base / "host" / "python" / "gates"), str(base / "host" / "python")):
        if p not in sys.path:
            sys.path.insert(0, p)
    open("/tmp/hmc_dc_fix_redraw.log", "w").write("start\n")
    import renpy_host, bootstrap as boot
    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, miss, err, extra = call()
        _log("stage %s good=%s err=%r" % (name, good, err))
        if not good:
            _quit(); return
    import renpy
    renpy.host_build = True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e:
        _log("main_host %s" % e)
    try:
        import renpy.arguments
        basedir = getattr(renpy.config, "basedir", None) or game
        argv0 = sys.argv[0] if sys.argv else "renpy-host"
        sys.argv = [argv0, basedir, "run"]
        if not getattr(renpy.arguments, "commands", None):
            try:
                renpy.arguments.register_command("run", renpy.arguments.run, True)
                renpy.arguments.register_command("quit", renpy.arguments.quit)
            except Exception:
                pass
        renpy.game.args = renpy.arguments.bootstrap()
    except Exception as e:
        _log("args %s" % e)
    _pre()
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    import renpy.main as m
    try:
        m.main()
    except BaseException as e:
        _log("main exit %s" % e)

if __name__ == "__main__":
    main()
else:
    try:
        main()
    except Exception:
        traceback.print_exc()
        _quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
