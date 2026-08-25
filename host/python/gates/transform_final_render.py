"""
Gate: host RenderTransform final_render stamps matrixcolor / shader / uniforms.

Gate name: transform_final_render  (RENPY_HOST_GATE=transform_final_render)

Proves GL2 final_render parity pieces used by HuangmeiC preferences:
  1. matrixcolor → renpy.matrixcolor + u_renpy_matrixcolor
  2. state.shader "image_dissolve" stamped
  3. ATL uniforms u_animation / u_transition stamped when registered
  4. alpha still stamped when < 1
  5. crop+zoom absolute ints still yield ~806×107 (no crop regression)

Note: no from __future__; host run_file prepends imports.
"""

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

_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-transform_final_render.txt"
out.parent.mkdir(parents=True, exist_ok=True)

notes = []
ok = True

# Ensure host accelerator is importable as renpy.display.accelerator.
sys.path.insert(0, str(Path(_base) / "host" / "python"))
try:
    import renpy_display_accelerator_host as _acc  # type: ignore

    sys.modules["renpy.display.accelerator"] = _acc
except Exception as e:
    notes.append(f"FAIL: import accelerator host: {type(e).__name__}: {e}")
    ok = False

# Minimal renpy.display.transform.uniforms so final_render loops them.
class _TFMod:
    uniforms = {"u_animation", "u_transition"}  # noqa: RUF012
    gl_properties = set()  # noqa: RUF012


sys.modules.setdefault(
    "renpy.display.transform",
    type(sys)("renpy.display.transform"),
)
_tf = sys.modules["renpy.display.transform"]
_tf.uniforms = {"u_animation", "u_transition"}
_tf.gl_properties = set()

# Fake Matrix-like object with fields WgpuDraw expects.
class _Mat:
    def __init__(self):
        # Identity-ish with a tint marker in xdx
        self.xdx = 2.0
        self.ydx = 0.0
        self.zdx = 0.0
        self.wdx = 0.0
        self.xdy = 0.0
        self.ydy = 1.0
        self.zdy = 0.0
        self.wdy = 0.0
        self.xdz = 0.0
        self.ydz = 0.0
        self.zdz = 1.0
        self.wdz = 0.0
        self.xdw = 0.0
        self.ydw = 0.0
        self.zdw = 0.0
        self.wdw = 1.0

    def __call__(self, *a, **k):
        return self


class _Child:
    def render(self, w, h, st, at):
        class _R:
            def __init__(self):
                self.width = float(w)
                self.height = float(h)
                self.children = []

        return _R()


class _State:
    def __init__(self, **kw):
        self.xsize = kw.get("xsize")
        self.ysize = kw.get("ysize")
        self.fit = kw.get("fit")
        self.zoom = kw.get("zoom", 1.0)
        self.xzoom = kw.get("xzoom", 1.0)
        self.yzoom = kw.get("yzoom", 1.0)
        self.rotate = kw.get("rotate")
        self.rotate_pad = kw.get("rotate_pad", True)
        self.crop = kw.get("crop")
        self.crop_relative = kw.get("crop_relative")
        self.corner1 = None
        self.corner2 = None
        self.alpha = kw.get("alpha", 1.0)
        self.additive = kw.get("additive", 0.0)
        self.subpixel = False
        self.matrixcolor = kw.get("matrixcolor")
        self.shader = kw.get("shader")
        self.nearest = kw.get("nearest")
        self.blend = kw.get("blend")
        self.maxsize = None
        self.u_animation = kw.get("u_animation")
        self.u_transition = kw.get("u_transition")
        # defaults for size path
        for k, v in kw.items():
            setattr(self, k, v)


class _Transform:
    def __init__(self, state, child=None):
        self.state = state
        self.child = child or _Child()
        self.child_st_base = 0
        self.offsets = []
        self.render_size = (0, 0)
        self.child_size = (0, 0)
        self.reverse = None


