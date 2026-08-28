"""RE1/RE6 sharpness probe: edge energy survives enlarge + draw_per_virt rises.

Gate: RENPY_HOST_GATE=resize_sharpness_probe
Writes: host/target/gate-resize_sharpness_probe.txt

Sequence:
  1. WgpuDraw init virtual 1280x720; present high-contrast checkerboard surface
  2. read_game_rt_rgba; compute edge metric (mean abs neighbor RGB diffs)
  3. Record window_size, physical_size, draw_per_virt
  4. request_window_size(1920,1080); pump until size changes or 2s timeout
  5. FAIL if size did not change (false-green prevention)
  6. draw.update(force=True) / resize / re-present checkerboard
  7. Re-read RT; record new sizes and draw_per_virt
  8. Pass if size increased AND draw_per_virt rose (or >=1.4 when enlarged ~1.5x)
     AND edge metric did not collapse vs baseline*scale heuristic

Bare gates: avoid print() after WgpuDraw import (renpy.log needs renpy.config).
"""

import os
import traceback
from pathlib import Path

import host_pygame.event as pev  # type: ignore
import renpy_host  # type: ignore
from host_pygame.locals import WINDOWRESIZED  # type: ignore

from renpy.wgpu.draw import WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---



class FakeRender:
    def __init__(self, width=1280, height=720):
        self.width = int(width)
        self.height = int(height)
        self.children = []
        self.mesh = None
        self.texture = None
        self.textures = None
        self.color = None
        self.shaders = None
        self.pipeline = None
        self.vertices = None
        self.indices = None
        self.cached_model = None
        self.blits = None
        self.ndc = None
        self.uniforms = None
        self.loaded = False
        self.forward = None
        self.reverse = None
        self.cached_texture = None

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self

    def get_size(self):
        return (self.width, self.height)


class FakeModel:
    """Solid full-screen model (same shape as resize_present_recovery_probe)."""

    def __init__(self, color=None, mesh=True, ndc=None, shaders=None):
        self.width = 0
        self.height = 0
        self.color = color
        self.texture = None
        self.mesh = mesh
        self.ndc = ndc
        self.shaders = shaders
        self.vertices = None
        self.indices = None
        self.pipeline = None
        self.textures = None
        self.uniforms = None
        self.texture1 = None


class _Surf:
    """Minimal surface with get_size + _pixels for WgpuDraw.load_texture."""

    def __init__(self, w, h, pixels):
        self._w = int(w)
        self._h = int(h)
        need = self._w * self._h * 4
        raw = bytes(pixels)
        self._pixels = raw if len(raw) >= need else raw + bytes(need - len(raw))

    def get_size(self):
        return self._w, self._h


def _drain(n=256):
    for _ in range(n):
        if renpy_host.poll_event() is None:
            break
    for _ in range(n):
        e = pev.poll()
        if e.type == 0:
            break


