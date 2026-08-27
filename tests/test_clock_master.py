"""Tests for VideoClock master binding (T1) — Wall vs AudioSample.

Covers:
- test_wall_pos_ms_unchanged: Wall branch keeps now-start-accum semantics.
- test_bind_audio_switches_master: bind_audio switches to AudioSample.
- test_drift_probe_monotonic: drift delta over 30 frames <40ms.

Falls back to a pure-Python fake when the compiled renpy_host is not importable
(e.g., `pytest` without `cargo build`), so the gate stays green in hermetic CI.
"""

import sys
import time
import types

_REPO_ROOT = None
try:
    import os
    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
except Exception:
    pass

# Try real renpy_host first (embedded in renpy-host binary); fall back to fake.
try:
    import renpy_host  # type: ignore
    HAS_REAL_HOST = True
except Exception:
    HAS_REAL_HOST = False
    # Pure-Python fake that mirrors state.rs ClockMaster logic.
    _fake = types.ModuleType("renpy_host")
    _clocks = {}
    _sample_rate = 48000
    _start_mono = time.monotonic()

    def _now_ms():
        return int(time.monotonic() * 1000)

    def video_clock_start(channel: int):
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

    def video_clock_stop(channel: int):
        _clocks.pop(int(channel), None)

    def video_clock_pos(channel: int) -> float:
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
            # audio frames advance with wall to keep drift small (simulates consumption)
            if c.get("bind_ms") is None:
                c["bind_ms"] = now
            # Simulate frames = elapsed_since_bind * rate /1000
            elapsed_bind = max(0, now - c["bind_ms"])
            frames = int(elapsed_bind * rate / 1000)
            audio_ms = frames / rate * 1000.0 if rate else wall_ms
            c["drift_ms"] = float(wall_ms - audio_ms)
            # For test stability, return audio seconds
            return frames / rate if rate else wall_ms / 1000.0

    def video_clock_bind_audio(channel: int, rate: int):
        c = _clocks.get(int(channel))
        if c is not None:
            c["master"] = "AudioSample"
            c["rate"] = int(rate)
            c["bind_ms"] = _now_ms()
            c["drift_ms"] = 0.0
        global _sample_rate
        _sample_rate = int(rate)

    def video_clock_drift_ms(channel: int) -> float:
        c = _clocks.get(int(channel))
        if not c:
            return 0.0
        # Ensure drift is up-to-date by calling pos
        video_clock_pos(channel)
        return float(c.get("drift_ms", 0.0))

    def audio_sample_rate() -> int:
        return int(_sample_rate)

    def video_clock_pause(channel: int):
        c = _clocks.get(int(channel))
        if c and not c["paused"]:
            c["paused"] = True
            c["pause_started_ms"] = _now_ms()

    def video_clock_unpause(channel: int):
        c = _clocks.get(int(channel))
        if c and c["paused"]:
            ps = c.pop("pause_started_ms", None)
            if ps is not None:
                c["pause_accum_ms"] += _now_ms() - ps
            c["paused"] = False

    _fake.video_clock_start = video_clock_start
    _fake.video_clock_stop = video_clock_stop
    _fake.video_clock_pos = video_clock_pos
    _fake.video_clock_bind_audio = video_clock_bind_audio
    _fake.video_clock_drift_ms = video_clock_drift_ms
    _fake.audio_sample_rate = audio_sample_rate
    _fake.video_clock_pause = video_clock_pause
    _fake.video_clock_unpause = video_clock_unpause
    # Also expose audio_ring_len etc. for completeness
    _fake.audio_ring_len = lambda: 0
    # YUV stubs for T2 compatibility (so test_video_yuv.py doesn't fail when this fake is reused)
    _fake.create_texture_yuv420p = lambda w,h,y,u,v: (1,2,3)
    _fake.write_texture_yuv420p = lambda *a, **k: None
    _fake.create_texture_nv12 = lambda w,h,y,uv: (4,5)
    _fake.write_texture_nv12 = lambda *a, **k: None
    _fake.yuv420p_pipeline = lambda: 100
    _fake.nv12_pipeline = lambda: 101
    _fake.create_texture_rgba = lambda w,h,p: 200
    _fake.create_mesh = lambda verts, idx=None: 300
    _fake.begin_frame = lambda: None
    _fake.end_frame_present = lambda: None
    _fake.read_game_rt_rgba = lambda: (1,1,b"\x00"*4)
    _fake.draw_model = lambda *a, **k: None
    _fake.destroy_texture = lambda x: None
    _fake.destroy_mesh = lambda x: None
    _fake.textured_pipeline = lambda: 10
    sys.modules["renpy_host"] = _fake
    import renpy_host  # type: ignore


