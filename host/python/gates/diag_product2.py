import os
import sys
import traceback
from pathlib import Path

from host.python.gates._harness import gate_harness, parametrized_gate
out = open("/tmp/diag_product2.txt", "w")  # noqa: SIM115
def log(m):
    out.write(str(m) + "\n"); out.flush()
try:
    log(f"stdout type={type(sys.stdout)} repr={sys.stdout!r}")
    log(f"stderr type={type(sys.stderr)}")
    log(f"renpy pre={ 'renpy' in sys.modules}")
    if 'renpy' in sys.modules:
        r=sys.modules['renpy']
        log(f"pre has_config={hasattr(r,'config')} host_build={getattr(r,'host_build',None)}")
        log(f"pre modules with renpy: {[k for k in sys.modules if k.startswith('renpy')][:40]}")
    # Ensure config bound before any print
    import renpy
    import renpy.config
    log(f"after explicit config import has={hasattr(renpy,'config')}")
    log(f"stdout still {type(sys.stdout)}")
    import bootstrap as boot
    log("bootstrap imported")
    base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
    os.environ.setdefault("RENPY_HOST_GAME", str(base / "the_question"))
    for name, call in [
        ("stage_import_renpy", lambda: boot.stage_import_renpy()),
        ("stage_import_all", lambda: boot.stage_import_all()),
        ("stage_set_game_dir", lambda: boot.stage_set_game_dir(base)),
    ]:
        good, miss, err, extra = call()
        log(f"{name}: good={good} err={err!r} miss={miss}")
        log(f"  extra={extra}")
        if not good:
            break
    log(f"final has_config={hasattr(renpy,'config')} basedir={getattr(getattr(renpy,'config',None),'basedir',None)}")
except Exception:
    log("EXC\n"+traceback.format_exc())
finally:
    out.close()
try:
    import renpy_host
    renpy_host.request_quit()
except Exception:
    pass

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
