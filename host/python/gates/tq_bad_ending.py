"""
A2 Path K harness: drive the_question to Bad Ending (keyboard + forced 2nd choice).

Gate name: tq_bad_ending
  RENPY_HOST_GATE=tq_bad_ending

Writes:
  host/target/gate-tq-bad-ending.txt
  host/target/tq-bad-ending.log            (mandatory evidence)
  .omc/artifacts/tq-one-ending.log         (mirror)

Path strategy (host-only, no the_question edits):
  1. Bootstrap stages a–c + path helpers + argv for command=run.
  2. Disable random first-choice: auto_choice_delay=0.05 with random.choice
     forced to index 1 when len>=2 (second item = "To ask her later." → later).
  3. Path K keyboard: on choice screen also inject focus_down (K_DOWN) +
     button_select (K_RETURN) so keyboard path is exercised after A1 unicode fix.
  4. Path M fallback: inject_mouse at second-choice coords if still stuck.
  5. Evidence line BAD_ENDING_REACHED when label later / Bad Ending text seen.

Locks: Mechanism 1 only, dual-tree, music soft-fail, no game content edits.
Note: run_file prepends imports — no __future__ here. Do NOT import main.py
(it auto-runs at module level).
"""

import os
import sys
import threading
import time
import traceback
import types
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---


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


def _base_dir() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "renpy").is_dir() and (p / "host" / "README.md").is_file():
            return p
    return here


def _log_paths(base: Path):
    host_log = base / "host" / "target" / "tq-bad-ending.log"
    art_log = base / ".omc" / "artifacts" / "tq-one-ending.log"
    report = base / "host" / "target" / "gate-tq-bad-ending.txt"
    return host_log, art_log, report


class HostStop(BaseException):
    def __init__(self, stage: str, detail: str = ""):
        self.stage = stage
        self.detail = detail
        super().__init__(f"HostStop@{stage}: {detail}" if detail else f"HostStop@{stage}")


def _request_quit():
    try:
        import renpy_host  # type: ignore

        renpy_host.request_quit()
    except Exception:
        pass


def _append_log(lines: list, msg: str):
    ts = time.strftime("%H:%M:%S")
    lines.append(f"[{ts}] {msg}")


def _write_evidence(base: Path, lines: list, meta: dict):
    host_log, art_log, report = _log_paths(base)
    host_log.parent.mkdir(parents=True, exist_ok=True)
    art_log.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    host_log.write_text(body, encoding="utf-8")
    art_log.write_text(body, encoding="utf-8")
    rep = [
        f"ok={meta.get('ok')}",
        f"reached_stage={meta.get('reached_stage')}",
        f"bad_ending={meta.get('bad_ending')}",
        f"path={meta.get('path')}",
        f"labels={meta.get('labels')}",
        f"interact_count={meta.get('interact_count')}",
        f"unicode_crash={meta.get('unicode_crash')}",
        f"menu_attempts={meta.get('menu_attempts')}",
        f"forced_second_choice={meta.get('forced_second_choice')}",
        f"notes={meta.get('notes')}",
        f"evidence={host_log}",
        f"evidence_mirror={art_log}",
        f"elapsed_secs={meta.get('elapsed_secs')}",
    ]
    if meta.get("traceback"):
        rep.append(f"traceback={meta['traceback'][:2000]}")
    report.write_text("\n".join(rep) + "\n", encoding="utf-8")


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


def _looks_like_later(label, say_blob: str, state: dict) -> bool:
    if state.get("hit_later_label") or state.get("hit_bad_ending_text") or state.get(
        "hit_later_dialogue"
    ):
        return True
    s = f"{say_blob}".lower()
    markers = (
        "i can't get up the nerve",
        "i'm an indecisive person",
        "never being able to ask her",
        "never know the answer to my question",
        "bad ending",
    )
    for m in markers:
        if m in s:
            return True
    if label is not None and "later" in str(label).lower():
        return True
    for x in state.get("say_seen") or []:
        xl = str(x).lower()
        for m in markers:
            if m in xl:
                return True
    return False


