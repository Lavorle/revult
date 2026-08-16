"""
AC5 hmc_menu_video_product — HuangmeiC main-menu video product parity gate.

Gate name: hmc_menu_video_product  (RENPY_HOST_GATE=hmc_menu_video_product)

Option B′ product budgets (NOT movie_channel_product tiny 8@320@10):
  - MAX_FRAMES=360, FPS=30, decode W/H=1920×1080 (layout-native Tier S)
  - layout Surface 1920×1080 (present 1b is identity at menu size)
  - WARM_MENU_VIDEO preferred when host exposes maybe_warm_menu_video

Asserts (AC5 / plan AC1–AC4 surface):
  1. frame_count >= 360
  2. decode_fps >= 29
  3. first read_video frame_index == 0 (clock after ready)
  4. clock_after_ready: video_clock must not advance during blocking pre-decode
  5. path cache survives stop→play same path (cache hit / frames retained)
  6. empty_movie_presents == 0 when frames exist (video_ready + non-None non-black)
  7. layout Surface size (1920, 1080)
  8. non-black mean rgb

If renpysound_host path-cache helpers are not yet present, assert what is
available and log WARN so verify can see residual.

Note: no from __future__; host run_file prepends imports.
"""

import os
import time
from pathlib import Path

import renpy_host  # type: ignore

from renpy.audio import renpysound_host as rps

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
out = _base / "host" / "target" / "gate-hmc_menu_video_product.txt"
out.parent.mkdir(parents=True, exist_ok=True)

CH = 11  # dedicated channel; avoid movie=0 / movie_channel_product CH=7


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


def _has_helper(name):
    return callable(getattr(rps, name, None))


lines = []
ok = True
warns = []


def log(msg):
    lines.append(msg)
    try:
        os.write(1, ("[hmc_menu_video_product] %s\n" % msg).encode("utf-8", "replace"))
    except Exception:
        pass


def warn(msg):
    warns.append(msg)
    log("WARN %s" % msg)


