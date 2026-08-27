"""
renpysound host adapter (Phase 4 + Phase 6 A/V clock + product Movie channel).

Maps the renpysound public surface used by renpy.audio to host PCM beeps /
queued f32 samples. Video channels:

  - video_ready / read_video feed the stock Movie → get_movie_texture path
  - Frames live in a path-keyed progressive cache (Option B′) that survives
    channel stop; presentation uses renpy_host.video_clock_*
  - Tier S store keeps **native 1920×1080** decode frames (layout-identical;
    present 1b is 1:1 sharp). Full 360 ≈ 2.85 GiB; splash warm is **staged**
    (``RENPY_HOST_MOVIE_WARM_FRAMES``, default 90 ≈ 0.71 GiB) so frame0 /
    playable prefix are ready before end_splash without holding the full list
    during dual-draw. Play / ensure continues to full target in the background.
  - Present default **1b**: return decode-size Surface; GPU mesh-scales only
    when the offered box differs. Opt-out via RENPY_HOST_MOVIE_PRESENT=layout
    for **1a** single-frame layout buffer.
  - Pure-Python bilinear is banned on the product path (nearest-neighbor
    fallback only when numpy/PIL unavailable on the rare 1a / frame0 path).

In-process libav decode remains a follow-up; interim CLI ffmpeg is required
on PATH for product Movie (blank ≠ pass).
"""

from __future__ import annotations

import os
import re
import threading
from collections import deque
from typing import Any, List, Optional

import renpy_host  # type: ignore

_channels: dict[int, dict] = {}
_inited = False

# Match renpysound.pyx video flags used by audio.Channel.
NO_VIDEO = 0
NODROP_VIDEO = 1
DROP_VIDEO = 2
is_webaudio = False

# Product interim decode budget (plan B′: product 360 @ 30fps; env overrides).
_DEFAULT_MAX_FRAMES = 48
_DEFAULT_DECODE_FPS = 12.0
# Decode size: product default matches native/layout 1920×1080 so present 1b
# is sharp 1:1 at menu size. Full 360 ≈ 2.85 GiB — splash only warms a
# staged prefix (see RENPY_HOST_MOVIE_WARM_FRAMES) so dual-draw is not
# contending with a multi-GiB progressive fill.
# Present default (1b): return decode-size Surface; Movie.render mesh-scales
# only when the offered box differs — zero CPU upscale per frame.
# Fail 1b → 1a: single-frame layout present buffer (lazy S1 for selected idx).
_DEFAULT_FRAME_W = 1920
_DEFAULT_FRAME_H = 1080
# Product layout Surface size (AC-M5 full-bleed). Independent of decode size.
_LAYOUT_FRAME_W = 1920
_LAYOUT_FRAME_H = 1080
# Splash warm stage: frames decoded before end_splash (default 90 ≈ 3s @30fps,
# ~0.71 GiB @1080). 0 / unset-to-full means warm the entire target immediately.
_DEFAULT_WARM_FRAMES = 90
# Pure-Python bilinear ban once-log guard.
_PURE_PY_BILINEAR_LOGGED = False

# HMC special-case warm media (v1 hardcode; generalize later).
# Path identity: prefer @2 for BOTH warm and play when loadable (C4 / AC-MenuVideo).
# Do not warm base while play uses @2 (or the reverse) — single resolve helper.
_HMC_MENU_VIDEO = "video/main_menu.webm"
_HMC_MENU_VIDEO_AT2 = "video/main_menu@2.webm"
# Basename forms used by path-identity rewrite (strip dirs / absolute recovered paths).
_HMC_MENU_BASENAME = "main_menu.webm"
_HMC_MENU_BASENAME_AT2 = "main_menu@2.webm"

# Path-keyed progressive frame cache. Survives channel stop / 00start movie stop.
# entry = {
#   frames, decode_w/h, layout_w/h, fps, ready_partial, ready_playable,
#   ready_full, lock, inflight, target_frames, path, layout_cached
# }
# Clock arms on ready_playable (≥ MIN_PLAYABLE, default 2), not only ready_full.
# While growing, read_video clamps the index; full list loops with %.
_PATH_FRAME_CACHE: dict[str, dict] = {}
_WARM_STARTED_PATHS: set[str] = set()
_warm_menu_once = False


def init(freq=48000, stereo=True, samples=1024, status=False, equal_mono=False, linear_fades=False):
    global _inited
    renpy_host.audio_start()
    _inited = True
    # Single warm call site (Option B′): host active + env gated.
    maybe_warm_menu_video()
    return None


def quit():
    global _inited
    for ch in list(_channels.keys()):
        _clear_video_cache(ch)
        if _channels[ch].get("video"):
            try:
                renpy_host.video_clock_stop(ch)
            except Exception:
                pass
    renpy_host.audio_stop()
    _channels.clear()
    _inited = False


def play(
    channel,
    file,
    name,
    synchro_start=False,
    fadein=0,
    tight=False,
    start=0,
    end=0,
    relative_volume=1.0,
    audio_filter=None,
):
    ch = _channels.setdefault(channel, {})
    prev_name = ch.get("name")
    ch["name"] = name
    ch["file"] = file
    ch["volume"] = float(relative_volume)
    ch["playing"] = True

    # Hermetic gates may mute (RENPY_HOST_MUTE) to avoid cpal/stream stalls.
    mute = os.environ.get("RENPY_HOST_MUTE", "0").lower() in ("1", "true", "yes", "on")
    is_video = bool(ch.get("video"))

    # Skip identity beep on movie channels (silent menu video is accepted).
    if not mute and not is_video:
        freq = 440.0 + (channel % 8) * 40.0
        renpy_host.audio_beep(freq, 120, 0.15 * float(relative_volume))

    if is_video:
        # Unbind channel cache when the media identity changes (path cache survives).
        if prev_name is not None and prev_name != name:
            _clear_video_cache(channel)
        # Bind / ensure path cache first; arm clock when playable prefix is ready
        # (full list preferred; playable prefix arms early so the menu moves).
        _ensure_video_frames(channel, block=True)
        _maybe_arm_clock(channel)


def queue(
    channel,
    file,
    name,
    synchro_start=False,
    fadein=0,
    tight=False,
    start=0,
    end=0,
    relative_volume=1.0,
    audio_filter=None,
):
    # Phase 4/6: treat queue as immediate play.
    play(channel, file, name, synchro_start, fadein, tight, start, end, relative_volume, audio_filter)


def stop(channel):
    if channel in _channels:
        _channels[channel]["playing"] = False
        _channels[channel]["name"] = None
        _channels[channel].pop("_audio_clock_bound", None)
        if _channels[channel].get("video"):
            try:
                renpy_host.video_clock_stop(channel)
            except Exception:
                pass
        # Unbind channel only — path-keyed cache survives for warm/hold.
        _clear_video_cache(channel)


def dequeue(channel, even_tight=False):
    stop(channel)


def queue_depth(channel):
    ch = _channels.get(channel)
    return 1 if ch and ch.get("playing") else 0


def playing_name(channel):
    ch = _channels.get(channel)
    if ch and ch.get("playing"):
        return ch.get("name")
    return None


def pause(channel):
    if channel in _channels:
        _channels[channel]["playing"] = False
        if _channels[channel].get("video"):
            renpy_host.video_clock_pause(channel)


def unpause(channel):
    if channel in _channels:
        _channels[channel]["playing"] = True
        if _channels[channel].get("video"):
            renpy_host.video_clock_unpause(channel)


def set_volume(channel, volume):
    if channel in _channels:
        _channels[channel]["volume"] = float(volume)
    renpy_host.audio_set_volume(float(volume))


def set_pan(channel, pan, delay):
    return None


def set_secondary_volume(channel, volume, delay):
    set_volume(channel, volume)


def get_duration(channel):
    ch = _channels.get(channel)
    frames = None
    fps = _DEFAULT_DECODE_FPS
    if ch:
        frames = ch.get("play_frames") or ch.get("frames")
        fps = float(ch.get("decode_fps") or _DEFAULT_DECODE_FPS)
        if not frames:
            path = ch.get("media_path")
            entry = _lookup_path_entry(path) if path else None
            if entry:
                with entry["lock"]:
                    frames = entry.get("frames")
                    fps = float(entry.get("fps") or fps)
    if frames:
        return float(len(frames)) / max(fps, 1e-6)
    return 0.0


def get_pos(channel):
    """Presentation time for the channel (video clock when movie channel)."""
    ch = _channels.get(channel)
    if ch and ch.get("video"):
        return float(renpy_host.video_clock_pos(channel))
    return 0.0


def set_end_event(channel, event):
    return None


def periodic():
    return None


def advance_time():
    """Called at the start of a frame (stock RPS_advance_time). Host no-op."""
    return None


def set_channel_count(count):
    """Host no-op; channel map is sparse."""
    return None


def busy(channel):
    return queue_depth(channel) > 0


def fadeout(channel, delay):
    stop(channel)


def set_audio_filter(channel, audio_filter, replace=False):
    return None


def replace_audio_filter(channel, audio_filter, playing=0):
    """Stock renpysound API; host has no filter graph yet."""
    return None


def deallocate_audio_filter(audio_filter):
    return None


def global_pause(pause):
    return None


def check_error():
    return None


def video_ready(channel):
    """True when a presentable Movie frame is available for this channel.

    Path cache with ≥1 frame for the bound path counts even before clock arm
    (partial hold / frame0).
    """
    ch = _channels.get(channel)
    if not ch or not ch.get("video"):
        return False
    if ch.get("frames") or ch.get("play_frames"):
        return True
    path = ch.get("media_path")
    if not path:
        path = _resolve_media_path(ch.get("name"))
    if path and path_cache_has_frames(path):
        return True
    # Also accept Movie name identity when channel media_path not yet bound.
    name = ch.get("name")
    if name and path_cache_has_frames(name):
        return True
    return False


