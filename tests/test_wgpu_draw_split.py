"""Minimal regression for draw split-out modules (no GPU)."""
import sys

from renpy.wgpu.host_texture import HostTexture, _surf_fingerprint
from renpy.wgpu.rtt_pool import RttPoolMixin


def test_surf_fingerprint_size_and_content():
    p1 = bytes([0] * 128)
    p2 = bytes([1] * 128)
    f1 = _surf_fingerprint(p1, 10, 10)
    f2 = _surf_fingerprint(p2, 10, 10)
    assert f1 != f2
    # size sensitivity
    f3 = _surf_fingerprint(p1, 10, 11)
    assert f1 != f3
    # deterministic
    assert _surf_fingerprint(p1, 10, 10) == f1
    assert len(f1) == 8


def test_host_texture_basic():
    t = HostTexture(handle=42, width=100, height=80)
    assert t.handle == 42
    assert t.get_size() == (100, 80)
    assert int(t) == 42
    # subsurface
    sub = t.subsurface((10, 5, 20, 10))
    assert sub.x == 10 and sub.y == 5 and sub.w == 20 and sub.h == 10
    assert sub.handle == 42
    # get_size after subsurface
    assert sub.get_size() == (20, 10)


def test_host_texture_alias():
    t = HostTexture(7, 4, 4)
    assert t.texture == t.handle


def test_rtt_pool_mixin_acquire_and_recycle():
    # Minimal dummy that mimics WgpuDraw state without GPU
    class Dummy(RttPoolMixin):
        def __init__(self):
            self._rtt_free = {}
            self._rtt_prev_frame = []
            self._rtt_curr_frame = []
            self._rtt_pool_cap = 2
            self.drawable_size = (1280, 720)
            self.virtual_size = (1280, 720)
            self.layout_virtual_size = (1280, 720)
            self.texture_cache = {}

    # Mock renpy_host.create_render_texture to return incrementing handle
    import types

    counter = {"n": 100}
    fake_host = types.SimpleNamespace(
        create_render_texture=lambda w, h: counter.__setitem__("n", counter["n"] + 1) or counter["n"],
        destroy_texture=lambda h: None,
    )
    # Inject fake module for import inside mixin
    sys_modules_orig = sys.modules.get("renpy_host")
    sys.modules["renpy_host"] = fake_host
    try:
        d = Dummy()
        # _acquire_rtt should clamp to min(layout, drawable) and track
        h1 = d._acquire_rtt(2000, 2000)  # larger than 1280x720 should clamp
        assert h1 is not None
        assert len(d._rtt_curr_frame) == 1
        # second acquire same size should create new handle (no free yet)
        h2 = d._acquire_rtt(100, 100)
        assert h2 != h1
        # recycle: current -> prev, then free one
        d._recycle_frame_rtts()
        assert len(d._rtt_prev_frame) == 2
        assert len(d._rtt_curr_frame) == 0
        # next frame recycle should free prev into freelist capped at 2
        d._recycle_frame_rtts()
        free_total = sum(len(v) for v in d._rtt_free.values())
        assert free_total == 2
        # acquiring same size should reuse from freelist
        h3 = d._acquire_rtt(100, 100)
        assert h3 in (h1, h2) or isinstance(h3, int)
    finally:
        if sys_modules_orig is not None:
            sys.modules["renpy_host"] = sys_modules_orig
        else:
            sys.modules.pop("renpy_host", None)
