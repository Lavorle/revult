"""S1 present-ownership recovery after resize (supporting; not bare-product claim).

Gate: RENPY_HOST_GATE=resize_present_recovery_probe
Writes: host/target/gate-resize-present-recovery.txt

Simulates the solid-deep-blue recovery chain:
  1. Product draw_screen → last_product_present=True
  2. request_window_size → winit Resized → host clear + last_product_present=False
  3. WINDOWRESIZED reaches host_pygame (type 0x206, not legacy 512)
  4. Product re-draw_screen → last_product_present=True + non-clear game RT

Bare product S1 still needs human/verify worker enlarge on the_question.
"""

import os
import traceback
from pathlib import Path

import host_pygame.event as pev  # type: ignore
import renpy_host  # type: ignore
from host_pygame.locals import WINDOWRESIZED  # type: ignore

from renpy.wgpu.draw import WgpuDraw

# --- harness (thin wrapper, original logic preserved) ---

# Legacy SDL2 umbrella type (host event_queue keeps 512; not in SDL3 locals).
WINDOWEVENT_LEGACY = 512


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

    def blit(self, child, xo=0, yo=0):
        self.children.append((child, float(xo), float(yo), False, True))
        return self


class FakeModel:
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


def _drain(n=256):
    for _ in range(n):
        if renpy_host.poll_event() is None:
            break
    for _ in range(n):
        e = pev.poll()
        if e.type == 0:
            break


def _mean_rgb(rgba, w, h):
    if not rgba or w < 1 or h < 1:
        return (0.0, 0.0, 0.0)
    # sample center 4x4
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


def _product_draw(draw, color=(0.85, 0.20, 0.15, 1.0)):
    root = FakeRender(*draw.virtual_size)
    bg = FakeModel(
        color=color,
        mesh=True,
        shaders=("renpy.solid",),
        ndc=(-1.0, -1.0, 1.0, 1.0),
    )
    root.blit(bg, 0, 0)
    draw.draw_screen(root, flip=True)


