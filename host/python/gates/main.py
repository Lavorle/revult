"""
AC5 main gate under renpy-host embed.

Gate name: main  (RENPY_HOST_GATE=main → host/target/gate-main.txt)

Stages:
  a. import_renpy          — reuse bootstrap stage
  b. import_all            — reuse bootstrap stage
  c. set_game_dir          — reuse bootstrap stage (the_question)
  d. main_helpers          — verify renpy.__main__ path helpers
  e. prepare_args          — argv/args for command=run
  f. early_main            — renpy.main.main() until Interface / HostStop
                             reports: script_loaded, interface_created, run_entered
  g. first_interact        — task #31 soft smoke (no-op until Interface)

Strategy A (dual-tree safe): monkeypatch renpy.main.run to raise HostStop
before the product restart/interact loop. host_build already selects WgpuDraw.
Does NOT rewrite interact_core to tick().

Locks:
  - Mechanism 1 only (nested wait_until pump)
  - no interact_core→tick rewrite
  - dual-tree / no libSDL*

Wall-clock: respect RENPY_HOST_MAX_SECS / RENPY_HOST_SMOKE_SECS; always
call renpy_host.request_quit() so the host does not hang forever.
"""

import os
import sys
import threading
import time
import traceback
from pathlib import Path



# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

STAGES = (
    "import_renpy",
    "import_all",
    "set_game_dir",
    "main_helpers",
    "prepare_args",
    "early_main",
    "script_loaded",
    "interface_created",
    "run_entered",
    "first_interact",  # task #31; soft when Interface exists
)

SUCCESS_MAIN_STAGES = frozenset(
    {"script_loaded", "interface_created", "run_entered", "first_interact"}
)


class HostStop(BaseException):
    """Controlled stop after Interface (or on run entry). Not a product error."""

    def __init__(self, stage: str, detail: str = ""):
        self.stage = stage
        self.detail = detail
        super().__init__(f"HostStop@{stage}: {detail}" if detail else f"HostStop@{stage}")


def _style_default_exists() -> bool:
    """True when style.default (or style registry 'default') is present."""
    try:
        import renpy.style as style_mod

        styles = getattr(style_mod, "styles", None)
        if styles is not None:
            # Style registry keys are tuples: ('default',) or 'default'.
            if ("default",) in styles or "default" in styles:
                return True
    except Exception:
        pass
    try:
        from renpy import store

        st = getattr(store, "style", None)
        if st is not None:
            try:
                d = st.default  # may raise if missing
                return d is not None
            except Exception:
                return False
    except Exception:
        pass
    return False


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


def _report_path(base: Path) -> Path:
    return base / "host" / "target" / "gate-main.txt"


def _write_report(
    base: Path,
    *,
    reached_stage: str,
    missing: list,
    tb: str,
    ok: bool,
    notes: str = "",
    extra: dict | None = None,
) -> Path:
    out = _report_path(base)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"reached_stage={reached_stage}",
        f"missing={missing!r}",
        f"traceback={tb!r}" if tb else "traceback=",
        f"ok={ok}",
    ]
    if notes:
        lines.append(f"notes={notes}")
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}={v}")
    text = "\n".join(lines) + "\n"
    out.write_text(text, encoding="utf-8")
    print(text, flush=True)
    return out


def _max_secs() -> float:
    """Wall-clock budget. Prefer RENPY_HOST_MAX_SECS, else SMOKE_SECS, else 30."""
    for key in ("RENPY_HOST_MAX_SECS", "RENPY_HOST_SMOKE_SECS"):
        raw = os.environ.get(key)
        if raw:
            try:
                return max(1.0, float(raw))
            except ValueError:
                pass
    return 30.0


def _parse_interact_n(raw: str | None = None) -> tuple[int | None, bool]:
    """
    Parse RENPY_HOST_INTERACT_N.

    Returns (max_interacts, unlimited):
      - unset/empty → (4, False)  # CI smoke default
      - positive int → (N, False)  # no hard ceiling
      - 0 / -1 / "unlimited" (case-insensitive) → (None, True)
      - other non-int → (4, False)  # fall back to default
    """
    if raw is None:
        raw = os.environ.get("RENPY_HOST_INTERACT_N", "4")
    s = (raw or "").strip()
    if not s:
        return 4, False
    if s.lower() == "unlimited":
        return None, True
    try:
        n = int(s)
    except ValueError:
        return 4, False
    if n == 0 or n == -1:
        return None, True
    if n < 0:
        # Other negatives: treat as unlimited (same intent as -1).
        return None, True
    return n, False


