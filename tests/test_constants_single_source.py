"""Constants single-source regression — T11.

Verifies that the canonical numbers now live exactly once in
renpy/wgpu/constants.py and no longer as scattered literals.
"""




def test_handle_pixels_cap():
    import renpy.wgpu.constants as c
    assert c.HANDLE_PIXELS_CAP == 2048


def test_mesh_cache_cap():
    import renpy.wgpu.constants as c
    assert c.MESH_CACHE_CAP == 4096


def test_golden_fallback():
    import renpy.wgpu.constants as c
    assert c.GOLDEN_FALLBACK_W == 1920
    assert c.GOLDEN_FALLBACK_H == 1080


def test_max_tex():
    import renpy.wgpu.constants as c
    assert c.MAX_TEX_W == 7680
    assert c.MAX_TEX_H == 4320


def test_present_lock():
    import renpy.wgpu.constants as c
    assert c.PRESENT_LOCK_TIMEOUT == 30.0


def test_ffmpeg_group():
    import renpy.wgpu.constants as c
    assert c.FFMPEG_CHUNK_FRAMES == 20
    assert c.FFMPEG_KICKSTART_FRAMES == 8
    assert c.FFMPEG_TIMEOUT_BASE == 30.0
    assert c.FFMPEG_TIMEOUT_PER_FRAME == 0.15


def test_auto_mipmap():
    import renpy.wgpu.constants as c
    assert c.AUTO_MIPMAP_THRESH == 0.75


def test_pil_padding_and_iso():
    import renpy.wgpu.constants as c
    assert c.PIL_PADDING == 4
    assert c.ISO_BASIS_X == 0.866
    assert c.ISO_BASIS_Y == 0.5


def test_no_scattered_magic():
    # Only constants.py should define 2048/4096/7680/4096 as assignment targets;
    # scattered code using them must import from constants, not re-declare.
    # Check that grep for 'HANDLE_PIXELS_CAP = 2048' only hits constants.py
    from pathlib import Path

    defining = []
    for _p in Path("renpy/wgpu").rglob("*.py"):
        for _ln in _p.read_text(encoding="utf-8", errors="replace").splitlines():
            if "2048" in _ln and "HANDLE_PIXELS_CAP" in _ln and "=" in _ln:
                defining.append(f"{_p}:{_ln}")
    assert len(defining) == 1 and "constants.py" in defining[0]
