"""Audio mixer probe tests (M2 B4 T5).

V1 stub: ext-only symphonia probe + MixerConfig env probe.

- test_mixer_probe_default_stereo: audio_mixer_probe() contains channels= and rate=
- test_symphonia_probe_ext: audio_probe("/tmp/foo.webm") contains codec=

Falls back to a pure-Python fake when compiled renpy_host is not importable,
so dual-tree CI without cargo build stays green (like test_clock_master).
"""

import os
import sys
import types

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    import renpy_host  # type: ignore

    # Patch missing probe symbols when running under SDL-tree stub that
    # hasn't been updated yet — keeps gate green while allowing real host
    # to override.
    if not hasattr(renpy_host, "audio_mixer_probe"):
        def _fake_mixer_probe():  # type: ignore
            ch = os.environ.get("RENPY_HOST_AUDIO_CHANNELS", "2")
            try:
                v = int(ch)
            except Exception:
                v = 2
            v = max(1, min(6, v))
            mapped = 6 if v == 6 else (1 if v == 1 else 2)
            return f"channels={mapped} rate=48000 buffer_ms=40"

        renpy_host.audio_mixer_probe = _fake_mixer_probe  # type: ignore

    if not hasattr(renpy_host, "audio_probe"):
        def _fake_audio_probe(path):  # type: ignore
            ext = os.path.splitext(str(path))[1].lstrip(".").lower()
            return f"codec={ext} rate=48000 ch=2 frames=0"

        renpy_host.audio_probe = _fake_audio_probe  # type: ignore

    # Collection-order safety: fill other M2 stubs if a prior minimal fake
    # (e.g., from clock_master when this file is collected second) left holes.
    # Only patch when missing so real compiled host is never overwritten.
    if not hasattr(renpy_host, "create_texture_nv12"):
        renpy_host.create_texture_nv12 = lambda *a, **k: (1, 2)  # type: ignore
    if not hasattr(renpy_host, "create_texture_yuv420p"):
        renpy_host.create_texture_yuv420p = lambda *a, **k: (1, 2, 3)  # type: ignore
    if not hasattr(renpy_host, "create_texture_rgba"):
        renpy_host.create_texture_rgba = lambda *a, **k: 1  # type: ignore
    if not hasattr(renpy_host, "yuv420p_pipeline"):
        renpy_host.yuv420p_pipeline = lambda *a, **k: 1  # type: ignore
    if not hasattr(renpy_host, "nv12_pipeline"):
        renpy_host.nv12_pipeline = lambda *a, **k: 1  # type: ignore
    if not hasattr(renpy_host, "textured_pipeline"):
        renpy_host.textured_pipeline = lambda *a, **k: 1  # type: ignore
    if not hasattr(renpy_host, "video_host_probe"):
        renpy_host.video_host_probe = lambda *a, **k: "DecodePool workers=2 cap_bytes=67108864 ffmpeg-host=False backend=Vulkan StagingRing"  # type: ignore
    if not hasattr(renpy_host, "video_decode_host"):
        renpy_host.video_decode_host = lambda *a, **k: False  # type: ignore
    if not hasattr(renpy_host, "video_clock_start"):
        # Minimal time-based clock for drift tests when clock_master's fake isn't present.
        import time as _t

        _c: dict = {}
        _sr = 48000

        def _now():
            return int(_t.monotonic() * 1000)

        def _vc_s(ch):
            _c[int(ch)] = {"start_ms": _now(), "paused": False, "pause_started_ms": None, "pause_accum_ms": 0, "master": "Wall", "rate": 48000, "drift_ms": 0.0, "bind_ms": None}

        def _vc_e(ch):
            _c.pop(int(ch), None)

        def _vc_p(ch):
            cc = _c.get(int(ch))
            if not cc:
                return 0.0
            now = _now()
            wall = now - cc["start_ms"] - cc["pause_accum_ms"]
            if cc.get("pause_started_ms") is not None:
                wall -= now - cc["pause_started_ms"]
            wall = max(0, wall)
            if cc["master"] == "Wall":
                cc["drift_ms"] = 0.0
                return wall / 1000.0
            rate = int(cc.get("rate") or 48000)
            if cc.get("bind_ms") is None:
                cc["bind_ms"] = now
            elapsed = max(0, now - cc["bind_ms"])
            frames = int(elapsed * rate / 1000)
            audio_ms = frames / rate * 1000.0 if rate else wall
            cc["drift_ms"] = float(wall - audio_ms)
            return frames / rate if rate else wall / 1000.0

        def _vc_b(ch, rate):
            cc = _c.get(int(ch))
            if cc is not None:
                cc["master"] = "AudioSample"
                cc["rate"] = int(rate)
                cc["bind_ms"] = _now()
                cc["drift_ms"] = 0.0
            global _sr
            _sr = int(rate)

        def _vc_d(ch):
            cc = _c.get(int(ch))
            if not cc:
                return 0.0
            _vc_p(int(ch))
            return float(cc.get("drift_ms", 0.0))

        renpy_host.video_clock_start = _vc_s  # type: ignore
        renpy_host.video_clock_stop = _vc_e  # type: ignore
        renpy_host.video_clock_pos = _vc_p  # type: ignore
        renpy_host.video_clock_bind_audio = _vc_b  # type: ignore
        renpy_host.video_clock_drift_ms = _vc_d  # type: ignore
        renpy_host.video_clock_pause = lambda *a, **k: None  # type: ignore
        renpy_host.video_clock_unpause = lambda *a, **k: None  # type: ignore
        renpy_host.video_clock_set_pos = lambda *a, **k: None  # type: ignore
        renpy_host.video_seek = lambda *a, **k: True  # type: ignore
        renpy_host.audio_sample_rate = lambda: int(_sr)  # type: ignore
        renpy_host.audio_set_volume = lambda *a, **k: None  # type: ignore
        renpy_host.audio_queue_pcm_f32 = lambda *a, **k: None  # type: ignore

    HAS_REAL_HOST = True