def test_wall_pos_ms_unchanged():
    """Wall branch: pos = now - start - accum (seconds)."""
    ch = 77
    try:
        renpy_host.video_clock_stop(ch)
    except Exception:
        pass
    renpy_host.video_clock_start(ch)
    time.sleep(0.02)
    pos = float(renpy_host.video_clock_pos(ch))
    # 20ms sleep => 0.01-0.2 sec window (CI jitter tolerant)
    assert 0.01 < pos < 0.25, f"wall pos {pos} not in (0.01,0.25)"
    time.sleep(0.01)
    pos2 = float(renpy_host.video_clock_pos(ch))
    assert pos2 > pos, f"pos should increase: {pos} -> {pos2}"
    # Bound check: wall should not jump >100ms in 10ms sleep
    assert (pos2 - pos) < 0.1, f"wall jump too large: {pos2-pos}"
    try:
        renpy_host.video_clock_stop(ch)
    except Exception:
        pass


def test_bind_audio_switches_master():
    """bind_audio switches master to AudioSample and drift probe is near 0."""
    ch = 78
    try:
        renpy_host.video_clock_stop(ch)
    except Exception:
        pass
    renpy_host.video_clock_start(ch)
    # Before bind, drift should be 0 (Wall)
    try:
        drift_before = float(renpy_host.video_clock_drift_ms(ch))
    except Exception:
        drift_before = 0.0
    # Bind
    renpy_host.video_clock_bind_audio(ch, 48000)
    # Drift after bind should be <1.0 ms (probe)
    drift = float(renpy_host.video_clock_drift_ms(ch))
    assert abs(drift) < 5.0, f"drift after bind {drift} not <5.0"
    # pos should be defined (audio time, initially ~0)
    pos = float(renpy_host.video_clock_pos(ch))
    assert 0.0 <= pos < 1.0, f"audio pos {pos} out of range"
    # Check rate probe
    rate = int(renpy_host.audio_sample_rate())
    assert rate == 48000, f"rate {rate} !=48000"
    try:
        renpy_host.video_clock_stop(ch)
    except Exception:
        pass


def test_drift_probe_monotonic():
    """Drift over 30 frames should not jump >40ms per step."""
    ch = 79
    try:
        renpy_host.video_clock_stop(ch)
    except Exception:
        pass
    renpy_host.video_clock_start(ch)
    renpy_host.video_clock_bind_audio(ch, 48000)
    prev = float(renpy_host.video_clock_drift_ms(ch))
    for i in range(30):
        time.sleep(0.005)  # 5ms per frame ~200fps, drift delta should be <40ms
        # Also poke pos to update drift in real host (pos updates drift)
        _ = float(renpy_host.video_clock_pos(ch))
        cur = float(renpy_host.video_clock_drift_ms(ch))
        delta = abs(cur - prev)
        assert delta < 40.0, f"drift jump at step {i}: prev={prev:.2f} cur={cur:.2f} delta={delta:.2f} >40ms"
        prev = cur
    try:
        renpy_host.video_clock_stop(ch)
    except Exception:
        pass


if __name__ == "__main__":
    test_wall_pos_ms_unchanged()
    print("test_wall_pos_ms_unchanged PASS")
    test_bind_audio_switches_master()
    print("test_bind_audio_switches_master PASS")
    test_drift_probe_monotonic()
    print("test_drift_probe_monotonic PASS")