def get_sample_rate():
    return 48000


def set_generate_audio_c_function(fn):
    return None


def sample_surfaces(rgb, rgba):
    return None


def get_volume(channel):
    ch = _channels.get(channel)
    return float(ch.get("volume", 1.0)) if ch else 1.0


def write_pcm(channel, samples):
    """Host extension: push f32 interleaved PCM to cpal ring."""
    renpy_host.audio_queue_pcm_f32(list(samples))


# --- Video channel surface (product Movie → get_movie_texture) ----------------


def set_video(channel, video, loop=False):
    """Mark channel as movie (DROP_VIDEO / NODROP_VIDEO) or audio-only."""
    ch = _channels.setdefault(channel, {"playing": False, "volume": 1.0})
    was_video = bool(ch.get("video"))
    ch["video"] = video != NO_VIDEO
    ch["video_mode"] = video
    ch["loop"] = bool(loop)
    if ch["video"] and ch.get("playing"):
        # Same contract as play: ensure/bind first; arm on playable prefix.
        _ensure_video_frames(channel, block=True)
        _maybe_arm_clock(channel)
    elif not ch["video"]:
        if was_video:
            _clear_video_cache(channel)
        try:
            renpy_host.video_clock_stop(channel)
        except Exception:
            pass


def set_movie_channel(channel, is_movie):
    set_video(channel, DROP_VIDEO if is_movie else NO_VIDEO)


def _present_mode_1b() -> bool:
    """Default present strategy 1b: decode-size texture + mesh scale (zero CPU S1).

    Opt out with RENPY_HOST_MOVIE_PRESENT=1a (single-frame layout buffer).
    """
    mode = os.environ.get("RENPY_HOST_MOVIE_PRESENT", "1b").strip().lower()
    return mode not in ("1a", "layout", "s1")


def read_video(channel):
    """
    Return a surface-like RGBA frame for stock get_movie_texture → load_texture.

    Default present **1b**: decode-size Surface (product 1920×1080); Movie.render
    mesh-scales only when the offered box differs. Zero CPU bilinear per frame.

    Fallback **1a** (RENPY_HOST_MOVIE_PRESENT=1a): reuse one layout-sized
    Surface and S1-upscale only the selected frame.

    ``frame_surf`` id is stable; pixels rewrite only when ``frame_index``
    changes so load_texture fingerprint skips write_texture thrash.
    """
    ch = _channels.get(channel)
    if not ch or not ch.get("video"):
        return None

    # Lazy ensure (set_video/play race) — non-blocking if warm inflight.
    if not ch.get("frames") and not ch.get("play_frames"):
        _ensure_video_frames(channel, block=False)
    # Arm clock if full list arrived while we were holding frame0.
    _maybe_arm_clock(channel)

    # Rebind from path cache when full list arrives after a partial hold, or
    # when channel has no frames yet (set_video/play race).
    path = ch.get("media_path") or _resolve_media_path(ch.get("name"))
    if path and not ch.get("media_path"):
        path = _abspath_key(path)
        ch["media_path"] = path
    entry = _lookup_path_entry(path) if path else None
    if entry is not None:
        with entry["lock"]:
            cache_frames = entry.get("frames") or []
            cache_n = int(entry.get("total_decoded") or len(cache_frames))
            cache_full = bool(entry.get("ready_full"))
            cache_inflight = bool(entry.get("inflight"))
            cache_target = int(entry.get("target_frames") or _DEFAULT_MAX_FRAMES)
        ch_n = int(ch.get("total_decoded") or len(ch.get("play_frames") or ch.get("frames") or []))
        # Rebind on growth (prefix decode) OR first full publish OR when the
        # clock is still waiting for a playable prefix.
        if cache_n > ch_n or (cache_full and not ch.get("ready_full")) or (
            not ch.get("clock_armed") and cache_n >= 1
        ):
            _bind_channel_from_entry(channel, entry)
            _maybe_arm_clock(channel)
        # Self-heal continue-fill after warm stage ends while playing.
        if (
            (not cache_full)
            and (not cache_inflight)
            and cache_n >= 1
            and cache_n < cache_target
            and ch.get("playing")
        ):
            try:
                _start_path_decode(path, background=True, stage_frames=cache_target)
            except Exception:
                pass

    frames: Optional[List[bytes]] = ch.get("play_frames") or ch.get("frames")
    if not frames and entry is not None:
        with entry["lock"]:
            frames = entry.get("frames")
        if frames:
            _bind_channel_from_entry(channel, entry)
            frames = ch.get("play_frames") or ch.get("frames") or frames
    if not frames:
        return None

    layout_w, layout_h = _layout_size()
    decode_w = int(ch.get("decode_w") or layout_w)
    decode_h = int(ch.get("decode_h") or layout_h)
    fps = float(ch.get("decode_fps") or _DEFAULT_DECODE_FPS)
    use_1b = _present_mode_1b()

    # Not armed → frame0 hold only (end_splash / pre-playable prefix).
    # Armed → index by wall clock. While the path cache is still growing
    # (ready_playable but not ready_full) clamp to the last available frame
    # instead of wrapping with % — wrapping mid-decode causes a jump-back
    # stutter (AC4). Once ready_full the list is frozen and loops.
    if not ch.get("clock_armed"):
        idx = 0
        abs_idx = int(ch.get("base_index") or 0)
    else:
        pos = float(renpy_host.video_clock_pos(channel))
        raw_idx = int(pos * fps)
        n = len(frames)
        if n <= 0:
            return None
        base = int(ch.get("base_index") or 0)
        total = int(ch.get("total_decoded") or (base + n))
        ready_full = bool(ch.get("ready_full"))
        if ch.get("loop") is False:
            abs_idx = min(raw_idx, max(0, total - 1))
        elif ready_full and total > 0:
            abs_idx = raw_idx % total
        else:
            abs_idx = min(raw_idx, max(0, total - 1))
        # While still growing, wall clock often outruns the progressive decode
        # buffer (1080p RGBA stream). Re-anchor the presentation clock to the
        # latest available absolute frame so we keep tracking new publishes
        # instead of clamping to the ring tail for many seconds (sticky feel).
        if (not ready_full) and total > 0 and raw_idx > (total - 1):
            try:
                set_pos = getattr(renpy_host, "video_clock_set_pos", None)
                if callable(set_pos):
                    # Keep a tiny lead so the next published frame advances
                    # presentation immediately without a multi-second freeze.
                    set_pos(channel, float(max(0, total - 1)) / float(fps))
                    ch["_clock_starved"] = True
                elif not ch.get("_clock_starved"):
                    # Fallback: pause once when starved (legacy hosts).
                    renpy_host.video_clock_pause(channel)
                    ch["_clock_starved"] = True
            except Exception:
                pass
        elif ch.get("_clock_starved"):
            try:
                renpy_host.video_clock_unpause(channel)
            except Exception:
                pass
            ch["_clock_starved"] = False
        if abs_idx < base:
            idx = 0
            abs_idx = base
        elif abs_idx >= base + n:
            idx = n - 1
            abs_idx = base + n - 1
        else:
            idx = abs_idx - base

    # Present key is absolute so ring-local idx (often n-1 while sliding) does
    # not skip pixel rewrite / GPU upload when the absolute frame advanced.
    ch["frame_index"] = int(abs_idx)
    ch["frame_index_local"] = int(idx)
    if not ch.get("_logged_first_read"):
        ch["_logged_first_read"] = True
        _phase0_log(
            f"T_first_read_idx channel={channel} idx={idx} abs={int(abs_idx)} "
            f"clock_armed={bool(ch.get('clock_armed'))} nframes={len(frames)} "
            f"present_mode={'1b' if use_1b else '1a'}"
        )

    # Skip pixel rewrite + fingerprint thrash when absolute frame unchanged.
    present_w = decode_w if use_1b else layout_w
    present_h = decode_h if use_1b else layout_h
    surf = ch.get("frame_surf")
    present_key = int(abs_idx)
    if (
        ch.get("_present_idx") == present_key
        and surf is not None
        and getattr(surf, "get_size", lambda: (0, 0))() == (present_w, present_h)
        and getattr(surf, "_pixels", None) is not None
        and len(surf._pixels) >= present_w * present_h * 4
    ):
        ch["frame_w"], ch["frame_h"] = present_w, present_h
        try:
            surf._host_frame_idx = present_key
        except Exception:
            pass
        if _phase0_signals_enabled():
            import time as _time

            now = _time.monotonic()
            last_t = float(ch.get("_phase0_last_t") or 0.0)
            if (now - last_t) >= 1.0:
                ch["_phase0_last_t"] = now
                _phase0_log(
                    f"present channel={channel} frame_index={present_key} "
                    f"local_idx={idx} base={int(ch.get('base_index') or 0)} "
                    f"layout_cached={int(bool(ch.get('layout_cached')))} "
                    f"s1_ms=0.000 s1_upscale=0 present={present_w}x{present_h} "
                    f"decode={decode_w}x{decode_h} nframes={len(frames)} "
                    f"clock_armed={int(bool(ch.get('clock_armed')))} "
                    f"present_mode={'1b' if use_1b else '1a'} skipped_rewrite=1"
                )
        return surf

    rgba_src = frames[idx]
    layout_cached = bool(ch.get("layout_cached"))
    s1_did_upscale = False
    s1_ms = 0.0
    s1_t0 = None
    if _phase0_signals_enabled():
        import time as _time

        s1_t0 = _time.monotonic()

    if use_1b:
        # 1b: present decode-size; mesh-scale in Movie.render (zero CPU S1).
        w, h = decode_w, decode_h
        need = w * h * 4
        if layout_cached and len(rgba_src) == layout_w * layout_h * 4:
            # Tier-L store: downscale is rare; prefer crop/nearest of first tile.
            # Product default is Tier S so this branch is opt-in only.
            rgba = _nearest_scale_rgba(rgba_src, layout_w, layout_h, w, h)
        elif len(rgba_src) >= need:
            rgba = rgba_src[:need] if len(rgba_src) > need else rgba_src
        else:
            buf = bytearray(need)
            buf[: len(rgba_src)] = rgba_src
            rgba = bytes(buf)
    else:
        # 1a: single-frame layout present buffer (lazy S1 for selected idx).
        w, h = layout_w, layout_h
        need = w * h * 4
        if layout_cached and len(rgba_src) == need:
            rgba = rgba_src
        elif len(rgba_src) == need and (decode_w, decode_h) == (w, h):
            rgba = rgba_src
        elif len(rgba_src) == decode_w * decode_h * 4 and (decode_w, decode_h) != (w, h):
            s1_did_upscale = True
            rgba = _bilinear_upscale_rgba(rgba_src, decode_w, decode_h, w, h)
        elif len(rgba_src) == need:
            rgba = rgba_src
        else:
            buf = bytearray(need)
            copy_n = min(len(rgba_src), need)
            buf[:copy_n] = rgba_src[:copy_n]
            rgba = bytes(buf)

    if s1_t0 is not None:
        import time as _time

        s1_ms = (_time.monotonic() - s1_t0) * 1000.0

    ch["frame_w"], ch["frame_h"] = w, h
    need = w * h * 4

    if len(rgba) != need and _movie_assert_enabled():
        _phase0_log(
            f"AC2_WARN read_video rgba_len={len(rgba)} need={need} "
            f"present={w}x{h} channel={channel}"
        )

    # Phase 0 present keys: s1_ms, frame_index, layout_cached, present w×h.
    if _phase0_signals_enabled():
        import time as _time

        now = _time.monotonic()
        last_idx = ch.get("_phase0_last_idx")
        last_t = float(ch.get("_phase0_last_t") or 0.0)
        first = not ch.get("_phase0_present_logged")
        idx_changed = last_idx is None or int(last_idx) != int(abs_idx)
        due = (now - last_t) >= 1.0
        if first or idx_changed or due:
            ch["_phase0_present_logged"] = True
            ch["_phase0_last_idx"] = int(abs_idx)
            ch["_phase0_last_t"] = now
            _phase0_log(
                f"present channel={channel} frame_index={int(abs_idx)} "
                f"local_idx={idx} base={int(ch.get('base_index') or 0)} "
                f"layout_cached={int(layout_cached)} s1_ms={s1_ms:.3f} "
                f"s1_upscale={int(s1_did_upscale)} present={w}x{h} "
                f"decode={decode_w}x{decode_h} nframes={len(frames)} "
                f"clock_armed={int(bool(ch.get('clock_armed')))} "
                f"present_mode={'1b' if use_1b else '1a'} skipped_rewrite=0"
            )

    if surf is None or getattr(surf, "get_size", lambda: (0, 0))() != (w, h):
        surf = _make_surface(w, h)
        ch["frame_surf"] = surf

    px = getattr(surf, "_pixels", None)
    if px is None or len(px) < need:
        surf._pixels = bytearray(
            rgba[:need] if len(rgba) >= need else rgba + bytes(need - len(rgba))
        )
    else:
        # In-place rewrite so id(surf) is stable; fingerprint drives re-upload.
        if len(rgba) >= need:
            px[:need] = rgba[:need]
        else:
            px[: len(rgba)] = rgba
            for i in range(len(rgba), need):
                px[i] = 0
    ch["_present_idx"] = int(abs_idx)
    # Stamp for load_texture: skip write_texture when gen unchanged.
    # Absolute index so ring-local n-1 does not suppress GPU re-upload.
    try:
        surf._host_frame_idx = int(abs_idx)
    except Exception:
        pass
    return surf


