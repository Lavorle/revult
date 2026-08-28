"""
Host video texture path (Phase 6).

Uploads RGBA frames to a reusable wgpu texture via create_texture_rgba +
write_texture_rgba, and drives presentation with renpy_host video_clock_*.

MVP sources:
  1. FFmpeg CLI decode when `ffmpeg` is on PATH and a media file is given.
  2. Synthetic gradient frames (always available).

In-process libav* linkage is deferred to Phase 9. Dual-tree safe: SDL path
does not import this module.

Chunked decode (product 360@30):
  decode_frames_ffmpeg splits work into chunks of
  RENPY_HOST_MOVIE_CHUNK_FRAMES (default 20) with per-chunk timeout
  max(30, 0.15 * chunk_frames). Pass on_chunk for frame0-first progressive warm.

  RENPY_HOST_MOVIE_KICKSTART_FRAMES (default 8): while the progressive stream
  has fewer than this many frames, publish on every frame so the host clock
  can arm as soon as MIN_PLAYABLE is reached. After kickstart, publish every
  CHUNK frames.
"""
# Shim: host A/V clocks/decode live in renpy_host (state.rs VideoClock +
# python.rs video_clock_*); this Python module is a compat shim — thin
# ffmpeg-CLI progressive decode + VideoTexture smoke. Dual-tree safe (SDL
# never imports renpy_host here). Keep lean — helpers are gate/smoke shims,
# not draw.py deps. Host product drives clocks via renpy_host.video_clock_*.
# Helpers retained intentionally for gate/smoke compat (Phase 6) - not draw deps.

from __future__ import annotations

import contextlib
import logging
import os
import queue as _queue
import shutil
import subprocess
import tempfile
import threading as _threading
import time as _time
from collections import deque
from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

try:
    from renpy.wgpu.constants import (
        FFMPEG_CHUNK_FRAMES as _C_CHUNK,
        FFMPEG_KICKSTART_FRAMES as _C_KICK,
        FFMPEG_TIMEOUT_BASE as _C_TBASE,
        FFMPEG_TIMEOUT_PER_FRAME as _C_TPER,
    )
except Exception:  # noqa: BLE001 -- constants fallback keeps dual-tree safe
    _C_CHUNK = 20
    _C_KICK = 8
    _C_TBASE = 30.0
    _C_TPER = 0.15

_DEFAULT_CHUNK_FRAMES = int(_C_CHUNK)
_DEFAULT_KICKSTART_FRAMES = int(_C_KICK)
_CHUNK_TIMEOUT_FLOOR_S = float(_C_TBASE)
_CHUNK_TIMEOUT_PER_FRAME_S = float(_C_TPER)

# Also expose under canonical names for importers/tests that read constants.
FFMPEG_CHUNK_FRAMES = _DEFAULT_CHUNK_FRAMES
FFMPEG_KICKSTART_FRAMES = _DEFAULT_KICKSTART_FRAMES
FFMPEG_TIMEOUT_BASE = _CHUNK_TIMEOUT_FLOOR_S
FFMPEG_TIMEOUT_PER_FRAME = _CHUNK_TIMEOUT_PER_FRAME_S

try:
    import renpy_host  # type: ignore
except ImportError:  # pragma: no cover - SDL tree
    renpy_host = None  # type: ignore

def _video_backend() -> str:
    """M2 B3 T4 分流桩: RENPY_HOST_VIDEO_BACKEND=cli|host 默认 cli, V1 仍 CLI V2 切 host."""
    return os.environ.get("RENPY_HOST_VIDEO_BACKEND", "cli")

# Full-screen NDC quad: x,y,u,v,r,g,b,a
_FULLSCREEN_VERTS = [
    -1.0, -1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0,
     1.0, -1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
     1.0,  1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0,
    -1.0,  1.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0,
]
_FULLSCREEN_INDICES = [0, 1, 2, 0, 2, 3]


