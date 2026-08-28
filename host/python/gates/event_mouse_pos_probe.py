"""A3 exit probe: inject_mouse pos/x/y on button events (parity with last-cursor).

Gate: RENPY_HOST_GATE=event_mouse_pos_probe
Writes: host/target/gate-event-mouse-pos.txt

Note: run_file prepends imports — no __future__ here.
"""

import os
import traceback


import host_pygame.event as pev  # type: ignore
import renpy_host  # type: ignore
from host_pygame.locals import MOUSEBUTTONDOWN  # type: ignore


def _drain(n=64):
    for _ in range(n):
        if renpy_host.poll_event() is None:
            break


def main():
    base = os.environ.get("RENPY_HOST_BASE", ".")
    out_path = os.path.join(base, "host", "target", "gate-event-mouse-pos.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines = []
    ok = True

    try:
        _drain()
        # inject_mouse already emits motion + button with x/y/pos
        renpy_host.inject_mouse(123, 456, 1, True)

        saw_motion = False
        saw_button = None
        for _ in range(64):
            d = renpy_host.poll_event()
            if d is None:
                renpy_host.wait_until(renpy_host.get_ticks_ms() + 5)
                continue
            t = d.get("type")
            if t == renpy_host.MOUSEMOTION:
                saw_motion = True
                if d.get("x") == 123 and d.get("y") == 456:
                    lines.append("PASS: inject_mouse motion x=123 y=456")
                else:
                    ok = False
                    lines.append(f"FAIL: motion coords {d!r}")
            elif t == renpy_host.MOUSEBUTTONDOWN:
                saw_button = d
                break

        if saw_button is None:
            ok = False
            lines.append("FAIL: no MOUSEBUTTONDOWN after inject_mouse")
        else:
            pos = saw_button.get("pos")
            x = saw_button.get("x")
            y = saw_button.get("y")
            # pos may be "123,456" string before Event coercion
            if x == 123 and y == 456 and (
                pos == "123,456" or pos == (123, 456) or str(pos) in ("123,456", "(123, 456)")
            ):
                lines.append(
                    f"PASS: button pos={pos!r} x={x} y={y} button={saw_button.get('button')}"
                )
            else:
                ok = False
                lines.append(f"FAIL: button fields {saw_button!r}")

            # Also via host_pygame Event coercion
            _drain()
            renpy_host.inject_mouse(10, 20, 1, True)
            got = None
            for _ in range(64):
                e = pev.poll()
                if e.type == MOUSEBUTTONDOWN:
                    got = e
                    break
                if e.type == 0:
                    renpy_host.wait_until(renpy_host.get_ticks_ms() + 5)
            if got is None:
                ok = False
                lines.append("FAIL: pev.poll never got MOUSEBUTTONDOWN")
            else:
                if not hasattr(got, "pos") or not hasattr(got, "x") or not hasattr(got, "y"):
                    ok = False
                    lines.append(f"FAIL: Event missing pos/x/y attrs dict={got.dict!r}")
                elif got.x == 10 and got.y == 20 and got.pos == (10, 20):
                    lines.append(f"PASS: Event coerced pos={got.pos} x={got.x} y={got.y}")
                else:
                    ok = False
                    lines.append(
                        f"FAIL: Event coords want (10,20) got pos={got.pos!r} x={got.x} y={got.y}"
                    )

        if not saw_motion:
            # inject_mouse is supposed to emit motion first; soft note only
            lines.append("NOTE: no MOUSEMOTION observed before button (unexpected)")

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
        raise RuntimeError(f"event_mouse_pos_probe failed; see {out_path}")
    renpy_host.request_quit()
    return 0


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
