"""
Shared MAE helpers for wgpu golden gates (AC6).

Metric: mean absolute error ≤ 2/255; max channel delta ≤ 16.
Capture: pre-present game RT (read_game_rt_rgba).
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

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


def try_write_png(path: Path, w: int, h: int, rgba: bytes) -> None:
    try:
        from PIL import Image  # type: ignore

        path.parent.mkdir(parents=True, exist_ok=True)
        Image.frombytes("RGBA", (int(w), int(h)), bytes(rgba)).save(path)
    except Exception:
        pass


def mae_compare(actual: bytes, baseline: bytes) -> tuple[float, int]:
    """Return (mean_abs_error_0_1, max_channel_delta_0_255)."""
    n = min(len(actual), len(baseline))
    if n == 0:
        return 1.0, 255
    total = 0
    max_d = 0
    for i in range(n):
        d = abs(actual[i] - baseline[i])
        total += d
        if d > max_d:
            max_d = d
    mean = (total / n) / 255.0
    return mean, max_d


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
    Compare against baseline under testcases/wgpu_golden/<name>/baseline.rgba.

    First run with missing baseline writes it and returns ok=True with
    'baseline written' in the message (explicit log for bootstrap policy).
    """
    gdir = golden_dir(name)
    base_path = gdir / "baseline.rgba"
    png_path = gdir / "baseline.png"
    actual_path = gdir / "actual.rgba"
    actual_png = gdir / "actual.png"

    write_raw_rgba(actual_path, w, h, rgba)
    try_write_png(actual_png, w, h, rgba)

    if not base_path.is_file():
        write_raw_rgba(base_path, w, h, rgba)
        try_write_png(png_path, w, h, rgba)
        msg = (
            f"[{name}] baseline written {w}x{h} bytes={len(rgba)} "
            f"path={base_path} ok=True"
        )
        print(msg, flush=True)
        return True, msg

    bw, bh, baseline = read_raw_rgba(base_path)
    if (bw, bh) != (w, h):
        msg = (
            f"[{name}] size mismatch actual={w}x{h} baseline={bw}x{bh} ok=False"
        )
        print(msg, flush=True)
        return False, msg

    mean, max_d = mae_compare(bytes(rgba), baseline)
    ok = mean <= mean_limit and max_d <= max_delta
    msg = (
        f"[{name}] {w}x{h} MAE_mean={mean:.6f} max_delta={max_d} "
        f"limits=({mean_limit:.6f},{max_delta}) ok={ok}"
    )
    print(msg, flush=True)
    return ok, msg