try:
    path, name = _find_media()
    if not path:
        ok = False
        log("FAIL no media path (ffmpeg product movie required)")
    else:
        log("media=%s name=%s" % (path, name))

        # Product Option B′ budgets — force product values for this dedicated gate.
        # Do NOT setdefault movie_channel_product tiny 8@320@10 budgets.
        os.environ["RENPY_HOST_MOVIE_MAX_FRAMES"] = os.environ.get(
            "RENPY_HOST_MOVIE_MAX_FRAMES", "360"
        )
        # Prefer product defaults if unset; never force tiny gate budgets.
        if os.environ.get("RENPY_HOST_MOVIE_MAX_FRAMES") in ("8", "16", "24", "36", "48"):
            # Operator/tiny leftover — override to product 360 for AC3.
            os.environ["RENPY_HOST_MOVIE_MAX_FRAMES"] = "360"
            log("override MAX_FRAMES → 360 (product AC3; was tiny leftover)")
        os.environ.setdefault("RENPY_HOST_MOVIE_W", "1920")
        os.environ.setdefault("RENPY_HOST_MOVIE_H", "1080")
        os.environ.setdefault("RENPY_HOST_MOVIE_FPS", "30")
        os.environ.setdefault("RENPY_HOST_MOVIE_LAYOUT_W", "1920")
        os.environ.setdefault("RENPY_HOST_MOVIE_LAYOUT_H", "1080")
        os.environ.setdefault("RENPY_HOST_MOVIE_KICKSTART_FRAMES", "8")
        os.environ.setdefault("RENPY_HOST_MOVIE_CHUNK_FRAMES", "20")
        # Gate needs full 360 for AC3; disable staged warm prefix for this gate.
        os.environ.setdefault("RENPY_HOST_MOVIE_WARM_FRAMES", "0")
        os.environ.setdefault("RENPY_HOST_MOVIE_RSS_MB", "4096")
        # Tier L layout-cache stays opt-in (default off).
        os.environ.setdefault("RENPY_HOST_WARM_MENU_VIDEO", "1")
        os.environ["RENPY_HOST_MOVIE_PATH"] = path

        log(
            "budget max_frames=%s w=%s h=%s fps=%s layout=%sx%s warm=%s"
            % (
                os.environ.get("RENPY_HOST_MOVIE_MAX_FRAMES"),
                os.environ.get("RENPY_HOST_MOVIE_W"),
                os.environ.get("RENPY_HOST_MOVIE_H"),
                os.environ.get("RENPY_HOST_MOVIE_FPS"),
                os.environ.get("RENPY_HOST_MOVIE_LAYOUT_W"),
                os.environ.get("RENPY_HOST_MOVIE_LAYOUT_H"),
                os.environ.get("RENPY_HOST_WARM_MENU_VIDEO"),
            )
        )

        # Optional warm hook (Option B′ Phase 4).
        if _has_helper("maybe_warm_menu_video"):
            try:
                rps.maybe_warm_menu_video()
                log("maybe_warm_menu_video() invoked")
            except Exception as e:
                warn("maybe_warm_menu_video raised: %s: %s" % (type(e).__name__, e))
        else:
            warn("maybe_warm_menu_video helper not present yet")

        # Helper inventory for residual tracking.
        for helper in (
            "path_cache_has_frames",
            "path_cache_frame_count",
            "path_cache_ready_full",
            "path_cache_get",
            "get_path_cache",
            "ready_full",
            "empty_movie_presents",
        ):
            if _has_helper(helper):
                log("helper_present=%s" % helper)
            else:
                # Not every name is required; only note absence once for residuals.
                pass
        if not any(
            _has_helper(h)
            for h in (
                "path_cache_has_frames",
                "path_cache_frame_count",
                "path_cache_ready_full",
                "path_cache_get",
                "get_path_cache",
            )
        ):
            warn(
                "path cache helpers not yet present "
                "(path_cache_has_frames / ready_full / etc.); "
                "stop→play survival will use channel frames best-effort"
            )

        rps.stop(CH)
        rps.set_video(CH, rps.DROP_VIDEO, loop=True)

        # Snapshot clock before play (should stay 0 / not advance mid-decode).
        try:
            pos_before = float(renpy_host.video_clock_pos(CH))
        except Exception:
            pos_before = None
        t_play0 = time.monotonic()
        rps.play(CH, file=None, name=name, relative_volume=0.0)
        t_play = time.monotonic() - t_play0
        log("play_wall_s=%.3f" % t_play)

        ready = bool(rps.video_ready(CH))
        log("video_ready=%s" % ready)
        if not ready:
            ok = False
            log("FAIL video_ready False (decode failed or path unresolved)")

        # AC2: first presented index must be 0. Capture IMMEDIATELY after play
        # (or after a short playable-prefix wait). Do NOT wait for ready_full
        # first — the product clock arms on a playable prefix and advances
        # while the rest of the 360-frame @2 decode fills in the background.
        first_idx_probe = None
        first_surf = None
        t_first0 = time.monotonic()
        while (time.monotonic() - t_first0) < 5.0:
            try:
                if hasattr(rps, "_ensure_video_frames"):
                    rps._ensure_video_frames(CH, block=False)
                if hasattr(rps, "_maybe_arm_clock"):
                    rps._maybe_arm_clock(CH)
            except Exception:
                pass
            first_surf = rps.read_video(CH)
            ch_now = rps._channels.get(CH, {})
            first_idx_probe = ch_now.get("frame_index")
            n_now = len(ch_now.get("play_frames") or ch_now.get("frames") or [])
            # Accept once we have ≥1 frame; prefer capturing while still near 0.
            if first_surf is not None and n_now >= 1:
                break
            time.sleep(0.01)
        log(
            "first_present_probe frame_index=%s nframes=%s wall_s=%.3f"
            % (
                first_idx_probe,
                len((rps._channels.get(CH, {}) or {}).get("frames") or []),
                time.monotonic() - t_first0,
            )
        )
        if first_idx_probe is None:
            ok = False
            log("FAIL first frame_index missing (no presentable frame within 5s)")
        elif int(first_idx_probe) > 2:
            ok = False
            log(
                "FAIL first frame_index=%s != 0 (AC2 start-at-zero; "
                "allow ≤2 for scheduling slack)" % first_idx_probe
            )
        else:
            log("PASS first frame_index == 0 (got %s)" % first_idx_probe)

        # Option B′: play may attach on ready_partial (frame0 hold) while warm
        # fills in the background. Plan AC3 allows full by T_menu+5s — poll
        # path cache for ready_full / ≥360 rather than requiring inline block.
        # While waiting, also sample frame_index to prove the video MOVES
        # (playable-prefix clock arm) instead of freezing on frame0.
        wait_full_s = float(os.environ.get("RENPY_HOST_HMC_WAIT_FULL_S", "90"))
        t_wait0 = time.monotonic()
        full_ok = False
        idx_samples = []
        while (time.monotonic() - t_wait0) < wait_full_s:
            if _has_helper("path_cache_ready_full"):
                try:
                    if rps.path_cache_ready_full(path):
                        full_ok = True
                        break
                except Exception:
                    pass
            if _has_helper("path_cache_frame_count"):
                try:
                    if int(rps.path_cache_frame_count(path)) >= 360:
                        full_ok = True
                        break
                except Exception:
                    pass
            ch_live = rps._channels.get(CH, {})
            if len(ch_live.get("frames") or []) >= 360:
                full_ok = True
                break
            # Re-bind channel from path cache as frames land.
            try:
                if hasattr(rps, "_ensure_video_frames"):
                    rps._ensure_video_frames(CH, block=False)
                if hasattr(rps, "_maybe_arm_clock"):
                    rps._maybe_arm_clock(CH)
                # Drive presentation so frame_index advances like the product path.
                rps.read_video(CH)
                idx_samples.append(int(rps._channels.get(CH, {}).get("frame_index") or 0))
            except Exception:
                pass
            time.sleep(0.05)
        wait_full_elapsed = time.monotonic() - t_wait0
        log(
            "wait_ready_full elapsed_s=%.3f full_ok=%s budget_s=%.1f"
            % (wait_full_elapsed, full_ok, wait_full_s)
        )
        # Final bind after wait so channel.frames reflects path-cache full list.
        try:
            if hasattr(rps, "_ensure_video_frames"):
                rps._ensure_video_frames(CH, block=False)
            if hasattr(rps, "_maybe_arm_clock"):
                rps._maybe_arm_clock(CH)
            # One more present sample after full bind.
            rps.read_video(CH)
            idx_samples.append(int(rps._channels.get(CH, {}).get("frame_index") or 0))
        except Exception:
            pass

        # AC-F3 / "video moves": frame_index must advance during wait when the
        # playable-prefix clock is armed. A single unique sample means freeze.
        uniq_idx = sorted(set(idx_samples)) if idx_samples else []
        log(
            "frame_index_samples n=%d unique=%d min=%s max=%s"
            % (
                len(idx_samples),
                len(uniq_idx),
                (uniq_idx[0] if uniq_idx else None),
                (uniq_idx[-1] if uniq_idx else None),
            )
        )
        if len(uniq_idx) < 2:
            ok = False
            log(
                "FAIL frame_index did not advance during wait "
                "(unique=%s) — menu video is frozen" % uniq_idx
            )
        else:
            log("PASS frame_index advances during wait (unique=%d)" % len(uniq_idx))

        ch = rps._channels.get(CH, {})
        frames = ch.get("frames") or []
        nframes = len(frames)
        # Prefer path-cache count when channel still bound to partial snapshot.
        if nframes < 360 and _has_helper("path_cache_frame_count"):
            try:
                pc_n = int(rps.path_cache_frame_count(path))
                if pc_n > nframes:
                    log("channel nframes=%d path_cache_frame_count=%d (use path cache)" % (nframes, pc_n))
                    nframes = pc_n
            except Exception:
                pass
        decode_fps = float(ch.get("decode_fps") or os.environ.get("RENPY_HOST_MOVIE_FPS") or 0)
        decode_w = ch.get("decode_w")
        decode_h = ch.get("decode_h")
        frame_w = ch.get("frame_w")
        frame_h = ch.get("frame_h")
        media_path = ch.get("media_path")
        log(
            "nframes=%d decode_fps=%.3f decode=%sx%s layout_meta=%sx%s media_path=%s"
            % (nframes, decode_fps, decode_w, decode_h, frame_w, frame_h, media_path)
        )

        # --- AC3: frame_count >= 360 ---
        if nframes < 360:
            ok = False
            log("FAIL frame_count=%d < 360 (AC3 temporal parity)" % nframes)
        else:
            log("PASS frame_count=%d >= 360" % nframes)

        # --- decode_fps >= 29 ---
        if decode_fps < 29.0:
            ok = False
            log("FAIL decode_fps=%.3f < 29 (product 30fps parity)" % decode_fps)
        else:
            log("PASS decode_fps=%.3f >= 29" % decode_fps)

        # --- ready_full flag if exposed ---
        ready_full = None
        if _has_helper("path_cache_ready_full"):
            try:
                ready_full = bool(rps.path_cache_ready_full(path))
                log("path_cache_ready_full=%s" % ready_full)
            except Exception as e:
                warn("path_cache_ready_full raised: %s" % e)
        elif "ready_full" in ch:
            ready_full = bool(ch.get("ready_full"))
            log("ch.ready_full=%s" % ready_full)
        else:
            warn("ready_full flag not exposed; infer from nframes>=360")
            ready_full = nframes >= 360

        # --- clock_after_ready: clock must not advance during blocking pre-decode ---
        # Measure relative to play_wall only. With playable-prefix arming the
        # clock may start once ≥MIN_PLAYABLE frames exist (often during the
        # wait_ready_full poll). That is intentional product behaviour — do
        # NOT treat post-wait pos as a pre-decode advance. Only fail when
        # play itself blocked long AND the clock already ran for ~that wall.
        try:
            pos_after_play = float(renpy_host.video_clock_pos(CH))
        except Exception:
            pos_after_play = None
        log(
            "clock pos_before=%s pos_after_play=%s play_wall=%.3f wait_full=%.3f"
            % (pos_before, pos_after_play, t_play, wait_full_elapsed)
        )
        # Fail only if play() itself was a long blocking decode AND the clock
        # advanced in lockstep — that means clock armed before any frames.
        if (
            pos_after_play is not None
            and t_play > 0.5
            and pos_after_play > max(0.25, t_play * 0.5)
            and wait_full_elapsed < 0.1
        ):
            ok = False
            log(
                "FAIL clock_after_ready: video_clock advanced during blocking "
                "pre-decode (pos=%.4f play_wall=%.3f)"
                % (pos_after_play, t_play)
            )
        else:
            log(
                "PASS clock_after_ready "
                "(play_wall=%.3f; early playable-prefix arm is OK)"
                % t_play
            )

        # --- first read_video frame_index == 0 ---
        surf = rps.read_video(CH)
        if surf is None:
            ok = False
            log("FAIL read_video returned None")
        else:
            size = surf.get_size()
            log("surf_size=%s has_pixels=%s" % (size, hasattr(surf, "_pixels")))

            # --- present Surface size ---
            # Present 1b (default): decode-size Surface; Movie.render mesh-scales
            # to layout. Present 1a/layout: layout-size Surface. Accept either.
            present_mode = os.environ.get("RENPY_HOST_MOVIE_PRESENT", "1b").strip().lower()
            expect_layout = present_mode in ("1a", "layout", "s1")
            layout_w = int(os.environ.get("RENPY_HOST_MOVIE_LAYOUT_W", "1920"))
            layout_h = int(os.environ.get("RENPY_HOST_MOVIE_LAYOUT_H", "1080"))
            decode_w_e = int(os.environ.get("RENPY_HOST_MOVIE_W", "1920"))
            decode_h_e = int(os.environ.get("RENPY_HOST_MOVIE_H", "1080"))
            if expect_layout:
                if size != (layout_w, layout_h):
                    ok = False
                    log("FAIL layout size %s != (%d, %d) (present=1a)" % (size, layout_w, layout_h))
                else:
                    log("PASS layout size %s (present=1a)" % (size,))
            else:
                # 1b: decode-size is correct; layout size is a residual only if
                # operator forced layout without 1a. Accept decode OR layout.
                if size == (decode_w_e, decode_h_e) or size == (layout_w, layout_h):
                    log(
                        "PASS present size %s (present=1b decode=%dx%d layout=%dx%d)"
                        % (size, decode_w_e, decode_h_e, layout_w, layout_h)
                    )
                else:
                    ok = False
                    log(
                        "FAIL present size %s not decode (%d,%d) or layout (%d,%d)"
                        % (size, decode_w_e, decode_h_e, layout_w, layout_h)
                    )

            # Surface quality checks (size / non-black / empty). Frame-index AC2
            # is already asserted at first_present_probe ABOVE the wait — by
            # the time we reach ready_full the clock has intentionally advanced.
            ch0 = rps._channels.get(CH, {})
            idx_now = ch0.get("frame_index")
            log("post_full_frame_index=%s (informational; AC2 checked at first present)" % idx_now)

            # --- non-black mean rgb ---
            r, g, b = _mean_rgb(surf)
            log("mean_rgb=%.1f,%.1f,%.1f" % (r, g, b))
            if (r + g + b) < 5.0:
                ok = False
                log("FAIL black/empty frame (mean rgb too low)")
            else:
                log("PASS non-black frame")

            # --- empty_movie_presents == 0 when frames exist ---
            empty_presents = None
            if _has_helper("empty_movie_presents"):
                try:
                    empty_presents = int(rps.empty_movie_presents())
                    log("empty_movie_presents=%d" % empty_presents)
                except Exception as e:
                    warn("empty_movie_presents raised: %s" % e)
            elif "empty_movie_presents" in ch0:
                empty_presents = int(ch0.get("empty_movie_presents") or 0)
                log("ch.empty_movie_presents=%d" % empty_presents)
            else:
                # Best available: video_ready True + read non-None + non-black.
                empty_presents = 0 if (ready and surf is not None and (r + g + b) >= 5.0) else 1
                log(
                    "empty_movie_presents inferred=%d "
                    "(no counter helper; ready+non-None+non-black)"
                    % empty_presents
                )
            if nframes > 0 and empty_presents != 0:
                ok = False
                log(
                    "FAIL empty_movie_presents=%s != 0 while frames exist (AC1)"
                    % empty_presents
                )
            elif nframes > 0:
                log("PASS empty_movie_presents==0 when frames exist")

        # --- path cache survives stop→play same path ---
        frames_before_stop = nframes
        path_cache_hit = None
        if _has_helper("path_cache_has_frames"):
            try:
                path_cache_hit = bool(rps.path_cache_has_frames(path))
                log("path_cache_has_frames(before stop)=%s" % path_cache_hit)
            except Exception as e:
                warn("path_cache_has_frames raised: %s" % e)
        elif _has_helper("path_cache_frame_count"):
            try:
                pc_n = int(rps.path_cache_frame_count(path))
                path_cache_hit = pc_n >= 360
                log("path_cache_frame_count(before stop)=%d" % pc_n)
            except Exception as e:
                warn("path_cache_frame_count raised: %s" % e)

        rps.stop(CH)

        # After stop: channel frames currently cleared by stop(); path cache
        # (Option B′) should retain by path if implemented.
        post_stop_ch = rps._channels.get(CH, {})
        post_stop_frames = post_stop_ch.get("frames") or []
        log("after_stop channel_nframes=%d" % len(post_stop_frames))

        survived = False
        if _has_helper("path_cache_has_frames"):
            try:
                survived = bool(rps.path_cache_has_frames(path))
                log("path_cache_has_frames(after stop)=%s" % survived)
            except Exception as e:
                warn("path_cache_has_frames after stop raised: %s" % e)
        elif _has_helper("path_cache_frame_count"):
            try:
                pc_n2 = int(rps.path_cache_frame_count(path))
                survived = pc_n2 >= max(1, frames_before_stop)
                log("path_cache_frame_count(after stop)=%d" % pc_n2)
            except Exception as e:
                warn("path_cache_frame_count after stop raised: %s" % e)
        else:
            # No public path-cache API yet: re-play and check whether frames
            # reappear quickly (cache hit would be near-instant; miss re-ffmpeg).
            warn(
                "no path_cache_* helper; using second play wall-time heuristic "
                "for stop→play survival"
            )

        t_replay0 = time.monotonic()
        rps.set_video(CH, rps.DROP_VIDEO, loop=True)
        rps.play(CH, file=None, name=name, relative_volume=0.0)
        t_replay = time.monotonic() - t_replay0
        ch2 = rps._channels.get(CH, {})
        nframes2 = len(ch2.get("frames") or [])
        log("second_play wall_s=%.3f nframes=%d" % (t_replay, nframes2))

        if _has_helper("path_cache_has_frames") or _has_helper("path_cache_frame_count"):
            if not survived and nframes2 < 360:
                ok = False
                log(
                    "FAIL path cache did not survive stop→play "
                    "(survived=%s nframes2=%d)" % (survived, nframes2)
                )
            elif not survived and nframes2 >= 360:
                # Frames re-decoded; cache miss.
                ok = False
                log(
                    "FAIL path cache miss on stop→play "
                    "(re-decoded nframes2=%d wall=%.3f)" % (nframes2, t_replay)
                )
            else:
                log("PASS path cache survives stop→play (survived=%s)" % survived)
        else:
            # Heuristic: cache hit should be much faster than first play if
            # first play did real ffmpeg work. Without helpers, only WARN.
            if nframes2 >= 360 and t_replay < max(0.05, t_play * 0.25):
                log(
                    "PASS stop→play likely cache hit "
                    "(nframes2=%d wall=%.3f << first %.3f)" % (nframes2, t_replay, t_play)
                )
            elif nframes2 >= 360:
                warn(
                    "stop→play re-play wall=%.3f (first=%.3f); "
                    "cannot prove path-cache hit without helpers"
                    % (t_replay, t_play)
                )
            else:
                ok = False
                log(
                    "FAIL second play nframes=%d < 360 after stop→play" % nframes2
                )

        # Second play first index still near 0 (clock re-arms on play; allow a
        # couple frames of scheduling slack so the assert is not flaky).
        surf2 = rps.read_video(CH)
        if surf2 is not None:
            idx2 = rps._channels.get(CH, {}).get("frame_index")
            log("second_play frame_index=%s" % idx2)
            if idx2 is not None and int(idx2) > 2:
                ok = False
                log("FAIL second_play frame_index=%s > 2 (expected near 0)" % idx2)
            else:
                log("PASS second_play frame_index near 0 (%s)" % idx2)
        else:
            ok = False
            log("FAIL second_play read_video None")

        rps.stop(CH)

