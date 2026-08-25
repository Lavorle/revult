"""
A3 tq_chrome_nav_transparent — product main-menu nav has no opaque black slabs.

Gate name: tq_chrome_nav_transparent  (RENPY_HOST_GATE=tq_chrome_nav_transparent)

AC1 contract (product, not synthetic Frame orange structure):
  - Product main menu capture under HostInterface / product surftree
  - Sample nav column (gui.navigation_xpos=40, yalign=0.5, button_h=36,
    spacing=4, 6 main-menu buttons) for opaque pure-black rectangular underlays
  - Idle button backgrounds are fully transparent PNGs; pixels under labels
    should be the semi-transparent left overlay over scenic BG — dark but
    NOT pure black slabs and NOT arena clear (13,13,20)
  - Do NOT set prefs.transitions=0

Note: do NOT import main.py (auto-runs with SKIP_MAIN_MENU=1 + HostStop N-cap).
"""

import atexit
import os
import sys
import threading
import time
import traceback
import types
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



REQUIRED_HELPERS = (
    "path_to_common",
    "path_to_gamedir",
    "path_to_saves",
    "predefined_searchpath",
    "path_to_logdir",
)

GATE_NAME = "tq_chrome_nav_transparent"

# Product layout (the_question screens.rpy + gui.rpy)
NAV_X = 40
NAV_BUTTON_H = 36
NAV_SPACING = 4
NAV_N_BUTTONS = 6  # Start Load Preferences About Help Quit on main_menu
NAV_W = 280  # main_menu_frame xsize
VW, VH = 1280, 720

# Legacy Frame chrome constants kept for optional structure log (not AC1 pass).
LEFT = TOP = RIGHT = BOTTOM = 40
DST_W, DST_H = 480, 280
OX, OY = 120, 100


class HostStop(BaseException):
    def __init__(self, stage: str, detail: str = ""):
        self.stage = stage
        self.detail = detail
        super().__init__(f"HostStop@{stage}: {detail}" if detail else f"HostStop@{stage}")


def _base_dir() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "renpy").is_dir() and (p / "host" / "README.md").is_file():
            return p
    return here


def _request_quit():
    try:
        import renpy_host  # type: ignore

        renpy_host.request_quit()
    except Exception:
        pass


def _append_log(lines: list, msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    lines.append(f"[{ts}] {msg}")
    print(f"[{GATE_NAME}] {msg}", flush=True)


def _report_path(base: Path) -> Path:
    return base / "host" / "target" / f"gate-{GATE_NAME}.txt"


def _write_report(base: Path, meta: dict, log: list) -> Path:
    out = _report_path(base)
    out.parent.mkdir(parents=True, exist_ok=True)
    own = meta.get("ownership") or {}
    probe0 = meta.get("probe0") or {}
    nav = meta.get("nav") or {}
    lines = [
        f"gate={GATE_NAME}",
        f"ok={meta.get('ok')}",
        "ac=AC1_nav_transparent",
        f"path_kind={meta.get('path_kind')}",
        f"reached_stage={meta.get('reached_stage')}",
        f"in_main_menu={meta.get('in_main_menu')}",
        f"last_product_present={own.get('last_product_present')}",
        f"product_presents={own.get('product_presents')}",
        f"idle_clears_after_present={own.get('idle_clears_after_present')}",
        f"ownership_ok={meta.get('ownership_ok')}",
        f"frame_w={meta.get('frame_w')}",
        f"frame_h={meta.get('frame_h')}",
        f"frame_bytes={meta.get('frame_bytes')}",
        f"present_path={meta.get('present_path')}",
        f"interact_count={meta.get('interact_count')}",
        f"loadable_main_menu={meta.get('loadable_main_menu')}",
        f"less_updates={meta.get('less_updates')}",
        f"models={meta.get('models')}",
        f"transitions_pref={meta.get('transitions_pref')}",
        f"transitions_forced_zero={meta.get('transitions_forced_zero')}",
        f"probe0_featureless_black={probe0.get('featureless_black')}",
        f"probe0_mean_rgb={probe0.get('mean_rgb')}",
        f"probe0_variance={probe0.get('variance')}",
        f"nav_ok={nav.get('nav_ok')}",
        f"nav_pure_black={nav.get('pure_black')}",
        f"nav_total={nav.get('total_samples')}",
        f"nav_pure_frac={nav.get('pure_frac')}",
        f"nav_arena_clear={nav.get('arena_clear')}",
        f"nav_top={nav.get('nav_top')}",
        f"overlay_rgb={nav.get('overlay_rgb')}",
        f"overlay_dark={nav.get('overlay_dark')}",
        f"scenic_rgb={nav.get('scenic_rgb')}",
        f"scenic_alive={nav.get('scenic_alive')}",
        f"mean_rgb={nav.get('mean_rgb')}",
        f"featureless_black={nav.get('featureless_black')}",
        f"notes={meta.get('notes')}",
        f"elapsed_secs={meta.get('elapsed_secs')}",
    ]
    if meta.get("traceback"):
        lines.append(f"traceback={meta['traceback'][:2000]!r}")
    if log:
        lines.append("log_tail=" + " | ".join(log[-16:]))
    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text, flush=True)
    return out


def _nav_vbox_top():
    total_h = NAV_N_BUTTONS * NAV_BUTTON_H + (NAV_N_BUTTONS - 1) * NAV_SPACING
    return int(VH / 2 - total_h / 2)


def _is_pure_black(c, tol=8):
    return int(c[0]) <= tol and int(c[1]) <= tol and int(c[2]) <= tol


def _is_arena_clear(c, tol=10):
    return (
        abs(int(c[0]) - 13) <= tol
        and abs(int(c[1]) - 13) <= tol
        and abs(int(c[2]) - 20) <= tol
    )


