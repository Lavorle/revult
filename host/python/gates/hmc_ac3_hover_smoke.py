"""
HuangmeiC AC3 chrome wipe hover thrash (product path).

Gate name: hmc_ac3_hover_smoke  (RENPY_HOST_GATE=hmc_ac3_hover_smoke)

Boots product HuangmeiC, waits for main_menu, samples baseline chrome
(logo + Movie + full dock), then hovers each dock button 3× (idle→hover→idle)
and re-samples after each thrash. Optionally focuses/selects without leaving
main menu (mouse motion + restart_interaction; no ShowMenu activation).

AC3 pass: any chrome wipe on hover/select fails; logo + Movie + full dock
remain until real ShowMenu/quit.

Writes:
  host/target/gate-hmc_ac3_hover_smoke.txt
  /tmp/huangmeic-ab/ac3-hover-smoke.log  (also mirrored)

Note: no from __future__; host run_file prepends imports.
"""

import os
import sys
import threading
import time
import traceback
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---



def _base():
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    line = f"[hmc_ac3] {msg}\n"
    try:
        sys.__stdout__.write(line)
        sys.__stdout__.flush()
    except Exception:
        pass
    for p in (
        "/tmp/hmc_ac3_hover_smoke.log",
        "/tmp/huangmeic-ab/ac3-hover-smoke.log",
    ):
        try:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            open(p, "a").write(msg + "\n")
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
    """Mirror hmc_nav_chrome_product stubs (sound/pygame/uguu/ecsign)."""
    import types

    try:
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound")
    except Exception as e:
        _log(f"renpysound soft-fail: {e}")

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
            _log(f"import_as_pygame soft-fail: {e}")
        _log("pygame host shim ok")
    except Exception as e:
        _log(f"pygame soft-fail: {e}")

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
        pkg.uguu = _uguu
        pkg.gl = _uguu
        try:
            import renpy

            renpy.uguu = pkg
        except Exception:
            pass
        _log("uguu host stub installed")
    except Exception as e:
        _log(f"uguu soft-fail: {e}")

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            _renpy_pkg.ecsign = _ecsign
        except Exception:
            pass
        _log("ecsign host stub installed")
    except Exception as e:
        _log(f"ecsign soft-fail: {e}")


def _pump(ms=80):
    """Let product interact drain injected mouse motion / redraw."""
    try:
        import renpy_host

        if hasattr(renpy_host, "wait_until") and hasattr(renpy_host, "get_ticks_ms"):
            deadline = int(renpy_host.get_ticks_ms()) + int(ms)
            renpy_host.wait_until(deadline)
            return
    except Exception:
        pass
    time.sleep(max(0.01, ms / 1000.0))


def _sample_rt():
    """Read the product game RT after pumping interact — no force rebuild.

    Side-thread force-redraw races product interact (``draw_model outside
    begin_frame``) and can wipe Movie/dock permanently. AC3 samples whatever
    the live present path last committed.
    """
    import renpy_host

    # Pump so injected mouse motion is drained by product interact + present.
    _pump(120)
    _pump(80)
    pres = {"path": "live_rt_only", "error": None}
    try:
        w, h, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        return {
            "ok": False,
            "error": f"read:{e}",
            "present": pres,
        }
    if not w or not h or not rgba:
        return {"ok": False, "error": "empty_rt", "present": pres}

    def band_stats(y0, y1, step_y=2, step_x=4):
        n = 0
        sr = sg = sb = 0
        nonclear = 0
        pure_black = 0
        for y in range(max(0, y0), min(h, y1), step_y):
            row = y * w * 4
            for x in range(0, w, step_x):
                i = row + x * 4
                r, g, b = rgba[i], rgba[i + 1], rgba[i + 2]
                sr += r
                sg += g
                sb += b
                n += 1
                # host clear ≈ (13,13,20)
                if abs(r - 13) > 12 or abs(g - 13) > 12 or abs(b - 20) > 12:
                    nonclear += 1
                if r < 8 and g < 8 and b < 8:
                    pure_black += 1
        if not n:
            return {
                "mean": (0.0, 0.0, 0.0),
                "nonclear_frac": 0.0,
                "pure_black_frac": 0.0,
                "n": 0,
            }
        return {
            "mean": (sr / n, sg / n, sb / n),
            "nonclear_frac": nonclear / float(n),
            "pure_black_frac": pure_black / float(n),
            "n": n,
        }

    full = band_stats(0, h, step_y=4, step_x=8)
    logo = band_stats(int(h * 0.02), int(h * 0.28), step_y=2, step_x=4)
    movie = band_stats(int(h * 0.30), int(h * 0.78), step_y=3, step_x=6)
    dock = band_stats(int(h * 0.88), h, step_y=1, step_x=3)

    arena = {}
    try:
        if hasattr(renpy_host, "sample_texture_count"):
            arena["sample_tex"] = int(renpy_host.sample_texture_count())
        if hasattr(renpy_host, "texture_order_len"):
            arena["order_len"] = int(renpy_host.texture_order_len())
        if hasattr(renpy_host, "texture_map_len"):
            arena["map_len"] = int(renpy_host.texture_map_len())
    except Exception as e:
        arena["error"] = str(e)

    return {
        "ok": True,
        "w": w,
        "h": h,
        "full": full,
        "logo": logo,
        "movie": movie,
        "dock": dock,
        "arena": arena,
        "present": {"path": pres.get("path"), "error": pres.get("error")},
    }