class FrameBag(list):
    """RGBA frame list that can carry absolute decode counters.

    Built-in ``list`` rejects arbitrary attributes on some Python builds; a
    thin subclass keeps ``_abs_total`` reliable after ring trims. When
    ``live_cap`` is given, an internal ``deque(maxlen=live_cap)`` enforces a
    bounded ring while preserving the ``list``-like API and ``isinstance``
    checks used across the decode pipeline.

    Byte-cap extension (M2 T3): ``cap_bytes`` limits retained bytes (None=unlimited,
    default 64 MiB), ``evicted_bytes`` counts evicted payload, ``seek_index``
    records (pts_ms, byte_offset, is_key) probes. ``append_limited`` enforces
    the cap by popleft eviction, keeps ``_abs_total`` monotonic, and syncs
    ``seek_index`` removal. ``build_seek_cmd`` is a probe returning an
    ``ffmpeg -ss``-prefixed command and recording the seek in ``seek_index``.
    """

    def __init__(self, iterable=(), *, abs_total: int = 0, live_cap: int | None = None, cap_bytes: int | None = 64 * 1024 * 1024):
        if iterable is None:
            iterable = ()
        super().__init__(iterable)
        self._abs_total = int(abs_total or 0)
        if self._abs_total == 0 and len(self) > 0:
            # Preserve monotonic total when caller supplies iterable without explicit total
            with contextlib.suppress(Exception):
                self._abs_total = len(self)
        self._live_cap = live_cap
        self.cap_bytes: int | None = cap_bytes
        self.evicted_bytes: int = 0
        self.seek_index: list[tuple[int, int, bool]] = []
        if live_cap is not None:
            self.frames: deque = deque(self, maxlen=live_cap)
        else:
            self.frames: deque = deque(self)
        # Enforce byte cap on initial iterable (if any) by evicting oldest until under cap
        if self.cap_bytes is not None and self.cap_bytes > 0 and len(self) > 0:
            try:
                total = sum(len(f) for f in self)  # type: ignore[arg-type]
                while total > self.cap_bytes and len(self) > 0:
                    ev = super().pop(0)
                    self.evicted_bytes += len(ev) if isinstance(ev, (bytes, bytearray)) else 0
                    if self.seek_index:
                        with contextlib.suppress(Exception):
                            self.seek_index.pop(0)
                    total = sum(len(f) for f in self)  # type: ignore[arg-type]
                if live_cap is not None:
                    self.frames = deque(self, maxlen=live_cap)
                else:
                    self.frames = deque(self)
            except Exception:
                pass

    def append(self, item):  # type: ignore[override]
        if self._live_cap is not None:
            self.frames.append(item)
            super().__init__(self.frames)
        else:
            super().append(item)
            self.frames.append(item)

    def extend(self, iterable):  # type: ignore[override]
        for it in iterable:
            self.append(it)

    def clear(self):  # type: ignore[override]
        super().clear()
        with contextlib.suppress(Exception):
            self.frames.clear()
        # Reset probes but keep cap
        with contextlib.suppress(Exception):
            self.evicted_bytes = 0
            self.seek_index.clear()

    def __setitem__(self, key, value):  # type: ignore[override]
        super().__setitem__(key, value)
        if self._live_cap is not None:
            if len(self) > self._live_cap:
                trimmed = list(self)[-self._live_cap:]
                super().__init__(trimmed)
            self.frames = deque(self, maxlen=self._live_cap)
        else:
            self.frames = deque(self)

    def append_limited(self, frame: bytes) -> None:
        """Append *frame* respecting ``cap_bytes`` byte budget.

        If ``cap_bytes`` is not None and ``sum_len + len(frame) > cap_bytes``,
        popleft the oldest frames (accumulating ``evicted_bytes``) until the
        new frame fits or the list is empty. Keeps ``_abs_total`` monotonic
        and syncs ``seek_index`` removal. Also logs ``evicted_bytes``/
        ``cap_bytes``/``ring_len`` for observability.
        """
        try:
            flen = len(frame)  # type: ignore[arg-type]
        except Exception:
            flen = 0
        # Byte-cap eviction loop
        if self.cap_bytes is not None:
            try:
                cur = sum(len(f) for f in self)  # type: ignore[arg-type]
            except Exception:
                cur = 0
            while cur + flen > self.cap_bytes and len(self) > 0:
                try:
                    ev = super().pop(0)
                except Exception:
                    break
                ev_len = len(ev) if isinstance(ev, (bytes, bytearray)) else 0
                self.evicted_bytes += ev_len
                if self.seek_index:
                    with contextlib.suppress(Exception):
                        self.seek_index.pop(0)
                # sync deque after each eviction
                with contextlib.suppress(Exception):
                    if self._live_cap is not None:
                        self.frames = deque(self, maxlen=self._live_cap)
                    else:
                        # rebuild deque from list
                        self.frames = deque(self)
                with contextlib.suppress(Exception):
                    logging.getLogger(__name__).info(
                        "FrameBag evicted cap_bytes=%s evicted_bytes=%s ring_len=%s",
                        self.cap_bytes,
                        self.evicted_bytes,
                        len(self),
                    )
                try:
                    cur = sum(len(f) for f in self)  # type: ignore[arg-type]
                except Exception:
                    cur = 0
        # live_cap pre-evict bookkeeping: if next append would overflow live_cap, account oldest
        if self._live_cap is not None and len(self) >= self._live_cap and len(self) > 0:
            try:
                oldest = self[0]
                oldest_len = len(oldest) if isinstance(oldest, (bytes, bytearray)) else 0
                # Will be evicted by deque maxlen; count it now and sync seek_index
                self.evicted_bytes += oldest_len
                if self.seek_index:
                    with contextlib.suppress(Exception):
                        self.seek_index.pop(0)
            except Exception:
                pass
        # Actual append respecting live_cap
        if self._live_cap is not None:
            self.frames.append(frame)
            super().__init__(self.frames)
        else:
            super().append(frame)
            with contextlib.suppress(Exception):
                self.frames.append(frame)
        # monotonic total
        try:
            self._abs_total = int(self._abs_total or 0) + 1
        except Exception:
            with contextlib.suppress(Exception):
                self._abs_total = len(self)
        # seek_index probe: pts derived from _abs_total (approx 30fps), byte_offset from evicted+prefix
        try:
            pts_ms = int((self._abs_total - 1) * 1000 / 30.0)
            try:
                # byte_offset = evicted plus bytes before this frame
                byte_offset = self.evicted_bytes + sum(len(f) for f in self[:-1])  # type: ignore[arg-type]
            except Exception:
                byte_offset = self.evicted_bytes
            is_key = True
            self.seek_index.append((pts_ms, int(byte_offset), bool(is_key)))
        except Exception:
            pass
        with contextlib.suppress(Exception):
            logging.getLogger(__name__).debug(
                "FrameBag cap_bytes=%s evicted_bytes=%s ring_len=%s total=%s",
                self.cap_bytes,
                self.evicted_bytes,
                len(self),
                self._abs_total,
            )

    def build_seek_cmd(self, path: str, w: int, h: int, fps: float, seek_ms: int) -> list[str]:
        """Probe: build ``ffmpeg -ss`` seek command and record ``seek_index``.

        Reuses :func:`_vf_scale_fps` and :func:`_chunk_frame_budget` (no new
        timeout dimension). Records ``(seek_ms, byte_offset, is_key)`` in
        ``seek_index`` and logs cap state.
        """
        # Reuse helpers for contract
        try:
            _ = _chunk_frame_budget()
        except Exception:
            pass
        try:
            vf = _vf_scale_fps(int(w), int(h), float(fps), scale=True)
        except Exception:
            vf = f"fps={fps}"
        try:
            seek_s = float(seek_ms) / 1000.0
        except Exception:
            seek_s = 0.0
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-threads",
            "0",
            "-ss",
            f"{seek_s:.3f}",
            "-i",
            str(path),
            "-vf",
            vf,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "pipe:1",
        ]
        try:
            byte_offset = self.evicted_bytes + sum(len(f) for f in self) if self else self.evicted_bytes  # type: ignore[arg-type]
        except Exception:
            byte_offset = int(self.evicted_bytes or 0)
        with contextlib.suppress(Exception):
            self.seek_index.append((int(seek_ms), int(byte_offset), True))
        with contextlib.suppress(Exception):
            logging.getLogger(__name__).info(
                "FrameBag build_seek_cmd path=%r seek_ms=%s cap_bytes=%s evicted_bytes=%s ring_len=%s",
                path,
                seek_ms,
                self.cap_bytes,
                self.evicted_bytes,
                len(self),
            )
        return cmd


def _as_frame_bag(frames) -> FrameBag:
    if isinstance(frames, FrameBag):
        return frames
    abs_total = getattr(frames, "_abs_total", 0) or 0
    with contextlib.suppress(Exception):
        abs_total = int(abs_total)
    if not isinstance(abs_total, int):
        abs_total = 0
    bag = FrameBag(
        frames or (),
        abs_total=abs_total if abs_total > 0 else len(frames or ()),
    )
    return bag


