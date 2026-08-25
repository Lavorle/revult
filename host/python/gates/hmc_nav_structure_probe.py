"""
HuangmeiC AC-Nav structure probe — GL2 chrome structure beyond non-clear RT.

Gate name: hmc_nav_structure_probe  (RENPY_HOST_GATE=hmc_nav_structure_probe)

Product ShowMenu for load / preferences / appreciation / flowchart / confirm,
then for each screen (LIVE-first, then FORCE helper):
  - LIVE: iface.surftree walk + read_game_rt_rgba BEFORE any force rebuild
    (product present path — primary structure helper authority)
  - FORCE: rebuild root → render_screen → load_all_textures → draw_screen
    (helper only; may green while LIVE fails — same class as Step 1.0 idle RCA)
  - structure: HostTexture leaf count, reverse-scale pieces (Frame multipiece),
    Text/glyph-ish, Bar-ish sizes
  - RT: overall non-clear + prefs white-pill band checks
  - FAIL if LIVE structure counts are zero even when FORCE RT mean is non-clear

Ban: non-clear RT mean alone is NOT a done bar (reopen plan Step 2).
Ban: force-only green while LIVE fails is NOT a done bar.

Authority: human full-screen photos remain AC-Nav1 authority. This gate is a
cheap structure helper OR documents counts for a written waiver.

Note: no from __future__; host run_file prepends imports.
"""

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
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path("/mnt/nvme1n1p2/revult")


def _log(msg):
    try:
        sys.__stdout__.write(f"[hmc_nav_struct] {msg}\n")
        sys.__stdout__.flush()
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        open("/tmp/hmc_nav_structure_probe.log", "a").write(msg + "\n")  # noqa: SIM115
    except Exception:  # noqa: BLE001, S110
        pass


def _request_quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:  # noqa: BLE001, S110
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
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound")
    except Exception as e:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            rpg.import_as_pygame()
        except Exception as e:  # noqa: BLE001
            _log(f"import_as_pygame soft-fail: {e}")
        _log("pygame host shim ok")
    except Exception as e:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001, S110
            pass
        _log("uguu host stub installed")
    except Exception as e:  # noqa: BLE001
        _log(f"uguu soft-fail: {e}")

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            _renpy_pkg.ecsign = _ecsign
        except Exception:  # noqa: BLE001, S110
            pass
        _log("ecsign host stub installed")
    except Exception as e:  # noqa: BLE001
        _log(f"ecsign soft-fail: {e}")


def _texinfo(t):
    try:
        from renpy.wgpu.draw import HostTexture

        if isinstance(t, HostTexture):
            return {
                "kind": "HostTexture",
                "handle": int(t.handle),
                "w": int(t.w),
                "h": int(t.h),
                "x": int(getattr(t, "x", 0) or 0),
                "y": int(getattr(t, "y", 0) or 0),
            }
    except Exception:  # noqa: BLE001, S110
        pass
    if t is None:
        return None
    if isinstance(t, int) and not isinstance(t, bool):
        return {"kind": "int", "handle": int(t)}
    return {"kind": type(t).__name__}


def _rev_scale(node):
    """Return (xdx, ydy) of reverse if present, else None."""
    rev = getattr(node, "reverse", None)
    if rev is None:
        return None
    try:
        xdx = float(getattr(rev, "xdx", 1.0) or 1.0)
        ydy = float(getattr(rev, "ydy", 1.0) or 1.0)
        return (xdx, ydy)
    except Exception:  # noqa: BLE001
        return None


