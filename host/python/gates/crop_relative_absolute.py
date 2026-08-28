"""Gate: absolute-pixel crop under crop_relative_default=True (text_config).

Product text_config uses:
  crop (0, 825, 1920, 255)   # ints = absolute pixels
  zoom 0.42

Ren'Py 8.x sets config.crop_relative_default = True. GL2 relative_for_crop
keeps ints as pixels and only multiplies floats by child size. Host must match:
multiplying ints by child size balloons the intermediate clip and paints the
full street scene over the preferences panel (user Image #1).

Gate name: crop_relative_absolute  (RENPY_HOST_GATE=crop_relative_absolute)

Pure unit test of renpy_display_accelerator_host.RenderTransform — does not
need GPU. Avoids stomping the real renpy package; only sets config attrs.
"""

import os
import sys
import types
from pathlib import Path


_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
for p in (_base, str(Path(_base) / "host" / "python")):
    if p not in sys.path:
        sys.path.insert(0, p)

# Host already seeds renpy; ensure config has crop_relative_default.
try:
    import renpy  # type: ignore
except Exception:
    renpy = types.ModuleType("renpy")
    sys.modules["renpy"] = renpy

if not hasattr(renpy, "config") or renpy.config is None:
    renpy.config = types.SimpleNamespace()
# Force the product default for this gate.
renpy.config.crop_relative_default = True
if not hasattr(renpy.config, "limit_transform_crop"):
    renpy.config.limit_transform_crop = False
if not hasattr(renpy.config, "relative_transform_size"):
    renpy.config.relative_transform_size = False
if not hasattr(renpy.config, "zoom_zaxis"):
    renpy.config.zoom_zaxis = False
if not hasattr(renpy.config, "log_to_stdout"):
    renpy.config.log_to_stdout = False

# Seed display.render / matrix if missing (gate may run before full bootstrap).
for name in (
    "renpy.display",
    "renpy.display.render",
    "renpy.display.matrix",
    "renpy.display.position",
    "renpy.display.core",
):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)

# Attach package attrs so `import renpy.display.render` style works.
if not hasattr(renpy, "display"):
    renpy.display = sys.modules["renpy.display"]


class _Matrix2D:
    def __init__(self, xdx, xdy, ydx, ydy):
        self.xdx = float(xdx)
        self.xdy = float(xdy)
        self.ydx = float(ydx)
        self.ydy = float(ydy)

    def inverse(self):
        ix = 1.0 / self.xdx if abs(self.xdx) > 1e-12 else 1.0
        iy = 1.0 / self.ydy if abs(self.ydy) > 1e-12 else 1.0
        return _Matrix2D(ix, 0.0, 0.0, iy)


class _Render:
    def __init__(self, w, h):
        self.width = w
        self.height = h
        self.children = []
        self.reverse = None
        self.forward = None
        self.xclipping = False
        self.yclipping = False
        self.alpha = 1.0
        self.over = 1.0
        self.shaders = None
        self.uniforms = None

    def blit(self, src, pos):
        self.children.append((src, pos[0], pos[1], False, True))

    def subpixel_blit(self, src, pos):
        self.blit(src, pos)

    def depends_on(self, *a, **k):
        return None


# Install fakes only if real Cython Render is unavailable or for isolation.
_rr = sys.modules["renpy.display.render"]
if not hasattr(_rr, "Render"):
    _rr.Render = _Render
if not hasattr(_rr, "IDENTITY"):
    _rr.IDENTITY = object()
_mx = sys.modules["renpy.display.matrix"]
if not hasattr(_mx, "Matrix2D"):
    _mx.Matrix2D = _Matrix2D
if not hasattr(_mx, "Matrix"):
    _mx.Matrix = _Matrix2D

from renpy_display_accelerator_host import RenderTransform


class _FakeChild:
    def __init__(self, w=1920, h=1080):
        self.w = w
        self.h = h

    def render(self, width, height, st, at):
        R = getattr(_rr, "Render", _Render)
        r = R(self.w, self.h)
        return r


