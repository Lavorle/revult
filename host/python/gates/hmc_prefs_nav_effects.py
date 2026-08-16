"""HuangmeiC preferences top-nav effects product probe.

Gate name: hmc_prefs_nav_effects  (RENPY_HOST_GATE=hmc_prefs_nav_effects)

Proves product path for preferences.rpy top hbox:
  1. Selected tab shows dissolve_transform → navigation_selected yellow chrome
  2. Hover on a non-selected tab ColorizeMatrix-yellows the icon region
  3. Optional: text hover_color yellow band increase

Does not modify game source. Uses ShowMenu + inject mouse + RT sample.

Writes: host/target/gate-hmc_prefs_nav_effects.txt
"""

import os
import sys
import threading
import time
import traceback
from pathlib import Path


def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    line = "[hmc_prefs_nav] %s\n" % msg
    try:
        sys.__stdout__.write(line)
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_prefs_nav_effects.log", "a").write(msg + "\n")
    except Exception:
        pass


def _request_quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:
        pass


def _clear_falsey_skip(name):
    val = os.environ.get(name)
    if val is None:
        return
    if str(val).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(name, None)


def _pre_main_host_stubs():
    import types

    try:
        import renpy.audio.renpysound_host as _rs_host
        import renpy.audio as _ra

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound")
    except Exception as e:
        _log("renpysound soft-fail: %s" % e)

    try:
        import host_pygame
        import host_pygame.locals as _loc
        import host_pygame.scrap as _host_scrap

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = _loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules.setdefault("pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"] = _host_scrap
        sys.modules["pygame.scrap"] = _host_scrap
        import renpy.pygame as rpg

        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        try:
            rpg.scrap = _host_scrap
        except Exception:
            pass
        try:
            rpg.import_as_pygame()
        except Exception as e:
            _log("import_as_pygame soft-fail: %s" % e)
        _log("pygame host shim ok")
    except Exception as e:
        _log("pygame soft-fail: %s" % e)

    try:
        import renpy_uguu_host as _uguu

        sys.modules["renpy.uguu.uguu"] = _uguu
        sys.modules["renpy.uguu.gl"] = _uguu
        pkg = sys.modules.get("renpy.uguu")
        if pkg is None:
            pkg = types.ModuleType("renpy.uguu")
            pkg.__path__ = []  # type: ignore[attr-defined]
            sys.modules["renpy.uguu"] = pkg
        for _name in dir(_uguu):
            if _name.startswith("GL_") or _name in ("clear_errors", "get_error"):
                setattr(pkg, _name, getattr(_uguu, _name))
        setattr(pkg, "uguu", _uguu)
        setattr(pkg, "gl", _uguu)
        try:
            import renpy

            renpy.uguu = pkg
        except Exception:
            pass
        _log("uguu host stub installed")
    except Exception as e:
        _log("uguu soft-fail: %s" % e)

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            setattr(_renpy_pkg, "ecsign", _ecsign)
        except Exception:
            pass
        _log("ecsign host stub installed")
    except Exception as e:
        _log("ecsign soft-fail: %s" % e)


def _force_product_redraw():
    import renpy
    import interact_helpers as ih

    info = {"path": None, "error": None}
    try:
        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            info["error"] = "iface:%s" % why
            return info
        root = ih._rebuild_product_root(iface)
        if root is None:
            info["error"] = "root_absent"
            return info
        w = int(getattr(renpy.config, "screen_width", 1920) or 1920)
        h = int(getattr(renpy.config, "screen_height", 1080) or 1080)
        surftree = renpy.display.render.render_screen(root, w, h)
        draw = getattr(renpy.display, "draw", None)
        if draw is None or not hasattr(draw, "draw_screen"):
            info["error"] = "no_draw"
            return info
        draw.draw_screen(surftree, flip=True)
        try:
            iface.surftree = surftree
        except Exception:
            pass
        info["path"] = "rebuild_render_screen"
        info["root"] = type(root).__name__
        return info
    except Exception as e:
        info["error"] = "%s:%s" % (type(e).__name__, e)
        return info