def _walk_dock_hosttex(surftree):
    """Collect HostTexture leaves with oy>=900 (dock band) and any large Movie-ish."""
    from renpy.wgpu.draw import HostTexture  # type: ignore

    dock = []
    movie_like = []
    logo_like = []
    n_tex = 0
    dead = 0

    def walk(node, ox=0.0, oy=0.0, depth=0):
        nonlocal n_tex, dead
        if node is None or depth > 40:
            return
        if isinstance(node, HostTexture):
            n_tex += 1
            alive = True
            try:
                import renpy_host

                if hasattr(renpy_host, "texture_alive"):
                    alive = bool(renpy_host.texture_alive(int(node.handle)))
            except Exception:
                pass
            if not alive:
                dead += 1
            entry = {
                "handle": int(getattr(node, "handle", 0) or 0),
                "w": int(getattr(node, "w", 0) or 0),
                "h": int(getattr(node, "h", 0) or 0),
                "ox": float(ox),
                "oy": float(oy),
                "alive": alive,
            }
            if oy >= 900:
                dock.append(entry)
            # Movie full-bleed ≈ 1920×1080 at origin
            if entry["w"] >= 1800 and entry["h"] >= 900 and oy < 200:
                movie_like.append(entry)
            # logo often upper-center-ish smaller PNG
            if 100 <= entry["w"] <= 900 and 80 <= entry["h"] <= 500 and oy < 400:
                logo_like.append(entry)
            return
        # Render-like: children
        children = getattr(node, "children", None)
        if children is None and hasattr(node, "blits"):
            children = getattr(node, "blits", None)
        try:
            if children is None and hasattr(node, "__iter__"):
                # Render iterates (child, x, y)
                for item in node:
                    if isinstance(item, tuple) and len(item) >= 3:
                        c, cx, cy = item[0], item[1], item[2]
                        walk(c, ox + float(cx or 0), oy + float(cy or 0), depth + 1)
                    else:
                        walk(item, ox, oy, depth + 1)
                return
        except Exception:
            pass
        if children:
            try:
                for item in children:
                    if isinstance(item, tuple) and len(item) >= 3:
                        c, cx, cy = item[0], item[1], item[2]
                        walk(c, ox + float(cx or 0), oy + float(cy or 0), depth + 1)
                    else:
                        walk(item, ox, oy, depth + 1)
            except Exception:
                pass
        for attr in ("cached_texture", "texture", "cached_model"):
            try:
                walk(getattr(node, attr, None), ox, oy, depth + 1)
            except Exception:
                pass

    walk(surftree)
    return {
        "n_tex": n_tex,
        "dead": dead,
        "dock": dock,
        "movie_like": movie_like,
        "logo_like": logo_like,
    }


def _surftree_snapshot():
    """Walk live iface.surftree only — never force-rebuild."""
    info = {"path": "live_iface.surftree", "error": None, "surftree": None}
    try:
        import interact_helpers as ih

        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            info["error"] = f"iface:{why}"
        else:
            st = getattr(iface, "surftree", None)
            info["surftree"] = st
            if st is None:
                info["error"] = "surftree_absent"
    except Exception as e:
        info["error"] = str(e)
        st = None
    else:
        st = info.get("surftree")
    walk = (
        _walk_dock_hosttex(st)
        if st is not None
        else {
            "n_tex": 0,
            "dead": 0,
            "dock": [],
            "movie_like": [],
            "logo_like": [],
            "error": "no_surftree",
        }
    )
    return {"present": info, "walk": walk}


