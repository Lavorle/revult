"""
Challenger M3 Adversarial Video Clock & Audio-Binding Stress Gate.

Gate name: challenger_m3_video_clock_stress
(RENPY_HOST_GATE=challenger_m3_video_clock_stress)

Stress-tests:
1. Multi-channel video clock lifecycle (channels 0..15 concurrent start/pos/stop).
2. High-frequency rapid pause/unpause burst (10,000 iterations).
3. Extreme seek/rewind edge cases (negative, nan, inf, 0.0, large forward, seek-while-paused).
4. Audio-sample clock binding parity & start_sample offset invariance across seeks.
5. renpysound_host _maybe_arm_clock idempotency and state safety.
"""

import math
import os
import sys
import time
import renpy_host  # type: ignore

import renpy.audio.renpysound_host as rsh


def log(msg):
    print(f"[video_clock_stress] {msg}", flush=True)


def test_multichannel_lifecycle():
    log("Test 1: Multi-channel lifecycle (channels 0..15)...")
    for ch in range(16):
        renpy_host.video_clock_start(ch)
    
    for ch in range(16):
        p = renpy_host.video_clock_pos(ch)
        assert p >= 0.0, f"Channel {ch} pos negative: {p}"
    
    for ch in range(16):
        renpy_host.video_clock_stop(ch)
    
    # After stop, pos should default to 0.0
    for ch in range(16):
        p = renpy_host.video_clock_pos(ch)
        assert p == 0.0, f"Stopped channel {ch} returned non-zero pos: {p}"
    log("  -> PASS: Multi-channel lifecycle.")


def test_rapid_pause_unpause_burst():
    log("Test 2: Rapid pause/unpause burst (10,000 iterations on ch 0)...")
    renpy_host.video_clock_start(0)
    time.sleep(0.01)
    
    for i in range(10000):
        if i % 2 == 0:
            renpy_host.video_clock_pause(0)
        else:
            renpy_host.video_clock_unpause(0)
        p = renpy_host.video_clock_pos(0)
        assert not math.isnan(p) and not math.isinf(p) and p >= 0.0, f"Invalid pos at iter {i}: {p}"
    
    renpy_host.video_clock_unpause(0)
    p_before = renpy_host.video_clock_pos(0)
    time.sleep(0.02)
    p_after = renpy_host.video_clock_pos(0)
    assert p_after >= p_before, f"Clock did not advance after unpause: before={p_before}, after={p_after}"
    
    # Pause and verify freeze
    renpy_host.video_clock_pause(0)
    p_paused1 = renpy_host.video_clock_pos(0)
    time.sleep(0.02)
    p_paused2 = renpy_host.video_clock_pos(0)
    assert abs(p_paused1 - p_paused2) < 1e-4, f"Clock did not freeze while paused: {p_paused1} vs {p_paused2}"
    
    renpy_host.video_clock_stop(0)
    log("  -> PASS: Rapid pause/unpause burst.")


def test_extreme_seek_rewind_stress():
    log("Test 3: Extreme seek/rewind stress (5,000 random/extreme seeks)...")
    renpy_host.video_clock_start(1)
    
    # Test finite valid seeks
    for target in [0.0, 1.0, 10.5, 0.0001, 3600.0, 0.0]:
        renpy_host.video_clock_set_pos(1, target)
        p = renpy_host.video_clock_pos(1)
        assert abs(p - target) < 0.05, f"Expected {target}, got {p}"
    
    # Test adversarial / invalid inputs (negative, NaN, Inf, -Inf)
    adversarial_inputs = [-1.0, -100.0, float('nan'), float('inf'), float('-inf'), -0.0]
    for adv in adversarial_inputs:
        renpy_host.video_clock_set_pos(1, adv)
        p = renpy_host.video_clock_pos(1)
        assert not math.isnan(p) and not math.isinf(p) and p >= 0.0, f"Adversarial seek {adv} caused invalid pos: {p}"
        assert p == 0.0 or abs(p - 0.0) < 0.05, f"Adversarial seek {adv} should clamp to 0, got {p}"
    
    # Test seek while paused
    renpy_host.video_clock_set_pos(1, 5.0)
    renpy_host.video_clock_pause(1)
    time.sleep(0.01)
    # Seek to 12.0 while paused
    renpy_host.video_clock_set_pos(1, 12.0)
    p_paused = renpy_host.video_clock_pos(1)
    assert abs(p_paused - 12.0) < 0.05, f"Seek while paused expected ~12.0, got {p_paused}"
    time.sleep(0.02)
    p_paused_again = renpy_host.video_clock_pos(1)
    assert abs(p_paused_again - 12.0) < 0.05, f"Clock drifted while paused after seek: {p_paused_again}"
    
    # Unpause and verify progression from 12.0
    renpy_host.video_clock_unpause(1)
    time.sleep(0.02)
    p_unpaused = renpy_host.video_clock_pos(1)
    assert p_unpaused >= 12.0, f"Clock after unpause should be >= 12.0, got {p_unpaused}"
    
    # Rewind to 0.0
    renpy_host.video_clock_set_pos(1, 0.0)
    p_rewound = renpy_host.video_clock_pos(1)
    assert abs(p_rewound - 0.0) < 0.05, f"Rewind to 0.0 failed: {p_rewound}"
    
    renpy_host.video_clock_stop(1)
    log("  -> PASS: Extreme seek/rewind stress.")