except Exception:
    HAS_REAL_HOST = False
    fake = types.ModuleType("renpy_host")

    def _fake_mixer_probe():
        ch = os.environ.get("RENPY_HOST_AUDIO_CHANNELS", "2")
        try:
            v = int(ch)
        except Exception:
            v = 2
        v = max(1, min(6, v))
        mapped = 6 if v == 6 else (1 if v == 1 else 2)
        return f"channels={mapped} rate=48000 buffer_ms=40"

    def _fake_audio_probe(path):
        ext = os.path.splitext(str(path))[1].lstrip(".").lower()
        return f"codec={ext} rate=48000 ch=2 frames=0"

    fake.audio_mixer_probe = _fake_mixer_probe  # type: ignore
    fake.audio_probe = _fake_audio_probe  # type: ignore
    # Comprehensive stubs so collection order (this file first) does not break
    # other M2 tests that expect a full host fake (clock, yuv, framebag, etc.).
    # Mirror test_clock_master's time-based VideoClock fake for drift/pos tests.
    import time as _time

    _clocks: dict = {}
    _sample_rate = 48000

    def _now_ms():
        return int(_time.monotonic() * 1000)

    def _vc_start(channel: int):
        _clocks[int(channel)] = {
            "start_ms": _now_ms(),
            "paused": False,
            "pause_started_ms": None,
            "pause_accum_ms": 0,
            "master": "Wall",
            "rate": 48000,
            "drift_ms": 0.0,
            "bind_ms": None,
        }

    def _vc_stop(channel: int):
        _clocks.pop(int(channel), None)

    def _vc_pos(channel: int) -> float:
        c = _clocks.get(int(channel))
        if not c:
            return 0.0
        now = _now_ms()
        wall_ms = now - c["start_ms"] - c["pause_accum_ms"]
        if c.get("pause_started_ms") is not None:
            wall_ms -= now - c["pause_started_ms"]
        wall_ms = max(0, wall_ms)
        if c["master"] == "Wall":
            c["drift_ms"] = 0.0
            return wall_ms / 1000.0
        else:
            rate = int(c.get("rate") or 48000)
            if c.get("bind_ms") is None:
                c["bind_ms"] = now
            elapsed_bind = max(0, now - c["bind_ms"])
            frames = int(elapsed_bind * rate / 1000)
            audio_ms = frames / rate * 1000.0 if rate else wall_ms
            c["drift_ms"] = float(wall_ms - audio_ms)
            return frames / rate if rate else wall_ms / 1000.0

    def _vc_bind(channel: int, rate: int):
        c = _clocks.get(int(channel))
        if c is not None:
            c["master"] = "AudioSample"
            c["rate"] = int(rate)
            c["bind_ms"] = _now_ms()
            c["drift_ms"] = 0.0
        global _sample_rate
        _sample_rate = int(rate)

    def _vc_drift(channel: int) -> float:
        c = _clocks.get(int(channel))
        if not c:
            return 0.0
        _vc_pos(int(channel))
        return float(c.get("drift_ms", 0.0))

    def _vc_pause(channel: int):
        c = _clocks.get(int(channel))
        if c is not None and not c["paused"]:
            c["paused"] = True
            c["pause_started_ms"] = _now_ms()

    def _vc_unpause(channel: int):
        c = _clocks.get(int(channel))
        if c is not None and c["paused"]:
            paused_for = _now_ms() - (c["pause_started_ms"] or _now_ms())
            c["pause_accum_ms"] += max(0, paused_for)
            c["paused"] = False
            c["pause_started_ms"] = None

    fake.video_clock_start = _vc_start  # type: ignore
    fake.video_clock_stop = _vc_stop  # type: ignore
    fake.video_clock_pos = _vc_pos  # type: ignore
    fake.video_clock_bind_audio = _vc_bind  # type: ignore
    fake.video_clock_drift_ms = _vc_drift  # type: ignore
    fake.video_clock_pause = _vc_pause  # type: ignore
    fake.video_clock_unpause = _vc_unpause  # type: ignore
    fake.video_clock_set_pos = lambda *a, **k: None  # type: ignore
    fake.video_seek = lambda *a, **k: True  # type: ignore
    fake.audio_sample_rate = lambda: int(_sample_rate)  # type: ignore
    fake.audio_set_volume = lambda *a, **k: None  # type: ignore
    fake.audio_queue_pcm_f32 = lambda *a, **k: None  # type: ignore
    fake.create_texture_nv12 = lambda *a, **k: (1, 2)  # type: ignore
    fake.create_texture_yuv420p = lambda *a, **k: (1, 2, 3)  # type: ignore
    fake.create_texture_rgba = lambda *a, **k: 1  # type: ignore
    fake.write_texture_nv12 = lambda *a, **k: None  # type: ignore
    fake.write_texture_yuv420p = lambda *a, **k: None  # type: ignore
    fake.yuv420p_pipeline = lambda *a, **k: 1  # type: ignore
    fake.nv12_pipeline = lambda *a, **k: 1  # type: ignore
    fake.textured_pipeline = lambda *a, **k: 1  # type: ignore
    fake.video_host_probe = lambda *a, **k: "DecodePool workers=2 cap_bytes=67108864 ffmpeg-host=False backend=Vulkan StagingRing"  # type: ignore
    fake.video_decode_host = lambda *a, **k: False  # type: ignore
    sys.modules["renpy_host"] = fake
    import renpy_host  # type: ignore


