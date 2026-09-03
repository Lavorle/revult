"""Adversarial stress test suite for Milestone 1 (HostTexture & Handle Resolver).

Challenges:
1. Subsurface Sharing: 100+ 9-slice Frame instances sharing a single HostTexture parent.
2. Sibling Remap Sharing: When a shared parent handle dies, slice 1 recovers it to new handle;
   slices 2..9 MUST hit State 2 (REMAPPED) to the new handle without creating redundant textures.
3. Coexistence & Shadowing: mesh_alive vs texture_alive with conflicting existence maps.
4. LRU Thrash & Handle Recycling: High volume churn through GpuHandleCache and GpuArena handle spaces.
5. Boundary & Malformed Inputs: 0x0 subsurfaces, negative dimensions, empty buffers, none types.
"""

import importlib
import sys
import types
import unittest


class _AdversarialRenpyHost(types.ModuleType):
    """Controllable adversarial mock for renpy_host."""

    def __init__(self):
        super().__init__("renpy_host")
        self._textures = {}  # handle -> (w, h, bytes)
        self._meshes = {}    # handle -> bool
        self._next_tex_id = 1000
        self._next_mesh_id = 5000
        self.touched_textures = []
        self.touched_meshes = []
        self.created_textures = []
        self.destroyed_textures = []

    def create_texture_rgba(self, w, h, pixels):
        w, h = int(w), int(h)
        if w <= 0 or h <= 0:
            raise ValueError(f"Invalid texture size {w}x{h}")
        h_id = self._next_tex_id
        self._next_tex_id += 1
        px = bytes(pixels[: w * h * 4]) if isinstance(pixels, (bytes, bytearray)) else bytes(w * h * 4)
        self._textures[h_id] = (w, h, px)
        self.created_textures.append((h_id, w, h))
        return h_id

    def destroy_texture(self, handle):
        h = int(handle)
        self.destroyed_textures.append(h)
        self._textures.pop(h, None)

    def texture_alive(self, handle):
        return int(handle) in self._textures

    def touch_texture(self, handle):
        h = int(handle)
        if h in self._textures:
            self.touched_textures.append(h)

    def create_mesh(self, *args, **kwargs):
        m_id = self._next_mesh_id
        self._next_mesh_id += 1
        self._meshes[m_id] = True
        return m_id

    def destroy_mesh(self, handle):
        self._meshes.pop(int(handle), None)

    def mesh_alive(self, handle):
        return int(handle) in self._meshes

    def touch_mesh(self, handle):
        h = int(handle)
        if h in self._meshes:
            self.touched_meshes.append(h)


def _load_wgpu_modules():
    mod_draw = importlib.import_module("renpy.wgpu.draw")
    mod_tex = importlib.import_module("renpy.wgpu.draw_texture")
    mod_ht = importlib.import_module("renpy.wgpu.host_texture")
    return mod_draw, mod_tex, mod_ht