def _collect_focus_rects():
    """Return list of {action, rect, type, prefix} from renpy.display.focus.focus_list."""
    import renpy

    out = []
    try:
        fl = list(getattr(renpy.display.focus, "focus_list", None) or [])
        for f in fl:
            try:
                w = getattr(f, "widget", None) or f
                action = getattr(w, "action", None)
                a = repr(action) if action is not None else ""
                fx = getattr(f, "x", None)
                fy = getattr(f, "y", None)
                fw = getattr(f, "w", None)
                fh = getattr(f, "h", None)
                if fx is None:
                    fr = getattr(f, "focus_rect", None) or getattr(w, "focus_rect", None)
                    if fr:
                        fx, fy, fw, fh = fr[0], fr[1], fr[2], fr[3]
                style = getattr(w, "style", None)
                prefix = getattr(style, "prefix", None) if style is not None else None
                out.append(
                    {
                        "action": a,
                        "type": type(w).__name__,
                        "rect": (
                            float(fx) if fx is not None else None,
                            float(fy) if fy is not None else None,
                            float(fw) if fw is not None else None,
                            float(fh) if fh is not None else None,
                        ),
                        "prefix": prefix,
                    }
                )
            except Exception as e:
                out.append({"error": str(e)})
    except Exception as e:
        return {"error": str(e), "items": []}
    return {"items": out}


# Known main_menu dock order (virtual) from prior idle probe.
# Used when focus_list not yet populated.
_DOCK_GEOM = [
    ("start", "Start", 131.0 + 120.5, 972.0 + 46.0),
    ("continue", "FileLoad", 367.0 + 120.5, 972.0 + 46.0),
    ("load", "ShowMenu(screen='load')", 603.0 + 120.5, 972.0 + 46.0),
    ("extra", "appreciation", 839.0 + 120.5, 972.0 + 46.0),
    ("flowchart", "flowchart", 1075.0 + 120.5, 972.0 + 46.0),
    ("config", "preferences", 1311.0 + 120.5, 972.0 + 46.0),
    ("exit", "ConfirmAction", 1547.0 + 120.5, 972.0 + 46.0),
]


def _dock_targets():
    """Prefer live focus rects; fall back to geometric dock centers."""
    fl = _collect_focus_rects()
    items = fl.get("items") or []
    targets = []
    used = set()
    for name, needle, gx, gy in _DOCK_GEOM:
        hit = None
        for it in items:
            a = it.get("action") or ""
            r = it.get("rect") or (None, None, None, None)
            if needle in a and r[0] is not None and r[2]:
                cx = r[0] + r[2] / 2.0
                cy = r[1] + r[3] / 2.0
                hit = (name, a, cx, cy, "focus")
                break
        if hit is None:
            hit = (name, needle, gx, gy, "geom")
        targets.append(hit)
        used.add(name)
    return targets, fl


def _mouse_move(vx, vy):
    """Move mouse to virtual coords (scaled to physical)."""
    import renpy_host

    import renpy

    pw = ph = None
    vw, vh = 1920, 1080
    draw = getattr(renpy.display, "draw", None)
    if draw is not None:
        try:
            ps = getattr(draw, "physical_size", None)
            if ps:
                pw, ph = int(ps[0]), int(ps[1])
        except Exception:
            pass
        try:
            vs = getattr(draw, "virtual_size", None)
            if vs:
                vw, vh = int(vs[0]), int(vs[1])
        except Exception:
            pass
    if not pw or not ph:
        try:
            w, h, _ = renpy_host.read_game_rt_rgba()
            pw, ph = int(w), int(h)
        except Exception:
            pw, ph = 1280, 720
    px = int(float(vx) * pw / float(vw))
    py = int(float(vy) * ph / float(vh))
    try:
        from renpy import pygame

        pygame.mouse.set_pos((px, py))
    except Exception:
        pass
    try:
        renpy_host.inject_mouse(int(px), int(py), 0, False)
    except Exception as e:
        return f"fail:{e}", px, py
    return "ok", px, py