class _State:
    def __init__(self, crop, crop_relative=None, zoom=1.0):
        self.crop = crop
        self.crop_relative = crop_relative
        self.corner1 = None
        self.corner2 = None
        self.xsize = None
        self.ysize = None
        self.fit = None
        self.maxsize = None
        self.zoom = zoom
        self.xzoom = 1.0
        self.yzoom = 1.0
        self.rotate = None
        self.rotate_pad = True
        self.alpha = 1.0
        self.additive = 0.0
        self.subpixel = False
        self.mesh = None
        self.blur = None
        self.perspective = None


class _Transform:
    def __init__(self, child, state):
        self.child = child
        self.state = state
        self.child_st_base = 0
        self.child_size = None
        self.offsets = None
        self.render_size = None
        self.reverse = None
        self.forward = None


def _patch_render():
    def _render(d, w, h, st, at):
        return d.render(w, h, st, at)

    _rr.render = _render


def _run(crop, zoom, crop_relative, expect_w, expect_h):
    _patch_render()
    t = _Transform(
        _FakeChild(1920, 1080),
        _State(crop, crop_relative=crop_relative, zoom=zoom),
    )
    rv = RenderTransform(t).render(1920, 1080, 0, 0)
    gw = float(getattr(rv, "width", 0) or 0)
    gh = float(getattr(rv, "height", 0) or 0)
    ok = abs(gw - expect_w) < 1.0 and abs(gh - expect_h) < 1.0
    return ok, gw, gh


def main():
    out = Path(_base) / "host" / "target" / "gate-crop_relative_absolute.txt"
    out.parent.mkdir(parents=True, exist_ok=True)

    cases = []
    # text_config absolute ints under crop_relative_default=True → ~806.4 × 107.1
    ok1, w1, h1 = _run((0, 825, 1920, 255), 0.42, None, 1920 * 0.42, 255 * 0.42)
    cases.append(("abs_ints", ok1, w1, h1, 1920 * 0.42, 255 * 0.42))

    # Fractional crop (floats) → fractions of child
    ok2, w2, h2 = _run((0.0, 0.5, 1.0, 0.25), 1.0, None, 1920.0, 270.0)
    cases.append(("frac_floats", ok2, w2, h2, 1920.0, 270.0))

    # Explicit crop_relative=False: floats as absolute pixels
    ok3, w3, h3 = _run((0.0, 100.0, 200.0, 50.0), 1.0, False, 200.0, 50.0)
    cases.append(("explicit_false", ok3, w3, h3, 200.0, 50.0))

    # ATL position objects: int → position(abs=N, rel=0); float → position(0, f)
    class _Pos:
        def __init__(self, absolute=0, relative=0.0):
            self.absolute = absolute
            self.relative = relative

    pos_crop = (_Pos(0, 0.0), _Pos(825, 0.0), _Pos(1920, 0.0), _Pos(255, 0.0))
    ok4, w4, h4 = _run(pos_crop, 0.42, None, 1920 * 0.42, 255 * 0.42)
    cases.append(("position_abs", ok4, w4, h4, 1920 * 0.42, 255 * 0.42))

    pos_frac = (_Pos(0, 0.0), _Pos(0, 0.5), _Pos(0, 1.0), _Pos(0, 0.25))
    ok5, w5, h5 = _run(pos_frac, 1.0, None, 1920.0, 270.0)
    cases.append(("position_frac", ok5, w5, h5, 1920.0, 270.0))

    ok = all(c[1] for c in cases)
    lines = [f"ok={ok}"]
    for name, cok, gw, gh, ew, eh in cases:
        lines.append(f"{name}: ok={cok} got={gw:.1f}x{gh:.1f} expect={ew:.1f}x{eh:.1f}")
    lines.append("contract=int crops stay absolute under crop_relative_default=True")
    msg = "\n".join(lines) + "\n"
    out.write_text(msg, encoding="utf-8")
    print(msg, flush=True)
    if not ok:
        raise RuntimeError(msg)
    try:
        import renpy_host  # type: ignore

        renpy_host.request_quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
