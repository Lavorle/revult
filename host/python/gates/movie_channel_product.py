"""
AC-M2 + AC-M5 movie_channel_product — product Movie channel Surface path.

Gate name: movie_channel_product  (RENPY_HOST_GATE=movie_channel_product)

Proves renpysound_host video_ready + read_video feed stock get_movie_texture:
  1. set_video + play with main_menu.webm name
  2. video_ready True
  3. read_video returns Surface-like with get_size + _pixels
  4. AC-M5: in default present 1b, read_video returns the decode-size Surface;
     the 1920×1080 layout size is produced by Movie.render scaling the texture
     to the offered box. The gate asserts both halves of that contract.
  5. non-black mean over sampled pixels
  6. optional AC-M2b: frame change after clock advance (if ≥2 frames)

Note: no from __future__; host run_file prepends imports.
"""

import os
from pathlib import Path


import renpy_host  # type: ignore

import renpy
from renpy.audio import renpysound_host as rps

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-movie_channel_product.txt"
out.parent.mkdir(parents=True, exist_ok=True)

CH = 7  # avoid colliding with default movie=0 used by other smokes


def _find_media():
    env = os.environ.get("RENPY_HOST_MOVIE_PATH")
    if env and os.path.isfile(env):
        return env, "video/main_menu.webm"
    candidates = [
        _base / "host" / "playtests" / "HuangmeiC" / "game" / "video" / "main_menu.webm",
        Path("/mnt/nvme0n1p2/@home/isah1221/huangmeic/recovered_project/video/main_menu.webm"),
        _base / "tutorial" / "game" / "oa4_launch.webm",
    ]
    for p in candidates:
        if p.is_file():
            # name is relative identity; path is absolute for resolve fallback
            name = "video/main_menu.webm" if "main_menu" in p.name else p.name
            os.environ.setdefault("RENPY_HOST_MOVIE_PATH", str(p))
            return str(p), name
    return None, None


def _mean_rgb(surf):
    w, h = surf.get_size()
    px = bytes(surf._pixels) if hasattr(surf, "_pixels") else bytes(surf.get_buffer())
    need = w * h * 4
    px = px[:need]
    if not px:
        return 0.0, 0.0, 0.0
    # subsample every 16th pixel for speed
    step = 16 * 4
    n = 0
    rs = gs = bs = 0
    for i in range(0, len(px) - 3, step):
        rs += px[i]
        gs += px[i + 1]
        bs += px[i + 2]
        n += 1
    if n <= 0:
        return 0.0, 0.0, 0.0
    return rs / n, gs / n, bs / n


def _fingerprint(surf):
    px = bytes(surf._pixels) if hasattr(surf, "_pixels") else bytes(surf.get_buffer())
    # cheap: first/mid/last 64 bytes + length
    if not px:
        return (0, b"")
    mid = len(px) // 2
    return (len(px), px[:64] + px[mid : mid + 64] + px[-64:])


lines = []
ok = True


def log(msg):
    lines.append(msg)
    # Prefer raw fd write — partial renpy import can hijack print → renpy.log.
    try:
        os.write(1, (f"[movie_channel_product] {msg}\n").encode("utf-8", "replace"))
    except Exception:
        pass


