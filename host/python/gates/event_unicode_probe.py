"""A1 exit probe: KEYDOWN unicode post→poll round-trip + Event defaults.

Gate: RENPY_HOST_GATE=event_unicode_probe
Writes: host/target/gate-event-unicode.txt

Note: run_file prepends imports before this source — no __future__ here.
"""

import os
import traceback

from host.python.gates._harness import gate_harness, parametrized_gate

# host_pygame is installed by embed bootstrap before gates run.
import host_pygame.event as pev  # type: ignore
import renpy_host  # type: ignore
from host_pygame.locals import KEYDOWN, KEYUP  # type: ignore


def _drain(n=64):
    for _ in range(n):
        if renpy_host.poll_event() is None:
            break


def main():
    base = os.environ.get("RENPY_HOST_BASE", ".")
    out_path = os.path.join(base, "host", "target", "gate-event-unicode.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines: list[str] = []
    ok = True

    try:
        # 1) Construct KEYDOWN with unicode="x", post, poll, assert.
        _drain()
        ev_in = pev.Event(KEYDOWN, key=ord("x"), unicode="x")
        assert hasattr(ev_in, "unicode"), "constructed KEYDOWN missing unicode attr"
        assert ev_in.unicode == "x", f"constructed unicode want 'x' got {ev_in.unicode!r}"
        pev.post(ev_in)

        got = None
        for _ in range(32):
            e = pev.poll()
            if e.type == KEYDOWN:
                got = e
                break
            if e.type == 0:  # NOEVENT
                renpy_host.wait_until(renpy_host.get_ticks_ms() + 5)

        if got is None:
            ok = False
            lines.append("FAIL: post KEYDOWN unicode='x' never polled")
        else:
            has = hasattr(got, "unicode")
            val = getattr(got, "unicode", None)
            if not has:
                ok = False
                lines.append("FAIL: polled KEYDOWN missing unicode attribute")
            elif val != "x":
                ok = False
                lines.append(f"FAIL: polled unicode want 'x' got {val!r} dict={got.dict!r}")
            else:
                lines.append(f"PASS: post→poll unicode='x' key={getattr(got, 'key', None)}")

        # 2) Empty-string case still has attribute (defaults + inject).
        _drain()
        ev_empty = pev.Event(KEYDOWN, key=13)  # no unicode kwarg
        if not hasattr(ev_empty, "unicode"):
            ok = False
            lines.append("FAIL: KEYDOWN without unicode kwarg missing attr")
        elif ev_empty.unicode != "":
            ok = False
            lines.append(f"FAIL: default unicode want '' got {ev_empty.unicode!r}")
        else:
            lines.append("PASS: KEYDOWN default unicode=''")

        # KEYUP defaults
        ev_up = pev.Event(KEYUP, key=13)
        for field, default in (
            ("unicode", ""),
            ("key", 13),
            ("scancode", 0),
            ("mod", 0),
            ("repeat", False),
        ):
            if not hasattr(ev_up, field):
                ok = False
                lines.append(f"FAIL: KEYUP missing {field}")
            else:
                val = getattr(ev_up, field)
                if field != "key" and val != default:
                    # key was provided as 13
                    ok = False
                    lines.append(f"FAIL: KEYUP.{field} want {default!r} got {val!r}")
        if ok or all("KEYUP" not in ln or ln.startswith("PASS") for ln in lines[-5:]):
            lines.append(
                f"PASS: KEYUP defaults unicode={ev_up.unicode!r} key={ev_up.key} "
                f"scancode={ev_up.scancode} mod={ev_up.mod} repeat={ev_up.repeat}"
            )

        # 3) inject_key with explicit unicode round-trip (Rust path).
        _drain()
        renpy_host.inject_key(ord("y"), True, "y")
        got2 = None
        for _ in range(32):
            d = renpy_host.poll_event()
            if d is None:
                renpy_host.wait_until(renpy_host.get_ticks_ms() + 5)
                continue
            if d.get("type") == renpy_host.KEYDOWN:
                got2 = d
                break
        if got2 is None:
            ok = False
            lines.append("FAIL: inject_key(y,True,'y') never polled")
        else:
            u = got2.get("unicode")
            if u != "y":
                ok = False
                lines.append(f"FAIL: inject_key unicode want 'y' got {u!r} full={got2!r}")
            else:
                lines.append(f"PASS: inject_key unicode='y' fields={sorted(got2.keys())}")

        # 4) inject_key default unicode="" still present.
        _drain()
        renpy_host.inject_key(13, True)
        got3 = None
        for _ in range(32):
            d = renpy_host.poll_event()
            if d is None:
                renpy_host.wait_until(renpy_host.get_ticks_ms() + 5)
                continue
            if d.get("type") == renpy_host.KEYDOWN:
                got3 = d
                break
        if got3 is None:
            ok = False
            lines.append("FAIL: inject_key(13,True) never polled")
        elif "unicode" not in got3:
            ok = False
            lines.append(f"FAIL: inject_key default missing unicode key full={got3!r}")
        elif got3.get("unicode") != "":
            ok = False
            lines.append(f"FAIL: inject_key default unicode want '' got {got3.get('unicode')!r}")
        else:
            lines.append("PASS: inject_key default unicode='' present")

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
        raise RuntimeError(f"event_unicode_probe failed; see {out_path}")
    renpy_host.request_quit()
    return 0


# Gate runner execs this file via py.run (often as __main__); never sys.exit
# so the host does not treat a clean probe as gate failure.
main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
