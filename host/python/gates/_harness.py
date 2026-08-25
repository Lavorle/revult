"""
_harness — parametrized gate harness for wgpu golden gates.

Replaces duplicated boilerplate across ~134 gate files:

- repo_root / gate_result_path / json report / MAE compare are unified
- reuses pure helpers from golden_mae (repo_root, gate_result_path,
  evaluate_golden, mae_compare, compare_or_bootstrap) without re-implementing

Usage — imperative:

    from _harness import gate_harness
    from golden_mae import compare_or_bootstrap

    def run_one(case):
        # ... draw with case["amount"] ...
        w, h, rgba = renpy_host.read_game_rt_rgba()
        return w, h, rgba

    def golden_compare(w, h, rgba):
        return compare_or_bootstrap("my_golden", w, h, rgba)

    gate_harness("my_gate", [{"amount": 0.0}, {"amount": 1.0}], run_one, golden_compare)

Usage — pytest parametrize:

    from _harness import parametrized_gate

    @parametrized_gate("dissolve", [{"amount": 0.0}, {"amount": 0.5}, {"amount": 1.0}])
    def run_case(case):
        ...  # case is one dict
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

# Reuse pure helpers from golden_mae — never duplicate MAE logic.
try:
    from golden_mae import (  # gates/ is on sys.path when run via renpy_host
        MAE_MAX_DELTA,
        MAE_MEAN_LIMIT,
        compare_or_bootstrap,
        evaluate_golden,
        gate_result_path,
        mae_compare,
        repo_root,
    )
except ImportError:  # `python -m host.python.gates._harness` / namespace import
    from host.python.gates.golden_mae import (  # type: ignore[no-redef]
        MAE_MAX_DELTA,
        MAE_MEAN_LIMIT,
        compare_or_bootstrap,
        evaluate_golden,
        gate_result_path,
        mae_compare,
        repo_root,
    )


def _safe_write(msg: str) -> None:
    data = (msg if msg.endswith("\n") else msg + "\n").encode("utf-8", "replace")
    try:
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
    except Exception:
        try:
            sys.stdout.write(msg if msg.endswith("\n") else msg + "\n")
            sys.stdout.flush()
        except Exception:
            pass


def parametrized_gate(name: str, cases: list[dict[str, Any]]):
    """Decorator for pytest parametrization.

    Example::

        @parametrized_gate("dissolve", [{"amount": 0.0}, {"amount": 0.5}])
        def run_case(case):
            assert 0.0 <= case["amount"] <= 1.0
    """

    def decorator(fn: Callable) -> Callable:
        try:
            import pytest  # type: ignore

            ids = []
            for i, c in enumerate(cases):
                if c:
                    tag = "_".join(f"{k}={v}" for k, v in c.items())
                    ids.append(f"{name}[{i}][{tag}]")
                else:
                    ids.append(f"{name}[{i}]")
            return pytest.mark.parametrize("case", cases, ids=ids)(fn)  # type: ignore[return-value]
        except ImportError:
            # pytest not installed — keep function usable; stash cases for introspection
            fn._parametrized_cases = cases  # type: ignore[attr-defined]
            fn._gate_name = name  # type: ignore[attr-defined]
            return fn

    return decorator


def gate_harness(
    name: str,
    params: list[dict[str, Any]],
    run_one: Callable[[dict[str, Any]], Any],
    golden_compare: Callable[..., tuple[bool, str]],
) -> bool:
    """Run parametrized gate, unify repo_root / gate_result_path / json / MAE.

    - ``name``: gate name for ``gate_result_path(name)`` (e.g. ``"g01"``)
    - ``params``: list of dicts, each passed to ``run_one`` (use ``[{}]`` for single run)
    - ``run_one``: ``case -> (w,h,rgba)`` or ``case -> (ok,msg)``; exceptions become FAIL
    - ``golden_compare``: ``(w,h,rgba) -> (ok,msg)`` — typically
      ``lambda w,h,rgba: compare_or_bootstrap("golden_name", w,h,rgba)``
      or a thin wrapper around ``evaluate_golden``/``mae_compare``.

    Writes ``host/target/gate-{name}.txt`` and ``host/target/gate-{name}.json``
    and mirrors the txt to stdout. Returns overall ``ok``.
    """
    cases = params if params else [{}]
    results: list[dict[str, Any]] = []
    notes: list[str] = []
    overall_ok = True

    for idx, case in enumerate(cases):
        label = f"case {idx} {case}" if case else f"case {idx}"
        try:
            out = run_one(case)
        except Exception as e:
            msg = f"[{name}] {label} FAIL: run_one {type(e).__name__}: {e}"
            notes.append(msg)
            results.append({"case": case, "passed": False, "error": msg})
            overall_ok = False
            continue

        # Normalise run_one output → (w,h,rgba) then delegate to golden_compare
        try:
            if isinstance(out, tuple) and len(out) == 3 and isinstance(out[0], int):
                # (w, h, rgba)
                w, h, rgba = out  # type: ignore[misc]
                ok, msg = golden_compare(w, h, rgba)
                results.append({"case": case, "passed": ok, "message": msg, "w": w, "h": h})
            elif isinstance(out, tuple) and len(out) == 2 and isinstance(out[0], bool):
                # already (ok, msg)
                ok, msg = out  # type: ignore[misc]
                results.append({"case": case, "passed": ok, "message": msg})
            elif isinstance(out, dict) and "passed" in out:
                ok = bool(out["passed"])
                msg = str(out.get("message") or out.get("msg") or "")
                results.append({"case": case, **out})
            else:
                # fallback: let golden_compare decide (may raise TypeError -> handled)
                ok, msg = golden_compare(out)  # type: ignore[call-arg]
                results.append({"case": case, "passed": ok, "message": msg})
        except Exception as e:
            ok, msg = False, f"[{name}] {label} FAIL: golden_compare {type(e).__name__}: {e}"
            results.append({"case": case, "passed": False, "error": msg})
        notes.append(msg)
        if not ok:
            overall_ok = False

    # Unified report — reuse repo_root / gate_result_path from golden_mae
    txt_lines = [f"gate={name}", f"ok={overall_ok}"] + notes
    txt = "\n".join(txt_lines) + "\n"
    out_txt = gate_result_path(name)
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text(txt, encoding="utf-8")

    out_json = out_txt.with_suffix(".json")
    payload = {
        "gate": name,
        "ok": overall_ok,
        "repo_root": str(repo_root()),
        "result_txt": str(out_txt),
        "cases": results,
        "notes": notes,
        "thresholds": {"mean_limit": MAE_MEAN_LIMIT, "max_delta": MAE_MAX_DELTA},
    }
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _safe_write(txt)
    return overall_ok


__all__ = [
    "gate_harness",
    "parametrized_gate",
    "repo_root",
    "gate_result_path",
    "evaluate_golden",
    "mae_compare",
    "compare_or_bootstrap",
]