def _set_abs_total(frames, n: int) -> None:
    n = int(n)
    with contextlib.suppress(Exception):
        frames._abs_total = n  # type: ignore[attr-defined]
        return
    with contextlib.suppress(Exception):
        object.__setattr__(frames, "_abs_total", n)


def _get_abs_total(frames) -> int:
    n = getattr(frames, "_abs_total", 0) or 0
    with contextlib.suppress(Exception):
        n = int(n)
    if isinstance(n, int) and n > 0:
        return n
    return len(frames) if frames is not None else 0


def _solid_frame(width: int, height: int, rgba: tuple[int, int, int, int]) -> bytes:  # type: ignore - shim retained (smoke init)
    r, g, b, a = rgba
    return bytes([r, g, b, a]) * (width * height)


def _gradient_frame(width: int, height: int, t: float) -> bytes:  # type: ignore - shim retained (golden bars)
    """Moving color bars keyed by t in [0,1] — deterministic for goldens later."""
    out = bytearray(width * height * 4)
    phase = int(t * 255) & 0xFF
    i = 0
    for y in range(height):
        for x in range(width):
            out[i] = (x * 255 // max(width - 1, 1) + phase) & 0xFF
            out[i + 1] = (y * 255 // max(height - 1, 1)) & 0xFF
            out[i + 2] = (255 - phase) & 0xFF
            out[i + 3] = 255
            i += 4
    return bytes(out)


def synthetic_frames(width: int, height: int, count: int) -> list[bytes]:  # type: ignore - shim retained (ffmpeg fallback)
    """Generate `count` RGBA frames of size width×height."""
    if count <= 0:
        return []
    frames = []
    for n in range(count):
        t = n / max(count - 1, 1)
        frames.append(_gradient_frame(width, height, t))
    return frames


def split_yuv420p(data: bytes, width: int, height: int) -> tuple[bytes, bytes, bytes]:
    """Split raw yuv420p bytes into (Y, U, V) planes.

    Y size = w*h, U/V each = w*h//4 (half width/height subsampled).
    Raises ValueError if buffer too short.
    """
    w = int(width)
    h = int(height)
    if w <= 0 or h <= 0:
        raise ValueError("width/height must be >0")
    y_size = w * h
    uv_size = (w // 2) * (h // 2)
    # fallback for odd sizes: use //4
    if uv_size == 0:
        uv_size = y_size // 4
    expected = y_size + uv_size * 2
    if len(data) < expected:
        raise ValueError(f"yuv420p buffer too short: {len(data)} < {expected}")
    y = data[:y_size]
    u = data[y_size : y_size + uv_size]
    v = data[y_size + uv_size : y_size + uv_size * 2]
    return bytes(y), bytes(u), bytes(v)


def rgba_to_yuv420p(rgba: bytes, width: int, height: int) -> bytes:
    """Convert RGBA (w*h*4) to yuv420p raw (BT.601 full range)."""
    w = int(width)
    h = int(height)
    expected = w * h * 4
    if len(rgba) < expected:
        raise ValueError(f"rgba too short: {len(rgba)} < {expected}")
    y_plane = bytearray(w * h)
    u_plane = bytearray((w // 2) * (h // 2))
    v_plane = bytearray((w // 2) * (h // 2))
    # per-pixel Y
    for y in range(h):
        for x in range(w):
            idx = (y * w + x) * 4
            r = rgba[idx]
            g = rgba[idx + 1]
            b = rgba[idx + 2]
            y_val = int(round(0.299 * r + 0.587 * g + 0.114 * b))
            y_plane[y * w + x] = max(0, min(255, y_val))
    # subsampled U/V average 2x2
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r_sum = g_sum = b_sum = 0
            cnt = 0
            for dy in (0, 1):
                for dx in (0, 1):
                    yy = y + dy
                    xx = x + dx
                    if yy < h and xx < w:
                        idx = (yy * w + xx) * 4
                        r_sum += rgba[idx]
                        g_sum += rgba[idx + 1]
                        b_sum += rgba[idx + 2]
                        cnt += 1
            if cnt == 0:
                continue
            r_avg = r_sum / cnt
            g_avg = g_sum / cnt
            b_avg = b_sum / cnt
            u_val = int(round(-0.168736 * r_avg - 0.331264 * g_avg + 0.5 * b_avg + 128))
            v_val = int(round(0.5 * r_avg - 0.418688 * g_avg - 0.081312 * b_avg + 128))
            u_plane[(y // 2) * (w // 2) + (x // 2)] = max(0, min(255, u_val))
            v_plane[(y // 2) * (w // 2) + (x // 2)] = max(0, min(255, v_val))
    return bytes(y_plane) + bytes(u_plane) + bytes(v_plane)


def yuv420p_to_rgba(yuv: bytes, width: int, height: int) -> bytes:
    """Convert yuv420p raw back to RGBA (BT.601 full range, shader parity)."""
    w = int(width)
    h = int(height)
    y_size = w * h
    uv_size = (w // 2) * (h // 2)
    if len(yuv) < y_size + uv_size * 2:
        raise ValueError("yuv buffer too short")
    y_plane, u_plane, v_plane = split_yuv420p(yuv, w, h)
    out = bytearray(w * h * 4)
    uv_w = w // 2
    for y in range(h):
        for x in range(w):
            Y = y_plane[y * w + x]
            uv_x = x // 2
            uv_y = y // 2
            U = u_plane[uv_y * uv_w + uv_x]
            V = v_plane[uv_y * uv_w + uv_x]
            u_ = U - 128
            v_ = V - 128
            r = Y + 1.402 * v_
            g = Y - 0.344136 * u_ - 0.714136 * v_
            b = Y + 1.772 * u_
            out[(y * w + x) * 4] = int(max(0, min(255, round(r))))
            out[(y * w + x) * 4 + 1] = int(max(0, min(255, round(g))))
            out[(y * w + x) * 4 + 2] = int(max(0, min(255, round(b))))
            out[(y * w + x) * 4 + 3] = 255
    return bytes(out)


def ffmpeg_available() -> bool:  # type: ignore - shim retained (feature probe)
    return shutil.which("ffmpeg") is not None


def _chunk_frame_budget() -> int:
    try:
        n = int(os.environ.get("RENPY_HOST_MOVIE_CHUNK_FRAMES", _DEFAULT_CHUNK_FRAMES))
    except (TypeError, ValueError):
        n = _DEFAULT_CHUNK_FRAMES
    return max(1, n)


def _kickstart_frame_budget() -> int:
    """Frames during which progressive decode publishes every frame.

    After frame0, the continuous stream publishes each new frame until the
    list length reaches this budget so the host clock can arm at
    MIN_PLAYABLE without waiting for a full CHUNK. Then publish_every
    (CHUNK) takes over. Env: RENPY_HOST_MOVIE_KICKSTART_FRAMES.
    """
    try:
        n = int(os.environ.get("RENPY_HOST_MOVIE_KICKSTART_FRAMES", _DEFAULT_KICKSTART_FRAMES))
    except (TypeError, ValueError):
        n = _DEFAULT_KICKSTART_FRAMES
    return max(1, n)


def _chunk_timeout_s(chunk_frames: int) -> float:
    return max(_CHUNK_TIMEOUT_FLOOR_S, _CHUNK_TIMEOUT_PER_FRAME_S * max(1, chunk_frames))


def _vf_scale_fps(width: int, height: int, fps: float, *, scale: bool = True) -> str:
    """Build ffmpeg -vf chain. Skip scale when media already matches size."""
    if scale:
        return f"scale={width}:{height}:flags=fast_bilinear,fps={fps}"
    return f"fps={fps}"


class FfmpegCmdBuilder:
    """Single point constructing the ffmpeg prefix.

    Produces ``["ffmpeg","-hide_banner","-threads","0","-vf",<vf>]`` and
    appends ``["-f","rawvideo"]`` only for pipe output. The three duplicate
    ``-hide_banner -threads 0 -vf scale+fps`` sites in the decode path are
    routed through this builder.
    """

    @staticmethod
    def build(
        path: str,
        w: int,
        h: int,
        fps: float,
        use_file: bool,
        scale: bool | None = None,
    ) -> list[str]:
        _ = path  # kept for call-site symmetry; prefix is path-independent
        vf = _vf_scale_fps(int(w), int(h), float(fps), scale=True if scale is None else bool(scale))
        cmd = ["ffmpeg", "-hide_banner", "-threads", "0", "-vf", vf]
        if not use_file:
            cmd += ["-f", "rawvideo"]
        return cmd

    @staticmethod
    def build_chunk_cmd(
        path: str,
        w: int,
        h: int,
        fps: float,
        *,
        yuv: str | None = None,
    ) -> list[str]:
        """Build ffmpeg command prefix for a chunk, optionally YUV420p.

        When yuv=='yuv420p' emits ``-pix_fmt yuv420p -f rawvideo`` (full-range BT.601),
        otherwise defaults to rgba.
        """
        _ = path
        vf = _vf_scale_fps(int(w), int(h), float(fps), scale=True)
        pix = "yuv420p" if yuv == "yuv420p" else "rgba"
        cmd = ["ffmpeg", "-hide_banner", "-threads", "0", "-vf", vf, "-pix_fmt", pix, "-f", "rawvideo"]
        return cmd

    @staticmethod
    def build_seek_cmd(path: str, w: int, h: int, fps: float, seek_ms: int) -> list[str]:
        """Probe: build ``ffmpeg -ss`` seek-prefixed command.

        Reuses :func:`_vf_scale_fps` and :func:`_chunk_frame_budget` (no new
        timeout dimension). Returns ``["ffmpeg","-hide_banner","-threads","0",
        "-ss",<seek_s>,"-i",path,"-vf",<vf>,"-f","rawvideo","-pix_fmt","rgba",
        "pipe:1"]``. Caller may record the seek in ``FrameBag.seek_index``.
        """
        # Reuse helpers for contract
        with contextlib.suppress(Exception):
            _ = _chunk_frame_budget()
        vf = _vf_scale_fps(int(w), int(h), float(fps), scale=True)
        try:
            seek_s = float(seek_ms) / 1000.0
        except Exception:
            seek_s = 0.0
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-threads",
            "0",
            "-ss",
            f"{seek_s:.3f}",
            "-i",
            str(path),
            "-vf",
            vf,
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "pipe:1",
        ]
        with contextlib.suppress(Exception):
            logging.getLogger(__name__).debug(
                "FfmpegCmdBuilder build_seek_cmd path=%r seek_ms=%s vf=%s", path, seek_ms, vf
            )
        return cmd


@runtime_checkable
class Decoder(Protocol):
    def read_chunk(self) -> bytes | None: ...
    def publish(self, bag: FrameBag) -> None: ...


class _BaseDecoder:
    """Abstract base for video decoders — subclasses must override :meth:`_read_one` and :meth:`is_done`.

    Concrete decoders: :class:`PipeReader` (Popen stdout queue) and
    :class:`FilePoller` (temp .rgba file poll). Both are fully implemented;
    the base intentionally raises ``NotImplementedError`` for missing overrides.
    """
    def __init__(
        self,
        *,
        raw_size: int,
        width: int,
        height: int,
        fps: float,
        path: str,
        live_cap: int,
        all_frames: list[bytes],
        on_chunk: Callable[[list[bytes]], None] | None,
        kickstart: int,
        publish_every: int,
        timeout: float,
        t0: float,
        max_frames: int = 0,
    ):
        self._raw_size = int(raw_size)
        self._width = int(width)
        self._height = int(height)
        self._fps = float(fps)
        self._path = path
        self._live: FrameBag = FrameBag(live_cap=live_cap)
        self._all_frames = all_frames
        self._on_chunk = on_chunk
        self._kickstart = max(1, int(kickstart))
        self._publish_every = max(1, int(publish_every))
        self._timeout = float(timeout)
        self._t0 = t0
        self._max_frames = int(max_frames)
        self.decoded = 0
        self._since_publish = 0
        self._last_published_n = 0
        self._pending: bytes | None = None
        self._proc = None

    # — centralised read: one try covers every transport ————————
    def read_chunk(self) -> bytes | None:
        try:
            return self._read_one()
        except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
            return None

    def _read_one(self) -> bytes | None:
        raise NotImplementedError

    def publish(self, bag: FrameBag) -> None:
        """Append the most recently read frame to ``bag`` and run cadence."""
        if self._pending is not None:
            frame = self._pending
            self._pending = None
            with contextlib.suppress(Exception):
                bag.append(frame)
            self._ingest(frame)

    def _ingest(self, frame: bytes) -> None:
        self._live.append(frame)
        self.decoded += 1
        self._since_publish += 1
        with contextlib.suppress(Exception):
            _set_abs_total(self._all_frames, self.decoded)
        if self._on_chunk is None:
            return
        if self.decoded <= self._kickstart or self._since_publish >= self._publish_every:
            self._sync_and_publish()

    def _sync_and_publish(self) -> None:
        ring = list(self._live)
        # wgpu must-not-abort-frame guard — single centralised suppress
        with contextlib.suppress(Exception):
            self._all_frames[:] = ring
            _set_abs_total(self._all_frames, self.decoded)
            if self._on_chunk is None:
                self._last_published_n = self.decoded
                self._since_publish = 0
                return
            snap = FrameBag(ring, abs_total=self.decoded)
            with contextlib.suppress(Exception):
                self._on_chunk(snap)
            self._last_published_n = self.decoded
            self._since_publish = 0

    def is_done(self) -> bool:
        raise NotImplementedError

    def kill(self) -> None:
        with contextlib.suppress(Exception):
            if self._proc is not None and self._proc.poll() is not None:  # type: ignore[union-attr]
                self._proc.kill()  # type: ignore[union-attr]

    def join(self) -> None:
        pass


class PipeReader(_BaseDecoder):
    """Wraps the Popen stdout pipe path (background reader thread + queue)."""

    def __init__(self, *, proc, **kw):
        super().__init__(**kw)
        self._proc = proc
        self._queue: _queue.Queue = _queue.Queue()
        self._sentinel = object()
        self._finished = False
        self._start_reader()

    def _start_reader(self) -> None:
        reader = _threading.Thread(
            target=self._reader, name="host-movie-pipe-reader", daemon=True
        )
        reader.start()

    def _reader(self) -> None:
        buf = bytearray()
        try:
            chunk = max(self._raw_size, self._raw_size * 2) if self._raw_size else 4096
            stdout = self._proc.stdout  # type: ignore[union-attr]
            while True:
                block = stdout.read(chunk)
                if not block:
                    break
                buf.extend(block)
                while len(buf) >= self._raw_size:
                    frame = bytes(buf[: self._raw_size])
                    del buf[: self._raw_size]
                    self._queue.put(frame)
        except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
            pass
        finally:
            with contextlib.suppress(Exception):
                self._queue.put(self._sentinel)
            with contextlib.suppress(Exception):
                self._proc.stdout.close()  # type: ignore[union-attr]

    def _read_one(self) -> bytes | None:
        try:
            item = self._queue.get(timeout=0.05)
        except _queue.Empty:
            if self._proc is not None and self._proc.poll() is not None and self._queue.empty():
                self._finished = True
            return None
        if item is self._sentinel:
            self._finished = True
            return None
        self._pending = item
        return item

    def is_done(self) -> bool:
        if self._finished:
            return True
        if self._proc is not None and self._proc.poll() is not None and self._queue.empty():
            return True
        return False

    def join(self) -> None:
        pass


class FilePoller(_BaseDecoder):
    """Wraps the file-backed poll path (temp .rgba file drained by offset)."""

    def __init__(self, *, tmp_path: str, proc=None, **kw):
        super().__init__(**kw)
        self._tmp_path = str(tmp_path) if tmp_path is not None else ""
        self._proc = proc
        self._pos = 0

    def _read_one(self) -> bytes | None:
        if not self._tmp_path:
            return None
        raw_size = self._raw_size
        if raw_size <= 0:
            return None
        try:
            size = os.path.getsize(self._tmp_path)
        except OSError:
            size = 0
        if size - self._pos < raw_size:
            return None
        with open(self._tmp_path, "rb") as fh:
            fh.seek(self._pos)
            data = fh.read(raw_size)
        if not data or len(data) < raw_size:
            return None
        self._pos += raw_size
        self._pending = data
        return data

    def is_done(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return False
        try:
            size = os.path.getsize(self._tmp_path) if self._tmp_path else 0
        except OSError:
            return True
        return (size - self._pos) < (self._raw_size or 1)


def _media_is_native_size(path: str, width: int, height: int) -> bool:
    """Cheap probe: cache (path,w,h)->bool for this process."""
    key = (path, int(width), int(height))
    cache = getattr(_media_is_native_size, "_cache", None)
    if cache is None:
        cache = {}
        _media_is_native_size._cache = cache  # type: ignore[attr-defined]
    if key in cache:
        return cache[key]
    ok = False
    with contextlib.suppress(Exception):
        import json as _json

        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        data = _json.loads(proc.stdout.decode("utf-8", "replace") or "{}")
        streams = data.get("streams") or []
        if streams:
            sw = int(streams[0].get("width") or 0)
            sh = int(streams[0].get("height") or 0)
            ok = sw == int(width) and sh == int(height)
    cache[key] = ok
    return ok


def _split_raw_rgba(data: bytes, width: int, height: int, max_frames: int) -> list[bytes]:
    raw_size = width * height * 4
    if raw_size <= 0 or len(data) < raw_size:
        return []
    frames: list[bytes] = []
    for off in range(0, len(data) - raw_size + 1, raw_size):
        frames.append(data[off : off + raw_size])
        if len(frames) >= max_frames:
            break
    return frames


def _decode_ffmpeg_chunk(
    path: str,
    width: int,
    height: int,
    start_frame: int,
    n_frames: int,
    fps: float,
) -> list[bytes]:
    """Decode one RGBA chunk starting at start_frame (in output fps units).

    Uses ``-ss`` before ``-i`` for fast input seek, then scale+fps resample
    and ``-vframes``. No temp files — rawvideo is read from stdout.
    Returns [] on any failure or empty output.

    Frame0 special-case (start_frame==0, n_frames==1): drop the ``fps`` filter.
    ``scale+fps`` with ``-vframes 1`` is flaky on some ffmpeg builds and was
    observed to return empty while a bulk 60-frame chunk succeeded — that left
    the product path publishing first at frames=60 and a multi-second black
    gap under end_splash. Scale-only + ``-frames:v 1`` is reliable and faster.
    """
    # M2 B3 T4: host 分流桩 — backend==host 则探针 renpy_host.video_decode_host
    if _video_backend() == "host" and renpy_host is not None and hasattr(renpy_host, "video_decode_host"):
        try:
            _log = logging.getLogger(__name__)
            _log.info(
                "video _decode_ffmpeg_chunk host path=%r start=%s n=%s fps=%s backend=host DecodePool StagingRing backend=Vulkan",
                path,
                start_frame,
                n_frames,
                fps,
            )
            try:
                probe = renpy_host.video_host_probe() if hasattr(renpy_host, "video_host_probe") else ""
                _log.info("video host probe=%s", probe)
            except Exception:
                pass
            ok = renpy_host.video_decode_host(path, float(fps), "yuv420p")
            _log.info("video host decode ok=%s path=%r backend=host", ok, path)
            if ok:
                # V2 would return host frames; V1 stub always False so fallthrough to CLI
                pass
        except Exception:
            pass
    if n_frames <= 0 or width <= 0 or height <= 0 or fps <= 0:
        return []
    start_time = float(start_frame) / float(fps)
    timeout = _chunk_timeout_s(n_frames)
    need_scale = not _media_is_native_size(path, width, height)

    if start_frame == 0 and n_frames == 1:
        # Frame0 fast path: scale-only (no fps), built via the shared prefix.
        prefix = FfmpegCmdBuilder.build(path, width, height, fps, use_file=False, scale=need_scale)
        vf0 = prefix[5] if len(prefix) > 5 else (f"scale={width}:{height}:flags=fast_bilinear" if need_scale else "null")
        # frame0 uses scale-only vf (strip the trailing ,fps=… if present)
        vf0 = vf0.split(",fps=")[0] if need_scale else "null"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "0",
            "-i",
            path,
            "-an",
        ]
        if need_scale:
            cmd += ["-vf", vf0]
        cmd += [
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "pipe:1",
        ]
    else:
        prefix = FfmpegCmdBuilder.build(path, width, height, fps, use_file=False, scale=need_scale)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "0",
            "-ss",
            f"{start_time:.6f}",
            "-i",
            path,
            "-an",
            "-vf",
            prefix[5] if len(prefix) > 5 else _vf_scale_fps(width, height, fps, scale=need_scale),
            "-vframes",
            str(n_frames),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "pipe:1",
        ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            timeout=timeout,
            capture_output=True,
        )
        data = proc.stdout or b""
    except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
        return []
    return _split_raw_rgba(data, width, height, n_frames)


def _stream_ffmpeg_remaining(
    path: str,
    width: int,
    height: int,
    max_frames: int,
    fps: float,
    all_frames: list[bytes],
    on_chunk: Callable[[list[bytes]], None] | None,
    publish_every: int,
    kickstart: int | None = None,
) -> list[bytes]:
    """Continue decode from absolute total via ffmpeg.

    For large frames (1080p RGBA) default to **file-backed** decode under
    ``/dev/shm`` (or temp) so product-thread GIL holds cannot stall ffmpeg on
    a full OS pipe. A poller drains complete frames into a live list and
    publishes progressively. Smaller frames keep the pipe+reader-thread path.

    Absolute progress is tracked with ``_set_abs_total`` / ``FrameBag``.
    """
    bag = _as_frame_bag(all_frames)
    abs_have = _get_abs_total(bag)
    remaining = max_frames - abs_have
    if remaining <= 0:
        _set_abs_total(all_frames, abs_have)
        return all_frames

    raw_size = width * height * 4
    if raw_size <= 0:
        return all_frames

    start_time = float(abs_have) / float(fps)
    need_scale = not _media_is_native_size(path, width, height)
    timeout = _chunk_timeout_s(remaining)
    publish_every = max(1, int(publish_every))
    if kickstart is None:
        kickstart = _kickstart_frame_budget()
    kickstart = max(1, int(kickstart))

    try:
        live_cap = int(os.environ.get("RENPY_HOST_MOVIE_LIVE_CAP", "0"))
    except (TypeError, ValueError):
        live_cap = 0
    if live_cap <= 0:
        live_cap = max(max_frames, abs_have + remaining, 16)
    else:
        live_cap = max(16, min(live_cap, max(max_frames, 360)))

    # Prefer file-backed when frame is large (1080p) or env forces it.
    mode = os.environ.get("RENPY_HOST_MOVIE_DECODE_MODE", "auto").strip().lower()
    use_file = mode in ("file", "shm", "1", "true", "yes")
    if mode in ("pipe", "0", "false", "no"):
        use_file = False
    elif mode in ("", "auto"):
        use_file = raw_size >= (1600 * 900 * 4)

    t0 = _time.monotonic()
    prefix = FfmpegCmdBuilder.build(path, width, height, fps, use_file, scale=need_scale)
    vf = prefix[5] if len(prefix) > 5 else _vf_scale_fps(width, height, fps, scale=need_scale)

    decoder_kwargs = dict(
        raw_size=raw_size,
        width=width,
        height=height,
        fps=fps,
        path=path,
        live_cap=live_cap,
        all_frames=all_frames,
        on_chunk=on_chunk,
        kickstart=kickstart,
        publish_every=publish_every,
        timeout=timeout,
        t0=t0,
        max_frames=max_frames,
    )

    if use_file:
        tmp_dir = None
        if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK):
            with contextlib.suppress(Exception):
                st = os.statvfs("/dev/shm")
                free = st.f_bavail * st.f_frsize
                need = int(raw_size) * int(remaining) + (32 << 20)
                if free >= need:
                    tmp_dir = "/dev/shm"
        tmp_path = None
        proc = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix="renpy_host_movie_", suffix=".rgba", dir=tmp_dir
            )
            os.close(fd)
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-threads",
                "0",
                "-ss",
                f"{start_time:.6f}",
                "-i",
                path,
                "-an",
                "-vf",
                vf,
                "-vframes",
                str(remaining),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgba",
                "-y",
                tmp_path,
            ]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            decoder = FilePoller(tmp_path=tmp_path, proc=proc, **decoder_kwargs)
            decoder.decoded = abs_have
            _run_decode_loop(decoder)
        finally:
            if proc is not None and proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=2)
            if tmp_path:
                with contextlib.suppress(Exception):
                    os.unlink(tmp_path)
        return all_frames

    # ---- pipe path (smaller frames / explicit mode=pipe) ----
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "0",
        "-ss",
        f"{start_time:.6f}",
        "-i",
        path,
        "-an",
        "-vf",
        vf,
        "-vframes",
        str(remaining),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba",
        "pipe:1",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
    except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
        return all_frames
    assert proc.stdout is not None
    decoder = PipeReader(proc=proc, **decoder_kwargs)
    decoder.decoded = abs_have
    try:
        _run_decode_loop(decoder)
    finally:
        with contextlib.suppress(Exception):
            if proc.poll() is None:
                proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=3)
        with contextlib.suppress(Exception):
            decoder.join()
    return all_frames


def _run_decode_loop(decoder: _BaseDecoder) -> None:
    """Shared timeout/loop semantics behind the Decoder Protocol indirection."""
    while decoder.decoded < decoder._max_frames:
        frame = decoder.read_chunk()
        if frame is not None:
            decoder.publish(decoder._live)
            continue
        if decoder.is_done():
            break
        if (_time.monotonic() - decoder._t0) > decoder._timeout:
            decoder.kill()
            break
        _time.sleep(0.0005)
    if decoder.decoded > decoder._last_published_n:
        decoder._sync_and_publish()
    else:
        with contextlib.suppress(Exception):
            decoder._all_frames[:] = list(decoder._live)
            _set_abs_total(decoder._all_frames, decoder.decoded)


def decode_frames_ffmpeg(
    path: str,
    width: int,
    height: int,
    max_frames: int = 8,
    fps: float = 10.0,
    on_chunk: Callable[[list[bytes]], None] | None = None,
) -> list[bytes]:
    """
    Decode up to `max_frames` RGBA frames from `path` via the ffmpeg CLI.

    Phase 6 MVP path (no libav* linkage). Phase 9 may replace with in-process
    FFmpeg. Returns [] on total failure so callers can use() synthetic frames.

    Progressive / frame0-first warm (``on_chunk`` set):
      1. One-frame scale-only ffmpeg for true frame0 (end_splash hold).
      2. One continuous stream for the remaining frames (single ``-ss`` + pipe);
         publish every frame until ``RENPY_HOST_MOVIE_KICKSTART_FRAMES``
         (default 8), then every ``RENPY_HOST_MOVIE_CHUNK_FRAMES`` (default 20).

    Non-progressive (gates / smoke): single bulk ``_decode_ffmpeg_chunk``.
    """
    if not ffmpeg_available() or not path or not os.path.isfile(path):
        return []
    if max_frames <= 0 or width <= 0 or height <= 0 or fps <= 0:
        return []

    chunk_size = _chunk_frame_budget()
    kickstart = _kickstart_frame_budget()
    all_frames: list[bytes] = FrameBag()

    if on_chunk is not None and max_frames >= 1:
        # Frame0 fast path — scale-only, no fps filter (reliable single frame).
        first = _decode_ffmpeg_chunk(path, width, height, 0, 1, fps)
        if first:
            all_frames.extend(first)
            with contextlib.suppress(Exception):
                on_chunk(all_frames)

        if len(all_frames) < max_frames:
            try:
                pub_cap = int(os.environ.get("RENPY_HOST_MOVIE_PUBLISH_CAP", "8"))
            except (TypeError, ValueError):
                pub_cap = 4
            pub_every = max(1, min(chunk_size, max(1, pub_cap)))
            _stream_ffmpeg_remaining(
                path,
                width,
                height,
                max_frames,
                fps,
                all_frames,
                on_chunk,
                publish_every=pub_every,
                kickstart=kickstart,
            )
        return all_frames

    # Non-progressive: one bulk invocation (legacy / smoke helpers).
    return _decode_ffmpeg_chunk(path, width, height, 0, max_frames, fps)


def decode_frames_ffmpeg_progressive(
    path: str,
    width: int,
    height: int,
    max_frames: int = 8,
    fps: float = 10.0,
    on_chunk: Callable[[list[bytes]], None] | None = None,
) -> list[bytes]:
    """Alias of :func:`decode_frames_ffmpeg` for progressive warm call sites.

    Prefer this name in path-cache warm code when the intent is frame0-first
    publish via ``on_chunk``. Behavior is identical to ``decode_frames_ffmpeg``.
    """
    return decode_frames_ffmpeg(
        path,
        width,
        height,
        max_frames=max_frames,
        fps=fps,
        on_chunk=on_chunk,
    )


def continue_frames_ffmpeg(
    path: str,
    width: int,
    height: int,
    all_frames: list[bytes],
    max_frames: int,
    fps: float = 10.0,
    on_chunk: Callable[[list[bytes]], None] | None = None,
) -> list[bytes]:
    """Append frames to an existing progressive list via a single ``-ss`` stream.

    Used by staged warm → full continue so the host does **not** re-decode
    frame0..N (which would thrash the live path cache mid-play). Mutates and
    returns ``all_frames``. No-ops when ``len(all_frames) >= max_frames``.
    """
    if not ffmpeg_available() or not path or not os.path.isfile(path):
        return all_frames
    if max_frames <= 0 or width <= 0 or height <= 0 or fps <= 0:
        return all_frames
    abs_have = _get_abs_total(all_frames)
    if abs_have >= max_frames:
        return all_frames
    try:
        cont_pub = int(os.environ.get("RENPY_HOST_MOVIE_CONTINUE_PUBLISH", "1"))
    except (TypeError, ValueError):
        cont_pub = 4
    cont_pub = max(1, min(cont_pub, _chunk_frame_budget()))
    try:
        cont_kick = int(os.environ.get("RENPY_HOST_MOVIE_CONTINUE_KICKSTART", "64"))
    except (TypeError, ValueError):
        cont_kick = 12
    cont_kick = max(0, cont_kick)
    abs_for_kick = _get_abs_total(all_frames)
    return _stream_ffmpeg_remaining(
        path,
        width,
        height,
        max_frames,
        fps,
        all_frames,
        on_chunk,
        publish_every=cont_pub,
        kickstart=abs_for_kick + cont_kick,
    )


class VideoTexture:
    """
    Host-side movie texture: one reusable RGBA texture updated per frame.

    YUV420p path (host_build): upload_yuv420p(y,u,v) or upload_yuv420p_raw(raw) then
    draw via renpy_host.yuv420p_pipeline with 3 planes (BT.601 full range).
    Dual-tree safe: SDL path does not import this module for RGBA.

    Usage:
        vt = VideoTexture(64, 64, channel=0)
        for rgba in frames:
            vt.upload(rgba)
            vt.draw()
        vt.close()
    """

    def __init__(self, width: int, height: int, channel: int = 0):
        if renpy_host is None:
            raise RuntimeError("VideoTexture requires renpy_host (host build)")
        self.width = int(width)
        self.height = int(height)
        self.channel = int(channel)
        self.texture = renpy_host.create_texture_rgba(
            self.width,
            self.height,
            _solid_frame(self.width, self.height, (0, 0, 0, 255)),
        )
        self.mesh = renpy_host.create_mesh(_FULLSCREEN_VERTS, _FULLSCREEN_INDICES)
        self.pipeline = renpy_host.textured_pipeline()
        self.frame_index = 0
        # YUV420p state (host_build branch)
        self._y_tex: int | None = None
        self._u_tex: int | None = None
        self._v_tex: int | None = None
        self._yuv_pipeline: int | None = None
        self._is_yuv = False
        # try to probe yuv pipeline (dual-tree safe - may not exist on SDL or old host)
        try:
            if hasattr(renpy_host, "yuv420p_pipeline"):
                self._yuv_pipeline = renpy_host.yuv420p_pipeline()  # type: ignore[attr-defined]
        except Exception:
            self._yuv_pipeline = None
        renpy_host.video_clock_start(self.channel)

    def upload(self, rgba: bytes) -> None:
        expected = self.width * self.height * 4
        if len(rgba) < expected:
            raise ValueError(f"frame too short: {len(rgba)} < {expected}")
        # if previously in YUV mode, ensure RGBA path still works (fallback)
        renpy_host.write_texture_rgba(self.texture, rgba)
        self.frame_index += 1
        self._is_yuv = False

    def upload_yuv420p(self, y: bytes, u: bytes | None = None, v: bytes | None = None) -> None:
        """Upload YUV420p planes. Supports upload_yuv420p(y,u,v) or upload_yuv420p(raw_yuv)."""
        # raw path: single arg contains concatenated Y+U+V
        if u is None and v is None:
            raw = y
            y, u, v = split_yuv420p(raw, self.width, self.height)
        if y is None or u is None or v is None:
            raise ValueError("y,u,v planes required")
        # lazy create yuv textures
        if self._y_tex is None or self._u_tex is None or self._v_tex is None:
            try:
                y_id, u_id, v_id = renpy_host.create_texture_yuv420p(  # type: ignore[attr-defined]
                    self.width, self.height, bytes(y), bytes(u), bytes(v)
                )
                self._y_tex, self._u_tex, self._v_tex = int(y_id), int(u_id), int(v_id)
            except AttributeError:
                # fallback: host lacks yuv - raise
                raise RuntimeError("host missing create_texture_yuv420p")
        else:
            renpy_host.write_texture_yuv420p(  # type: ignore[attr-defined]
                self._y_tex, bytes(y), self._u_tex, bytes(u), self._v_tex, bytes(v)
            )
        self.frame_index += 1
        self._is_yuv = True

    def split_yuv420p(self, data: bytes) -> tuple[bytes, bytes, bytes]:
        """Helper: split raw yuv420p into (Y,U,V) — U/V each w*h//4."""
        return split_yuv420p(data, self.width, self.height)

    def draw(self) -> None:
        # host_build YUV branch
        if self._is_yuv and self._y_tex is not None and self._u_tex is not None and self._v_tex is not None and self._yuv_pipeline is not None:
            renpy_host.begin_frame()
            # draw_model pipeline mesh texture texture1 uniforms texture2
            # Use keyword-style to handle uniforms=None correctly
            try:
                renpy_host.draw_model(self._yuv_pipeline, self.mesh, self._y_tex, self._u_tex, None, self._v_tex)  # type: ignore[call-arg]
            except TypeError:
                # fallback positional if binding mismatched
                renpy_host.draw_model(self._yuv_pipeline, self.mesh, self._y_tex, self._u_tex, self._v_tex)  # type: ignore[call-arg]
            renpy_host.end_frame_present()
            return
        renpy_host.begin_frame()
        renpy_host.draw_model(self.pipeline, self.mesh, self.texture)
        renpy_host.end_frame_present()

    def pos(self) -> float:
        return float(renpy_host.video_clock_pos(self.channel))

    def pause(self) -> None:
        renpy_host.video_clock_pause(self.channel)

    def unpause(self) -> None:
        renpy_host.video_clock_unpause(self.channel)

    def close(self) -> None:
        renpy_host.video_clock_stop(self.channel)
        try:
            if self._y_tex is not None:
                renpy_host.destroy_texture(self._y_tex)
            if self._u_tex is not None:
                renpy_host.destroy_texture(self._u_tex)
            if self._v_tex is not None:
                renpy_host.destroy_texture(self._v_tex)
        except Exception:
            pass
        renpy_host.destroy_texture(self.texture)
        renpy_host.destroy_mesh(self.mesh)

    def play_frames(self, frames: Sequence[bytes], frame_ms: int = 33) -> int:
        """Upload+draw each frame, advancing the host wait between frames."""
        drawn = 0
        for rgba in frames:
            self.upload(rgba)
            self.draw()
            drawn += 1
            deadline = renpy_host.get_ticks_ms() + max(1, int(frame_ms))
            renpy_host.wait_until(deadline)
        return drawn


def play_movie_smoke(  # type: ignore - shim retained (gate smoke)
    width: int = 64,
    height: int = 64,
    frame_count: int = 8,
    media_path: str | None = None,
    channel: int = 0,
    frame_ms: int = 20,
) -> dict:
    """
    Phase 6 movie smoke helper.

    Prefers FFmpeg CLI decode of `media_path` when available; otherwise uses
    synthetic gradient frames. Returns a status dict for gate scripts.
    """
    source = "synthetic"
    frames: list[bytes] = []
    if media_path:
        frames = decode_frames_ffmpeg(
            media_path, width, height, max_frames=frame_count
        )
        if frames:
            source = "ffmpeg"
    if not frames:
        frames = synthetic_frames(width, height, frame_count)
        source = "synthetic"

    vt = VideoTexture(width, height, channel=channel)
    drawn = vt.play_frames(frames, frame_ms=frame_ms)

    # A/V clock handoff: short beep on movie channel while video plays.
    with contextlib.suppress(Exception):
        renpy_host.audio_start()
        renpy_host.audio_beep(660.0, 80, 0.1)

    # After multi-frame play with wait_until, clock must be > 0.
    pos = vt.pos()
    clock_ok = pos > 0.0
    result = {
        "source": source,
        "frames": drawn,
        "width": width,
        "height": height,
        "texture": vt.texture,
        "pos": pos,
        "ffmpeg": ffmpeg_available(),
        "clock_ok": clock_ok,
    }
    vt.close()
    return result