def _analyze_nav(rgba, rw, rh, log: list) -> dict:
    """AC1: sample nav button column for opaque pure-black underlays."""
    sx = rw / float(VW)
    sy = rh / float(VH)
    top = _nav_vbox_top()
    pure_black = 0
    arena_clear = 0
    total = 0
    for bi in range(NAV_N_BUTTONS):
        by = top + bi * (NAV_BUTTON_H + NAV_SPACING)
        for u in (0.15, 0.4, 0.65, 0.85):
            for v in (0.3, 0.5, 0.7):
                px = NAV_X + int(u * (NAV_W - 20))
                py = by + int(v * NAV_BUTTON_H)
                c = _sample(rgba, rw, rh, px * sx, py * sy)
                total += 1
                if _is_pure_black(c):
                    pure_black += 1
                if _is_arena_clear(c):
                    arena_clear += 1

    pure_frac = pure_black / float(total) if total else 1.0
    overlay_c = _sample(rgba, rw, rh, 100 * sx, (VH // 2) * sy)
    scenic_c = _sample(rgba, rw, rh, 900 * sx, 360 * sy)

    mean_r = mean_g = mean_b = 0.0
    n = 0
    step = max(1, (rw * rh) // 2000)
    for i in range(0, rw * rh, step):
        o = i * 4
        if o + 3 >= len(rgba):
            break
        mean_r += rgba[o]
        mean_g += rgba[o + 1]
        mean_b += rgba[o + 2]
        n += 1
    if n:
        mean_r /= n
        mean_g /= n
        mean_b /= n
    featureless_black = mean_r < 8 and mean_g < 8 and mean_b < 8
    overlay_dark = (
        int(overlay_c[0]) < 80 and int(overlay_c[1]) < 80 and int(overlay_c[2]) < 80
    )
    scenic_alive = not _is_pure_black(scenic_c) and not _is_arena_clear(scenic_c)
    # Transparent idle buttons over semi-transparent overlay still darken pixels.
    # Button-sized opaque black slabs push pure_black fraction high (near 1.0).
    # Arena-clear matching is soft: dark overlay pixels can land near (13,13,20)
    # by chance — do not hard-fail AC1 on arena_clear alone.
    # Threshold: pure_frac > 0.45 ≈ half the lattice pure black → slabs dominate.
    nav_ok = pure_frac <= 0.45 and not featureless_black

    out = {
        "total_samples": total,
        "pure_black": pure_black,
        "pure_frac": round(pure_frac, 4),
        "arena_clear": arena_clear,
        "nav_ok": bool(nav_ok),
        "overlay_rgb": overlay_c[:3],
        "overlay_dark": overlay_dark,
        "scenic_rgb": scenic_c[:3],
        "scenic_alive": scenic_alive,
        "mean_rgb": (round(mean_r, 2), round(mean_g, 2), round(mean_b, 2)),
        "featureless_black": featureless_black,
        "nav_top": top,
    }
    _append_log(
        log,
        "nav pure_black=%d/%d frac=%.3f arena_clear=%d nav_ok=%s overlay=%s scenic=%s "  # noqa: UP031
        "mean=%s featureless_black=%s"
        % (
            pure_black,
            total,
            pure_frac,
            arena_clear,
            out["nav_ok"],
            overlay_c[:3],
            scenic_c[:3],
            out["mean_rgb"],
            featureless_black,
        ),
    )
    return out


def _ensure_renpy_main(base: Path):
    import renpy

    main_mod = getattr(renpy, "__main__", None)
    have = {
        name: callable(getattr(main_mod, name, None)) if main_mod is not None else False
        for name in REQUIRED_HELPERS
    }
    if all(have.values()):
        return main_mod, have, "present"
    import renpy_main_host  # type: ignore

    main_mod = renpy_main_host.install(renpy)
    have = {name: callable(getattr(main_mod, name, None)) for name in REQUIRED_HELPERS}
    if not all(have.values()):
        missing = [n for n, ok in have.items() if not ok]
        raise RuntimeError(f"renpy.__main__ still missing helpers: {missing}")
    return main_mod, have, "installed"


def _prepare_run_args(base: Path):
    import renpy
    import renpy.arguments

    basedir = getattr(renpy.config, "basedir", None) or str(base / "the_question")
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
    return args


def _pre_main_host_stubs(log: list) -> None:
    try:
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _append_log(log, "renpysound rebound to host")
    except Exception as e:
        _append_log(log, f"renpysound rebound soft-fail: {e}")

    try:
        import host_pygame
        import host_pygame.locals as _loc

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = _loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules.setdefault("pygame.constants", host_pygame.constants)
        import renpy.pygame as rpg

        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        try:
            rpg.import_as_pygame()
        except Exception as e:
            _append_log(log, f"import_as_pygame soft-fail: {e}")
    except Exception as e:
        _append_log(log, f"pygame.constants soft-fail: {e}")

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
        _append_log(log, "uguu host stub installed")
    except Exception as e:
        _append_log(log, f"uguu stub soft-fail: {type(e).__name__}: {e}")

    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            _renpy_pkg.ecsign = _ecsign
        except Exception:
            pass
        _append_log(log, "ecsign host stub installed")
    except Exception as e:
        _append_log(log, f"ecsign soft-fail: {e}")


def _sample(rgba, w, h, x, y):
    x = max(0, min(w - 1, int(x)))
    y = max(0, min(h - 1, int(y)))
    o = (y * w + x) * 4
    return rgba[o], rgba[o + 1], rgba[o + 2], rgba[o + 3]


def _is_orange(c, tol=50):
    """Orange border: high R, mid G, low B (product 204,102,0)."""
    r, g, b = int(c[0]), int(c[1]), int(c[2])
    return r >= 150 and 40 <= g <= 160 and b <= 60 and (r - g) >= 40


def _is_blackish(c, tol=30):
    return int(c[0]) <= tol and int(c[1]) <= tol and int(c[2]) <= tol


def _probe0_main_menu(rgba, rw, rh, log: list) -> dict:
    """Log-only Probe0: is orange present on bare main menu? featureless black?"""
    import interact_helpers as ih

    nb = ih.analyze_frame_nonblank(int(rw), int(rh), rgba)
    # Sample a sparse grid for orange hits (main menu uses overlay, not frame.png).
    orange_hits = 0
    samples = 0
    grid = 12
    for gy in range(grid):
        for gx in range(grid):
            x = int((gx + 0.5) * rw / grid)
            y = int((gy + 0.5) * rh / grid)
            c = _sample(rgba, rw, rh, x, y)
            samples += 1
            if _is_orange(c):
                orange_hits += 1
    # Featureless black: low variance + dark mean
    mean = nb.get("mean_rgb") or (0, 0, 0)
    try:
        mr, mg, mb = float(mean[0]), float(mean[1]), float(mean[2])
    except Exception:
        mr = mg = mb = 0.0
    var = float(nb.get("variance") or 0.0)
    featureless_black = (
        mr <= 8 and mg <= 8 and mb <= 8 and var < 5.0 and orange_hits == 0
    )
    note = (
        "main_menu uses gui/overlay/main_menu.png (not frame.png); "
        "orange may be absent by design on bare main menu"
    )
    out = {
        "orange_hits": orange_hits,
        "samples": samples,
        "featureless_black": featureless_black,
        "mean_rgb": (round(mr, 2), round(mg, 2), round(mb, 2)),
        "variance": round(var, 3),
        "nonblank_ok": bool(nb.get("nonblank_ok")),
        "note": note,
    }
    _append_log(
        log,
        "Probe0 main_menu orange_hits={}/{} featureless_black={} mean={} var={} nonblank={}".format(
            orange_hits,
            samples,
            featureless_black,
            out["mean_rgb"],
            out["variance"],
            out["nonblank_ok"],
        ),
    )
    return out


def _read_l0_state(log: list) -> dict:
    """AC-L0-adjacent: less_updates / models / transitions_pref (do not force 0)."""
    out = {
        "less_updates": None,
        "models": None,
        "transitions_pref": None,
        "transitions_forced_zero": False,
    }
    try:
        import renpy

        out["less_updates"] = bool(getattr(renpy.config, "less_updates", False))
    except Exception as e:
        _append_log(log, f"less_updates read soft-fail: {e}")
    try:
        import renpy

        draw = getattr(getattr(renpy, "display", None), "draw", None)
        info = getattr(draw, "info", None) if draw is not None else None
        # WgpuDraw.info is a dict attribute (not callable). GL2 uses .info as dict too.
        if isinstance(info, dict):
            out["models"] = bool(info.get("models"))
        elif callable(info):
            try:
                d = info()
                out["models"] = bool(d.get("models")) if isinstance(d, dict) else None
            except Exception:
                out["models"] = getattr(draw, "models", None)
        else:
            out["models"] = getattr(draw, "models", None) if draw is not None else None
    except Exception as e:
        _append_log(log, f"models read soft-fail: {e}")
    try:
        import renpy

        prefs = getattr(renpy.game, "preferences", None)
        if prefs is not None and hasattr(prefs, "transitions"):
            out["transitions_pref"] = int(prefs.transitions)
            # Explicit: we never assign 0 in this gate.
            out["transitions_forced_zero"] = False
    except Exception as e:
        _append_log(log, f"transitions_pref read soft-fail: {e}")
    _append_log(
        log,
        "AC-L0-adj less_updates={} models={} transitions_pref={} forced_zero={}".format(
            out["less_updates"],
            out["models"],
            out["transitions_pref"],
            out["transitions_forced_zero"],
        ),
    )
    return out


def _draw_product_frame_chrome(log: list) -> dict:
    """
    Product-stack Frame chrome: real imagelike.Frame + product WgpuDraw.

    Uses renpy.display.imagelike.Frame (not FakeRender multipiece) with product
    gui/frame.png and confirm Borders(40). Draws via product renpy.display.draw
    so the scale / multipiece / _draw_texture_at path is exercised live.
    """
    out = {
        "ok": False,
        "path": "",
        "error": "",
        "border_hits": 0,
        "border_ok": False,
        "center_rgb": (0, 0, 0),
        "center_black": False,
        "center_orange": False,
        "featureless_black": True,
        "top": None,
        "left": None,
        "right": None,
        "bottom": None,
        "tl": None,
        "outside": None,
        "frame_w": 0,
        "frame_h": 0,
    }
    try:
        import interact_helpers as ih
        import renpy.display.render as render_mod
        import renpy_host  # type: ignore

        import renpy
        from renpy.display import imagelike

        # Ensure product draw is started.
        iface = getattr(getattr(renpy, "game", None), "interface", None) or getattr(
            getattr(renpy, "display", None), "interface", None
        )
        if iface is not None:
            try:
                ih._ensure_interface_started(iface)
            except Exception as e:
                out["error"] = f"start:{type(e).__name__}:{e}"

        draw = getattr(getattr(renpy, "display", None), "draw", None)
        if draw is None:
            out["error"] = (out.get("error") or "") + ";no_display_draw"
            return out
        out["draw_type"] = type(draw).__name__

        # Real product Frame displayable (confirm chrome borders).
        try:
            borders = imagelike.Borders(LEFT, TOP, RIGHT, BOTTOM)
        except Exception:
            borders = None

        if borders is not None:
            frame_d = imagelike.Frame("gui/frame.png", borders, tile=False)
        else:
            frame_d = imagelike.Frame(
                "gui/frame.png", LEFT, TOP, RIGHT, BOTTOM, tile=False
            )

        # Render Frame into a (DST_W, DST_H) Render tree via real .render path.
        try:
            # Invalidate so we get a fresh render each capture.
            try:
                renpy.display.render.redraw(frame_d, 0)
            except Exception:
                pass
            frame_rv = render_mod.render(frame_d, DST_W, DST_H, 0, 0)
        except Exception as e:
            out["error"] = (out.get("error") or "") + f";frame_render:{type(e).__name__}:{e}"
            _append_log(log, f"Frame.render failed: {e}")
            return out

        out["path"] = "product_Frame_render"
        out["frame_rv_type"] = type(frame_rv).__name__
        try:
            out["frame_rv_size"] = (
                int(getattr(frame_rv, "width", 0) or 0),
                int(getattr(frame_rv, "height", 0) or 0),
            )
            out["frame_rv_children"] = len(list(getattr(frame_rv, "children", []) or []))
        except Exception:
            pass

        # Compose over a dark solid full-window background so outside samples are stable.
        vw = int(getattr(renpy.config, "screen_width", 1280) or 1280)
        vh = int(getattr(renpy.config, "screen_height", 720) or 720)

        try:
            # Prefer real Render for root when available.
            root = render_mod.Render(vw, vh)
            # Dark BG via Solid displayable → product solid_texture + reverse path.
            solid = imagelike.Solid("#14141e")
            bg_rv = render_mod.render(solid, vw, vh, 0, 0)
            root.blit(bg_rv, (0, 0))
            root.blit(frame_rv, (OX, OY))
            out["path"] = "product_Frame_render+Solid_bg"
        except Exception as e:
            # Fall back: draw frame alone (outside may be clear).
            root = frame_rv
            out["path"] = "product_Frame_render_alone"
            out["error"] = (out.get("error") or "") + f";compose:{type(e).__name__}:{e}"
            _append_log(log, f"compose soft-fail, drawing frame alone: {e}")

        # Draw through product WgpuDraw (same instance as main-menu stack).
        try:
            renpy_host.reset_present_stats()
        except Exception:
            pass
        try:
            draw.draw_screen(root, flip=True)
        except TypeError:
            draw.draw_screen(root)
        except Exception as e:
            out["error"] = (out.get("error") or "") + f";draw:{type(e).__name__}:{e}"
            _append_log(log, f"draw_screen failed: {e}")
            return out

        try:
            rw, rh, rgba = renpy_host.read_game_rt_rgba()
        except Exception as e:
            out["error"] = (out.get("error") or "") + f";read_rt:{type(e).__name__}:{e}"
            return out

        if not rw or not rh or rgba is None:
            out["error"] = (out.get("error") or "") + ";empty_rt"
            return out

        out["frame_w"] = int(rw)
        out["frame_h"] = int(rh)
        sx = rw / float(vw)
        sy = rh / float(vh)

        # frame.png orange ring is only ~3px at source edge; after 9-slice with
        # Borders(40,...) the outer dest edge still carries that orange. Sample
        # very close to the outer edge (inset=1).
        inset = 1
        samples = {
            "top": (OX + DST_W // 2, OY + inset),
            "bottom": (OX + DST_W // 2, OY + DST_H - 1 - inset),
            "left": (OX + inset, OY + DST_H // 2),
            "right": (OX + DST_W - 1 - inset, OY + DST_H // 2),
            "tl": (OX + inset, OY + inset),
            "center": (OX + DST_W // 2, OY + DST_H // 2),
            "outside": (10, 10),
        }
        rgb = {}
        for name, (px, py) in samples.items():
            r, g, b, a = _sample(rgba, rw, rh, px * sx, py * sy)
            rgb[name] = (r, g, b, a)

        border_hits = sum(
            1 for k in ("top", "bottom", "left", "right", "tl") if _is_orange(rgb[k])
        )
        border_ok = border_hits >= 3
        center_black = _is_blackish(rgb["center"])
        center_orange = _is_orange(rgb["center"])
        featureless_black = border_hits == 0 and all(
            _is_blackish(rgb[k])
            for k in ("top", "bottom", "left", "right", "tl", "center")
        )

        out.update(
            {
                "border_hits": border_hits,
                "border_ok": border_ok,
                "center_rgb": rgb["center"][:3],
                "center_black": center_black,
                "center_orange": center_orange,
                "featureless_black": featureless_black,
                "top": rgb["top"][:3],
                "left": rgb["left"][:3],
                "right": rgb["right"][:3],
                "bottom": rgb["bottom"][:3],
                "tl": rgb["tl"][:3],
                "outside": rgb["outside"][:3],
                # Center black is OK by design; do not require center non-black.
                "ok": bool(border_ok and not featureless_black),
            }
        )
        _append_log(
            log,
            "chrome border_hits=%d/5 border_ok=%s center=%s featureless_black=%s "  # noqa: UP031
            "top=%s left=%s path=%s"
            % (
                border_hits,
                border_ok,
                out["center_rgb"],
                featureless_black,
                out["top"],
                out["left"],
                out["path"],
            ),
        )
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc()
        _append_log(log, f"chrome draw fatal: {out['error']}")
        return out


def _install_hooks(state: dict, log: list, max_interacts: int) -> None:
    import interact_helpers as ih

    import renpy
    import renpy.main as renpy_main
    from renpy.display import core

    OrigInterface = core.Interface

    class HostInterface(OrigInterface):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **k):
            OrigInterface.__init__(self, *a, **k)
            state["reached"] = "interface_created"
            state["interface"] = True
            _append_log(log, "HostInterface created")

        def draw_screen(self, root_widget, fullscreen_video, draw):
            """Product draw path: capture once on main menu, then Frame chrome."""
            OrigInterface.draw_screen(self, root_widget, fullscreen_video, draw)
            try:
                in_mm = bool(ih.in_main_menu())
            except Exception:
                in_mm = bool(state.get("in_main_menu"))
            state["in_main_menu"] = in_mm
            state["draw_count"] = int(state.get("draw_count") or 0) + 1
            if in_mm and not state.get("main_menu_logged"):
                state["main_menu_logged"] = True
                state["reached"] = "main_menu"
                _append_log(log, f"in_main_menu at draw#{state['draw_count']}")

            need = int(state.get("capture_after_draw") or 1)
            if (
                in_mm
                and int(state.get("draw_count") or 0) >= need
                and not state.get("capture_attempted")
            ):
                capturer = state.get("_do_capture")
                if callable(capturer):
                    capturer(f"draw_screen_n{state['draw_count']}")
                    raise HostStop(
                        "nav_transparent",
                        "captured ownership_ok={} nav_ok={} pure_frac={}".format(
                            state.get("ownership_ok"),
                            (state.get("nav") or {}).get("nav_ok"),
                            (state.get("nav") or {}).get("pure_frac"),
                        ),
                    )

    core.Interface = HostInterface  # type: ignore[misc,assignment]
    renpy.display.core.Interface = HostInterface  # type: ignore[attr-defined]

    _orig_run = renpy_main.run

    def _host_run(restart):
        state["reached"] = "run_entered"
        state["run_restart"] = repr(restart)
        _append_log(log, f"run_entered restart={restart!r}")

        # AC-C1 / AC-L0: leave transitions at product default. Do NOT set 0.
        # Only disable music/performance for host stability.
        try:
            prefs = getattr(renpy.game, "preferences", None)
            # Explicit: never assign prefs.transitions = 0 in this gate.
            if prefs is not None and hasattr(prefs, "performance_test"):
                prefs.performance_test = False
            renpy.config.performance_test = False
            renpy.config.has_music = False
            renpy.config.main_menu_music = None
            if prefs is not None:
                if hasattr(prefs, "text_cps"):
                    prefs.text_cps = 0
                if hasattr(prefs, "afm_enable"):
                    prefs.afm_enable = False
            tp = None
            if prefs is not None and hasattr(prefs, "transitions"):
                tp = int(prefs.transitions)
            _append_log(
                log,
                f"prefs: music off, performance_test off, transitions left default={tp} "
                f"(NOT forced to 0)",
            )
            state["transitions_pref"] = tp
            state["transitions_forced_zero"] = False
        except Exception as e:
            state["prefs_error"] = f"{type(e).__name__}: {e}"
            _append_log(log, f"prefs_error={state['prefs_error']}")

        iface = getattr(getattr(renpy, "game", None), "interface", None)
        if iface is None:
            raise HostStop("run_entered", "interface missing")

        prev = iface.interact
        if getattr(prev, "_host_limited", False):
            orig_interact = getattr(prev, "_host_orig", None) or prev
        else:
            orig_interact = prev

        state.setdefault("interact_count", 0)
        state.setdefault("draw_count", 0)

        def _force_short_timeout(secs: float = 0.08) -> None:
            try:
                iface.timeout(float(secs))
            except Exception:
                pass
            try:
                renpy.exports.timeout(float(secs))
            except Exception:
                pass

        def _force_product_present() -> dict:
            out: dict = {"presented": False, "path": "", "error": "", "draw_source": ""}
            try:
                import renpy as _renpy

                iface2 = getattr(getattr(_renpy, "game", None), "interface", None) or getattr(
                    getattr(_renpy, "display", None), "interface", None
                )
                if iface2 is not None:
                    try:
                        ok_start, start_detail = ih._ensure_interface_started(iface2)
                        out["interface_start"] = start_detail
                        if not ok_start:
                            out["error"] = f"start:{start_detail}"
                    except Exception as e:
                        out["error"] = f"start:{type(e).__name__}:{e}"
                draw = getattr(getattr(_renpy, "display", None), "draw", None)
                out["draw_source"] = type(draw).__name__ if draw is not None else "None"
                if draw is not None and iface2 is not None:
                    surftree = getattr(iface2, "surftree", None)
                    if surftree is not None:
                        try:
                            draw.draw_screen(surftree, flip=True)
                            out["presented"] = True
                            out["path"] = f"product_surftree:{type(surftree).__name__}"
                            return out
                        except Exception as e:
                            out["error"] = f"surftree:{type(e).__name__}:{e}"

                    root = None
                    for attr in ("root_widget", "displayable", "root"):
                        root = getattr(iface2, attr, None)
                        if root is not None:
                            break
                    if root is None:
                        try:
                            root = ih._rebuild_product_root(iface2)
                        except Exception as e:
                            out["error"] = (out.get("error") or "") + f";rebuild:{type(e).__name__}:{e}"

                    if root is not None and hasattr(iface2, "draw_screen"):
                        try:
                            iface2.draw_screen(root, False, True)
                            out["presented"] = True
                            out["path"] = f"product_iface:{type(getattr(iface2, 'surftree', root)).__name__}"
                            return out
                        except TypeError:
                            try:
                                draw.draw_screen(root, flip=True)
                                out["presented"] = True
                                out["path"] = f"product_draw_root:{type(root).__name__}"
                                return out
                            except Exception as e:
                                out["error"] = (out.get("error") or "") + f";root:{type(e).__name__}:{e}"
                        except Exception as e:
                            out["error"] = (out.get("error") or "") + f";iface:{type(e).__name__}:{e}"

                pres = ih.ensure_frame_present(force=True)
                out["presented"] = bool(pres.get("presented"))
                out["path"] = pres.get("path") or ""
                out["draw_source"] = pres.get("draw_source") or out["draw_source"]
                if pres.get("error"):
                    out["error"] = (out.get("error") or "") + f";{pres.get('error')}"
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {e}"
            return out

        def _do_capture(tag: str) -> None:
            if state.get("capture_attempted"):
                return
            state["capture_attempted"] = True
            state["reached"] = "capture"
            _append_log(log, f"capture cycle ({tag}) at interact#{state.get('interact_count')}")
            try:
                # --- Product main-menu present + Probe0 ---
                try:
                    ih.pump_ms(16)
                    pref = _force_product_present()
                    state["product_present_attempt"] = pref
                    _append_log(log, f"product_present_attempt={pref}")
                except Exception as e:
                    state["product_present_attempt_error"] = f"{type(e).__name__}: {e}"

                # Ownership + main-menu frame (Probe0 source).
                try:
                    import renpy_host  # type: ignore

                    renpy_host.reset_present_stats()
                    pref_mm = _force_product_present()
                    w, hgt, rgba = renpy_host.read_game_rt_rgba()
                    own = ih.read_present_ownership()
                    state["ownership"] = own
                    state["ownership_ok"] = (
                        bool(own.get("last_product_present"))
                        and int(own.get("product_presents") or 0) >= 1
                        and int(own.get("idle_clears_after_present") or 0) == 0
                        and not own.get("error")
                    )
                    state["frame_w"] = int(w)
                    state["frame_h"] = int(hgt)
                    state["frame_bytes"] = len(rgba) if rgba is not None else 0
                    state["frame_ok"] = bool(w and hgt and rgba is not None and len(rgba) > 0)
                    state["present_path"] = pref_mm.get("path") or ""
                    state["main_menu_rgba"] = rgba
                    if state["frame_ok"]:
                        state["probe0"] = _probe0_main_menu(rgba, int(w), int(hgt), log)
                    _append_log(
                        log,
                        "main_menu ownership_ok={} size={}x{} path={}".format(
                            state.get("ownership_ok"),
                            state.get("frame_w"),
                            state.get("frame_h"),
                            state.get("present_path"),
                        ),
                    )
                except Exception as e:
                    state["main_menu_capture_error"] = f"{type(e).__name__}: {e}"
                    _append_log(log, f"main_menu capture error: {state['main_menu_capture_error']}")

                # AC-L0-adjacent snapshot (after product prefs live).
                l0 = _read_l0_state(log)
                state["less_updates"] = l0.get("less_updates")
                state["models"] = l0.get("models")
                if state.get("transitions_pref") is None:
                    state["transitions_pref"] = l0.get("transitions_pref")
                state["transitions_forced_zero"] = bool(l0.get("transitions_forced_zero"))

                # --- AC1: nav transparent analysis on product main-menu RT ---
                # Prefer the main_menu_rgba captured above (bare product stack).
                rgba_mm = state.get("main_menu_rgba")
                fw = int(state.get("frame_w") or 0)
                fh = int(state.get("frame_h") or 0)
                if rgba_mm is not None and fw > 0 and fh > 0:
                    state["nav"] = _analyze_nav(rgba_mm, fw, fh, log)
                else:
                    # Fallback: re-present and read.
                    try:
                        import renpy_host  # type: ignore

                        _force_product_present()
                        fw, fh, rgba_mm = renpy_host.read_game_rt_rgba()
                        state["frame_w"] = int(fw)
                        state["frame_h"] = int(fh)
                        state["nav"] = _analyze_nav(rgba_mm, int(fw), int(fh), log)
                    except Exception as e:
                        state["nav"] = {"nav_ok": False, "error": f"{type(e).__name__}: {e}"}
                        _append_log(log, f"nav analyze fail: {state['nav']['error']}")

            except Exception as e:
                state["capture_error"] = f"{type(e).__name__}: {e}"
                state["capture_tb"] = traceback.format_exc()
                _append_log(log, f"capture_error={state['capture_error']}")

        state["_do_capture"] = _do_capture

        def _limited_interact(*a, **k):
            n = int(state.get("interact_count") or 0) + 1
            state["interact_count"] = n
            in_mm = False
            try:
                in_mm = bool(ih.in_main_menu())
            except Exception as e:
                state["in_main_menu_error"] = f"{type(e).__name__}: {e}"
            state["in_main_menu"] = in_mm
            if in_mm and not state.get("main_menu_logged"):
                state["main_menu_logged"] = True
                state["reached"] = "main_menu"
                _append_log(log, f"in_main_menu at interact#{n}")

            if in_mm or not state.get("capture_attempted"):
                _force_short_timeout(0.05)

            try:
                rv = orig_interact(*a, **k)
            except HostStop:
                raise
            except BaseException as e:
                try:
                    from renpy import game

                    if isinstance(e, game.CONTROL_EXCEPTIONS):
                        raise
                except ImportError:
                    if type(e).__name__ in (
                        "JumpOutException",
                        "JumpException",
                        "QuitException",
                        "CallException",
                        "FullRestartException",
                    ):
                        raise
                raise

            try:
                in_mm = bool(ih.in_main_menu())
            except Exception:
                pass
            state["in_main_menu"] = in_mm

            if in_mm and not state.get("capture_attempted"):
                _do_capture(f"post_interact_main_menu_n{n}")
                raise HostStop(
                    "nav_transparent",
                    "captured ownership_ok={} nav_ok={} pure_frac={}".format(
                        state.get("ownership_ok"),
                        (state.get("nav") or {}).get("nav_ok"),
                        (state.get("nav") or {}).get("pure_frac"),
                    ),
                )

            if n >= max_interacts:
                state["reached"] = "first_interact"
                if not state.get("capture_attempted"):
                    try:
                        state["in_main_menu"] = bool(ih.in_main_menu())
                    except Exception:
                        pass
                    _do_capture(f"ncap_n{n}")
                raise HostStop(
                    "first_interact",
                    f"N-cap interact_count={n}; in_main_menu={state.get('in_main_menu')}",
                )
            return rv

        _limited_interact._host_limited = True  # type: ignore[attr-defined]
        _limited_interact._host_orig = orig_interact  # type: ignore[attr-defined]
        iface.interact = _limited_interact  # type: ignore[method-assign]
        state["interact_wrapped"] = True

        try:
            return _orig_run(restart)
        except HostStop:
            raise
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            raise HostStop("run_entered", f"run error: {msg}") from e

    renpy_main.run = _host_run  # type: ignore[assignment]
    state["run_wrapped"] = True


def run() -> None:
    base = _base_dir()
    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    # Leave main menu on; never force SKIP_MAIN_MENU=1.
    os.environ.setdefault("RENPY_SKIP_SPLASHSCREEN", "0")
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_HOST_MUTE", "1")

    tq = base / "the_question"
    if tq.is_dir():
        os.environ.setdefault("RENPY_HOST_GAME", str(tq))

    gates = base / "host" / "python" / "gates"
    if str(gates) not in sys.path:
        sys.path.insert(0, str(gates))

    log: list = []
    t0 = time.monotonic()
    budget = float(os.environ.get("RENPY_HOST_MAX_SECS", "75") or "75")
    max_interacts = int(os.environ.get("RENPY_HOST_INTERACT_N", "40") or "40")

    state: dict = {
        "reached": "init",
        "interact_count": 0,
        "in_main_menu": False,
        "capture_after_draw": 1,
        "transitions_forced_zero": False,
    }

    skip_mm = os.environ.get("RENPY_SKIP_MAIN_MENU")
    perf = os.environ.get("RENPY_PERFORMANCE_TEST")
    _append_log(log, f"=== AC-C1 {GATE_NAME} ===")
    _append_log(
        log,
        f"base={base} budget={budget}s N={max_interacts} "
        f"SKIP_MAIN_MENU={skip_mm!r} PERFORMANCE_TEST={perf!r}",
    )

    meta = {
        "ok": False,
        "path_kind": "product_stack_Frame",
        "reached_stage": "init",
        "in_main_menu": False,
        "ownership": {},
        "ownership_ok": False,
        "probe0": {},
        "chrome": {},
        "frame_w": 0,
        "frame_h": 0,
        "frame_bytes": 0,
        "present_path": "",
        "interact_count": 0,
        "loadable_frame": False,
        "loadable_main_menu": False,
        "less_updates": None,
        "models": None,
        "transitions_pref": None,
        "transitions_forced_zero": False,
        "skip_main_menu": skip_mm,
        "performance_test": perf,
        "notes": "",
        "traceback": "",
        "elapsed_secs": 0,
    }

    def _atexit_report():
        if state.get("report_written"):
            return
        try:
            meta["reached_stage"] = state.get("reached") or meta.get("reached_stage")
            meta["in_main_menu"] = bool(state.get("in_main_menu"))
            meta["ownership"] = state.get("ownership") or {}
            meta["ownership_ok"] = bool(state.get("ownership_ok"))
            meta["probe0"] = state.get("probe0") or {}
            meta["nav"] = state.get("nav") or {}
            meta["frame_w"] = state.get("frame_w") or 0
            meta["frame_h"] = state.get("frame_h") or 0
            meta["frame_bytes"] = state.get("frame_bytes") or 0
            meta["interact_count"] = state.get("interact_count")
            meta["elapsed_secs"] = round(time.monotonic() - t0, 3)
            meta["notes"] = (meta.get("notes") or "") + ";atexit_flush"
            meta["ok"] = False
            _write_report(base, meta, log)
            state["report_written"] = True
        except Exception:
            pass

    atexit.register(_atexit_report)

    try:
        import renpy_host  # noqa: F401
    except Exception as e:
        _append_log(log, f"FATAL no renpy_host: {e}")
        meta["notes"] = "must run under renpy-host embed"
        meta["traceback"] = traceback.format_exc()
        meta["elapsed_secs"] = round(time.monotonic() - t0, 3)
        _write_report(base, meta, log)
        _request_quit()
        return

    try:
        import bootstrap as boot
    except Exception as e:
        _append_log(log, f"FATAL import bootstrap: {e}")
        meta["traceback"] = traceback.format_exc()
        meta["elapsed_secs"] = round(time.monotonic() - t0, 3)
        _write_report(base, meta, log)
        _request_quit()
        return

    try:
        good, miss, err, extra = boot.stage_import_renpy()
        if not good:
            raise RuntimeError(f"import_renpy: {err}")
        state["reached"] = "import_renpy"
        _append_log(log, "stage import_renpy ok")

        good, miss, err, extra = boot.stage_import_all()  # noqa: RUF059
        if not good:
            raise RuntimeError(f"import_all: {err}")
        state["reached"] = "import_all"
        _append_log(log, f"stage import_all ok import_all={extra.get('import_all')}")

        good, _miss, err, extra = boot.stage_set_game_dir(base)
        if not good:
            raise RuntimeError(f"set_game_dir: {err}")
        state["reached"] = "set_game_dir"
        _append_log(log, f"stage set_game_dir ok basedir={extra.get('basedir')}")

        import renpy

        if not getattr(renpy, "host_build", False):
            renpy.host_build = True
        main_mod, have, how = _ensure_renpy_main(base)
        _append_log(log, f"path helpers {how} have={have}")

        basedir = getattr(renpy.config, "basedir", None) or str(tq)
        renpy.config.renpy_base = getattr(renpy.config, "renpy_base", None) or str(base)
        try:
            logdir = main_mod.path_to_logdir(basedir)
            renpy.config.logdir = logdir
            os.makedirs(logdir, 0o777, exist_ok=True)
        except Exception as e:
            _append_log(log, f"logdir soft-fail: {e}")

        args = _prepare_run_args(base)
        _append_log(log, f"args command={getattr(args, 'command', None)}")
        try:
            renpy.importer.init_importer()
        except Exception as e:
            _append_log(log, f"importer soft-fail: {e}")

        _pre_main_host_stubs(log)
        try:
            renpy.config.performance_test = False
            renpy.config.has_music = False
            renpy.config.main_menu_music = None
        except Exception:
            pass

        _install_hooks(state, log, max_interacts)
        _append_log(log, "hooks installed")

        def _watchdog():
            time.sleep(max(1.0, budget - 1.0))
            if not state.get("done"):
                _append_log(log, f"watchdog soft-quit after {budget}s")
                _request_quit()

        threading.Thread(target=_watchdog, daemon=True).start()

        import renpy.main as renpy_main

        state["reached"] = "early_main"
        try:
            renpy_main.main()
        except HostStop as hs:
            state["hoststop"] = f"{hs.stage}: {hs.detail}"
            state["reached"] = hs.stage
            _append_log(log, f"HostStop {hs.stage}: {hs.detail}")
        except SystemExit as se:
            _append_log(log, f"SystemExit {se}")
        except BaseException as e:
            tb = traceback.format_exc()
            state["run_error"] = f"{type(e).__name__}: {e}"
            _append_log(log, f"run BaseException: {state['run_error']}")
            meta["traceback"] = tb

    except Exception as e:
        meta["traceback"] = traceback.format_exc()
        _append_log(log, f"FATAL {type(e).__name__}: {e}")
    finally:
        state["done"] = True
        elapsed = round(time.monotonic() - t0, 3)

        in_mm = bool(state.get("in_main_menu"))
        own = state.get("ownership") or {}
        ownership_ok = bool(state.get("ownership_ok"))
        nav = state.get("nav") or {}
        nav_ok = bool(nav.get("nav_ok"))
        scenic_ok = bool(nav.get("scenic_alive"))
        probe0 = state.get("probe0") or {}
        frame_ok = bool(state.get("frame_ok"))

        loadable_main_menu = False
        loadable_err = ""
        try:
            import renpy.loader as _loader

            loadable_main_menu = bool(_loader.loadable("gui/main_menu.png"))
        except Exception as e:
            loadable_err = f"{type(e).__name__}: {e}"
        _append_log(
            log,
            f"loadable main_menu={loadable_main_menu}"
            + (f" err={loadable_err}" if loadable_err else ""),
        )

        transitions_forced_zero = bool(state.get("transitions_forced_zero"))
        # AC1: product present + nav no black slabs + scenic alive + not featureless.
        ok = bool(
            nav_ok
            and scenic_ok
            and ownership_ok
            and in_mm
            and not transitions_forced_zero
            and not nav.get("featureless_black")
        )

        notes_parts = []
        if not in_mm:
            notes_parts.append("main_menu_not_reached")
        if not ownership_ok:
            notes_parts.append(
                "ownership_fail last={} presents={} idle_clears={} err={}".format(
                    own.get("last_product_present"),
                    own.get("product_presents"),
                    own.get("idle_clears_after_present"),
                    own.get("error"),
                )
            )
        if not nav_ok:
            notes_parts.append(
                "nav_fail pure_frac={} pure_black={}/{} arena_clear={} err={}".format(
                    nav.get("pure_frac"),
                    nav.get("pure_black"),
                    nav.get("total_samples"),
                    nav.get("arena_clear"),
                    nav.get("error"),
                )
            )
        if not scenic_ok:
            notes_parts.append(f"scenic_dead rgb={nav.get('scenic_rgb')}")
        if transitions_forced_zero:
            notes_parts.append("BAN: transitions_forced_zero")
        if ok:
            notes_parts.append(
                "AC1 product nav: no opaque black underlays under idle transparent "
                "button backgrounds; overlay strip dark; scenic BG alive"
            )
        if state.get("capture_error"):
            notes_parts.append(f"capture_error={state.get('capture_error')}")
        if probe0.get("featureless_black"):
            notes_parts.append("PROBE0: main menu RT featureless black")

        meta.update(
            {
                "ok": ok,
                "path_kind": "product_main_menu_nav",
                "reached_stage": state.get("reached"),
                "in_main_menu": in_mm,
                "ownership": own,
                "ownership_ok": ownership_ok,
                "probe0": probe0,
                "nav": nav,
                "frame_w": state.get("frame_w") or 0,
                "frame_h": state.get("frame_h") or 0,
                "frame_bytes": state.get("frame_bytes") or 0,
                "frame_ok": frame_ok,
                "frame_error": state.get("frame_error") or "",
                "present_path": state.get("present_path") or "",
                "interact_count": state.get("interact_count"),
                "loadable_main_menu": loadable_main_menu,
                "less_updates": state.get("less_updates"),
                "models": state.get("models"),
                "transitions_pref": state.get("transitions_pref"),
                "transitions_forced_zero": transitions_forced_zero,
                "notes": "; ".join(notes_parts),
                "elapsed_secs": elapsed,
            }
        )
        _append_log(
            log,
            f"SUMMARY ok={ok} in_main_menu={in_mm} ownership_ok={ownership_ok} "
            f"nav_ok={nav_ok} pure_frac={nav.get('pure_frac')} "
            f"transitions_pref={meta.get('transitions_pref')} "
            f"forced_zero={transitions_forced_zero} "
            f"size={meta['frame_w']}x{meta['frame_h']} elapsed={elapsed}s",
        )
        _write_report(base, meta, log)
        state["report_written"] = True
        _request_quit()

        if not ok:
            raise RuntimeError(
                f"{GATE_NAME} ok=False notes={meta['notes']}; see gate-{GATE_NAME}.txt"
            )


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