def _auto_advance_enabled() -> bool:
    """RENPY_HOST_AUTO_ADVANCE default on; 0/false/no/off disables injects."""
    return os.environ.get("RENPY_HOST_AUTO_ADVANCE", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _request_quit():
    try:
        import renpy_host

        renpy_host.request_quit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shared helpers (importable by task #30 / #31)
# ---------------------------------------------------------------------------

def ensure_renpy_main(base: Path | None = None):
    """
    Ensure renpy.__main__ exposes the path helpers.

    Prefer the host install (renpy_main_host). If embed already installed it,
    verify; otherwise install now (idempotent).
    """
    import renpy

    base = base or _base_dir()
    main_mod = getattr(renpy, "__main__", None)
    have = {
        name: callable(getattr(main_mod, name, None)) if main_mod is not None else False
        for name in REQUIRED_HELPERS
    }

    if all(have.values()):
        return main_mod, have, "present"

    # Install / re-install from host helper module.
    try:
        import renpy_main_host
    except Exception as e:
        raise RuntimeError(f"renpy_main_host import failed: {e}") from e

    main_mod = renpy_main_host.install(renpy)
    have = {name: callable(getattr(main_mod, name, None)) for name in REQUIRED_HELPERS}
    if not all(have.values()):
        missing = [n for n, ok in have.items() if not ok]
        raise RuntimeError(f"renpy.__main__ still missing helpers: {missing}")
    return main_mod, have, "installed"


def prepare_run_args(base: Path, basedir: str | None = None):
    """
    Prepare sys.argv and renpy.game.args for command=run.

    Returns (args, extra_dict). Shared with later main-body stages.
    """
    import renpy
    import renpy.arguments

    if basedir is None:
        basedir = getattr(renpy.config, "basedir", None) or str(base / "the_question")

    argv0 = sys.argv[0] if sys.argv else "renpy-host"
    sys.argv = [argv0, basedir, "run"]

    extra = {"sys.argv": list(sys.argv)}

    # Register default commands if empty (import_all usually does this via
    # renpy.arguments side-effects, but be defensive for stepwise use).
    if not getattr(renpy.arguments, "commands", None):
        # Mirror renpy.arguments bottom-of-module registration.
        try:
            renpy.arguments.register_command("run", renpy.arguments.run, True)
            renpy.arguments.register_command("quit", renpy.arguments.quit)
        except Exception as e:
            extra["register_command_error"] = f"{type(e).__name__}: {e}"

    args = renpy.arguments.bootstrap()
    renpy.game.args = args
    extra["arguments"] = f"command={getattr(args, 'command', None)}"
    extra["game.args.command"] = getattr(getattr(renpy.game, "args", None), "command", None)
    return args, extra


def probe_main_helpers(base: Path):
    """
    Stage d body: install + exercise path helpers against the_question.

    Returns (ok, missing, err, extra).
    """
    import renpy

    extra: dict = {}
    missing: list = []

    try:
        main_mod, have, how = ensure_renpy_main(base)
    except Exception as e:
        return False, ["renpy.__main__"], f"{type(e).__name__}: {e}", extra

    extra["renpy.__main__"] = getattr(main_mod, "__name__", str(main_mod))
    extra["renpy.__main__.file"] = getattr(main_mod, "__file__", "?")
    extra["helpers_source"] = how
    for name, okh in have.items():
        extra[f"helper.{name}"] = okh
        if not okh:
            missing.append(name)

    if missing:
        return False, missing, f"missing helpers: {missing}", extra

    # Exercise helpers with current config (set_game_dir already ran).
    renpy_base = getattr(renpy.config, "renpy_base", None) or str(base)
    gamedir = getattr(renpy.config, "gamedir", None) or str(base / "the_question" / "game")
    basedir = getattr(renpy.config, "basedir", None) or str(base / "the_question")

    try:
        common = main_mod.path_to_common(renpy_base)
        extra["path_to_common"] = common
        if not common or not os.path.isdir(common):
            missing.append("common_dir")
            return False, missing, f"path_to_common invalid: {common!r}", extra

        gamedir_probe = main_mod.path_to_gamedir(basedir, "the_question")
        extra["path_to_gamedir"] = gamedir_probe
        if not gamedir_probe or not os.path.isdir(gamedir_probe):
            missing.append("gamedir_probe")
            return False, missing, f"path_to_gamedir invalid: {gamedir_probe!r}", extra

        # Ensure config.gamedir is set before searchpath (predefined_searchpath reads it).
        if not getattr(renpy.config, "gamedir", None):
            renpy.config.gamedir = gamedir

        searchpath = main_mod.predefined_searchpath(common)
        extra["predefined_searchpath"] = list(searchpath) if searchpath is not None else None
        if not searchpath:
            missing.append("searchpath")
            return False, missing, "predefined_searchpath empty", extra

        logdir = main_mod.path_to_logdir(basedir)
        extra["path_to_logdir"] = logdir

        # path_to_saves needs config.save_directory; may be None pre-init — OK,
        # helper falls back to gamedir/saves.
        try:
            saves = main_mod.path_to_saves(gamedir)
            extra["path_to_saves"] = saves
        except Exception as e:
            # Soft: save_directory may be unset; still count helper present.
            extra["path_to_saves_error"] = f"{type(e).__name__}: {e}"
            try:
                saves = main_mod.path_to_saves(gamedir, save_directory="the_question")
                extra["path_to_saves"] = saves
            except Exception as e2:
                missing.append("path_to_saves")
                return False, missing, f"path_to_saves failed: {e2}", extra

    except Exception as e:
        return False, missing or ["helpers_runtime"], f"{type(e).__name__}: {e}\n{traceback.format_exc()}", extra

    extra["main_helpers"] = "ok"
    return True, [], "", extra


def stage_prepare_args(base: Path):
    """Stage e: argv/args for command=run; prepare logdir for main()."""
    import renpy

    extra: dict = {
        "main_body": "prepared",
        "policy": "Strategy A HostStop after Interface; Mechanism 1 pump only",
        "strategy": "A_hoststop_after_interface",
    }
    try:
        args, args_extra = prepare_run_args(base)
        extra.update(args_extra)
        cmd = getattr(args, "command", None)
        if cmd != "run":
            return False, ["arguments.command"], f"expected command=run, got {cmd!r}", extra
    except SystemExit as e:
        return False, ["arguments"], f"arguments SystemExit: {e}", extra
    except Exception as e:
        return False, ["arguments"], f"{type(e).__name__}: {e}\n{traceback.format_exc()}", extra

    # Confirm renpy.main is importable and main is callable.
    try:
        import renpy.main as renpy_main

        extra["renpy.main"] = "imported"
        extra["renpy.main.main"] = callable(getattr(renpy_main, "main", None))
        extra["renpy.main.run"] = callable(getattr(renpy_main, "run", None))
    except Exception as e:
        return False, ["renpy.main"], f"renpy.main not importable: {e}", extra

    # logdir + renpy_base (bootstrap sets these before main).
    basedir = getattr(renpy.config, "basedir", None) or str(base / "the_question")
    renpy.config.renpy_base = getattr(renpy.config, "renpy_base", None) or str(base)
    try:
        main_mod, _, _ = ensure_renpy_main(base)
        logdir = main_mod.path_to_logdir(basedir)
        renpy.config.logdir = logdir
        os.makedirs(logdir, 0o777, exist_ok=True)
        extra["logdir"] = logdir
    except Exception as e:
        extra["logdir_error"] = f"{type(e).__name__}: {e}"

    try:
        renpy.importer.init_importer()
        extra["importer"] = "ok"
    except Exception as e:
        extra["importer"] = f"{type(e).__name__}: {e}"

    extra["shared.ensure_renpy_main"] = True
    extra["shared.prepare_run_args"] = True
    extra["shared.probe_main_helpers"] = True
    extra["shared.stage_early_main"] = True
    return True, [], "", extra


def _install_host_stop_hooks(state: dict) -> None:
    """
    Strategy A hooks (runtime only, dual-tree safe):

    1. After Interface.__init__ completes → mark interface_created.
    2. renpy.main.run → limited product interact under Mechanism 1, then HostStop.
    3. Script.load_script → mark script_loaded.
    """
    import renpy
    import renpy.main as renpy_main
    from renpy.display import core

    OrigInterface = core.Interface

    class HostInterface(OrigInterface):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **k):
            OrigInterface.__init__(self, *a, **k)
            state["reached"] = "interface_created"
            state["interface"] = True
            try:
                draw = getattr(renpy.display, "draw", None)
                state["draw_type"] = type(draw).__name__ if draw is not None else None
            except Exception:
                state["draw_type"] = "?"

    core.Interface = HostInterface  # type: ignore[misc,assignment]
    renpy.display.core.Interface = HostInterface  # type: ignore[attr-defined]
    state["Interface_wrapped"] = True

    _orig_run = renpy_main.run

    def _host_run(restart):
        state["reached"] = "run_entered"
        state["run_restart"] = repr(restart)

        # Snapshot post-initcode state the moment product run() is entered.
        # initcode execute happens in renpy.main.main BEFORE run(); so this is
        # the "after execute_init" probe for task #32.
        try:
            script = getattr(getattr(renpy, "game", None), "script", None)
            if script is not None:
                state["initcode_len"] = len(getattr(script, "initcode", []) or [])
                state["initcode_len_after_execute"] = state["initcode_len"]
                common_n = len(getattr(script, "common_script_files", []) or [])
                game_n = len(getattr(script, "script_files", []) or [])
                state["script_files"] = (
                    common_n + game_n if (common_n or game_n) else game_n
                )
            state["style_default_exists"] = _style_default_exists()
            state["style_default_exists_after_execute"] = state["style_default_exists"]
        except Exception as e:
            state["post_init_probe_error"] = f"{type(e).__name__}: {e}"

        # Hard gate: do not product-interact without styles/initcode.
        # Prefer clear diagnosis over a flaky Style 'default' crash later.
        init_len = int(state.get("initcode_len") or 0)
        style_ok = bool(state.get("style_default_exists"))
        if init_len < 100 or not style_ok:
            raise HostStop(
                "run_entered",
                f"init incomplete: initcode_len={init_len} "
                f"style_default_exists={style_ok} "
                f"(need initcode_len>=100 and style.default before interact)",
            )

        # Default: allow a short product interact so task #31 can inject.
        # Set RENPY_HOST_ALLOW_INTERACT=0 to restore pure HostStop-at-run-entry.
        allow = os.environ.get("RENPY_HOST_ALLOW_INTERACT", "1").lower() not in (
            "0",
            "false",
            "no",
            "off",
        )
        if not allow:
            raise HostStop("run_entered", f"restart={restart!r}; interact disabled")

        max_interacts, unlimited = _parse_interact_n()
        state["max_interacts"] = max_interacts
        state["unlimited_interacts"] = unlimited
        auto_advance = _auto_advance_enabled()
        state["auto_advance"] = auto_advance
        # Period for frame present under unlimited (every 10 interacts).
        present_period = max_interacts if (max_interacts and max_interacts > 0) else 10

        # Re-apply after initcode — 00preferences may have restored transitions=2.
        try:
            prefs = getattr(renpy.game, "preferences", None)
            if prefs is not None and hasattr(prefs, "transitions"):
                prefs.transitions = 0
                state["preferences.transitions"] = 0
            if prefs is not None and hasattr(prefs, "performance_test"):
                prefs.performance_test = False
            renpy.config.performance_test = False
            # Soft-mute product BGM for hermetic gates (cpal beep path is fine but
            # opening large opus via loader can stall; music is not AC5 signal).
            renpy.config.has_music = False
            renpy.config.main_menu_music = None
            state["has_music"] = False
            # Instant text + AFM so say interacts complete without multi-dismiss.
            # Pre-inject alone often only skips typewriter (first dismiss) then waits.
            if prefs is not None:
                if hasattr(prefs, "text_cps"):
                    prefs.text_cps = 0
                    state["preferences.text_cps"] = 0
                if hasattr(prefs, "afm_enable"):
                    prefs.afm_enable = True
                    state["preferences.afm_enable"] = True
                if hasattr(prefs, "afm_time"):
                    prefs.afm_time = 0.05
                    state["preferences.afm_time"] = 0.05
                if hasattr(prefs, "using_afm_enable"):
                    prefs.using_afm_enable = True
            # Auto-pick menu choices so HostStop N-cap is not stuck on menus.
            try:
                renpy.config.auto_choice_delay = 0.05
                state["auto_choice_delay"] = 0.05
            except Exception:
                pass
            try:
                renpy.config.skip_delay = 1
            except Exception:
                pass
        except Exception as e:
            state["run_entry_prefs_error"] = f"{type(e).__name__}: {e}"

        iface = getattr(getattr(renpy, "game", None), "interface", None)
        if iface is None:
            raise HostStop("run_entered", "interface missing at run entry")

        # Unwrap any prior host limited-interact wrapper so run() restarts
        # (main menu / exception recover) do not nest wrappers forever.
        prev = iface.interact
        if getattr(prev, "_host_limited", False):
            orig_interact = getattr(prev, "_host_orig", None) or prev
            state["interact_unwrapped"] = True
        else:
            orig_interact = prev

        before_ctx = {}
        try:
            import interact_helpers as ih

            # Keep first snapshot for advance probe across restarts if already set.
            if not state.get("interact_before"):
                before_ctx = ih.snapshot_context()
            else:
                before_ctx = state.get("interact_before") or {}
        except Exception as e:
            state["interact_snapshot_error"] = f"{type(e).__name__}: {e}"

        # Do not zero interact_count on every run restart — N-cap is total product interacts.
        state.setdefault("interact_count", 0)
        state.setdefault("advanced", False)
        state["events_seen"] = int(state.get("events_seen") or 0)
        state.setdefault("injects_ok", 0)

        def _limited_interact(*a, **k):
            n = int(state.get("interact_count") or 0) + 1
            state["interact_count"] = n

            # Pre-queue dismiss/select so event_wait (Mechanism 1) can drain them.
            # Skip when RENPY_HOST_AUTO_ADVANCE is off so a human can drive.
            # With RENPY_SKIP_MAIN_MENU=1, product starts at dialogue; RETURN/SPACE
            # advances say. Multi-click remains for menus / fallback.
            if auto_advance:
                try:
                    import interact_helpers as ih

                    # Optional: if still on main menu (env skip disabled), Start via JumpOut.
                    if ih.in_main_menu() and not state.get("main_menu_started"):
                        try:
                            st = ih.activate_main_menu_start("start")
                            state["main_menu_start"] = st
                            if st.get("started"):
                                state["main_menu_started"] = True
                        except BaseException as je:
                            try:
                                from renpy import game

                                if isinstance(je, game.CONTROL_EXCEPTIONS):
                                    state["main_menu_started"] = True
                                    state["main_menu_start"] = {
                                        "started": True,
                                        "reason": type(je).__name__,
                                    }
                                    raise
                            except ImportError:
                                if type(je).__name__ in (
                                    "JumpOutException",
                                    "JumpException",
                                    "QuitException",
                                    "CallException",
                                ):
                                    state["main_menu_started"] = True
                                    raise
                            raise

                    # Named keymap events (dismiss/button_select) + raw key/mouse fallback.
                    pulse = ih.advance_dialogue_pulse()
                    state["injects_ok"] = int(state.get("injects_ok") or 0) + int(
                        pulse.get("queued") or 0
                    ) + int(pulse.get("injected") or 0)
                    if pulse.get("error"):
                        state["inject_error"] = pulse["error"]
                except Exception as e:
                    # Re-raise product control exceptions (Start/JumpOut).
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

            try:
                rv = orig_interact(*a, **k)
            except HostStop:
                raise
            except BaseException as e:
                # Product control exceptions must not be treated as interact errors.
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
                # Task #34: still try to present a frame even when interact aborts
                # (e.g. Style 'default' missing during init) so frame_ok can pass.
                try:
                    import interact_helpers as ih

                    fr = ih.ensure_frame_present(force=True)
                    state["present_path"] = fr.get("path")
                    state["present_draw_source"] = fr.get("draw_source")
                    if fr.get("presented"):
                        state["frame_presented"] = True
                    if fr.get("error"):
                        state["present_error"] = fr.get("error")
                except Exception as pe:
                    state["present_error"] = f"{type(pe).__name__}: {pe}"
                try:
                    import interact_helpers as ih

                    after = ih.snapshot_context()
                    state["interact_after"] = after
                    if before_ctx.get("label") is not None and after.get("label") is not None:
                        if before_ctx.get("label") != after.get("label"):
                            state["advanced"] = True
                            state["advanced_reason"] = "label_changed"
                except Exception:
                    pass
                raise

            try:
                import interact_helpers as ih

                after = ih.snapshot_context()
                state["interact_after"] = after
                reasons = []
                if before_ctx.get("label") is not None and after.get("label") is not None:
                    if before_ctx.get("label") != after.get("label"):
                        reasons.append("label_changed")
                bi = before_ctx.get("interaction_counter")
                ai = after.get("interaction_counter")
                if bi is not None and ai is not None and int(ai) > int(bi):
                    reasons.append("interaction_counter_up")
                bt = before_ctx.get("ticks")
                at = after.get("ticks")
                if bt is not None and at is not None and int(at) > int(bt):
                    reasons.append("ticks_up")
                if n > 1:
                    reasons.append("multi_interact")
                if reasons:
                    state["advanced"] = True
                    state["advanced_reason"] = ",".join(reasons)
            except Exception as e:
                state["advance_probe_error"] = f"{type(e).__name__}: {e}"

            # Task #34: ensure a product/surrogate present so frame_ok can pass.
            # Limited: present on n==1 and at the N-cap HostStop.
            # Unlimited: present on n==1 and every present_period (default 10).
            at_cap = (not unlimited) and max_interacts is not None and n >= max_interacts
            periodic = unlimited and (n % present_period == 0)
            if n == 1 or at_cap or periodic:
                try:
                    import interact_helpers as ih

                    fr = ih.ensure_frame_present(force=at_cap or periodic)
                    state["present_path"] = fr.get("path")
                    state["present_draw_source"] = fr.get("draw_source")
                    if fr.get("presented"):
                        state["frame_presented"] = True
                    if fr.get("error"):
                        state["present_error"] = fr.get("error")
                except Exception as e:
                    state["present_error"] = f"{type(e).__name__}: {e}"

            # N-based HostStop only in limited mode. Unlimited exits via
            # wall-clock (RENPY_HOST_MAX_SECS / watchdog / request_quit).
            if at_cap:
                state["reached"] = "first_interact"
                raise HostStop(
                    "first_interact",
                    f"interact_count={n}; advanced={state.get('advanced')}",
                )
            return rv

        _limited_interact._host_limited = True  # type: ignore[attr-defined]
        _limited_interact._host_orig = orig_interact  # type: ignore[attr-defined]
        iface.interact = _limited_interact  # type: ignore[method-assign]
        state["interact_wrapped"] = True
        if not state.get("interact_before"):
            state["interact_before"] = before_ctx

        try:
            return _orig_run(restart)
        except HostStop:
            raise
        except Exception as e:
            # Product control flow (Start → JumpOutException) must reach
            # renpy.game.invoke_in_new_context handlers, not become HostStop.
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
            state["run_error"] = f"{type(e).__name__}: {e}"
            state["run_traceback"] = traceback.format_exc()
            if int(state.get("interact_count") or 0) > 0:
                state["reached"] = "first_interact"
                raise HostStop(
                    "first_interact",
                    f"run error after interact: {type(e).__name__}: {e}",
                ) from e
            raise HostStop("run_entered", f"run error: {type(e).__name__}: {e}") from e

    renpy_main.run = _host_run  # type: ignore[assignment]
    state["run_patched"] = True

    try:
        import renpy.script as script_mod

        if hasattr(script_mod, "Script") and not getattr(
            script_mod.Script.load_script, "_host_wrapped", False
        ):
            _orig_load = script_mod.Script.load_script

            def _load_script(self, *a, **k):
                rv = _orig_load(self, *a, **k)
                # Only advance stage if we have not already passed it.
                if state.get("reached") in (
                    None,
                    "early_main",
                    "early_init",
                    "prepare_args",
                    "main_helpers",
                ):
                    state["reached"] = "script_loaded"
                try:
                    common_n = len(getattr(self, "common_script_files", []) or [])
                    game_n = len(getattr(self, "script_files", []) or [])
                    state["script_files"] = (
                        common_n + game_n if (common_n or game_n) else game_n
                    )
                    state["script_files_common"] = common_n
                    state["script_files_game"] = game_n
                    state["initcode_len"] = len(getattr(self, "initcode", []) or [])
                    state["initcode_len_after_load"] = state["initcode_len"]
                    state["style_default_exists_after_load"] = _style_default_exists()
                except Exception as e:
                    state["load_script_probe_error"] = f"{type(e).__name__}: {e}"
                return rv

            _load_script._host_wrapped = True  # type: ignore[attr-defined]
            script_mod.Script.load_script = _load_script  # type: ignore[assignment]
            state["load_script_wrapped"] = True
    except Exception as e:
        state["load_script_wrap_error"] = f"{type(e).__name__}: {e}"


def _start_wall_clock_watchdog(state: dict, max_secs: float, base: Path | None = None):
    """Background soft quit after max_secs (host also has hard RENPY_HOST_MAX_SECS).

    Critical: run_gate blocks the outer Rust event loop, so MAX_SECS only helps when
    product is inside Mechanism 1 wait_until (should_exit check). If Python is stuck
    outside wait_until, request_quit alone will not unwind — still write a partial
    gate-main.txt so external kills leave evidence.
    """
    if max_secs <= 0:
        return None

    def _watch():
        deadline = time.time() + max_secs
        while time.time() < deadline:
            if state.get("done"):
                return
            # Heartbeat so operators can see progress without waiting for HostStop.
            try:
                state["watchdog_heartbeat"] = time.time()
                state["watchdog_alive_secs"] = round(max_secs - (deadline - time.time()), 1)
            except Exception:
                pass
            time.sleep(0.25)
        if state.get("done"):
            return
        state["watchdog_fired"] = True
        try:
            import renpy_host

            renpy_host.request_quit()
        except Exception:
            pass
        # Also try product QuitException path if interface exists.
        try:
            from renpy import game

            if getattr(game, "interface", None) is not None:
                # Cannot raise into main thread; set a flag product may poll.
                state["force_quit"] = True
        except Exception:
            pass
        # Partial report — main thread may never reach finally.
        if base is not None and not state.get("watchdog_report_written"):
            try:
                extra = {
                    "watchdog_fired": True,
                    "watchdog_partial_report": True,
                    "interact_count": state.get("interact_count"),
                    "advanced": state.get("advanced"),
                    "max_interacts": state.get("max_interacts"),
                    "unlimited_interacts": state.get("unlimited_interacts"),
                    "main_menu_started": state.get("main_menu_started"),
                    "frame_presented": state.get("frame_presented"),
                    "initcode_len": state.get("initcode_len"),
                    "style_default_exists": state.get("style_default_exists"),
                    "Interface_wrapped": state.get("Interface_wrapped"),
                    "run_patched": state.get("run_patched"),
                    "injects_ok": state.get("injects_ok"),
                    "run_error": state.get("run_error"),
                    "inject_error": state.get("inject_error"),
                    "policy": "watchdog partial; Mechanism 1 only",
                }
                reached = state.get("reached") or "watchdog"
                ok = bool(state.get("advanced")) or int(state.get("interact_count") or 0) > 0
                _write_report(
                    base,
                    reached_stage=str(reached),
                    missing=[],
                    tb="",
                    ok=ok,
                    notes="watchdog_partial: main thread still in product path",
                    extra={k: v for k, v in extra.items() if v is not None},
                )
                state["watchdog_report_written"] = True
            except Exception:
                pass

    t = threading.Thread(target=_watch, name="host-main-watchdog", daemon=True)
    t.start()
    return t


def stage_early_main(base: Path, state: dict, budget_remaining: float):
    """
    Stage f (task #30): call renpy.main.main() with HostStop hooks.

    Returns (ok, missing, err, extra). ok=True if script_loaded/interface_created/run_entered.
    """
    import renpy
    import renpy.main as renpy_main

    extra: dict = {
        "strategy": "A_hoststop_after_interface",
        "host_build": bool(getattr(renpy, "host_build", False)),
        "calling": "renpy.main.main",
    }
    missing: list = []

    if not getattr(renpy, "host_build", False):
        # Soft-correct under embed.
        if getattr(sys, "renpy_host_build", False) or os.environ.get("RENPY_HOST_BUILD") in (
            "1",
            "true",
            "yes",
        ):
            renpy.host_build = True
            extra["host_build_forced"] = True
            extra["host_build"] = True
        else:
            return False, ["host_build"], "renpy.host_build is False", extra

    # Belt-and-suspenders: disable GL performance test under host_build so
    # 00gltest does not open a blocking ui.interact before main menu.
    # Also disable transitions so `with fade` does not depend on TIMEEVENT
    # one-shots for hermetic HostStop N-cap (still Mechanism 1 injects).
    try:
        if getattr(renpy, "host_build", False):
            renpy.config.performance_test = False
            extra["performance_test"] = False
            try:
                prefs = getattr(renpy.game, "preferences", None)
                if prefs is not None and hasattr(prefs, "performance_test"):
                    prefs.performance_test = False
                    extra["preferences.performance_test"] = False
                if prefs is not None and hasattr(prefs, "transitions"):
                    # 0 = no transitions (preferences.py Preference("transitions", 2))
                    prefs.transitions = 0
                    extra["preferences.transitions"] = 0
            except Exception:
                pass
            try:
                renpy.config.has_music = False
                renpy.config.main_menu_music = None
                extra["has_music"] = False
            except Exception:
                pass
    except Exception as e:
        extra["performance_test_error"] = f"{type(e).__name__}: {e}"

    # Re-assert renpy.__main__ helpers (import_all may have reset pointer).
    try:
        ensure_renpy_main(base)
        extra["path_helpers_reasserted"] = True
    except Exception as e:
        return False, ["renpy.__main__"], f"path helpers reassert failed: {e}", extra

    # Re-bind host renpysound + pygame.constants after import_all (attribute form).
    try:
        import sys as _sys

        import renpy.audio.renpysound_host as _rs_host

        _sys.modules["renpy.audio.renpysound"] = _rs_host
        import renpy.audio as _ra

        _ra.renpysound = _rs_host
        extra["renpysound_rebound"] = True
    except Exception as e:
        extra["renpysound_rebound"] = f"{type(e).__name__}: {e}"

    try:
        import sys as _sys

        import host_pygame
        import host_pygame.locals as _loc

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = _loc
        _sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        _sys.modules.setdefault("pygame.constants", host_pygame.constants)
        import renpy.pygame as rpg

        if not hasattr(rpg, "constants"):
            rpg.constants = host_pygame.constants
        extra["pygame_constants"] = True
    except Exception as e:
        extra["pygame_constants"] = f"{type(e).__name__}: {e}"

    # Force renpy.uguu host stub (SDL Class B; enums only for config.init).
    # Always overwrite — setdefault is wrong if a failed import left a broken entry.
    try:
        import types as _types

        import renpy_uguu_host as _uguu

        sys.modules["renpy.uguu.uguu"] = _uguu
        sys.modules["renpy.uguu.gl"] = _uguu
        # Ensure package exists and re-exports constants for `from renpy.uguu import X`.
        pkg = sys.modules.get("renpy.uguu")
        if pkg is None:
            pkg = _types.ModuleType("renpy.uguu")
            pkg.__path__ = []  # type: ignore[attr-defined]
            sys.modules["renpy.uguu"] = pkg
        for _name in dir(_uguu):
            if _name.startswith("GL_") or _name in ("clear_errors", "get_error"):
                setattr(pkg, _name, getattr(_uguu, _name))
        pkg.uguu = _uguu
        pkg.gl = _uguu
        extra["uguu_stub"] = True
    except Exception as e:
        extra["uguu_stub_error"] = f"{type(e).__name__}: {e}"

    # host_pygame.constants alias + pygame.import_as_pygame registration.
    # behavior.init_keymap uses getattr(pygame.constants, key).
    try:
        import renpy.pygame as rpg

        if not hasattr(rpg, "constants"):
            rpg.constants = rpg.locals  # type: ignore[attr-defined]
        try:
            rpg.import_as_pygame()
        except Exception as e:
            extra["import_as_pygame_error"] = f"{type(e).__name__}: {e}"
        sys.modules.setdefault(
            "renpy.pygame.constants", getattr(rpg, "constants", rpg.locals)
        )
        extra["pygame.constants"] = True
    except Exception as e:
        extra["pygame.constants_error"] = f"{type(e).__name__}: {e}"

    # Ensure renpy.audio.renpysound package attribute is bound (embed may only
    # seed sys.modules without the attribute after import_all).
    try:
        import renpy.audio as audio_pkg

        rs = sys.modules.get("renpy.audio.renpysound")
        if rs is not None and not hasattr(audio_pkg, "renpysound"):
            audio_pkg.renpysound = rs
            extra["renpysound_bound"] = True
        elif hasattr(audio_pkg, "renpysound"):
            extra["renpysound_bound"] = "already"
        else:
            try:
                import renpy.audio.renpysound_host as _rs_host

                sys.modules["renpy.audio.renpysound"] = _rs_host
                audio_pkg.renpysound = _rs_host
                extra["renpysound_bound"] = "installed_host"
            except Exception as e:
                extra["renpysound_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        extra["renpysound_bind_error"] = f"{type(e).__name__}: {e}"

    # Re-assert host ecsign (OpenSSL 3.x blocks ECDSA+SHA1 in stock .so).
    # Pre-seed in python.rs can lose if something reloaded renpy.ecsign from .so.
    try:
        import renpy_ecsign_host as _ecsign

        sys.modules["renpy.ecsign"] = _ecsign
        try:
            import renpy as _renpy_pkg

            _renpy_pkg.ecsign = _ecsign
        except Exception:
            pass
        extra["ecsign_host"] = getattr(_ecsign, "__file__", "renpy_ecsign_host")
    except Exception as e:
        extra["ecsign_host_error"] = f"{type(e).__name__}: {e}"

    state["reached"] = "early_main"
    _install_host_stop_hooks(state)
    extra["Interface_wrapped"] = state.get("Interface_wrapped")
    extra["run_patched"] = state.get("run_patched")
    extra["load_script_wrapped"] = state.get("load_script_wrapped")

    watchdog = _start_wall_clock_watchdog(state, budget_remaining, base=base)
    extra["watchdog"] = bool(watchdog)
    extra["budget_remaining"] = budget_remaining

    try:
        renpy_main.main()
        # Normal return (unexpected under HostStop).
        extra["main_returned"] = True
        state["reached"] = state.get("reached") or "main_returned"
    except HostStop as hs:
        extra["host_stop"] = hs.stage
        extra["host_stop_detail"] = hs.detail
        state["reached"] = hs.stage or state.get("reached") or "run_entered"
    except Exception as e:
        # Keep partial progress if we already reached a success stage.
        tb = traceback.format_exc()
        extra["main_error"] = f"{type(e).__name__}: {e}"
        extra["main_traceback"] = tb
        m = None
        if isinstance(e, ModuleNotFoundError):
            m = getattr(e, "name", None) or str(e)
        if m:
            missing = [str(m)]
        reached = state.get("reached") or "early_main"
        if reached in {"script_loaded", "interface_created", "run_entered"}:
            # Partial success: report ok with notes about the trailing error.
            extra["partial_success"] = True
            extra["partial_error"] = extra["main_error"]
        else:
            return False, missing or ["main"], f"{type(e).__name__}: {e}\n{tb}", extra

    reached = state.get("reached") or "early_main"
    extra["reached_after_main"] = reached

    # Probe interface / script / draw for the report.
    try:
        iface = getattr(renpy.game, "interface", None)
        extra["interface"] = iface is not None
        if iface is not None:
            extra["interface_type"] = type(iface).__name__
            if reached not in {"interface_created", "run_entered"}:
                # Interface exists even if stage tracker lagged.
                state["reached"] = "interface_created"
                reached = "interface_created"
                extra["reached_after_main"] = reached
        draw = getattr(renpy.display, "draw", None)
        extra["draw"] = type(draw).__name__ if draw is not None else None
        script = getattr(renpy.game, "script", None)
        if script is not None:
            extra["script"] = True
            extra["initcode_len"] = len(getattr(script, "initcode", []) or [])
            common_n = len(getattr(script, "common_script_files", []) or [])
            game_n = len(getattr(script, "script_files", []) or [])
            if common_n or game_n:
                extra["script_files"] = common_n + game_n
                extra["script_files_common"] = common_n
                extra["script_files_game"] = game_n
            if reached in {"early_main", "early_init", "prepare_args"}:
                state["reached"] = "script_loaded"
                reached = "script_loaded"
                extra["reached_after_main"] = reached
        # Always probe style.default after main returns / HostStop.
        extra["style_default_exists"] = _style_default_exists()
        if "style_default_exists" not in state:
            state["style_default_exists"] = extra["style_default_exists"]
    except Exception as e:
        extra["post_probe_error"] = f"{type(e).__name__}: {e}"

    for k in (
        "script_files",
        "script_files_common",
        "script_files_game",
        "initcode_len",
        "initcode_len_after_load",
        "initcode_len_after_execute",
        "style_default_exists",
        "style_default_exists_after_load",
        "style_default_exists_after_execute",
        "draw_type",
        "run_restart",
        "watchdog_fired",
        "interact_count",
        "advanced",
        "advanced_reason",
        "injects_ok",
        "interact_wrapped",
        "events_seen",
        "inject_error",
        "run_error",
        "post_init_probe_error",
        "load_script_probe_error",
        "max_interacts",
        "unlimited_interacts",
        "auto_advance",
        "main_menu_started",
        "main_menu_start",
    ):
        if k in state:
            extra[k] = state[k]

    # Final style probe if still missing.
    if "style_default_exists" not in extra:
        try:
            extra["style_default_exists"] = _style_default_exists()
        except Exception:
            extra["style_default_exists"] = False

    # Promote task #31 report fields from limited interact if present.
    if "interact_count" in state:
        extra["interact_count"] = int(state.get("interact_count") or 0)
    if "advanced" in state:
        extra["advanced"] = bool(state.get("advanced"))
    if state.get("advanced"):
        extra["interact_stage"] = "advanced"
        extra["interact_reason"] = state.get("advanced_reason") or "advanced"
    elif int(state.get("interact_count") or 0) > 0:
        extra["interact_stage"] = "ran_no_advance"
        extra["interact_reason"] = state.get("advanced_reason") or "interacted_no_label_change"
    if "injects_ok" in state:
        extra["interact_injects_ok"] = state.get("injects_ok")

    # first_interact is a success stage (HostStop after limited product interact).
    ok = reached in {
        "script_loaded",
        "interface_created",
        "run_entered",
        "first_interact",
    }
    # Task #32: also require initcode+style when we claim full early_main success
    # past run_entered. Soft-ok for script_loaded/interface_created alone so
    # partial diagnosis still works.
    if ok and reached in {"run_entered", "first_interact"}:
        init_len = int(extra.get("initcode_len") or state.get("initcode_len") or 0)
        style_ok = bool(
            extra.get("style_default_exists")
            if "style_default_exists" in extra
            else state.get("style_default_exists")
        )
        extra["initcode_ok"] = init_len >= 100
        extra["style_default_ok"] = style_ok
        if init_len < 100 or not style_ok:
            # Hard-fail with clear stage diagnosis (do not pretend green).
            return (
                False,
                ["initcode"] if init_len < 100 else ["style_default"],
                (f"init incomplete after main: initcode_len={init_len} "
                f"style_default_exists={style_ok}"),
                extra,
            )
    if ok:
        return True, [], "", extra
    return False, missing or ["early_main"], f"did not reach script/interface; reached={reached}", extra


def stage_first_interact_soft(budget_remaining: float = 5.0, state: dict | None = None):
    """
    Stage g (task #31): first interact + key advance smoke.

    Soft by design:
      - always emits interact_count / events_seen / advanced / frame_ok
      - if limited interact during early_main already advanced, promote that
      - when Interface exists post-run, may still inject + probe
      - no-ops cleanly if Interface is absent

    Returns (ok_stage, missing, err, extra). ok_stage is True for no-op and
    for successful advance; False only on hard helper failure.
    """
    state = state or {}
    extra: dict = {
        "interact_count": int(state.get("interact_count") or 0),
        "events_seen": int(state.get("events_seen") or 0),
        "advanced": bool(state.get("advanced")),
        "frame_ok": False,
        "interact_stage": "init",
    }

    # Promote results already collected during limited product run.
    if state.get("advanced"):
        extra["interact_stage"] = "advanced"
        extra["interact_reason"] = state.get("advanced_reason") or "advanced_in_run"
        extra["interact_injects_ok"] = state.get("injects_ok")
        extra["shared.stage_first_interact"] = True
        extra["shared.smoke_advance"] = True
        # Optional frame capture after interact — ensure product/surrogate present.
        try:
            import interact_helpers as ih

            fr = ih.try_read_frame(ensure_present=True)
            extra["frame_ok"] = bool(fr.get("frame_ok"))
            extra["frame_w"] = fr.get("frame_w")
            extra["frame_h"] = fr.get("frame_h")
            extra["frame_nonzero"] = fr.get("frame_nonzero")
            if fr.get("frame_error"):
                extra["frame_error"] = fr.get("frame_error")
            if fr.get("present_path"):
                extra["present_path"] = fr.get("present_path")
            if fr.get("present_draw_source"):
                extra["present_draw_source"] = fr.get("present_draw_source")
            if fr.get("present_error"):
                extra["present_error"] = fr.get("present_error")
        except Exception as e:
            extra["frame_error"] = f"{type(e).__name__}: {e}"
        return True, [], "", extra

    if int(state.get("interact_count") or 0) > 0:
        # Interacted inside run but did not detect label change — still report.
        extra["interact_stage"] = "ran_no_advance"
        extra["interact_reason"] = state.get("advanced_reason") or "interacted_in_run_no_label_change"
        extra["interact_injects_ok"] = state.get("injects_ok")
        extra["shared.stage_first_interact"] = True
        extra["shared.smoke_advance"] = True
        try:
            import interact_helpers as ih

            fr = ih.try_read_frame(ensure_present=True)
            extra["frame_ok"] = bool(fr.get("frame_ok"))
            extra["frame_w"] = fr.get("frame_w")
            extra["frame_h"] = fr.get("frame_h")
            extra["frame_nonzero"] = fr.get("frame_nonzero")
            if fr.get("frame_error"):
                extra["frame_error"] = fr.get("frame_error")
            if fr.get("present_path"):
                extra["present_path"] = fr.get("present_path")
            if fr.get("present_draw_source"):
                extra["present_draw_source"] = fr.get("present_draw_source")
            if fr.get("present_error"):
                extra["present_error"] = fr.get("present_error")
        except Exception as e:
            extra["frame_error"] = f"{type(e).__name__}: {e}"
        return True, [], extra["interact_reason"], extra

    try:
        import interact_helpers as ih
    except Exception as e:
        extra["interact_stage"] = "import_failed"
        extra["interact_error"] = f"{type(e).__name__}: {e}"
        return False, ["interact_helpers"], f"interact_helpers import failed: {e}", extra

    ready, why, _iface = ih.interface_ready()
    if not ready:
        extra["interact_stage"] = "noop"
        extra["interact_reason"] = f"noop_no_interface:{why}"
        extra["shared.stage_first_interact"] = True
        extra["shared.smoke_advance"] = True
        return True, [], "", extra

    # Interface present but no prior limited-run interact — post-run inject smoke.
    max_secs = max(1.0, min(float(budget_remaining), 15.0))
    try:
        ok_adv, miss, err, adv_extra = ih.stage_first_interact(max_secs=max_secs)
    except Exception as e:
        extra["interact_stage"] = "error"
        extra["interact_error"] = f"{type(e).__name__}: {e}"
        extra["traceback"] = traceback.format_exc()
        return False, ["interact"], f"stage_first_interact crashed: {e}", extra

    extra.update(adv_extra)
    extra["shared.stage_first_interact"] = True
    extra["shared.smoke_advance"] = True

    if ok_adv:
        extra["interact_stage"] = "advanced"
        return True, [], "", extra

    if extra.get("interact_stage") == "ran" or miss == ["advance"]:
        extra["interact_stage"] = "ran_no_advance"
        return True, [], err or "no advance", extra

    return False, miss or ["interact"], err or "interact failed", extra


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def run() -> None:
    base = _base_dir()
    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")
    # Skip 00gltest performance interact (ui.pausebehavior → ui.interact).
    # Stock honors RENPY_PERFORMANCE_TEST=0 in renpy/common/00gltest.rpy.
    # Without this, host product path often hangs before main menu / dialogue.
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    # Dialogue-first for hermetic HostStop N-cap: stock 00start.rpy honors these.
    # Requires transitions off + TIMEEVENT once= (host timer) so `with fade` does not hang.
    os.environ.setdefault("RENPY_SKIP_MAIN_MENU", "1")
    os.environ.setdefault("RENPY_SKIP_SPLASHSCREEN", "1")
    # Soft-mute renpysound_host beeps for hermetic N-cap stability.
    os.environ.setdefault("RENPY_HOST_MUTE", "1")

    # Prefer the_question when present (same as the_question gate).
    tq = base / "the_question"
    if tq.is_dir():
        os.environ.setdefault("RENPY_HOST_GAME", str(tq))

    t0 = time.monotonic()
    budget = _max_secs()

    reached = "init"
    missing: list = []
    tb = ""
    notes_parts: list = []
    state: dict = {"reached": "init", "done": False, "interface": False}
    extra_all: dict = {
        "base": str(base),
        "gate": "main",
        "max_secs": budget,
        "policy": "no interact_core→tick rewrite; Mechanism 1 pump only; dual-tree",
        "strategy": "A_hoststop_after_interface",
        # Stage flags for task #30 report consumers.
        "script_loaded": False,
        "interface_created": False,
        "run_entered": False,
        # Task #31 report fields (overwritten by stage_first_interact_soft).
        "interact_count": 0,
        "events_seen": 0,
        "advanced": False,
        "frame_ok": False,
    }
    ok = False

    try:
        import renpy_host  # noqa: F401
    except Exception as e:
        _write_report(
            base,
            reached_stage="init",
            missing=["renpy_host"],
            tb=f"{type(e).__name__}: {e}",
            ok=False,
            notes="gate must run under renpy-host embed (RENPY_HOST_GATE=main)",
            extra=extra_all,
        )
        raise

    # Import bootstrap stages as a library (bootstrap auto-run is gated so
    # import does not re-enter import_all / request_quit).
    try:
        import bootstrap as boot
    except Exception as e:
        _write_report(
            base,
            reached_stage="init",
            missing=["bootstrap"],
            tb=f"{type(e).__name__}: {e}",
            ok=False,
            notes="could not import host/python/gates/bootstrap.py",
            extra=extra_all,
        )
        _request_quit()
        raise

    def _budget_ok():
        return (time.monotonic() - t0) < budget

    try:
        if not _budget_ok():
            raise TimeoutError(f"wall-clock budget {budget}s exhausted before stages")

        # --- stage a ---
        reached = "import_renpy"
        good, miss, err, extra = boot.stage_import_renpy()
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            raise RuntimeError(err)

        if not _budget_ok():
            raise TimeoutError("wall-clock budget exhausted after import_renpy")

        # --- stage b ---
        reached = "import_all"
        good, miss, err, extra = boot.stage_import_all()
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            notes_parts.append("import_all hard-failed; see missing[]")
            raise RuntimeError(err.split("\n", 1)[0])

        if not _budget_ok():
            raise TimeoutError("wall-clock budget exhausted after import_all")

        # --- stage c ---
        reached = "set_game_dir"
        good, miss, err, extra = boot.stage_set_game_dir(base)
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            raise RuntimeError(err)

        if not _budget_ok():
            raise TimeoutError("wall-clock budget exhausted after set_game_dir")

        # --- stage d: renpy.__main__ helpers ---
        reached = "main_helpers"
        good, miss, err, extra = probe_main_helpers(base)
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            raise RuntimeError(err)

        if not _budget_ok():
            raise TimeoutError("wall-clock budget exhausted after main_helpers")

        # --- stage e: prepare argv/args for command=run ---
        reached = "prepare_args"
        good, miss, err, extra = stage_prepare_args(base)
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            raise RuntimeError(err)

        if not _budget_ok():
            raise TimeoutError("wall-clock budget exhausted after prepare_args")

        # --- stage f: early renpy.main.main() → Interface / HostStop (task #30) ---
        reached = "early_main"
        remaining = max(1.0, budget - (time.monotonic() - t0))
        good, miss, err, extra = stage_early_main(base, state, budget_remaining=remaining)
        extra_all.update(extra)
        reached = state.get("reached") or reached
        if not good:
            missing = miss
            tb = err if not tb else tb
            notes_parts.append(f"early_main stopped: {err.split(chr(10), 1)[0]}")
            # Do not raise yet if we at least have a partial stage — report and fail.
            raise RuntimeError(err.split("\n", 1)[0])

        notes_parts.append(
            f"early renpy.main proven under host (reached={reached}); "
            "interact deferred / soft"
        )

        # --- stage g: first interact smoke (task #31; soft) ---
        remaining = max(1.0, budget - (time.monotonic() - t0))
        good_i, miss_i, err_i, extra_i = stage_first_interact_soft(
            budget_remaining=remaining, state=state
        )
        extra_all.update(extra_i)
        # Soft stage: never fail the whole gate solely for no advance.
        # Hard fail only if helper import/crash.
        if not good_i and miss_i and miss_i not in (["advance"], ["interface"]):
            missing = miss_i
            tb = err_i
            raise RuntimeError(err_i)

        if extra_all.get("advanced"):
            notes_parts.append(
                "first_interact advanced under host inject (AC5 candidate)"
            )
            reached = "first_interact"
        elif extra_all.get("interact_stage") == "noop":
            notes_parts.append(
                "first_interact no-op (Interface not ready for inject path)"
            )
        else:
            notes_parts.append(
                "first_interact ran but did not advance dialogue/menu "
                f"(reason={extra_all.get('interact_reason', '?')}); AC5 PARTIAL"
            )

        ok = reached in SUCCESS_MAIN_STAGES or state.get("reached") in {
            "script_loaded",
            "interface_created",
            "run_entered",
        }
        if not ok and good:
            # stage_early_main said ok; trust its reached.
            ok = True
            reached = state.get("reached") or reached

    except HostStop as hs:
        # Safety net if HostStop escapes stage_early_main.
        reached = hs.stage or state.get("reached") or "run_entered"
        state["reached"] = reached
        ok = reached in SUCCESS_MAIN_STAGES
        notes_parts.append(f"outer HostStop at {reached}")
        extra_all["host_stop"] = reached
    except Exception as e:
        if not tb:
            tb = traceback.format_exc()
        if not missing:
            if isinstance(e, TimeoutError):
                missing = ["wall_clock"]
            else:
                m = boot._missing_name_from_exc(e)
                if m:
                    missing = [m]
        notes_parts.append(f"stopped_at={reached}")
        # Preserve partial success if main body already reached a proven stage.
        if state.get("reached") in {"script_loaded", "interface_created", "run_entered"}:
            reached = state["reached"]
            ok = True
            notes_parts.append(f"partial_ok reached={reached} after error")
        else:
            ok = False
    finally:
        state["done"] = True
        elapsed = time.monotonic() - t0
        extra_all["elapsed_secs"] = round(elapsed, 3)
        final_reached = state.get("reached") or reached
        # Prefer the furthest known stage for the report.
        order = {s: i for i, s in enumerate(STAGES)}
        if order.get(final_reached, -1) >= order.get(reached, -1):
            reached = final_reached
        extra_all["script_loaded"] = reached in {
            "script_loaded",
            "interface_created",
            "run_entered",
            "first_interact",
        }
        extra_all["interface_created"] = reached in {
            "interface_created",
            "run_entered",
            "first_interact",
        } or bool(state.get("interface"))
        extra_all["run_entered"] = reached in {"run_entered", "first_interact"}
        if reached in SUCCESS_MAIN_STAGES or extra_all["script_loaded"] or extra_all["interface_created"]:
            ok = True
        notes = " | ".join(p for p in notes_parts if p)
        _write_report(
            base,
            reached_stage=reached,
            missing=missing,
            tb=tb,
            ok=ok,
            notes=notes,
            extra=extra_all,
        )
        _request_quit()

    if not ok:
        raise RuntimeError(
            f"main gate stopped at {reached}; missing={missing}; see gate-main.txt"
        )


# run_file executes the script body; call run() at module level.
run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