def _make_surface(w: int, h: int):
    try:
        from renpy.pygame.surface import Surface

        return Surface((w, h))
    except Exception:
        class _MiniSurf:
            def __init__(self, size):
                self.width, self.height = int(size[0]), int(size[1])
                self._pixels = bytearray(self.width * self.height * 4)

            def get_size(self):
                return (self.width, self.height)

            def get_buffer(self):
                return bytes(self._pixels)

        return _MiniSurf((w, h))


def _clear_video_cache(channel: int) -> None:
    """Unbind channel-local video state only. Path cache is intentionally kept."""
    ch = _channels.get(channel)
    if not ch:
        return
    ch.pop("frames", None)
    ch.pop("play_frames", None)
    ch.pop("frame_surf", None)
    ch.pop("frame_index", None)
    ch.pop("frame_index_local", None)
    ch.pop("media_path", None)
    ch.pop("frame_w", None)
    ch.pop("frame_h", None)
    ch.pop("decode_w", None)
    ch.pop("decode_h", None)
    ch.pop("decode_fps", None)
    ch.pop("clock_armed", None)
    ch.pop("layout_cached", None)
    ch.pop("ready_full", None)
    ch.pop("growing", None)
    ch.pop("base_index", None)
    ch.pop("total_decoded", None)
    ch.pop("_logged_first_read", None)
    ch.pop("_present_idx", None)
    ch.pop("_clock_starved", None)
    ch.pop("_phase0_present_logged", None)
    ch.pop("_phase0_last_idx", None)
    ch.pop("_phase0_last_t", None)
    ch.pop("_audio_clock_bound", None)


def _strip_audio_spec(name: Any) -> str:
    """Strip renpy audio angle-bracket specs: ``<from 0 to 1>foo.webm`` → ``foo.webm``."""
    n = str(name) if name is not None else ""
    if not n:
        return ""
    m = re.match(r"<[^>]*>(.*)$", n)
    if m:
        return m.group(1).strip()
    return n.strip()


def _is_hmc_menu_base_identity(name_or_path: str) -> bool:
    """True when identity is base main_menu.webm (not already an @N oversample)."""
    if not name_or_path:
        return False
    s = str(name_or_path).replace("\\", "/")
    base = os.path.basename(s)
    if base == _HMC_MENU_BASENAME:
        return True
    # Relative game identity without directory noise.
    if s == _HMC_MENU_VIDEO or s.endswith("/" + _HMC_MENU_VIDEO):
        return True
    return False


def _sibling_at2_path(path: str) -> Optional[str]:
    """Return sibling ``main_menu@2.webm`` next to a base main_menu path if it exists."""
    if not path:
        return None
    s = str(path).replace("\\", "/")
    if not s.endswith(_HMC_MENU_BASENAME):
        return None
    # Avoid rewriting an already-oversampled name (main_menu@2.webm ends with
    # ...@2.webm, not bare main_menu.webm after basename check above).
    base = os.path.basename(s)
    if base != _HMC_MENU_BASENAME:
        return None
    at2 = s[: -len(_HMC_MENU_BASENAME)] + _HMC_MENU_BASENAME_AT2
    if os.path.isfile(at2):
        return at2
    return None