class TestMilestone1AdversarialStress(unittest.TestCase):
    def setUp(self):
        self._orig_host = sys.modules.get("renpy_host")
        self.host = _AdversarialRenpyHost()
        sys.modules["renpy_host"] = self.host
        self.mod_draw, self.mod_tex, self.mod_ht = _load_wgpu_modules()
        self.GpuHandleCache = self.mod_draw.GpuHandleCache
        self.HostTexture = self.mod_ht.HostTexture

    def tearDown(self):
        if self._orig_host is not None:
            sys.modules["renpy_host"] = self._orig_host
        else:
            sys.modules.pop("renpy_host", None)

    def _make_texture_mixin(self, remap_cap=2048, pixel_cap=2048):
        m = self.mod_tex.TextureMixin()
        m.texture_cache = {}
        m._transient_tex = {}
        m._handle_remap = self.GpuHandleCache(remap_cap)
        m._handle_pixels = self.GpuHandleCache(pixel_cap)
        m._handle_pixels_cap = pixel_cap
        m._rtt_free = {}
        m._rtt_prev_frame = []
        m._rtt_curr_frame = []
        m._mesh_cache = {}
        m._mesh_deferred_destroy = []
        m._quad_mesh = None
        return m

    def test_subsurface_sharing_100_frames_9_slices(self):
        """Stress: 100 9-slice Frame instances (900 subsurfaces) sharing 1 HostTexture."""
        mixin = self._make_texture_mixin()
        # Create parent texture on host
        parent_id = self.host.create_texture_rgba(120, 120, bytes(120 * 120 * 4))
        parent_ht = self.HostTexture(parent_id, 120, 120)
        mixin._stash_handle_pixels(parent_id, 120, 120, bytes(120 * 120 * 4))

        # Generate 100 frames with 9 slices each
        frames = []
        for i in range(100):
            slices = []
            for r in range(3):
                for c in range(3):
                    sub = parent_ht.subsurface((c * 40, r * 40, 40, 40))
                    slices.append(sub)
            frames.append(slices)

        self.assertEqual(len(frames), 100)
        self.assertEqual(len(frames[0]), 9)

        # Probe all 900 subsurfaces
        for f_idx, slices in enumerate(frames):
            for s_idx, sub in enumerate(slices):
                res = mixin._ensure_host_texture_alive(sub)
                self.assertIsNotNone(res, f"Subsurface {s_idx} of frame {f_idx} failed alive check")
                self.assertEqual(int(res.handle), parent_id)
                self.assertEqual(int(res.texture), parent_id)

        # Verify touch_texture was called for the handle
        self.assertGreaterEqual(len(self.host.touched_textures), 900)

    def test_sibling_subsurface_remap_deduplication(self):
        """Stress: When a shared handle dies, first slice recreates it, subsequent 8 slices

        MUST resolve via _handle_remap (State 2) without triggering duplicate create_texture_rgba.
        """
        mixin = self._make_texture_mixin()
        parent_id = self.host.create_texture_rgba(60, 60, b"\xaa" * (60 * 60 * 4))
        parent_ht = self.HostTexture(parent_id, 60, 60)
        mixin._stash_handle_pixels(parent_id, 60, 60, b"\xaa" * (60 * 60 * 4))

        # Create 9 slices
        slices = [parent_ht.subsurface((i % 3 * 20, i // 3 * 20, 20, 20)) for i in range(9)]

        # Simulate GPU eviction: parent_id destroyed from host
        self.host.destroy_texture(parent_id)
        self.assertFalse(self.host.texture_alive(parent_id))

        initial_created_count = len(self.host.created_textures)

        # Slice 0 triggers dead recovery (State 3)
        res0 = mixin._ensure_host_texture_alive(slices[0])
        self.assertIsNotNone(res0)
        new_handle = int(res0.handle)
        self.assertNotEqual(new_handle, parent_id)
        # Exactly 1 new texture created on host
        self.assertEqual(len(self.host.created_textures), initial_created_count + 1)

        # Slices 1..8 should hit State 2 (REMAPPED) and return new_handle WITHOUT creating more textures
        for idx in range(1, 9):
            res_idx = mixin._ensure_host_texture_alive(slices[idx])
            self.assertIsNotNone(res_idx)
            self.assertEqual(int(res_idx.handle), new_handle, f"Slice {idx} did not remap to new handle")
            self.assertEqual(int(res_idx.texture), new_handle)

        # Created count must STILL be exactly initial + 1 (no duplicate textures created for sibling subsurfaces)
        self.assertEqual(len(self.host.created_textures), initial_created_count + 1)

    def test_mesh_alive_shadow_and_cross_domain_isolation(self):
        """Stress: If a handle ID exists in meshes but not in textures, texture_alive must return False.

        If a handle ID exists in textures but not in meshes, texture_alive must return True.
        _ensure_host_texture_alive must NEVER be misled by mesh_alive status.
        """
        mixin = self._make_texture_mixin()

        # Mesh 5000 is created (in meshes, not textures)
        mesh_id = self.host.create_mesh()
        self.assertTrue(self.host.mesh_alive(mesh_id))
        self.assertFalse(self.host.texture_alive(mesh_id))

        # Texture 1000 is created (in textures, not meshes)
        tex_id = self.host.create_texture_rgba(32, 32, bytes(32 * 32 * 4))
        self.assertTrue(self.host.texture_alive(tex_id))
        self.assertFalse(self.host.mesh_alive(tex_id))

        # 1. Probing a HostTexture with mesh_id and no pixel stash must return None (dead, not alive)
        fake_ht_mesh = self.HostTexture(mesh_id, 32, 32)
        out_mesh = mixin._ensure_host_texture_alive(fake_ht_mesh)
        self.assertIsNone(out_mesh, "HostTexture with mesh_id should be recognized as dead texture")

        # 2. Probing a HostTexture with tex_id must return the alive HostTexture
        ht_tex = self.HostTexture(tex_id, 32, 32)
        out_tex = mixin._ensure_host_texture_alive(ht_tex)
        self.assertIsNotNone(out_tex)
        self.assertEqual(int(out_tex.handle), tex_id)

    def test_lru_eviction_pressure_and_cache_bounds(self):
        """Stress: Push 500 distinct textures through a tight cache (cap=50).

        Verify GpuHandleCache enforces capacity and evicts oldest entries without error.
        """
        cap = 50
        mixin = self._make_texture_mixin(remap_cap=cap, pixel_cap=cap)

        handles = []
        for i in range(500):
            h = self.host.create_texture_rgba(16, 16, bytes(16 * 16 * 4))
            handles.append(h)
            mixin._stash_handle_pixels(h, 16, 16, bytes(16 * 16 * 4))

        # Check that _handle_pixels does not exceed cap
        self.assertLessEqual(len(mixin._handle_pixels), cap)

        # Check that the most recent entries are retained
        for h in handles[-cap:]:
            self.assertIn(h, mixin._handle_pixels)

    def test_boundary_inputs_and_exception_safety(self):
        """Stress: Boundary values, None types, 0 handles, negative sizes."""
        mixin = self._make_texture_mixin()

        # None input
        self.assertIsNone(mixin._ensure_host_texture_alive(None))

        # Zero handle HostTexture
        ht_zero = self.HostTexture(0, 10, 10)
        self.assertEqual(mixin._ensure_host_texture_alive(ht_zero), ht_zero)

        # Negative handle
        ht_neg = self.HostTexture(-5, 10, 10)
        self.assertEqual(mixin._ensure_host_texture_alive(ht_neg), ht_neg)

        # Raw int 0
        self.assertEqual(mixin._ensure_host_texture_alive(0), 0)

        # Raw positive int with dead handle and no pixel stash
        self.assertIsNone(mixin._ensure_host_texture_alive(999999))

        # Stash with invalid args
        mixin._stash_handle_pixels(None, 10, 10, b"")
        mixin._stash_handle_pixels(10, -5, 10, b"")
        mixin._stash_handle_pixels(10, 10, 10, b"short")
        self.assertEqual(len(mixin._handle_pixels), 0)


if __name__ == "__main__":
    unittest.main()
