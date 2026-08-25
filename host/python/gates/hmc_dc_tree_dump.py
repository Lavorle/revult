"""Dump preferences_layout Fixed structure for dialog_config_1 vs _2.

Finds 1849x846 Renders and logs children sizes / reverse / HT / mesh.
Also captures exceptions during render of kind screens alone.
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
        sys.__stdout__.write("[dc_tree] %s\n" % m); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/hmc_dc_tree_dump.log", "a").write(m + "\n")

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

def _node_brief(n, depth=0):
    from renpy.wgpu.draw import HostTexture
    t = type(n).__name__
    w = getattr(n, "width", None) or getattr(n, "w", None)
    h = getattr(n, "height", None) or getattr(n, "h", None)
    try:
        if hasattr(n, "get_size"):
            w, h = n.get_size()
    except Exception:
        pass
    mesh = getattr(n, "mesh", None)
    rev = getattr(n, "reverse", None)
    rev_s = None
    if rev is not None:
        try:
            rev_s = (float(getattr(rev, "xdx", 1)), float(getattr(rev, "ydy", 1)))
        except Exception:
            rev_s = "?"
    ct = getattr(n, "cached_texture", None)
    cm = getattr(n, "cached_model", None)
    ch = getattr(n, "children", None)
    nch = len(ch) if ch is not None else 0
    ht = None
    if isinstance(n, HostTexture):
        ht = (int(n.handle), int(n.w), int(n.h))
    elif isinstance(ct, HostTexture):
        ht = ("ct", int(ct.handle), int(ct.w), int(ct.h))
    shaders = getattr(n, "shaders", None)
    xc = bool(getattr(n, "xclipping", False))
    yc = bool(getattr(n, "yclipping", False))
    return {
        "t": t, "w": w, "h": h, "mesh": bool(mesh) if mesh is not None else None,
        "mesh_v": repr(mesh)[:40] if mesh not in (None, False, True) else mesh,
        "rev": rev_s, "nch": nch, "ht": ht,
        "cm": type(cm).__name__ if cm is not None else None,
        "shaders": list(shaders)[:4] if shaders else None,
        "clip": (xc, yc) if (xc or yc) else None,
    }

def _walk_dump(node, acc, depth=0, path="", budget=None, max_depth=12, max_acc=200):
    if budget is None:
        budget = [3000]
    if node is None or budget[0] <= 0 or depth > max_depth or len(acc) >= max_acc:
        return
    budget[0] -= 1
    brief = _node_brief(node, depth)
    # Keep interesting nodes: large, HT, reverse, mesh, or shallow
    keep = (
        depth <= 4
        or (brief["w"] and brief["h"] and (brief["w"] >= 400 or brief["h"] >= 400))
        or brief["ht"] is not None
        or brief["rev"] is not None
        or brief["mesh"]
        or brief["cm"] is not None
    )
    if keep:
        acc.append((path, brief))
    kids = []
    try:
        ch = getattr(node, "children", None)
        if ch:
            for i, c in enumerate(ch):
                if isinstance(c, (list, tuple)) and c:
                    child = c[0]
                    xo = c[1] if len(c) > 1 else 0
                    yo = c[2] if len(c) > 2 else 0
                    kids.append((child, "%s/c%d@%s,%s" % (path, i, xo, yo)))
                else:
                    kids.append((c, "%s/c%d" % (path, i)))
    except Exception:
        pass
    for attr in ("cached_texture", "cached_model"):
        try:
            v = getattr(node, attr, None)
            if v is not None:
                kids.append((v, "%s/%s" % (path, attr)))
        except Exception:
            pass
    for k, p in kids:
        _walk_dump(k, acc, depth + 1, p, budget, max_depth, max_acc)

def _find_sized(node, tw, th, acc=None, budget=None, depth=0, path=""):
    if acc is None:
        acc = []
    if budget is None:
        budget = [5000]
    if node is None or budget[0] <= 0 or depth > 40:
        return acc
    budget[0] -= 1
    w = getattr(node, "width", None) or getattr(node, "w", None)
    h = getattr(node, "height", None) or getattr(node, "h", None)
    try:
        if hasattr(node, "get_size"):
            w, h = node.get_size()
    except Exception:
        pass
    try:
        if w is not None and h is not None and abs(float(w) - tw) < 1 and abs(float(h) - th) < 1:
            acc.append((path, node))
    except Exception:
        pass
    kids = []
    try:
        ch = getattr(node, "children", None)
        if ch:
            for i, c in enumerate(ch):
                if isinstance(c, (list, tuple)) and c:
                    kids.append((c[0], "%s/c%d" % (path, i)))
                else:
                    kids.append((c, "%s/c%d" % (path, i)))
    except Exception:
        pass
    for attr in ("cached_texture", "cached_model"):
        try:
            v = getattr(node, attr, None)
            if v is not None:
                kids.append((v, "%s/%s" % (path, attr)))
        except Exception:
            pass
    for k, p in kids:
        _find_sized(k, tw, th, acc, budget, depth + 1, p)
    return acc

def _hide():
    import renpy
    for n in ("preferences", "confirm", "load", "save", "appreciation", "flowchart"):
        try: renpy.store.Hide(n)()
        except Exception:
            try: renpy.hide_screen(n)
            except Exception: pass

def _show(kind):
    import renpy
    renpy.display.screen.show_screen("preferences", kind=kind)
    try: renpy.restart_interaction()
    except Exception: pass

def _redraw():
    import renpy, interact_helpers as ih
    ready, why, iface = ih.interface_ready()
    if not ready:
        return "iface:" + why, None
    root = ih._rebuild_product_root(iface)
    w = int(getattr(renpy.config, "screen_width", 1920) or 1920)
    h = int(getattr(renpy.config, "screen_height", 1080) or 1080)
    st = renpy.display.render.render_screen(root, w, h)
    draw = renpy.display.draw
    try:
        if hasattr(draw, "load_all_textures"):
            draw.load_all_textures(st)
    except Exception as e:
        return "load:" + str(e), st
    draw.draw_screen(st, flip=True)
    try: iface.surftree = st
    except Exception: pass
    return "ok", st

def _sample_rt():
    import renpy_host
    rw, rh, rt = renpy_host.read_game_rt_rgba()
    if not rw:
        return {"err": "empty"}
    # sample center and panel region (centered 1849x846 on 1920x1080)
    # panel origin ≈ ((1920-1849)/2, (1080-846)/2) = (35.5, 117)
    regions = {
        "full": (0, 0, rw, rh),
        "panel_c": (rw // 2 - 50, rh // 2 - 50, 100, 100),
        "panel_tl": (40, 130, 80, 80),
        "mask_corner": (10, 10, 40, 40),
    }
    out = {}
    for name, (x0, y0, ww, hh) in regions.items():
        rs = gs = bs = n = 0
        dark = 0
        step = max(1, min(ww, hh) // 8)
        for y in range(y0, min(y0 + hh, rh), step):
            for x in range(x0, min(x0 + ww, rw), step):
                o = (y * rw + x) * 4
                r, g, b = rt[o], rt[o + 1], rt[o + 2]
                rs += r; gs += g; bs += b; n += 1
                if r < 25 and g < 25 and b < 30:
                    dark += 1
        if n:
            out[name] = {"mean": (rs / n, gs / n, bs / n), "dark": dark / n, "n": n}
    return out

def _render_kind_alone(kind):
    """Render only the kind screen (dialog_config_*) without full preferences chrome."""
    import renpy
    try:
        d = renpy.display.screen.get_screen_displayable(kind) if hasattr(renpy.display.screen, "get_screen_displayable") else None
    except Exception:
        d = None
    try:
        # Use ScreenDisplayable via show or by constructing from screen
        scr = renpy.display.screen.ScreenDisplayable(
            renpy.display.screen.get_screen_variant(kind) if hasattr(renpy.display.screen, "get_screen_variant") else renpy.display.screen.get_screen(kind),
            kind, "screens", {}, {},
        )
    except Exception as e:
        # Fallback: show_screen transient and grab
        try:
            renpy.display.screen.show_screen(kind)
            try: renpy.restart_interaction()
            except Exception: pass
            time.sleep(0.1)
            sd = renpy.display.screen.get_screen(kind)
            r = renpy.display.render.render(sd, 1920, 1080, 0, 0)
            return "via_show", r, None
        except Exception as e2:
            return "fail", None, "%s / %s" % (e, e2)
    try:
        # Force update
        try:
            scr.update()
        except Exception as e:
            return "update_fail", None, str(e)
        r = renpy.display.render.render(scr, 1920, 1080, 0, 0)
        return "ok", r, None
    except Exception as e:
        return "render_fail", None, traceback.format_exc()

def _dump_screen_widgets(kind):
    """After preferences shown, inspect widget tree for layout fixed / background."""
    import renpy
    lines = []
    try:
        scr = renpy.display.screen.get_screen("preferences")
        if scr is None:
            return ["no preferences screen"]
        # ScreenDisplayable has .child after update, and widgets dict
        lines.append("scr type=%s child=%s" % (type(scr).__name__, type(getattr(scr, "child", None)).__name__))
        widgets = getattr(scr, "widgets", None) or getattr(scr, "widget_properties", None)
        if isinstance(widgets, dict):
            lines.append("widgets keys sample=%s" % list(widgets.keys())[:20])
        # visit children
        def walk_d(d, path, depth, budget):
            if d is None or budget[0] <= 0 or depth > 25:
                return
            budget[0] -= 1
            t = type(d).__name__
            interesting = False
            rep = ""
            try:
                rep = repr(d)[:160]
            except Exception:
                rep = t
            if any(s in rep for s in ("background.png", "mask.png", "1849", "preferences_layout", "dialog_config")):
                interesting = True
            # size hints
            try:
                xs = getattr(d, "style", None)
                if xs is not None:
                    try:
                        xsz = xs.xsize if hasattr(xs, "xsize") else xs["xsize"]
                        ysz = xs.ysize if hasattr(xs, "ysize") else xs["ysize"]
                        if xsz == 1849 or ysz == 846:
                            interesting = True
                            rep += " style_size=(%s,%s)" % (xsz, ysz)
                    except Exception:
                        pass
            except Exception:
                pass
            if interesting or depth <= 2:
                lines.append("  d%s %s %s" % (path, t, rep[:180]))
            # children
            try:
                if hasattr(d, "visit"):
                    for i, c in enumerate(d.visit() or []):
                        walk_d(c, path + "/v%d" % i, depth + 1, budget)
            except Exception:
                pass
            try:
                ch = getattr(d, "children", None)
                if ch:
                    for i, c in enumerate(list(ch)[:40]):
                        walk_d(c, path + "/c%d" % i, depth + 1, budget)
            except Exception:
                pass
            for attr in ("child", "displayable"):
                try:
                    v = getattr(d, attr, None)
                    if v is not None:
                        walk_d(v, path + "/" + attr, depth + 1, budget)
                except Exception:
                    pass
        budget = [2000]
        walk_d(scr, "", 0, budget)
    except Exception:
        lines.append("dump_widgets exc %s" % traceback.format_exc())
    return lines

def _worker():
    out = _base() / "host" / "target" / "gate-hmc_dc_tree_dump.txt"
    lines = []
    try:
        import renpy
        from renpy.wgpu.draw import HostTexture
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                if getattr(renpy.store, "main_menu", False):
                    break
            except Exception:
                pass
            time.sleep(0.2)
        lines.append("main_menu=%s" % getattr(renpy.store, "main_menu", None))
        try:
            lines.append("brightness=%s" % getattr(renpy.store.persistent, "preferences_brightness", None))
        except Exception as e:
            lines.append("brightness err %s" % e)

        # Solo render background + layout fixed
        try:
            bg = renpy.easy.displayable("gui/preferences/common/background.png")
            rbg = renpy.display.render.render(bg, 1849, 846, 0, 0)
            lines.append("solo_bg size=%s nch=%s ctex=%s" % (
                rbg.get_size(),
                len(getattr(rbg, "children", []) or []),
                type(getattr(rbg, "cached_texture", None)).__name__,
            ))
            for i, c in enumerate(list(getattr(rbg, "children", []) or [])[:4]):
                child = c[0] if isinstance(c, (list, tuple)) else c
                lines.append("  solo_bg child%d %s" % (i, _node_brief(child)))
        except Exception:
            lines.append("solo_bg EXC %s" % traceback.format_exc())

        for kind in ("dialog_config_2", "dialog_config_1", "dialog_config_1"):
            _hide(); time.sleep(0.15)
            try:
                _show(kind)
            except Exception as e:
                lines.append("KIND %s show_fail %s" % (kind, e))
                continue
            time.sleep(0.25)

            # Widget tree
            lines.append("KIND %s widgets:" % kind)
            lines.extend(_dump_screen_widgets(kind)[:40])

            rd, st = _redraw()
            lines.append("KIND %s redraw=%s" % (kind, rd))
            rt = _sample_rt()
            lines.append("  rt %s" % {k: (tuple(round(x, 1) for x in v["mean"]), round(v["dark"], 3))
                                      for k, v in rt.items() if isinstance(v, dict) and "mean" in v})

            if st is None:
                continue

            # Find 1849x846 nodes
            found = _find_sized(st, 1849, 846)
            lines.append("  nodes_1849x846=%d" % len(found))
            for p, n in found[:6]:
                lines.append("  N1849 path=%s brief=%s" % (p, _node_brief(n)))
                # dump children of this node
                ch = getattr(n, "children", None) or []
                lines.append("    nchildren=%d" % len(ch))
                for i, c in enumerate(list(ch)[:12]):
                    child = c[0] if isinstance(c, (list, tuple)) else c
                    off = (c[1], c[2]) if isinstance(c, (list, tuple)) and len(c) > 2 else None
                    lines.append("    ch%d off=%s %s" % (i, off, _node_brief(child)))
                    # one more level if HostTexture missing
                    sub = getattr(child, "children", None) or []
                    for j, s in enumerate(list(sub)[:6]):
                        sc = s[0] if isinstance(s, (list, tuple)) else s
                        lines.append("      ch%d.%d %s" % (i, j, _node_brief(sc)))

            # Also find any HT near 1849
            big = []
            def pred_big(n):
                if isinstance(n, HostTexture):
                    ww, hh = int(n.w), int(n.h)
                    if ww >= 1000 or hh >= 700:
                        big.append((ww, hh, int(n.handle)))
                return False
            # reuse walk
            budget = [4000]
            def walk(n, depth=0):
                if n is None or budget[0] <= 0 or depth > 40:
                    return
                budget[0] -= 1
                pred_big(n)
                try:
                    ch = getattr(n, "children", None)
                    if ch:
                        for c in ch:
                            child = c[0] if isinstance(c, (list, tuple)) else c
                            walk(child, depth + 1)
                except Exception:
                    pass
                for attr in ("cached_texture", "cached_model"):
                    try:
                        v = getattr(n, attr, None)
                        if v is not None:
                            walk(v, depth + 1)
                    except Exception:
                        pass
            walk(st)
            lines.append("  big_hts=%s" % big[:20])

            # Top-level children of root surftree
            lines.append("  root_brief=%s" % _node_brief(st))
            ch = getattr(st, "children", None) or []
            lines.append("  root_nch=%d" % len(ch))
            for i, c in enumerate(list(ch)[:16]):
                child = c[0] if isinstance(c, (list, tuple)) else c
                off = (c[1], c[2]) if isinstance(c, (list, tuple)) and len(c) > 2 else None
                lines.append("  root_ch%d off=%s %s" % (i, off, _node_brief(child)))

            # Shallow interesting dump
            acc = []
            _walk_dump(st, acc, max_depth=6, max_acc=80)
            lines.append("  dump_n=%d" % len(acc))
            for p, b in acc[:50]:
                if b.get("w") and b.get("h") and (b["w"] >= 500 or b["h"] >= 500 or b.get("ht") or b.get("rev")):
                    lines.append("  D %s %s" % (p, b))

            _log("KIND %s nodes1849=%d big=%s rt=%s" % (kind, len(found), big[:5], lines[-10]))

            # Also try rendering dialog_config_1 screen alone via use
            try:
                # Direct displayable path: renpy.display.screen.ScreenDisplayable
                sd = renpy.display.screen.get_screen(kind)
                # kind is not a top-level shown screen - it's used inside preferences
                # Build via renpy.show_screen of the kind itself if possible
                try:
                    renpy.display.screen.show_screen(kind)
                    time.sleep(0.05)
                    sk = renpy.display.render.render(
                        renpy.display.screen.get_screen(kind), 1920, 1080, 0, 0
                    )
                    f2 = _find_sized(sk, 1849, 846)
                    lines.append("  alone_kind_show nodes1849=%d size=%s nch=%s" % (
                        len(f2), sk.get_size() if hasattr(sk, "get_size") else None,
                        len(getattr(sk, "children", []) or [])))
                    for p, n in f2[:3]:
                        lines.append("    alone %s %s" % (p, _node_brief(n)))
                        for i, c in enumerate(list(getattr(n, "children", []) or [])[:6]):
                            child = c[0] if isinstance(c, (list, tuple)) else c
                            lines.append("      ch%d %s" % (i, _node_brief(child)))
                    renpy.hide_screen(kind)
                except Exception as e:
                    lines.append("  alone_kind_show fail %s" % e)
            except Exception as e:
                lines.append("  alone err %s" % e)

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
    open("/tmp/hmc_dc_tree_dump.log", "w").write("start\n")
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