def _run_case(name, state_kw, checks):
    global ok
    from renpy_display_accelerator_host import RenderTransform  # type: ignore

    # Child size for crop cases
    class _SizedChild:
        def __init__(self, w, h):
            self.w = w
            self.h = h

        def render(self, w, h, st, at):
            class _R:
                def __init__(self, ww, hh):
                    self.width = float(ww)
                    self.height = float(hh)
                    self.children = []

            return _R(self.w, self.h)

    child = _SizedChild(1920, 1080) if "crop" in state_kw else _Child()
    st = _State(**state_kw)
    tr = _Transform(st, child=child)
    rt = RenderTransform(tr)
    try:
        # config.crop_relative_default for absolute crop path
        import types

        if "renpy" not in sys.modules:
            sys.modules["renpy"] = types.ModuleType("renpy")
        renpy = sys.modules["renpy"]
        if not hasattr(renpy, "config"):
            renpy.config = types.SimpleNamespace(crop_relative_default=True)
        else:
            renpy.config.crop_relative_default = True
        rv = rt.render(1920, 1080, 0.0, 0.0)
    except Exception as e:
        notes.append(f"{name}: FAIL render {type(e).__name__}: {e}")
        ok = False
        return

    shaders = tuple(getattr(rv, "shaders", None) or ())
    uniforms = dict(getattr(rv, "uniforms", None) or {})
    case_ok = True
    for label, pred in checks:
        try:
            good = bool(pred(rv, shaders, uniforms))
        except Exception as e:
            good = False
            notes.append(f"{name}.{label}: exc {type(e).__name__}:{e}")
        if not good:
            case_ok = False
            notes.append(
                f"{name}.{label}: FAIL shaders={shaders} uniforms_keys={list(uniforms.keys())} "
                f"size=({getattr(rv,'width',None)},{getattr(rv,'height',None)})"
            )
        else:
            notes.append(f"{name}.{label}: ok")
    if not case_ok:
        ok = False
    notes.append(f"{name}: ok={case_ok}")


# 1) ColorizeMatrix-like matrixcolor
_run_case(
    "matrixcolor",
    {"matrixcolor": _Mat()},
    [
        (
            "has_shader",
            lambda rv, sh, u: "renpy.matrixcolor" in sh,
        ),
        (
            "has_uniform",
            lambda rv, sh, u: "u_renpy_matrixcolor" in u,
        ),
    ],
)

# 2) dissolve_transform-like shader + uniforms
_run_case(
    "image_dissolve_uniforms",
    {
        "shader": "image_dissolve",
        "u_animation": 0.5,
        "u_transition": 0.2,
        "alpha": 1.0,
    },
    [
        ("shader", lambda rv, sh, u: "image_dissolve" in sh),
        ("u_animation", lambda rv, sh, u: abs(float(u.get("u_animation", -1)) - 0.5) < 1e-6),
        ("u_transition", lambda rv, sh, u: abs(float(u.get("u_transition", -1)) - 0.2) < 1e-6),
    ],
)

# 3) alpha stamp still works
_run_case(
    "alpha",
    {"alpha": 0.42},
    [
        ("shader", lambda rv, sh, u: "renpy.alpha" in sh),
        ("u_alpha", lambda rv, sh, u: abs(float(u.get("u_renpy_alpha", -1)) - 0.42) < 1e-6),
    ],
)

# 4) crop+zoom absolute under crop_relative_default (no regression)
_run_case(
    "crop_zoom",
    {
        "crop": (0, 825, 1920, 255),
        "crop_relative": None,  # use config default True
        "zoom": 0.42,
    },
    [
        (
            "size",
            lambda rv, sh, u: abs(float(rv.width) - 806.4) < 1.0
            and abs(float(rv.height) - 107.1) < 1.0,
        ),
    ],
)

lines = [
    "gate=transform_final_render",
    f"ok={ok}",
] + notes
out.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
sys.exit(0 if ok else 1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
