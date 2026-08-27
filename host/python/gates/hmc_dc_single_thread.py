"""Single-thread product-path probe for dialog_config panel bg.

Runs inside interact_core via interact_callbacks so no second force-redraw
thread races MultiBox children. Opens preferences kinds and samples RT +
surftree after the product draw.
"""
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from host.python.gates._harness import gate_harness, parametrized_gate

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write(f"[dc_st] {m}\n"); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_dc_single_thread.log", "a").write(m + "\n")  # noqa: SIM115

def _quit():
    try:
        import renpy_host; renpy_host.request_quit()  # noqa: I001
    except Exception:
        pass

def _clear_falsey(n):
    v = os.environ.get(n)
    if v is not None and str(v).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(n, None)

def _pre():
    import types
    try:
        import renpy.audio as a
        import renpy.audio.renpysound_host as h
        sys.modules["renpy.audio.renpysound"] = h; a.renpysound = h
    except Exception as e:
        _log(f"sound {e}")
    try:
        import host_pygame
        import host_pygame.locals as loc
        from host_pygame import scrap
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
        _log(f"pygame {e}")
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu.uguu"] = u; sys.modules["renpy.uguu.gl"] = u
        pkg = sys.modules.get("renpy.uguu") or types.ModuleType("renpy.uguu"); pkg.__path__ = []
        sys.modules["renpy.uguu"] = pkg
        for n in dir(u):
            if n.startswith("GL_") or n in ("clear_errors", "get_error"):
                setattr(pkg, n, getattr(u, n))
        pkg.uguu = u; pkg.gl = u
        import renpy; renpy.uguu = pkg  # noqa: I001
    except Exception as e:
        _log(f"uguu {e}")
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"] = e
        import renpy; renpy.ecsign = e  # noqa: I001
    except Exception as e:
        _log(f"ecsign {e}")

_STATE = {
    "phase": "wait_menu",
    "kinds": ["dialog_config_2", "dialog_config_1", "dialog_config_1", "dialog_config_2"],
    "ki": 0,
    "frames_on_kind": 0,
    "results": [],
    "done": False,
    "lines": [],
}