def _prefer_at2_enabled() -> bool:
    """Opt-in: rewrite HMC base menu → ``@2`` for warm/play identity.

    Default **off**. Preferring the 4K ``@2`` source for a 960/1280 present
    budget multiplies ffmpeg cost and RAM for no visible gain on the product
    path, and was a leading cause of the post-logo black/stall gap. Operators
    can re-enable with ``RENPY_HOST_MOVIE_PREFER_AT2=1`` for A/B.
    """
    return os.environ.get("RENPY_HOST_MOVIE_PREFER_AT2", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _prefer_hmc_menu_at2(path_or_name: Optional[str]) -> Optional[str]:
    """
    Optional C4 path identity: if enabled and HMC ``video/main_menu.webm`` and
    ``@2`` is loadable, return the ``@2`` path/name so warm AND play share one
    cache key.

    Default is **no rewrite** (base ``main_menu.webm``). See
    :func:`_prefer_at2_enabled`. Never rewrites non-menu media. Never mutates
    recovered_project.
    """
    if not path_or_name:
        return path_or_name
    s = _strip_audio_spec(path_or_name)
    if not s:
        return path_or_name

    # Already @2 (or other @N): keep as-is (explicit identity).
    base = os.path.basename(s.replace("\\", "/"))
    if "@" in base:
        return s

    if not _prefer_at2_enabled():
        return s

    if not _is_hmc_menu_base_identity(s):
        return s

    # Absolute / existing file → sibling @2 on disk.
    if os.path.isfile(s):
        at2 = _sibling_at2_path(s)
        return at2 if at2 else s

    # Relative game identity: try @2 name through the same resolve candidates
    # without recursing into this prefer helper (resolve raw).
    at2_name = _HMC_MENU_VIDEO_AT2
    raw = _resolve_media_path_raw(at2_name)
    if raw and os.path.isfile(raw):
        return raw

    # Name-form fallback when raw resolve is not yet available (pre-gamedir).
    return at2_name if _media_name_loadable(at2_name) else s


def _media_name_loadable(name: str) -> bool:
    """True if a relative media name exists on any known candidate root."""
    if not name:
        return False
    if os.path.isfile(name):
        return True
    try:
        import renpy  # type: ignore

        gamedir = getattr(getattr(renpy, "config", None), "gamedir", None)
        if gamedir and os.path.isfile(os.path.join(str(gamedir), name)):
            return True
        try:
            from renpy.loader import transfn  # type: ignore

            p = transfn(name)
            if p and os.path.isfile(p):
                return True
        except Exception:
            pass
    except Exception:
        pass
    base = os.environ.get("RENPY_HOST_BASE") or os.getcwd()
    for p in (
        os.path.join(base, name),
        os.path.join(base, "game", name),
        os.path.join(base, "host", "playtests", "HuangmeiC", "game", name),
        "/mnt/nvme0n1p2/@home/isah1221/huangmeic/recovered_project/" + name,
    ):
        if os.path.isfile(p):
            return True
    return False


def _resolve_media_path_raw(name: Any) -> Optional[str]:
    """
    Resolve on-disk path for a Movie/audio name (no HMC @2 rewrite).

    Channel.play receives a file-like from loader.load, NOT a path — the
    media identity is the ``name`` argument (e.g. ``video/main_menu.webm``).
    """
    n = _strip_audio_spec(name)
    if not n:
        env = os.environ.get("RENPY_HOST_MOVIE_PATH")
        if env and os.path.isfile(env):
            return env
        return None

    if os.path.isfile(n):
        return n

    # renpy.config.gamedir / loader.transfn when product is up.
    try:
        import renpy  # type: ignore

        gamedir = getattr(getattr(renpy, "config", None), "gamedir", None)
        if gamedir:
            p = os.path.join(str(gamedir), n)
            if os.path.isfile(p):
                return p
        try:
            from renpy.loader import transfn  # type: ignore

            p = transfn(n)
            if p and os.path.isfile(p):
                return p
        except Exception:
            pass
    except Exception:
        pass

    env = os.environ.get("RENPY_HOST_MOVIE_PATH")
    if env and os.path.isfile(env):
        return env

    base = os.environ.get("RENPY_HOST_BASE") or os.getcwd()
    candidates = [
        os.path.join(base, n),
        os.path.join(base, "game", n),
        os.path.join(base, "host", "playtests", "HuangmeiC", "game", n),
        # recovered_project layout (read-only; path resolve only)
        "/mnt/nvme0n1p2/@home/isah1221/huangmeic/recovered_project/" + n,
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _resolve_media_path(name: Any) -> Optional[str]:
    """Resolve media path with C4 HMC menu ``@2`` path identity when loadable."""
    # Prefer rewriting the *name* first so warm/play share identity even when
    # RENPY_HOST_MOVIE_PATH points at the base file.
    n = _strip_audio_spec(name)
    preferred_name = _prefer_hmc_menu_at2(n) if n else n
    path = _resolve_media_path_raw(preferred_name if preferred_name else name)
    if path:
        # Final on-disk prefer: base absolute → sibling @2.
        preferred = _prefer_hmc_menu_at2(path)
        if preferred and os.path.isfile(preferred):
            return preferred
        return path
    # Name-only prefer may yield relative @2 that raw could not yet resolve.
    if preferred_name and preferred_name != n:
        path2 = _resolve_media_path_raw(preferred_name)
        if path2:
            return path2
    return None


def _abspath_key(path: str) -> str:
    """Stable path-cache key: prefer realpath so playtest symlinks collapse.

    HuangmeiC game/video is a symlink into recovered_project. Warm at init may
    resolve via recovered candidate while Movie.render later resolves via
    gamedir under the playtest tree — abspath differs, realpath matches.
    """
    if not path:
        return path
    try:
        # realpath follows symlinks; fall back to abspath if it fails.
        return os.path.realpath(path)
    except Exception:
        try:
            return os.path.abspath(path)
        except Exception:
            return path


def _path_cache_key(path_or_name: Any) -> Optional[str]:
    """Normalize a path or Movie play name to the absolute path-cache key.

    C4: HMC base menu identity and ``@2`` collapse to the same realpath key
    when ``@2`` is loadable, so warm(base→@2) and play(base) share the cache.
    """
    if path_or_name is None:
        return None
    s = str(path_or_name)
    if not s:
        return None
    # Prefer @2 rewrite before keying so base and @2 share one entry.
    preferred = _prefer_hmc_menu_at2(_strip_audio_spec(s))
    if preferred:
        s = preferred
    if os.path.isfile(s):
        return _abspath_key(s)
    resolved = _resolve_media_path(s)
    if resolved:
        return _abspath_key(resolved)
    return None


def _nearest_scale_rgba(src: bytes, sw: int, sh: int, dw: int, dh: int) -> bytes:
    """Nearest-neighbor scale packed RGBA8. Cheap last resort; not bilinear."""
    sw = int(sw)
    sh = int(sh)
    dw = int(dw)
    dh = int(dh)
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return b""
    need = sw * sh * 4
    if len(src) < need:
        src = bytes(src) + bytes(need - len(src))
    elif len(src) > need:
        src = src[:need]
    if sw == dw and sh == dh:
        return bytes(src) if not isinstance(src, (bytes, bytearray)) else bytes(src)
    out = bytearray(dw * dh * 4)
    for y in range(dh):
        sy = min(sh - 1, (y * sh) // dh)
        src_row = sy * sw * 4
        dst_row = y * dw * 4
        for x in range(dw):
            sx = min(sw - 1, (x * sw) // dw)
            si = src_row + sx * 4
            di = dst_row + x * 4
            out[di : di + 4] = src[si : si + 4]
    return bytes(out)


def _bilinear_upscale_rgba(src: bytes, sw: int, sh: int, dw: int, dh: int) -> bytes:
    """Bilinear scale packed RGBA8 ``sw×sh`` → ``dw×dh``. Identity when sizes match.

    Prefers numpy (vectorized) then Pillow. Pure-Python bilinear is **banned**
    on product path (multi-second frametime class); falls back to nearest.
    Allowed for S1 single-frame present only — never full-sequence product 360.
    """
    global _PURE_PY_BILINEAR_LOGGED
    sw = int(sw)
    sh = int(sh)
    dw = int(dw)
    dh = int(dh)
    if sw <= 0 or sh <= 0 or dw <= 0 or dh <= 0:
        return b""
    need = sw * sh * 4
    if len(src) < need:
        src = bytes(src) + bytes(need - len(src))
    elif len(src) > need:
        src = src[:need]
    if sw == dw and sh == dh:
        return bytes(src) if not isinstance(src, (bytes, bytearray)) else bytes(src)

    # Fast path: numpy float bilinear (available on host gate/product path).
    try:
        import numpy as np  # type: ignore

        img = np.frombuffer(src, dtype=np.uint8).reshape(sh, sw, 4)
        # Destination sample coords in source space (edge-safe).
        ys = np.linspace(0, sh - 1, dh, dtype=np.float64)
        xs = np.linspace(0, sw - 1, dw, dtype=np.float64)
        y0 = np.floor(ys).astype(np.intp)
        x0 = np.floor(xs).astype(np.intp)
        y1 = np.minimum(y0 + 1, sh - 1)
        x1 = np.minimum(x0 + 1, sw - 1)
        fy = (ys - y0).reshape(dh, 1, 1)
        fx = (xs - x0).reshape(1, dw, 1)
        # Gather 2×2 neighborhoods via advanced indexing.
        c00 = img[y0[:, None], x0[None, :], :].astype(np.float64)
        c10 = img[y0[:, None], x1[None, :], :].astype(np.float64)
        c01 = img[y1[:, None], x0[None, :], :].astype(np.float64)
        c11 = img[y1[:, None], x1[None, :], :].astype(np.float64)
        top = c00 * (1.0 - fx) + c10 * fx
        bot = c01 * (1.0 - fx) + c11 * fx
        out = (top * (1.0 - fy) + bot * fy + 0.5).astype(np.uint8)
        return out.tobytes()
    except Exception:
        pass

    # Pillow bilinear.
    try:
        from PIL import Image  # type: ignore

        im = Image.frombytes("RGBA", (sw, sh), bytes(src))
        im = im.resize((dw, dh), resample=Image.BILINEAR)
        return im.tobytes()
    except Exception:
        pass

    # Pure-Python bilinear is banned (plan Phase 1 hard ban). Nearest only.
    if not _PURE_PY_BILINEAR_LOGGED:
        _PURE_PY_BILINEAR_LOGGED = True
        try:
            import sys

            print(
                "PHASE0_SIGNAL pure_python_bilinear_banned "
                f"sw={sw} sh={sh} dw={dw} dh={dh} using_nearest=1",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            pass
    return _nearest_scale_rgba(src, sw, sh, dw, dh)


def _layout_size() -> tuple[int, int]:
    """Product layout Surface size (AC-M5 / AC2 full-bleed). Env override is A/B only."""
    lw = int(os.environ.get("RENPY_HOST_MOVIE_LAYOUT_W", _LAYOUT_FRAME_W))
    lh = int(os.environ.get("RENPY_HOST_MOVIE_LAYOUT_H", _LAYOUT_FRAME_H))
    return max(1, lw), max(1, lh)


def _decode_size() -> tuple[int, int]:
    w = int(os.environ.get("RENPY_HOST_MOVIE_W", _DEFAULT_FRAME_W))
    h = int(os.environ.get("RENPY_HOST_MOVIE_H", _DEFAULT_FRAME_H))
    return max(1, w), max(1, h)


def _decode_budget() -> tuple[int, float]:
    max_frames = int(os.environ.get("RENPY_HOST_MOVIE_MAX_FRAMES", _DEFAULT_MAX_FRAMES))
    fps = float(os.environ.get("RENPY_HOST_MOVIE_FPS", _DEFAULT_DECODE_FPS))
    return max(1, max_frames), max(1.0, fps)


def _layout_cache_enabled() -> bool:
    """Tier L opt-in (default off for product 360)."""
    return os.environ.get("RENPY_HOST_MOVIE_LAYOUT_CACHE", "").strip() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _rss_limit_mb() -> int:
    try:
        return int(os.environ.get("RENPY_HOST_MOVIE_RSS_MB", "4096"))
    except Exception:
        return 4096


def _phase0_signals_enabled() -> bool:
    """Phase 0 dual-signal logs (T_decode / pump). Env-gated; hang-lead only."""
    return os.environ.get("RENPY_HOST_PHASE0_SIGNALS", "").strip() in ("1", "true", "yes")


def _movie_assert_enabled() -> bool:
    """AC2 layout / size asserts + once-logs. Env-gated (playtest sets this)."""
    return os.environ.get("RENPY_HOST_MOVIE_ASSERT", "").strip() in ("1", "true", "yes")


def _phase0_log(msg: str) -> None:
    if not _phase0_signals_enabled() and not _movie_assert_enabled():
        return
    try:
        import sys
        import time as _time

        print(
            f"PHASE0_SIGNAL t={_time.monotonic():.3f} {msg}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        pass


def _rss_telemetry(nframes: int, dw: int, dh: int, path: str) -> None:
    """Log-only RSS kill-switch (Option C escalate telemetry; no full C)."""
    est_mb = (float(nframes) * float(dw) * float(dh) * 4.0) / (1024.0 * 1024.0)
    limit = _rss_limit_mb()
    if est_mb > float(limit):
        _phase0_log(
            f"RSS_KILL_SWITCH path={path!r} estimated_mb={est_mb:.1f} "
            f"limit_mb={limit} nframes={nframes} decode={dw}x{dh} "
            f"(escalate Option C; log only)"
        )
    else:
        _phase0_log(
            f"RSS_OK path={path!r} estimated_mb={est_mb:.1f} "
            f"limit_mb={limit} nframes={nframes} decode={dw}x{dh}"
        )


def _warm_stage_frames(target_frames: int) -> int:
    """Splash warm stage size (prefix of target). 0 = full target immediately.

    Env: RENPY_HOST_MOVIE_WARM_FRAMES (default 90). Clamped to [1, target]
    when >0 so a staged warm always leaves room to continue later when
    target > stage. When target <= stage the stage is the full target.
    """
    try:
        raw = os.environ.get("RENPY_HOST_MOVIE_WARM_FRAMES", str(_DEFAULT_WARM_FRAMES))
        n = int(raw)
    except (TypeError, ValueError):
        n = _DEFAULT_WARM_FRAMES
    target = max(1, int(target_frames))
    if n <= 0:
        return target
    return max(1, min(n, target))


def _ring_frames_budget() -> int:
    """Max RGBA frames retained in path cache (0 = unlimited full list).

    Product 1080p full 360 is multi-GiB and triggers GC/allocator stalls
    around the 4-5s mark. Default keeps ~2s @30fps.
    Env: RENPY_HOST_MOVIE_RING_FRAMES (0 disables).
    """
    try:
        n = int(os.environ.get("RENPY_HOST_MOVIE_RING_FRAMES", "0"))
    except (TypeError, ValueError):
        n = 60
    return max(0, n)


def _new_path_entry(path: str) -> dict:
    dw, dh = _decode_size()
    lw, lh = _layout_size()
    max_frames, fps = _decode_budget()
    return {
        "path": path,
        "frames": None,  # list[bytes] | None; immutable swap under lock
        "decode_w": dw,
        "decode_h": dh,
        "layout_w": lw,
        "layout_h": lh,
        "fps": fps,
        "target_frames": max_frames,
        "ready_partial": False,
        "ready_playable": False,  # ≥ min_playable frames; clock may arm
        "ready_full": False,
        "lock": threading.Lock(),
        "inflight": False,
        "layout_cached": False,
        # Stage budget for the *current* worker. 0 until the first start sets it
        # (warm uses a short prefix; play/ensure raises it to target_frames).
        "stage_frames": 0,
        "base_index": 0,
        "total_decoded": 0,
    }


def _get_or_create_entry(path: str) -> dict:
    key = _abspath_key(path) if path else path
    entry = _PATH_FRAME_CACHE.get(key)
    if entry is None:
        # Alias lookup: older entries may still be keyed by non-real abspath
        # (pre-realpath code). Prefer exact key; also probe realpath/abspath pair.
        if key and key not in _PATH_FRAME_CACHE:
            try:
                alt = os.path.abspath(path)
            except Exception:
                alt = None
            if alt and alt != key and alt in _PATH_FRAME_CACHE:
                entry = _PATH_FRAME_CACHE[alt]
                # Re-index under stable realpath key for future lookups.
                _PATH_FRAME_CACHE[key] = entry
        if entry is None:
            entry = _new_path_entry(key)
            _PATH_FRAME_CACHE[key] = entry
    return entry


def _min_playable_frames() -> int:
    """Minimum decoded frames before arming the presentation clock.

    Default 2 so true frame0 can hold under end_splash, then the kickstart
    chunk (see wgpu/video.py) arms the clock and the menu video starts moving
    without waiting for the full 360-frame @2 decode (~tens of seconds).
    """
    try:
        n = int(os.environ.get("RENPY_HOST_MOVIE_MIN_PLAYABLE", "2"))
    except (TypeError, ValueError):
        n = 2
    return max(1, n)


def _publish_frames(path: str, frames: List[bytes], *, full: bool) -> None:
    """Main-thread-safe list swap under entry lock (optional ring window).

    Absolute total from frames._abs_total when set; otherwise len(frames).
    Storage keeps only the trailing RING frames when RING > 0.
    """
    entry = _get_or_create_entry(path)
    # Snapshot frames for storage (may ring-trim). Do not mutate caller's list
    # here beyond reading _abs_total.
    try:
        total = int(getattr(frames, "_abs_total", 0) or 0)
    except Exception:
        total = 0
    src_list = frames if isinstance(frames, list) else list(frames)
    if total <= 0:
        total = len(src_list)
    ring = _ring_frames_budget()
    with entry["lock"]:
        prev_total_quick = int(entry.get("total_decoded") or 0)
        prev_n_quick = len(entry.get("frames") or [])
    # Stale restart publishes (lower total while already ahead) are dropped.
    if prev_total_quick > 0 and int(total) + 1 < prev_total_quick and not full:
        return
    # Growing + RING>0: trailing window only (RAM bound).
    # RING==0 / full: share the live list reference when possible so 1080p
    # progressive publish does not copy hundreds of frame pointers under the
    # product GIL every chunk (decode starvation class).
    # Full list identity is required for loop (abs 0..total-1).
    if ring > 0 and (not full) and len(src_list) > ring:
        frozen = list(src_list[-ring:])
    elif isinstance(src_list, list):
        frozen = src_list  # share; worker only appends / rare rebind
    else:
        frozen = list(src_list)
    if (
        not full
        and prev_total_quick == int(total)
        and prev_n_quick == len(frozen)
    ):
        return
    target = int(entry.get("target_frames") or _DEFAULT_MAX_FRAMES)
    min_play = _min_playable_frames()
    with entry["lock"]:
        prev_total = int(entry.get("total_decoded") or 0)
        # total_decoded never regresses; base is always derived from the
        # committed total so ring windows stay (total-n .. total-1).
        new_total = max(prev_total, int(total))
        have = len(frozen)
        new_base = max(0, new_total - have)
        entry["frames"] = frozen
        entry["base_index"] = int(new_base)
        entry["total_decoded"] = int(new_total)
        entry["ready_partial"] = have >= 1
        entry["ready_playable"] = have >= min_play or entry["total_decoded"] >= min_play
        if full and entry["total_decoded"] >= max(1, target):
            entry["ready_full"] = True
        elif full and entry["total_decoded"] < max(1, target):
            # Undershot target only counts as full on true EOF (caller sets
            # full=True after pipe EOF). Require progress near stage to avoid
            # marking full after a mid-stream stall at ~half the movie.
            if (
                entry["total_decoded"] >= 1
                and not entry.get("inflight")
                and entry["total_decoded"] >= max(1, min(target, entry.get("stage_frames") or target))
            ):
                entry["ready_full"] = True
            else:
                entry["ready_full"] = False
        if entry.get("layout_cached"):
            entry["layout_cached"] = bool(entry.get("layout_cached"))
    if entry.get("ready_full") and frozen:
        _phase0_log(
            f"T_cache_full path={path!r} frames={len(frozen)} "
            f"total={entry.get('total_decoded')} base={entry.get('base_index')} "
            f"decode={entry['decode_w']}x{entry['decode_h']}"
        )
    elif frozen:
        tag = "T_frame0_ready" if entry.get("total_decoded") == 1 else "T_partial_ready"
        # Throttle partial logs after playable to avoid I/O storms.
        tot = int(entry.get("total_decoded") or 0)
        if tot <= 8 or (tot % 30) == 0 or full:
            _phase0_log(
                f"{tag} path={path!r} frames={len(frozen)} "
                f"total={entry.get('total_decoded')} base={entry.get('base_index')} "
                f"decode={entry['decode_w']}x{entry['decode_h']} "
                f"playable={int(bool(entry.get('ready_playable')))}"
            )


def _maybe_tier_l_upscale(frames: List[bytes], dw: int, dh: int, lw: int, lh: int) -> tuple[List[bytes], bool]:
    """Optional Tier L: store full layout list. Default off.

    Forbidden on product 360 unless env opt-in. Uses per-frame helper only when
    explicitly enabled — never the default store path.
    """
    if not _layout_cache_enabled():
        return frames, False
    if (dw, dh) == (lw, lh):
        return frames, True
    # RSS guard: refuse Tier L when estimated layout RSS exceeds kill switch.
    est_mb = (float(len(frames)) * float(lw) * float(lh) * 4.0) / (1024.0 * 1024.0)
    if est_mb > float(_rss_limit_mb()):
        _phase0_log(
            f"TIER_L_REFUSED estimated_mb={est_mb:.1f} limit_mb={_rss_limit_mb()} "
            f"(keeping Tier S decode-size store)"
        )
        return frames, False
    out = [_bilinear_upscale_rgba(fr, dw, dh, lw, lh) for fr in frames]
    return out, True



def _decode_path_worker(path: str) -> None:
    """Guaranteed inflight clear on any exit path."""
    try:
        _decode_path_worker_impl(path)
    except Exception as exc:
        try:
            _phase0_log(f"T_decode_exc path={path!r} err={type(exc).__name__}:{exc}")
        except Exception:
            pass
    finally:
        try:
            entry = _get_or_create_entry(path)
            with entry["lock"]:
                entry["inflight"] = False
        except Exception:
            pass


def _decode_path_worker_impl(path: str) -> None:
    """Decode path into _PATH_FRAME_CACHE. Progressive: frame0 then stage.

    ``entry["stage_frames"]`` is the *this-worker* budget (warm prefix or full
    target). When the stage is less than ``target_frames``, ready_full stays
    False so a later continue worker can fill the rest. Prefers progressive
    on_chunk API; falls back to bulk decode_frames_ffmpeg.
    """
    entry = _get_or_create_entry(path)
    with entry["lock"]:
        dw = int(entry["decode_w"])
        dh = int(entry["decode_h"])
        lw = int(entry["layout_w"])
        lh = int(entry["layout_h"])
        target = int(entry.get("target_frames") or _DEFAULT_MAX_FRAMES)
        stage = int(entry.get("stage_frames") or target)
        existing = list(entry.get("frames") or [])
        try:
            abs_existing = int(entry.get("total_decoded") or 0)
            if abs_existing <= 0:
                abs_existing = int(entry.get("base_index") or 0) + len(existing)
            setattr(existing, "_abs_total", abs_existing)
        except Exception:
            pass
        fps = float(entry["fps"])
    stage = max(int(getattr(existing, "_abs_total", 0) or len(existing)), min(stage, target))

    import time as _time

    t0 = _time.monotonic()
    _phase0_log(
        f"T_decode_begin path={path!r} w={dw} h={dh} "
        f"stage={stage} target={target} have={len(existing)} fps={fps}"
    )

    try:
        from renpy.wgpu.video import decode_frames_ffmpeg
    except Exception:
        with entry["lock"]:
            entry["inflight"] = False
        _phase0_log(f"T_decode_end path={path!r} frames=0 err=import")
        return

    frames: List[bytes] = existing
    used_progressive = False

    def _on_chunk(frames_so_far: List[bytes]) -> None:
        if not frames_so_far:
            return
        try:
            abs_total = int(getattr(frames_so_far, "_abs_total", 0) or 0)
        except Exception:
            abs_total = 0
        if abs_total <= 0:
            abs_total = len(frames_so_far)
            try:
                setattr(frames_so_far, "_abs_total", abs_total)
            except Exception:
                pass
        hit_target = abs_total >= target
        # Stream already rate-limits publishes; always swap ring + total here.
        _publish_frames(path, frames_so_far, full=hit_target)

    # Already at/above stage — nothing to do (another worker may continue).
    if len(frames) >= stage:
        with entry["lock"]:
            entry["inflight"] = False
        if len(frames) >= target:
            _publish_frames(path, frames, full=True)
        _phase0_log(
            f"T_decode_end path={path!r} frames={len(frames)} "
            f"already_staged stage={stage} target={target}"
        )
        return

    # Fresh progressive decode from 0 up to stage.
    if not frames:
        try:
            try:
                from renpy.wgpu.video import (
                    decode_frames_ffmpeg_progressive as _decode_prog,
                )
            except Exception:
                _decode_prog = decode_frames_ffmpeg  # type: ignore
            frames = list(
                _decode_prog(
                    path,
                    dw,
                    dh,
                    max_frames=stage,
                    fps=fps,
                    on_chunk=_on_chunk,
                )
                or []
            )
            used_progressive = True
        except TypeError:
            used_progressive = False
        except Exception:
            used_progressive = False
            frames = []

        if not used_progressive:
            try:
                f0 = decode_frames_ffmpeg(path, dw, dh, max_frames=1, fps=fps)
            except Exception:
                f0 = []
            if f0:
                _publish_frames(path, list(f0), full=False)
            try:
                if stage <= 1 and f0:
                    frames = list(f0)
                else:
                    frames = list(
                        decode_frames_ffmpeg(
                            path, dw, dh, max_frames=stage, fps=fps
                        )
                        or []
                    )
            except Exception:
                frames = list(f0) if f0 else []
    else:
        # Continue from an existing playable prefix toward stage/target.
        # MUST append (continue_frames_ffmpeg / -ss), never re-decode from 0 —
        # re-decode would briefly shrink the live path-cache list mid-play.
        try:
            from renpy.wgpu.video import continue_frames_ffmpeg, FrameBag, _as_frame_bag

            # Work on a private list; publish via on_chunk (immutable swap).
            # Seed absolute total from path-cache entry so -ss continues past ring.
            work = _as_frame_bag(frames)
            try:
                with entry["lock"]:
                    abs_seed = int(entry.get("total_decoded") or 0)
                    base_seed = int(entry.get("base_index") or 0)
                if abs_seed <= 0:
                    abs_seed = base_seed + len(work)
                setattr(work, "_abs_total", abs_seed)
            except Exception:
                try:
                    setattr(work, "_abs_total", len(work))
                except Exception:
                    pass
            continue_frames_ffmpeg(
                path,
                dw,
                dh,
                work,
                max_frames=stage,
                fps=fps,
                on_chunk=_on_chunk,
            )
            frames = work
            used_progressive = True
        except Exception:
            used_progressive = False

    t_ffmpeg = _time.monotonic() - t0

    if not frames:
        with entry["lock"]:
            entry["inflight"] = False
        _phase0_log(
            f"T_decode_end path={path!r} frames=0 ffmpeg_s={t_ffmpeg:.3f}"
        )
        return

    # Tier S default: keep decode-size. Optional Tier L upscale store.
    store_frames, layout_cached = _maybe_tier_l_upscale(frames, dw, dh, lw, lh)
    with entry["lock"]:
        entry["layout_cached"] = layout_cached
        if layout_cached:
            entry["decode_w"] = lw
            entry["decode_h"] = lh

    _rss_telemetry(
        len(store_frames),
        int(entry["decode_w"]),
        int(entry["decode_h"]),
        path,
    )

    # Full only when we hit the product target (not a warm-stage mid-point).
    try:
        abs_total = int(getattr(store_frames, "_abs_total", 0) or 0)
    except Exception:
        abs_total = 0
    if abs_total <= 0:
        abs_total = len(store_frames)
        try:
            setattr(store_frames, "_abs_total", abs_total)
        except Exception:
            pass
    # Full only when we hit the product target, or this stage ended at/above
    # its budget with no further growth expected. Do NOT mark full merely
    # because stage==target while abs_total is a mid-stream stall (~half
    # movie) — that forces % wrapping and a visible jump/twitch.
    is_full = abs_total >= target or (
        stage >= target and abs_total >= max(1, stage) and abs_total >= target
    )
    # True short media: stage==target and worker finished with abs < target
    # but progressive stream hit EOF (abs_total == what we could decode).
    # Detect via "no inflight continue needed" only when abs is stable and
    # stage was the full target — still require abs_total >= 1 and that we
    # did not stop early due to a partial stage budget.
    if (not is_full) and stage >= target and abs_total >= 1:
        # Leave ready_full to a later continue/EOF publish; auto-chain below
        # will restart if have < target.
        is_full = False
    try:
        setattr(store_frames, "_abs_total", abs_total)
    except Exception:
        pass
    _publish_frames(path, store_frames, full=is_full)

    t_total = _time.monotonic() - t0
    _phase0_log(
        f"T_decode_end path={path!r} frames={len(store_frames)} "
        f"ffmpeg_s={t_ffmpeg:.3f} total_s={t_total:.3f} "
        f"layout={lw}x{lh} layout_cached={layout_cached} "
        f"progressive={used_progressive} full={int(is_full)} "
        f"stage={stage} target={target}"
    )

    with entry["lock"]:
        entry["inflight"] = False
        have_now = len(entry.get("frames") or store_frames or [])
        target_now = int(entry.get("target_frames") or _DEFAULT_MAX_FRAMES)
        ready_full_now = bool(entry.get("ready_full"))
    # Auto-chain continue-fill after warm stage ends so presentation does not
    # freeze at the warm prefix when play attached while warm was inflight.
    if (not ready_full_now) and have_now < target_now:
        try:
            _start_path_decode(path, background=True, stage_frames=target_now)
            _phase0_log(
                "T_decode_continue path=%r have=%s target=%s" % (path, have_now, target_now)
            )
        except Exception:
            pass


def _start_path_decode(
    path: str,
    *,
    background: bool,
    stage_frames: Optional[int] = None,
) -> None:
    """Start decode for path if not ready_full and not already inflight.

    ``stage_frames`` limits this worker's budget (warm prefix). None means
    decode up to ``target_frames`` (full product list).
    """
    entry = _get_or_create_entry(path)
    with entry["lock"]:
        if entry.get("ready_full"):
            return
        if entry.get("inflight"):
            return
        target = int(entry.get("target_frames") or _DEFAULT_MAX_FRAMES)
        if stage_frames is None:
            stage = target
        else:
            stage = max(1, min(int(stage_frames), target))
        # Never shrink an already-higher stage mid-flight intent.
        prev = int(entry.get("stage_frames") or 0)
        have = len(entry.get("frames") or [])
        entry["stage_frames"] = max(stage, prev, have)
        entry["inflight"] = True

    if background:
        t = threading.Thread(
            target=_decode_path_worker,
            args=(path,),
            name=f"host-movie-decode:{os.path.basename(path)}",
            daemon=True,
        )
        t.start()
    else:
        _decode_path_worker(path)


def _wait_entry_partial(entry: dict, timeout: float = 30.0) -> None:
    """Block until ready_partial or ready_full or timeout (play attach)."""
    import time as _time

    deadline = _time.monotonic() + max(0.1, float(timeout))
    while _time.monotonic() < deadline:
        with entry["lock"]:
            if entry.get("ready_partial") or entry.get("ready_full"):
                return
            if not entry.get("inflight") and not entry.get("ready_partial"):
                # Worker finished empty.
                return
        _time.sleep(0.01)


def _bind_channel_from_entry(channel: int, entry: dict) -> None:
    ch = _channels.get(channel)
    if not ch:
        return
    with entry["lock"]:
        frames = entry.get("frames")
        dw = int(entry.get("decode_w") or _DEFAULT_FRAME_W)
        dh = int(entry.get("decode_h") or _DEFAULT_FRAME_H)
        fps = float(entry.get("fps") or _DEFAULT_DECODE_FPS)
        layout_cached = bool(entry.get("layout_cached"))
        path = entry.get("path")
        ready_full = bool(entry.get("ready_full"))
        ready_playable = bool(entry.get("ready_playable")) or ready_full
        base_index = int(entry.get("base_index") or 0)
        total_decoded = int(entry.get("total_decoded") or (len(frames) if frames else 0))
    if not frames:
        return
    layout_w, layout_h = _layout_size()
    ch["frames"] = frames
    ch["media_path"] = path
    ch["decode_w"] = dw
    ch["decode_h"] = dh
    ch["decode_fps"] = fps
    ch["ready_full"] = ready_full
    ch["growing"] = (not ready_full) and ready_playable
    ch["base_index"] = base_index
    ch["total_decoded"] = total_decoded
    # Present size follows 1b (decode) or 1a (layout); read_video overwrites.
    if _present_mode_1b() and not layout_cached:
        ch["frame_w"], ch["frame_h"] = dw, dh
    else:
        ch["frame_w"], ch["frame_h"] = layout_w, layout_h
    ch["layout_cached"] = layout_cached
    # Keep play_frames in sync so read_video sees progressive growth without
    # resetting the armed clock. Share the same list object as the path cache
    # (immutable swap under entry lock) — do not copy 1080p frame lists.
    if ch.get("clock_armed"):
        ch["play_frames"] = frames
    else:
        ch["frame_index"] = 0
        if ready_playable or ready_full:
            ch["play_frames"] = frames


def _maybe_arm_clock(channel: int) -> None:
    """Arm video_clock when path cache is playable and channel is playing.

    Product path: arm as soon as a playable prefix is ready (≥
    ``RENPY_HOST_MOVIE_MIN_PLAYABLE``, default 2) so the main-menu movie
    starts moving without waiting for the full 360-frame @2 decode. While
    still growing, ``read_video`` clamps the index (no wrap). When the full
    list arrives later, ``_bind_channel_from_entry`` extends ``play_frames``
    without resetting the already-running clock (AC2 first-index still 0
    because the clock starts near the first playable prefix).
    """
    ch = _channels.get(channel)
    if not ch or not ch.get("video") or not ch.get("playing"):
        return

    path = ch.get("media_path")
    entry = _lookup_path_entry(path) if path else None

    def _extend_from_entry() -> None:
        if entry is None:
            return
        with entry["lock"]:
            n = len(entry.get("frames") or [])
            full = bool(entry.get("ready_full"))
        ch_n = len(ch.get("play_frames") or ch.get("frames") or [])
        if n > ch_n or (full and not ch.get("ready_full")):
            _bind_channel_from_entry(channel, entry)

    # Already armed (Python flag) — extend play_frames only; never restart clock.
    if ch.get("clock_armed"):
        _extend_from_entry()
        return

    # Host clock already running but Python flag was lost (e.g. partial unbind).
    # Re-sync the flag and extend frames — do NOT call video_clock_start again
    # or presentation jumps back to frame 0 mid-loop.
    try:
        pos_now = float(renpy_host.video_clock_pos(channel))
    except Exception:
        pos_now = 0.0
    if pos_now > 0.0:
        ch["clock_armed"] = True
        if entry is not None:
            _bind_channel_from_entry(channel, entry)
        _phase0_log(
            f"T_clock_resync channel={channel} pos={pos_now:.3f} "
            f"(flag lost; not restarting clock)"
        )
        return

    if entry is None:
        return

    with entry["lock"]:
        frames = entry.get("frames")
        ready_full = bool(entry.get("ready_full"))
        ready_playable = bool(entry.get("ready_playable")) or ready_full
        if not frames or not ready_playable:
            return
        # Share path-cache list; publish path never mutates in place.
        frozen = frames
        fps = float(entry.get("fps") or _DEFAULT_DECODE_FPS)
        dw = int(entry.get("decode_w") or _DEFAULT_FRAME_W)
        dh = int(entry.get("decode_h") or _DEFAULT_FRAME_H)
        layout_cached = bool(entry.get("layout_cached"))
        base_index = int(entry.get("base_index") or 0)
        total_decoded = int(entry.get("total_decoded") or len(frames))
        # Keep media_path on the stable realpath key.
        path = entry.get("path") or path

    layout_w, layout_h = _layout_size()
    ch["media_path"] = path
    ch["play_frames"] = frozen
    ch["frames"] = frozen
    ch["decode_fps"] = fps
    ch["decode_w"] = dw
    ch["decode_h"] = dh
    ch["ready_full"] = ready_full
    ch["growing"] = (not ready_full)
    ch["base_index"] = base_index
    ch["total_decoded"] = total_decoded
    if _present_mode_1b() and not layout_cached:
        ch["frame_w"], ch["frame_h"] = dw, dh
    else:
        ch["frame_w"], ch["frame_h"] = layout_w, layout_h
    ch["layout_cached"] = layout_cached
    # Never restart an already-running host clock (would jump to frame0).
    already = bool(ch.get("clock_armed"))
    if not already:
        try:
            pos_now = float(renpy_host.video_clock_pos(channel))
        except Exception:
            pos_now = 0.0
        already = pos_now > 0.0
    ch["clock_armed"] = True
    if not already:
        renpy_host.video_clock_start(channel)
    _phase0_log(
        f"T_clock_start channel={channel} path={path!r} "
        f"nframes={len(frozen)} fps={fps} ready_full={int(ready_full)} "
        f"growing={int(not ready_full)}"
    )
    # T1: bind audio sample clock as master when channel is playing and clock exists.
    # Only bind once when master is Wall; AudioSample master is set via video_clock_bind_audio.
    try:
        import renpy_host
        ch2 = _channels.get(channel)
        if ch2 and ch2.get("playing") and ch2.get("video"):
            # Check video_clock exists (pos >=0) and not yet bound
            try:
                # If drift probe exists, we can infer master; otherwise just attempt bind
                _has_bind = hasattr(renpy_host, "video_clock_bind_audio")
                _has_rate = hasattr(renpy_host, "audio_sample_rate")
                if _has_bind:
                    # Only bind if not already AudioSample (use flag or check drift? try bind idempotently)
                    if not ch2.get("_audio_clock_bound"):
                        _rate = int(renpy_host.audio_sample_rate() if _has_rate else 48000)
                        renpy_host.video_clock_bind_audio(int(channel), int(_rate))
                        ch2["_audio_clock_bound"] = True
            except Exception:
                pass
    except Exception:
        pass


def _ensure_video_frames(channel: int, block: bool = True) -> None:
    """Bind channel to path cache; start decode if needed.

    Does NOT start the video clock — that is deferred to ``_maybe_arm_clock``
    once a playable prefix is ready (``ready_playable`` / ``ready_full``).

    Decode always runs in a background thread. When ``block=True`` (play /
    set_video), this only waits for ``ready_partial`` (frame0), never the full
    360-frame product list.
    """
    ch = _channels.get(channel)
    if not ch or not ch.get("video"):
        return

    path = _resolve_media_path(ch.get("name"))
    if not path:
        f = ch.get("file")
        fname = getattr(f, "name", None) if f is not None else None
        if fname and os.path.isfile(str(fname)):
            path = str(fname)
    if not path:
        return

    path = _abspath_key(path)

    entry = _get_or_create_entry(path)
    ch["media_path"] = path

    # Fast path: already full — bind and return (clock armed by caller).
    with entry["lock"]:
        ready_full = bool(entry.get("ready_full"))
        ready_partial = bool(entry.get("ready_partial"))
        inflight = bool(entry.get("inflight"))
        have_n = len(entry.get("frames") or [])
        target = int(entry.get("target_frames") or _DEFAULT_MAX_FRAMES)

    if ready_full:
        _bind_channel_from_entry(channel, entry)
        return

    if ready_partial:
        _bind_channel_from_entry(channel, entry)
        # Warm stage may have finished (have_n < target, not inflight).
        # Continue to full product target in the background without blocking.
        if not inflight and have_n < target:
            _start_path_decode(path, background=True, stage_frames=target)
        # Always return once we have ≥1 frame — never fall into cold re-decode.
        # block=True still only waited for frame0; clock arms on playable later.
        if have_n >= 1 or not block:
            return
        # have_n == 0 but ready_partial flagged (shouldn't happen) → wait below.

    # Start decode if nothing inflight (cold path — no warm).
    if not inflight and not ready_full:
        # Always background the ffmpeg worker. Blocking play/set_video only
        # waits for ready_partial (frame0 hold) so the main thread is never
        # stuck on a multi-second full 1080p decode (product hang class).
        # Cold play goes straight to full target (warm already staged if any).
        _start_path_decode(path, background=True, stage_frames=target)
        if not block:
            return

    if block:
        # Wait for at least frame0 (or full if already done). Never re-ffmpeg.
        # Cap partial wait tightly — multi-second block here is the post-logo
        # stall class. Frame0 CLI decode is ~0.15s; allow headroom for cold
        # start / machine load without hanging the interaction.
        with entry["lock"]:
            still_inflight = bool(entry.get("inflight"))
            have_partial = bool(entry.get("ready_partial") or entry.get("ready_full"))
        if still_inflight and not have_partial:
            try:
                wait_s = float(os.environ.get("RENPY_HOST_MOVIE_PARTIAL_WAIT_S", "3.0"))
            except (TypeError, ValueError):
                wait_s = 3.0
            wait_s = max(0.5, min(wait_s, 10.0))
            _wait_entry_partial(entry, timeout=wait_s)
        elif still_inflight and have_partial and block:
            # Already have frame0 — return immediately so play() can present.
            pass

    _bind_channel_from_entry(channel, entry)
    # Retry warm if play resolved a path that init could not (gamedir race).
    try:
        maybe_warm_menu_video()
    except Exception:
        pass


# --- Public helpers (worker-3 Movie.render hold / gates) ----------------------


def path_cache_has_frames(path_or_name: Any) -> bool:
    """True when the path-keyed cache has ≥1 frame for this media identity."""
    entry = _lookup_path_entry(path_or_name)
    if not entry:
        return False
    with entry["lock"]:
        frames = entry.get("frames")
        return bool(frames) and len(frames) >= 1


def path_cache_ready_full(path_or_name: Any) -> bool:
    """True when path cache finished full decode for this media."""
    entry = _lookup_path_entry(path_or_name)
    if not entry:
        return False
    with entry["lock"]:
        return bool(entry.get("ready_full")) and bool(entry.get("frames"))


def path_cache_frame_count(path_or_name: Any) -> int:
    entry = _lookup_path_entry(path_or_name)
    if not entry:
        return 0
    with entry["lock"]:
        total = int(entry.get("total_decoded") or 0)
        if total > 0:
            return total
        frames = entry.get("frames")
        return len(frames) if frames else 0


def path_cache_frame0_surface(path_or_name: Any):
    """Frame-0 Surface for Movie.render hold, or None if cache empty.

    Present 1b: decode-size (mesh-scaled). Present 1a: layout-sized.
    Alias-friendly name for worker-3 Movie.render hold.
    """
    return peek_path_frame0(path_or_name)


def _lookup_path_entry(path_or_name: Any) -> Optional[dict]:
    """Find a path-cache entry by Movie name / path (realpath-stable)."""
    key = _path_cache_key(path_or_name)
    entry = _PATH_FRAME_CACHE.get(key) if key else None
    if entry is None and path_or_name is not None:
        raw = str(path_or_name)
        entry = _PATH_FRAME_CACHE.get(raw)
        if entry is None and raw and os.path.isfile(raw):
            entry = _PATH_FRAME_CACHE.get(_abspath_key(raw))
    return entry


def peek_path_frame0(path_or_name: Any):
    """Return a presentable Surface of frame0 for path, or None.

    Used by host_build Movie.render to hold first frame even when the movie
    channel is not yet playing (AC1 / end_splash dual-slot). Does not arm any
    channel clock.

    Present **1b** (default): return **decode-size** Surface so Movie.render
    mesh-scales (matches read_video; avoids a one-shot CPU bilinear 540→1080
    hitch on first hold under end_splash). Present **1a**: layout-sized.
    """
    entry = _lookup_path_entry(path_or_name)
    if not entry:
        return None
    with entry["lock"]:
        frames = entry.get("frames")
        if not frames:
            return None
        fr = frames[0]
        dw = int(entry.get("decode_w") or _DEFAULT_FRAME_W)
        dh = int(entry.get("decode_h") or _DEFAULT_FRAME_H)
        lw = int(entry.get("layout_w") or _LAYOUT_FRAME_W)
        lh = int(entry.get("layout_h") or _LAYOUT_FRAME_H)
        layout_cached = bool(entry.get("layout_cached"))

    use_1b = _present_mode_1b() and not layout_cached
    if use_1b:
        w, h = dw, dh
        need = w * h * 4
        if len(fr) >= need:
            rgba = fr[:need] if len(fr) > need else (
                fr if isinstance(fr, (bytes, bytearray)) else bytes(fr)
            )
            if not isinstance(rgba, (bytes, bytearray)):
                rgba = bytes(rgba)
            elif isinstance(rgba, bytearray):
                rgba = bytes(rgba)
        else:
            buf = bytearray(need)
            copy_n = min(len(fr), need)
            buf[:copy_n] = fr[:copy_n]
            rgba = bytes(buf)
    else:
        w, h = lw, lh
        need = w * h * 4
        if layout_cached and len(fr) == need:
            rgba = fr if isinstance(fr, (bytes, bytearray)) else bytes(fr)
        elif len(fr) == need and (dw, dh) == (lw, lh):
            rgba = fr if isinstance(fr, (bytes, bytearray)) else bytes(fr)
        elif len(fr) == dw * dh * 4 and (dw, dh) != (lw, lh):
            rgba = _bilinear_upscale_rgba(fr, dw, dh, lw, lh)
        elif len(fr) == need:
            rgba = fr if isinstance(fr, (bytes, bytearray)) else bytes(fr)
        else:
            buf = bytearray(need)
            copy_n = min(len(fr), need)
            buf[:copy_n] = fr[:copy_n]
            rgba = bytes(buf)

    surf = _make_surface(w, h)
    need = w * h * 4
    if len(rgba) >= need:
        surf._pixels = bytearray(rgba[:need])
    else:
        surf._pixels = bytearray(rgba + bytes(need - len(rgba)))
    return surf


def warm_video_path(path: Any) -> bool:
    """Begin background staged decode for ``path`` (or Movie name).

    Splash warm uses ``RENPY_HOST_MOVIE_WARM_FRAMES`` (default 90) so frame0 /
    a playable prefix are ready before end_splash without filling the full
    360-frame 1080p list during dual-draw. Play / ensure later continues to
    ``target_frames``. Survives channel stop. No-ops if already ready_full
    or inflight. Returns True if a warm was started or cache already has frames.
    """
    key = _path_cache_key(path)
    if not key:
        # Accept absolute existing paths even if key helper missed.
        s = str(path) if path is not None else ""
        if s and os.path.isfile(s):
            key = _abspath_key(s)
        else:
            return False

    entry = _get_or_create_entry(key)
    with entry["lock"]:
        if entry.get("ready_full"):
            return True
        if entry.get("inflight"):
            return True
        target = int(entry.get("target_frames") or _DEFAULT_MAX_FRAMES)
        # Default: one progressive stream to full target (avoids warm/continue
        # boundary freeze). Opt into staged stop with RENPY_HOST_MOVIE_WARM_STAGED=1.
        staged = os.environ.get("RENPY_HOST_MOVIE_WARM_STAGED", "").strip().lower()
        if staged in ("1", "true", "yes", "on"):
            stage = _warm_stage_frames(target)
        else:
            stage = target
    _start_path_decode(key, background=True, stage_frames=stage)
    _WARM_STARTED_PATHS.add(key)
    return True


def maybe_warm_menu_video() -> bool:
    """Warm HMC main-menu video when env enabled (idempotent, retryable).

    Gated by RENPY_HOST_WARM_MENU_VIDEO=1. Resolves the same path identity as
    later play() — base ``video/main_menu.webm`` unless PREFER_AT2 rewrites.

    If the media path is not resolvable yet (pre-gamedir init), returns False
    without latching so a later call (play / set_video / second init) can retry.
    Once a warm is started or frames exist, subsequent calls are no-ops.
    Must not cancel on channel stop.
    """
    global _warm_menu_once
    flag = os.environ.get("RENPY_HOST_WARM_MENU_VIDEO", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False

    # Already warm / inflight / cached — do not start a second worker.
    if (
        path_cache_has_frames(_HMC_MENU_VIDEO)
        or path_cache_has_frames(_HMC_MENU_VIDEO_AT2)
        or bool(_WARM_STARTED_PATHS)
    ):
        _warm_menu_once = True
        return True

    # Single resolve helper: @2 when loadable (opt-in), else base, else env.
    path = _resolve_media_path(_HMC_MENU_VIDEO)
    if not path:
        env = os.environ.get("RENPY_HOST_MOVIE_PATH")
        if env and os.path.isfile(env):
            preferred = _prefer_hmc_menu_at2(env)
            path = preferred if preferred and os.path.isfile(preferred) else env
    if not path:
        # Do not latch _warm_menu_once — gamedir may appear later.
        _phase0_log("T_warm_menu path=None started=0 (retryable; unresolved)")
        return False

    _warm_menu_once = True
    ok = warm_video_path(path)
    _phase0_log(
        f"T_warm_menu path={path!r} started={ok} "
        f"at2={int(os.path.basename(path) == _HMC_MENU_BASENAME_AT2)}"
    )
    return ok
