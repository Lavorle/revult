"""S1 residual probe: winit Resized emits SDL3 WINDOWRESIZED (0x206).

Gate: RENPY_HOST_GATE=resize_windowresized_probe
Writes: host/target/gate-resize-windowresized.txt

Proves input.rs emits WINDOWRESIZED (not legacy WINDOWEVENT=512) so
renpy.display.core can force_redraw after enlarge. Does not claim bare
product solid-blue recovery alone (needs product re-present).
"""

import os
import traceback

import host_pygame.event as pev  # type: ignore
import renpy_host  # type: ignore
from host_pygame.locals import WINDOWRESIZED  # type: ignore

# --- harness (thin wrapper, original logic preserved) ---
from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore


def _drain(n=128):
    for _ in range(n):
        if renpy_host.poll_event() is None:
            break
    for _ in range(n):
        e = pev.poll()
        if e.type == 0:
            break


def main():
    base = os.environ.get("RENPY_HOST_BASE", ".")
    out_path = os.path.join(base, "host", "target", "gate-resize-windowresized.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines = []
    ok = True

    try:
        # Constant parity
        if int(WINDOWRESIZED) != 0x206:
            ok = False
            lines.append(f"FAIL: host_pygame WINDOWRESIZED={WINDOWRESIZED!r} want 0x206")
        else:
            lines.append(f"PASS: host_pygame.WINDOWRESIZED=0x{int(WINDOWRESIZED):x}")

        host_const = getattr(renpy_host, "WINDOWRESIZED", None)
        if host_const is None:
            ok = False
            lines.append("FAIL: renpy_host.WINDOWRESIZED missing")
        elif int(host_const) != 0x206:
            ok = False
            lines.append(f"FAIL: renpy_host.WINDOWRESIZED={host_const!r} want 0x206")
        else:
            lines.append(f"PASS: renpy_host.WINDOWRESIZED=0x{int(host_const):x}")

        _drain()
        w0, h0 = renpy_host.window_size()
        lines.append(f"NOTE: window_size before={w0}x{h0}")

        # Request enlarge (must go through winit → handle_window_event → queue).
        target_w = max(int(w0) + 64, 960)
        target_h = max(int(h0) + 48, 540)
        if not hasattr(renpy_host, "request_window_size"):
            ok = False
            lines.append("FAIL: renpy_host.request_window_size missing")
        else:
            renpy_host.request_window_size(target_w, target_h)
            lines.append(f"NOTE: request_window_size({target_w},{target_h})")

        saw_raw = None
        saw_pev = None
        deadline = renpy_host.get_ticks_ms() + 1500
        while renpy_host.get_ticks_ms() < deadline:
            # Nested pump so winit Resized is delivered.
            try:
                renpy_host.pump_once(16)
            except Exception:
                renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

            d = renpy_host.poll_event()
            if d is not None:
                t = d.get("type")
                if t == renpy_host.WINDOWRESIZED or t == 0x206:
                    saw_raw = d
                    break
                if t == 512:  # legacy WINDOWEVENT — wrong for Ren'Py force_redraw
                    ok = False
                    lines.append(f"FAIL: got legacy WINDOWEVENT=512 dict={d!r}")
                    break
            else:
                # also try pev path
                e = pev.poll()
                if e.type == WINDOWRESIZED:
                    saw_pev = e
                    break
                if e.type == 0:
                    continue

        if saw_raw is None and saw_pev is None:
            # One more pev drain after pumps
            for _ in range(64):
                e = pev.poll()
                if e.type == WINDOWRESIZED:
                    saw_pev = e
                    break
                if e.type == 0:
                    break

        if saw_raw is not None:
            lines.append(
                f"PASS: raw WINDOWRESIZED type={saw_raw.get('type')} "
                f"x={saw_raw.get('x')} y={saw_raw.get('y')} "
                f"w={saw_raw.get('w')} h={saw_raw.get('h')}"
            )
        elif saw_pev is not None:
            lines.append(
                f"PASS: pev WINDOWRESIZED type={saw_pev.type} "
                f"dict={getattr(saw_pev, 'dict', {})!r}"
            )
        else:
            ok = False
            lines.append(
                "FAIL: no WINDOWRESIZED after request_window_size "
                "(winit may ignore same-size or platform deferred resize)"
            )

        w1, h1 = renpy_host.window_size()
        lines.append(f"NOTE: window_size after={w1}x{h1}")

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
        raise RuntimeError(f"resize_windowresized_probe failed; see {out_path}")
    renpy_host.request_quit()
    return 0


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
