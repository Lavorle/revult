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
base = Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")
sys.path.insert(0, str(base/"host/python/gates"))
import bootstrap as boot

for call in (boot.stage_import_renpy, boot.stage_import_all):
    good, miss, err, extra = call()
    print("stage", good, err, miss)
import renpy

print("renpy.gl2", renpy.gl2, dir(renpy.gl2)[:40])
print("has gl2mesh2", hasattr(renpy.gl2, "gl2mesh2"))
try:
    import renpy.gl2.gl2mesh2 as m
    print("import ok", m, m.Mesh2)
except Exception:
    traceback.print_exc()
try:
    import renpy.gl2.gl2mesh as m2
    print("gl2mesh", m2, dir(m2)[:20])
except Exception:
    traceback.print_exc()
# package path
print("gl2 __path__", getattr(renpy.gl2, "__path__", None))
print("gl2 __file__", getattr(renpy.gl2, "__file__", None))
print("modules gl2*", [k for k in sys.modules if "gl2" in k])

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
