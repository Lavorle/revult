"""
Phase 6 gate: video texture upload + A/V clock smoke.

Writes host/target/gate-video.txt. Raises on failure.

Note: loaded via py.run; host run_file injects a preamble, so no
`from __future__` here (must be first statement of a module).
"""

import os

import renpy_host  # type: ignore

from renpy.wgpu.video import play_movie_smoke

# --- harness (thin wrapper, original logic preserved) ---
try:
    from _harness import gate_harness, parametrized_gate  # type: ignore
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate  # type: ignore
    except ImportError:
        gate_harness = None  # type: ignore
        parametrized_gate = None  # type: ignore
# fallback



def _write_texture_smoke() -> dict:
    """Direct write_texture_rgba roundtrip without VideoTexture helper."""
    w, h = 4, 4
    red = bytes([255, 0, 0, 255] * (w * h))
    green = bytes([0, 255, 0, 255] * (w * h))
    tex = renpy_host.create_texture_rgba(w, h, red)
    renpy_host.write_texture_rgba(tex, green)
    verts = [
        -0.5, -0.5, 0.0, 1.0, 1, 1, 1, 1,
         0.5, -0.5, 1.0, 1.0, 1, 1, 1, 1,
         0.5,  0.5, 1.0, 0.0, 1, 1, 1, 1,
        -0.5,  0.5, 0.0, 0.0, 1, 1, 1, 1,
    ]
    mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
    pipe = renpy_host.textured_pipeline()
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex)
    renpy_host.end_frame_present()
    renpy_host.destroy_texture(tex)
    renpy_host.destroy_mesh(mesh)
    return {"write_ok": True, "tex_size": (w, h)}


def main() -> None:
    base = os.environ.get("RENPY_HOST_BASE", ".")
    out_path = os.path.join(base, "host", "target", "gate-video.txt")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    wt = _write_texture_smoke()

    media = os.path.join(base, "tutorial", "game", "oa4_launch.webm")
    if not os.path.isfile(media):
        media = None

    smoke = play_movie_smoke(
        width=64,
        height=64,
        frame_count=8,
        media_path=media,
        channel=0,
        frame_ms=16,
    )

    ok = (
        bool(wt.get("write_ok"))
        and int(smoke.get("frames", 0)) >= 4
        and smoke.get("clock_ok") is True
        and float(smoke.get("pos") or 0.0) > 0.0
    )
    msg = (
        "[video-gate] write_ok={} source={} frames={} pos={:.4f} "
        "ffmpeg={} clock_ok={} phase={} ok={}".format(
            wt.get("write_ok"),
            smoke.get("source"),
            smoke.get("frames"),
            float(smoke.get("pos") or 0.0),
            smoke.get("ffmpeg"),
            smoke.get("clock_ok"),
            getattr(renpy_host, "PHASE", "?"),
            ok,
        )
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)
    if not ok:
        raise RuntimeError(msg)
    renpy_host.request_quit()


if __name__ == "__main__":
    main()

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)