def _sample_panel():
    import renpy_host
    rw, rh, rt = renpy_host.read_game_rt_rgba()
    if not rw:
        return None
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
    # also full mean
    rs2 = gs2 = bs2 = n2 = 0
    step_x = max(1, rw // 32); step_y = max(1, rh // 18)
    for y in range(step_y // 2, rh, step_y):
        for x in range(step_x // 2, rw, step_x):
            o = (y * rw + x) * 4
            rs2 += rt[o]; gs2 += rt[o + 1]; bs2 += rt[o + 2]; n2 += 1
    return {
        "panel": (rs / n, gs / n, bs / n),
        "panel_dark": dark / n,
        "full": (rs2 / n2, gs2 / n2, bs2 / n2),
    }

def _count_bg(st):
    from renpy.wgpu.draw import HostTexture
    n1849 = 0
    n1920 = 0
    bg_alive = None
    budget = [5000]
    def walk(node, depth=0):
        nonlocal n1849, n1920, bg_alive
        if node is None or budget[0] <= 0 or depth > 45:
            return
        budget[0] -= 1
        if isinstance(node, HostTexture):
            w, h = int(node.w), int(node.h)
            if (w, h) == (1849, 846):
                n1849 += 1
                if bg_alive is None:
                    try:
                        import renpy_host
                        bg_alive = bool(renpy_host.texture_alive(int(node.handle)))
                    except Exception:
                        bg_alive = "?"
            if (w, h) == (1920, 1080):
                n1920 += 1
        try:
            ww = getattr(node, "width", None)
            hh = getattr(node, "height", None)
            if hasattr(node, "get_size"):
                ww, hh = node.get_size()
            if ww is not None and hh is not None and abs(float(ww) - 1849) < 1 and abs(float(hh) - 846) < 1:
                # count children
                ch = getattr(node, "children", None) or []
                if not hasattr(walk, "layout_nch"):
                    walk.layout_nch = len(ch)
                    walk.layout_kids = []
                    for i, c in enumerate(list(ch)[:6]):
                        child = c[0] if isinstance(c, (list, tuple)) else c
                        cw = getattr(child, "width", None) or getattr(child, "w", None)
                        chh = getattr(child, "height", None) or getattr(child, "h", None)
                        walk.layout_kids.append((type(child).__name__, cw, chh, len(getattr(child, "children", None) or [])))
        except Exception:
            pass
        try:
            for c in getattr(node, "children", None) or []:
                child = c[0] if isinstance(c, (list, tuple)) else c
                walk(child, depth + 1)
        except Exception:
            pass
        for attr in ("cached_texture", "cached_model"):
            try:
                v = getattr(node, attr, None)
                if v is not None:
                    walk(v, depth + 1)
            except Exception:
                pass
    walk.layout_nch = None
    walk.layout_kids = None
    walk(st)
    return n1849, n1920, bg_alive, walk.layout_nch, walk.layout_kids

def _callback():
    """Called from interact_callbacks on the main interact thread."""
    if _STATE["done"]:
        return
    import renpy
    try:
        if _STATE["phase"] == "wait_menu":
            if getattr(renpy.store, "main_menu", False):
                _STATE["phase"] = "open"
                _STATE["ki"] = 0
                _STATE["frames_on_kind"] = 0
                _log("main_menu reached")
            return

        if _STATE["phase"] == "open":
            if _STATE["ki"] >= len(_STATE["kinds"]):
                _STATE["phase"] = "finish"
                return
            kind = _STATE["kinds"][_STATE["ki"]]
            # hide then show
            try:
                renpy.display.screen.hide_screen("preferences")
            except Exception:
                pass
            for n in ("dialog_config_1", "dialog_config_2", "confirm"):
                try:
                    renpy.display.screen.hide_screen(n)
                except Exception:
                    pass
            renpy.display.screen.show_screen("preferences", kind=kind)
            try:
                renpy.exports.restart_interaction()
            except Exception:
                try:
                    renpy.game.interface.restart_interaction = True
                except Exception:
                    pass
            _STATE["phase"] = "hold"
            _STATE["frames_on_kind"] = 0
            _STATE["current_kind"] = kind
            _log(f"opened {kind}")
            return

        if _STATE["phase"] == "hold":
            _STATE["frames_on_kind"] += 1
            # wait a few product frames for textures / movie settle
            if _STATE["frames_on_kind"] < 4:
                return
            kind = _STATE["current_kind"]
            # Sample product surftree from interface (post product draw)
            iface = renpy.display.interface
            st = getattr(iface, "surftree", None)
            n1849, n1920, bg_alive, layout_nch, layout_kids = (0, 0, None, None, None)
            if st is not None:
                n1849, n1920, bg_alive, layout_nch, layout_kids = _count_bg(st)
            # Also dump Fixed displayable
            fixed_nch = None
            fixed_kids = None
            try:
                scr = renpy.display.screen.get_screen("preferences")
                if scr is not None and getattr(scr, "child", None) is not None:
                    # find 1849 fixed
                    def find_f(d, depth=0):
                        if d is None or depth > 20:
                            return None
                        try:
                            stl = d.style
                            if stl["xsize"] == 1849 or stl["ysize"] == 846:
                                return d
                        except Exception:
                            pass
                        try:
                            for c in d.visit() or []:
                                r = find_f(c, depth + 1)
                                if r is not None:
                                    return r
                        except Exception:
                            pass
                        try:
                            for c in list(getattr(d, "children", None) or [])[:20]:
                                r = find_f(c, depth + 1)
                                if r is not None:
                                    return r
                        except Exception:
                            pass
                        return None
                    f = find_f(scr)
                    if f is not None:
                        ch = list(getattr(f, "children", None) or [])
                        fixed_nch = len(ch)
                        fixed_kids = []
                        for c in ch[:6]:
                            try:
                                fixed_kids.append(type(c).__name__ + ":" + repr(c)[:60])
                            except Exception:
                                fixed_kids.append("?")
            except Exception as e:
                fixed_kids = [f"err:{e}"]

            panel = _sample_panel()
            rec = {
                "kind": kind,
                "n1849": n1849,
                "n1920": n1920,
                "bg_alive": bg_alive,
                "layout_nch": layout_nch,
                "layout_kids": layout_kids,
                "fixed_nch": fixed_nch,
                "fixed_kids": fixed_kids,
                "panel": panel,
            }
            _STATE["results"].append(rec)
            pmean = tuple(round(x, 1) for x in panel["panel"]) if panel else None
            pdark = round(panel["panel_dark"], 3) if panel else None
            fmean = tuple(round(x, 1) for x in panel["full"]) if panel else None
            line = f"RESULT kind={kind} n1849={n1849} n1920={n1920} bg_alive={bg_alive} layout_nch={layout_nch} fixed_nch={fixed_nch} panel={pmean} dark={pdark} full={fmean} kids={layout_kids}"
            _STATE["lines"].append(line)
            _log(line)
            _STATE["lines"].append(f"  fixed_kids={fixed_kids}")

            _STATE["ki"] += 1
            _STATE["phase"] = "open"
            return

        if _STATE["phase"] == "finish":
            out = _base() / "host" / "target" / "gate-hmc_dc_single_thread.txt"
            # label
            ok = True
            for r in _STATE["results"]:
                if r["kind"].startswith("dialog_config"):
                    p = r.get("panel") or {}
                    dark = float(p.get("panel_dark") or 1)
                    n1849 = r.get("n1849") or 0
                    if dark > 0.5 or n1849 < 1:
                        ok = False
            _STATE["lines"].append(f"ok={ok}")
            out.write_text("\n".join(_STATE["lines"]) + "\n")
            _log(f"wrote {out} ok={ok}")
            _STATE["done"] = True
            _quit()
    except Exception:
        tb = traceback.format_exc()
        _log(tb)
        _STATE["lines"].append(tb)
        out = _base() / "host" / "target" / "gate-hmc_dc_single_thread.txt"
        out.write_text("\n".join(_STATE["lines"]) + "\n")
        _STATE["done"] = True
        _quit()

def _worker_watchdog():
    # safety quit if stuck
    time.sleep(90)
    if not _STATE["done"]:
        _log("watchdog quit")
        out = _base() / "host" / "target" / "gate-hmc_dc_single_thread.txt"
        _STATE["lines"].append("watchdog timeout")
        out.write_text("\n".join(_STATE["lines"]) + "\n")
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
    open("/tmp/hmc_dc_single_thread.log", "w").write("start\n")  # noqa: SIM115
    import bootstrap as boot
    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, _miss, err, _extra = call()
        _log(f"stage {name} good={good} err={err!r}")
        if not good:
            _quit(); return
    import renpy
    renpy.host_build = True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)  # noqa: I001
    except Exception as e:
        _log(f"main_host {e}")
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
        _log(f"args {e}")
    _pre()
    # Install callback on main thread path
    try:
        renpy.config.interact_callbacks.append(_callback)
        _log("interact_callback installed")
    except Exception as e:
        _log(f"callback install fail {e}")
    threading.Thread(target=_worker_watchdog, daemon=True).start()
    import renpy.main as m
    try:
        m.main()
    except BaseException as e:
        _log(f"main exit {e}")

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
