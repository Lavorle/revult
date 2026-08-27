"""
AC5 product bootstrap probe under renpy-host embed.

Gate name: bootstrap  (RENPY_HOST_GATE=bootstrap)

Walks real Ren'Py entry as far as is safe under an already-running winit
loop (Mechanism 1). Does NOT rewrite interact_core to tick().

Structured report always written to:
  $RENPY_HOST_BASE/host/target/gate-bootstrap.txt

Stages (stop at first hard fail, still write report):
  a. import_renpy          — import renpy; host_build True
  b. import_all            — renpy.import_all() or progressive fallback
  c. set_game_dir          — point config at the_question (or RENPY_HOST_GAME)
  d. bootstrap_main        — bootstrap/main setup short of full interact

ok=True only if stages a–c complete and d reaches a non-interact ready
point. Day-one expected outcome with missing Cython .so: ok=False with
a missing=[] inventory — that is still useful AC5 progress.
"""

import os
import sys
import traceback
from pathlib import Path

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

STAGES = (
    "import_renpy",
    "import_all",
    "set_game_dir",
    "bootstrap_main",
)

# Ordered progressive import list mirroring renpy.import_all() as closely as
# practical. Used when a single import_all() fails so the report can name the
# first missing extension modules.
IMPORT_ALL_PROGRESSIVE = [
    "renpy.types",
    "renpy.error",
    "renpy.config",
    "renpy.log",
    "renpy.arguments",
    "renpy.compat.fixes",
    "renpy.display",
    "renpy.debug",
    "renpy.object",
    "renpy.game",
    "renpy.preferences",
    "renpy.loader",
    "renpy.importer",
    "renpy.pyanalysis",
    "renpy.astsupport",
    "renpy.parameter",
    "renpy.ast",
    "renpy.atl",
    "renpy.curry",
    "renpy.color",
    "renpy.easy",
    "renpy.encryption",
    "renpy.execution",
    "renpy.lexer",
    "renpy.loadsave",
    "renpy.savelocation",
    "renpy.savetoken",
    "renpy.persistent",
    "renpy.scriptedit",
    "renpy.parser",
    "renpy.performance",
    "renpy.pydict",
    "renpy.revertable",
    "renpy.rollback",
    "renpy.python",
    "renpy.script",
    "renpy.statements",
    "renpy.util",
    "renpy.versions",
    "renpy.styledata",
    "renpy.style",
    "renpy.substitutions",
    "renpy.translation",
    "renpy.display.position",
    "renpy.display.presplash",
    "renpy.display.pgrender",
    "renpy.display.scale",
    "renpy.display.module",
    "renpy.display.render",
    "renpy.display.displayable",
    "renpy.display.core",
    "renpy.display.scenelists",
    "renpy.display.swdraw",
    "renpy.text",
    "renpy.text.ftfont",
    "renpy.text.font",
    "renpy.text.textsupport",
    "renpy.text.texwrap",
    "renpy.text.text",
    "renpy.text.extras",
    "renpy.text.shader",
    "renpy.gl2",
    "renpy.display.layout",
    "renpy.display.viewport",
    "renpy.display.transform",
    "renpy.display.motion",
    "renpy.display.behavior",
    "renpy.display.transition",
    "renpy.display.movetransition",
    "renpy.display.im",
    "renpy.display.imagelike",
    "renpy.display.image",
    "renpy.display.video",
    "renpy.display.focus",
    "renpy.display.anim",
    "renpy.display.particle",
    "renpy.display.joystick",
    "renpy.display.controller",
    "renpy.display.minigame",
    "renpy.display.screen",
    "renpy.display.dragdrop",
    "renpy.display.imagemap",
    "renpy.display.predict",
    "renpy.display.emulator",
    "renpy.display.tts",
    "renpy.display.gesture",
    "renpy.display.model",
    "renpy.display.quaternion",
    "renpy.display.error",
    "renpy.audio",
    "renpy.audio.audio",
    "renpy.audio.music",
    "renpy.audio.sound",
    "renpy.audio.filter",
    "renpy.ui",
    "renpy.screenlang",
    "renpy.sl2",
    "renpy.sl2.slast",
    "renpy.sl2.slparser",
    "renpy.sl2.slproperties",
    "renpy.sl2.sldisplayables",
    "renpy.lint",
    "renpy.warp",
    "renpy.editor",
    "renpy.memory",
    "renpy.exports",
    "renpy.character",
    "renpy.add_from",
    "renpy.dump",
    "renpy.gl2.gl2draw",
    "renpy.gl2.gl2mesh",
    "renpy.gl2.gl2mesh2",
    "renpy.gl2.gl2mesh3",
    "renpy.gl2.gl2model",
    "renpy.gl2.gl2polygon",
    "renpy.gl2.gl2shader",
    "renpy.gl2.gl2texture",
    "renpy.gl2.live2d",
    "renpy.gl2.assimp",
    "renpy.minstore",
    "renpy.defaultstore",
    "renpy.test",
    "renpy.update",
    "renpy.update.deferred",
    "renpy.main",
]