def main():
    base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
    out_path = os.path.join(base, "host", "target", "gate-resize-present-recovery.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines = []
    ok = True

    try:
        if int(WINDOWRESIZED) != 0x206:
            ok = False
            lines.append(f"FAIL: WINDOWRESIZED={WINDOWRESIZED!r} want 0x206")
        else:
            lines.append(f"PASS: WINDOWRESIZED=0x{int(WINDOWRESIZED):x}")

        draw = WgpuDraw()
        if not draw.init((1280, 720)):
            ok = False
            lines.append("FAIL: WgpuDraw.init failed")
            raise RuntimeError("init failed")

        renpy_host.reset_present_stats()
        _product_draw(draw, color=(0.85, 0.20, 0.15, 1.0))
        owned0 = bool(renpy_host.last_product_present())
        presents0 = int(renpy_host.product_presents())
        w0, h0, rgba0 = renpy_host.read_game_rt_rgba()
        mean0 = _mean_rgb(rgba0, w0, h0)
        lines.append(
            f"NOTE: after first present owned={owned0} presents={presents0} "
            f"rt={w0}x{h0} mean_rgb={mean0}"
        )
        if not owned0:
            ok = False
            lines.append("FAIL: last_product_present False after first product draw")
        if presents0 < 1:
            ok = False
            lines.append("FAIL: product_presents < 1 after first product draw")
        # product red-ish must not match idle clear ~ (0.08,0.18,0.28)*255
        if mean0[0] < 100:
            ok = False
            lines.append(f"FAIL: first present not product-red mean={mean0}")
        else:
            lines.append("PASS: first product present owns swapchain + non-clear RT")

        _drain()
        ww0, wh0 = renpy_host.window_size()
        target_w = max(int(ww0) + 96, 1024)
        target_h = max(int(wh0) + 64, 576)
        if not hasattr(renpy_host, "request_window_size"):
            ok = False
            lines.append("FAIL: request_window_size missing")
        else:
            renpy_host.request_window_size(target_w, target_h)
            lines.append(f"NOTE: request_window_size({target_w},{target_h}) from {ww0}x{wh0}")

        saw_resized = None
        saw_legacy = False
        deadline = renpy_host.get_ticks_ms() + 2000
        while renpy_host.get_ticks_ms() < deadline:
            try:
                renpy_host.pump_once(16)
            except Exception:
                renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

            d = renpy_host.poll_event()
            if d is not None:
                t = d.get("type")
                if t == WINDOWEVENT_LEGACY or t == 512:
                    saw_legacy = True
                    lines.append(f"FAIL: legacy WINDOWEVENT=512 after resize dict={d!r}")
                    ok = False
                    break
                if t == WINDOWRESIZED or t == 0x206:
                    saw_resized = d
                    break
            else:
                e = pev.poll()
                if e.type == WINDOWRESIZED:
                    saw_resized = {
                        "type": e.type,
                        "x": getattr(e, "x", None),
                        "y": getattr(e, "y", None),
                        "w": getattr(e, "w", None),
                        "h": getattr(e, "h", None),
                    }
                    break
                if e.type == 0:
                    continue

        if saw_resized is None and not saw_legacy:
            for _ in range(64):
                e = pev.poll()
                if e.type == WINDOWRESIZED:
                    saw_resized = {
                        "type": e.type,
                        "x": getattr(e, "x", None),
                        "y": getattr(e, "y", None),
                    }
                    break
                if e.type == 0:
                    break

        if saw_resized is None:
            ok = False
            lines.append(
                "FAIL: no WINDOWRESIZED after request_window_size "
                "(cannot prove recovery path without Resized delivery)"
            )
        else:
            lines.append(
                f"PASS: WINDOWRESIZED type={saw_resized.get('type')} "
                f"x={saw_resized.get('x')} y={saw_resized.get('y')} "
                f"w={saw_resized.get('w')} h={saw_resized.get('h')}"
            )

        # After Resized handler, ownership is dropped until product re-presents.
        owned_mid = bool(renpy_host.last_product_present())
        ww1, wh1 = renpy_host.window_size()
        lines.append(
            f"NOTE: post-resize owned={owned_mid} window={ww1}x{wh1} "
            f"(owned may be False after clear; expected)"
        )

        # False-green prevention (RE4): size must actually change after request.
        if int(ww1) == int(ww0) and int(wh1) == int(wh0):
            ok = False
            lines.append(
                f"FAIL: size_unchanged after request_window_size "
                f"(still {ww1}x{wh1}; cannot prove resize recovery)"
            )
        else:
            lines.append(
                f"PASS: window size changed {ww0}x{wh0} -> {ww1}x{wh1}"
            )

        # Simulate core.py force_redraw path: update scale + re-present product.
        draw.resize()
        renpy_host.reset_present_stats()
        _product_draw(draw, color=(0.20, 0.75, 0.35, 1.0))
        owned1 = bool(renpy_host.last_product_present())
        presents1 = int(renpy_host.product_presents())
        w1, h1, rgba1 = renpy_host.read_game_rt_rgba()
        mean1 = _mean_rgb(rgba1, w1, h1)
        lines.append(
            f"NOTE: after re-present owned={owned1} presents={presents1} "
            f"rt={w1}x{h1} mean_rgb={mean1}"
        )

        if not owned1:
            ok = False
            lines.append("FAIL: last_product_present False after re-present (S1 stuck clear)")
        else:
            lines.append("PASS: product re-present restored last_product_present")

        if presents1 < 1:
            ok = False
            lines.append("FAIL: product_presents < 1 after re-present")

        # Green product must not match idle clear (0.08,0.18,0.28)*255 ≈ (20,46,71)
        clear_like = mean1[0] < 40 and mean1[1] < 70 and mean1[2] > 40 and mean1[2] < 100
        if clear_like:
            ok = False
            lines.append(f"FAIL: re-present still looks like deep-teal clear mean={mean1}")
        elif mean1[1] < 80:
            ok = False
            lines.append(f"FAIL: re-present not product-green mean={mean1}")
        else:
            lines.append("PASS: re-present game RT non-clear (product green)")

        idle_clears = int(renpy_host.idle_clears_after_present())
        lines.append(f"NOTE: idle_clears_after_present={idle_clears}")

    except Exception:
        ok = False
        lines.append("EXCEPTION:")
        lines.append(traceback.format_exc())

    status = "ok=True" if ok else "ok=False"
    body = status + "\n" + "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    print(body, end="")
    if not ok:
        raise RuntimeError(f"resize_present_recovery_probe failed; see {out_path}")
    renpy_host.request_quit()
    return 0


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
