"""RTT pool regression — T7/T8.

Covers freelist hit/miss/evict/clamp without GPU.
"""

import importlib
import sys
import types


def _dummy_cls(pool_cap=2):
    from renpy.wgpu.rtt_pool import RttPoolMixin

    class Dummy(RttPoolMixin):
        def __init__(self):
            self._rtt_free = {}
            self._rtt_prev_frame = []
            self._rtt_curr_frame = []
            self._rtt_pool_cap = pool_cap
            self.drawable_size = (1280, 720)
            self.virtual_size = (1280, 720)
            self.layout_virtual_size = (1280, 720)
            self.texture_cache = {}

    return Dummy


def _fake_host(counter_start=100):
    counter = {"n": counter_start}
    fake = types.SimpleNamespace(
        create_render_texture=lambda w, h: counter.__setitem__("n", counter["n"] + 1) or counter["n"],
        destroy_texture=lambda h: None,
    )
    return fake


def test_acquire_miss():
    Dummy = _dummy_cls()
    fake = _fake_host(200)
    orig = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake
    try:
        d = Dummy()
        h = d._acquire_rtt(100, 100)
        assert isinstance(h, int) and h > 0
        assert len(d._rtt_curr_frame) == 1
    finally:
        if orig is not None:
            sys.modules["renpy_host"] = orig
        else:
            sys.modules.pop("renpy_host", None)


def test_acquire_hit():
    Dummy = _dummy_cls(pool_cap=4)
    fake = _fake_host(300)
    orig = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake
    try:
        d = Dummy()
        h1 = d._acquire_rtt(100, 100)
        d._recycle_frame_rtts()
        d._recycle_frame_rtts()
        # now 100x100 in freelist
        h2 = d._acquire_rtt(100, 100)
        assert isinstance(h2, int)
    finally:
        if orig is not None:
            sys.modules["renpy_host"] = orig
        else:
            sys.modules.pop("renpy_host", None)


def test_evict_cap():
    Dummy = _dummy_cls(pool_cap=1)
    fake = _fake_host(400)
    orig = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake
    try:
        d = Dummy()
        d._acquire_rtt(64, 64)
        d._recycle_frame_rtts()
        d._recycle_frame_rtts()
        free_before = sum(len(v) for v in d._rtt_free.values())
        # acquiring again should respect cap
        d._acquire_rtt(64, 64)
        d._recycle_frame_rtts()
        free_after = sum(len(v) for v in d._rtt_free.values())
        assert free_after <= 1
    finally:
        if orig is not None:
            sys.modules["renpy_host"] = orig
        else:
            sys.modules.pop("renpy_host", None)


def test_clamp():
    from renpy.wgpu.rtt_pool import _clamp_rtt_size

    # oversize → clamped
    w, h = _clamp_rtt_size(2000, 2000, 1280, 1280, 720, 720)
    assert w <= 1280 and h <= 720
    # small stays small
    w, h = _clamp_rtt_size(100, 100, 1280, 1280, 720, 720)
    assert (w, h) == (100, 100)