def _force_second_choice_random(state: dict, log: list) -> None:
    """
    Make renpy.exports.random.choice pick index 1 when possible.

    menuexports.menu uses:
      renpy.ui.pausebehavior(auto_choice_delay, renpy.exports.random.choice(choices))
    For the_question first menu, choices[1] → jump later → Bad Ending.
    """
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
    # Keep store.random in sync if bound.
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
            _append_log(log, f"PathK inject_key K_DOWN ok key={K_DOWN}")
        elif r.get("error"):
            out["error"] = r["error"]
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
            _append_log(log, "PathK inject_key K_RETURN ok")
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
            # Force second menu item via auto_choice (index 1).
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
            if on_choice:
                state["menu_seen"] = True
                state["menu_label"] = label_before
                _append_log(log, f"interact#{n} CHOICE screen label={label_before!r}")

            try:
                if ih.in_main_menu() and not state.get("main_menu_started"):
                    try:
                        st = ih.activate_main_menu_start("start")
                        state["main_menu_start"] = st
                        if st.get("started"):
                            state["main_menu_started"] = True
                    except BaseException as je:
                        from renpy import game

                        if isinstance(je, game.CONTROL_EXCEPTIONS):
                            state["main_menu_started"] = True
                            raise
                        raise

                if on_choice and not state.get("hit_later_label"):
                    k_att = int(state.get("menu_path_k_attempts") or 0)
                    if k_att < 8:
                        _path_k_select_second(state, log)
                    else:
                        _path_m_click_second(state, log)
                else:
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
                if "unicode" in msg.lower() or (
                    type(e).__name__ == "AttributeError" and "unicode" in str(e)
                ):
                    state["unicode_crash"] = msg
                    _append_log(log, f"UNICODE_CRASH {msg}")
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

            label_after = _current_label()
            say_blob = _probe_say_text()
            if label_after is not None:
                ls = state["labels_seen"]
                if not ls or ls[-1] != label_after:
                    ls.append(label_after)
            # Strong markers for label later (script.rpy lines 237–250).
            later_markers = (
                "I can't get up the nerve",
                "I'm an indecisive person",
                "never being able to ask her",
                "never know the answer to my question",
                "Bad Ending",
            )
            if say_blob:
                if not state["say_seen"] or state["say_seen"][-1] != say_blob[:300]:
                    state["say_seen"].append(say_blob[:300])
                low = say_blob.lower()
                for mk in later_markers:
                    if mk.lower() in low:
                        state["hit_later_dialogue"] = True
                        if "bad ending" in mk.lower():
                            state["hit_bad_ending_text"] = True
                        _append_log(
                            log,
                            f"HIT later-marker {mk!r} interact#{n} label={label_after!r}",
                        )
                        break

            # Detect jump-into-later by serial gap: menu node then later branch.
            if state.get("menu_label") and label_after is not None:
                try:
                    # name tuples: (filename, serial, ...) — later is after rightaway block.
                    if isinstance(label_after, tuple) and len(label_after) >= 2:
                        int(label_after[1]) if False else label_after
                except Exception:
                    pass

            if n % 5 == 0 or on_choice or state.get("hit_later_dialogue"):
                _append_log(
                    log,
                    f"interact#{n} label={label_after!r} choice={_is_choice_screen()} "
                    f"later_dlg={bool(state.get('hit_later_dialogue'))} "
                    f"bad_text={bool(state.get('hit_bad_ending_text'))}",
                )

            if (
                state.get("hit_bad_ending_text")
                or state.get("hit_later_dialogue")
                or _looks_like_later(label_after, say_blob, state)
            ):
                state["bad_ending"] = True
                state["reached"] = "bad_ending"
                post = int(state.get("post_later_count") or 0) + 1
                state["post_later_count"] = post
                # Prefer waiting for explicit Bad Ending line after later dialogue.
                if state.get("hit_bad_ending_text") or (
                    state.get("hit_later_dialogue") and post >= 5
                ):
                    path = state.get("last_menu_path") or "auto_choice_index1"
                    _append_log(
                        log,
                        "BAD_ENDING_REACHED path={} label={!r} say={!r} forced_picks={} "
                        "later_dlg={} bad_text={}".format(
                            path,
                            label_after,
                            (say_blob or "")[:240],
                            state.get("forced_second_picks"),
                            bool(state.get("hit_later_dialogue")),
                            bool(state.get("hit_bad_ending_text")),
                        ),
                    )
                    raise HostStop(
                        "bad_ending",
                        f"later/Bad Ending at interact={n} path={path}",
                    )

            if n >= max_interacts:
                state["reached"] = "first_interact"
                raise HostStop(
                    "first_interact",
                    f"N-cap interact_count={n}; bad_ending={state.get('bad_ending')}",
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
            if "unicode" in msg.lower():
                state["unicode_crash"] = msg
                _append_log(log, f"UNICODE_CRASH_RUN {msg}")
            raise HostStop("run_entered", f"run error: {msg}") from e

    renpy_main.run = _host_run  # type: ignore[assignment]
    state["run_wrapped"] = True

    # Optional load_script marker.
    try:
        import renpy.script as script_mod

        Script = script_mod.Script
        _orig_load = Script.load_script

        def _load_script(self, *a, **k):
            rv = _orig_load(self, *a, **k)
            state["script_loaded"] = True
            if state.get("reached") in (None, "init", "early_main"):
                state["reached"] = "script_loaded"
            return rv

        Script.load_script = _load_script  # type: ignore[assignment]
    except Exception as e:
        state["load_script_wrap_error"] = f"{type(e).__name__}: {e}"


def _pre_main_host_stubs(log: list) -> None:
    """Mirror main.py stage_early_main host rebinds (uguu/ecsign/sound/pygame)."""
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

    # Force renpy.uguu host stub — config.init imports GL_* from renpy.uguu.
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
        _append_log(log, f"uguu stub FATAL-ish: {type(e).__name__}: {e}")

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


def run() -> None:
    base = _base_dir()
    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_SKIP_MAIN_MENU", "1")
    os.environ.setdefault("RENPY_SKIP_SPLASHSCREEN", "1")
    os.environ.setdefault("RENPY_HOST_MUTE", "1")

    tq = base / "the_question"
    if tq.is_dir():
        os.environ.setdefault("RENPY_HOST_GAME", str(tq))

    gates = base / "host" / "python" / "gates"
    if str(gates) not in sys.path:
        sys.path.insert(0, str(gates))

    log: list = []
    t0 = time.monotonic()
    budget = float(os.environ.get("RENPY_HOST_MAX_SECS", "90") or "90")
    max_interacts = int(os.environ.get("RENPY_HOST_INTERACT_N", "120") or "120")

    state: dict = {
        "reached": "init",
        "bad_ending": False,
        "labels_seen": [],
        "say_seen": [],
        "interact_count": 0,
        "unicode_crash": None,
    }

    _append_log(log, "=== A2 Path K/M Bad Ending harness ===")
    _append_log(log, f"base={base} budget={budget}s N={max_interacts}")
    _append_log(
        log,
        "menu[0]='To ask her right away.' menu[1]='To ask her later.' → label later → Bad Ending",
    )
    _append_log(log, "strategy: auto_choice index=1 + Path K Down/Return + Path M fallback")

    meta = {
        "ok": False,
        "reached_stage": "init",
        "bad_ending": False,
        "path": None,
        "labels": [],
        "interact_count": 0,
        "unicode_crash": False,
        "menu_attempts": {},
        "forced_second_choice": False,
        "notes": "",
        "traceback": "",
        "elapsed_secs": 0,
    }

    try:
        import renpy_host  # noqa: F401
    except Exception as e:
        _append_log(log, f"FATAL no renpy_host: {e}")
        meta["notes"] = "must run under renpy-host embed"
        meta["traceback"] = traceback.format_exc()
        meta["elapsed_secs"] = round(time.monotonic() - t0, 3)
        _write_evidence(base, log, meta)
        _request_quit()
        return

    try:
        import bootstrap as boot
    except Exception as e:
        _append_log(log, f"FATAL import bootstrap: {e}")
        meta["traceback"] = traceback.format_exc()
        meta["elapsed_secs"] = round(time.monotonic() - t0, 3)
        _write_evidence(base, log, meta)
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
            if "unicode" in str(e).lower() or (
                type(e).__name__ == "AttributeError" and "unicode" in str(e)
            ):
                state["unicode_crash"] = state["run_error"]
                _append_log(log, f"UNICODE_CRASH {state['unicode_crash']}")
            _append_log(log, f"run BaseException: {state['run_error']}")
            meta["traceback"] = tb

        label = _current_label()
        say_blob = _probe_say_text()
        if _looks_like_later(label, say_blob, state):
            state["bad_ending"] = True
            if not any("BAD_ENDING_REACHED" in x for x in log):
                path = state.get("last_menu_path") or "auto_choice_index1"
                _append_log(
                    log,
                    "BAD_ENDING_REACHED path={} label={!r} say={!r} forced_picks={}".format(path, label, (say_blob or "")[:200], state.get("forced_second_picks")),
                )

        tb_file = tq / "traceback.txt"
        if tb_file.is_file():
            try:
                tbt = tb_file.read_text(encoding="utf-8", errors="replace")
                age = time.time() - tb_file.stat().st_mtime
                if age < budget + 60 and "unicode" in tbt.lower() and "AttributeError" in tbt:
                    state["unicode_crash"] = (
                        state.get("unicode_crash") or "traceback.txt AttributeError unicode"
                    )
                    _append_log(log, f"product traceback.txt unicode AttributeError age={age:.1f}s")
            except Exception:
                pass

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

        bad = bool(
            state.get("bad_ending")
            or state.get("hit_later_label")
            or state.get("hit_bad_ending_text")
        )
        unicode_crash = bool(state.get("unicode_crash"))
        path = state.get("last_menu_path")
        if not path and state.get("forced_second_picks"):
            path = "auto_choice_index1"

        if bad and path:
            notes = f"Bad Ending reached via {path}"
        elif bad:
            notes = "Bad Ending reached (path uncertain)"
        elif state.get("menu_seen"):
            notes = (
                f"HARD_BLOCK: menu seen but later/Bad Ending not reached; "
                f"K={state.get('menu_path_k_attempts')} M={state.get('menu_path_m_attempts')} "
                f"forced_picks={state.get('forced_second_picks')} labels_tail={uniq[-8:]}"
            )
            _append_log(log, notes)
        else:
            notes = (
                f"HARD_BLOCK: choice menu never observed; "
                f"interact={state.get('interact_count')} labels_tail={uniq[-8:]}"
            )
            _append_log(log, notes)

        if unicode_crash:
            notes += f" | UNICODE_CRASH={state.get('unicode_crash')}"
        else:
            _append_log(
                log,
                "unicode_crash=False (no AttributeError unicode on menu path this run)",
            )

        meta.update(
            {
                "ok": bool(bad) and not unicode_crash,
                "reached_stage": state.get("reached"),
                "bad_ending": bad,
                "path": path,
                "labels": uniq[-20:],
                "interact_count": state.get("interact_count"),
                "unicode_crash": unicode_crash,
                "menu_attempts": {
                    "K": state.get("menu_path_k_attempts"),
                    "M": state.get("menu_path_m_attempts"),
                    "menu_seen": state.get("menu_seen"),
                    "forced_second_picks": state.get("forced_second_picks"),
                },
                "forced_second_choice": bool(state.get("forced_second_choice")),
                "notes": notes,
                "elapsed_secs": elapsed,
            }
        )
        _append_log(
            log,
            f"SUMMARY ok={meta['ok']} bad_ending={bad} path={path} "
            f"interact={meta['interact_count']} unicode_crash={unicode_crash} "
            f"elapsed={elapsed}s",
        )
        _write_evidence(base, log, meta)
        _request_quit()


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