def _judge_chrome(sample, walk, label, baseline=None):
    """
    AC3 chrome present heuristics — **game RT bands are authoritative**.

    Surftree walk is diagnostic only: live iface.surftree may lag a frame and
    dock HostTexture counts flap under hover restyle; RT nonclear is the wipe.
    Wipe fail modes:
      - dock band collapses to clear (nonclear≈0)
      - movie center collapses to clear
      - full RT featureless clear vs baseline
    """
    reasons = []
    notes = []
    ok = True
    if not sample.get("ok"):
        return False, ["sample_fail:{}".format(sample.get("error"))]

    dock = sample.get("dock") or {}
    movie = sample.get("movie") or {}
    logo = sample.get("logo") or {}
    full = sample.get("full") or {}

    dock_nc = float(dock.get("nonclear_frac") or 0.0)
    movie_nc = float(movie.get("nonclear_frac") or 0.0)
    logo_nc = float(logo.get("nonclear_frac") or 0.0)
    full_nc = float(full.get("nonclear_frac") or 0.0)

    # Dock must be substantially non-clear (prior idle ≈0.72–1.0)
    if dock_nc < 0.25:
        ok = False
        reasons.append(f"dock_wipe nonclear={dock_nc:.3f}")
    # Movie center must not be pure clear
    if movie_nc < 0.20:
        ok = False
        reasons.append(f"movie_wipe nonclear={movie_nc:.3f}")
    # Logo band: softer — logo may be semi-transparent over movie
    if logo_nc < 0.08 and full_nc < 0.20:
        ok = False
        reasons.append(f"logo_or_full_wipe logo_nc={logo_nc:.3f} full_nc={full_nc:.3f}")

    dock_nodes = (walk or {}).get("dock") or []
    dead = int((walk or {}).get("dead") or 0)
    n_dock = len(dock_nodes)
    n_dead_dock = sum(1 for d in dock_nodes if not d.get("alive"))
    notes.append("dock_tex=%d dead_dock=%d dead_all=%d" % (n_dock, n_dead_dock, dead))

    # Dead dock handles only fail when RT also shows wipe (avoids false red
    # when walk sees one-frame stale leaves under hover restyle).
    if n_dead_dock >= 4 and dock_nc < 0.40:
        ok = False
        reasons.append("dock_dead_handles=%d/%d with weak RT" % (n_dead_dock, n_dock))

    if baseline is not None:
        b_dock = float(((baseline.get("dock") or {}).get("nonclear_frac")) or 0.0)
        b_movie = float(((baseline.get("movie") or {}).get("nonclear_frac")) or 0.0)
        if b_dock > 0.40 and dock_nc < b_dock * 0.35:
            ok = False
            reasons.append(
                f"dock_collapse vs baseline {b_dock:.3f} -> {dock_nc:.3f}"
            )
        if b_movie > 0.40 and movie_nc < b_movie * 0.25:
            ok = False
            reasons.append(
                f"movie_collapse vs baseline {b_movie:.3f} -> {movie_nc:.3f}"
            )

    if not reasons:
        reasons.append(
            "ok dock_nc={:.3f} movie_nc={:.3f} logo_nc={:.3f} {}".format(dock_nc, movie_nc, logo_nc, " ".join(notes))
        )
    else:
        reasons.extend(notes)
    return ok, reasons


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_ac3_hover_smoke.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    Path("/tmp/huangmeic-ab").mkdir(parents=True, exist_ok=True)
    # truncate log
    try:
        open("/tmp/huangmeic-ab/ac3-hover-smoke.log", "w").write("")
        open("/tmp/hmc_ac3_hover_smoke.log", "w").write("")
    except Exception:
        pass

    lines = []
    state = {
        "main_menu": False,
        "error": None,
        "pass": False,
        "checks": [],
        "failures": [],
    }

    def rec(m):
        lines.append(m)
        _log(m)

    game = os.environ.get("RENPY_HOST_GAME") or str(
        base / "host" / "playtests" / "HuangmeiC"
    )
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    # AC2 movie budget for thrash under full-bleed
    os.environ.setdefault("RENPY_HOST_MOVIE_MAX_FRAMES", "36")
    os.environ.setdefault("RENPY_HOST_MOVIE_W", "1920")
    os.environ.setdefault("RENPY_HOST_MOVIE_H", "1080")
    os.environ.setdefault("RENPY_HOST_MOVIE_LAYOUT_W", "1920")
    os.environ.setdefault("RENPY_HOST_MOVIE_LAYOUT_H", "1080")
    os.environ.setdefault("RENPY_HOST_MOVIE_ASSERT", "1")
    os.environ.setdefault("RENPY_HOST_ASSERT_VIRTUAL", "1")
    os.environ.setdefault("RENPY_HOST_PHASE0_SIGNALS", "1")
    _clear_falsey_skip("RENPY_SKIP_MAIN_MENU")
    _clear_falsey_skip("RENPY_SKIP_SPLASHSCREEN")

    gates = str(base / "host" / "python" / "gates")
    host_py = str(base / "host" / "python")
    if gates not in sys.path:
        sys.path.insert(0, gates)
    if host_py not in sys.path:
        sys.path.insert(0, host_py)

    import bootstrap as boot

    for name, call in (
        ("import_renpy", boot.stage_import_renpy),
        ("import_all", boot.stage_import_all),
        ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ):
        good, _miss, err, _extra = call()
        rec(f"stage {name} good={good} err={err!r}")
        if not good:
            body = f"gate=hmc_ac3_hover_smoke\nok=False\nerror={err}\n"
            out.write_text(body)
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
        rec(f"main_host: {e}")

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
        args = renpy.arguments.bootstrap()
        renpy.game.args = args
        rec("args command={} basedir={}".format(getattr(args, "command", None), basedir))
    except Exception as e:
        rec(f"args fail: {e}")
        rec(traceback.format_exc())

    _pre_main_host_stubs()

    def injector():
        rec("waiting main_menu")
        for i in range(600):
            try:
                if bool(getattr(renpy.store, "main_menu", False)):
                    state["main_menu"] = True
                    rec("main_menu at tick=%d" % i)
                    break
            except Exception:
                pass
            time.sleep(0.05)
        if not state["main_menu"]:
            state["error"] = "main_menu_timeout"
            rec("main_menu timeout")
            _request_quit()
            return

        # Stabilize Movie + chrome
        time.sleep(2.5)

        # --- Baseline ---
        rec("=" * 72)
        rec("PHASE BASELINE")
        rec("=" * 72)
        try:
            sample0 = _sample_rt()
            snap0 = _surftree_snapshot()
            walk0 = snap0.get("walk") or {}
            ok0, why0 = _judge_chrome(
                sample0, walk0, "baseline", baseline=None
            )
            # attach walk into baseline for later drop checks
            sample0 = dict(sample0)
            sample0["walk"] = walk0
            state["baseline"] = sample0
            rec(
                "BASELINE ok=%s w=%s h=%s dock_nc=%.3f movie_nc=%.3f logo_nc=%.3f "
                "arena=%s dock_tex=%d movie_like=%d logo_like=%d dead=%d reasons=%s"
                % (
                    ok0,
                    sample0.get("w"),
                    sample0.get("h"),
                    float((sample0.get("dock") or {}).get("nonclear_frac") or 0),
                    float((sample0.get("movie") or {}).get("nonclear_frac") or 0),
                    float((sample0.get("logo") or {}).get("nonclear_frac") or 0),
                    sample0.get("arena"),
                    len(walk0.get("dock") or []),
                    len(walk0.get("movie_like") or []),
                    len(walk0.get("logo_like") or []),
                    int(walk0.get("dead") or 0),
                    why0,
                )
            )
            state["checks"].append(("baseline", ok0, why0))
            if not ok0:
                state["failures"].append(("baseline", why0))
                # continue thrash anyway — still collect evidence
        except Exception as e:
            rec(f"BASELINE fail: {e}")
            rec(traceback.format_exc()[-1200:])
            state["error"] = f"baseline:{e}"
            _request_quit()
            return

        targets, fl = _dock_targets()
        rec("FOCUS_LIST raw_n={}".format(len(fl.get("items") or [])))
        for it in (fl.get("items") or [])[:12]:
            rec(f"  focus {it}")
        rec("TARGETS n=%d" % len(targets))
        for t in targets:
            rec("  target name={} action={} cx={:.1f} cy={:.1f} via={}".format(*t))

        # --- Hover each dock button 3× ---
        for name, action, cx, cy, via in targets:
            for rep in range(3):
                label = "hover_%s_%d" % (name, rep + 1)
                rec("-" * 60)
                rec(f"PHASE {label} action={action} via={via}")
                # idle off-dock first (center screen) so transition is real
                if rep == 0:
                    r_off, px_off, py_off = _mouse_move(960, 540)
                    rec(f"mouse_off result={r_off} phys=({px_off},{py_off})")
                    try:
                        renpy.restart_interaction()
                    except Exception:
                        pass
                    time.sleep(0.25)

                r, px, py = _mouse_move(cx, cy)
                rec(f"mouse_on result={r} virt=({cx:.1f},{cy:.1f}) phys=({px},{py})")
                try:
                    renpy.restart_interaction()
                except Exception:
                    pass
                try:
                    import renpy_host as _rh

                    if hasattr(_rh, "request_redraw"):
                        _rh.request_redraw()
                except Exception:
                    pass
                # Product interact must drain motion + re-present (no force redraw).
                _pump(200)
                _pump(200)
                time.sleep(0.15)

                try:
                    sample = _sample_rt()
                    snap = _surftree_snapshot()
                    walk = snap.get("walk") or {}
                    ok, why = _judge_chrome(
                        sample, walk, label, baseline=state.get("baseline")
                    )
                    rec(
                        "%s ok=%s dock_nc=%.3f movie_nc=%.3f logo_nc=%.3f "
                        "arena=%s dock_tex=%d dead=%d reasons=%s"
                        % (
                            label,
                            ok,
                            float((sample.get("dock") or {}).get("nonclear_frac") or 0),
                            float((sample.get("movie") or {}).get("nonclear_frac") or 0),
                            float((sample.get("logo") or {}).get("nonclear_frac") or 0),
                            sample.get("arena"),
                            len(walk.get("dock") or []),
                            int(walk.get("dead") or 0),
                            why,
                        )
                    )
                    # still on main_menu?
                    try:
                        mm = bool(getattr(renpy.store, "main_menu", False))
                    except Exception:
                        mm = "?"
                    rec(f"{label} still_main_menu={mm}")
                    state["checks"].append((label, ok, why))
                    if not ok:
                        state["failures"].append((label, why))
                except Exception as e:
                    rec(f"{label} sample fail: {e}")
                    rec(traceback.format_exc()[-800:])
                    state["failures"].append((label, [f"exception:{e}"]))

                # back to idle center
                _r2, _, _ = _mouse_move(960, 540)
                try:
                    renpy.restart_interaction()
                except Exception:
                    pass
                time.sleep(0.20)

            # post-button idle sample (select/focus residual)
            label = f"post_{name}_idle"
            try:
                sample = _sample_rt()
                snap = _surftree_snapshot()
                walk = snap.get("walk") or {}
                ok, why = _judge_chrome(
                    sample, walk, label, baseline=state.get("baseline")
                )
                rec(
                    "%s ok=%s dock_nc=%.3f movie_nc=%.3f dock_tex=%d reasons=%s"
                    % (
                        label,
                        ok,
                        float((sample.get("dock") or {}).get("nonclear_frac") or 0),
                        float((sample.get("movie") or {}).get("nonclear_frac") or 0),
                        len(walk.get("dock") or []),
                        why,
                    )
                )
                state["checks"].append((label, ok, why))
                if not ok:
                    state["failures"].append((label, why))
            except Exception as e:
                rec(f"{label} fail: {e}")
                state["failures"].append((label, [f"exception:{e}"]))

        # --- Light focus/select without leaving main_menu ---
        # Hover start then inject a non-activating focus path: keyboard focus only.
        rec("=" * 72)
        rec("PHASE FOCUS_SELECT_STAY")
        rec("=" * 72)
        try:
            # focus Start via mouse, then continue via Tab-like next focus if available
            for name, action, cx, cy, via in targets[:4]:
                r, px, py = _mouse_move(cx, cy)
                try:
                    renpy.restart_interaction()
                except Exception:
                    pass
                time.sleep(0.35)
                # Do NOT click — click may ShowMenu/Start. Focus alone is AC3 select path.
                sample = _sample_rt()
                snap = _surftree_snapshot()
                walk = snap.get("walk") or {}
                ok, why = _judge_chrome(
                    sample, walk, f"focus_{name}", baseline=state.get("baseline")
                )
                try:
                    mm = bool(getattr(renpy.store, "main_menu", False))
                except Exception:
                    mm = "?"
                rec(
                    "focus_%s ok=%s main_menu=%s dock_nc=%.3f movie_nc=%.3f dock_tex=%d reasons=%s"
                    % (
                        name,
                        ok,
                        mm,
                        float((sample.get("dock") or {}).get("nonclear_frac") or 0),
                        float((sample.get("movie") or {}).get("nonclear_frac") or 0),
                        len(walk.get("dock") or []),
                        why,
                    )
                )
                state["checks"].append((f"focus_{name}", ok, why))
                if not ok:
                    state["failures"].append((f"focus_{name}", why))
                if mm is False:
                    state["failures"].append(
                        (f"focus_{name}", ["left_main_menu_unexpected"])
                    )
                    break
        except Exception as e:
            rec(f"FOCUS_SELECT_STAY fail: {e}")
            rec(traceback.format_exc()[-800:])

        # Final idle
        rec("=" * 72)
        rec("PHASE FINAL_IDLE")
        rec("=" * 72)
        try:
            _mouse_move(960, 540)
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            time.sleep(0.5)
            sample = _sample_rt()
            snap = _surftree_snapshot()
            walk = snap.get("walk") or {}
            ok, why = _judge_chrome(
                sample, walk, "final", baseline=state.get("baseline")
            )
            rec(
                "FINAL ok=%s dock_nc=%.3f movie_nc=%.3f logo_nc=%.3f arena=%s "
                "dock_tex=%d dead=%d reasons=%s"
                % (
                    ok,
                    float((sample.get("dock") or {}).get("nonclear_frac") or 0),
                    float((sample.get("movie") or {}).get("nonclear_frac") or 0),
                    float((sample.get("logo") or {}).get("nonclear_frac") or 0),
                    sample.get("arena"),
                    len(walk.get("dock") or []),
                    int(walk.get("dead") or 0),
                    why,
                )
            )
            state["checks"].append(("final", ok, why))
            if not ok:
                state["failures"].append(("final", why))
        except Exception as e:
            rec(f"FINAL fail: {e}")
            state["failures"].append(("final", [f"exception:{e}"]))

        n_fail = len(state["failures"])
        n_chk = len(state["checks"])
        state["pass"] = (
            bool(state.get("main_menu"))
            and n_chk >= 8
            and n_fail == 0
            and not state.get("error")
        )
        rec("=" * 72)
        rec(
            "SUMMARY pass=%s checks=%d failures=%d error=%s"
            % (state["pass"], n_chk, n_fail, state.get("error"))
        )
        for lab, why in state["failures"]:
            rec(f"FAIL {lab} -> {why}")
        rec("=" * 72)

        time.sleep(0.2)
        _request_quit()

    t = threading.Thread(target=injector, daemon=True)
    t.start()
    try:
        import renpy.main as renpy_main

        renpy_main.main()
    except BaseException as e:
        rec(f"main exit {type(e).__name__}: {e}")
    t.join(timeout=3.0)

    ok = bool(state.get("pass"))
    header = [
        "gate=hmc_ac3_hover_smoke",
        f"ok={ok}",
        "main_menu={}".format(state.get("main_menu")),
        "checks={}".format(len(state.get("checks") or [])),
        "failures={}".format(len(state.get("failures") or [])),
        "error={}".format(state.get("error")),
        "ac3=%s" % ("PASS" if ok else "FAIL"),
        "",
    ]
    if state.get("failures"):
        header.append("failure_list:")
        for lab, why in state["failures"]:
            header.append(f"  - {lab}: {why}")
        header.append("")
    body = "\n".join(header + lines) + "\n"
    out.write_text(body)
    try:
        Path("/tmp/huangmeic-ab/ac3-hover-smoke-gate.txt").write_text(body)
    except Exception:
        pass
    try:
        sys.__stdout__.write(body[-6000:])
        sys.__stdout__.flush()
    except Exception:
        pass
    _request_quit()
    if not ok:
        raise RuntimeError(
            "hmc_ac3_hover_smoke failed: failures={} error={}".format(len(state.get("failures") or []), state.get("error"))
        )


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

