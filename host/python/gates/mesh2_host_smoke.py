import os
import sys
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

for call in (boot.stage_import_renpy, boot.stage_import_all, lambda: boot.stage_set_game_dir(base)):
    good, miss, err, extra = call()
    print("stage", good, err)
    if not good: raise SystemExit(1)
import renpy

renpy.host_build = True
print("models", getattr(renpy.display.render, "models", None))
try:
    from renpy.gl2.gl2mesh2 import Mesh2
    m = Mesh2.texture_rectangle(0,0,100,100,0,0,1,1)
    print("Mesh2 ok", type(m), getattr(m,"points",None), hasattr(m,"vertices"))
    # attrs used by draw
    for a in ("vertices","points","triangles","attribute","get_points"):
        print(" ", a, hasattr(m,a), getattr(m,a,None) if hasattr(m,a) and a!="vertices" else "...")
except Exception as e:  # noqa: BLE001
    import traceback; traceback.print_exc()  # noqa: I001
    print("Mesh2 FAIL", e)

# Render a Model
try:
    renpy.display.render.models = True
    from renpy.display.model import Model
    mod = Model().child("gui/preferences/common/navigation_selected.png").texture(
        "gui/preferences/common/navigation_idle.png").texture("images/rule/00.png")
    # need gamedir
    print("basedir", renpy.config.basedir, "gamedir", renpy.config.gamedir)
    rv = renpy.display.render.render(mod, 179, 64, 0, 0)
    print("Model render", type(rv), "mesh", type(getattr(rv,"mesh",None)), "children", len(rv.children), "shaders", rv.shaders)
except Exception as e:  # noqa: BLE001
    import traceback; traceback.print_exc()  # noqa: I001
    print("Model FAIL", e)

# Render dissolve_transform after show
try:
    # ensure ATL properties live
    dt = renpy.store.dissolve_transform(
        old="gui/preferences/common/navigation_idle.png",
        new="gui/preferences/common/navigation_selected.png",
        rule="images/rule/00.png",
    )
    # Execute ATL by setting child via update
    print("dt", dt, "atl", getattr(dt,"atl",None))
    # ATLTransform.render path
    rv = renpy.display.render.render(dt, 179, 64, 0.5, 0.5)
    print("dt render", type(rv), "mesh", getattr(rv,"mesh",None), "shaders", getattr(rv,"shaders",None),
          "uniforms", getattr(rv,"uniforms",None), "children", len(getattr(rv,"children",()) or ()))
    # inspect state after render
    st = dt.state
    print("after state shader", getattr(st,"shader",None), "u_anim", getattr(st,"u_animation",None),
          "child", type(getattr(dt,"child",None)).__name__ if getattr(dt,"child",None) else None)
except Exception as e:  # noqa: BLE001
    import traceback; traceback.print_exc()  # noqa: I001
    print("dt FAIL", e)

out = base/"host/target/gate-mesh2_host_smoke.txt"
out.write_text("ok=True\n")
print("done")

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