def _read_rt():
    import renpy_host

    pres = _force_product_redraw()
    try:
        rw, rh, rt = renpy_host.read_game_rt_rgba()
    except Exception as e:
        return None, None, None, {"error": "read:%s" % e, "present": pres}
    return rw, rh, rt, {"present": pres}


def _sample_band(rt, rw, rh, x0, y0, x1, y1, step=2):
    """Return mean rgb, yellow_frac, n for axis-aligned band in virtual pixels."""
    if not rt or not rw or not rh:
        return {"ok": False, "error": "empty"}
    x0 = max(0, min(rw - 1, int(x0)))
    x1 = max(0, min(rw, int(x1)))
    y0 = max(0, min(rh - 1, int(y0)))
    y1 = max(0, min(rh, int(y1)))
    if x1 <= x0 or y1 <= y0:
        return {"ok": False, "error": "bad_rect"}
    rs = gs = bs = n = yellow = 0
    for y in range(y0, y1, step):
        row = y * rw * 4
        for x in range(x0, x1, step):
            o = row + x * 4
            r, g, b, a = rt[o], rt[o + 1], rt[o + 2], rt[o + 3]
            if a < 40:
                continue
            rs += r
            gs += g
            bs += b
            n += 1
            # ColorizeMatrix #ffde00 and navigation_selected yellow
            if r > 180 and g > 140 and b < 90:
                yellow += 1
    if n == 0:
        return {"ok": False, "error": "no_opaque", "n": 0}
    return {
        "ok": True,
        "n": n,
        "mean": (rs / n, gs / n, bs / n),
        "yellow_frac": yellow / float(n),
        "yellow": yellow,
    }


def _force_show_prefs(kind=None):
    import renpy

    try:
        if kind is None:
            action = renpy.store.ShowMenu("preferences")
        else:
            # product Show("preferences", kind=name)
            action = renpy.store.Show("preferences", kind=kind)
        action()
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        return True, "Show(%r)" % (kind or "preferences")
    except Exception as e1:
        try:
            renpy.display.screen.show_screen("preferences", kind=kind or "sound_config")
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            return True, "show_screen:%s" % e1
        except Exception as e2:
            return False, "fail:%s|%s" % (e1, e2)


def _virtual_to_window(x, y):
    """Map virtual 1920×1080 coords to physical window pixels for inject_mouse."""
    import renpy

    vx = float(getattr(renpy.config, "screen_width", 1920) or 1920)
    vy = float(getattr(renpy.config, "screen_height", 1080) or 1080)
    ww = wh = None
    try:
        import renpy_host

        if hasattr(renpy_host, "window_size"):
            ww, wh = renpy_host.window_size()
    except Exception:
        pass
    if not ww or not wh:
        try:
            draw = getattr(renpy.display, "draw", None)
            phys = getattr(draw, "physical_size", None) or getattr(draw, "window_size", None)
            if phys and len(phys) >= 2:
                ww, wh = int(phys[0]), int(phys[1])
        except Exception:
            pass
    if not ww or not wh:
        return int(x), int(y)
    # virtual→physical via draw.untranslate_point (pure scale on wgpu host)
    try:
        draw = renpy.display.draw
        if hasattr(draw, "untranslate_point"):
            return draw.untranslate_point(int(x), int(y))
    except Exception:
        pass
    sx = float(ww) / vx
    sy = float(wh) / vy
    return int(x * sx), int(y * sy)


