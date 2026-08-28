"""
V1 gate: capture a non-blank main-menu frame under product present ownership.

Gate name: tq_main_menu_frame  (RENPY_HOST_GATE=tq_main_menu_frame)

Contract (consensus-tq-gui-residual V1):
  - NEVER setdefault RENPY_SKIP_MAIN_MENU=1
  - setdefault RENPY_PERFORMANCE_TEST=0
  - Bootstrap stages a–c, then renpy.main.main with HostStop after capture
  - Interact until interact_helpers.in_main_menu()
  - Capture via try_read_frame / read_game_rt_rgba with capture-cycle ownership
  - Non-blank AND-chain (anti-uniform mandatory; dual clear RGB reject)
  - Present-ownership hard assert: last_product_present, product_presents>=1,
    idle_clears_after_present==0
  - Fail-closed loadable SSOT: renpy.loader.loadable("gui/main_menu.png")
    must be True before ok=True; loadable_main_menu= written to artifact
  - Artifact host/target/gate-tq-main-menu-frame.txt with gate= + ok=

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



REQUIRED_HELPERS = (
    "path_to_common",
    "path_to_gamedir",
    "path_to_saves",
    "predefined_searchpath",
    "path_to_logdir",
)

GATE_NAME = "tq_main_menu_frame"


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
    print(f"[tq_main_menu_frame] {msg}", flush=True)


def _report_path(base: Path) -> Path:
    return base / "host" / "target" / "gate-tq-main-menu-frame.txt"


def _write_report(base: Path, meta: dict, log: list) -> Path:
    out = _report_path(base)
    out.parent.mkdir(parents=True, exist_ok=True)
    own = meta.get("ownership") or {}
    nb = meta.get("nonblank") or {}
    lines = [
        f"gate={GATE_NAME}",
        f"ok={meta.get('ok')}",
        f"reached_stage={meta.get('reached_stage')}",
        f"in_main_menu={meta.get('in_main_menu')}",
        f"last_product_present={own.get('last_product_present')}",
        f"product_presents={own.get('product_presents')}",
        f"idle_clears_after_present={own.get('idle_clears_after_present')}",
        f"ownership_ok={meta.get('ownership_ok')}",
        f"frame_w={meta.get('frame_w')}",
        f"frame_h={meta.get('frame_h')}",
        f"frame_bytes={meta.get('frame_bytes')}",
        f"nonblank_ok={meta.get('nonblank_ok')}",
        f"nonblank_reject={nb.get('reject')}",
        f"nonblank_reasons={nb.get('reasons')}",
        f"nonblank_variance={nb.get('variance')}",
        f"nonblank_range={nb.get('range')}",
        f"nonblank_mean_rgb={nb.get('mean_rgb')}",
        f"nonblank_grid_good={nb.get('grid_good')}",
        f"present_path={meta.get('present_path')}",
        f"interact_count={meta.get('interact_count')}",
        f"loadable_main_menu={meta.get('loadable_main_menu')}",
        f"SKIP_MAIN_MENU={meta.get('skip_main_menu')!r}",
        f"PERFORMANCE_TEST={meta.get('performance_test')!r}",
        f"notes={meta.get('notes')}",
        f"elapsed_secs={meta.get('elapsed_secs')}",
    ]
    if meta.get("traceback"):
        lines.append(f"traceback={meta['traceback'][:2000]!r}")
    if meta.get("frame_error"):
        lines.append(f"frame_error={meta.get('frame_error')}")
    if meta.get("present_error"):
        lines.append(f"present_error={meta.get('present_error')}")
    # Append last log lines as soft evidence (not required for parse).
    if log:
        lines.append("log_tail=" + " | ".join(log[-12:]))
    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text, flush=True)
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
            """
            Product draw path: after surftree is rendered+presented, capture once
            while still on main menu. Main-menu interact often never returns under
            host pump (timeout alone insufficient), so capture-on-draw is required.
            """
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

            # Capture on first product main-menu draw (surftree just presented).
            # need=1: waiting for 2+ draws hangs when interact_core never returns.
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
                        "main_menu_frame",
                        "captured ownership_ok={} nonblank_ok={} present_path={}".format(
                            state.get("ownership_ok"),
                            state.get("nonblank_ok"),
                            state.get("present_path"),
                        ),
                    )
                # Capturer not wired yet — fall through; interact wrapper will capture.

    core.Interface = HostInterface  # type: ignore[misc,assignment]
    renpy.display.core.Interface = HostInterface  # type: ignore[attr-defined]

    _orig_run = renpy_main.run

    def _host_run(restart):
        state["reached"] = "run_entered"
        state["run_restart"] = repr(restart)
        _append_log(log, f"run_entered restart={restart!r}")

        try:
            prefs = getattr(renpy.game, "preferences", None)
            if prefs is not None and hasattr(prefs, "transitions"):
                prefs.transitions = 0
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
            _append_log(log, "prefs: music off, transitions 0, performance_test off")
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
            """Best-effort unblock of main-menu interact_core under host pump."""
            try:
                iface.timeout(float(secs))
            except Exception:
                pass
            try:
                renpy.exports.timeout(float(secs))
            except Exception:
                pass

        def _force_product_present() -> dict:
            """
            Prefer product Interface.surftree / draw_screen over surrogate solid.

            V1 must sample real main-menu content when possible; surrogate solid
            is only a last resort and will fail non-blank anti-uniform by design.

            Pre-interact capture: surftree / root_widget may be absent until the
            first interact_core draw — rebuild scene root via compute_scene.
            """
            out: dict = {"presented": False, "path": "", "error": "", "draw_source": ""}
            try:
                import renpy

                iface = getattr(getattr(renpy, "game", None), "interface", None) or getattr(
                    getattr(renpy, "display", None), "interface", None
                )
                # Pre-interact capture: start()/set_mode must run so display.draw
                # is product WgpuDraw (not a local surrogate instance).
                if iface is not None:
                    try:
                        ok_start, start_detail = ih._ensure_interface_started(iface)
                        out["interface_start"] = start_detail
                        if not ok_start:
                            out["error"] = f"start:{start_detail}"
                    except Exception as e:
                        out["error"] = f"start:{type(e).__name__}:{e}"
                draw = getattr(getattr(renpy, "display", None), "draw", None)
                out["draw_source"] = type(draw).__name__ if draw is not None else "None"
                if draw is not None and iface is not None:
                    surftree = getattr(iface, "surftree", None)
                    if surftree is not None:
                        try:
                            draw.draw_screen(surftree, flip=True)
                            out["presented"] = True
                            out["path"] = f"product_surftree:{type(surftree).__name__}"
                            return out
                        except Exception as e:
                            out["error"] = f"surftree:{type(e).__name__}:{e}"

                    # Rebuild root from scene_lists when surftree is still absent
                    # (pre-interact capture path).
                    root = None
                    for attr in ("root_widget", "displayable", "root"):
                        root = getattr(iface, attr, None)
                        if root is not None:
                            break
                    if root is None:
                        try:
                            root = ih._rebuild_product_root(iface)
                        except Exception as e:
                            out["error"] = (out.get("error") or "") + f";rebuild:{type(e).__name__}:{e}"

                    if root is not None and hasattr(iface, "draw_screen"):
                        try:
                            iface.draw_screen(root, False, True)
                            out["presented"] = True
                            out["path"] = f"product_iface:{type(getattr(iface, 'surftree', root)).__name__}"
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

                    # Explicit render_screen rebuild when iface.draw_screen path fails.
                    if root is not None:
                        try:
                            w = int(getattr(renpy.config, "screen_width", 1280) or 1280)
                            h = int(getattr(renpy.config, "screen_height", 720) or 720)
                            st = renpy.display.render.render_screen(root, w, h)
                            draw.draw_screen(st, flip=True)
                            try:
                                iface.surftree = st
                            except Exception:
                                pass
                            out["presented"] = True
                            out["path"] = f"product_render_screen:{type(st).__name__}"
                            return out
                        except Exception as e:
                            out["error"] = (out.get("error") or "") + f";render:{type(e).__name__}:{e}"

                # Fall back to shared ensure (may use surrogate).
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
                # Prefer product present before ownership capture cycle.
                try:
                    ih.pump_ms(16)
                    pref = _force_product_present()
                    state["product_present_attempt"] = pref
                    _append_log(log, f"product_present_attempt={pref}")
                except Exception as e:
                    state["product_present_attempt_error"] = f"{type(e).__name__}: {e}"

                cap = ih.capture_with_present_ownership(force_present=True)
                # If capture used surrogate and product path reported a surftree, try
                # one more product-only present + readback without surrogate rewrite.
                if (cap.get("present_path") or "").startswith("surrogate") or (
                    (cap.get("nonblank") or {}).get("reject") == "anti_uniform"
                ):
                    try:
                        pref2 = _force_product_present()
                        state["product_present_retry"] = pref2
                        _append_log(log, f"product_present_retry={pref2}")
                        # Manual ownership cycle without ensure_frame_present surrogate.
                        import renpy_host  # type: ignore

                        renpy_host.reset_present_stats()
                        pref3 = _force_product_present()
                        w, hgt, rgba = renpy_host.read_game_rt_rgba()
                        own = ih.read_present_ownership()
                        nb = ih.analyze_frame_nonblank(int(w), int(hgt), rgba)
                        cap = {
                            "frame_ok": bool(w and hgt and rgba is not None and len(rgba) > 0),
                            "frame_w": int(w),
                            "frame_h": int(hgt),
                            "frame_bytes": len(rgba) if rgba is not None else 0,
                            "frame_error": "",
                            "present_path": pref3.get("path") or pref2.get("path") or "",
                            "present_error": pref3.get("error") or "",
                            "rgba": rgba,
                            "ownership": own,
                            "ownership_ok": (
                                bool(own.get("last_product_present"))
                                and int(own.get("product_presents") or 0) >= 1
                                and int(own.get("idle_clears_after_present") or 0) == 0
                                and not own.get("error")
                            ),
                            "nonblank": nb,
                            "nonblank_ok": bool(nb.get("nonblank_ok")),
                            "ok": False,
                        }
                        cap["ok"] = bool(
                            cap["ownership_ok"] and cap["nonblank_ok"] and cap["frame_ok"]
                        )
                        _append_log(
                            log,
                            "manual product readback size={}x{} nonblank={} reject={} path={}".format(
                                cap["frame_w"],
                                cap["frame_h"],
                                cap["nonblank_ok"],
                                (cap.get("nonblank") or {}).get("reject"),
                                cap.get("present_path"),
                            ),
                        )
                    except Exception as e:
                        state["manual_product_capture_error"] = f"{type(e).__name__}: {e}"
                        _append_log(log, f"manual product capture error: {state['manual_product_capture_error']}")

                state["capture"] = {k: v for k, v in cap.items() if k != "rgba"}
                state["capture_rgba"] = cap.get("rgba")
                state["ownership"] = cap.get("ownership") or {}
                state["ownership_ok"] = bool(cap.get("ownership_ok"))
                state["nonblank"] = cap.get("nonblank") or {}
                state["nonblank_ok"] = bool(cap.get("nonblank_ok"))
                state["frame_w"] = cap.get("frame_w")
                state["frame_h"] = cap.get("frame_h")
                state["frame_bytes"] = cap.get("frame_bytes")
                state["frame_ok"] = bool(cap.get("frame_ok"))
                state["present_path"] = cap.get("present_path")
                state["present_error"] = cap.get("present_error")
                state["frame_error"] = cap.get("frame_error")
                _append_log(
                    log,
                    "capture ownership_ok={} nonblank_ok={} size={}x{} "
                    "reject={} presents={} idle_clears={} last={} path={}".format(
                        state.get("ownership_ok"),
                        state.get("nonblank_ok"),
                        state.get("frame_w"),
                        state.get("frame_h"),
                        (state.get("nonblank") or {}).get("reject"),
                        (state.get("ownership") or {}).get("product_presents"),
                        (state.get("ownership") or {}).get("idle_clears_after_present"),
                        (state.get("ownership") or {}).get("last_product_present"),
                        state.get("present_path"),
                    ),
                )
                # Optional golden bootstrap only when nonblank passes (avoid
                # writing surrogate/clear baselines as golden).
                try:
                    if (
                        state.get("frame_ok")
                        and state.get("nonblank_ok")
                        and state.get("capture_rgba") is not None
                    ):
                        import golden_mae as gm

                        gm.compare_or_bootstrap(
                            GATE_NAME,
                            int(state.get("frame_w") or 0),
                            int(state.get("frame_h") or 0),
                            state.get("capture_rgba"),
                        )
                except Exception as ge:
                    state["golden_error"] = f"{type(ge).__name__}: {ge}"
                    _append_log(log, f"golden soft-fail: {state['golden_error']}")
            except Exception as e:
                state["capture_error"] = f"{type(e).__name__}: {e}"
                state["capture_tb"] = traceback.format_exc()
                _append_log(log, f"capture_error={state['capture_error']}")

        # Expose capturer to HostInterface.draw_screen (defined above; called
        # from product interact_core after surftree is presented).
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

            # Enter product interact so Interface.draw_screen runs with a real
            # main-menu surftree. Capture happens inside HostInterface.draw_screen
            # (HostStop), NOT pre-interact (pre-interact → surrogate solid only).
            # Force short timeout as best-effort unblock if draw_screen path misses.
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

            # Fallback if draw_screen path did not fire (e.g. no draw flag).
            if in_mm and not state.get("capture_attempted"):
                _do_capture(f"post_interact_main_menu_n{n}")
                raise HostStop(
                    "main_menu_frame",
                    "captured ownership_ok={} nonblank_ok={} present_path={}".format(
                        state.get("ownership_ok"),
                        state.get("nonblank_ok"),
                        state.get("present_path"),
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
    # V1 contract: NEVER setdefault RENPY_SKIP_MAIN_MENU=1
    # Leave unset so stock 00start.rpy reaches the real main menu.
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
        # Wait ~3 short main-menu interacts so Interface.surftree is built
        # by draw_screen before capture (pre-interact = surrogate only).
        "capture_after_interact": 1,
        "capture_after_draw": 1,
    }

    skip_mm = os.environ.get("RENPY_SKIP_MAIN_MENU")
    perf = os.environ.get("RENPY_PERFORMANCE_TEST")
    _append_log(log, f"=== V1 {GATE_NAME} ===")
    _append_log(
        log,
        f"base={base} budget={budget}s N={max_interacts} "
        f"SKIP_MAIN_MENU={skip_mm!r} PERFORMANCE_TEST={perf!r}",
    )
    if skip_mm in ("1", "true", "yes", "on"):
        _append_log(
            log,
            "WARN: RENPY_SKIP_MAIN_MENU is set by operator; V1 prefers unset/0",
        )

    meta = {
        "ok": False,
        "reached_stage": "init",
        "in_main_menu": False,
        "ownership": {},
        "ownership_ok": False,
        "nonblank": {},
        "nonblank_ok": False,
        "frame_w": 0,
        "frame_h": 0,
        "frame_bytes": 0,
        "present_path": "",
        "interact_count": 0,
        "loadable_main_menu": False,
        "skip_main_menu": skip_mm,
        "performance_test": perf,
        "notes": "",
        "traceback": "",
        "elapsed_secs": 0,
    }

    # Fail-closed artifact even if the process is hard-killed mid-interact.
    def _atexit_report():
        if state.get("report_written"):
            return
        try:
            meta["reached_stage"] = state.get("reached") or meta.get("reached_stage")
            meta["in_main_menu"] = bool(state.get("in_main_menu"))
            meta["ownership"] = state.get("ownership") or meta.get("ownership") or {}
            meta["ownership_ok"] = bool(state.get("ownership_ok"))
            meta["nonblank"] = state.get("nonblank") or meta.get("nonblank") or {}
            meta["nonblank_ok"] = bool(state.get("nonblank_ok"))
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
        nb = state.get("nonblank") or {}
        ownership_ok = bool(state.get("ownership_ok"))
        nonblank_ok = bool(state.get("nonblank_ok"))
        frame_ok = bool(state.get("frame_ok"))

        # V1 hard SSOT: gui/main_menu.png must be loadable before ok=True.
        # Prefer renpy.loader after bootstrap (same phase as main-menu capture).
        loadable_main_menu = False
        loadable_err = ""
        try:
            import renpy.loader as _loader

            loadable_main_menu = bool(_loader.loadable("gui/main_menu.png"))
        except Exception as e:
            loadable_err = f"{type(e).__name__}: {e}"
            loadable_main_menu = False
        state["loadable_main_menu"] = loadable_main_menu
        if loadable_err:
            state["loadable_main_menu_error"] = loadable_err
        _append_log(
            log,
            f"loadable_main_menu={loadable_main_menu}"
            + (f" err={loadable_err}" if loadable_err else ""),
        )

        # Fail-closed ok: main menu + ownership + nonblank + loadable SSOT.
        ok = bool(
            in_mm
            and ownership_ok
            and nonblank_ok
            and frame_ok
            and loadable_main_menu
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
        if not nonblank_ok:
            notes_parts.append(
                "nonblank_fail reject={} reasons={}".format(nb.get("reject"), nb.get("reasons"))
            )
        if not frame_ok and not notes_parts:
            notes_parts.append("frame_not_ok")
        if not loadable_main_menu:
            notes_parts.append(
                "loadable_main_menu_fail path=gui/main_menu.png"
                + (f" err={loadable_err}" if loadable_err else "")
            )
        if ok:
            notes_parts.append(
                "main_menu frame nonblank under product present ownership; "
                "loadable gui/main_menu.png"
            )
        if state.get("capture_error"):
            notes_parts.append(f"capture_error={state.get('capture_error')}")

        # Slice 2 hint: ownership green but content empty → mesh residual.
        if ownership_ok and in_mm and not nonblank_ok:
            notes_parts.append(
                "SLICE2_HINT: present ownership OK but main-menu content empty/uniform; "
                "candidate H2 mesh+children bake residual"
            )

        meta.update(
            {
                "ok": ok,
                "reached_stage": state.get("reached"),
                "in_main_menu": in_mm,
                "ownership": own,
                "ownership_ok": ownership_ok,
                "nonblank": nb,
                "nonblank_ok": nonblank_ok,
                "frame_w": state.get("frame_w") or 0,
                "frame_h": state.get("frame_h") or 0,
                "frame_bytes": state.get("frame_bytes") or 0,
                "frame_ok": frame_ok,
                "frame_error": state.get("frame_error") or "",
                "present_path": state.get("present_path") or "",
                "present_error": state.get("present_error") or "",
                "interact_count": state.get("interact_count"),
                "loadable_main_menu": loadable_main_menu,
                "notes": "; ".join(notes_parts),
                "elapsed_secs": elapsed,
            }
        )
        _append_log(
            log,
            f"SUMMARY ok={ok} in_main_menu={in_mm} ownership_ok={ownership_ok} "
            f"nonblank_ok={nonblank_ok} loadable_main_menu={loadable_main_menu} "
            f"size={meta['frame_w']}x{meta['frame_h']} elapsed={elapsed}s",
        )
        _write_report(base, meta, log)
        state["report_written"] = True
        _request_quit()

        if not ok:
            # Raise so renpy-host logs "Python gate failed" (artifact already on disk).
            raise RuntimeError(
                f"{GATE_NAME} ok=False notes={meta['notes']}; see gate-tq-main-menu-frame.txt"
            )


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

