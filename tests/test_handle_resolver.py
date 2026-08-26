"""Unit tests for renpy.wgpu.draw_texture._ensure_host_texture_alive 3-state resolver.

Loads draw_texture without constructing WgpuDraw so no GPU/renpy_host init fires.
renpy_host is replaced by a fake module providing mesh_alive / create_texture_rgba.
"""

import importlib
import sys
import types
import unittest


class _RenpyHostFake(types.ModuleType):
    """Minimal renpy_host fake: tunable mesh_alive + create_texture_rgba."""

    def __init__(self, alive_map=None, created=None):
        super().__init__("renpy_host")
        self._alive = alive_map if alive_map is not None else {}
        self._created = created if created is not None else []
        self.create_texture_rgba = self._create
        self.mesh_alive = self._alive_fn

    def _alive_fn(self, handle):
        return bool(self._alive.get(int(handle), False))

    def _create(self, w, h, pixels):
        h_win = len(self._created) + 9000
        self._created.append((h_win, int(w), int(h), pixels))
        return h_win


def _install_fake(alive_map=None, created=None):
    fake = _RenpyHostFake(alive_map=alive_map, created=created)
    sys.modules["renpy_host"] = fake
    return fake


def _load_mixin():
    # Import the module fresh so the fake renpy_host is seen at call time.
    mod = importlib.import_module("renpy.wgpu.draw_texture")
    return mod


def _make_mixin(remap, pixels, log_once=None):
    mod = _load_mixin()
    m = mod.TextureMixin()
    m._handle_remap = remap
    m._handle_pixels = pixels
    m._handle_pixels_cap = 2048
    if log_once is not None:
        m._log_once = log_once  # type: ignore[assignment]
    return m, mod


class TestHandleResolver(unittest.TestCase):
    def setUp(self):
        # Ensure a fake renpy_host is always present before any import-time use.
        self.fake = _install_fake()

    def tearDown(self):
        sys.modules.pop("renpy_host", None)

    def test_alive_hit(self):
        # ht.handle alive -> returns same handle
        self.fake._alive = {100: True}
        m, mod = _make_mixin(remap={}, pixels={})
        ht = mod.HostTexture(100, 8, 8)
        out = m._ensure_host_texture_alive(ht)
        self.assertIs(out, ht)
        self.assertEqual(int(out.handle), 100)

    def test_remapped_hit(self):
        # ht.handle dead but _handle_remap[ht.handle]=remapped alive -> returns remapped
        self.fake._alive = {200: True}
        m, mod = _make_mixin(remap={100: 200}, pixels={})
        ht = mod.HostTexture(100, 8, 8)
        out = m._ensure_host_texture_alive(ht)
        self.assertIs(out, ht)
        self.assertEqual(int(out.handle), 200)
        self.assertEqual(int(out.texture), 200)

    def test_dead_recover_create(self):
        # both dead, _handle_pixels hit -> calls create_texture_rgba -> returns new handle
        created = []
        self.fake = _install_fake(alive_map={}, created=created)
        m, mod = _make_mixin(
            remap={},
            pixels={100: (8, 8, bytes(8 * 8 * 4))},
        )
        ht = mod.HostTexture(100, 8, 8)
        out = m._ensure_host_texture_alive(ht)
        self.assertIs(out, ht)
        self.assertEqual(len(created), 1)
        new_h = created[0][0]
        self.assertEqual(int(out.handle), new_h)
        self.assertEqual(int(out.texture), new_h)
        self.assertEqual(created[0][1], 8)
        self.assertEqual(created[0][2], 8)


if __name__ == "__main__":
    unittest.main()