def _base_dir() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    # walk up from cwd looking for renpy/ + host/README.md
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "renpy").is_dir() and (p / "host" / "README.md").is_file():
            return p
    return here


def _report_path(base: Path) -> Path:
    return base / "host" / "target" / "gate-bootstrap.txt"


def _write_report(
    base: Path,
    *,
    reached_stage: str,
    missing: list[str],
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


def resolve_game_dir(base: Path):
    """
    Game basedir resolution (aligned with host/README §4):
      1. RENPY_HOST_GAME env
      2. the_question under repo root
      3. tutorial under repo root
    Returns the basedir (parent of game/), or None.
    """
    candidates: list[Path] = []
    env = os.environ.get("RENPY_HOST_GAME")
    if env:
        candidates.append(Path(env).expanduser().resolve())
    candidates.append(base / "the_question")
    candidates.append(base / "tutorial")

    for c in candidates:
        if not c.exists():
            continue
        # Accept either the basedir (has game/) or the game/ dir itself.
        if (c / "game").is_dir():
            return c
        if c.name == "game" and c.is_dir():
            return c.parent
        if c.is_dir() and any(c.glob("*.rpy")):
            # bare game scripts dir
            return c
    return None


def _missing_name_from_exc(exc: BaseException):
    if isinstance(exc, ModuleNotFoundError):
        name = getattr(exc, "name", None)
        if name:
            return str(name)
        msg = str(exc)
        # "No module named 'foo'"
        if "No module named" in msg:
            return msg.split("No module named", 1)[-1].strip().strip("'\"")
    if isinstance(exc, ImportError):
        name = getattr(exc, "name", None)
        if name:
            return str(name)
    return None


def progressive_import_all():
    """
    Attempt each module in IMPORT_ALL_PROGRESSIVE.
    Returns (imported_ok, missing_or_failed, first_hard_error_msg).
    Continues after failures so the inventory is complete.
    """
    ok: list[str] = []
    missing: list[str] = []
    first_err = None
    for mod in IMPORT_ALL_PROGRESSIVE:
        try:
            __import__(mod)
            ok.append(mod)
        except Exception as e:
            mname = _missing_name_from_exc(e) or mod
            if mname not in missing:
                missing.append(mname)
            if first_err is None:
                first_err = f"{mod}: {type(e).__name__}: {e}"
    return ok, missing, first_err


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def stage_import_renpy():
    """Stage a: import renpy + host_build True."""
    extra: dict = {}
    try:
        import renpy
    except Exception as e:
        return False, [_missing_name_from_exc(e) or "renpy"], f"{type(e).__name__}: {e}", extra

    import renpy

    # Force-consistent with embed: python.rs sets sys.renpy_host_build=True.
    host_flag = bool(getattr(renpy, "host_build", False))
    sys_flag = bool(getattr(sys, "renpy_host_build", False))
    extra["renpy.host_build"] = host_flag
    extra["sys.renpy_host_build"] = sys_flag
    extra["renpy_version"] = getattr(renpy, "version", None) or getattr(renpy, "version_string", "?")

    if not host_flag:
        # Soft-correct under embed if flag drifted (should not happen).
        if sys_flag or os.environ.get("RENPY_HOST_BUILD", "") in ("1", "true", "yes"):
            renpy.host_build = True
            host_flag = True
            extra["renpy.host_build"] = True
            extra["host_build_forced"] = True
        else:
            return (
                False,
                ["host_build"],
                "renpy.host_build is False; host embed did not set sys.renpy_host_build",
                extra,
            )

    # Confirm renpy_host is present (embed-only).
    try:
        import renpy_host  # noqa: F401

        extra["renpy_host"] = True
    except Exception as e:
        return False, ["renpy_host"], f"renpy_host import failed: {e}", extra

    return True, [], "", extra


def _bind_host_display_accelerator(extra: dict | None = None) -> None:
    """Ensure renpy.display.accelerator package attribute is the host stub.

    Host embed seeds ``sys.modules['renpy.display.accelerator']`` before
    ``renpy.display`` exists, but attribute access
    ``renpy.display.accelerator.nogil_copy`` still fails unless the package
    attribute is bound. Classic accelerator is Cython (.pyx) and not built
    for host; without this bind, ``pgrender.copy_surface`` → AttributeError
    → ImageBase paints ``_image_error`` tiles for every present PNG.
    """
    import sys

    try:
        import renpy_display_accelerator_host as _acc  # type: ignore
    except Exception as e:
        if extra is not None:
            extra["accelerator_bind"] = f"import_fail:{type(e).__name__}"
        return

    sys.modules["renpy.display.accelerator"] = _acc
    try:
        import renpy.display as _rd  # type: ignore

        _rd.accelerator = _acc
        if extra is not None:
            extra["accelerator_bind"] = "ok"
            extra["accelerator_has_nogil_copy"] = hasattr(_acc, "nogil_copy")
    except Exception as e:
        if extra is not None:
            extra["accelerator_bind"] = f"attr_fail:{type(e).__name__}:{e}"


def _bind_host_gl2_mesh_modules(extra: dict | None = None) -> None:
    """Bind renpy.gl2.gl2mesh2 / gl2mesh3 package attributes for Model.render.

    ``renpy.gl2.__init__`` only TYPE_CHECKING-imports submodules (generated
    relative_imports). Product ``Model.render`` does
    ``renpy.gl2.gl2mesh2.Mesh2.texture_rectangle(...)`` via attribute access,
    which fails with AttributeError when the package attr is unbound — host
    ``RenderTransform`` then swallows the child render error and returns an
    empty Render. That kills HuangmeiC ``dissolve_transform`` (selected prefs
    top-nav wipe) and any other Model multi-tex path even though the .so
    imports fine via ``import renpy.gl2.gl2mesh2``.
    """
    import sys

    bound = []
    failed = []
    try:
        import renpy.gl2 as _gl2  # type: ignore
    except Exception as e:
        if extra is not None:
            extra["gl2_mesh_bind"] = f"gl2_import_fail:{type(e).__name__}:{e}"
        return

    for mod_name in ("gl2mesh2", "gl2mesh3", "gl2mesh", "gl2polygon"):
        full = f"renpy.gl2.{mod_name}"
        try:
            if full in sys.modules:
                mod = sys.modules[full]
            else:
                mod = __import__(full, fromlist=["*"])
            setattr(_gl2, mod_name, mod)
            bound.append(mod_name)
        except Exception as e:
            failed.append(f"{mod_name}:{type(e).__name__}")
            if extra is not None:
                extra[f"gl2_mesh_bind_{mod_name}"] = f"{type(e).__name__}:{e}"
    if extra is not None:
        extra["gl2_mesh_bind"] = "ok:{}".format(",".join(bound)) if bound else "none"
        if failed:
            extra["gl2_mesh_bind_failed"] = failed


def _patch_render_screen_tree_lock(extra: dict | None = None) -> None:
    """Wrap render_screen with the screen-tree RLock on host.

    ScreenDisplayable.update rebuilds MultiBox trees (SL _clear + re-add).
    Concurrent force-redraw / gate threads call render_screen mid-update and
    can snapshot Fixed children before background.png is re-added — that is the
    HuangmeiC 确认设置1/2 black panel residual. Holding the same reentrant lock
    used by ScreenDisplayable.update/render serializes tree mutation vs walk.
    """
    try:
        import renpy.display.render as rr  # type: ignore

        import renpy.display.screen as rs  # type: ignore
    except Exception as e:
        if extra is not None:
            extra["render_screen_lock"] = f"import_fail:{type(e).__name__}:{e}"
        return
    if getattr(rr.render_screen, "_host_tree_lock", False):
        if extra is not None:
            extra["render_screen_lock"] = "already"
        return
    lock_fn = getattr(rs, "_screen_tree_rlock", None)
    if lock_fn is None:
        if extra is not None:
            extra["render_screen_lock"] = "no_lock"
        return
    _orig = rr.render_screen

    def _locked_render_screen(root, width, height, *a, **k):
        with lock_fn():
            # Drain pending redraws under the same lock so MultiBox._clear
            # kill_cache + process_redraws cannot race a concurrent update.
            try:
                rr.process_redraws()
            except Exception:
                pass
            return _orig(root, width, height, *a, **k)

    _locked_render_screen._host_tree_lock = True  # type: ignore[attr-defined]
    _locked_render_screen._host_orig = _orig  # type: ignore[attr-defined]
    rr.render_screen = _locked_render_screen  # type: ignore[assignment]
    if extra is not None:
        extra["render_screen_lock"] = "ok"


def stage_import_all():
    """Stage b: renpy.import_all() with progressive fallback inventory."""
    import renpy

    extra: dict = {}
    try:
        renpy.import_all()
        extra["import_all"] = "full"
        # Host-local: bind accelerator package attr after import_all.
        _bind_host_display_accelerator(extra)
        _bind_host_gl2_mesh_modules(extra)
        _patch_render_screen_tree_lock(extra)
        return True, [], "", extra
    except Exception as e:
        full_tb = traceback.format_exc()
        first_missing = _missing_name_from_exc(e)
        extra["import_all"] = "failed"
        extra["import_all_error"] = f"{type(e).__name__}: {e}"
        # Progressive inventory — do not raise; report what is missing.
        ok_mods, missing, first_err = progressive_import_all()
        extra["progressive_ok_count"] = len(ok_mods)
        extra["progressive_ok_tail"] = ok_mods[-5:] if ok_mods else []
        if first_missing and first_missing not in missing:
            missing.insert(0, first_missing)
        # de-dupe preserve order
        seen: set[str] = set()
        uniq: list[str] = []
        for m in missing:
            if m not in seen:
                seen.add(m)
                uniq.append(m)
        # Still attempt accelerator bind on partial import so image load works.
        _bind_host_display_accelerator(extra)
        _bind_host_gl2_mesh_modules(extra)
        _patch_render_screen_tree_lock(extra)
        note = first_err or str(e)
        return False, uniq, note + "\n" + full_tb, extra


def stage_set_game_dir(base: Path):
    """Stage c: set renpy.config basedir/gamedir for the_question (or env)."""
    import renpy

    extra: dict = {}
    game_basedir = resolve_game_dir(base)
    if game_basedir is None:
        return (
            False,
            ["game_dir"],
            "no game dir: set RENPY_HOST_GAME or place the_question/ under repo root",
            extra,
        )

    gamedir = game_basedir / "game"
    if not gamedir.is_dir():
        # allow basedir that IS the game folder
        if any(game_basedir.glob("*.rpy")):
            gamedir = game_basedir
            game_basedir = game_basedir.parent
        else:
            return (
                False,
                ["game_dir"],
                f"game/ missing under {game_basedir}",
                extra,
            )

    # Config may already exist even if import_all only partially ran.
    try:
        import renpy.config
    except Exception as e:
        return False, ["renpy.config"], f"renpy.config unavailable: {e}", extra

    renpy.config.renpy_base = str(base)
    renpy.config.basedir = str(game_basedir)
    renpy.config.gamedir = str(gamedir)
    extra["basedir"] = str(game_basedir)
    extra["gamedir"] = str(gamedir)
    extra["renpy_base"] = str(base)

    # Ensure basedir on sys.path (bootstrap does this).
    bd = str(game_basedir)
    if bd not in sys.path:
        sys.path.insert(0, bd)

    return True, [], "", extra


def stage_bootstrap_main(base: Path):
    """
    Stage d: advance toward renpy.main / bootstrap as far as is safe.

    Under the host, the winit loop is already running and Python is pumped
    via about_to_wait → run_gate. Full renpy.bootstrap.bootstrap() ends in
    renpy.main.main() → interact, which is a multi-frame product loop that
    we must NOT rewrite to tick() on day one.

    Safe day-one steps:
      - ensure renpy.pygame is the host shim (already done by embed)
      - parse arguments with a synthetic argv for the game
      - if renpy.main is importable, call only pre-interact setup pieces
        that do not enter the interact loop
      - document hard stop before interact

    Returns ok=True only if we reach a documented pre-interact ready point.
    """
    import renpy

    extra: dict = {
        "interact": "not_attempted",
        "policy": "no interact_core→tick rewrite; Mechanism 1 pump only",
    }
    missing: list[str] = []

    # 1) pygame shim must be host_pygame.
    try:
        import renpy.pygame as rpg

        extra["renpy.pygame"] = getattr(rpg, "__name__", str(rpg))
        extra["renpy.pygame.file"] = getattr(rpg, "__file__", "?")
    except Exception as e:
        return False, ["renpy.pygame"], f"renpy.pygame import failed: {e}", extra

    # 2) Synthetic argv for renpy.arguments if basedir known.
    basedir = getattr(renpy.config, "basedir", None) or str(base / "the_question")
    # Preserve host binary path as argv[0]; supply basedir + run.
    argv0 = sys.argv[0] if sys.argv else "renpy-host"
    sys.argv = [argv0, basedir, "run"]
    extra["sys.argv"] = list(sys.argv)

    # 3) Try argument parse (does not need full import_all for ArgumentParser
    # construction, but post_init commands may).
    try:
        import renpy.arguments

        # bootstrap() of arguments expects commands registered; that happens
        # during import_all / main. If commands empty, take a soft path.
        if not getattr(renpy.arguments, "commands", None):
            extra["arguments"] = "commands_empty_pre_import_all"
        else:
            args = renpy.arguments.bootstrap()
            renpy.game.args = args
            extra["arguments"] = f"command={getattr(args, 'command', None)}"
    except SystemExit as e:
        # argparse --help etc.
        return False, ["arguments"], f"arguments SystemExit: {e}", extra
    except Exception as e:
        missing.append("renpy.arguments")
        extra["arguments_error"] = f"{type(e).__name__}: {e}"

    # 4) renpy.main availability — full main() is unsafe (enters interact).
    try:
        import renpy.main as renpy_main

        extra["renpy.main"] = "imported"
        # Probe for symbols without calling main().
        has_main = callable(getattr(renpy_main, "main", None))
        has_run = callable(getattr(renpy_main, "run", None))
        extra["renpy.main.main"] = has_main
        extra["renpy.main.run"] = has_run
    except Exception as e:
        m = _missing_name_from_exc(e) or "renpy.main"
        missing.append(m)
        return (
            False,
            missing,
            f"renpy.main not importable (expected until Cython subset built): {type(e).__name__}: {e}",
            extra,
        )

    # 5) Explicit hard-stop before interact. Full product playthrough is AC5
    # exit, not day-one gate requirement.
    extra["interact"] = "blocked_by_policy"
    extra["hard_stop"] = (
        "Full renpy.main.main()/interact not entered under host pump. "
        "Requires Cython host subset + WgpuDraw.draw_screen tree walk + "
        "Mechanism 1-compatible event_wait (already present). "
        "Do NOT rewrite interact_core to tick()."
    )

    # Stage d "success" means we reached the documented pre-interact ready
    # point with renpy.main importable. That only happens once import_all
    # fully works — callers gate on that.
    if missing:
        return False, missing, "pre-interact setup incomplete", extra
    return True, [], "", extra


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def run() -> None:
    base = _base_dir()
    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    # Help pure-python paths that check the env flag.
    os.environ.setdefault("RENPY_HOST_BUILD", "1")

    reached = "init"
    missing: list[str] = []
    tb = ""
    notes_parts: list[str] = []
    extra_all: dict = {"base": str(base)}
    ok = False

    try:
        import renpy_host  # embed-only; fails outside host
    except Exception as e:
        _write_report(
            base,
            reached_stage="init",
            missing=["renpy_host"],
            tb=f"{type(e).__name__}: {e}",
            ok=False,
            notes="gate must run under renpy-host embed (RENPY_HOST_GATE=bootstrap)",
        )
        # Outside embed there is no request_quit; re-raise for visibility.
        raise

    try:
        # --- stage a ---
        reached = "import_renpy"
        good, miss, err, extra = stage_import_renpy()
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            raise RuntimeError(err)

        # --- stage b ---
        reached = "import_all"
        good, miss, err, extra = stage_import_all()
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            notes_parts.append(
                "import_all hard-failed; progressive inventory in missing[]. "
                "Cython host subset (worker-ac5-cython) required before the_question advances."
            )
            raise RuntimeError(err.split("\n", 1)[0])

        # --- stage c ---
        reached = "set_game_dir"
        good, miss, err, extra = stage_set_game_dir(base)
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            raise RuntimeError(err)

        # --- stage d ---
        reached = "bootstrap_main"
        good, miss, err, extra = stage_bootstrap_main(base)
        extra_all.update(extra)
        if not good:
            missing = miss
            tb = err
            notes_parts.append(extra.get("hard_stop", ""))
            raise RuntimeError(err)

        # All stages cleared pre-interact.
        ok = True
        notes_parts.append(
            "pre-interact ready; full interact/playthrough deferred "
            "(no interact_core tick rewrite)."
        )
        reached = "bootstrap_main"

    except Exception as e:
        if not tb:
            tb = traceback.format_exc()
        if not missing:
            m = _missing_name_from_exc(e)
            if m:
                missing = [m]
        notes_parts.append(f"stopped_at={reached}")
        ok = False
    finally:
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
        try:
            import renpy_host

            renpy_host.request_quit()
        except Exception:
            pass

    # Gate script convention: raise on failure so Rust logs "Python gate failed"
    # but report file is already on disk (AC5 accepts ok=False with inventory).
    if not ok:
        raise RuntimeError(
            f"bootstrap gate stopped at {reached}; missing={missing}; see gate-bootstrap.txt"
        )


# run_file execs this source (not as module name "bootstrap"). Other gates may
# `import bootstrap` for stage helpers — skip auto-run in that case so we do
# not double-call renpy.import_all() / request_quit().
if globals().get("__name__", "__main__") != "bootstrap":
    run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