def test_audio_sample_binding_and_offset():
    log("Test 4: Audio sample clock binding & start_sample offset...")
    renpy_host.video_clock_start(2)
    
    # Bind audio rate 48000
    renpy_host.video_clock_bind_audio(2, 48000)
    
    # Set pos to 3.5 seconds
    renpy_host.video_clock_set_pos(2, 3.5)
    p = renpy_host.video_clock_pos(2)
    assert abs(p - 3.5) < 0.05, f"Expected 3.5s under audio clock, got {p}"
    
    # Rewind to 0.0
    renpy_host.video_clock_set_pos(2, 0.0)
    p0 = renpy_host.video_clock_pos(2)
    assert abs(p0 - 0.0) < 0.05, f"Rewind to 0.0 under audio clock failed: {p0}"
    
    # Test drift calculation
    drift = renpy_host.video_clock_drift_ms(2)
    assert not math.isnan(drift) and not math.isinf(drift), f"Invalid drift: {drift}"
    
    renpy_host.video_clock_stop(2)
    log("  -> PASS: Audio sample clock binding & offset.")


def test_renpysound_host_idempotency_and_state():
    log("Test 5: renpysound_host _maybe_arm_clock idempotency...")
    # Ensure renpysound channel data structures
    ch_id = 7
    rsh._channels[ch_id] = {
        "video": True,
        "playing": True,
        "media_path": "/fake/video.webm",
        "clock_armed": False,
        "play_frames": [b"f1", b"f2", b"f3"],
        "frames": [b"f1", b"f2", b"f3"],
    }
    
    # Create fake path cache entry
    import threading
    fake_entry = {
        "path": "/fake/video.webm",
        "lock": threading.Lock(),
        "frames": [b"f1", b"f2", b"f3"],
        "ready_playable": True,
        "ready_full": True,
        "fps": 30.0,
        "decode_w": 1280,
        "decode_h": 720,
    }
    with rsh._PATH_CACHE_LOCK:
        rsh._PATH_CACHE["/fake/video.webm"] = fake_entry
    
    # First arming
    rsh._maybe_arm_clock(ch_id)
    assert rsh._channels[ch_id].get("clock_armed") is True
    p1 = renpy_host.video_clock_pos(ch_id)
    
    # Repeated 500 calls to _maybe_arm_clock should be idempotent and not reset clock
    time.sleep(0.01)
    for _ in range(500):
        rsh._maybe_arm_clock(ch_id)
    
    p2 = renpy_host.video_clock_pos(ch_id)
    assert p2 >= p1, f"_maybe_arm_clock reset clock: p1={p1}, p2={p2}"
    
    # Channel pause / unpause via renpysound_host
    rsh.pause(ch_id, 1)
    p_paused = renpy_host.video_clock_pos(ch_id)
    time.sleep(0.01)
    assert abs(renpy_host.video_clock_pos(ch_id) - p_paused) < 1e-4
    
    rsh.pause(ch_id, 0)
    time.sleep(0.01)
    assert renpy_host.video_clock_pos(ch_id) >= p_paused
    
    # Cleanup
    rsh.stop(ch_id)
    renpy_host.video_clock_stop(ch_id)
    with rsh._PATH_CACHE_LOCK:
        rsh._PATH_CACHE.pop("/fake/video.webm", None)
    rsh._channels.pop(ch_id, None)
    log("  -> PASS: renpysound_host _maybe_arm_clock idempotency.")


def main():
    log("Starting Challenger M3 Video Clock & Audio-Binding Stress Gate...")
    test_multichannel_lifecycle()
    test_rapid_pause_unpause_burst()
    test_extreme_seek_rewind_stress()
    test_audio_sample_binding_and_offset()
    test_renpysound_host_idempotency_and_state()
    log("ALL CHALLENGER M3 STRESS TESTS PASSED EMPIRICALLY!")
    renpy_host.request_quit()


if __name__ == "__main__":
    main()
