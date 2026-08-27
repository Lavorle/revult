"""
Shared pure MAE helpers for wgpu golden gates.

Metric: mean absolute error <= 2/255; max channel delta <= 16.
Capture: pre-present game RT (read_game_rt_rgba).

STRICT PURE EVALUATION CONTRACT:
- NEVER write baseline files implicitly to disk on missing baseline.
- Fail closed immediately with non-zero exit code if baseline is missing or dimensions mismatch.
- Precise pixel-level RGBA channel MAE comparison without truncation.
- Return structured JSON results and clear exit status.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any

MAE_MEAN_LIMIT = 2.0 / 255.0
MAE_MAX_DELTA = 16


def repo_root() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    # host/python/gates -> host/python -> host -> repo
    here = Path(__file__).resolve()
    for p in [here.parent, *here.parents]:
        if (p / "renpy").is_dir() and (p / "host" / "README.md").is_file():
            return p
    return Path.cwd()


def golden_dir(name: str) -> Path:
    return repo_root() / "testcases" / "wgpu_golden" / name


def gate_result_path(name: str) -> Path:
    return repo_root() / "host" / "target" / f"gate-{name}.txt"


def write_raw_rgba(path: Path, w: int, h: int, rgba: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # header: magic + w + h (little-endian u32) then tight RGBA
    header = b"RGBA" + struct.pack("<II", int(w), int(h))
    path.write_bytes(header + bytes(rgba))


def read_raw_rgba(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"RGBA":
        raise ValueError(f"bad golden format: {path}")
    w, h = struct.unpack("<II", data[4:12])
    body = data[12:]
    expect = w * h * 4
    if len(body) < expect:
        raise ValueError(f"short golden body: {len(body)} < {expect}")
    return w, h, body[:expect]


def load_image_or_rgba(path: Path) -> tuple[int, int, bytes]:
    """Load image from .rgba or image file (png, jpg, etc.)."""
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    if path.suffix.lower() == ".rgba":
        return read_raw_rgba(path)

    # Try loading with PIL
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as img:
            img_rgba = img.convert("RGBA")
            w, h = img_rgba.size
            return w, h, img_rgba.tobytes()
    except Exception as e:
        raise ValueError(f"Unable to read image at {path}: {e}") from e


def try_write_png(path: Path, w: int, h: int, rgba: bytes) -> None:
    try:
        from PIL import Image  # type: ignore

        path.parent.mkdir(parents=True, exist_ok=True)
        Image.frombytes("RGBA", (int(w), int(h)), bytes(rgba)).save(path)
    except Exception:
        pass


def mae_compare(actual: bytes, baseline: bytes) -> tuple[float, int, int]:
    """Return (mean_abs_error_0_1, max_channel_delta_0_255, mismatch_count)."""
    if len(actual) != len(baseline):
        raise ValueError(f"buffer length mismatch: {len(actual)} != {len(baseline)}")

    n = len(actual)
    if n == 0:
        return 0.0, 0, 0

    total = 0
    max_d = 0
    mismatches = 0
    for i in range(n):
        d = abs(actual[i] - baseline[i])
        if d > 0:
            mismatches += 1
            total += d
            max_d = max(max_d, d)

    mean = (total / n) / 255.0
    return mean, max_d, mismatches


def evaluate_golden(
    actual_w: int,
    actual_h: int,
    actual_rgba: bytes,
    baseline_w: int,
    baseline_h: int,
    baseline_rgba: bytes,
    *,
    mean_limit: float = MAE_MEAN_LIMIT,
    max_delta: int = MAE_MAX_DELTA,
) -> dict[str, Any]:
    """Pure golden evaluation function with structured dict output."""
    dim_match = (actual_w, actual_h) == (baseline_w, baseline_h)
    if not dim_match:
        return {
            "status": "FAIL",
            "passed": False,
            "error": f"Dimension mismatch: actual={actual_w}x{actual_h} vs baseline={baseline_w}x{baseline_h}",
            "dimension_match": False,
            "actual_dimensions": [actual_w, actual_h],
            "baseline_dimensions": [baseline_w, baseline_h],
            "mae": None,
            "max_delta": None,
            "mismatch_count": None,
            "thresholds": {"mean_limit": mean_limit, "max_delta": max_delta},
        }

    expected_len = actual_w * actual_h * 4
    if len(actual_rgba) != expected_len or len(baseline_rgba) != expected_len:
        return {
            "status": "FAIL",
            "passed": False,
            "error": f"Buffer length invalid: actual={len(actual_rgba)}, baseline={len(baseline_rgba)}, expected={expected_len}",
            "dimension_match": True,
            "actual_dimensions": [actual_w, actual_h],
            "baseline_dimensions": [baseline_w, baseline_h],
            "mae": None,
            "max_delta": None,
            "mismatch_count": None,
            "thresholds": {"mean_limit": mean_limit, "max_delta": max_delta},
        }

    mean, max_d, mismatches = mae_compare(bytes(actual_rgba), bytes(baseline_rgba))
    passed = mean <= mean_limit and max_d <= max_delta

    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "error": None if passed else f"MAE {mean:.6f} > {mean_limit:.6f} or max delta {max_d} > {max_delta}",
        "dimension_match": True,
        "actual_dimensions": [actual_w, actual_h],
        "baseline_dimensions": [baseline_w, baseline_h],
        "mae": mean,
        "max_delta": max_d,
        "mismatch_count": mismatches,
        "thresholds": {"mean_limit": mean_limit, "max_delta": max_delta},
    }


def compare_or_bootstrap(
    name: str,
    w: int,
    h: int,
    rgba: bytes,
    *,
    mean_limit: float = MAE_MEAN_LIMIT,
    max_delta: int = MAE_MAX_DELTA,
) -> tuple[bool, str]:
    """
    Strictly pure Golden comparator:
    - NEVER writes baseline images implicitly or on missing file.
    - Writes actual.rgba / actual.png for diagnostic observation only if golden dir exists.
    - Strictly validates image dimensions and buffer sizes.
    - Compares MAE and max channel delta.
    - Returns (ok, message).
    """
    gdir = golden_dir(name)
    base_path = gdir / "baseline.rgba"
    actual_path = gdir / "actual.rgba"
    actual_png = gdir / "actual.png"

    if not base_path.is_file():
        msg = (
            f"[{name}] FAIL-CLOSED: baseline missing at {base_path} "
            f"(pure golden comparator never writes baseline implicitly) ok=False"
        )
        print(msg, flush=True)
        return False, msg

    # Diagnostic write when baseline exists
    write_raw_rgba(actual_path, w, h, rgba)
    try_write_png(actual_png, w, h, rgba)

    if not base_path.is_file():
        msg = (
            f"[{name}] FAIL-CLOSED: baseline missing at {base_path} "
            f"(pure golden comparator never writes baseline implicitly) ok=False"
        )
        print(msg, flush=True)
        return False, msg

    try:
        bw, bh, baseline = read_raw_rgba(base_path)
    except Exception as e:
        msg = f"[{name}] FAIL-CLOSED: failed reading baseline at {base_path}: {e} ok=False"
        print(msg, flush=True)
        return False, msg

    res = evaluate_golden(
        w,
        h,
        rgba,
        bw,
        bh,
        baseline,
        mean_limit=mean_limit,
        max_delta=max_delta,
    )

    if not res["passed"]:
        msg = (
            f"[{name}] FAIL: {res.get('error')} "
            f"actual={w}x{h} baseline={bw}x{bh} "
            f"MAE_mean={res['mae'] if res['mae'] is not None else -1:.6f} "
            f"max_delta={res['max_delta'] if res['max_delta'] is not None else -1} "
            f"mismatches={res['mismatch_count']} ok=False"
        )
        print(msg, flush=True)
        return False, msg

    msg = (
        f"[{name}] {w}x{h} MAE_mean={res['mae']:.6f} max_delta={res['max_delta']} "
        f"mismatches={res['mismatch_count']} dimension_match=True "
        f"limits=({mean_limit:.6f},{max_delta}) ok=True"
    )
    print(msg, flush=True)
    return True, msg


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pure strict MAE Golden image evaluation tool."
    )
    parser.add_argument(
        "--actual",
        type=Path,
        required=True,
        help="Path to actual image file (.rgba or .png)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Path to baseline image file (.rgba or .png)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=MAE_MEAN_LIMIT,
        help=f"Mean absolute error limit (0.0 to 1.0, default: {MAE_MEAN_LIMIT:.6f})",
    )
    parser.add_argument(
        "--max-delta",
        type=int,
        default=MAE_MAX_DELTA,
        help=f"Max channel delta limit (0 to 255, default: {MAE_MAX_DELTA})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write structured JSON result metrics",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON to stdout",
    )

    args = parser.parse_args()

    # Fail closed check for baseline
    if not args.baseline.is_file():
        err_res = {
            "status": "FAIL",
            "passed": False,
            "error": f"Baseline file does not exist: {args.baseline}",
            "dimension_match": False,
            "actual_dimensions": None,
            "baseline_dimensions": None,
            "mae": None,
            "max_delta": None,
            "mismatch_count": None,
            "thresholds": {"mean_limit": args.threshold, "max_delta": args.max_delta},
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(err_res, indent=2) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(err_res, indent=2))
        else:
            print(f"ERROR: {err_res['error']}", file=sys.stderr)
        return 1

    # Fail closed check for actual
    if not args.actual.is_file():
        err_res = {
            "status": "FAIL",
            "passed": False,
            "error": f"Actual file does not exist: {args.actual}",
            "dimension_match": False,
            "actual_dimensions": None,
            "baseline_dimensions": None,
            "mae": None,
            "max_delta": None,
            "mismatch_count": None,
            "thresholds": {"mean_limit": args.threshold, "max_delta": args.max_delta},
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(err_res, indent=2) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(err_res, indent=2))
        else:
            print(f"ERROR: {err_res['error']}", file=sys.stderr)
        return 1

    try:
        aw, ah, actual_rgba = load_image_or_rgba(args.actual)
        bw, bh, baseline_rgba = load_image_or_rgba(args.baseline)
    except Exception as e:
        err_res = {
            "status": "FAIL",
            "passed": False,
            "error": f"Image load failed: {e}",
            "dimension_match": False,
            "actual_dimensions": None,
            "baseline_dimensions": None,
            "mae": None,
            "max_delta": None,
            "mismatch_count": None,
            "thresholds": {"mean_limit": args.threshold, "max_delta": args.max_delta},
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(err_res, indent=2) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(err_res, indent=2))
        else:
            print(f"ERROR: {err_res['error']}", file=sys.stderr)
        return 1

    res = evaluate_golden(
        aw,
        ah,
        actual_rgba,
        bw,
        bh,
        baseline_rgba,
        mean_limit=args.threshold,
        max_delta=args.max_delta,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        if res["passed"]:
            print(
                f"PASS: {aw}x{ah} MAE={res['mae']:.6f} (<= {args.threshold:.6f}), "
                f"max_delta={res['max_delta']} (<= {args.max_delta}), mismatches={res['mismatch_count']}"
            )
        else:
            print(f"FAIL: {res['error']}", file=sys.stderr)

    return 0 if res["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