def _inject_motion(x, y):
    """Inject mouse motion at virtual coords. Host API: inject_mouse(x,y,button,pressed)."""
    import renpy_host

    wx, wy = _virtual_to_window(x, y)
    _err4 = None
    # Keep pygame mouse pos in sync for get_pos() consumers.
    try:
        import renpy.pygame as pygame

        pygame.mouse.set_pos((int(wx), int(wy)))
    except Exception:
        pass
    try:
        if hasattr(renpy_host, "inject_mouse"):
            renpy_host.inject_mouse(int(wx), int(wy), 0, False)
            return "inject_mouse4@%d,%d" % (wx, wy)
    except TypeError:
        # Older 3-arg signature (should not happen on current host).
        try:
            renpy_host.inject_mouse(int(wx), int(wy), 0)
            return "inject_mouse3@%d,%d" % (wx, wy)
        except Exception as e:
            return "inject_mouse3_fail:%s" % e
    except Exception as e:
        _err4 = "inject_mouse4_fail:%s" % e
    try:
        if hasattr(renpy_host, "inject_mouse_motion"):
            renpy_host.inject_mouse_motion(int(wx), int(wy))
            return "inject_mouse_motion"
    except Exception:
        pass
    try:
        import renpy

        try:
            renpy.display.interface.set_mouse_pos(int(x), int(y))
        except Exception:
            pass
        try:
            renpy.display.focus.set_focused(None, None, None)
        except Exception:
            pass
        return (_err4 + "|set_mouse_pos") if _err4 else "set_mouse_pos"
    except Exception as e:
        return "fail:%s" % e


def _force_icon_hover_events(tab_index):
    """Directly fire transform_event hover on prefs nav icon ATLs (bypass mouse).

    Returns list of notes. Used to separate H1 (event delivery) from H2/H3 (draw).
    """
    import renpy

    notes = []
    try:
        scr = renpy.display.screen.get_screen("preferences")
    except Exception as e:
        return ["get_screen:%s" % e]
    if scr is None:
        return ["no_screen"]

    # Walk displayable tree for Button nodes under prefs; pick tab_index-th nav-like button.
    buttons = []

    def walk(d, depth=0):
        if d is None or depth > 30:
            return
        try:
            from renpy.display.behavior import Button

            if isinstance(d, Button):
                buttons.append(d)
        except Exception:
            pass
        kids = getattr(d, "children", None) or ()
        for c in kids:
            walk(c, depth + 1)
        child = getattr(d, "child", None)
        if child is not None and child not in kids:
            walk(child, depth + 1)

    root = getattr(scr, "child", None) or scr
    walk(root)
    notes.append("buttons_n=%d" % len(buttons))
    # Top nav buttons are the first 7 Buttons with xsize-ish 179; fall back to index.
    nav = []
    for b in buttons:
        try:
            xs = getattr(b.style, "xsize", None) or getattr(b, "xsize", None)
        except Exception:
            xs = None
        # style may expose xmaximum; also check render size later
        nav.append(b)
    # Prefer buttons whose style size is 179 or action is Show preferences
    ranked = []
    for b in nav:
        score = 0
        try:
            act = getattr(b, "action", None)
            if act is not None and "preferences" in repr(act).lower():
                score += 2
        except Exception:
            pass
        try:
            # selected flag used by prefs tabs
            if getattr(b, "selected", None) is not None:
                score += 1
        except Exception:
            pass
        ranked.append((score, b))
    ranked.sort(key=lambda t: -t[0])
    # If many buttons, take high-score ones first then by encounter order of top band.
    candidates = [b for s, b in ranked if s >= 1] or [b for _, b in ranked]
    notes.append("candidates_n=%d" % len(candidates))
    if tab_index < 0 or tab_index >= len(candidates):
        notes.append("tab_index_oob")
        # still try to hover all non-selected
        targets = candidates[:7]
    else:
        targets = [candidates[tab_index]]

    def force_hover_on(d, depth=0):
        if d is None or depth > 20:
            return 0
        n = 0
        try:
            if getattr(d, "transform_event_responder", False) or hasattr(d, "set_transform_event"):
                d.set_transform_event("hover")
                n += 1
        except Exception:
            pass
        kids = getattr(d, "children", None) or ()
        for c in kids:
            n += force_hover_on(c, depth + 1)
        child = getattr(d, "child", None)
        if child is not None and child not in kids:
            n += force_hover_on(child, depth + 1)
        return n

    for i, b in enumerate(targets):
        try:
            # Full button focus path (style prefix + transform events).
            b.focus(default=False)
            notes.append("focus[%d]=ok" % i)
        except Exception as e:
            notes.append("focus[%d]=%s" % (i, e))
        n = force_hover_on(b)
        notes.append("set_hover[%d]=%d" % (i, n))
        try:
            renpy.display.render.redraw(b, 0)
        except Exception:
            pass
    try:
        renpy.restart_interaction()
    except Exception:
        pass
    return notes


