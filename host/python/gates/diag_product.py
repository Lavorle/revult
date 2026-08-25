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
outp = Path("/tmp/diag_product.txt")
out = outp.open("w")
def log(m):
    out.write(m + "\n"); out.flush(); print(m, flush=True)
try:
    log(f"path0={sys.path[:5]!r}")
    log(f"renpy pre={ 'renpy' in sys.modules }")
    import bootstrap as boot
    log("bootstrap imported")
    base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
    for name, call in [
        ("stage_import_renpy", lambda: boot.stage_import_renpy()),
        ("stage_import_all", lambda: boot.stage_import_all()),
        ("stage_set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ]:
        try:
            good, miss, err, extra = call()
            log(f"{name}: good={good} err={err!r} miss={miss} extra={ {k:extra.get(k) for k in list(extra)[:12]} }")
            if not good:
                break
        except Exception as e:  # noqa: BLE001
            log(f"{name} EXC {type(e).__name__}: {e}")
            log(traceback.format_exc())
            break
    import renpy
    log(f"renpy host_build={getattr(renpy,'host_build',None)} has_config={hasattr(renpy,'config')}")
    if hasattr(renpy, "config"):
        log(f"basedir={getattr(renpy.config,'basedir',None)} gamedir={getattr(renpy.config,'gamedir',None)}")
    else:
        try:
            import renpy.config
            log(f"manual import renpy.config ok has_attr_now={hasattr(renpy,'config')}")
        except Exception:  # noqa: BLE001
            log("manual import config FAIL\n"+traceback.format_exc())
except Exception:  # noqa: BLE001
    log("OUTER\n"+traceback.format_exc())
finally:
    out.close()
try:
    import renpy_host
    renpy_host.request_quit()
except Exception:  # noqa: BLE001, S110
    pass

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
