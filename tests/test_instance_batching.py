"""Unit tests for _InstanceGroup, quad collapsing, and 10x perf gate (M3)."""
import types
import sys
from renpy.wgpu.host_bridge import _InstanceGroup, get_frame_stats


def test_instance_group_add_and_pack():
    grp = _InstanceGroup()
    assert grp.empty()
    # Add one quad: x0=10, y0=20, x1=50, y1=80, u0=0.0, v0=0.0, u1=1.0, v1=1.0, color=(1,0.5,0.25,1)
    grp.add(1, 101, None, None, 10, 20, 50, 80, 0.0, 0.0, 1.0, 1.0, (1.0, 0.5, 0.25, 1.0))
    assert not grp.empty()
    key = (1, 101, None, None)
    assert key in grp.map
    data = grp.map[key]
    assert len(data) == 12
    # Check 12-float layout: [rox, roy, rsx, rsy, uox, voy, usx, vsy, cr, cg, cb, ca]
    assert data[0] == 10.0  # rox
    assert data[1] == 20.0  # roy
    assert data[2] == 40.0  # rsx (50 - 10)
    assert data[3] == 60.0  # rsy (80 - 20)
    assert data[4] == 0.0   # uox
    assert data[5] == 0.0   # voy
    assert data[6] == 1.0   # usx
    assert data[7] == 1.0   # vsy
    assert data[8] == 1.0   # cr
    assert data[9] == 0.5   # cg
    assert data[10] == 0.25 # cb
    assert data[11] == 1.0  # ca


def test_instance_group_add_packed():
    grp = _InstanceGroup()
    key = (1, 101, None, None)
    grp.add_packed(key, (10, 20), (40, 60), (0.0, 0.0), (1.0, 1.0), (1.0, 0.5, 0.25, 1.0))
    assert key in grp.map
    data = grp.map[key]
    assert len(data) == 12
    assert data[0] == 10.0
    assert data[2] == 40.0


def test_instance_group_invalid_input_resilience():
    grp = _InstanceGroup()
    # Invalid non-float value should not crash, but silently skip
    grp.add(1, 101, None, None, "invalid", 20, 50, 80, 0.0, 0.0, 1.0, 1.0, (1, 1, 1, 1))
    assert grp.empty()


def test_instance_group_10x_collapsing():
    grp = _InstanceGroup()
    # Add 50 quads sharing the same pipeline (1) and texture (200)
    for i in range(50):
        grp.add(1, 200, None, None, i * 10, 0, (i + 1) * 10, 20, 0.0, 0.0, 1.0, 1.0, (1, 1, 1, 1))

    # All 50 quads should collapse into a single map entry
    assert len(grp.map) == 1
    key = (1, 200, None, None)
    assert len(grp.map[key]) == 50 * 12

    # Mock host draw_instances to count dispatched draw calls
    dispatched_calls = []
    fake_host = types.SimpleNamespace(
        draw_instances=lambda pipe, t0, t1, t2, instances: dispatched_calls.append((pipe, t0, t1, t2, len(instances) // 12)),
    )
    sys_modules_orig = sys.modules.get('renpy_host')
    sys.modules['renpy_host'] = fake_host
    try:
        grp.flush()
        # Exactly 1 host draw_instances call for 50 quads
        assert len(dispatched_calls) == 1
        pipe, t0, t1, t2, quad_count = dispatched_calls[0]
        assert pipe == 1
        assert t0 == 200
        assert quad_count == 50

        # Enforce 10x gate: draw_calls (1) < quads (50) / 10 (5)
        draw_calls = len(dispatched_calls)
        quads = quad_count
        assert draw_calls < quads / 10, f'Draw calls {draw_calls} not < {quads / 10}'
    finally:
        if sys_modules_orig is not None:
            sys.modules['renpy_host'] = sys_modules_orig
        else:
            sys.modules.pop('renpy_host', None)


def test_instance_group_multi_key_batching():
    grp = _InstanceGroup()
    # 2 pipelines, 2 textures each, 25 quads per combination -> 100 quads total
    for pipe in [1, 2]:
        for tex in [10, 20]:
            for i in range(25):
                grp.add(pipe, tex, None, None, i, 0, i + 1, 10, 0, 0, 1, 1, (1, 1, 1, 1))

    assert len(grp.map) == 4
    for k in grp.map:
        assert len(grp.map[k]) == 25 * 12

    dispatched = []
    fake_host = types.SimpleNamespace(
        draw_instances=lambda pipe, t0, t1, t2, instances: dispatched.append((pipe, t0, len(instances) // 12)),
    )
    sys_modules_orig = sys.modules.get('renpy_host')
    sys.modules['renpy_host'] = fake_host
    try:
        grp.flush()
        assert len(dispatched) == 4
        total_quads = sum(cnt for _, _, cnt in dispatched)
        assert total_quads == 100
        draw_calls = len(dispatched)
        assert draw_calls < total_quads / 10  # 4 < 10
    finally:
        if sys_modules_orig is not None:
            sys.modules['renpy_host'] = sys_modules_orig
        else:
            sys.modules.pop('renpy_host', None)


def test_instance_group_stack_push_pop():
    grp = _InstanceGroup()
    grp.add(1, 100, None, None, 0, 0, 10, 10, 0, 0, 1, 1, (1, 1, 1, 1))
    assert len(grp.map[(1, 100, None, None)]) == 12

    # Push for nested frame
    grp.push()
    assert grp.empty()

    # Add child RTT quad
    grp.add(2, 200, None, None, 5, 5, 15, 15, 0, 0, 1, 1, (1, 1, 1, 1))
    assert len(grp.map[(2, 200, None, None)]) == 12
    grp.clear()
    assert grp.empty()

    # Pop parent
    grp.pop()
    assert not grp.empty()
    assert (1, 100, None, None) in grp.map
    assert len(grp.map[(1, 100, None, None)]) == 12


def test_instance_group_flush_fallback():
    grp = _InstanceGroup()
    grp.add(1, 100, None, None, 0, 0, 10, 10, 0, 0, 1, 1, (1, 1, 1, 1))
    grp.add(1, 100, None, None, 10, 0, 20, 10, 0, 0, 1, 1, (1, 1, 1, 1))

    # Without host, flush with fallback object
    fallback_emitted = []
    fake_draw_obj = types.SimpleNamespace(
        _flush_instance_fallback=lambda p, t, t1, t2, datas: fallback_emitted.append((p, t, len(datas) // 12))
    )

    sys_modules_orig = sys.modules.get('renpy_host')
    sys.modules.pop('renpy_host', None)
    try:
        grp.flush(fake_draw_obj)
        assert len(fallback_emitted) == 1
        p, t, cnt = fallback_emitted[0]
        assert p == 1
        assert t == 100
        assert cnt == 2
        assert grp.empty()
    finally:
        if sys_modules_orig is not None:
            sys.modules['renpy_host'] = sys_modules_orig


def test_get_frame_stats_fallback_and_types():
    stats = get_frame_stats()
    assert isinstance(stats, dict)
    assert 'draw_calls' in stats
    assert 'quads' in stats
    assert 'instances' in stats
    assert 'overdraw_est' in stats
    assert 'ms' in stats
    assert isinstance(stats['draw_calls'], int)
    assert isinstance(stats['quads'], int)