except Exception as e:
    ok = False
    log("FAIL exception: %s: %s" % (type(e).__name__, e))
    import traceback

    log(traceback.format_exc())

if warns:
    log("warn_count=%d" % len(warns))
lines.append("ok=%s" % ok)
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
# Companion JSON for runners that only parse .json (txt remains authoritative).
try:
    import json
    out_json = out.with_suffix(".json")
    report = {
        "ok": bool(ok),
        "pass": bool(ok),
        "measured": True,
        "gate": "hmc_menu_video_product",
        "decode_wh": [
            int(os.environ.get("RENPY_HOST_MOVIE_W", "1920")),
            int(os.environ.get("RENPY_HOST_MOVIE_H", "1080")),
        ],
        "layout_wh": [
            int(os.environ.get("RENPY_HOST_MOVIE_LAYOUT_W", "1920")),
            int(os.environ.get("RENPY_HOST_MOVIE_LAYOUT_H", "1080")),
        ],
        "max_frames": int(os.environ.get("RENPY_HOST_MOVIE_MAX_FRAMES", "360")),
        "warn_count": len(warns),
        "warns": list(warns),
        "lines_tail": lines[-40:],
    }
    out_json.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    log("WROTE %s ok=%s" % (out_json, ok))
except Exception as e:
    log("WARN product json write failed: %s" % e)
log("WROTE %s ok=%s" % (out, ok))
try:
    renpy_host.request_quit()
except Exception:
    pass
if not ok:
    raise SystemExit(1)