def _make_checker_pixels(w, h, cell=32):
    """RGBA bytes: alternating black/white cells + red 4px border."""
    buf = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 4
            on_border = x < 4 or y < 4 or x >= w - 4 or y >= h - 4
            if on_border:
                buf[i] = 255
                buf[i + 1] = 0
                buf[i + 2] = 0
                buf[i + 3] = 255
            else:
                white = ((x // cell) + (y // cell)) % 2 == 0
                v = 255 if white else 0
                buf[i] = v
                buf[i + 1] = v
                buf[i + 2] = v
                buf[i + 3] = 255
    return bytes(buf)


def _checkerboard_draw(draw, cell=32):
    """Present high-contrast checkerboard via surface texture (soft-upscale path).

    Falls back to a single product-style solid if texture path fails so the
    size/dpv checks still run; edge metric then documents the failure.
    """
    vw, vh = draw.virtual_size
    vw, vh = int(vw), int(vh)
    # Source at virtual size: soft enlarge without density re-render blurs edges.
    pixels = _make_checker_pixels(vw, vh, cell=cell)
    surf = _Surf(vw, vh, pixels)
    try:
        tex = draw.load_texture(surf, transient=True)
    except Exception:
        tex = None

    root = FakeRender(vw, vh)
    if tex is not None:
        # Prefer mesh container + texture child (product image path).
        root.mesh = True
        root.blit(tex, 0, 0)
        # Also stash as cached_texture for image-cache branch.
        root.cached_texture = tex
    else:
        # Fallback solid red — edge metric will be near-zero; size checks still run.
        root.blit(
            FakeModel(
                color=(0.85, 0.20, 0.15, 1.0),
                mesh=True,
                shaders=("renpy.solid",),
                ndc=(-1.0, -1.0, 1.0, 1.0),
            ),
            0,
            0,
        )
    draw.draw_screen(root, flip=True)
    return tex is not None


def _edge_metric(rgba, w, h, step=4):
    """Mean abs neighbor RGB diffs on a grid; higher = sharper edges.

    Compares samples `step` pixels apart (not adjacent of a strided grid) so
    checkerboard cell boundaries are not systematically skipped.
    """
    if not rgba or w < 2 or h < 2:
        return 0.0
    need = w * h * 4
    if len(rgba) < need:
        return 0.0
    total = 0.0
    n = 0
    # Horizontal: (x,y) vs (x+step,y)
    for y in range(0, h, step):
        for x in range(0, w - step, step):
            i = (y * w + x) * 4
            j = i + step * 4
            total += abs(rgba[i] - rgba[j])
            total += abs(rgba[i + 1] - rgba[j + 1])
            total += abs(rgba[i + 2] - rgba[j + 2])
            n += 1
    # Vertical: (x,y) vs (x,y+step)
    for y in range(0, h - step, step):
        for x in range(0, w, step):
            i = (y * w + x) * 4
            j = i + step * w * 4
            total += abs(rgba[i] - rgba[j])
            total += abs(rgba[i + 1] - rgba[j + 1])
            total += abs(rgba[i + 2] - rgba[j + 2])
            n += 1
    if n == 0:
        return 0.0
    return total / float(n)


def _mean_rgb_center(rgba, w, h):
    if not rgba or w < 1 or h < 1:
        return (0.0, 0.0, 0.0)
    cx, cy = w // 2, h // 2
    rs = gs = bs = n = 0
    for dy in range(-2, 2):
        for dx in range(-2, 2):
            x = max(0, min(w - 1, cx + dx))
            y = max(0, min(h - 1, cy + dy))
            i = (y * w + x) * 4
            rs += rgba[i]
            gs += rgba[i + 1]
            bs += rgba[i + 2]
            n += 1
    return (rs / n, gs / n, bs / n)


def _pump_until_size(timeout_ms=2000, baseline=None):
    """Pump winit until window_size differs from baseline or timeout.

    `baseline` is the pre-request size. If omitted, captures current size at
    entry — callers that already applied request_window_size must pass the
    pre-request pair, or an immediate apply looks "unchanged".

    Returns (w, h, saw_windowresized_or_None, changed_vs_baseline).
    """
    saw = None
    deadline = renpy_host.get_ticks_ms() + int(timeout_ms)
    if baseline is None:
        w0, h0 = renpy_host.window_size()
    else:
        w0, h0 = int(baseline[0]), int(baseline[1])
    # Immediate apply (Wayland force-drawable) may already have changed size
    # before the first pump iteration.
    w, h = renpy_host.window_size()
    if (int(w), int(h)) != (int(w0), int(h0)):
        # Still drain any pending WINDOWRESIZED for notes.
        for _ in range(8):
            try:
                renpy_host.pump_once(0)
            except Exception:
                break
            d = renpy_host.poll_event()
            if d is not None and (d.get("type") == WINDOWRESIZED or d.get("type") == 0x206):
                saw = d
                break
            e = pev.poll()
            if getattr(e, "type", 0) == WINDOWRESIZED:
                saw = {"type": e.type, "w": getattr(e, "w", None), "h": getattr(e, "h", None)}
                break
        return w, h, saw, True

    while renpy_host.get_ticks_ms() < deadline:
        try:
            renpy_host.pump_once(16)
        except Exception:
            renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

        d = renpy_host.poll_event()
        if d is not None:
            t = d.get("type")
            if t == WINDOWRESIZED or t == 0x206:
                saw = d
        else:
            e = pev.poll()
            if e.type == WINDOWRESIZED:
                saw = {
                    "type": e.type,
                    "w": getattr(e, "w", None),
                    "h": getattr(e, "h", None),
                }

        w, h = renpy_host.window_size()
        if (int(w), int(h)) != (int(w0), int(h0)):
            return w, h, saw, True
    w, h = renpy_host.window_size()
    return w, h, saw, (int(w), int(h)) != (int(w0), int(h0))


def main():
    base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
    out_path = os.path.join(base, "host", "target", "gate-resize_sharpness_probe.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines = []
    ok = True

    def note(msg):
        # Buffer only — renpy.log may intercept print without renpy.config.
        lines.append(str(msg))

    try:
        draw = WgpuDraw()
        if not draw.init((1280, 720)):
            ok = False
            note("FAIL: WgpuDraw.init failed")
            raise RuntimeError("init failed")

        # Pin baseline size so enlarge is meaningful when possible.
        if hasattr(renpy_host, "request_window_size"):
            renpy_host.request_window_size(1280, 720)
            _pump_until_size(timeout_ms=400)
            _drain()
            draw.update(force=True)

        ww0, wh0 = renpy_host.window_size()
        note(f"NOTE: baseline window_size={ww0}x{wh0}")
        note(
            f"NOTE: baseline physical={draw.physical_size} "
            f"drawable={draw.drawable_size} virtual={draw.virtual_size} "
            f"draw_per_virt={draw.draw_per_virt:.4f}"
        )

        tex_ok = _checkerboard_draw(draw)
        note(f"NOTE: checkerboard via texture={tex_ok}")
        rt_w0, rt_h0, rgba0 = renpy_host.read_game_rt_rgba()
        edge0 = _edge_metric(rgba0, rt_w0, rt_h0)
        mean0 = _mean_rgb_center(rgba0, rt_w0, rt_h0)
        dpv0 = float(draw.draw_per_virt)
        note(
            f"NOTE: baseline game_rt={rt_w0}x{rt_h0} edge_metric={edge0:.4f} "
            f"mean_rgb={mean0}"
        )

        if edge0 < 1.0:
            ok = False
            note(
                f"FAIL: baseline edge_metric={edge0:.4f} too low "
                "(checkerboard did not present high-contrast edges)"
            )
        else:
            note(f"PASS: baseline edge_metric={edge0:.4f}")

        target_w, target_h = 1920, 1080
        if not hasattr(renpy_host, "request_window_size"):
            ok = False
            note("FAIL: renpy_host.request_window_size missing")
            ww1, wh1, saw, size_changed = ww0, wh0, None, False
        else:
            # Capture baseline BEFORE request — force-drawable may apply
            # synchronously so a post-request pump baseline would false-FAIL.
            renpy_host.request_window_size(target_w, target_h)
            note(f"NOTE: request_window_size({target_w},{target_h})")
            ww1, wh1, saw, size_changed = _pump_until_size(
                timeout_ms=2000, baseline=(ww0, wh0)
            )
        if saw is not None:
            note(f"NOTE: WINDOWRESIZED saw={saw!r}")
        note(f"NOTE: post-request window_size={ww1}x{wh1}")

        # Authoritative: compare to pre-request baseline (not pump-local baseline).
        size_changed = (int(ww1), int(wh1)) != (int(ww0), int(wh0))
        size_grew = (int(ww1) > int(ww0)) or (int(wh1) > int(wh0))
        if not size_changed:
            ok = False
            note(
                "FAIL: size_unchanged after request_window_size "
                f"(still {ww1}x{wh1} vs baseline {ww0}x{wh0}; "
                "cannot measure sharpness / density)"
            )
        elif not size_grew:
            ok = False
            note(
                f"FAIL: window size did not increase after enlarge request "
                f"before={ww0}x{wh0} after={ww1}x{wh1}"
            )
        else:
            note(f"PASS: window size changed {ww0}x{wh0} -> {ww1}x{wh1}")

        # Density re-render path (Lane A): update/resize should refresh draw_per_virt.
        try:
            updated = draw.update(force=True)
            note(f"NOTE: draw.update(force=True) returned {updated!r}")
        except Exception as e:
            note(f"NOTE: draw.update raised {e!r}; trying resize()")
            draw.resize()
        draw.resize()

        dpv1 = float(draw.draw_per_virt)
        note(
            f"NOTE: after update physical={draw.physical_size} "
            f"drawable={draw.drawable_size} draw_per_virt={dpv1:.4f}"
        )

        # Re-present at new density. kill_textures path (Lane A) should re-raster;
        # without it, stretched 1x bitmap softens (edge metric collapses).
        try:
            if hasattr(draw, "kill_textures"):
                # Only if size actually changed — mirrors before_resize intent.
                if size_changed:
                    draw.kill_textures()
                    note("NOTE: kill_textures() after size change")
        except Exception as e:
            note(f"NOTE: kill_textures raised {e!r}")

        _checkerboard_draw(draw)
        rt_w1, rt_h1, rgba1 = renpy_host.read_game_rt_rgba()
        edge1 = _edge_metric(rgba1, rt_w1, rt_h1)
        mean1 = _mean_rgb_center(rgba1, rt_w1, rt_h1)
        note(
            f"NOTE: after resize game_rt={rt_w1}x{rt_h1} edge_metric={edge1:.4f} "
            f"mean_rgb={mean1}"
        )

        scale_w = float(ww1) / max(1.0, float(ww0))
        scale_h = float(wh1) / max(1.0, float(wh0))
        scale = max(scale_w, scale_h)
        note(f"NOTE: enlarge_scale≈{scale:.3f} (w={scale_w:.3f} h={scale_h:.3f})")

        if size_grew:
            if dpv1 <= dpv0 + 1e-6 and scale >= 1.2:
                ok = False
                note(
                    f"FAIL: draw_per_virt did not increase "
                    f"before={dpv0:.4f} after={dpv1:.4f} (scale={scale:.3f})"
                )
            elif scale >= 1.4 and dpv1 < 1.4:
                ok = False
                note(
                    f"FAIL: draw_per_virt={dpv1:.4f} < 1.4 after ~1.5x enlarge "
                    f"(scale={scale:.3f})"
                )
            else:
                note(f"PASS: draw_per_virt {dpv0:.4f} -> {dpv1:.4f}")

        # Edge heuristic: after re-present, mean edge energy must not collapse.
        floor = max(1.0, edge0 * 0.15)
        if edge0 >= 1.0:
            if edge1 < floor:
                ok = False
                note(
                    f"FAIL: edge_metric collapsed after resize "
                    f"baseline={edge0:.4f} after={edge1:.4f} floor={floor:.4f}"
                )
            else:
                note(
                    f"PASS: edge_metric held "
                    f"baseline={edge0:.4f} after={edge1:.4f} floor={floor:.4f}"
                )

        note(
            "heuristic=edge mean abs neighbor RGB diff; "
            "collapse if after < max(1.0, baseline*0.15); "
            "dpv must rise on enlarge and >=1.4 when scale>=1.4; "
            "size_unchanged is hard FAIL (false-green prevention)"
        )

    except Exception:
        ok = False
        lines.append("EXCEPTION:")
        lines.append(traceback.format_exc())

    status = "ok=True" if ok else "ok=False"
    body = status + "\n" + "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        import sys

        sys.__stdout__.write(body)
        sys.__stdout__.flush()
    except Exception:
        pass
    try:
        renpy_host.request_quit()
    except Exception:
        pass
    if not ok:
        raise RuntimeError(f"resize_sharpness_probe failed; see {out_path}")
    return 0


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

