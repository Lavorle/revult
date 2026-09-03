"""Adversarial stress test harness for Milestone 1: Frame 9-slice, reverse scaling, subsurface lifetime, and texture resolver."""

import unittest
from unittest.mock import MagicMock
from renpy.wgpu.host_texture import HostTexture
from renpy.wgpu.draw_texture import TextureMixin
from renpy.wgpu.draw_surftree import SurftreeMixin
from renpy.wgpu.draw_walk import ReverseScaler


class DummyWgpuDraw(TextureMixin, SurftreeMixin):
    def __init__(self):
        self.virtual_size = (1280, 720)
        self._clip_rect = None
        self._clip_poly = None
        self.texture_cache = {}
        self._transient_tex = {}
        self._handle_pixels = MagicMock()
        self._handle_pixels._cap = 2048
        self._handle_pixels.__len__ = lambda s: len(self.texture_cache)
        self._handle_pixels.get = lambda k: None
        self._handle_remap = {}
        self._mesh_cache = {}
        self._mesh_cache_cap = 4096
        self._mesh_deferred_destroy = []
        self._quad_mesh = 1


class TestAdversarialM1FrameStress(unittest.TestCase):
    def setUp(self):
        self.draw = DummyWgpuDraw()

    def test_subsurface_uv_precision_and_nesting(self):
        """Test recursive subsurface UV calculation precision for 9-slice parts."""
        parent = HostTexture(100, 200, 100)  # 200x100 texture
        self.assertEqual(parent.get_size(), (200, 100))
        self.assertTrue(self.draw._host_tex_is_full(parent))
        
        # Slicing top-left corner (20x20)
        tl = parent.subsurface((0, 0, 20, 20))
        self.assertEqual(tl.handle, 100)
        self.assertEqual(tl.get_size(), (20, 20))
        self.assertFalse(self.draw._host_tex_is_full(tl))
        u0, vb, u1, vt = self.draw._host_tex_uv(tl)
        self.assertAlmostEqual(u0, 0.0)
        self.assertAlmostEqual(u1, 0.1)  # 20 / 200
        self.assertAlmostEqual(vt, 0.0)  # top
        self.assertAlmostEqual(vb, 0.2)  # 20 / 100
        
        # Slicing center piece (160x60 at offset 20, 20)
        center = parent.subsurface((20, 20, 160, 60))
        u0, vb, u1, vt = self.draw._host_tex_uv(center)
        self.assertAlmostEqual(u0, 0.1)  # 20 / 200
        self.assertAlmostEqual(u1, 0.9)  # 180 / 200
        self.assertAlmostEqual(vt, 0.2)  # 20 / 100
        self.assertAlmostEqual(vb, 0.8)  # 80 / 100

        # Subsurface of a subsurface (nested 9-slice slice)
        nested = center.subsurface((10, 10, 50, 40))
        self.assertEqual(nested.handle, 100)
        self.assertEqual(nested.x, 30)
        self.assertEqual(nested.y, 30)
        self.assertEqual(nested.w, 50)
        self.assertEqual(nested.h, 40)
        u0, vb, u1, vt = self.draw._host_tex_uv(nested)
        self.assertAlmostEqual(u0, 0.15)  # 30 / 200
        self.assertAlmostEqual(u1, 0.40)  # 80 / 200
        self.assertAlmostEqual(vt, 0.30)  # 30 / 100
        self.assertAlmostEqual(vb, 0.70)  # 70 / 100

    def test_reverse_scale_matrix_inversion_and_identity(self):
        """Test matrix inversion for extreme non-uniform scales, rotations, and shear."""
        class MockMat2:
            def __init__(self, xdx, xdy, ydx, ydy, xdw=0.0, ydw=0.0):
                self.xdx = xdx
                self.xdy = xdy
                self.ydx = ydx
                self.ydy = ydy
                self.xdw = xdw
                self.ydw = ydw

        # Identity
        id_mat = MockMat2(1.0, 0.0, 0.0, 1.0)
        self.assertTrue(ReverseScaler.is_identity(id_mat))
        self.assertTrue(self.draw._is_identity(id_mat))

        # Non-uniform stretch (Frame piece)
        stretch = MockMat2(5.0, 0.0, 0.0, 0.2)
        self.assertFalse(ReverseScaler.is_identity(stretch))
        self.assertFalse(self.draw._is_identity(stretch))
        inv = ReverseScaler.inverse_matrix(stretch)
        self.assertIsNotNone(inv)
        px, py = inv.transform(10.0, 2.0)
        self.assertAlmostEqual(px, 2.0)   # 10 / 5
        self.assertAlmostEqual(py, 10.0)  # 2 / 0.2

        # Extreme scale
        huge_scale = MockMat2(1000.0, 0.0, 0.0, 0.001)
        inv_huge = ReverseScaler.inverse_matrix(huge_scale)
        px, py = inv_huge.transform(5000.0, 0.005)
        self.assertAlmostEqual(px, 5.0)
        self.assertAlmostEqual(py, 5.0)

        # Singular / degenerate matrix (det = 0)
        singular = MockMat2(0.0, 0.0, 0.0, 0.0)
        self.assertIsNone(ReverseScaler.inverse_matrix(singular))

    def test_reverse_dest_size_adversarial_branches(self):
        """Test _reverse_dest_size across all 3 ADR branches with boundary cases."""
        class MockNode:
            def __init__(self, rev):
                self.reverse = rev

        class MockMat2:
            def __init__(self, a, b, c, d):
                self.xdx = a
                self.xdy = b
                self.ydx = c
                self.ydy = d

        # 1. Scale-up / layout fill (Frame border stretch)
        node_up = MockNode(MockMat2(5.0, 0.0, 0.0, 2.0))
        ht_child = HostTexture(1, 10, 10)
        dw, dh = self.draw._reverse_dest_size(node_up, ht_child, (50, 20))
        self.assertEqual((dw, dh), (50, 20))

        # 2. Scale-down (oversample) with FULL HostTexture
        node_down = MockNode(MockMat2(0.5, 0.0, 0.0, 0.5))
        full_ht = HostTexture(2, 1920, 1080)
        dw, dh = self.draw._reverse_dest_size(node_down, full_ht, (960, 540))
        self.assertEqual((dw, dh), (960, 540))

        # 3. Scale-down with SUBSURFACE HostTexture (typewriter partial)
        sub_ht = HostTexture(2, 1920, 1080, x=0, y=0, w=400, h=200)
        dw, dh = self.draw._reverse_dest_size(node_down, sub_ht, (960, 540))
        self.assertEqual((dw, dh), (200, 100))  # 400 * 0.5, 200 * 0.5

        # 4. Zero parent size fallback
        dw, dh = self.draw._reverse_dest_size(node_up, ht_child, (0, 0))
        self.assertEqual((dw, dh), (1, 1))

    def test_crop_virt_quad_uv_clipping_bounds(self):
        """Test axis-aligned UV cropping under clip stack."""
        self.draw._clip_rect = (100.0, 100.0, 300.0, 300.0)

        # Dest completely outside clip (to the left)
        res = self.draw._crop_virt_quad_uv(0, 0, 50, 50, 0.0, 1.0, 1.0, 0.0)
        self.assertIsNone(res)

        # Dest completely inside clip
        res = self.draw._crop_virt_quad_uv(120, 120, 50, 50, 0.0, 1.0, 1.0, 0.0)
        self.assertIsNotNone(res)
        cx, cy, cw, ch, u0, vb, u1, vt = res
        self.assertEqual((cx, cy, cw, ch), (120.0, 120.0, 50.0, 50.0))
        self.assertEqual((u0, vb, u1, vt), (0.0, 1.0, 1.0, 0.0))

        # Dest partially clipped on top-left
        res = self.draw._crop_virt_quad_uv(50, 50, 100, 100, 0.0, 1.0, 1.0, 0.0)
        self.assertIsNotNone(res)
        cx, cy, cw, ch, u0, vb, u1, vt = res
        self.assertEqual((cx, cy, cw, ch), (100.0, 100.0, 50.0, 50.0))
        self.assertAlmostEqual(u0, 0.5)  # half cropped from left
        self.assertAlmostEqual(u1, 1.0)
        self.assertAlmostEqual(vt, 0.5)  # half cropped from top
        self.assertAlmostEqual(vb, 1.0)


if __name__ == "__main__":
    unittest.main()
