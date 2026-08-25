"""
Interactive product-entry gate under renpy-host embed.

Gate name: product  (RENPY_HOST_GATE=product)

Closed product-entry contract (H1 / consensus-tq-gui-residual):
  RENPY_SKIP_MAIN_MENU     — leave unset / "0"; NEVER setdefault "1"
  RENPY_SKIP_SPLASHSCREEN  — setdefault "0"
  RENPY_PERFORMANCE_TEST   — setdefault "0" (required; hang without it)
  HostStop / interact N-cap — none; continuous until window quit
  Exit                     — blocks in renpy.main.main() until quit

Default selection (main.rs):
  When RENPY_HOST_GAME is set (env or argv) and RENPY_HOST_GATE is unset,
  renpy-host defaults to this gate. Explicit GATE always wins; no game → smoke.

Note: do NOT import main.py (it auto-runs at module level with HostStop N-cap
and SKIP_MAIN_MENU=1). Reuse bootstrap stages + renpy_main_host helpers only.
"""

import os
import sys
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
    except Exception:  # noqa: BLE001, S110
        pass


def _log(msg: str) -> None:
    # Host bootstrap may partially import renpy and install renpy.log StdoutRedirector
    # before renpy.config is bound. Prefer real stdout to avoid AttributeError on print.
    line = f"[product-gate] {msg}\n"
    try:
        sys.__stdout__.write(line)
        sys.__stdout__.flush()
    except Exception:  # noqa: BLE001
        try:
            print(line, end="", flush=True)
        except Exception:  # noqa: BLE001, S110
            pass


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
        except Exception:  # noqa: BLE001, S110
            pass
    args = renpy.arguments.bootstrap()
    renpy.game.args = args
    return args


def _pre_main_host_stubs() -> None:
    """Host rebinds required before renpy.main.main() (uguu/ecsign/sound/pygame)."""
    try:
        import renpy.audio as _ra
        import renpy.audio.renpysound_host as _rs_host

        sys.modules["renpy.audio.renpysound"] = _rs_host
        _ra.renpysound = _rs_host
        _log("renpysound rebound to host")
    except Exception as e:  # noqa: BLE001
        _log(f"renpysound rebound soft-fail: {e}")

    try:
        import host_pygame
        import host_pygame.locals as _loc
        import host_pygame.scrap as _host_scrap

        if not hasattr(host_pygame, "constants"):
            host_pygame.constants = _loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules.setdefault("pygame.constants", host_pygame.constants)
        # S-key screenshot → put_clipboard_image_file → scrap.put_data.
        # Cython scrap needs SDL; rebind host no-op scrap so maximize+S does not
        # AttributeError/wedge the interact loop.
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
        _log("host scrap rebound (clipboard no-op)")
    except Exception as e:  # noqa: BLE001
        _log(f"pygame.constants soft-fail: {e}")

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
        _log("uguu host stub installed")
    except Exception as e:  # noqa: BLE001
        _log(f"uguu stub soft-fail: {type(e).__name__}: {e}")

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


