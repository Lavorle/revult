"""
Phase 0 empty-RTT alpha probe (task #27).

Gate name: empty_rtt_alpha  (RENPY_HOST_GATE=empty_rtt_alpha)

Sequence:
  create_render_texture → begin_target → begin_frame → empty draw
  → end_frame_present → end_target → read_texture_rgba

Records clear alpha of emptied RTT pixels (hypothesis: a≈1.0 today because
encode_pass always LoadOp::Clear(self.clear_color) with a=1.0).

Also samples game RT after a present-side empty frame for contrast.
"""

import os
from pathlib import Path
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

import renpy_host


def _repo_root() -> Path:
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return Path(env)
    return Path.cwd()


def _result_path() -> Path:
    return _repo_root() / "host" / "target" / "gate-empty_rtt_alpha.txt"


def _sample_pixels(rgba: bytes, w: int, h: int):
    """Return list of (label, x, y, r, g, b, a_byte, a_norm)."""
    points = [
        ("center", w // 2, h // 2),
        ("top_left", 0, 0),
        ("top_right", w - 1, 0),
        ("bottom_left", 0, h - 1),
        ("bottom_right", w - 1, h - 1),
        ("mid_left", 0, h // 2),
        ("mid_right", w - 1, h // 2),
    ]
    out = []
    for label, x, y in points:
        i = (y * w + x) * 4
        r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
        out.append((label, x, y, r, g, b, a, a / 255.0))
    return out


def _mean_rgba(rgba: bytes):
    n = len(rgba) // 4
    if n == 0:
        return (0.0, 0.0, 0.0, 0.0)
    mr = sum(rgba[i] for i in range(0, len(rgba), 4)) / n
    mg = sum(rgba[i + 1] for i in range(0, len(rgba), 4)) / n
    mb = sum(rgba[i + 2] for i in range(0, len(rgba), 4)) / n
    ma = sum(rgba[i + 3] for i in range(0, len(rgba), 4)) / n
    return (mr, mg, mb, ma)


def _unique_alphas(rgba: bytes):
    alphas = set(rgba[i + 3] for i in range(0, len(rgba), 4))
    return sorted(alphas)


# --- Empty RTT probe -------------------------------------------------------
RTT_W, RTT_H = 64, 64
rtt = renpy_host.create_render_texture(RTT_W, RTT_H)

# Empty draw into RTT: no draw_model calls.
renpy_host.begin_target(rtt)
renpy_host.begin_frame()
# intentionally empty
renpy_host.end_frame_present()
renpy_host.end_target()

w, h, rgba = renpy_host.read_texture_rgba(rtt)
assert (w, h) == (RTT_W, RTT_H), (w, h)
assert len(rgba) == RTT_W * RTT_H * 4, len(rgba)

rtt_samples = _sample_pixels(rgba, w, h)
rtt_mean = _mean_rgba(rgba)
rtt_alphas = _unique_alphas(rgba)

# --- Present-side empty frame (game RT / swapchain path) -------------------
renpy_host.begin_frame()
# empty present-side draw
renpy_host.end_frame_present()
gw, gh, grgba = renpy_host.read_game_rt_rgba()
game_samples = _sample_pixels(grgba, gw, gh)
game_mean = _mean_rgba(grgba)
game_alphas = _unique_alphas(grgba)

# --- Nested empty RTT then present (poison check baseline) -----------------
rtt2 = renpy_host.create_render_texture(32, 32)
renpy_host.begin_target(rtt2)
renpy_host.begin_frame()
renpy_host.end_frame_present()
renpy_host.end_target()
w2, h2, rgba2 = renpy_host.read_texture_rgba(rtt2)
rtt2_mean = _mean_rgba(rgba2)

renpy_host.begin_frame()
renpy_host.end_frame_present()
gw2, gh2, grgba2 = renpy_host.read_game_rt_rgba()
game2_mean = _mean_rgba(grgba2)
game2_alphas = _unique_alphas(grgba2)

# Hypothesis: current clear_color.a = 1.0 applies to RTT too → a≈1.0 (255)
center_a = rtt_samples[0][6]  # byte
center_a_norm = rtt_samples[0][7]
hypothesis_holds = all(s[6] == 255 for s in rtt_samples) and rtt_alphas == [255]

lines = []
lines.append("gate=empty_rtt_alpha")
lines.append(f"ok={str(True)}")  # measurement always succeeds; value is the data
lines.append(f"rtt_handle={rtt}")
lines.append(f"rtt_size={w}x{h}")
lines.append(f"rtt_mean_rgba={rtt_mean[0]:.2f},{rtt_mean[1]:.2f},{rtt_mean[2]:.2f},{rtt_mean[3]:.2f}")
lines.append(f"rtt_unique_alphas={rtt_alphas}")
lines.append(f"rtt_center_a_byte={center_a}")
lines.append(f"rtt_center_a_norm={center_a_norm:.4f}")
lines.append(f"hypothesis_a_is_1={hypothesis_holds}")
for label, x, y, r, g, b, a, an in rtt_samples:
    lines.append(f"rtt_sample {label}=({x},{y}) rgba=({r},{g},{b},{a}) a_norm={an:.4f}")
lines.append(
    f"game_rt_size={gw}x{gh} mean_rgba={game_mean[0]:.2f},{game_mean[1]:.2f},{game_mean[2]:.2f},{game_mean[3]:.2f} unique_alphas={game_alphas}"
)
for label, x, y, r, g, b, a, an in game_samples[:3]:
    lines.append(f"game_sample {label}=({x},{y}) rgba=({r},{g},{b},{a}) a_norm={an:.4f}")
lines.append(
    f"nested_rtt2_mean={rtt2_mean[0]:.2f},{rtt2_mean[1]:.2f},{rtt2_mean[2]:.2f},{rtt2_mean[3]:.2f}"
)
lines.append(
    f"post_nested_game_mean={game2_mean[0]:.2f},{game2_mean[1]:.2f},{game2_mean[2]:.2f},{game2_mean[3]:.2f} unique_alphas={game2_alphas}"
)
lines.append(
    "note=pre-A1 baseline; encode_pass always Clear(self.clear_color) with a=1.0"
)

msg = "\n".join(lines) + "\n"
out = _result_path()
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(msg, encoding="utf-8")
print(msg, flush=True)
renpy_host.request_quit()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