def _matrix_pack_sample(node, depth=0, acc=None):
    """Collect packed matrixcolor first-column floats from surftree for diagnostics."""
    if acc is None:
        acc = []
    if node is None or depth > 24:
        return acc
    shaders = getattr(node, "shaders", None) or ()
    uniforms = getattr(node, "uniforms", None)
    if isinstance(uniforms, dict) and "u_renpy_matrixcolor" in uniforms:
        mat = uniforms.get("u_renpy_matrixcolor")
        floats = None
        try:
            draw = __import__("renpy.display.draw", fromlist=["*"])
            # Prefer WgpuDraw packer if available
            wd = getattr(__import__("renpy", fromlist=["display"]).display, "draw", None)
            if wd is not None and hasattr(wd, "_matrix_to_floats"):
                floats = wd._matrix_to_floats(mat)
        except Exception:
            floats = None
        if floats is None:
            try:
                floats = [
                    float(mat.xdx), float(mat.ydx), float(mat.zdx), float(mat.wdx),
                    float(mat.xdy), float(mat.ydy), float(mat.zdy), float(mat.wdy),
                    float(mat.xdz), float(mat.ydz), float(mat.zdz), float(mat.wdz),
                    float(mat.xdw), float(mat.ydw), float(mat.zdw), float(mat.wdw),
                ]
            except Exception:
                floats = None
        # Colorize yellow: col3 (translation) ≈ (1, 0.87, 0); Identity: diag 1
        kind = "unknown"
        if floats and len(floats) >= 16:
            if abs(floats[0] - 1.0) < 1e-3 and abs(floats[12]) < 1e-3:
                kind = "identity"
            elif abs(floats[12] - 1.0) < 0.05 and floats[13] > 0.5:
                kind = "colorize_yellow"
            else:
                kind = "other"
        acc.append({"path": depth, "kind": kind, "c3": floats[12:16] if floats else None})
    kids = getattr(node, "children", None) or ()
    for item in kids:
        child = item[0] if isinstance(item, tuple) and item else item
        _matrix_pack_sample(child, depth + 1, acc)
    return acc


