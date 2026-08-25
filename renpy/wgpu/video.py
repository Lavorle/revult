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

import os
import shutil
import subprocess
from collections import deque
from collections.abc import Callable, Sequence


class FrameBag(list):
    """RGBA frame list that can carry absolute decode counters.

    Built-in ``list`` rejects arbitrary attributes on some Python builds; a
    thin subclass keeps ``_abs_total`` reliable after ring trims.
    """

    __slots__ = ("_abs_total",)

    def __init__(self, iterable=(), *, abs_total: int = 0):
        super().__init__(iterable)
        self._abs_total = int(abs_total or 0)


def _as_frame_bag(frames) -> FrameBag:
    if isinstance(frames, FrameBag):
        return frames
    try:
        abs_total = int(getattr(frames, "_abs_total", 0) or 0)
    except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
        abs_total = 0
    bag = FrameBag(frames or (), abs_total=abs_total if abs_total > 0 else len(frames or ()))
    return bag


def _set_abs_total(frames, n: int) -> None:
    n = int(n)
    try:
        frames._abs_total = n  # type: ignore[attr-defined]
        return
    except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
        pass
    try:
        frames._abs_total = n
    except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
        pass


def _get_abs_total(frames) -> int:
    try:
        n = int(getattr(frames, "_abs_total", 0) or 0)
    except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
        n = 0
    if n > 0:
        return n
    return len(frames) if frames is not None else 0


try:
    import renpy_host  # type: ignore
except ImportError:  # pragma: no cover - SDL tree
    renpy_host = None  # type: ignore


# Full-screen NDC quad: x,y,u,v,r,g,b,a
_FULLSCREEN_VERTS = [
    -1.0, -1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0,
     1.0, -1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
     1.0,  1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0,
    -1.0,  1.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0,
]
_FULLSCREEN_INDICES = [0, 1, 2, 0, 2, 3]

# Env: RENPY_HOST_MOVIE_CHUNK_FRAMES — progressive publish interval after kickstart.
# Per-chunk wall timeout is max(30, 0.15 * n_frames) seconds so 360@30
# product decodes finish without a single 30s hard wall.
_DEFAULT_CHUNK_FRAMES = 20
_DEFAULT_KICKSTART_FRAMES = 8
_CHUNK_TIMEOUT_FLOOR_S = 30.0
_CHUNK_TIMEOUT_PER_FRAME_S = 0.15


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