def run() -> None:
    base = _base_dir()
    os.environ.setdefault("RENPY_HOST_BASE", str(base))
    os.environ.setdefault("RENPY_HOST_BUILD", "1")

    # Closed product contract — do NOT setdefault SKIP_MAIN_MENU=1 (main.py does).
    # 00start.rpy tests environ.get with Python truthiness: any non-empty string
    # (including "0") skips the main menu / splash. Normalize falsey values away.
    def _clear_falsey_skip(name: str) -> None:
        val = os.environ.get(name)
        if val is None:
            return
        if str(val).strip().lower() in ("", "0", "false", "no", "off", "n"):
            os.environ.pop(name, None)

    _clear_falsey_skip("RENPY_SKIP_MAIN_MENU")
    _clear_falsey_skip("RENPY_SKIP_SPLASHSCREEN")
    # Required: without this, host product path often hangs in 00gltest performance interact.
    # Unlike SKIP_*, this var is int()'d (0 = skip performance test) — "0" is correct.
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")

    # Prefer the_question when present and no game was supplied (env/argv).
    tq = base / "the_question"
    if tq.is_dir():
        os.environ.setdefault("RENPY_HOST_GAME", str(tq))

    gates = base / "host" / "python" / "gates"
    if str(gates) not in sys.path:
        sys.path.insert(0, str(gates))

    skip_mm = os.environ.get("RENPY_SKIP_MAIN_MENU")
    _log(
        f"base={base} game={os.environ.get('RENPY_HOST_GAME')} "
        f"SKIP_MAIN_MENU={skip_mm!r} "
        f"SKIP_SPLASHSCREEN={os.environ.get('RENPY_SKIP_SPLASHSCREEN')!r} "
        f"PERFORMANCE_TEST={os.environ.get('RENPY_PERFORMANCE_TEST')!r}"
    )
    if skip_mm is not None:
        _log(
            "WARN: RENPY_SKIP_MAIN_MENU is set; product default is unset "
            "(enter main menu). Continuing with operator override."
        )

    try:
        import renpy_host  # noqa: F401
    except Exception as e:
        _log(f"FATAL no renpy_host: {e}")
        raise RuntimeError(
            "product gate must run under renpy-host embed (RENPY_HOST_GATE=product)"
        ) from e

    try:
        import bootstrap as boot
    except Exception as e:
        _log(f"FATAL import bootstrap: {e}")
        _request_quit()
        raise

    try:
        good, miss, err, extra = boot.stage_import_renpy()
        if not good:
            raise RuntimeError(f"import_renpy: {err} missing={miss}")
        _log("stage import_renpy ok")

        good, miss, err, extra = boot.stage_import_all()
        if not good:
            raise RuntimeError(f"import_all: {err} missing={miss}")
        _log(f"stage import_all ok import_all={extra.get('import_all')}")

        good, miss, err, extra = boot.stage_set_game_dir(base)
        if not good:
            raise RuntimeError(f"set_game_dir: {err} missing={miss}")
        _log(f"stage set_game_dir ok basedir={extra.get('basedir')}")

        import renpy

        if not getattr(renpy, "host_build", False):
            renpy.host_build = True

        main_mod, have, how = _ensure_renpy_main(base)
        _log(f"path helpers {how} have={have}")

        basedir = getattr(renpy.config, "basedir", None) or str(
            os.environ.get("RENPY_HOST_GAME") or (base / "the_question")
        )
        renpy.config.renpy_base = getattr(renpy.config, "renpy_base", None) or str(base)
        try:
            logdir = main_mod.path_to_logdir(basedir)
            renpy.config.logdir = logdir
            os.makedirs(logdir, 0o777, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            _log(f"logdir soft-fail: {e}")

        args = _prepare_run_args(base)
        _log(f"args command={getattr(args, 'command', None)}")
        try:
            renpy.importer.init_importer()
        except Exception as e:  # noqa: BLE001
            _log(f"importer soft-fail: {e}")

        _pre_main_host_stubs()

        # Belt-and-suspenders for 00gltest hang (env already set; also force config).
        try:
            renpy.config.performance_test = False
        except Exception:  # noqa: BLE001, S110
            pass

        # Temporary D4 loader probe (Slice 0). Gated by RENPY_HOST_LOADER_PROBE=1.
        # Logs basedir/gamedir/searchpath + loadable/transpath/isfile once after index_files.
        if os.environ.get("RENPY_HOST_LOADER_PROBE") in ("1", "true", "yes", "on"):
            import renpy.loader as _loader

            _orig_index = _loader.index_files
            _probe_done = {"v": False}

            def _index_files_probe():
                _orig_index()
                if _probe_done["v"]:
                    return
                _probe_done["v"] = True
                try:
                    import renpy as _r

                    basedir = getattr(_r.config, "basedir", None)
                    gamedir = getattr(_r.config, "gamedir", None)
                    searchpath = list(getattr(_r.config, "searchpath", []) or [])
                    commondir = getattr(_r.config, "commondir", None)
                    renpy_base = getattr(_r.config, "renpy_base", None)
                    target = "gui/main_menu.png"
                    isfile = False
                    if gamedir:
                        isfile = os.path.isfile(os.path.join(gamedir, target))
                    entries = []
                    for d in searchpath:
                        isabs = os.path.isabs(d)
                        joined = os.path.join(basedir or "", d)
                        walk = d if isabs else joined
                        has = os.path.isfile(os.path.join(walk, target))
                        entries.append(
                            {
                                "raw": d,
                                "isabs": isabs,
                                "join_basedir": joined,
                                "walk_root": walk,
                                "contains_main_menu": has,
                                "walk_exists": os.path.isdir(walk),
                            }
                        )
                    try:
                        tp = _loader.transpath(target)
                    except Exception as e:  # noqa: BLE001
                        tp = f"ERR {type(e).__name__}: {e}"
                    try:
                        loadable = _loader.loadable(target)
                    except Exception as e:  # noqa: BLE001
                        loadable = f"ERR {type(e).__name__}: {e}"
                    # game_files membership for the asset
                    try:
                        gf_hit = any(
                            fn == target or fn.endswith("/" + target)
                            for _d, fn in getattr(_loader, "game_files", [])
                        )
                        gf_count = len(getattr(_loader, "game_files", []) or [])
                        cf_count = len(getattr(_loader, "common_files", []) or [])
                        lm_has = target.lower() in {
                            k.lower() for k in (getattr(_loader, "lower_map", {}) or {})
                        }
                    except Exception as e:  # noqa: BLE001
                        gf_hit, gf_count, cf_count, lm_has = f"ERR {e}", -1, -1, False
                    _log(
                        "LOADER_PROBE "
                        f"renpy_base={renpy_base!r} basedir={basedir!r} "
                        f"gamedir={gamedir!r} commondir={commondir!r}"
                    )
                    _log(f"LOADER_PROBE searchpath={searchpath!r}")
                    for i, e in enumerate(entries):
                        _log(f"LOADER_PROBE searchpath[{i}]={e}")
                    _log(
                        "LOADER_PROBE "
                        f"isfile(join(gamedir,{target!r}))={isfile} "
                        f"transpath={tp!r} loadable={loadable!r} "
                        f"game_files_hit={gf_hit} game_files_n={gf_count} "
                        f"common_files_n={cf_count} lower_map_has={lm_has}"
                    )
                    # Slice1 runtime open/load probe
                    try:
                        of = _loader.open_file
                        _log(
                            "LOADER_PROBE open_file "
                            f"type={type(of)!r} module={getattr(of,'__module__',None)!r} "
                            f"name={getattr(of,'__name__',None)!r}"
                        )
                        if tp and not str(tp).startswith("ERR"):
                            s = of(tp, "rb")
                            hdr = s.read(8)
                            sz = s.seek(0, 2)
                            s.close()
                            _log(f"LOADER_PROBE open_file(tp,'rb') hdr={hdr!r} size={sz}")
                        try:
                            lf = _loader.load(target)
                            data = lf.read(8)
                            lf.close()
                            _log(f"LOADER_PROBE load({target!r}) ok hdr={data!r} type={type(lf)!r}")
                        except Exception as e:  # noqa: BLE001
                            _log(f"LOADER_PROBE load({target!r}) FAIL {type(e).__name__}: {e}")
                            import traceback as _tb
                            print(_tb.format_exc(), flush=True)
                        try:
                            lf2 = _loader.load(target, directory="images")
                            data2 = lf2.read(8)
                            lf2.close()
                            _log(f"LOADER_PROBE load(dir=images) ok hdr={data2!r}")
                        except Exception as e:  # noqa: BLE001
                            _log(f"LOADER_PROBE load(dir=images) FAIL {type(e).__name__}: {e}")
                        # Full Image/pgrender decode path (post-open residual probe)
                        try:
                            import renpy.display.pgrender as _pgr
                            lf3 = _loader.load(target, directory="images")
                            with lf3 as f3:
                                surf = _pgr.load_image(f3, target)
                            sz = surf.get_size() if surf is not None else None
                            px0 = None
                            try:
                                px0 = surf.get_at((0, 0)) if surf is not None else None
                            except Exception as e2:  # noqa: BLE001
                                px0 = f"get_at ERR {type(e2).__name__}: {e2}"
                            _log(
                                f"LOADER_PROBE pgrender.load_image ok size={sz!r} "
                                f"type={type(surf)!r} px0={px0!r}"
                            )
                        except Exception as e:  # noqa: BLE001
                            _log(
                                f"LOADER_PROBE pgrender.load_image FAIL "
                                f"{type(e).__name__}: {e}"
                            )
                            import traceback as _tb
                            print(_tb.format_exc(), flush=True)
                        try:
                            import renpy.display.im as _im
                            img = _im.Image(target)
                            surf2 = img.load()
                            sz2 = surf2.get_size() if surf2 is not None else None
                            mean_s = None
                            try:
                                # sample a few pixels for outdoor-scene mean signal
                                samples = [
                                    surf2.get_at((sz2[0] // 2, sz2[1] // 2)),
                                    surf2.get_at((10, 10)),
                                    surf2.get_at((sz2[0] - 10, sz2[1] - 10)),
                                ]
                                mean_s = tuple(
                                    sum(s[i] for s in samples) // len(samples)
                                    for i in range(3)
                                )
                            except Exception as e3:  # noqa: BLE001
                                mean_s = f"sample ERR {type(e3).__name__}: {e3}"
                            _log(
                                f"LOADER_PROBE im.Image.load ok size={sz2!r} "
                                f"type={type(surf2)!r} sample_rgb={mean_s!r}"
                            )
                        except Exception as e:  # noqa: BLE001
                            _log(
                                f"LOADER_PROBE im.Image.load FAIL "
                                f"{type(e).__name__}: {e}"
                            )
                            import traceback as _tb
                            print(_tb.format_exc(), flush=True)
                    except Exception as e:  # noqa: BLE001
                        _log(f"LOADER_PROBE open/load probe FAIL {type(e).__name__}: {e}")
                        import traceback as _tb
                        print(_tb.format_exc(), flush=True)
                    # POSIX absolute-join falsifier
                    if basedir and searchpath:
                        for d in searchpath:
                            if os.path.isabs(d):
                                j = os.path.join(basedir, d)
                                _log(
                                    "LOADER_PROBE abs_join_falsifier "
                                    f"basedir={basedir!r} d={d!r} "
                                    f"join={j!r} equal_d={j == d} "
                                    f"isdir_join={os.path.isdir(j)} isdir_d={os.path.isdir(d)}"
                                )
                except Exception as e:  # noqa: BLE001
                    _log(f"LOADER_PROBE FAILED {type(e).__name__}: {e}")
                    print(traceback.format_exc(), flush=True)

            _loader.index_files = _index_files_probe  # type: ignore[assignment]
            _log("LOADER_PROBE installed (wraps index_files)")

        # No HostStop hooks, no interact N-cap — continuous product until quit.
        import renpy.main as renpy_main

        _log("entering renpy.main.main() (continuous product; quit via window)")
        try:
            renpy_main.main()
            _log("renpy.main.main() returned")
        except SystemExit as se:
            _log(f"SystemExit {se}")
        except BaseException as e:
            # Product control / quit paths sometimes surface as BaseException.
            name = type(e).__name__
            if name in ("QuitException", "SystemExit"):
                _log(f"product quit via {name}: {e}")
            else:
                _log(f"product BaseException: {name}: {e}")
                tb = traceback.format_exc()
                print(tb, flush=True)
                raise
    except Exception as e:
        _log(f"FATAL {type(e).__name__}: {e}")
        print(traceback.format_exc(), flush=True)
        _request_quit()
        raise
    finally:
        # Window close / main return → signal host event loop to exit.
        _request_quit()


# run_file executes the script body; call run() at module level.
run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