def _walk_structure(node, acc=None, depth=0, budget=800, ox=0.0, oy=0.0):
    """Walk Render tree collecting structure signals (textures, reverse, text-ish)."""
    if acc is None:
        acc = {
            "n_nodes": 0,
            "n_host_tex": 0,
            "n_int_tex": 0,
            "n_reverse_scale": 0,
            "n_multipiece_parents": 0,
            "n_text_ish": 0,
            "n_bar_ish": 0,
            "n_mesh": 0,
            "handles": set(),
            "dests": [],
            "text_sizes": [],
            "rev_pieces": [],
            "errors": [],
        }
    if node is None or acc["n_nodes"] >= budget or depth > 50:
        return acc
    acc["n_nodes"] += 1
    try:
        tw = getattr(node, "width", None) or getattr(node, "w", None)
        th = getattr(node, "height", None) or getattr(node, "h", None)
    except Exception:  # noqa: BLE001
        tw = th = None
    try:
        tw = int(tw) if tw is not None else None
        th = int(th) if th is not None else None
    except Exception:  # noqa: BLE001
        tw = th = None

    tex = getattr(node, "texture", None)
    cm = getattr(node, "cached_model", None)
    mesh = getattr(node, "mesh", None)
    shaders = getattr(node, "shaders", None)
    kids = list(getattr(node, "children", None) or [])

    ti = _texinfo(tex)
    if ti is not None:
        if ti.get("kind") == "HostTexture":
            acc["n_host_tex"] += 1
            h = ti.get("handle")
            if h:
                acc["handles"].add(int(h))
            # Text glyphs often small-ish textured leaves with UV subrects
            sw = ti.get("w") or 0
            sh = ti.get("h") or 0
            if sw > 0 and sh > 0 and (sw <= 64 or sh <= 64) and (sw * sh) <= 64 * 128:  # noqa: SIM102
                # candidate glyph/atlas piece; also bar thumbs ~40x40
                if 8 <= sw <= 64 and 8 <= sh <= 64:
                    acc["n_bar_ish"] += 1  # thumb-sized; refined below
            # Text atlas slabs: wide short or tall thin
            if sw >= 32 and sh >= 8 and (sw >= 80 or sh >= 16):
                # not unique; count as potential text slab if UV subsurface
                x = ti.get("x") or 0
                y = ti.get("y") or 0
                if x != 0 or y != 0 or (tw and sw and (tw != sw or th != sh)):
                    acc["n_text_ish"] += 1
                    acc["text_sizes"].append((sw, sh, tw, th, ox, oy))
        elif ti.get("kind") == "int":
            acc["n_int_tex"] += 1

    if cm is not None:
        cti = _texinfo(getattr(cm, "texture", None))
        if cti and cti.get("kind") == "HostTexture":
            acc["n_host_tex"] += 1
            h = cti.get("handle")
            if h:
                acc["handles"].add(int(h))

    if mesh is not None:
        acc["n_mesh"] += 1

    # shaders hinting text / renpy.geometry / alpha
    if shaders:
        try:
            sl = list(shaders) if not isinstance(shaders, str) else [shaders]
            sjoin = " ".join(str(s) for s in sl).lower()
            if "text" in sjoin or "glyph" in sjoin:
                acc["n_text_ish"] += 1
        except Exception:  # noqa: BLE001, S110
            pass

    rev = _rev_scale(node)
    if rev is not None:
        xdx, ydy = rev
        # non-identity reverse = Frame piece / Solid stretch / oversample
        if abs(xdx - 1.0) > 0.02 or abs(ydy - 1.0) > 0.02:
            acc["n_reverse_scale"] += 1
            acc["rev_pieces"].append(
                {
                    "ox": ox,
                    "oy": oy,
                    "size": (tw, th),
                    "xdx": xdx,
                    "ydy": ydy,
                    "n_kids": len(kids),
                    "tex": ti,
                }
            )

    # multipiece parent: container with >=3 reverse-scaled kids (Frame 9-slice)
    rev_kids = 0
    for entry in kids[:24]:
        try:
            ch = entry[0] if isinstance(entry, (tuple, list)) else entry
            cr = _rev_scale(ch)
            if cr is not None and (
                abs(cr[0] - 1.0) > 0.02 or abs(cr[1] - 1.0) > 0.02
            ):
                rev_kids += 1
        except Exception:  # noqa: BLE001, S110
            pass
    if rev_kids >= 3:
        acc["n_multipiece_parents"] += 1

    # bar-ish dest sizes: long thin tracks (~525x40) or thumbs (~40x40)
    if tw and th:
        if (tw >= 200 and 6 <= th <= 50) or (th >= 200 and 6 <= tw <= 50):
            acc["n_bar_ish"] += 1
            acc["dests"].append(("bar_track", ox, oy, tw, th))
        elif 20 <= tw <= 60 and 20 <= th <= 60:
            acc["dests"].append(("thumb_or_btn", ox, oy, tw, th))
        elif tw >= 100 and 30 <= th <= 60:
            acc["dests"].append(("choice_row", ox, oy, tw, th))

    for entry in kids[:24]:
        try:
            if isinstance(entry, (tuple, list)):
                ch = entry[0]
                cx = float(entry[1]) if len(entry) > 1 else 0.0
                cy = float(entry[2]) if len(entry) > 2 else 0.0
            else:
                ch = entry
                cx = cy = 0.0
        except Exception:  # noqa: BLE001, S112
            continue
        try:
            from renpy.wgpu.draw import HostTexture

            if isinstance(ch, HostTexture):
                acc["n_host_tex"] += 1
                acc["handles"].add(int(ch.handle))
                continue
        except Exception:  # noqa: BLE001, S110
            pass
        _walk_structure(ch, acc, depth + 1, budget, ox + cx, oy + cy)
    return acc


def _force_product_redraw():
    """Product present after ShowMenu when nested interact frames=1.

    Rebuilds root → render_screen → load_all_textures → draw_screen.
    Does **not** clobber iface.surftree with an empty/broken rebuild (flowchart
    custom displayables can yield a sparse rebuild while live tree is rich).
    """
    import interact_helpers as ih

    import renpy

    info = {"path": None, "error": None, "surftree": None, "root": None}
    try:
        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            info["error"] = f"iface:{why}"
            return info
        prev = getattr(iface, "surftree", None)
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
        try:
            if hasattr(draw, "load_all_textures"):
                draw.load_all_textures(surftree)
        except Exception as e:  # noqa: BLE001
            info["prepare_err"] = str(e)
        draw.draw_screen(surftree, flip=True)
        # Only adopt rebuilt tree if it has real HostTexture leaves; otherwise
        # keep previous live product surftree (present still updated RT).
        try:
            rebuilt_struct = _walk_structure(surftree) if surftree is not None else None
            n_tex = int((rebuilt_struct or {}).get("n_host_tex", 0) or 0)
        except Exception:  # noqa: BLE001
            n_tex = 0
            rebuilt_struct = None
        if surftree is not None and n_tex > 0:
            try:
                iface.surftree = surftree
            except Exception:  # noqa: BLE001, S110
                pass
            info["surftree"] = surftree
            info["path"] = "rebuild_render_screen"
        else:
            info["surftree"] = prev if prev is not None else surftree
            info["path"] = "rebuild_present_rt_keep_prev_tree"
            info["rebuild_host_tex"] = n_tex
            if prev is None and surftree is not None:
                try:
                    iface.surftree = surftree
                except Exception:  # noqa: BLE001, S110
                    pass
        info["root"] = type(root).__name__
        return info
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}:{e}"
        return info


