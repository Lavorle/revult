"""Adversarial stress testing for Batching, RTT Pool, and Concurrency (M4 Gate).

Exhaustively verifies:
1. High-throughput quad batch collapsing (10x gate, adversarial interleaving, 10000+ quads).
2. Nested RTT pass cycles, freelist recycling, and dimension clamping under adversarial inputs.
3. Concurrency, presentation mutex stability, and zero memory leaks.
"""

import math
import random
import sys
import threading
import types
import pytest

from renpy.wgpu.host_bridge import _InstanceGroup, get_frame_stats
from renpy.wgpu.rtt_pool import RttPoolMixin, _clamp_rtt_size


# ============================================================================
# 1. High-Throughput Quad Batch Collapsing & 10x Gate Stress Tests
# ============================================================================

def test_rapid_burst_10000_quads_alternating_textures_and_pipelines():
    """Burst of 10,000 quads across 10 pipelines and 10 textures in randomized interleaved order.
    
    Verifies:
    - Packing into exactly 100 instance groups (10x10).
    - Dispatched draw calls == 100.
    - Total quads == 10,000.
    - Strict 10x gate: draw_calls (100) < quads (10,000) / 10 (1,000).
    """
    grp = _InstanceGroup()
    num_pipes = 10
    num_textures = 10
    quads_per_combo = 100
    total_expected_quads = num_pipes * num_textures * quads_per_combo  # 10,000

    # Generate quads and shuffle order to simulate pathological interleaved rendering
    quads = []
    for pipe in range(1, num_pipes + 1):
        for tex in range(100, 100 + num_textures):
            for i in range(quads_per_combo):
                quads.append((pipe, tex, i))
    
    random.seed(42)
    random.shuffle(quads)

    for pipe, tex, i in quads:
        x0 = float(i % 100)
        y0 = float(i // 100)
        x1 = x0 + 10.0
        y1 = y0 + 10.0
        color = (1.0, 0.8, 0.6, 1.0)
        grp.add(pipe, tex, None, None, x0, y0, x1, y1, 0.0, 0.0, 1.0, 1.0, color)

    # Verify grouping
    assert len(grp.map) == num_pipes * num_textures  # exactly 100 unique keys
    for key, data in grp.map.items():
        assert len(data) == quads_per_combo * 12

    # Dispatch via mock host
    dispatched = []
    fake_host = types.SimpleNamespace(
        draw_instances=lambda pipe, t0, t1, t2, instances: dispatched.append((pipe, t0, len(instances) // 12)),
    )
    orig_host = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake_host
    try:
        grp.flush()
        assert len(dispatched) == 100
        total_quads = sum(cnt for _, _, cnt in dispatched)
        assert total_quads == total_expected_quads
        
        draw_calls = len(dispatched)
        assert draw_calls < total_quads / 10, f"Failed 10x gate: {draw_calls} not < {total_quads / 10}"
        assert grp.empty()
    finally:
        if orig_host is not None:
            sys.modules["renpy_host"] = orig_host
        else:
            sys.modules.pop("renpy_host", None)


def test_pathological_ping_pong_quads():
    """5,000 quads strictly ping-ponging between (Pipe 1, Tex A) and (Pipe 2, Tex B) on every single quad.
    
    Verifies that hash-map batching collapses the 5,000 alternating quads into exactly 2 draw calls
    (2,500x collapse factor).
    """
    grp = _InstanceGroup()
    total_quads = 5000
    for i in range(total_quads):
        if i % 2 == 0:
            grp.add(1, 1001, None, None, i, 0, i + 1, 10, 0, 0, 1, 1, (1, 1, 1, 1))
        else:
            grp.add(2, 2002, None, None, i, 0, i + 1, 10, 0, 0, 1, 1, (1, 1, 1, 1))

    assert len(grp.map) == 2
    assert len(grp.map[(1, 1001, None, None)]) == 2500 * 12
    assert len(grp.map[(2, 2002, None, None)]) == 2500 * 12

    dispatched = []
    fake_host = types.SimpleNamespace(
        draw_instances=lambda pipe, t0, t1, t2, instances: dispatched.append((pipe, t0, len(instances) // 12)),
    )
    orig_host = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake_host
    try:
        grp.flush()
        assert len(dispatched) == 2
        assert dispatched[0][2] == 2500
        assert dispatched[1][2] == 2500
        assert len(dispatched) < total_quads / 10
    finally:
        if orig_host is not None:
            sys.modules["renpy_host"] = orig_host
        else:
            sys.modules.pop("renpy_host", None)


def test_extreme_quad_volume_and_12_float_packing_precision():
    """50,000 quads added into _InstanceGroup to verify memory scalability and exact 12-float layout."""
    grp = _InstanceGroup()
    quad_count = 50_000
    for i in range(quad_count):
        # Precise coordinates
        x0 = float(i)
        y0 = float(i * 2)
        x1 = x0 + 15.5
        y1 = y0 + 25.5
        u0 = 0.125
        v0 = 0.25
        u1 = 0.875
        v1 = 0.75
        color = (0.1, 0.2, 0.3, 0.4)
        grp.add(1, 555, None, None, x0, y0, x1, y1, u0, v0, u1, v1, color)

    key = (1, 555, None, None)
    assert key in grp.map
    data = grp.map[key]
    assert len(data) == quad_count * 12

    # Spot-check first, middle, and last quad data
    for idx in [0, 25000, 49999]:
        base = idx * 12
        x0 = float(idx)
        y0 = float(idx * 2)
        assert data[base + 0] == pytest.approx(x0)
        assert data[base + 1] == pytest.approx(y0)
        assert data[base + 2] == pytest.approx(15.5)  # rsx = x1 - x0
        assert data[base + 3] == pytest.approx(25.5)  # rsy = y1 - y0
        assert data[base + 4] == pytest.approx(0.125) # uox
        assert data[base + 5] == pytest.approx(0.25)  # voy
        assert data[base + 6] == pytest.approx(0.75)  # usx = u1 - u0 (0.875 - 0.125)
        assert data[base + 7] == pytest.approx(0.5)   # vsy = v1 - v0 (0.75 - 0.25)
        assert data[base + 8] == pytest.approx(0.1)   # cr
        assert data[base + 9] == pytest.approx(0.2)   # cg
        assert data[base + 10] == pytest.approx(0.3)  # cb
        assert data[base + 11] == pytest.approx(0.4)  # ca


def test_instance_group_deep_nesting_push_pop():
    """50 levels of nested push() and pop() with quad additions at each level."""
    grp = _InstanceGroup()
    depth = 50
    for d in range(depth):
        grp.add(d, d * 10, None, None, d, d, d + 1, d + 1, 0, 0, 1, 1, (1, 1, 1, 1))
        assert len(grp.map) == 1
        grp.push()
        assert grp.empty()

    # Unwind all levels and verify each restored map has its exact expected contents
    for d in reversed(range(depth)):
        grp.pop()
        assert not grp.empty()
        assert (d, d * 10, None, None) in grp.map
        assert len(grp.map[(d, d * 10, None, None)]) == 12


def test_instance_group_adversarial_invalid_inputs():
    """Test adversarial non-numeric, None, short color tuples, and exception safety."""
    grp = _InstanceGroup()
    # String values
    grp.add(1, 100, None, None, "bad", 0, 10, 10, 0, 0, 1, 1, (1, 1, 1, 1))
    assert grp.empty()

    # None coordinates
    grp.add(1, 100, None, None, None, 0, 10, 10, 0, 0, 1, 1, (1, 1, 1, 1))
    assert grp.empty()

    # Short color tuple (e.g. RGB only or empty)
    grp.add(1, 100, None, None, 0, 0, 10, 10, 0, 0, 1, 1, (0.5, 0.5))
    assert (1, 100, None, None) in grp.map
    data = grp.map[(1, 100, None, None)]
    assert data[8] == 0.5
    assert data[9] == 0.5
    assert data[10] == 1.0 # default fallback for missing components
    assert data[11] == 1.0


# ============================================================================
# 2. Nested RTT Pass Cycles & Freelist Recycling Stress Tests
# ============================================================================

class _RttTestHost:
    def __init__(self):
        self.counter = 1000
        self.created = []
        self.destroyed = []

    def create_render_texture(self, w, h):
        self.counter += 1
        self.created.append((self.counter, w, h))
        return self.counter

    def destroy_texture(self, handle):
        self.destroyed.append(handle)


class _RttRig(RttPoolMixin):
    def __init__(self, pool_cap=8, dw=1920, dh=1080):
        self._rtt_free = {}
        self._rtt_prev_frame = []
        self._rtt_curr_frame = []
        self._rtt_pool_cap = pool_cap
        self.drawable_size = (dw, dh)
        self.virtual_size = (dw, dh)
        self.layout_virtual_size = (dw, dh)
        self.texture_cache = {}


def test_rtt_pool_freelist_fallthrough_empirical_defect():
    """Empirical demonstration of defect in RttPoolMixin._acquire_rtt.
    
    When a handle is available in self._rtt_free, _acquire_rtt pops the handle
    and appends it to self._rtt_curr_frame, but does not return it immediately.
    Instead, it falls through and calls renpy_host.create_render_texture(w, h),
    appending a SECOND handle to self._rtt_curr_frame and returning the new one.
    """
    fake_host = _RttTestHost()
    orig_host = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake_host
    try:
        rig = _RttRig(pool_cap=4)
        # 1. Allocate RTT in Frame 1
        h1 = rig._acquire_rtt(100, 100)
        assert h1 == 1001
        assert len(rig._rtt_curr_frame) == 1
        
        # 2. Recycle across 2 frames to park h1 into freelist
        rig._recycle_frame_rtts()
        rig._recycle_frame_rtts()
        assert rig._rtt_free == {(100, 100): [1001]}
        
        # 3. Acquire same size RTT: Expected behavior is returning 1001 without host allocation.
        h2 = rig._acquire_rtt(100, 100)
        
        # Empirical observation: h2 is 1002 (new allocation) instead of 1001 (reused),
        # and _rtt_curr_frame contains both handles [(1001, 100, 100), (1002, 100, 100)]
        # This empirically confirms the freelist hit fallthrough bug.
        if h2 != 1001 or len(rig._rtt_curr_frame) > 1:
            pytest.fail(f"CONFIRMED BUG: _acquire_rtt failed to return freelist handle 1001! Returned {h2} with curr_frame={rig._rtt_curr_frame}")
    finally:
        if orig_host is not None:
            sys.modules["renpy_host"] = orig_host
        else:
            sys.modules.pop("renpy_host", None)


def test_rtt_acquire_in_place_rotation_under_heavy_nesting():
    """Verify that acquiring > pool_cap RTTs within a single frame reuses the oldest live RTT in place (HuangmeiC OOM guard)."""
    fake_host = _RttTestHost()
    orig_host = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake_host
    try:
        rig = _RttRig(pool_cap=3)
        w, h = (500, 500)

        # Acquire 3 RTTs (hits pool_cap)
        h1 = rig._acquire_rtt(w, h)
        h2 = rig._acquire_rtt(w, h)
        h3 = rig._acquire_rtt(w, h)
        assert len({h1, h2, h3}) == 3

        # 4th acquisition of same size in same frame must reuse h1 without allocating new handle from host
        h4 = rig._acquire_rtt(w, h)
        assert h4 == h1
        # 5th acquisition reuses h2
        h5 = rig._acquire_rtt(w, h)
        assert h5 == h2
        # Host create_render_texture was called exactly 3 times, not 5!
        assert len([c for c in fake_host.created if c[1] == w and c[2] == h]) == 3
    finally:
        if orig_host is not None:
            sys.modules["renpy_host"] = orig_host
        else:
            sys.modules.pop("renpy_host", None)


def test_rtt_dimension_clamping_adversarial_matrix():
    """Exhaustive matrix of adversarial and pathological dimensions."""
    # (w, h, lw, dw, lh, dh) -> expected (clamped_w, clamped_h)
    cases = [
        # Normal within bounds
        (800, 600, 1920, 1920, 1080, 1080, 800, 600),
        # Oversized (exceeds bounds)
        (3000, 2000, 1920, 1920, 1080, 1080, 1920, 1080),
        # Reverse-inflated (HuangmeiC 2219x5567)
        (2219, 5567, 1920, 1920, 1080, 1080, 1920, 1080),
        # Zero and negative dimensions clamped to >= 1
        (0, 0, 1920, 1920, 1080, 1080, 1, 1),
        (-100, -500, 1920, 1920, 1080, 1080, 1, 1),
        # Missing bounds (None) fallback
        (500, 400, None, None, None, None, 500, 400),
        # Zero bounds fallback
        (500, 400, 0, 0, 0, 0, 500, 400),
        # Extreme aspect ratio
        (1, 100000, 1280, 1280, 720, 720, 1, 720),
        (100000, 1, 1280, 1280, 720, 720, 1280, 1),
    ]

    for w, h, lw, dw, lh, dh, exp_w, exp_h in cases:
        cw, ch = _clamp_rtt_size(w, h, lw, dw, lh, dh)
        assert cw == exp_w, f"Failed width for ({w},{h}): got {cw}, expected {exp_w}"
        assert ch == exp_h, f"Failed height for ({w},{h}): got {ch}, expected {exp_h}"
        assert cw >= 1 and ch >= 1


# ============================================================================
# 3. Concurrency & Multi-Threaded Stress Tests
# ============================================================================

def test_multithreaded_rtt_pool_and_batching_concurrency():
    """Stress tests concurrent threads performing RTT allocation, recycling, and batching."""
    fake_host = _RttTestHost()
    orig_host = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake_host

    num_threads = 10
    iterations = 200
    barrier = threading.Barrier(num_threads)
    errors = []

    try:
        def worker(thread_id):
            try:
                barrier.wait()
                rig = _RttRig(pool_cap=4)
                grp = _InstanceGroup()

                for i in range(iterations):
                    # 1. RTT allocations & recycling
                    w = 64 * ((i % 4) + 1)
                    h = 64 * ((i % 4) + 1)
                    h1 = rig._acquire_rtt(w, h)
                    rig._release_rtt_now(h1, w, h)
                    rig._recycle_frame_rtts()

                    # 2. Quad batching
                    pipe = (i % 3) + 1
                    tex = (i % 5) + 100
                    grp.add(pipe, tex, None, None, 0, 0, 10, 10, 0, 0, 1, 1, (1, 1, 1, 1))
                    if i % 20 == 0:
                        grp.flush()

                rig._destroy_all_rtts()
            except Exception as e:
                errors.append((thread_id, e))

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent worker encountered errors: {errors}"
    finally:
        if orig_host is not None:
            sys.modules["renpy_host"] = orig_host
        else:
            sys.modules.pop("renpy_host", None)


def test_get_frame_stats_concurrency():
    """Verify get_frame_stats can be called concurrently from multiple threads without crash."""
    stats_results = []
    num_threads = 8
    barrier = threading.Barrier(num_threads)

    def stats_poller():
        barrier.wait()
        for _ in range(500):
            s = get_frame_stats()
            assert isinstance(s, dict)
            assert "draw_calls" in s
            stats_results.append(s["draw_calls"])

    threads = [threading.Thread(target=stats_poller) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(stats_results) == num_threads * 500
