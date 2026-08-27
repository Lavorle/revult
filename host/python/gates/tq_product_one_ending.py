"""
F3b gate: product path from real main menu → New Game → one ending.

Gate name: tq_product_one_ending  (RENPY_HOST_GATE=tq_product_one_ending)

Contract:
  - Main menu NOT skipped (no setdefault SKIP_MAIN_MENU=1)
  - PERFORMANCE_TEST=0
  - From main menu: New Game → complete ONE ending without uncaught exception
  - Sample first stable non-menu frame with V1 non-blank algorithm
  - HostStop after success (unlike bare product continuous)
  - Reuse path techniques from tq_bad_ending (keyboard/mouse, forced choice)
    but START from real main menu

Artifact: host/target/gate-tq-product-one-ending.txt with gate= + ok=

Note: do NOT import main.py (auto-runs). Do not import tq_bad_ending (auto-runs).
"""

import os
import sys
import threading
import time
import traceback
import types
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore



K_RETURN = 13
K_SPACE = 32
K_DOWN = 1073741905  # SDLK_DOWN

REQUIRED_HELPERS = (
    "path_to_common",
    "path_to_gamedir",
    "path_to_saves",
    "predefined_searchpath",
    "path_to_logdir",
)

GATE_NAME = "tq_product_one_ending"


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
    print(f"[tq_product_one_ending] {msg}", flush=True)


def _report_path(base: Path) -> Path:
    return base / "host" / "target" / "gate-tq-product-one-ending.txt"


