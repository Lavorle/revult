"""Bare-product path verification for dead-input (worker-5).

Does NOT claim V1 done from inject alone. Uses product-like path; inject is
supporting evidence for queue→poll only.
"""
import os
import sys
import traceback
from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

# Bind renpy.config BEFORE any print: host bootstrap partially imports renpy and
# renpy.log redirects stdout to StdoutRedirector which reads renpy.config.
import renpy  # noqa: F401
import renpy.config  # noqa: F401

OUT = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult")) / "host" / "target" / "gate-verify-dead-input.txt"
_lines = []


def log(msg):
    _lines.append(msg)
    try:
        sys.__stdout__.write("[verify-dead-input] %s\n" % msg)
        sys.__stdout__.flush()
    except Exception:
        pass


def finish(ok, **extra):
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with OUT.open("w") as f:
            f.write("ok=%s\n" % ok)
            for k, v in extra.items():
                f.write("%s=%s\n" % (k, v))
            f.write("--- log ---\n")
            for line in _lines:
                f.write(line + "\n")
    except Exception as e:
        try:
            sys.__stderr__.write("write report fail: %s\n" % e)
        except Exception:
            pass


def _request_quit():
    try:
        import renpy_host
        renpy_host.request_quit()
    except Exception:
        pass


def run():
    base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
    game = Path(os.environ.get("RENPY_HOST_GAME") or (base / "the_question"))
    os.environ.setdefault("RENPY_HOST_GAME", str(game))
    os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")
    os.environ.setdefault("RENPY_SKIP_SPLASHSCREEN", "0")
    os.environ.pop("RENPY_SKIP_MAIN_MENU", None)

    try:
        import renpy_host  # type: ignore
    except Exception as e:
        log("FATAL no renpy_host: %s" % e)
        finish(False, error=str(e))
        raise

    try:
        gates = str(base / "host" / "python" / "gates")
        if gates not in sys.path:
            sys.path.insert(0, gates)
        import bootstrap as boot

        for name, call in [
            ("import_renpy", boot.stage_import_renpy),
            ("import_all", boot.stage_import_all),
            ("set_game_dir", lambda: boot.stage_set_game_dir(base)),
        ]:
            good, miss, err, extra = call()
            log("stage %s good=%s err=%r" % (name, good, err))
            if not good:
                finish(False, stage=name, error=err, miss=miss)
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
            log("renpy_main_host installed")
        except Exception as e:
            log("renpy_main_host soft-fail: %s" % e)

        try:
            import renpy.audio.renpysound_host as _rs
            import renpy.audio as _ra
            _ra.renpysound = _rs
            sys.modules["renpy.audio.renpysound"] = _rs
            log("renpysound rebound")
        except Exception as e:
            log("renpysound soft-fail: %s" % e)

        try:
            import renpy.arguments
            # product uses custom prepare; keep minimal
            log("arguments module present")
        except Exception as e:
            log("args soft-fail: %s" % e)

        # Supporting inject (NOT V1 bare proof). Pre-queue Return for Start.
        try:
            for _ in range(3):
                renpy_host.inject_key(13, True, "\r")
                renpy_host.inject_key(13, False, "\r")
            log("pre-injected KEYDOWN/UP Return x3 (supporting only; V3)")
        except Exception as e:
            log("inject soft-fail: %s" % e)

        # Match product.py: prepare run args if available
        try:
            import product as product_mod
            if hasattr(product_mod, "_prepare_run_args"):
                args = product_mod._prepare_run_args(base)
                log("args command=%s" % getattr(args, "command", None))
            if hasattr(product_mod, "_pre_main_host_stubs"):
                product_mod._pre_main_host_stubs()
                log("pre_main_host_stubs done")
        except Exception as e:
            log("product helpers soft-fail: %s" % e)
            log(traceback.format_exc())

        import renpy.main as renpy_main
        log("entering renpy.main.main()")
        try:
            renpy_main.main()
            log("main returned")
        except BaseException as e:
            name = type(e).__name__
            if name in ("QuitException", "SystemExit", "HostStop"):
                log("quit via %s: %s" % (name, e))
            else:
                log("BaseException %s: %s" % (name, e))
                log(traceback.format_exc())
                finish(False, error="%s: %s" % (name, e))
                raise
        finish(True, note="main exited cleanly")
    except Exception as e:
        log("FATAL %s: %s" % (type(e).__name__, e))
        log(traceback.format_exc())
        finish(False, error="%s: %s" % (type(e).__name__, e))
        _request_quit()
        raise
    finally:
        _request_quit()


run()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
