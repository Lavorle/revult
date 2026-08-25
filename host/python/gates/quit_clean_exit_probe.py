"""Q3 cooperative quit probe: request_quit → should_exit → wait_until returns.

Gate: RENPY_HOST_GATE=quit_clean_exit_probe
Writes: host/target/gate-quit_clean_exit_probe.txt

Covers the host-side stop signal path (request_quit / should_exit / wait_until).
Live window-X (CloseRequested) is separate acceptance criterion Q2 and is
documented here but not automated.

Sequence:
  1. Confirm should_exit starts False
  2. renpy_host.request_quit()
  3. Assert should_exit() True
  4. wait_until returns promptly under deadline
  5. Optionally observe QUIT event on the queue
  6. ok=True if stop signal works; always request_quit so host exits
"""

import os
import traceback
from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host  # type: ignore


# SDL3 / host_pygame QUIT type (host_pygame.locals.QUIT = 0x100)
QUIT_TYPE = 0x100


def main():
    base = os.environ.get("RENPY_HOST_BASE") or str(Path.cwd())
    out_path = os.path.join(base, "host", "target", "gate-quit_clean_exit_probe.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    lines = []
    ok = True

    def note(msg):
        lines.append(str(msg))
        print("[quit_clean_exit_probe]", msg, flush=True)

    try:
        if not hasattr(renpy_host, "should_exit"):
            ok = False
            note("FAIL: renpy_host.should_exit missing")
            raise RuntimeError("should_exit missing")
        if not hasattr(renpy_host, "request_quit"):
            ok = False
            note("FAIL: renpy_host.request_quit missing")
            raise RuntimeError("request_quit missing")

        before = bool(renpy_host.should_exit())
        note(f"NOTE: should_exit before request_quit={before}")
        if before:
            ok = False
            note("FAIL: should_exit already True at gate start (unexpected)")
        else:
            note("PASS: should_exit starts False")

        t0 = int(renpy_host.get_ticks_ms())
        renpy_host.request_quit()
        t1 = int(renpy_host.get_ticks_ms())
        note(f"NOTE: request_quit() returned in {t1 - t0}ms")

        after = bool(renpy_host.should_exit())
        note(f"NOTE: should_exit after request_quit={after}")
        if not after:
            ok = False
            note("FAIL: should_exit still False after request_quit()")
        else:
            note("PASS: should_exit True after request_quit()")

        # wait_until must return promptly when should_exit is set (nested pump
        # / product event_wait must not hang). Request a far-future deadline;
        # cooperative quit should wake early.
        far = int(renpy_host.get_ticks_ms()) + 5000
        t2 = int(renpy_host.get_ticks_ms())
        try:
            renpy_host.wait_until(far)
            t3 = int(renpy_host.get_ticks_ms())
            waited = t3 - t2
            note(f"NOTE: wait_until(far) returned after {waited}ms (deadline was +5000ms)")
            # Prompt: well under the 5s deadline; allow some pump overhead.
            if waited >= 4500:
                ok = False
                note(
                    f"FAIL: wait_until did not return promptly under should_exit "
                    f"(waited {waited}ms ≈ full deadline)"
                )
            else:
                note(f"PASS: wait_until returned promptly ({waited}ms < 4500ms)")
        except Exception as e:
            # Some host builds may raise QuitException / HostStop from wait_until.
            t3 = int(renpy_host.get_ticks_ms())
            waited = t3 - t2
            note(
                f"NOTE: wait_until raised {type(e).__name__}: {e!r} after {waited}ms "
                "(acceptable if should_exit already True)"
            )
            if waited >= 4500:
                ok = False
                note("FAIL: wait_until raise was not prompt")
            else:
                note("PASS: wait_until aborted promptly via exception path")

        # Optional: QUIT event should be on the queue (request_quit pushes it).
        saw_quit = False
        host_quit = getattr(renpy_host, "QUIT", QUIT_TYPE)
        for _ in range(64):
            d = renpy_host.poll_event()
            if d is None:
                break
            t = d.get("type")
            if t == host_quit or t == QUIT_TYPE or t == 256:
                saw_quit = True
                note(f"NOTE: observed QUIT event dict={d!r}")
                break
        if saw_quit:
            note("PASS: QUIT event present after request_quit (optional AC)")
        else:
            note(
                "NOTE: no QUIT event observed on poll (optional; should_exit is primary)"
            )

        note(
            "NOTE: live window X (CloseRequested → should_exit without early "
            "event_loop.exit) is AC Q2 — human/verify, not this gate"
        )
        note("path=request_quit → should_exit → wait_until (cooperative Q-A)")

    except Exception:
        ok = False
        lines.append("EXCEPTION:")
        lines.append(traceback.format_exc())

    status = "ok=True" if ok else "ok=False"
    body = status + "\n" + "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(body)
    print(body, end="")

    # Always signal quit so the host process exits even on failure.
    try:
        renpy_host.request_quit()
    except Exception:
        pass

    if not ok:
        raise RuntimeError(f"quit_clean_exit_probe failed; see {out_path}")
    return 0


main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