def _write_report(base: Path, meta: dict, log: list) -> Path:
    out = _report_path(base)
    out.parent.mkdir(parents=True, exist_ok=True)
    own = meta.get("ownership") or {}
    nb = meta.get("nonblank") or {}
    lines = [
        f"gate={GATE_NAME}",
        f"ok={meta.get('ok')}",
        f"reached_stage={meta.get('reached_stage')}",
        f"ending={meta.get('ending')}",
        f"ending_kind={meta.get('ending_kind')}",
        f"main_menu_started={meta.get('main_menu_started')}",
        f"in_game_frame_ok={meta.get('in_game_frame_ok')}",
        f"nonblank_ok={meta.get('nonblank_ok')}",
        f"nonblank_reject={nb.get('reject')}",
        f"nonblank_reasons={nb.get('reasons')}",
        f"frame_w={meta.get('frame_w')}",
        f"frame_h={meta.get('frame_h')}",
        f"last_product_present={own.get('last_product_present')}",
        f"product_presents={own.get('product_presents')}",
        f"idle_clears_after_present={own.get('idle_clears_after_present')}",
        f"ownership_ok={meta.get('ownership_ok')}",
        f"uncaught_exception={meta.get('uncaught_exception')}",
        f"labels={meta.get('labels')}",
        f"interact_count={meta.get('interact_count')}",
        f"path={meta.get('path')}",
        f"SKIP_MAIN_MENU={meta.get('skip_main_menu')!r}",
        f"PERFORMANCE_TEST={meta.get('performance_test')!r}",
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


def _is_choice_screen() -> bool:
    try:
        import renpy

        return renpy.exports.get_screen("choice") is not None
    except Exception:
        return False


def _current_label():
    try:
        from renpy import game

        ctx = game.context()
        return getattr(ctx, "current", None)
    except Exception:
        return None


def _probe_say_text() -> str:
    chunks = []
    try:
        import renpy

        hist = getattr(renpy.store, "_history_list", None)
        if hist:
            for h in list(hist)[-8:]:
                what = getattr(h, "what", None)
                if what is None and isinstance(h, dict):
                    what = h.get("what")
                if what:
                    chunks.append(str(what))
        last = getattr(renpy.store, "_last_raw_what", None)
        if last:
            chunks.append(str(last))
    except Exception:
        pass
    return " || ".join(chunks)


def _looks_like_ending(label, say_blob: str, state: dict) -> tuple[bool, str]:
    """Return (is_ending, kind) for Good or Bad ending."""
    if state.get("hit_good_ending") or state.get("hit_bad_ending"):
        kind = "good" if state.get("hit_good_ending") else "bad"
        return True, kind
    s = f"{say_blob}".lower()
    if "good ending" in s:
        return True, "good"
    if "bad ending" in s:
        return True, "bad"
    # Dialogue markers for later (bad) branch.
    bad_markers = (
        "i can't get up the nerve",
        "i'm an indecisive person",
        "never being able to ask her",
        "never know the answer to my question",
    )
    for m in bad_markers:
        if m in s:
            return True, "bad"
    # Good ending markers from rightaway branch.
    good_markers = (
        "i love you",
        "good ending",
    )
    for m in good_markers:
        if m in s and "bad" not in s:
            # Be conservative: only trust explicit good ending or strong love line
            # after we left main menu and saw a choice.
            if state.get("menu_seen") or state.get("main_menu_started"):
                if m == "good ending" or state.get("hit_later_dialogue") is not True:
                    if "good ending" in s:
                        return True, "good"
    if label is not None:
        ls = str(label).lower()
        if "later" in ls and state.get("menu_seen"):
            return True, "bad"
    return False, ""


def _force_second_choice_random(state: dict, log: list) -> None:
    """Force menu index 1 → later → Bad Ending (same as F3a)."""
    import renpy

    try:
        orig = renpy.exports.random
    except Exception as e:
        _append_log(log, f"force_second_choice: no renpy.exports.random ({e})")
        return

    class _SecondChoiceRandom:
        def choice(self, seq):
            try:
                n = len(seq)
            except Exception:
                n = 0
            if n >= 2:
                state["forced_second_picks"] = int(state.get("forced_second_picks") or 0) + 1
                pick = seq[1]
                _append_log(
                    log,
                    f"auto_choice forced index=1 of {n} pick={pick!r} "
                    f"count={state['forced_second_picks']}",
                )
                return pick
            if n == 1:
                return seq[0]
            return orig.choice(seq)

        def __getattr__(self, name):
            return getattr(orig, name)

    renpy.exports.random = _SecondChoiceRandom()  # type: ignore[assignment]
    try:
        renpy.store.random = renpy.exports.random  # type: ignore[attr-defined]
    except Exception:
        pass
    state["forced_second_choice"] = True
    _append_log(log, "installed SecondChoiceRandom on renpy.exports.random")


def _path_k_select_second(state: dict, log: list) -> dict:
    out = {"queued": 0, "injected": 0, "error": ""}
    try:
        import interact_helpers as ih

        for name in ("focus_down",):
            r = ih.queue_renpy_event(name, up=False)
            if r.get("queued"):
                out["queued"] += 1
            r = ih.queue_renpy_event(name, up=True)
            if r.get("queued"):
                out["queued"] += 1
        r = ih.inject_key_pulse(K_DOWN, hold_ms=40)
        if r.get("injected"):
            out["injected"] += 1
        for name in ("button_select",):
            r = ih.queue_renpy_event(name, up=False)
            if r.get("queued"):
                out["queued"] += 1
            r = ih.queue_renpy_event(name, up=True)
            if r.get("queued"):
                out["queued"] += 1
        r = ih.inject_key_pulse(K_RETURN, hold_ms=40)
        if r.get("injected"):
            out["injected"] += 1
        state["menu_path_k_attempts"] = int(state.get("menu_path_k_attempts") or 0) + 1
        state["last_menu_path"] = "K"
        _append_log(
            log,
            f"PathK menu select attempt#{state['menu_path_k_attempts']} "
            f"queued={out['queued']} injected={out['injected']}",
        )
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        _append_log(log, f"PathK error: {out['error']}")
    return out


def _path_m_click_second(state: dict, log: list) -> dict:
    out = {"injected": False, "error": ""}
    coords = [(640, 420), (640, 450), (640, 390), (700, 430), (580, 430), (640, 480)]
    try:
        import interact_helpers as ih

        idx = int(state.get("menu_path_m_attempts") or 0) % len(coords)
        x, y = coords[idx]
        r = ih.inject_mouse_click(x, y, button=1, hold_ms=40)
        out.update(r)
        state["menu_path_m_attempts"] = int(state.get("menu_path_m_attempts") or 0) + 1
        state["last_menu_path"] = "M"
        _append_log(
            log,
            f"PathM click attempt#{state['menu_path_m_attempts']} at ({x},{y}) "
            f"injected={r.get('injected')}",
        )
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        _append_log(log, f"PathM error: {out['error']}")
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
                    prefs.afm_enable = True
                if hasattr(prefs, "afm_time"):
                    prefs.afm_time = 0.05
                if hasattr(prefs, "using_afm_enable"):
                    prefs.using_afm_enable = True
            _force_second_choice_random(state, log)
            renpy.config.auto_choice_delay = 0.05
            state["auto_choice_delay"] = 0.05
            try:
                renpy.config.skip_delay = 1
            except Exception:
                pass
            _append_log(log, "prefs: music off, afm on, auto_choice_delay=0.05 + second-pick")
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
        state.setdefault("labels_seen", [])
        state.setdefault("say_seen", [])

        def _limited_interact(*a, **k):
            n = int(state.get("interact_count") or 0) + 1
            state["interact_count"] = n
            on_choice = _is_choice_screen()
            label_before = _current_label()
            in_mm = False
            try:
                in_mm = bool(ih.in_main_menu())
            except Exception:
                in_mm = False
            state["in_main_menu"] = in_mm

            if on_choice:
                state["menu_seen"] = True
                state["menu_label"] = label_before
                _append_log(log, f"interact#{n} CHOICE screen label={label_before!r}")

            try:
                # Unblock main-menu interact so Start/click can be retried each frame.
                if in_mm and not state.get("main_menu_started"):
                    try:
                        iface.timeout(0.08)
                    except Exception:
                        pass
                    try:
                        renpy.exports.timeout(0.08)
                    except Exception:
                        pass

                # F3b: leave real main menu via Start / JumpOut (not SKIP_MAIN_MENU).
                if in_mm and not state.get("main_menu_started"):
                    try:
                        # Prefer Start() action; also try clicking Start button region.
                        st = ih.activate_main_menu_start("start")
                        state["main_menu_start"] = st
                        if st.get("started"):
                            state["main_menu_started"] = True
                            _append_log(log, f"main_menu Start via activate: {st}")
                    except BaseException as je:
                        from renpy import game

                        if isinstance(je, game.CONTROL_EXCEPTIONS):
                            state["main_menu_started"] = True
                            _append_log(log, f"main_menu Start CONTROL {type(je).__name__}")
                            raise
                        # Fall through to click/keyboard.
                        state["main_menu_start_error"] = f"{type(je).__name__}: {je}"

                    if not state.get("main_menu_started"):
                        # Click typical Start button area (left nav, upper).
                        for x, y in ((200, 300), (180, 280), (220, 320), (150, 350)):
                            r = ih.inject_mouse_click(x, y, button=1, hold_ms=40)
                            if r.get("injected"):
                                state["main_menu_click"] = (x, y)
                                _append_log(log, f"main_menu click Start region ({x},{y})")
                                break
                        # Keyboard: focus + select.
                        ih.queue_renpy_event("button_select", up=False)
                        ih.queue_renpy_event("button_select", up=True)
                        ih.inject_key_pulse(K_RETURN, hold_ms=40)
                        state["main_menu_key_attempt"] = int(
                            state.get("main_menu_key_attempt") or 0
                        ) + 1

                if (not in_mm) and state.get("main_menu_started") and on_choice:
                    k_att = int(state.get("menu_path_k_attempts") or 0)
                    if k_att < 8:
                        _path_k_select_second(state, log)
                    else:
                        _path_m_click_second(state, log)
                elif not in_mm and state.get("main_menu_started"):
                    pulse = ih.advance_dialogue_pulse()
                    state["injects_ok"] = int(state.get("injects_ok") or 0) + int(
                        pulse.get("queued") or 0
                    ) + int(pulse.get("injected") or 0)
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
                    ):
                        raise
                state["inject_error"] = f"{type(e).__name__}: {e}"
                _append_log(log, f"inject_error={state['inject_error']}")

            try:
                rv = orig_interact(*a, **k)
            except HostStop:
                raise
            except BaseException as e:
                msg = f"{type(e).__name__}: {e}"
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
                # Uncaught product exception — record and HostStop fail-closed.
                state["uncaught_exception"] = msg
                state["uncaught_tb"] = traceback.format_exc()
                _append_log(log, f"UNCAUGHT {msg}")
                raise HostStop("uncaught", msg) from e

            label_after = _current_label()
            say_blob = _probe_say_text()
            if label_after is not None:
                ls = state["labels_seen"]
                if not ls or ls[-1] != label_after:
                    ls.append(label_after)

            # Sample first stable non-menu frame after New Game.
            if (
                state.get("main_menu_started")
                and not in_mm
                and not state.get("in_game_frame_sampled")
                and not on_choice
            ):
                stable = int(state.get("in_game_stable") or 0) + 1
                state["in_game_stable"] = stable
                if stable >= 2:
                    state["in_game_frame_sampled"] = True
                    try:
                        cap = ih.capture_with_present_ownership(force_present=True)
                        state["in_game_capture"] = {
                            k: v for k, v in cap.items() if k != "rgba"
                        }
                        state["ownership"] = cap.get("ownership") or {}
                        state["ownership_ok"] = bool(cap.get("ownership_ok"))
                        state["nonblank"] = cap.get("nonblank") or {}
                        state["nonblank_ok"] = bool(cap.get("nonblank_ok"))
                        state["frame_w"] = cap.get("frame_w")
                        state["frame_h"] = cap.get("frame_h")
                        state["frame_bytes"] = cap.get("frame_bytes")
                        state["in_game_frame_ok"] = bool(
                            cap.get("frame_ok") and cap.get("nonblank_ok")
                        )
                        state["present_path"] = cap.get("present_path")
                        _append_log(
                            log,
                            "in_game_frame ownership_ok={} nonblank_ok={} size={}x{} reject={}".format(
                                state.get("ownership_ok"),
                                state.get("nonblank_ok"),
                                state.get("frame_w"),
                                state.get("frame_h"),
                                (state.get("nonblank") or {}).get("reject"),
                            ),
                        )
                    except Exception as e:
                        state["in_game_frame_error"] = f"{type(e).__name__}: {e}"
                        _append_log(log, f"in_game_frame_error={state['in_game_frame_error']}")
            elif in_mm:
                state["in_game_stable"] = 0

            # Ending markers.
            if say_blob:
                if not state["say_seen"] or state["say_seen"][-1] != say_blob[:300]:
                    state["say_seen"].append(say_blob[:300])
                low = say_blob.lower()
                if "good ending" in low:
                    state["hit_good_ending"] = True
                    _append_log(log, f"HIT good ending text interact#{n}")
                if "bad ending" in low:
                    state["hit_bad_ending"] = True
                    _append_log(log, f"HIT bad ending text interact#{n}")
                for mk in (
                    "I can't get up the nerve",
                    "I'm an indecisive person",
                    "never being able to ask her",
                    "never know the answer to my question",
                ):
                    if mk.lower() in low:
                        state["hit_later_dialogue"] = True
                        break

            is_end, kind = _looks_like_ending(label_after, say_blob, state)
            if is_end:
                state["ending"] = True
                state["ending_kind"] = kind
                state["reached"] = f"ending_{kind}"
                post = int(state.get("post_ending_count") or 0) + 1
                state["post_ending_count"] = post
                # Prefer explicit ending text; accept later dialogue after a few frames.
                if (
                    state.get("hit_good_ending")
                    or state.get("hit_bad_ending")
                    or (state.get("hit_later_dialogue") and post >= 5)
                    or post >= 8
                ):
                    path = state.get("last_menu_path") or "auto_choice_index1"
                    _append_log(
                        log,
                        "ENDING_REACHED kind={} path={} label={!r} interact={} "
                        "in_game_frame_ok={}".format(
                            kind,
                            path,
                            label_after,
                            n,
                            state.get("in_game_frame_ok"),
                        ),
                    )
                    raise HostStop(
                        "ending",
                        f"kind={kind} path={path} interact={n}",
                    )

            if n % 10 == 0 or on_choice or in_mm:
                _append_log(
                    log,
                    f"interact#{n} mm={in_mm} started={bool(state.get('main_menu_started'))} "
                    f"choice={on_choice} label={label_after!r} ending={bool(state.get('ending'))}",
                )

            if n >= max_interacts:
                state["reached"] = "first_interact"
                # Best-effort sample if we never got a stable non-menu frame.
                if state.get("main_menu_started") and not state.get("in_game_frame_sampled"):
                    try:
                        cap = ih.capture_with_present_ownership(force_present=True)
                        state["ownership"] = cap.get("ownership") or {}
                        state["ownership_ok"] = bool(cap.get("ownership_ok"))
                        state["nonblank"] = cap.get("nonblank") or {}
                        state["nonblank_ok"] = bool(cap.get("nonblank_ok"))
                        state["frame_w"] = cap.get("frame_w")
                        state["frame_h"] = cap.get("frame_h")
                        state["in_game_frame_ok"] = bool(
                            cap.get("frame_ok") and cap.get("nonblank_ok")
                        )
                        state["in_game_frame_sampled"] = True
                    except Exception as e:
                        state["in_game_frame_error"] = f"{type(e).__name__}: {e}"
                raise HostStop(
                    "first_interact",
                    f"N-cap interact_count={n}; ending={state.get('ending')} "
                    f"started={state.get('main_menu_started')}",
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
            state["uncaught_exception"] = state.get("uncaught_exception") or msg
            raise HostStop("run_entered", f"run error: {msg}") from e

    renpy_main.run = _host_run  # type: ignore[assignment]
    state["run_wrapped"] = True


def run() -> None:
    base = _base_dir()
    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    # F3b: NEVER setdefault RENPY_SKIP_MAIN_MENU=1
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
    budget = float(os.environ.get("RENPY_HOST_MAX_SECS", "160") or "160")
    max_interacts = int(os.environ.get("RENPY_HOST_INTERACT_N", "160") or "160")

    state: dict = {
        "reached": "init",
        "ending": False,
        "labels_seen": [],
        "say_seen": [],
        "interact_count": 0,
    }

    skip_mm = os.environ.get("RENPY_SKIP_MAIN_MENU")
    perf = os.environ.get("RENPY_PERFORMANCE_TEST")
    _append_log(log, f"=== F3b {GATE_NAME} ===")
    _append_log(
        log,
        f"base={base} budget={budget}s N={max_interacts} "
        f"SKIP_MAIN_MENU={skip_mm!r} PERFORMANCE_TEST={perf!r}",
    )
    if skip_mm in ("1", "true", "yes", "on"):
        _append_log(
            log,
            "WARN: RENPY_SKIP_MAIN_MENU is set; F3b requires real main menu path",
        )

    meta = {
        "ok": False,
        "reached_stage": "init",
        "ending": False,
        "ending_kind": None,
        "main_menu_started": False,
        "in_game_frame_ok": False,
        "nonblank_ok": False,
        "nonblank": {},
        "ownership": {},
        "ownership_ok": False,
        "uncaught_exception": None,
        "labels": [],
        "interact_count": 0,
        "path": None,
        "skip_main_menu": skip_mm,
        "performance_test": perf,
        "notes": "",
        "traceback": "",
        "elapsed_secs": 0,
        "frame_w": 0,
        "frame_h": 0,
    }

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
            state["uncaught_exception"] = state.get("uncaught_exception") or state[
                "run_error"
            ]
            _append_log(log, f"run BaseException: {state['run_error']}")
            meta["traceback"] = tb

        # Post-run ending probe.
        label = _current_label()
        say_blob = _probe_say_text()
        is_end, kind = _looks_like_ending(label, say_blob, state)
        if is_end:
            state["ending"] = True
            state["ending_kind"] = kind or state.get("ending_kind")
            if not any("ENDING_REACHED" in x for x in log):
                _append_log(
                    log,
                    "ENDING_REACHED kind={} label={!r} say={!r}".format(state.get("ending_kind"), label, (say_blob or "")[:200]),
                )

    except Exception as e:
        meta["traceback"] = traceback.format_exc()
        _append_log(log, f"FATAL {type(e).__name__}: {e}")
    finally:
        state["done"] = True
        elapsed = round(time.monotonic() - t0, 3)
        labels = state.get("labels_seen") or []
        uniq = []
        for x in labels:
            s = str(x)
            if s not in uniq:
                uniq.append(s)

        ending = bool(state.get("ending"))
        uncaught = state.get("uncaught_exception")
        started = bool(state.get("main_menu_started"))
        in_game_ok = bool(state.get("in_game_frame_ok"))
        nonblank_ok = bool(state.get("nonblank_ok"))
        ownership_ok = bool(state.get("ownership_ok"))
        path = state.get("last_menu_path")
        if not path and state.get("forced_second_picks"):
            path = "auto_choice_index1"

        # F3b ok: started from main menu, reached an ending, no uncaught exception,
        # and at least one non-blank in-game frame sample.
        ok = bool(started and ending and not uncaught and in_game_ok)

        notes_parts = []
        if not started:
            notes_parts.append("main_menu_not_started")
        if not ending:
            notes_parts.append(
                f"ending_not_reached menu_seen={state.get('menu_seen')} "
                f"K={state.get('menu_path_k_attempts')} M={state.get('menu_path_m_attempts')} "
                f"forced_picks={state.get('forced_second_picks')}"
            )
        if uncaught:
            notes_parts.append(f"uncaught={uncaught}")
        if not in_game_ok:
            notes_parts.append(
                "in_game_frame_fail nonblank={} ownership={} reject={}".format(
                    nonblank_ok,
                    ownership_ok,
                    (state.get("nonblank") or {}).get("reject"),
                )
            )
        if ok:
            notes_parts.append(
                f"New Game→{state.get('ending_kind')} ending via {path}; "
                f"in-game frame nonblank"
            )

        meta.update(
            {
                "ok": ok,
                "reached_stage": state.get("reached"),
                "ending": ending,
                "ending_kind": state.get("ending_kind"),
                "main_menu_started": started,
                "in_game_frame_ok": in_game_ok,
                "nonblank_ok": nonblank_ok,
                "nonblank": state.get("nonblank") or {},
                "ownership": state.get("ownership") or {},
                "ownership_ok": ownership_ok,
                "uncaught_exception": uncaught,
                "labels": uniq[-20:],
                "interact_count": state.get("interact_count"),
                "path": path,
                "frame_w": state.get("frame_w") or 0,
                "frame_h": state.get("frame_h") or 0,
                "notes": "; ".join(notes_parts),
                "elapsed_secs": elapsed,
            }
        )
        _append_log(
            log,
            f"SUMMARY ok={ok} ending={ending}/{state.get('ending_kind')} "
            f"started={started} in_game_frame_ok={in_game_ok} "
            f"interact={meta['interact_count']} elapsed={elapsed}s",
        )
        _write_report(base, meta, log)
        _request_quit()

        if not ok:
            raise RuntimeError(
                f"{GATE_NAME} ok=False notes={meta['notes']}; see gate-tq-product-one-ending.txt"
            )


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