def _media_is_native_size(path: str, width: int, height: int) -> bool:
    """Cheap probe: cache (path,w,h)->bool for this process."""
    key = (path, int(width), int(height))
    cache = getattr(_media_is_native_size, "_cache", None)
    if cache is None:
        cache = {}
        _media_is_native_size._cache = cache
    if key in cache:
        return cache[key]
    ok = False
    try:
        # Prefer ffprobe when available; fail open to scale.
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
    except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
        ok = False
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
    if n_frames <= 0 or width <= 0 or height <= 0 or fps <= 0:
        return []
    start_time = float(start_frame) / float(fps)
    timeout = _chunk_timeout_s(n_frames)
    need_scale = not _media_is_native_size(path, width, height)

    # Frame0 fast path: no fps resample (avoids empty single-frame outputs).
    if start_frame == 0 and n_frames == 1:
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
            cmd += [
                "-vf",
                f"scale={width}:{height}:flags=fast_bilinear",
            ]
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
            _vf_scale_fps(width, height, fps, scale=need_scale),
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
    import queue as _queue
    import tempfile
    import threading as _threading
    import time as _time

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
        # 1080p RGBA is 8 MiB/frame — pipe backpressure under product GIL is the
        # dominant 4-5s sticky class. File-backed keeps ffmpeg free.
        use_file = raw_size >= (1600 * 900 * 4)

    vf = _vf_scale_fps(width, height, fps, scale=need_scale)
    live: deque = deque(bag, maxlen=live_cap)
    decoded = abs_have
    last_published_n = decoded
    since_publish = 0
    t0 = _time.monotonic()

    def _sync_and_publish() -> None:
        nonlocal last_published_n, since_publish
        ring_list = list(live)
        try:
            all_frames[:] = ring_list
        except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
            try:
                all_frames.clear()
                all_frames.extend(ring_list)
            except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                pass
        _set_abs_total(all_frames, decoded)
        if on_chunk is None:
            last_published_n = decoded
            since_publish = 0
            return
        snap = FrameBag(ring_list, abs_total=decoded)
        try:
            on_chunk(snap)
        except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
            pass
        last_published_n = decoded
        since_publish = 0

    def _ingest(frame: bytes) -> None:
        nonlocal decoded, since_publish
        live.append(frame)
        decoded += 1
        since_publish += 1
        _set_abs_total(all_frames, decoded)
        if on_chunk is None:
            return
        if decoded <= kickstart or since_publish >= publish_every:
            _sync_and_publish()

    if use_file:
        tmp_dir = None
        if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK):
            # Need headroom for remaining frames.
            try:
                st = os.statvfs("/dev/shm")
                free = st.f_bavail * st.f_frsize
                need = int(raw_size) * int(remaining) + (32 << 20)
                if free >= need:
                    tmp_dir = "/dev/shm"
            except Exception:  # noqa: BLE001 -- media/host best-effort — failure must not block playback or crash frame
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
            consumed = 0
            with open(tmp_path, "rb") as fh:
                while decoded < max_frames:
                    try:
                        size = os.path.getsize(tmp_path)
                    except OSError:
                        size = 0
                    # Drain all complete new frames.
                    while size - consumed >= raw_size and decoded < max_frames:
                        if fh.tell() != consumed:
                            fh.seek(consumed)
                        data = fh.read(raw_size)
                        if not data or len(data) < raw_size:
                            break
                        consumed += raw_size
                        _ingest(data)
                    if proc.poll() is not None:
                        try:
                            size = os.path.getsize(tmp_path)
                        except OSError:
                            size = 0
                        if size - consumed < raw_size:
                            break
                        continue
                    if (_time.monotonic() - t0) > timeout:
                        try:
                            proc.kill()
                        except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                            pass
                        break
                    # Free GIL for product present thread.
                    _time.sleep(0.0005)
            if decoded > last_published_n:
                _sync_and_publish()
            else:
                try:
                    all_frames[:] = list(live)
                except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                    pass
                _set_abs_total(all_frames, decoded)
        finally:
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                    pass
                try:
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                    pass
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                    pass
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
    try:
        qmax = int(os.environ.get("RENPY_HOST_MOVIE_PIPE_QUEUE", "180"))
    except (TypeError, ValueError):
        qmax = 180
    qmax = max(16, min(qmax, 360))
    frame_q: _queue.Queue = _queue.Queue(maxsize=qmax)
    sentinel = object()

    def _reader() -> None:
        buf = bytearray()
        try:
            chunk = max(raw_size, raw_size * 2)
            while True:
                block = proc.stdout.read(chunk)
                if not block:
                    break
                buf.extend(block)
                while len(buf) >= raw_size:
                    frame = bytes(buf[:raw_size])
                    del buf[:raw_size]
                    frame_q.put(frame)
        except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
            pass
        finally:
            try:
                frame_q.put(sentinel)
            except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                pass
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                pass

    reader = _threading.Thread(
        target=_reader, name="host-movie-pipe-reader", daemon=True
    )
    reader.start()
    try:
        while decoded < max_frames:
            try:
                item = frame_q.get(timeout=0.5)
            except _queue.Empty:
                if proc.poll() is not None and frame_q.empty():
                    break
                if (_time.monotonic() - t0) > timeout:
                    break
                continue
            if item is sentinel:
                break
            _ingest(item)
        if decoded > last_published_n:
            _sync_and_publish()
        else:
            try:
                all_frames[:] = list(live)
            except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                pass
            _set_abs_total(all_frames, decoded)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
            pass
        try:
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
            pass
        try:
            reader.join(timeout=2)
        except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
            pass
    return all_frames



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
    FFmpeg. Returns [] on total failure so callers can use synthetic frames.

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
            try:
                on_chunk(all_frames)
            except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
                pass

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
    # Continue publish cadence: env CHUNK may be 90 for warm thrash
    # control, but that freezes read_video for ~8s between publishes at
    # 1080p. Cap continue publish interval so the path cache grows at a
    # human-visible rate even when CHUNK is large.
    try:
        cont_pub = int(os.environ.get("RENPY_HOST_MOVIE_CONTINUE_PUBLISH", "1"))
    except (TypeError, ValueError):
        cont_pub = 4
    cont_pub = max(1, min(cont_pub, _chunk_frame_budget()))
    # Keep a short kickstart window on continue so the first new frames
    # after warm appear immediately (breaks the warm-boundary freeze).
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
        renpy_host.video_clock_start(self.channel)

    def upload(self, rgba: bytes) -> None:
        expected = self.width * self.height * 4
        if len(rgba) < expected:
            raise ValueError(f"frame too short: {len(rgba)} < {expected}")
        renpy_host.write_texture_rgba(self.texture, rgba)
        self.frame_index += 1

    def draw(self) -> None:
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
    try:
        renpy_host.audio_start()
        renpy_host.audio_beep(660.0, 80, 0.1)
    except Exception:  # noqa: BLE001, S110 -- media/host best-effort — failure must not block playback or crash frame
        pass

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