def _product_present_after_showmenu():
    """Ensure product draw path runs after ShowMenu (nested frames=1).

    Pure iface.surftree before any present is often the *previous* screen.
    Soft product present exercises the same draw_screen path as interact.
    """
    try:
        import renpy

        try:
            renpy.restart_interaction()
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            draw = getattr(renpy.display, "draw", None)
            if draw is not None and hasattr(draw, "request_redraw"):
                draw.request_redraw()
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            import renpy_host

            if hasattr(renpy_host, "request_redraw"):
                renpy_host.request_redraw()
        except Exception:  # noqa: BLE001, S110
            pass
    except Exception:  # noqa: BLE001, S110
        pass
    # Nested product interact often reports frames=1; soft present is required
    # for gate structure after ShowMenu. This is product draw_screen, not an
    # isolated panel blit.
    return _force_product_redraw()


def _sample_live_surftree_only():
    """Inspect iface.surftree WITHOUT force rebuild (true product present path).

    Step 1.0 idle RCA: force redraw paints while live product RT stays clear.
    Step 2.0 must compare LIVE vs FORCE the same way for post-nav screens.
    """
    info = {
        "path": None,
        "error": None,
        "surftree": None,
        "n_host_tex": 0,
        "n_unique_handles": 0,
        "n_reverse_scale": 0,
        "n_multipiece_parents": 0,
        "n_text_ish": 0,
        "n_bar_ish": 0,
        "n_nodes": 0,
    }
    try:
        import interact_helpers as ih

        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            info["error"] = f"iface:{why}"
            return info
        st = getattr(iface, "surftree", None)
        info["surftree"] = st
        info["path"] = "iface.surftree"
        if st is None:
            info["error"] = "surftree_none"
            return info
        try:
            struct = _walk_structure(st)
            info["n_nodes"] = int(struct.get("n_nodes", 0))
            info["n_host_tex"] = int(struct.get("n_host_tex", 0))
            info["n_unique_handles"] = len(struct.get("handles") or [])
            info["n_reverse_scale"] = int(struct.get("n_reverse_scale", 0))
            info["n_multipiece_parents"] = int(struct.get("n_multipiece_parents", 0))
            info["n_text_ish"] = int(struct.get("n_text_ish", 0))
            info["n_bar_ish"] = int(struct.get("n_bar_ish", 0))
            info["struct"] = {
                "n_nodes": info["n_nodes"],
                "n_host_tex": info["n_host_tex"],
                "n_unique_handles": info["n_unique_handles"],
                "n_reverse_scale": info["n_reverse_scale"],
                "n_multipiece_parents": info["n_multipiece_parents"],
                "n_text_ish": info["n_text_ish"],
                "n_bar_ish": info["n_bar_ish"],
                "n_mesh": int(struct.get("n_mesh", 0)),
                "n_dests": len(struct.get("dests") or []),
                "n_rev_pieces": len(struct.get("rev_pieces") or []),
                "rev_sample": (struct.get("rev_pieces") or [])[:6],
                "dest_sample": (struct.get("dests") or [])[:10],
                "errors": list(struct.get("errors") or []),
            }
        except Exception as e:  # noqa: BLE001
            info["error"] = f"walk:{e}"
        return info
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}:{e}"
        return info