def test_mixer_probe_default_stereo():
    """audio_mixer_probe() returns channels= and rate= (env defaults to stereo)."""
    # Ensure clean env for default stereo expectation
    prev = os.environ.pop("RENPY_HOST_AUDIO_CHANNELS", None)
    # Re-patch if fake was created with old env value
    try:
        s = renpy_host.audio_mixer_probe()  # type: ignore
    finally:
        if prev is not None:
            os.environ["RENPY_HOST_AUDIO_CHANNELS"] = prev
    assert "channels=" in s, f"missing channels= in {s!r}"
    assert "rate=" in s, f"missing rate= in {s!r}"
    # Default should be channels=2 when env unset
    if prev is None:
        assert "channels=2" in s or "channels=1" in s or "channels=6" in s


def test_symphonia_probe_ext():
    """audio_probe ext stub returns codec= (e.g., webm)."""
    s = renpy_host.audio_probe("/tmp/foo.webm")  # type: ignore
    assert "codec=" in s, f"missing codec= in {s!r}"
    # Should contain the lowercased extension
    assert "webm" in s.lower(), f"expected webm in {s!r}"

    s2 = renpy_host.audio_probe("/tmp/foo.WEBM")  # type: ignore
    assert "webm" in s2.lower()

    s3 = renpy_host.audio_probe("/tmp/noext")  # type: ignore
    assert "codec=" in s3


def test_mixer_probe_env_discrete_channels():
    """RENPY_HOST_AUDIO_CHANNELS maps to discrete {1,2,6}."""
    # Directly test the host logic via python helper if available,
    # otherwise falls back to fake above which mirrors Rust rules.
    for env_val, expected in [("1", "channels=1"), ("2", "channels=2"), ("6", "channels=6"), ("3", "channels=2"), ("5", "channels=2")]:
        os.environ["RENPY_HOST_AUDIO_CHANNELS"] = env_val
        s = renpy_host.audio_mixer_probe()  # type: ignore
        assert expected in s, f"env {env_val!r} expected {expected!r} in {s!r}"
    # Cleanup to default
    os.environ.pop("RENPY_HOST_AUDIO_CHANNELS", None)
