"""
Step 1.0 — HuangmeiC idle main_menu imagebutton auto probe (product path).

Gate name: hmc_idle_chrome_probe  (RENPY_HOST_GATE=hmc_idle_chrome_probe)

Boots product HuangmeiC, waits for first stable main_menu frame with NO mouse
motion, then dumps:

  - focus name / default focus
  - per dock ImageButton: style.prefix, state_children paths, get_child() path,
    displayable type, texture handle/size (from surftree walk), dest rect
    (focus coords), prepared/loaded, u_renpy_alpha if any

Then moves mouse over EXTRA then Start and dumps before/after.

Does NOT mutate renpy/wgpu/draw.py permanently. Temporary dumps only.

Writes:
  host/target/gate-hmc_idle_chrome_probe.txt
  .omc/artifacts/.../probes/step1-0-idle-probe-raw.txt  (if ARTIFACT_DIR set)

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
    try:
        sys.__stdout__.write(f"[hmc_idle_probe] {msg}\n")
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        open("/tmp/hmc_idle_chrome_probe.log", "a").write(msg + "\n")  # noqa: SIM115
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


def _disp_path(d):
    """Best-effort path/filename for a displayable (Image / ImageReference / str)."""
    if d is None:
        return None
    for attr in ("filename", "name", "image", "_target"):
        try:
            v = getattr(d, attr, None)
        except Exception:
            v = None
        if v is None:
            continue
        if callable(v) and attr == "_target":
            try:
                t = v()
                return _disp_path(t)
            except Exception:
                continue
        if isinstance(v, (str, bytes)):
            return str(v)
        if isinstance(v, tuple):
            return repr(v)
    # ImageReference often stores name as tuple
    try:
        n = getattr(d, "name", None)
        if n is not None:
            return repr(n)
    except Exception:
        pass
    return f"{type(d).__name__}@{id(d)}"


def _action_label(action):
    if action is None:
        return None
    try:
        cls = type(action).__name__
    except Exception:
        cls = "?"
    # Start / ShowMenu / ConfirmAction
    for attr in ("label", "screen", "name", "confirm_type", "slot"):
        try:
            v = getattr(action, attr, None)
        except Exception:
            v = None
        if v is not None and not callable(v):
            return f"{cls}({attr}={v!r})"
    # nested list
    if isinstance(action, (list, tuple)):
        return "[{}]".format(", ".join(_action_label(a) or type(a).__name__ for a in action[:4]))
    return cls


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
    except Exception:
        pass
    if t is None:
        return None
    if isinstance(t, int) and not isinstance(t, bool):
        return {"kind": "int", "handle": int(t)}
    return {"kind": type(t).__name__}


def _walk_surftree_textures(node, acc=None, depth=0, budget=400, ox=0.0, oy=0.0):
    """Collect textured leaves + dest-ish offsets from a Render tree."""
    if acc is None:
        acc = []
    if node is None or len(acc) >= budget or depth > 40:
        return acc
    try:
        tw = getattr(node, "width", None) or getattr(node, "w", None)
        th = getattr(node, "height", None) or getattr(node, "h", None)
    except Exception:
        tw = th = None
    tex = getattr(node, "texture", None)
    cm = getattr(node, "cached_model", None)
    loaded = getattr(node, "loaded", None)
    shaders = getattr(node, "shaders", None)
    uniforms = getattr(node, "uniforms", None)
    mesh = getattr(node, "mesh", None)
    ti = _texinfo(tex)
    cmi = None
    if cm is not None:
        cmi = {
            "tex": _texinfo(getattr(cm, "texture", None)),
            "w": getattr(cm, "width", None),
            "h": getattr(cm, "height", None),
            "shaders": getattr(cm, "shaders", None),
        }
    kids = getattr(node, "children", None) or []
    alpha_u = None
    if isinstance(uniforms, dict):
        alpha_u = uniforms.get("u_renpy_alpha")
    # Record textured or model nodes
    if ti is not None or cmi is not None:
        acc.append(
            {
                "depth": depth,
                "cls": type(node).__name__,
                "size": (tw, th),
                "ox": ox,
                "oy": oy,
                "tex": ti,
                "cached_model": cmi,
                "loaded": loaded,
                "mesh": mesh,
                "shaders": list(shaders) if shaders else None,
                "u_renpy_alpha": alpha_u,
                "n_kids": len(kids),
            }
        )
    for entry in list(kids)[:16]:
        try:
            if isinstance(entry, (tuple, list)):
                ch = entry[0]
                cx = float(entry[1]) if len(entry) > 1 else 0.0
                cy = float(entry[2]) if len(entry) > 2 else 0.0
            else:
                ch = entry
                cx = cy = 0.0
        except Exception:
            continue
        # HostTexture as direct child
        try:
            from renpy.wgpu.draw import HostTexture

            if isinstance(ch, HostTexture):
                acc.append(
                    {
                        "depth": depth + 1,
                        "cls": "HostTexture",
                        "size": (ch.w, ch.h),
                        "ox": ox + cx,
                        "oy": oy + cy,
                        "tex": _texinfo(ch),
                        "cached_model": None,
                        "loaded": True,
                        "mesh": None,
                        "shaders": None,
                        "u_renpy_alpha": None,
                        "n_kids": 0,
                    }
                )
                continue
        except Exception:
            pass
        _walk_surftree_textures(ch, acc, depth + 1, budget, ox + cx, oy + cy)
    return acc


def _force_product_redraw():
    import renpy

    info = {"path": None, "error": None, "surftree": None}
    try:
        import interact_helpers as ih

        ready, why, iface = ih.interface_ready()
        if not ready or iface is None:
            info["error"] = f"iface:{why}"
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
            info["surftree"] = surftree
            return info
        # Ensure prepare runs so HostTextures exist for walk
        try:
            if hasattr(draw, "load_all_textures"):
                draw.load_all_textures(surftree)
        except Exception as e:
            info["prepare_err"] = f"{type(e).__name__}:{e}"
        draw.draw_screen(surftree, flip=True)
        try:
            iface.surftree = surftree
        except Exception:
            pass
        info["path"] = "rebuild_render_screen"
        info["root"] = type(root).__name__
        info["surftree"] = surftree
        return info
    except Exception as e:
        info["error"] = f"{type(e).__name__}:{e}"
        info["tb"] = traceback.format_exc()[-800:]
        return info


def _sample_rt_bottom_band():
    """Sample bottom dock band of game RT for non-clear presence."""
    import renpy_host

    try:
        w, h, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not w or not h or not rgba:
        return {"ok": False, "error": "empty_rt"}
    # Bottom 12% of frame (dock at y≈969–1080 virtual)
    y0 = int(h * 0.88)
    n = 0
    sr = sg = sb = 0
    nonclear = 0
    # clear ≈ (13,13,20)
    for y in range(y0, h, 2):
        row = y * w * 4
        for x in range(0, w, 4):
            i = row + x * 4
            r, g, b = rgba[i], rgba[i + 1], rgba[i + 2]
            sr += r
            sg += g
            sb += b
            n += 1
            if abs(r - 13) > 12 or abs(g - 13) > 12 or abs(b - 20) > 12:
                nonclear += 1
    mean = (sr / n, sg / n, sb / n) if n else (0, 0, 0)
    return {
        "ok": True,
        "w": w,
        "h": h,
        "band_y0": y0,
        "mean": mean,
        "nonclear_frac": (nonclear / n) if n else 0.0,
        "n": n,
        "rgba": rgba,  # keep for per-button samples in same frame
    }


def _virt_to_phys(vx, vy, vw=1920, vh=1080, pw=None, ph=None):
    import renpy

    draw = getattr(renpy.display, "draw", None)
    if pw is None or ph is None:
        try:
            pw, ph = renpy_host_size()
        except Exception:
            pw = ph = None
    if draw is not None:
        try:
            ps = getattr(draw, "physical_size", None)
            if ps:
                pw, ph = int(ps[0]), int(ps[1])
        except Exception:
            pass
        try:
            vs = getattr(draw, "virtual_size", None) or getattr(draw, "physical_size", None)
            if vs:
                vw, vh = int(vs[0]), int(vs[1])
        except Exception:
            pass
    if not pw or not ph:
        pw, ph = 1280, 720
    return int(vx * pw / float(vw)), int(vy * ph / float(vh)), pw, ph


def renpy_host_size():
    import renpy_host

    w, h, _ = renpy_host.read_game_rt_rgba()
    return w, h


def _sample_rect_mean(rgba, w, h, cx, cy, rw=12, rh=8):
    """Mean RGB in a small window around physical (cx,cy)."""
    if not rgba or not w or not h:
        return None
    x0 = max(0, int(cx - rw // 2))
    y0 = max(0, int(cy - rh // 2))
    x1 = min(w, x0 + rw)
    y1 = min(h, y0 + rh)
    n = sr = sg = sb = sa = 0
    for y in range(y0, y1):
        row = y * w * 4
        for x in range(x0, x1):
            i = row + x * 4
            sr += rgba[i]
            sg += rgba[i + 1]
            sb += rgba[i + 2]
            sa += rgba[i + 3]
            n += 1
    if not n:
        return None
    return (sr / n, sg / n, sb / n, sa / n, n)


def _sample_live_surftree_only():
    """Inspect iface.surftree WITHOUT force rebuild (live present path)."""
    info = {"path": None, "error": None, "surftree": None, "n_tex": 0, "dock": []}
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
        nodes = _walk_surftree_textures(st)
        info["n_tex"] = len(nodes)
        info["all"] = nodes
        info["dock"] = [n for n in nodes if (n.get("oy") or 0) >= 900]
        return info
    except Exception as e:
        info["error"] = f"{type(e).__name__}:{e}"
        info["tb"] = traceback.format_exc()[-600:]
        return info


def _collect_imagebuttons():
    """Walk focus_list + screen tree for ImageButton dock widgets."""
    import renpy
    from renpy.display.behavior import Button, ImageButton

    rows = []
    seen = set()

    # From focus_list (has dest rects)
    try:
        fl = list(renpy.display.focus.focus_list or [])
    except Exception:
        fl = []

    for f in fl:
        w = getattr(f, "widget", None)
        if w is None:
            continue
        if id(w) in seen:
            continue
        is_ib = isinstance(w, ImageButton)
        is_btn = isinstance(w, Button)
        if not (is_ib or is_btn):
            continue
        # Prefer dock-ish: ImageButton or buttons with action
        seen.add(id(w))
        rows.append(_describe_button(w, focus=f, source="focus_list"))

    # Also walk main_menu screen root for any ImageButtons not in focus
    try:
        scr = renpy.display.screen.get_screen("main_menu")
        if scr is not None:
            root = getattr(scr, "child", None) or getattr(scr, "root", None) or scr
            for d in _iter_displayables(root, budget=500):
                if id(d) in seen:
                    continue
                if isinstance(d, ImageButton):
                    seen.add(id(d))
                    rows.append(_describe_button(d, focus=None, source="screen_walk"))
                elif isinstance(d, Button) and getattr(d, "action", None) is not None:
                    # continue dual-ATL button etc.
                    seen.add(id(d))
                    rows.append(_describe_button(d, focus=None, source="screen_walk"))
    except Exception as e:
        rows.append({"error": f"screen_walk:{e}"})

    return rows


def _iter_displayables(d, budget=500, _acc=None, _seen=None):
    if _acc is None:
        _acc = []
    if _seen is None:
        _seen = set()
    if d is None or len(_acc) >= budget:
        return _acc
    try:
        i = id(d)
    except Exception:
        return _acc
    if i in _seen:
        return _acc
    _seen.add(i)
    _acc.append(d)
    # children
    kids = getattr(d, "children", None) or []
    for ch in list(kids)[:32]:
        _iter_displayables(ch, budget, _acc, _seen)
    child = getattr(d, "child", None)
    if child is not None:
        _iter_displayables(child, budget, _acc, _seen)
    # ImageButton state children
    sc = getattr(d, "state_children", None)
    if isinstance(sc, dict):
        for v in sc.values():
            _iter_displayables(v, budget, _acc, _seen)
    ibc = getattr(d, "imagebutton_child", None)
    if ibc is not None:
        _iter_displayables(ibc, budget, _acc, _seen)
    return _acc


def _describe_button(w, focus=None, source="?"):
    import renpy
    from renpy.display.behavior import ImageButton

    row = {
        "source": source,
        "type": type(w).__name__,
        "id": id(w),
        "action": _action_label(getattr(w, "action", None)),
        "role": getattr(w, "role", None),
        "focusable": getattr(w, "focusable", None),
        "clicked_set": getattr(w, "clicked", None) is not None,
    }
    try:
        row["style_prefix"] = getattr(w.style, "prefix", None)
    except Exception as e:
        row["style_prefix"] = f"err:{e}"
    try:
        row["style_name"] = str(getattr(w.style, "style_name", None) or getattr(w.style, "name", None))
    except Exception:
        row["style_name"] = None

    # focus rect
    if focus is not None:
        row["dest_rect"] = (
            getattr(focus, "x", None),
            getattr(focus, "y", None),
            getattr(focus, "w", None),
            getattr(focus, "h", None),
        )
        try:
            row["full_focus_name"] = getattr(focus, "full_focus_name", None)
        except Exception:
            pass
    else:
        row["dest_rect"] = None

    # ImageButton state_children
    if isinstance(w, ImageButton):
        sc = getattr(w, "state_children", None) or {}
        paths = {}
        for k, v in sc.items():
            paths[k] = {
                "path": _disp_path(v),
                "type": type(v).__name__,
                "id": id(v),
            }
        row["state_children"] = paths
        try:
            child = w.get_child()
            row["get_child"] = {
                "path": _disp_path(child),
                "type": type(child).__name__ if child is not None else None,
                "id": id(child) if child is not None else None,
                "raw_child_path": _disp_path(getattr(w, "imagebutton_raw_child", None)),
            }
        except Exception as e:
            row["get_child"] = {"error": f"{type(e).__name__}:{e}"}
        # Which prefix key is used
        pref = row.get("style_prefix")
        if pref and pref in sc:
            row["active_state_key"] = pref
            row["active_state_path"] = paths.get(pref, {}).get("path")
        else:
            row["active_state_key"] = pref
            row["active_state_path"] = None
            row["active_state_missing"] = True
    else:
        # plain Button — background styles
        try:
            row["background"] = _disp_path(getattr(w.style, "background", None))
            row["hover_background"] = _disp_path(
                getattr(w.style, "hover_background", None)
                if hasattr(w.style, "hover_background")
                else None
            )
        except Exception as e:
            row["background_err"] = str(e)
        try:
            child = w.get_child() if hasattr(w, "get_child") else getattr(w, "child", None)
            row["get_child"] = {
                "path": _disp_path(child),
                "type": type(child).__name__ if child is not None else None,
            }
        except Exception as e:
            row["get_child"] = {"error": str(e)}

    # selected / sensitive
    try:
        row["is_selected"] = bool(w.is_selected()) if hasattr(w, "is_selected") else None
    except Exception as e:
        row["is_selected"] = f"err:{e}"
    try:
        row["is_sensitive"] = bool(w.is_sensitive()) if hasattr(w, "is_sensitive") else None
    except Exception as e:
        row["is_sensitive"] = f"err:{e}"

    # Try render-size of active child if possible
    try:
        child = None
        if isinstance(w, ImageButton):
            child = w.get_child()
        if child is not None:
            renpy.display.render.render
            # Don't force full render here — just note child attributes
            row["child_style_prefix"] = getattr(getattr(child, "style", None), "prefix", None)
    except Exception:
        pass

    return row


def _focus_snapshot():
    import renpy

    out = {
        "focused": None,
        "focused_type": None,
        "focused_action": None,
        "focused_prefix": None,
        "focus_list_n": 0,
        "focus_list": [],
        "grab": None,
        "default_focus_hint": None,
    }
    try:
        fw = renpy.display.focus.get_focused()
        out["focused"] = id(fw) if fw is not None else None
        out["focused_type"] = type(fw).__name__ if fw is not None else None
        if fw is not None:
            out["focused_action"] = _action_label(getattr(fw, "action", None))
            try:
                out["focused_prefix"] = getattr(fw.style, "prefix", None)
            except Exception:
                pass
            out["focused_role"] = getattr(fw, "role", None)
    except Exception as e:
        out["focused_err"] = str(e)
    try:
        fl = list(renpy.display.focus.focus_list or [])
        out["focus_list_n"] = len(fl)
        for f in fl[:24]:
            w = getattr(f, "widget", None)
            out["focus_list"].append(
                {
                    "widget_type": type(w).__name__ if w is not None else None,
                    "widget_id": id(w) if w is not None else None,
                    "action": _action_label(getattr(w, "action", None)) if w else None,
                    "prefix": (getattr(w.style, "prefix", None) if w is not None else None),
                    "rect": (
                        getattr(f, "x", None),
                        getattr(f, "y", None),
                        getattr(f, "w", None),
                        getattr(f, "h", None),
                    ),
                    "full_focus_name": getattr(f, "full_focus_name", None),
                }
            )
    except Exception as e:
        out["focus_list_err"] = str(e)
    try:
        out["grab"] = type(renpy.display.focus.get_grab()).__name__ if renpy.display.focus.get_grab() else None
    except Exception:
        pass
    return out


def _match_button_to_textures(btn_row, tex_nodes):
    """Heuristic: match focus dest rect to nearby surftree textured leaves."""
    rect = btn_row.get("dest_rect")
    if not rect or rect[0] is None:
        return []
    bx, by, bw, bh = rect
    hits = []
    for n in tex_nodes:
        ox, oy = n.get("ox", 0), n.get("oy", 0)
        # proximity in virtual pixels
        if abs(ox - bx) < 40 and abs(oy - by) < 40 or bw and bh and ox >= bx - 5 and oy >= by - 5 and ox < bx + bw + 5 and oy < by + bh + 5:
            hits.append(n)
    return hits[:6]


def _fmt_btn(b, tex_nodes=None):
    lines = []
    lines.append(
        "  [{}] {} action={} prefix={} role={} selected={} sensitive={} focusable={}".format(
            b.get("source"),
            b.get("type"),
            b.get("action"),
            b.get("style_prefix"),
            b.get("role"),
            b.get("is_selected"),
            b.get("is_sensitive"),
            b.get("focusable"),
        )
    )
    lines.append("    dest_rect={} focus_name={}".format(b.get("dest_rect"), b.get("full_focus_name")))
    if "state_children" in b:
        for k, v in sorted((b.get("state_children") or {}).items()):
            mark = " <<<" if k == b.get("active_state_key") else ""
            lines.append("    state[{}]={} type={}{}".format(k, v.get("path"), v.get("type"), mark))
        gc = b.get("get_child") or {}
        lines.append(
            "    get_child path={} type={} raw={} missing_key={}".format(
                gc.get("path"),
                gc.get("type"),
                gc.get("raw_child_path"),
                b.get("active_state_missing"),
            )
        )
    else:
        lines.append("    background={} get_child={}".format(b.get("background"), b.get("get_child")))
    if tex_nodes is not None:
        hits = _match_button_to_textures(b, tex_nodes)
        if hits:
            for h in hits:
                lines.append(
                    "    tex_hit depth={} cls={} ox={:.1f} oy={:.1f} size={} tex={} loaded={} alpha={} shaders={}".format(
                        h.get("depth"),
                        h.get("cls"),
                        h.get("ox", 0),
                        h.get("oy", 0),
                        h.get("size"),
                        h.get("tex"),
                        h.get("loaded"),
                        h.get("u_renpy_alpha"),
                        h.get("shaders"),
                    )
                )
        else:
            lines.append("    tex_hit=NONE (no surftree leaf near dest_rect)")
    return "\n".join(lines)


def _dump_phase(label, lines):
    import renpy

    lines.append("")
    lines.append("=" * 72)
    lines.append(f"PHASE: {label}")
    lines.append("=" * 72)

    # focus
    fs = _focus_snapshot()
    lines.append("FOCUS focused_type={} action={} prefix={} role={} id={}".format(
        fs.get("focused_type"),
        fs.get("focused_action"),
        fs.get("focused_prefix"),
        fs.get("focused_role"),
        fs.get("focused"),
    ))
    lines.append("FOCUS_LIST n={} grab={}".format(fs.get("focus_list_n"), fs.get("grab")))
    for e in fs.get("focus_list") or []:
        lines.append(
            "  focus {} action={} prefix={} rect={} name={}".format(
                e.get("widget_type"),
                e.get("action"),
                e.get("prefix"),
                e.get("rect"),
                e.get("full_focus_name"),
            )
        )

    # LIVE RT sample BEFORE force redraw (true product present)
    try:
        import renpy_host
        w0,h0,rgba0 = renpy_host.read_game_rt_rgba()
        if w0 and h0 and rgba0:
            n=0; sr=sg=sb=0; nc=0
            y0=int(h0*0.88)
            for y in range(y0,h0,3):
                row=y*w0*4
                for x in range(0,w0,6):
                    i=row+x*4
                    r,g,b=rgba0[i],rgba0[i+1],rgba0[i+2]
                    sr+=r; sg+=g; sb+=b; n+=1
                    if abs(r-13)>12 or abs(g-13)>12 or abs(b-20)>12: nc+=1
            lines.append(f"LIVE_RT_PRE_FORCE size={w0}x{h0} dock_mean=({sr/n if n else 0:.1f},{sg/n if n else 0:.1f},{sb/n if n else 0:.1f}) nonclear={nc/n if n else 0:.3f}")
            # per-button from focus list rects later after buttons collected — store
            lines.append("LIVE_RT_PRE_FORCE stored for PIX_PRE")
            # stash on function via list
            _dump_phase._pre_rgba = (w0,h0,rgba0)
        else:
            lines.append("LIVE_RT_PRE_FORCE empty")
            _dump_phase._pre_rgba = None
    except Exception as e:
        lines.append(f"LIVE_RT_PRE_FORCE fail: {e}")
        _dump_phase._pre_rgba = None

    # LIVE surftree first (no force rebuild) — product present path
    live = _sample_live_surftree_only()
    lines.append(
        "LIVE_SURFTREE path={} err={} n_tex={} dock_n={}".format(live.get("path"), live.get("error"), live.get("n_tex"), len(live.get("dock") or []))
    )
    # Arena thrash / dead-handle probe (AC-Idle present residual).
    try:
        import renpy_host as _rh

        lines.append(
            "ARENA sample_tex={} map_len={} order_len={} last_pp={} n_pp={}".format(
                _rh.sample_texture_count() if hasattr(_rh, "sample_texture_count") else "?",
                _rh.texture_map_len() if hasattr(_rh, "texture_map_len") else "?",
                _rh.texture_order_len() if hasattr(_rh, "texture_order_len") else "?",
                _rh.last_product_present() if hasattr(_rh, "last_product_present") else "?",
                _rh.product_presents() if hasattr(_rh, "product_presents") else "?",
            )
        )
    except Exception as e:
        lines.append(f"ARENA fail: {e}")
    for n in (live.get("dock") or [])[:40]:
        tex = n.get("tex") or {}
        handle = tex.get("handle") if isinstance(tex, dict) else None
        alive = None
        if handle is not None:
            try:
                import renpy_host as _rh

                if hasattr(_rh, "texture_alive"):
                    alive = bool(_rh.texture_alive(int(handle)))
            except Exception as e:
                alive = f"err:{e}"
        lines.append(
            "  live_dock d={} {} ox={:.0f} oy={:.0f} size={} tex={} loaded={} alpha={} alive={}".format(
                n.get("depth"),
                n.get("cls"),
                n.get("ox", 0),
                n.get("oy", 0),
                n.get("size"),
                n.get("tex"),
                n.get("loaded"),
                n.get("u_renpy_alpha"),
                alive,
            )
        )
    # Force draw of LIVE surftree (no rebuild) — isolates walk/draw vs re-upload.
    try:
        import renpy_host as _rh

        import renpy
        from renpy.wgpu.draw import HostTexture as _HT

        iface = getattr(renpy.display, "interface", None)
        st = getattr(iface, "surftree", None) if iface is not None else None
        draw = getattr(renpy.display, "draw", None)

        # Structure dump: ancestors of first dock HostTexture (mesh/cached_model).
        def _struct_path(node, target_oy_min=900, path=None, depth=0, budget=None):
            if budget is None:
                budget = [400]
            if path is None:
                path = []
            if node is None or budget[0] <= 0 or depth > 30:
                return None
            budget[0] -= 1
            kids = getattr(node, "children", None) or []
            cm = getattr(node, "cached_model", None)
            info = {
                "d": depth,
                "cls": type(node).__name__,
                "mesh": getattr(node, "mesh", None),
                "n_kids": len(kids) if kids is not None else 0,
                "loaded": getattr(node, "loaded", None),
                "has_cm": cm is not None,
                "cm_texs": (
                    len(getattr(cm, "textures", None) or []) if cm is not None else 0
                ),
                "shaders": list(getattr(node, "shaders", None) or [])[:4] or None,
                "size": (
                    getattr(node, "width", None) or getattr(node, "w", None),
                    getattr(node, "height", None) or getattr(node, "h", None),
                ),
            }
            here = path + [info]
            if isinstance(node, _HT):
                if int(getattr(node, "h", 0) or 0) > 0:
                    # Leaf — caller filters by oy via walk below.
                    return here
                return None
            for entry in list(kids)[:24]:
                if isinstance(entry, (tuple, list)):
                    ch = entry[0]
                    cy = float(entry[2]) if len(entry) > 2 else 0.0
                else:
                    ch = entry
                    cy = 0.0
                if isinstance(ch, _HT) and cy >= target_oy_min:
                    info2 = {
                        "d": depth + 1,
                        "cls": "HostTexture",
                        "mesh": None,
                        "n_kids": 0,
                        "loaded": True,
                        "has_cm": False,
                        "cm_texs": 0,
                        "shaders": None,
                        "size": (ch.w, ch.h),
                        "oy": cy,
                        "handle": int(ch.handle),
                    }
                    return here + [info2]
                found = _struct_path(ch, target_oy_min, here, depth + 1, budget)
                if found is not None:
                    return found
            return None

        if st is not None:
            # Dump top-level dissolve nodes: complete amount + per-child texture counts.
            try:
                from renpy.wgpu.draw import HostTexture as _HT2

                def _count_ht(node, budget=None):
                    if budget is None:
                        budget = [300]
                    if node is None or budget[0] <= 0:
                        return 0
                    budget[0] -= 1
                    n = 0
                    if isinstance(node, _HT2):
                        return 1
                    for entry in list(getattr(node, "children", None) or [])[:32]:
                        ch = entry[0] if isinstance(entry, (tuple, list)) else entry
                        n += _count_ht(ch, budget)
                    return n

                def _dump_diss(node, depth=0, budget=None):
                    if budget is None:
                        budget = [80]
                    if node is None or budget[0] <= 0 or depth > 8:
                        return
                    budget[0] -= 1
                    shaders = list(getattr(node, "shaders", None) or [])
                    if any(s in ("renpy.dissolve", "dissolve") for s in shaders) or getattr(node, "mesh", None):
                        uniforms = getattr(node, "uniforms", None)
                        kids = list(getattr(node, "children", None) or [])
                        if any(s in ("renpy.dissolve", "dissolve") for s in shaders) and len(kids) >= 1:
                            u_amt = None
                            if isinstance(uniforms, dict):
                                u_amt = uniforms.get("u_renpy_dissolve")
                            op = getattr(node, "operation", None)
                            opc = getattr(node, "operation_complete", None)
                            # What would draw path choose?
                            try:
                                draw = renpy.display.draw
                                complete = draw._dissolve_complete(node) if hasattr(draw, "_dissolve_complete") else "?"
                            except Exception as e:
                                complete = f"err:{e}"
                            lines.append(
                                "DISSOLVE d={} kids={} u_renpy_dissolve={} op={} op_complete={} helper_complete={} cm={}".format(
                                    depth,
                                    len(kids),
                                    u_amt,
                                    op,
                                    opc,
                                    complete,
                                    getattr(node, "cached_model", None) is not None,
                                )
                            )
                            for i, entry in enumerate(kids[:4]):
                                ch = entry[0] if isinstance(entry, (tuple, list)) else entry
                                lines.append(
                                    "  diss_child[%d] cls=%s mesh=%s kids=%s ht_count=%s size=%s sh=%s"  # noqa: UP031
                                    % (
                                        i,
                                        type(ch).__name__,
                                        getattr(ch, "mesh", None),
                                        len(getattr(ch, "children", None) or []),
                                        _count_ht(ch, [200]),
                                        (
                                            getattr(ch, "width", None) or getattr(ch, "w", None),
                                            getattr(ch, "height", None) or getattr(ch, "h", None),
                                        ),
                                        list(getattr(ch, "shaders", None) or [])[:3] or None,
                                    )
                                )
                    for entry in list(getattr(node, "children", None) or [])[:8]:
                        ch = entry[0] if isinstance(entry, (tuple, list)) else entry
                        _dump_diss(ch, depth + 1, budget)

                _dump_diss(st)
            except Exception as e:
                lines.append(f"DISSOLVE dump fail: {e}")
            path = _struct_path(st)
            if path:
                lines.append("LIVE_STRUCT_PATH n=%d" % len(path))  # noqa: UP031
                for p in path:
                    lines.append(
                        "  path d={} {} mesh={} kids={} loaded={} cm={} cm_texs={} size={} sh={} extra={}".format(
                            p.get("d"),
                            p.get("cls"),
                            p.get("mesh"),
                            p.get("n_kids"),
                            p.get("loaded"),
                            p.get("has_cm"),
                            p.get("cm_texs"),
                            p.get("size"),
                            p.get("shaders"),
                            {k: p[k] for k in ("oy", "handle") if k in p},
                        )
                    )
            else:
                lines.append("LIVE_STRUCT_PATH none")

        if st is not None and draw is not None and hasattr(draw, "draw_screen"):
            # Count draw_model emissions during force of live tree.
            dm0 = {"n": 0, "tex": 0}
            _odm = _rh.draw_model

            def _wrap_dm(pipeline, mesh, texture=None, texture1=None, uniforms=None, texture2=None):
                dm0["n"] += 1
                if texture:
                    dm0["tex"] += 1
                return _odm(pipeline, mesh, texture, texture1, uniforms, texture2)

            try:
                _rh.draw_model = _wrap_dm
                draw.draw_screen(st, flip=True)
            finally:
                _rh.draw_model = _odm
            lines.append("LIVE_FORCE_DRAW cmds={} tex_cmds={}".format(dm0["n"], dm0["tex"]))
            w1, h1, rgba1 = _rh.read_game_rt_rgba()
            if w1 and h1 and rgba1:
                n = 0
                sr = sg = sb = 0
                nc = 0
                y0 = int(h1 * 0.88)
                for y in range(y0, h1, 3):
                    row = y * w1 * 4
                    for x in range(0, w1, 6):
                        i = row + x * 4
                        r, g, b = rgba1[i], rgba1[i + 1], rgba1[i + 2]
                        sr += r
                        sg += g
                        sb += b
                        n += 1
                        if abs(r - 13) > 12 or abs(g - 13) > 12 or abs(b - 20) > 12:
                            nc += 1
                lines.append(
                    f"LIVE_FORCE_DRAW dock_mean=({sr / n if n else 0:.1f},{sg / n if n else 0:.1f},{sb / n if n else 0:.1f}) nonclear={nc / n if n else 0:.3f}"
                )
            else:
                lines.append("LIVE_FORCE_DRAW empty_rt")
        else:
            lines.append(f"LIVE_FORCE_DRAW skip st={st is not None} draw={draw is not None}")
    except Exception as e:
        lines.append(f"LIVE_FORCE_DRAW fail: {e}")
    if live.get("all") is not None:
        lines.append("LIVE_SURFTREE all_textured (first 25):")
        for n in (live.get("all") or [])[:25]:
            lines.append(
                "  live d={} {} ox={:.0f} oy={:.0f} size={} tex={}".format(
                    n.get("depth"),
                    n.get("cls"),
                    n.get("ox", 0),
                    n.get("oy", 0),
                    n.get("size"),
                    n.get("tex"),
                )
            )

    # forced rebuild + surftree (prepare path)
    pres = _force_product_redraw()
    lines.append(
        "PRESENT path={} err={} root={} prepare_err={}".format(pres.get("path"), pres.get("error"), pres.get("root"), pres.get("prepare_err"))
    )
    surftree = pres.get("surftree")
    tex_nodes = []
    if surftree is not None:
        try:
            tex_nodes = _walk_surftree_textures(surftree)
            lines.append("SURFTREE textured_nodes=%d" % len(tex_nodes))  # noqa: UP031
            # All textured (not only dock) — logo / movie may be outside band
            lines.append("SURFTREE all_textured:")
            for n in tex_nodes[:40]:
                lines.append(
                    "  node d={} {} ox={:.0f} oy={:.0f} size={} tex={} loaded={} alpha={} mesh={}".format(
                        n.get("depth"),
                        n.get("cls"),
                        n.get("ox", 0),
                        n.get("oy", 0),
                        n.get("size"),
                        n.get("tex"),
                        n.get("loaded"),
                        n.get("u_renpy_alpha"),
                        n.get("mesh"),
                    )
                )
            dock_nodes = [n for n in tex_nodes if (n.get("oy") or 0) >= 900]
            lines.append("SURFTREE dock_band(oy>=900) n=%d" % len(dock_nodes))  # noqa: UP031
        except Exception as e:
            lines.append(f"SURFTREE walk fail: {e}")
            lines.append(traceback.format_exc()[-600:])

    # imagebuttons
    try:
        btns = _collect_imagebuttons()
    except Exception as e:
        lines.append(f"BUTTONS fail: {e}")
        lines.append(traceback.format_exc()[-600:])
        btns = []
    lines.append("BUTTONS n=%d" % len(btns))  # noqa: UP031
    for b in btns:
        if "error" in b and len(b) == 1:
            lines.append("  ERROR {}".format(b["error"]))
            continue
        lines.append(_fmt_btn(b, tex_nodes))

    # PIX from LIVE pre-force RT if available
    try:
        pre = getattr(_dump_phase, "_pre_rgba", None)
        if pre and btns:
            w0,h0,rgba0 = pre
            for b in btns:
                rect = b.get("dest_rect")
                if not rect or rect[0] is None:
                    continue
                vx = rect[0] + (rect[2] or 0) / 2.0
                vy = rect[1] + (rect[3] or 0) / 2.0
                px, py, _, _ = _virt_to_phys(vx, vy, pw=w0, ph=h0)
                samp = _sample_rect_mean(rgba0, w0, h0, px, py)
                if samp:
                    lines.append(
                        "  PIX_PRE action=%s virt_c=(%.0f,%.0f) phys_c=(%d,%d) mean_rgba=(%.1f,%.1f,%.1f,%.1f)"  # noqa: UP031
                        % (b.get("action"), vx, vy, px, py, samp[0], samp[1], samp[2], samp[3])
                    )
    except Exception as e:
        lines.append(f"PIX_PRE fail: {e}")

    # RT sample + per-button pixel means (post force present)
    try:
        rt = _sample_rt_bottom_band()
        lines.append(
            "RT_DOCK ok={} size={}x{} mean=({:.1f},{:.1f},{:.1f}) nonclear={:.3f}".format(
                rt.get("ok"),
                rt.get("w"),
                rt.get("h"),
                (rt.get("mean") or (0, 0, 0))[0],
                (rt.get("mean") or (0, 0, 0))[1],
                (rt.get("mean") or (0, 0, 0))[2],
                rt.get("nonclear_frac") or 0,
            )
        )
        rgba = rt.get("rgba")
        pw, ph = rt.get("w"), rt.get("h")
        if rgba and pw and ph:
            for b in btns:
                rect = b.get("dest_rect")
                if not rect or rect[0] is None:
                    continue
                vx = rect[0] + (rect[2] or 0) / 2.0
                vy = rect[1] + (rect[3] or 0) / 2.0
                px, py, _, _ = _virt_to_phys(vx, vy, pw=pw, ph=ph)
                samp = _sample_rect_mean(rgba, pw, ph, px, py)
                if samp:
                    lines.append(
                        "  PIX action=%s virt_c=(%.0f,%.0f) phys_c=(%d,%d) mean_rgba=(%.1f,%.1f,%.1f,%.1f)"  # noqa: UP031
                        % (
                            b.get("action"),
                            vx,
                            vy,
                            px,
                            py,
                            samp[0],
                            samp[1],
                            samp[2],
                            samp[3],
                        )
                    )
            # logo region sample (top-left-ish logo placement: full height image at 0,0 typically)
            for name, vx, vy in (
                ("logo_centerish", 340, 500),
                ("movie_center", 960, 540),
                ("dock_bg_mid", 960, 1010),
            ):
                px, py, _, _ = _virt_to_phys(vx, vy, pw=pw, ph=ph)
                samp = _sample_rect_mean(rgba, pw, ph, px, py)
                if samp:
                    lines.append(
                        "  PIX region=%s virt=(%d,%d) phys=(%d,%d) mean_rgba=(%.1f,%.1f,%.1f,%.1f)"  # noqa: UP031
                        % (name, vx, vy, px, py, samp[0], samp[1], samp[2], samp[3])
                    )
    except Exception as e:
        lines.append(f"RT_DOCK fail: {e}")
        lines.append(traceback.format_exc()[-400:])

    # loadable check for expected assets
    try:
        assets = [
            "gui/main_menu/start_idle.png",
            "gui/main_menu/start_hover.png",
            "gui/main_menu/load_idle.png",
            "gui/main_menu/extra_idle.png",
            "gui/main_menu/flowchart_idle.png",
            "gui/main_menu/config_idle.png",
            "gui/main_menu/exit_idle.png",
            "gui/main_menu/dock_bg.png",
            "gui/main_menu/logo.png",
        ]
        for a in assets:
            try:
                ok1 = renpy.loadable(a)
            except Exception as e:
                ok1 = f"err:{e}"
            try:
                ok2 = renpy.loadable(a, directory="images")
            except Exception as e:
                ok2 = f"err:{e}"
            lines.append(f"LOADABLE {a} plain={ok1} images_dir={ok2}")
    except Exception as e:
        lines.append(f"LOADABLE batch fail: {e}")

    # imagemap auto resolution for one button
    try:
        auto = "gui/main_menu/start_%s.png"
        fn = getattr(renpy.config, "imagemap_auto_function", None)
        if fn:
            for variant in ("idle", "hover", "selected_idle", "selected_hover", "insensitive"):
                try:
                    rv = fn(auto, variant)
                except Exception as e:
                    rv = f"err:{e}"
                lines.append(f"AUTO_RESOLVE start {variant} -> {rv!r}")
    except Exception as e:
        lines.append(f"AUTO_RESOLVE fail: {e}")

    return btns, fs, tex_nodes


def _mouse_move(x, y):
    """Move mouse without click (hover)."""
    import renpy_host

    try:
        from renpy import pygame

        pygame.mouse.set_pos((int(x), int(y)))
    except Exception:
        pass
    # inject_mouse always sends button event — send motion-only via inject with button 0?
    # Use inject_mouse press=False at pos (still emits button up). Prefer host inject.
    try:
        renpy_host.inject_mouse(int(x), int(y), 0, False)
    except Exception as e:
        return f"fail:{e}"
    return "ok"


def _center_of_action(btns, action_substr):
    for b in btns:
        a = b.get("action") or ""
        if action_substr in a:
            r = b.get("dest_rect")
            if r and r[0] is not None and r[2]:
                return int(r[0] + r[2] / 2), int(r[1] + r[3] / 2), b
    return None


def run():
    base = _base()
    out = base / "host" / "target" / "gate-hmc_idle_chrome_probe.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    art_root = os.environ.get(
        "HMC_IDLE_PROBE_ARTIFACT",
        str(
            base
            / ".omc"
            / "artifacts"
            / "huangmeic-visual-residual-reopen-20260718e"
            / "probes"
            / "step1-0-idle-probe-raw.txt"
        ),
    )
    lines = []
    state = {"main_menu": False, "phases": 0, "error": None}

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
            body = f"gate=hmc_idle_chrome_probe\nok=False\nerror={err}\n"
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
        rec("waiting main_menu (no mouse motion)")
        for i in range(500):
            try:
                if bool(getattr(renpy.store, "main_menu", False)):
                    state["main_menu"] = True
                    rec("main_menu at tick=%d" % i)  # noqa: UP031
                    break
            except Exception:
                pass
            time.sleep(0.05)
        if not state["main_menu"]:
            state["error"] = "main_menu_timeout"
            rec("main_menu timeout")
            _request_quit()
            return

        # Stabilize: Movie + chrome; NO mouse motion
        time.sleep(2.8)

        try:
            btns0, fs0, _tex0 = _dump_phase("IDLE_NO_MOUSE", lines)
            state["phases"] += 1
            state["idle_btns"] = btns0
            state["idle_focus"] = fs0
        except Exception as e:
            rec(f"IDLE dump fail: {e}")
            rec(traceback.format_exc()[-1200:])
            state["error"] = f"idle_dump:{e}"
            _request_quit()
            return

        # Hover EXTRA
        loc = _center_of_action(btns0, "appreciation")
        if loc is None:
            # try EXTRA by name / flowchart fallbacks; estimate dock positions
            # dock y≈969+3+46 = center ~1015; 6 buttons 241 wide spacing -5
            # centered hbox of 6*241 + 5*(-5) = 1446-25=1421; x0=(1920-1421)/2=249.5
            rec("EXTRA focus rect missing — using geometric estimate")
            # order: start, [continue?], load, extra, flowchart, config, exit
            # without continue: 0 start, 1 load, 2 extra, 3 flowchart, 4 config, 5 exit
            x0 = 250
            bw = 241
            sp = -5
            idx = 2
            cx = int(x0 + idx * (bw + sp) + bw / 2)
            cy = 1015
            loc = (cx, cy, None)
        mx, my = loc[0], loc[1]
        rec(f"MOUSE_MOVE EXTRA -> ({mx},{my})")
        rec(f"mouse_move result={_mouse_move(mx, my)}")
        time.sleep(0.6)
        try:
            # force interaction restart so hover restyle applies
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            time.sleep(0.4)
            btns1, fs1, _tex1 = _dump_phase("HOVER_EXTRA", lines)
            state["phases"] += 1
            state["hover_extra_btns"] = btns1
            state["hover_extra_focus"] = fs1
        except Exception as e:
            rec(f"HOVER_EXTRA dump fail: {e}")
            rec(traceback.format_exc()[-800:])

        # Hover Start
        loc2 = _center_of_action(btns0, "Start")
        if loc2 is None:
            loc2 = _center_of_action(state.get("hover_extra_btns") or [], "Start")
        if loc2 is None:
            rec("START focus rect missing — geometric estimate idx=0")
            loc2 = (int(250 + 241 / 2), 1015, None)
        mx2, my2 = loc2[0], loc2[1]
        rec(f"MOUSE_MOVE START -> ({mx2},{my2})")
        rec(f"mouse_move result={_mouse_move(mx2, my2)}")
        time.sleep(0.6)
        try:
            try:
                renpy.restart_interaction()
            except Exception:
                pass
            time.sleep(0.4)
            btns2, fs2, _tex2 = _dump_phase("HOVER_START", lines)
            state["phases"] += 1
            state["hover_start_btns"] = btns2
            state["hover_start_focus"] = fs2
        except Exception as e:
            rec(f"HOVER_START dump fail: {e}")
            rec(traceback.format_exc()[-800:])

        # Classification helpers from idle dump
        rec("")
        rec("=" * 72)
        rec("AUTO_CLASSIFY_HINTS")
        rec("=" * 72)
        idle_btns = state.get("idle_btns") or []
        prefixes = {}
        missing_child = []
        no_tex = []
        for b in idle_btns:
            if b.get("type") != "ImageButton":
                continue
            a = b.get("action") or "?"
            prefixes[a] = b.get("style_prefix")
            if b.get("active_state_missing") or not (b.get("get_child") or {}).get("path"):
                missing_child.append(a)
            # tex hits would need re-walk; use dest
            if b.get("dest_rect") and b.get("dest_rect")[0] is not None:
                pass
            else:
                no_tex.append(a)
        rec(f"idle_prefixes={prefixes!r}")
        rec(f"missing_or_empty_child={missing_child!r}")
        rec(f"no_dest_rect={no_tex!r}")
        fs = state.get("idle_focus") or {}
        rec(
            "idle_focused={} action={} prefix={}".format(fs.get("focused_type"), fs.get("focused_action"), fs.get("focused_prefix"))
        )

        # Heuristic classification notes (final write-up in md)
        idle_prefixes_set = set(prefixes.values())
        if idle_prefixes_set == {"idle_"} or idle_prefixes_set <= {"idle_", None}:
            rec("HINT: all idle_ prefix at rest — H-Idle-B (focus seed selected_) unlikely as root")
        elif any(p and str(p).startswith("selected_") for p in idle_prefixes_set):
            rec("HINT: selected_* prefix present at idle — H-Idle-B candidate")
        elif any(p and str(p).startswith("hover_") for p in idle_prefixes_set):
            rec("HINT: hover_ prefix without mouse — H-Idle-B focus stickiness")

        for b in idle_btns:
            if b.get("type") != "ImageButton":
                continue
            sc = b.get("state_children") or {}
            idle_p = (sc.get("idle_") or {}).get("path")
            if idle_p is None or idle_p.startswith("ImageButton"):
                rec("HINT: idle_ state child path missing for {} — H-Idle-A asset resolve".format(b.get("action")))
            hover_p = (sc.get("hover_") or {}).get("path")
            if idle_p and hover_p and idle_p == hover_p:
                rec("HINT: idle_==hover_ path for {} — auto may have failed one variant".format(b.get("action")))

        time.sleep(0.2)
        _request_quit()

    t = threading.Thread(target=injector, daemon=True)
    t.start()
    try:
        import renpy.main as renpy_main

        renpy_main.main()
    except BaseException as e:
        rec(f"main exit {type(e).__name__}: {e}")
    t.join(timeout=2.0)

    ok = bool(state.get("main_menu")) and state.get("phases", 0) >= 1 and not state.get("error")
    header = [
        "gate=hmc_idle_chrome_probe",
        f"ok={ok}",
        "main_menu={}".format(state.get("main_menu")),
        "phases={}".format(state.get("phases")),
        "error={}".format(state.get("error")),
        "",
    ]
    body = "\n".join(header + lines) + "\n"
    out.write_text(body)
    try:
        Path(art_root).parent.mkdir(parents=True, exist_ok=True)
        Path(art_root).write_text(body)
        rec(f"wrote artifact {art_root}")
    except Exception as e:
        rec(f"artifact write fail: {e}")
    try:
        sys.__stdout__.write(body[-4000:])
        sys.__stdout__.flush()
    except Exception:
        pass
    _request_quit()
    if not ok:
        raise RuntimeError("hmc_idle_chrome_probe failed: {}".format(state.get("error")))


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