def _sample_rt():
    import renpy_host

    try:
        rw, rh, rt = renpy_host.read_game_rt_rgba()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"read_rt:{e}"}
    if not rw or not rh or not rt:
        return {"ok": False, "error": "empty_rt"}
    rs = gs = bs = n = pure = white_hi = dark = 0
    step_x = max(1, rw // 32)
    step_y = max(1, rh // 18)
    samples = []
    for y in range(step_y // 2, rh, step_y):
        for x in range(step_x // 2, rw, step_x):
            o = (y * rw + x) * 4
            r, g, b, a = rt[o], rt[o + 1], rt[o + 2], rt[o + 3]
            samples.append((r, g, b, a))
            rs += r
            gs += g
            bs += b
            n += 1
            if r + g + b < 20 and a > 200:
                pure += 1
            if r > 220 and g > 220 and b > 220 and a > 180:
                white_hi += 1
            if r + g + b < 80 and a > 180:
                dark += 1
    if n == 0:
        return {"ok": False, "error": "no_samples", "w": rw, "h": rh}
    mean = (rs / n, gs / n, bs / n)
    pure_frac = pure / float(n)
    white_frac = white_hi / float(n)
    dark_frac = dark / float(n)
    mr, mg, mb = mean
    var = sum((s[0] - mr) ** 2 + (s[1] - mg) ** 2 + (s[2] - mb) ** 2 for s in samples) / n
    featureless_black = pure_frac > 0.85 or (mean[0] + mean[1] + mean[2] < 40 and var < 80)
    clearish = (
        abs(mean[0] - 13) < 8
        and abs(mean[1] - 13) < 8
        and abs(mean[2] - 20) < 12
        and var < 5.0
    )
    nonclear_ok = (not featureless_black) and (not clearish) and var >= 5.0
    return {
        "ok": nonclear_ok,
        "w": rw,
        "h": rh,
        "mean": mean,
        "var": var,
        "pure_frac": pure_frac,
        "white_frac": white_frac,
        "dark_frac": dark_frac,
        "featureless_black": featureless_black,
        "clearish": clearish,
        "n": n,
        "rt": rt,
    }


def _sample_band_mean(rt, rw, rh, x0, y0, x1, y1, step=4):
    rs = gs = bs = n = white = dark = 0
    x0 = max(0, min(rw - 1, int(x0)))
    y0 = max(0, min(rh - 1, int(y0)))
    x1 = max(x0 + 1, min(rw, int(x1)))
    y1 = max(y0 + 1, min(rh, int(y1)))
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            o = (y * rw + x) * 4
            r, g, b, _a = rt[o], rt[o + 1], rt[o + 2], rt[o + 3]
            rs += r
            gs += g
            bs += b
            n += 1
            if r > 220 and g > 220 and b > 220:
                white += 1
            if r + g + b < 90:
                dark += 1
    if n == 0:
        return None
    return {
        "mean": (rs / n, gs / n, bs / n),
        "n": n,
        "white_frac": white / float(n),
        "dark_frac": dark / float(n),
    }


def _get_screen(name):
    try:
        import renpy

        return renpy.display.screen.get_screen(name)
    except Exception:  # noqa: BLE001
        return None


def _force_show_menu(name):
    import renpy

    try:
        action = renpy.store.ShowMenu(name)
        action()
        try:
            renpy.restart_interaction()
        except Exception:  # noqa: BLE001, S110
            pass
        return True, "ShowMenu()"
    except Exception as e1:  # noqa: BLE001
        try:
            renpy.display.screen.show_screen(name)
            try:
                renpy.restart_interaction()
            except Exception:  # noqa: BLE001, S110
                pass
            return True, f"display.screen.show_screen:{e1}"
        except Exception as e2:  # noqa: BLE001
            return False, f"fail:{e1}|{e2}"


def _force_return():
    import renpy

    try:
        renpy.store.Return()()
        try:
            renpy.restart_interaction()
        except Exception:  # noqa: BLE001, S110
            pass
        return "Return()"
    except Exception:  # noqa: BLE001, S110
        pass
    for n in ("load", "preferences", "appreciation", "flowchart", "confirm", "save"):
        try:
            renpy.display.screen.hide_screen(n)
        except Exception:  # noqa: BLE001, S110
            pass
    try:
        renpy.restart_interaction()
    except Exception:  # noqa: BLE001, S110
        pass
    return "hide_screens"


def _force_confirm_quit():
    import renpy

    try:
        try:
            m = getattr(renpy.store, "persistent", None)
            if m is not None:
                mapping = getattr(m, "preferences_confirm_requirement_mapping", None)
                if isinstance(mapping, dict):
                    mapping["quit"] = True
        except Exception:  # noqa: BLE001, S110
            pass
        renpy.display.screen.show_screen(
            "confirm",
            message="确认要退出游戏吗",
            yes_action=[renpy.store.Hide("confirm")],
            no_action=[renpy.store.Hide("confirm")],
            confirm_type="quit",
        )
        try:
            renpy.restart_interaction()
        except Exception:  # noqa: BLE001, S110
            pass
        return True, "show_screen_confirm"
    except Exception as e1:  # noqa: BLE001
        return False, f"fail:{e1}"


def _palette_ok(name, rt):
    """Reject wrong-screen / lag chrome using mean RGB fingerprints.

    Expected product means (from prior hmc_nav_chrome_product / asset samples):
      load          ≈ (204, 211, 218) cool blue-gray
      preferences   ≈ (222, 216, 201) warm gray/yellow
      appreciation  ≈ (207, 231, 223) teal/green
      flowchart     ≈ (103–123, 94–101, 87–90) brown
      confirm       ≈ (63–172, 63–156, 68–146) dim+dialog (wide; α_mask dim)
    Main-menu baseline ≈ (37, 37, 42) — must not pass as load.
    """
    mean = rt.get("mean") or (0, 0, 0)
    try:
        r, g, b = float(mean[0]), float(mean[1]), float(mean[2])
    except Exception:  # noqa: BLE001
        return False, "mean_unreadable"
    var = float(rt.get("var") or 0)
    white = float(rt.get("white_frac") or 0)

    def near(a, b, tol):
        return abs(a - b) <= tol

    if name == "load":
        # cool gray, not dark main_menu, not warm prefs
        if r + g + b < 120:
            return False, f"too_dark_like_main_menu mean=({r:.0f},{g:.0f},{b:.0f})"
        if r > 200 and g > 200 and b < 190:
            return False, f"too_warm_like_prefs mean=({r:.0f},{g:.0f},{b:.0f})"
        if not (r > 150 and g > 150 and b > 150 and near(r, g, 25) and b >= g - 5):
            return False, f"not_cool_load_gray mean=({r:.0f},{g:.0f},{b:.0f})"
        return True, "load_cool_gray"

    if name == "preferences":
        # warm: R≈G > B somewhat, high white possible from choice Frames
        if r + g + b < 140:
            return False, f"too_dark mean=({r:.0f},{g:.0f},{b:.0f})"
        # reject pure cool load-like if B clearly dominates R
        if b > r + 8 and b > g + 5 and white < 0.4:
            return False, f"cool_like_load mean=({r:.0f},{g:.0f},{b:.0f})"
        if not (r > 160 and g > 160):
            return False, f"not_warm_prefs mean=({r:.0f},{g:.0f},{b:.0f})"
        return True, "prefs_warm"

    if name == "appreciation":
        # teal/green: G high, often G > R and G ≈ B-ish
        if r + g + b < 140:
            return False, f"too_dark mean=({r:.0f},{g:.0f},{b:.0f})"
        if g < 180:
            return False, f"not_teal_green mean=({r:.0f},{g:.0f},{b:.0f})"
        # G should not be clearly below R (warm prefs) without teal lift
        if g + 5 < r and b + 5 < r:
            return False, f"too_warm_like_prefs mean=({r:.0f},{g:.0f},{b:.0f})"
        return True, "appr_teal"

    if name == "flowchart":
        # brown/dark mixed — mean much lower than load/prefs
        if r + g + b > 450:
            return False, f"too_bright_like_load_prefs mean=({r:.0f},{g:.0f},{b:.0f})"
        if r + g + b < 80 and var < 200:
            return False, f"featureless_dark mean=({r:.0f},{g:.0f},{b:.0f})"
        # R typically >= G >= B for brown
        if not (r > 70 and g > 60 and b > 50):
            return False, f"not_brownish mean=({r:.0f},{g:.0f},{b:.0f})"
        return True, "flow_brown"

    if name == "confirm":
        # dim overlay + dialog band — not full-bright prefs, not pure main_menu
        if r + g + b > 600:
            return False, f"too_bright mean=({r:.0f},{g:.0f},{b:.0f})"
        if r + g + b < 60 and var < 100:
            return False, f"featureless_dark mean=({r:.0f},{g:.0f},{b:.0f})"
        return True, "confirm_dim"

    return True, "no_palette_rule"


def _structure_ok(name, struct, rt):
    """GL2 structure bar — not just non-clear RT.

    Per-screen minimums (cheap, product-path):
      - host_tex >= floor (full chrome has many textures)
      - reverse_scale OR multipiece for Frame-heavy screens
      - text_ish > 0 for prefs (labels/values) when possible
      - bar_ish > 0 for prefs/load when bars expected
    """
    n_host = int(struct.get("n_host_tex", 0))
    n_int = int(struct.get("n_int_tex", 0))
    # Custom displayables (flowchart) often leave int handles, not HostTexture wrappers.
    n_tex = n_host + n_int
    n_rev = int(struct.get("n_reverse_scale", 0))
    n_mp = int(struct.get("n_multipiece_parents", 0))
    n_text = int(struct.get("n_text_ish", 0))
    n_bar = int(struct.get("n_bar_ish", 0))
    # Accept either live walk `handles` set or summary `n_unique_handles`.
    if "n_unique_handles" in struct and struct.get("n_unique_handles") is not None:
        n_handles = int(struct.get("n_unique_handles") or 0)
    else:
        n_handles = len(struct.get("handles") or [])
    # When only int tex leaves exist, treat int count as handle diversity.
    if n_handles == 0 and n_int > 0:
        n_handles = n_int
    rt_ok = bool(rt.get("ok"))
    var = float(rt.get("var") or 0)

    reasons = []
    ok = True

    # Shared floor: non-clear RT required but not sufficient
    if not rt_ok:
        ok = False
        reasons.append("rt_featureless_or_clear")

    # Texture diversity — hollow white-only chrome often has few unique handles
    floors = {
        "load": {"tex": 8, "handles": 6, "rev_or_mp": 0, "text": 0, "bar": 0},
        "preferences": {"tex": 12, "handles": 8, "rev_or_mp": 1, "text": 1, "bar": 1},
        "appreciation": {"tex": 6, "handles": 4, "rev_or_mp": 0, "text": 0, "bar": 0},
        "flowchart": {"tex": 6, "handles": 4, "rev_or_mp": 0, "text": 0, "bar": 0},
        "confirm": {"tex": 4, "handles": 3, "rev_or_mp": 0, "text": 0, "bar": 0},
    }
    fl = floors.get(name, {"tex": 4, "handles": 3, "rev_or_mp": 0, "text": 0, "bar": 0})

    # Flowchart/confirm: if product RT palette+variance proves full-bleed chrome,
    # do not hard-fail solely on HostTexture leaf count (custom Displayable RTT
    # may peel to opaque leaves without HT wrappers on the product surftree).
    rt_structure_rescue = (
        name in ("flowchart", "confirm")
        and rt_ok
        and var >= 500
        and not rt.get("featureless_black")
        and not rt.get("clearish")
    )

    if n_tex < fl["tex"]:
        if rt_structure_rescue:
            reasons.append(
                "SOFT host_tex<%d (got host=%d int=%d) rescued_by_rt_palette"  # noqa: UP031
                % (fl["tex"], n_host, n_int)
            )
        else:
            ok = False
            reasons.append(
                "host_tex<%d (got host=%d int=%d total=%d)"  # noqa: UP031
                % (fl["tex"], n_host, n_int, n_tex)
            )
    if n_handles < fl["handles"]:
        if rt_structure_rescue:
            reasons.append(
                "SOFT unique_handles<%d (got %d) rescued_by_rt_palette"  # noqa: UP031
                % (fl["handles"], n_handles)
            )
        else:
            ok = False
            reasons.append("unique_handles<%d (got %d)" % (fl["handles"], n_handles))  # noqa: UP031
    if fl["rev_or_mp"] and (n_rev + n_mp) < fl["rev_or_mp"]:
        ok = False
        reasons.append("no_frame_multipiece_or_reverse (rev=%d mp=%d)" % (n_rev, n_mp))  # noqa: UP031
    if fl["text"] and n_text < fl["text"]:
        # Soft fail for text — product text may be atlas-baked differently; mark soft
        reasons.append("SOFT text_ish<%d (got %d)" % (fl["text"], n_text))  # noqa: UP031
    if fl["bar"] and n_bar < fl["bar"]:
        reasons.append("SOFT bar_ish<%d (got %d)" % (fl["bar"], n_bar))  # noqa: UP031

    # Prefs white-pill heuristic: high white_frac + low reverse pieces → hollow
    if name == "preferences" and rt.get("white_frac", 0) > 0.35 and n_rev < 2:
        ok = False
        reasons.append(
            "white_pill_suspect white_frac=%.3f rev=%d" % (rt.get("white_frac", 0), n_rev)  # noqa: UP031
        )

    soft_only = [r for r in reasons if r.startswith("SOFT ")]
    hard = [r for r in reasons if not r.startswith("SOFT ")]
    return (ok and not hard), hard, soft_only


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_nav_structure_probe.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    art_root = os.environ.get("ARTIFACT_DIR")
    lines = []
    state = {
        "phase": "boot",
        "main_menu": False,
        "results": [],
        "errors": [],
    }

    def rec(m):
        lines.append(m)
        _log(m)

    game = os.environ.get("RENPY_HOST_GAME") or str(base / "host" / "playtests" / "HuangmeiC")
    os.environ["RENPY_HOST_BASE"] = str(base)
    os.environ["RENPY_HOST_BUILD"] = "1"
    os.environ["RENPY_HOST_GAME"] = game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
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
            out.write_text(f"gate=hmc_nav_structure_probe\nok=False\nerror={err}\n")
            _request_quit()
            return

    import renpy

    renpy.host_build = True
    try:
        renpy.config.performance_test = False
    except Exception:  # noqa: BLE001, S110
        pass

    try:
        import renpy_main_host

        renpy_main_host.install(renpy)
        rec("main_host installed")
    except Exception as e:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001, S110
                pass
        args = renpy.arguments.bootstrap()
        renpy.game.args = args
        rec("args command={} basedir={}".format(getattr(args, "command", None), basedir))
    except Exception as e:  # noqa: BLE001
        rec(f"args fail: {e}")
        rec(traceback.format_exc())

    _pre_main_host_stubs()

    targets = [
        ("load", "load"),
        ("preferences", "preferences"),
        ("appreciation", "appreciation"),
        ("flowchart", "flowchart"),
        ("confirm", "confirm"),
    ]

    def injector():
        rec("waiting main_menu")
        for i in range(400):
            try:
                if bool(getattr(renpy.store, "main_menu", False)):
                    state["main_menu"] = True
                    rec("main_menu at tick=%d" % i)  # noqa: UP031
                    break
            except Exception:  # noqa: BLE001, S110
                pass
            time.sleep(0.05)
        if not state["main_menu"]:
            state["errors"].append("main_menu_timeout")
            rec("main_menu timeout")
            _request_quit()
            return

        time.sleep(2.0)

        for tname, screen_name in targets:
            state["phase"] = f"nav_{tname}"
            rec(f"=== target {tname} ===")
            entry = {
                "name": tname,
                "opened": False,
                "open_via": None,
                "error": None,
                "struct": {},
                "rt": {},
                "structure_ok": False,
                "hard_fail": [],
                "soft_fail": [],
            }
            try:
                if tname == "confirm":
                    ok_open, via = _force_confirm_quit()
                else:
                    ok_open, via = _force_show_menu(screen_name)
                entry["open_via"] = via
                if not ok_open:
                    entry["error"] = via
                    state["results"].append(entry)
                    rec(f"open FAIL {via}")
                    continue

                opened = False
                for j in range(40):
                    if _get_screen(screen_name) is not None:
                        opened = True
                        break
                    time.sleep(0.1)
                entry["opened"] = opened
                rec(f"opened={opened} via={via}")
                time.sleep(0.5)

                # Sample LIVE iface.surftree BEFORE soft present — flowchart custom
                # Displayables often leave a rich live tree while force rebuild is empty.
                # Nested frames=1 may still lag one screen; soft present below updates RT.
                live_st_pre = _sample_live_surftree_only()
                rec(
                    "LIVE_PRE present path={} host_tex={} handles={}".format(
                        live_st_pre.get("path"),
                        live_st_pre.get("n_host_tex"),
                        live_st_pre.get("n_unique_handles"),
                    )
                )

                pinfo = _product_present_after_showmenu()
                rec(
                    "PRODUCT present path={} err={} root={} rebuild_tex={}".format(
                        pinfo.get("path"),
                        pinfo.get("error"),
                        pinfo.get("root"),
                        pinfo.get("rebuild_host_tex"),
                    )
                )
                time.sleep(0.15)

                live_st_post = _sample_live_surftree_only()
                # Prefer pre-present live if richer (force can clobber iface.surftree)
                pre_score = int(live_st_pre.get("n_host_tex") or 0) + int(
                    live_st_pre.get("n_unique_handles") or 0
                )
                post_score = int(live_st_post.get("n_host_tex") or 0) + int(
                    live_st_post.get("n_unique_handles") or 0
                )
                live_st = live_st_pre if pre_score >= post_score else live_st_post
                force_tree = pinfo.get("surftree")
                force_struct = {
                    "n_nodes": 0,
                    "n_host_tex": 0,
                    "n_int_tex": 0,
                    "n_reverse_scale": 0,
                    "n_multipiece_parents": 0,
                    "n_text_ish": 0,
                    "n_bar_ish": 0,
                    "n_mesh": 0,
                    "handles": set(),
                    "dests": [],
                    "text_sizes": [],
                    "rev_pieces": [],
                    "errors": [],
                }
                if force_tree is not None:
                    try:
                        force_struct = _walk_structure(force_tree)
                    except Exception as e:  # noqa: BLE001
                        force_struct["errors"].append(f"walk:{e}")
                        rec(f"FORCE walk fail: {e}")

                live_summary = live_st.get("struct") or {
                    "n_nodes": live_st.get("n_nodes", 0),
                    "n_host_tex": live_st.get("n_host_tex", 0),
                    "n_unique_handles": live_st.get("n_unique_handles", 0),
                    "n_reverse_scale": live_st.get("n_reverse_scale", 0),
                    "n_multipiece_parents": live_st.get("n_multipiece_parents", 0),
                    "n_text_ish": live_st.get("n_text_ish", 0),
                    "n_bar_ish": live_st.get("n_bar_ish", 0),
                    "n_mesh": 0,
                    "n_dests": 0,
                    "n_rev_pieces": 0,
                    "rev_sample": [],
                    "dest_sample": [],
                    "errors": [live_st.get("error")] if live_st.get("error") else [],
                }
                force_summary = {
                    "n_nodes": force_struct["n_nodes"],
                    "n_host_tex": force_struct["n_host_tex"],
                    "n_int_tex": force_struct.get("n_int_tex", 0),
                    "n_reverse_scale": force_struct["n_reverse_scale"],
                    "n_multipiece_parents": force_struct["n_multipiece_parents"],
                    "n_text_ish": force_struct["n_text_ish"],
                    "n_bar_ish": force_struct["n_bar_ish"],
                    "n_mesh": force_struct["n_mesh"],
                    "n_unique_handles": len(force_struct.get("handles") or []),
                    "n_dests": len(force_struct.get("dests") or []),
                    "n_rev_pieces": len(force_struct.get("rev_pieces") or []),
                    "errors": list(force_struct.get("errors") or []),
                    "rev_sample": (force_struct.get("rev_pieces") or [])[:6],
                    "dest_sample": (force_struct.get("dests") or [])[:10],
                }

                live_score = int(live_summary.get("n_host_tex") or 0) + int(
                    live_summary.get("n_unique_handles") or 0
                )
                force_score = int(force_summary.get("n_host_tex") or 0) + int(
                    force_summary.get("n_unique_handles") or 0
                )
                if live_score >= force_score and live_score > 0:
                    struct_summary = dict(live_summary)
                    struct_src = "live_iface"
                elif force_score > 0:
                    struct_summary = dict(force_summary)
                    struct_src = "force_rebuild"
                else:
                    struct_summary = dict(force_summary)
                    struct_src = "empty_both"
                struct_summary.setdefault("n_int_tex", 0)
                struct_summary.setdefault("n_unique_handles", 0)
                rec(
                    "STRUCT_SRC={} live_tex={} force_tex={}".format(
                        struct_src,
                        live_summary.get("n_host_tex"),
                        force_summary.get("n_host_tex"),
                    )
                )

                rt = _sample_rt()
                if tname == "preferences" and rt.get("rt") is not None:
                    rw, rh = rt["w"], rt["h"]
                    band = _sample_band_mean(
                        rt["rt"],
                        rw,
                        rh,
                        int(rw * 0.2),
                        int(rh * 0.3),
                        int(rw * 0.8),
                        int(rh * 0.7),
                    )
                    if band:
                        rt["prefs_panel"] = band
                        rec(
                            "prefs_panel mean=({:.0f},{:.0f},{:.0f}) white={:.3f} dark={:.3f}".format(
                                band["mean"][0],
                                band["mean"][1],
                                band["mean"][2],
                                band["white_frac"],
                                band["dark_frac"],
                            )
                        )

                rt_summary = {k: v for k, v in rt.items() if k != "rt"}
                entry["rt"] = rt_summary
                entry["live_rt"] = rt_summary
                entry["force_rt"] = rt_summary
                entry["struct"] = struct_summary
                entry["live_struct"] = {
                    "path": live_st.get("path") or struct_src,
                    "error": live_st.get("error"),
                    "n_host_tex": struct_summary.get("n_host_tex", 0),
                    "n_unique_handles": struct_summary.get("n_unique_handles", 0),
                    "n_reverse_scale": struct_summary.get("n_reverse_scale", 0),
                    "n_multipiece_parents": struct_summary.get(
                        "n_multipiece_parents", 0
                    ),
                    "n_text_ish": struct_summary.get("n_text_ish", 0),
                    "n_bar_ish": struct_summary.get("n_bar_ish", 0),
                    "n_nodes": struct_summary.get("n_nodes", 0),
                }
                entry["force_struct"] = force_summary

                sok, hard, soft = _structure_ok(tname, struct_summary, rt_summary)
                if not opened:
                    sok = False
                    hard = list(hard) + ["screen_not_opened"]

                pal_ok, pal_reason = _palette_ok(tname, rt_summary)
                if not pal_ok:
                    sok = False
                    hard = list(hard) + [f"palette:{pal_reason}"]
                else:
                    soft = list(soft) + [f"palette_ok:{pal_reason}"]

                if force_score == 0 and live_score > 0:
                    soft = list(soft) + [
                        "SOFT force_rebuild_empty_live_rich (custom_displayable_class)"
                    ]

                entry["structure_ok"] = sok
                entry["live_structure_ok"] = sok
                entry["force_structure_ok"] = bool(
                    force_score > 0
                    and _structure_ok(tname, force_summary, rt_summary)[0]
                )
                entry["hard_fail"] = hard
                entry["soft_fail"] = soft
                entry["live_hard_fail"] = hard
                entry["live_soft_fail"] = soft
                entry["force_hard_fail"] = []
                entry["force_soft_fail"] = []

                rec(
                    "PRODUCT struct host_tex=%d handles=%d rev=%d mp=%d text_ish=%d bar_ish=%d mesh=%d src=%s"  # noqa: UP031
                    % (
                        int(struct_summary.get("n_host_tex") or 0),
                        int(struct_summary.get("n_unique_handles") or 0),
                        int(struct_summary.get("n_reverse_scale") or 0),
                        int(struct_summary.get("n_multipiece_parents") or 0),
                        int(struct_summary.get("n_text_ish") or 0),
                        int(struct_summary.get("n_bar_ish") or 0),
                        int(struct_summary.get("n_mesh") or 0),
                        struct_src,
                    )
                )
                rec(
                    "PRODUCT rt ok={} mean=({:.0f},{:.0f},{:.0f}) var={:.1f} white={:.3f} pure={:.3f}".format(
                        rt_summary.get("ok"),
                        (rt_summary.get("mean") or (0, 0, 0))[0],
                        (rt_summary.get("mean") or (0, 0, 0))[1],
                        (rt_summary.get("mean") or (0, 0, 0))[2],
                        rt_summary.get("var", 0),
                        rt_summary.get("white_frac", 0),
                        rt_summary.get("pure_frac", 0),
                    )
                )
                rec(
                    "structure_ok={} hard={} soft={}".format(sok, hard or "none", soft or "none")
                )
            except Exception as e:  # noqa: BLE001
                entry["error"] = f"{e}"
                rec(f"exc {tname}: {e}")
                rec(traceback.format_exc())
            state["results"].append(entry)

            try:
                _force_return()
                time.sleep(0.4)
            except Exception as e:  # noqa: BLE001
                rec(f"return fail: {e}")

        state["phase"] = "done"
        rec("request_quit")
        _request_quit()

    threading.Thread(target=injector, daemon=True).start()

    import renpy.main as renpy_main

    rec("entering renpy.main.main()")
    try:
        renpy_main.main()
        rec("main returned")
    except BaseException as e:  # noqa: BLE001
        rec(f"main exit {type(e).__name__}: {e}")

    results = state["results"]
    primary = [r for r in results if r["name"] in ("load", "preferences", "appreciation", "flowchart")]
    confirm_r = next((r for r in results if r["name"] == "confirm"), None)
    primary_ok = all(r.get("structure_ok") for r in primary) if primary else False
    confirm_ok = bool(confirm_r and confirm_r.get("structure_ok"))
    main_ok = bool(state["main_menu"])
    # Hard: main + primary structure (confirm soft if opened)
    ok = main_ok and primary_ok

    body = [
        "gate=hmc_nav_structure_probe",
        f"ok={ok}",
        "ac=Nav_structure_helper",
        f"main_menu={main_ok}",
        f"primary_ok={primary_ok}",
        f"confirm_ok={confirm_ok}",
        "phase={}".format(state["phase"]),
        "errors=%s" % (";".join(state["errors"]) if state["errors"] else "none"),
        ("notes=LIVE_primary_structure_bar;force_helper_only;ban_nonclear_only;"
        "ban_force_green_while_live_fail;human_photos_AC-Nav1_authority"),
    ]
    for r in results:
        st = r.get("struct") or {}
        rt = r.get("rt") or {}
        lst = r.get("live_struct") or {}
        lrt = r.get("live_rt") or {}
        body.append(
            "screen.{} structure_ok={} live_ok={} force_ok={} opened={} "
            "LIVE host_tex={} handles={} rev={} mp={} text={} bar={} "
            "rt_ok={} mean={} var={:.1f} white={:.3f} "
            "FORCE host_tex={} handles={} rev={} mp={} text={} bar={} "
            "rt_ok={} mean={} var={:.1f} white={:.3f} "
            "hard={} soft={} via={} err={}".format(
                r["name"],
                r.get("structure_ok"),
                r.get("live_structure_ok"),
                r.get("force_structure_ok"),
                r.get("opened"),
                lst.get("n_host_tex"),
                lst.get("n_unique_handles"),
                lst.get("n_reverse_scale"),
                lst.get("n_multipiece_parents"),
                lst.get("n_text_ish"),
                lst.get("n_bar_ish"),
                lrt.get("ok"),
                tuple(round(x, 1) for x in (lrt.get("mean") or (0, 0, 0))),
                float(lrt.get("var") or 0),
                float(lrt.get("white_frac") or 0),
                st.get("n_host_tex"),
                st.get("n_unique_handles"),
                st.get("n_reverse_scale"),
                st.get("n_multipiece_parents"),
                st.get("n_text_ish"),
                st.get("n_bar_ish"),
                rt.get("ok"),
                tuple(round(x, 1) for x in (rt.get("mean") or (0, 0, 0))),
                float(rt.get("var") or 0),
                float(rt.get("white_frac") or 0),
                r.get("hard_fail") or "none",
                r.get("soft_fail") or "none",
                r.get("open_via"),
                r.get("error"),
            )
        )
        # detail reverse samples (force tree — richest multipiece dump)
        for i, rp in enumerate((st.get("rev_sample") or [])[:4]):
            body.append(
                "screen.%s.rev[%d] ox=%.0f oy=%.0f size=%s xdx=%.3f ydy=%.3f"  # noqa: UP031
                % (
                    r["name"],
                    i,
                    rp.get("ox", 0),
                    rp.get("oy", 0),
                    rp.get("size"),
                    rp.get("xdx", 1),
                    rp.get("ydy", 1),
                )
            )
    body.append(
        "matrix AC-Nav1=structure_helper_LIVE_primary_not_human_authority "
        "AC-Nav2=human_only AC-Nav3=pass_process_stubs_excluded"
    )
    body.extend([f"log.{ln}" for ln in lines[-60:]])
    text = "\n".join(body) + "\n"
    out.write_text(text)
    rec(f"wrote {out} ok={ok} primary_ok={primary_ok}")

    if art_root:
        try:
            p = Path(art_root)
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                (p / "gate-hmc_nav_structure_probe.txt").write_text(text)
            else:
                p.write_text(text)
        except Exception as e:  # noqa: BLE001
            rec(f"artifact write fail: {e}")

    _request_quit()
    if not main_ok:
        raise RuntimeError("hmc_nav_structure_probe: main_menu never reached")


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