def _walk_shaders(node, depth=0, acc=None, path=""):
    if acc is None:
        acc = []
    if node is None or depth > 24:
        return acc
    shaders = getattr(node, "shaders", None)
    uniforms = getattr(node, "uniforms", None)
    mesh = getattr(node, "mesh", None)
    if shaders or (isinstance(uniforms, dict) and (
        "u_animation" in uniforms
        or "u_transition" in uniforms
        or "u_renpy_matrixcolor" in uniforms
    )):
        acc.append(
            {
                "path": path,
                "type": type(node).__name__,
                "shaders": list(shaders) if shaders else None,
                "uniforms_keys": sorted(list(uniforms.keys()))
                if isinstance(uniforms, dict)
                else None,
                "u_animation": (
                    uniforms.get("u_animation")
                    if isinstance(uniforms, dict)
                    else None
                ),
                "has_matrixcolor": (
                    isinstance(uniforms, dict) and "u_renpy_matrixcolor" in uniforms
                ),
                "mesh": bool(mesh),
                "n_children": len(getattr(node, "children", None) or ()),
            }
        )
    kids = getattr(node, "children", None) or ()
    for i, item in enumerate(kids):
        if isinstance(item, tuple) and len(item) >= 1:
            child = item[0]
        else:
            child = item
        _walk_shaders(child, depth + 1, acc, path + "/%d" % i)
    return acc


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_prefs_nav_effects.txt"
    lines = []

    def rec(m):
        lines.append(m)
        _log(m)

    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    os.environ.setdefault(
        "RENPY_HOST_GAME", str(base / "host" / "playtests" / "HuangmeiC")
    )
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    _clear_falsey_skip("RENPY_SKIP_SPLASHSCREEN")
    os.environ["RENPY_SKIP_SPLASHSCREEN"] = "1"
    os.environ.pop("RENPY_SKIP_MAIN_MENU", None)

    gates = str(base / "host" / "python" / "gates")
    if gates not in sys.path:
        sys.path.insert(0, gates)

    import renpy_host  # type: ignore
    import bootstrap as boot

    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, miss, err, extra = call()
        rec("stage %s good=%s err=%r" % (name, good, err))
        if not good:
            out.write_text("ok=False\nerror=%s\n" % err)
            _request_quit()
            return

    import renpy

    renpy.host_build = True
    try:
        renpy.config.performance_test = False
    except Exception:
        pass

    try:
        import renpy_main_host

        renpy_main_host.install(renpy)
        rec("main_host installed")
    except Exception as e:
        rec("main_host: %s" % e)

    try:
        import renpy.arguments

        basedir = getattr(renpy.config, "basedir", None) or str(
            base / "host" / "playtests" / "HuangmeiC"
        )
        argv0 = sys.argv[0] if sys.argv else "renpy-host"
        sys.argv = [argv0, basedir, "run"]
        if not getattr(renpy.arguments, "commands", None):
            try:
                renpy.arguments.register_command("run", renpy.arguments.run, True)
                renpy.arguments.register_command("quit", renpy.arguments.quit)
            except Exception:
                pass
        args = renpy.arguments.bootstrap()
        renpy.game.args = args
        rec("args command=%s basedir=%s" % (getattr(args, "command", None), basedir))
    except Exception as e:
        rec("args fail: %s" % e)
        rec(traceback.format_exc())

    _pre_main_host_stubs()

    state = {
        "phase": "boot",
        "selected_ok": False,
        "hover_ok": False,
        "hover_via_mouse": False,
        "hover_via_force": False,
        "tree_ok": False,
        "notes": [],
    }

    def probe():
        # Wait main_menu
        deadline = time.time() + 45.0
        while time.time() < deadline:
            try:
                mm = getattr(renpy.store, "main_menu", None)
                if mm:
                    rec("main_menu at t=%.2f" % time.time())
                    break
            except Exception:
                pass
            time.sleep(0.1)
        else:
            rec("FAIL: main_menu timeout")
            state["phase"] = "fail"
            _request_quit()
            return

        time.sleep(0.4)
        ok, via = _force_show_prefs("sound_config")
        rec("opened preferences ok=%s via=%s" % (ok, via))
        time.sleep(0.6)

        # Pump interaction frames so ATL ease + product present settle (duration=0.2)
        # WP4 residual: incomplete/sparse suppress can hold last RT for a few frames
        # after sound_config open; wait for non-black selected chrome before pass/fail.
        for _ in range(24):
            _force_product_redraw()
            time.sleep(0.05)

        rw, rh, rt, meta = _read_rt()
        rec(
            "rt size=%sx%s present=%s"
            % (rw, rh, (meta.get("present") or {}).get("path"))
        )
        if not rw or not rh or not rt:
            rec("FAIL empty rt: %s" % meta)
            state["phase"] = "fail"
            _request_quit()
            return

        # Nav hbox: xpos 462 ypos 47; each button 179x64 (virtual 1920x1080).
        # Game RT may be 1280x720 or 1920x1080 — scale sample rects.
        sx = float(rw) / 1920.0
        sy = float(rh) / 1080.0
        # kind=sound_config is 4th entry (0-based index 3) in mapping order
        # image_config, game_config_1, text_config, sound_config, ...
        tab_index = 3
        x0 = (462 + tab_index * 179) * sx
        y0 = 47 * sy
        x1 = (462 + (tab_index + 1) * 179) * sx
        y1 = (47 + 64) * sy
        tab0 = {"ok": False}
        for settle_i in range(20):
            rw, rh, rt, meta = _read_rt()
            if not rw or not rh or not rt:
                _force_product_redraw()
                time.sleep(0.05)
                continue
            tab0 = _sample_band(rt, rw, rh, x0, y0, x1, y1, step=1)
            mean = tab0.get("mean", (0, 0, 0)) if tab0.get("ok") else (0, 0, 0)
            yf = tab0.get("yellow_frac", 0) if tab0.get("ok") else 0
            lum = (mean[0] + mean[1] + mean[2]) / 3.0
            if settle_i == 0 or settle_i == 19 or yf >= 0.08 or lum > 5.0:
                rec(
                    "tab0_selected settle=%d mean=%s yellow_frac=%.4f n=%s lum=%.1f"
                    % (
                        settle_i,
                        tuple(round(x, 1) for x in mean),
                        yf,
                        tab0.get("n"),
                        lum,
                    )
                )
            if tab0.get("ok") and yf >= 0.08:
                break
            _force_product_redraw()
            time.sleep(0.05)
        # Selected should show navigation_selected yellow (frac ~0.2+ of opaque)
        if tab0.get("ok") and tab0.get("yellow_frac", 0) >= 0.08:
            state["selected_ok"] = True
            rec("PASS selected yellow wipe chrome")
        else:
            rec("FAIL selected yellow chrome weak/absent")

        # Walk surftree for image_dissolve / matrixcolor stamps
        try:
            iface = renpy.display.interface
            st = getattr(iface, "surftree", None)
            hits = _walk_shaders(st)
            rec("shader_hits n=%d" % len(hits))
            for h in hits[:40]:
                rec(
                    "  hit path=%s shaders=%s keys=%s anim=%s mc=%s mesh=%s"
                    % (
                        h["path"],
                        h["shaders"],
                        h["uniforms_keys"],
                        h["u_animation"],
                        h["has_matrixcolor"],
                        h["mesh"],
                    )
                )
            has_id = any(
                h.get("shaders")
                and any(
                    s in ("image_dissolve", "renpy.imagedissolve", "imagedissolve")
                    for s in (h["shaders"] or [])
                )
                for h in hits
            )
            has_anim = any(
                h.get("uniforms_keys") and "u_animation" in (h["uniforms_keys"] or [])
                for h in hits
            )
            has_mc = any(h.get("has_matrixcolor") for h in hits)
            state["tree_ok"] = bool(has_id or has_anim)
            rec(
                "tree has_image_dissolve=%s has_u_animation=%s has_matrixcolor=%s"
                % (has_id, has_anim, has_mc)
            )
        except Exception as e:
            rec("walk exc: %s" % e)
            rec(traceback.format_exc())

        # Hover a non-selected tab (next after sound_config index 3).
        t1 = tab_index + 1
        if t1 >= 7:
            t1 = 0
        hx, hy = 462 + t1 * 179 + 90, 47 + 32
        how = _inject_motion(hx, hy)
        rec("hover inject virtual=(%d,%d) via=%s" % (hx, hy, how))
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        try:
            for _ in range(8):
                try:
                    renpy.display.focus.mouse_handler(
                        None, int(hx), int(hy), default=False
                    )
                except Exception:
                    pass
                try:
                    renpy.restart_interaction()
                except Exception:
                    pass
                _force_product_redraw()
                time.sleep(0.04)
        except Exception as e:
            rec("hover drive soft-fail: %s" % e)

        try:
            iface = renpy.display.interface
            st = getattr(iface, "surftree", None)
            mats = _matrix_pack_sample(st)
            kinds = {}
            for m in mats:
                kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
            rec("matrix_kinds_after_mouse=%s n=%d" % (kinds, len(mats)))
            for m in mats[:12]:
                rec("  matrix kind=%s c3=%s" % (m["kind"], m.get("c3")))
        except Exception as e:
            rec("matrix sample: %s" % e)

        rw2, rh2, rt2, meta2 = _read_rt()
        sx2 = float(rw2) / 1920.0 if rw2 else sx
        sy2 = float(rh2) / 1080.0 if rh2 else sy
        icon_band = _sample_band(
            rt2, rw2, rh2,
            (462 + t1 * 179 + 5) * sx2, (47 + 10) * sy2,
            (462 + t1 * 179 + 50) * sx2, (47 + 50) * sy2,
            step=1,
        )
        rec(
            "tab1_icon_hover mean=%s yellow_frac=%.4f n=%s"
            % (
                tuple(round(x, 1) for x in icon_band.get("mean", (0, 0, 0)))
                if icon_band.get("ok")
                else None,
                icon_band.get("yellow_frac", -1),
                icon_band.get("n"),
            )
        )
        tab1 = _sample_band(
            rt2, rw2, rh2,
            (462 + t1 * 179) * sx2, 47 * sy2,
            (462 + (t1 + 1) * 179) * sx2, (47 + 64) * sy2,
            step=1,
        )
        rec(
            "tab1_full_hover mean=%s yellow_frac=%.4f n=%s"
            % (
                tuple(round(x, 1) for x in tab1.get("mean", (0, 0, 0)))
                if tab1.get("ok")
                else None,
                tab1.get("yellow_frac", -1),
                tab1.get("n"),
            )
        )

        y_icon_mouse = icon_band.get("yellow_frac", 0) if icon_band.get("ok") else 0
        if y_icon_mouse < 0.05:
            notes = _force_icon_hover_events(t1)
            state["hover_via_force"] = True
            rec("force_hover notes=%s" % notes)
            for _ in range(6):
                _force_product_redraw()
                time.sleep(0.04)
            try:
                iface = renpy.display.interface
                st = getattr(iface, "surftree", None)
                mats = _matrix_pack_sample(st)
                kinds = {}
                for m in mats:
                    kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
                rec("matrix_kinds_after_force=%s n=%d" % (kinds, len(mats)))
                for m in mats[:12]:
                    rec("  matrix kind=%s c3=%s" % (m["kind"], m.get("c3")))
            except Exception as e:
                rec("matrix force sample: %s" % e)
            rw2, rh2, rt2, meta2 = _read_rt()
            sx2 = float(rw2) / 1920.0 if rw2 else sx
            sy2 = float(rh2) / 1080.0 if rh2 else sy
            icon_band = _sample_band(
                rt2, rw2, rh2,
                (462 + t1 * 179 + 5) * sx2, (47 + 10) * sy2,
                (462 + t1 * 179 + 50) * sx2, (47 + 50) * sy2,
                step=1,
            )
            rec(
                "tab1_icon_force mean=%s yellow_frac=%.4f n=%s"
                % (
                    tuple(round(x, 1) for x in icon_band.get("mean", (0, 0, 0)))
                    if icon_band.get("ok")
                    else None,
                    icon_band.get("yellow_frac", -1),
                    icon_band.get("n"),
                )
            )
            tab1 = _sample_band(
                rt2, rw2, rh2,
                (462 + t1 * 179) * sx2, 47 * sy2,
                (462 + (t1 + 1) * 179) * sx2, (47 + 64) * sy2,
                step=1,
            )
            rec(
                "tab1_full_force mean=%s yellow_frac=%.4f n=%s"
                % (
                    tuple(round(x, 1) for x in tab1.get("mean", (0, 0, 0)))
                    if tab1.get("ok")
                    else None,
                    tab1.get("yellow_frac", -1),
                    tab1.get("n"),
                )
            )

        _inject_motion(10, 10)
        try:
            renpy.restart_interaction()
        except Exception:
            pass
        for _ in range(4):
            try:
                renpy.display.focus.mouse_handler(None, 10, 10, default=False)
            except Exception:
                pass
            _force_product_redraw()
            time.sleep(0.04)
        rw3, rh3, rt3, _ = _read_rt()
        sx3 = float(rw3) / 1920.0 if rw3 else sx
        sy3 = float(rh3) / 1080.0 if rh3 else sy
        tab1_idle = _sample_band(
            rt3, rw3, rh3,
            (462 + t1 * 179) * sx3, 47 * sy3,
            (462 + (t1 + 1) * 179) * sx3, (47 + 64) * sy3,
            step=1,
        )
        rec(
            "tab1_full_idle mean=%s yellow_frac=%.4f n=%s"
            % (
                tuple(round(x, 1) for x in tab1_idle.get("mean", (0, 0, 0)))
                if tab1_idle.get("ok")
                else None,
                tab1_idle.get("yellow_frac", -1),
                tab1_idle.get("n"),
            )
        )

        y_hover = tab1.get("yellow_frac", 0) if tab1.get("ok") else 0
        y_idle = tab1_idle.get("yellow_frac", 0) if tab1_idle.get("ok") else 0
        y_icon = icon_band.get("yellow_frac", 0) if icon_band.get("ok") else 0
        yellow_good = (y_hover > y_idle + 0.02 and y_icon >= 0.05) or (
            y_icon >= 0.08 and y_hover > y_idle + 0.01
        )
        if yellow_good and not state.get("hover_via_force"):
            state["hover_ok"] = True
            state["hover_via_mouse"] = True
            rec(
                "PASS hover yellow delta hover=%.4f idle=%.4f icon=%.4f via=mouse"
                % (y_hover, y_idle, y_icon)
            )
        elif yellow_good and state.get("hover_via_force"):
            # Force path proves draw stack only — not product mouse hover AC.
            state["hover_ok"] = False
            rec(
                "FAIL hover yellow only via force (mouse dead) hover=%.4f idle=%.4f icon=%.4f"
                % (y_hover, y_idle, y_icon)
            )
        else:
            rec(
                "FAIL hover yellow weak hover=%.4f idle=%.4f icon=%.4f"
                % (y_hover, y_idle, y_icon)
            )
            try:
                scr = renpy.display.screen.get_screen("preferences")
                rec("prefs screen=%s" % (type(scr).__name__ if scr else None))
            except Exception as e:
                rec("screen dump: %s" % e)

        state["phase"] = "done"
        time.sleep(0.2)
        _request_quit()

    threading.Thread(target=probe, daemon=True).start()

    import renpy.main as renpy_main

    rec("entering renpy.main.main()")
    try:
        renpy_main.main()
        rec("main returned")
    except BaseException as e:
        rec("main exit %s: %s" % (type(e).__name__, e))

    selected_ok = bool(state.get("selected_ok"))
    hover_ok = bool(state.get("hover_ok"))
    tree_ok = bool(state.get("tree_ok"))
    # Product AC: selected wipe chrome AND hover ColorizeMatrix must both land.
    # tree_ok alone is insufficient (shaders present ≠ pixels yellow).
    ok = selected_ok and hover_ok
    body = [
        "gate=hmc_prefs_nav_effects",
        "ok=%s" % ok,
        "selected_ok=%s" % selected_ok,
        "hover_ok=%s" % hover_ok,
        "hover_via_mouse=%s" % bool(state.get("hover_via_mouse")),
        "hover_via_force=%s" % bool(state.get("hover_via_force")),
        "tree_ok=%s" % tree_ok,
        "phase=%s" % state.get("phase"),
    ]
    body.extend("log.%s" % m for m in lines)
    out.write_text("\n".join(body) + "\n")
    rec("wrote %s ok=%s" % (out, ok))


if __name__ == "__main__":
    run()
