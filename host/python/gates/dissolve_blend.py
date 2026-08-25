"""
AC-T1 dissolve_blend gate — dual-source crossfade via WgpuDraw dissolve path.

Gate name: dissolve_blend  (RENPY_HOST_GATE=dissolve_blend)

Builds a mesh Render with renpy.dissolve + u_renpy_dissolve=0.5 and two solid
child textures (red / blue). After draw_screen, game RT mean must sit between
pure red and pure blue (not hard-cut to either, not arena clear).

Note: no from __future__; host run_file prepends imports.
"""

import os
from pathlib import Path

import renpy_host  # type: ignore
from renpy.pygame.surface import Surface
from renpy.wgpu.draw import WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---
try:
    from _harness import gate_harness, parametrized_gate  # type: ignore
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore
    except ImportError:
        gate_harness = None  # type: ignore
        parametrized_gate = None  # type: ignore


_base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
out = Path(_base) / "host" / "target" / "gate-dissolve_blend.txt"
out.parent.mkdir(parents=True, exist_ok=True)


class FakeRender:
    def __init__(self, width=1280, height=720):
        self.width = int(width)
        self.height = int(height)
        self.children = []
        self.mesh = True
        self.texture = None
        self.textures = None
        self.color = None
        self.shaders = ("renpy.dissolve",)
        self.pipeline = None
        self.vertices = None
        self.indices = None
        self.cached_model = None
        self.blits = None
        self.ndc = None
        self.uniforms = {"u_renpy_dissolve": 0.5}
        self.loaded = False

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self

    def get_size(self):
        return (self.width, self.height)


def _solid_surface(w, h, rgba):
    s = Surface((w, h))
    s.fill(rgba)
    return s


def _mean_rgb(rgba, w, h):
    n = w * h
    if n <= 0 or len(rgba) < n * 4:
        return 0.0, 0.0, 0.0
    rs = gs = bs = 0
    step = max(1, n // 50000)  # subsample large RTs
    count = 0
    for i in range(0, n, step):
        o = i * 4
        rs += rgba[o]
        gs += rgba[o + 1]
        bs += rgba[o + 2]
        count += 1
    inv = 1.0 / max(1, count)
    return rs * inv, gs * inv, bs * inv


def main():
    w, h = 1280, 720
    draw = WgpuDraw()
    draw.init((w, h))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass

    old = _solid_surface(w, h, (255, 0, 0, 255))
    new = _solid_surface(w, h, (0, 0, 255, 255))
    root = FakeRender(w, h)
    root.blit(old, 0, 0)
    root.blit(new, 0, 0)

    draw.draw_screen(root, flip=True)
    try:
        rw, rh, rgba = renpy_host.read_game_rt_rgba()
    except Exception as e:
        msg = "ok=False reason=read_rt err=%s" % e
        out.write_text(msg + "\n")
        print("[dissolve_blend]", msg, flush=True)
        return

    mr, mg, mb = _mean_rgb(rgba, rw, rh)
    # Pure red mean ~ (255,0,0); pure blue ~ (0,0,255); 50% blend ~ (127,0,127)
    # Accept wide band: both R and B elevated, not arena clear (~13,13,20).
    clear_like = mr < 40 and mg < 40 and mb < 40
    hard_red = mr > 200 and mb < 40
    hard_blue = mb > 200 and mr < 40
    blended = (mr > 40 and mb > 40) and not clear_like
    ok = blended and not hard_red and not hard_blue
    msg = (
        "ok=%s mean=(%.1f,%.1f,%.1f) blended=%s hard_red=%s hard_blue=%s clear_like=%s"
        % (ok, mr, mg, mb, blended, hard_red, hard_blue, clear_like)
    )
    out.write_text(msg + "\n")
    print("[dissolve_blend]", msg, flush=True)


main()

# ----------------------------------------------------------------------
# HARNESS MIGRATION (thin wrapper, original logic preserved above)
# ----------------------------------------------------------------------
# Full harness rewrite example: original body of main() extracted as
# _harness_run_one(case).  Parametrized via gate_harness / parametrized_gate.
# Original main() above is kept runnable; this wrapper is additive.

def _harness_run_one(case):
    """Harness entry: case -> (w,h,rgba) or (ok,msg). Parameterized dissolve."""
    amount = float(case.get("amount", 0.5)) if isinstance(case, dict) else 0.5
    old_rgba = case.get("old_rgba", (255, 0, 0, 255)) if isinstance(case, dict) else (255, 0, 0, 255)
    new_rgba = case.get("new_rgba", (0, 0, 255, 255)) if isinstance(case, dict) else (0, 0, 255, 255)
    w, h = 1280, 720
    draw = WgpuDraw()
    draw.init((w, h))
    try:
        draw.physical_size = renpy_host.window_size()
    except Exception:
        pass
    old = _solid_surface(w, h, old_rgba)
    new = _solid_surface(w, h, new_rgba)
    root = FakeRender(w, h)
    root.uniforms = {"u_renpy_dissolve": amount}
    root.blit(old, 0, 0)
    root.blit(new, 0, 0)
    draw.draw_screen(root, flip=True)
    rw, rh, rgba = renpy_host.read_game_rt_rgba()
    return rw, rh, rgba


def _harness_golden_compare(w, h, rgba):
    """Custom MAE-style compare mirroring main()'s mean logic."""
    mr, mg, mb = _mean_rgb(rgba, w, h)
    clear_like = mr < 40 and mg < 40 and mb < 40
    hard_red = mr > 200 and mb < 40
    hard_blue = mb > 200 and mr < 40
    blended = (mr > 40 and mb > 40) and not clear_like
    ok = blended and not hard_red and not hard_blue
    msg = f"ok={ok} mean=({mr:.1f},{mg:.1f},{mb:.1f}) blended={blended} hard_red={hard_red} hard_blue={hard_blue} clear_like={clear_like} amount=param"
    return ok, msg


# Optional parametrized entry for pytest / harness runner.
# Enabled only when RENPY_HOST_HARNESS=1 to keep original `main()` path default.
if parametrized_gate is not None:
    @parametrized_gate("dissolve_blend", [{"amount": 0.0}, {"amount": 0.5}, {"amount": 1.0}])
    def _parametrized_case(case):
        w, h, rgba = _harness_run_one(case)
        return _harness_golden_compare(w, h, rgba)


def _harness_main():
    """Thin harness dispatch preserving original main() for default runs."""
    import os as _os
    if gate_harness is not None and _os.environ.get("RENPY_HOST_HARNESS") == "1":
        cases = [{"amount": 0.0}, {"amount": 0.5}, {"amount": 1.0}]
        ok = gate_harness("dissolve_blend", cases, _harness_run_one, _harness_golden_compare)
        raise SystemExit(0 if ok else 1)
    else:
        pass


# To fully migrate, replace the bottom `main()` call with:
#   if __name__ == "__main__":
#       _harness_main()
# Original `main()` remains above for backward compat.