try:
    path, name = _find_media()
    if not path:
        ok = False
        log("FAIL no media path (ffmpeg product movie required)")
    else:
        log(f"media={path} name={name}")
        # Prefer smaller decode for gate speed; product layout remains 1920×1080
        # after Movie.render upscale (AC-M5). Decode env is A/B only, not layout size.
        # Force present 1b so the gate tests the default product path deterministically.
        os.environ["RENPY_HOST_MOVIE_PRESENT"] = "1b"
        os.environ.setdefault("RENPY_HOST_MOVIE_MAX_FRAMES", "8")
        os.environ.setdefault("RENPY_HOST_MOVIE_W", "320")
        os.environ.setdefault("RENPY_HOST_MOVIE_H", "180")
        os.environ.setdefault("RENPY_HOST_MOVIE_FPS", "10")
        os.environ["RENPY_HOST_MOVIE_PATH"] = path
        # Do not set RENPY_HOST_MOVIE_LAYOUT_*; product default must be 1920×1080.
        decode_w = int(os.environ["RENPY_HOST_MOVIE_W"])
        decode_h = int(os.environ["RENPY_HOST_MOVIE_H"])
        layout_w = int(os.environ.get("RENPY_HOST_MOVIE_LAYOUT_W", "1920"))
        layout_h = int(os.environ.get("RENPY_HOST_MOVIE_LAYOUT_H", "1080"))

        rps.stop(CH)
        rps.set_video(CH, rps.DROP_VIDEO, loop=True)
        # file-like is ignored for path resolve; name carries identity.
        rps.play(CH, file=None, name=name, relative_volume=0.0)

        ready = bool(rps.video_ready(CH))
        log(f"video_ready={ready}")
        if not ready:
            ok = False
            log("FAIL video_ready False (decode failed or path unresolved)")

        surf = rps.read_video(CH)
        if surf is None:
            ok = False
            log("FAIL read_video returned None")
        else:
            size = surf.get_size()
            log("surf_size={} has_pixels={}".format(size, hasattr(surf, "_pixels")))
            # AC-M5 part 1: under present 1b read_video must expose the decode
            # Surface; the layout 1920×1080 render is verified via Movie.render.
            if size != (decode_w, decode_h):
                ok = False
                log(
                    f"FAIL AC-M5 decode surface {size} != expected decode {decode_w}x{decode_h}"
                )
            else:
                log(f"PASS AC-M5 decode surface {size[0]}x{size[1]}")
                ch_meta = rps._channels.get(CH, {})
                log(
                    "decode_size={}x{} frame_w/h={}x{} layout_target={}x{}".format(
                        ch_meta.get("decode_w"),
                        ch_meta.get("decode_h"),
                        ch_meta.get("frame_w"),
                        ch_meta.get("frame_h"),
                        layout_w,
                        layout_h,
                    )
                )

            r, g, b = _mean_rgb(surf)
            log(f"mean_rgb={r:.1f},{g:.1f},{b:.1f}")
            if (r + g + b) < 5.0:
                ok = False
                log("FAIL black/empty frame (mean rgb too low)")
            else:
                log("PASS non-black frame")

            ch0 = rps._channels.get(CH, {})
            idx0 = ch0.get("frame_index")
            fp1 = _fingerprint(surf)
            # Advance ≥1 decode-frame of wall time so frame_index steps (fps default 10).
            fps = float(ch0.get("decode_fps") or os.environ.get("RENPY_HOST_MOVIE_FPS") or 10)
            wait_ms = max(250, int(1000.0 / max(fps, 1.0)) + 50)
            t0 = renpy_host.get_ticks_ms()
            renpy_host.wait_until(t0 + wait_ms)
            pos = float(rps.get_pos(CH))
            log("pos_after_wait=%.4f wait_ms=%d" % (pos, wait_ms))  # noqa: UP031
            surf2 = rps.read_video(CH)
            if surf2 is not None:
                fp2 = _fingerprint(surf2)
                ch = rps._channels.get(CH, {})
                nframes = len(ch.get("frames") or [])
                idx1 = ch.get("frame_index")
                log("nframes=%d frame_index=%s→%s" % (nframes, idx0, idx1))  # noqa: UP031
                if nframes >= 2:
                    # AC-M2b optional: prefer change; if single decode still non-black OK
                    if fp1 != fp2 or (idx0 is not None and idx1 is not None and idx0 != idx1):
                        log("PASS AC-M2b frame_change")
                    else:
                        # may still be same if wait too short for fps step
                        log("WARN AC-M2b no frame change (non-blocking if non-black)")
                else:
                    log("AC-M2b skip (single frame cache)")
            else:
                ok = False
                log("FAIL second read_video None")

        # AC-M5 part 2: Movie.render must produce the offered 1920×1080 layout
        # even though read_video returned the 320×180 decode Surface (present 1b).
        try:
            import types as _types

            import renpy.display.matrix as _matrix  # noqa: F401
            import renpy.display.render as _render  # noqa: F401
            import renpy.style as _style  # noqa: F401

            import renpy.audio.music as _music  # noqa: F401
            import renpy.config as _config  # noqa: F401
            import renpy.display.displayable as _displayable  # noqa: F401
            import renpy.easy as _easy  # noqa: F401
            import renpy.game as _game  # noqa: F401
            import renpy.loader as _loader  # noqa: F401
            import renpy.object as _object  # noqa: F401
            import renpy.revertable as _revertable  # noqa: F401
            from renpy.display.video import resize_movie

            # Minimal product-like preferences for loader calls.
            if getattr(renpy.game, "preferences", None) is None:
                renpy.game.preferences = _types.SimpleNamespace(
                    language=None,
                    video_image_fallback=False,
                )

            # Mirror Movie.render's present-1b scaling path: read_video gives
            # the decode-size Surface; resize_movie produces the offered
            # layout-size Render.
            rv = renpy.display.render.Render(size[0], size[1])
            rv.blit(surf, (0, 0))
            rv = resize_movie(rv, layout_w, layout_h)
            rv_size = rv.get_size()
            log(f"movie_render_size={rv_size}")
            if rv_size != (layout_w, layout_h):
                ok = False
                log(
                    f"FAIL AC-M5 layout render {rv_size} != ({layout_w}, {layout_h})"
                )
            else:
                log("PASS AC-M5 layout render {}x{}".format(*rv_size))
        except Exception as e:
            ok = False
            log(f"FAIL AC-M5 layout render exception: {type(e).__name__}: {e}")
            import traceback

            log(traceback.format_exc())

        rps.stop(CH)

except Exception as e:
    ok = False
    log(f"FAIL exception: {type(e).__name__}: {e}")
    import traceback

    log(traceback.format_exc())

lines.append(f"ok={ok}")
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
log(f"WROTE {out} ok={ok}")
try:
    renpy_host.request_quit()
except Exception:
    pass
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
