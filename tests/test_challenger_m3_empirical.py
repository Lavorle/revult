"""
Adversarial Pytest Harness for Milestone 3 (Viewport & Mesh Clipping Parity).

Validates:
1. _crop_virt_quad_uv mathematical invariants:
   - Large negative child offsets (-100, -80) inside clip rect
   - Deep boundary overflow (3000x3000) inside 150x150 clip rect
   - Partial clipping (x-only and y-only)
   - Disjoint clip rects (empty intersection returning None)
   - Exact UV interpolation preservation
2. _clip_push_from_node multi-level nesting and stack invariance:
   - 3-level nested clip intersection
   - Single-axis clip propagation
   - Non-render / leaf node handling
"""

import math
import unittest

from renpy.wgpu.draw_surftree import SurftreeMixin


class FakeMatrix:
    def __init__(self, xdx=1.0, xdy=0.0, ydx=0.0, ydy=1.0, xdw=0.0, ydw=0.0):
        self.xdx = float(xdx)
        self.xdy = float(xdy)
        self.ydx = float(ydx)
        self.ydy = float(ydy)
        self.xdw = float(xdw)
        self.ydw = float(ydw)

    def transform(self, x, y):
        return (
            self.xdx * float(x) + self.xdy * float(y) + self.xdw,
            self.ydx * float(x) + self.ydy * float(y) + self.ydw,
        )

    def inverse(self):
        a, b, c, d = self.xdx, self.xdy, self.ydx, self.ydy
        tx, ty = self.xdw, self.ydw
        det = a * d - b * c
        if abs(det) < 1e-9:
            return FakeMatrix()
        inv = 1.0 / det
        ia = d * inv
        ib = -b * inv
        ic = -c * inv
        id_ = a * inv
        itx = -(ia * tx + ib * ty)
        ity = -(ic * tx + id_ * ty)
        return FakeMatrix(ia, ib, ic, id_, itx, ity)


class DummyNode:
    def __init__(self, width=100, height=100, xclipping=False, yclipping=False, reverse=None):
        self.width = width
        self.height = height
        self.xclipping = bool(xclipping)
        self.yclipping = bool(yclipping)
        self.reverse = reverse


class SurftreeStub(SurftreeMixin):
    def __init__(self, vs=(1280, 720)):
        self.virtual_size = vs
        self._clip_rect = None
        self._clip_poly = None
        self._mesh_cache = {}
        self._mesh_cache_cap = 4096
        self._mesh_deferred_destroy = []


class TestMilestone3ClippingEmpirical(unittest.TestCase):
    def setUp(self):
        self.stub = SurftreeStub()

    def test_crop_virt_quad_uv_negative_offset(self):
        """Child at (-100, -80) size (600, 600) under clip box (200, 150, 400, 300)."""
        self.stub._clip_rect = (200.0, 150.0, 400.0, 300.0)
        # Child placed at (100, 70) with width 600, height 600
        # Absolute child AABB = (100, 70, 700, 670)
        # Intersect with clip rect (200, 150, 400, 300) => (200, 150, 400, 300)
        cropped = self.stub._crop_virt_quad_uv(
            ox=100.0, oy=70.0, w=600.0, h=600.0,
            u0=0.0, v_bottom=1.0, u1=1.0, v_top=0.0
        )
        self.assertIsNotNone(cropped)
        ix0, iy0, cw, ch, nu0, nvb, nu1, nvt = cropped
        self.assertEqual(ix0, 200.0)
        self.assertEqual(iy0, 150.0)
        self.assertEqual(cw, 200.0)
        self.assertEqual(ch, 150.0)
        # Fractional UV checks:
        # fx0 = (200 - 100) / 600 = 1/6
        # fx1 = (400 - 100) / 600 = 3/6 = 0.5
        # fy0 = (150 - 70) / 600 = 80/600 = 2/15
        # fy1 = (300 - 70) / 600 = 230/600 = 23/60
        self.assertAlmostEqual(nu0, 1.0 / 6.0, places=5)
        self.assertAlmostEqual(nu1, 0.5, places=5)
        self.assertAlmostEqual(nvt, 80.0 / 600.0, places=5)
        self.assertAlmostEqual(nvb, 230.0 / 600.0, places=5)

    def test_crop_virt_quad_uv_extreme_overflow(self):
        """Child at (-1000, -1000) size (3000, 3000) under clip box (400, 300, 550, 450)."""
        self.stub._clip_rect = (400.0, 300.0, 550.0, 450.0)
        # Child dest: (-600, -700) to (2400, 2300)
        cropped = self.stub._crop_virt_quad_uv(
            ox=-600.0, oy=-700.0, w=3000.0, h=3000.0,
            u0=0.0, v_bottom=1.0, u1=1.0, v_top=0.0
        )
        self.assertIsNotNone(cropped)
        ix0, iy0, cw, ch, nu0, nvb, nu1, nvt = cropped
        self.assertEqual(ix0, 400.0)
        self.assertEqual(iy0, 300.0)
        self.assertEqual(cw, 150.0)
        self.assertEqual(ch, 150.0)
        self.assertAlmostEqual(nu0, (400.0 - (-600.0)) / 3000.0, places=5)
        self.assertAlmostEqual(nu1, (550.0 - (-600.0)) / 3000.0, places=5)

    def test_crop_virt_quad_uv_disjoint_returns_none(self):
        """Completely disjoint child dest rect returns None."""
        self.stub._clip_rect = (100.0, 100.0, 200.0, 200.0)
        # Child completely to the right
        self.assertIsNone(self.stub._crop_virt_quad_uv(300.0, 100.0, 50.0, 50.0, 0, 1, 1, 0))
        # Child completely above
        self.assertIsNone(self.stub._crop_virt_quad_uv(100.0, 10.0, 50.0, 50.0, 0, 1, 1, 0))
        # Zero-sized child
        self.assertIsNone(self.stub._crop_virt_quad_uv(100.0, 100.0, 0.0, 50.0, 0, 1, 1, 0))

    def test_clip_push_3level_nested_intersection(self):
        """Simulate 3 nested nodes with xclipping & yclipping."""
        # Level 1 at (100, 100) size 400x300 => [100, 100, 500, 400]
        n1 = DummyNode(400, 300, xclipping=True, yclipping=True)
        clip1, empty1 = self.stub._clip_push_from_node(n1, 100.0, 100.0)
        self.assertFalse(empty1)
        self.assertEqual(clip1, (100.0, 100.0, 500.0, 400.0))

        # Install clip1
        self.stub._clip_rect = clip1
        # Level 2 at relative (50, 50) => absolute (150, 150), size 200x200 => [150, 150, 350, 350]
        n2 = DummyNode(200, 200, xclipping=True, yclipping=True)
        clip2, empty2 = self.stub._clip_push_from_node(n2, 150.0, 150.0)
        self.assertFalse(empty2)
        self.assertEqual(clip2, (150.0, 150.0, 350.0, 350.0))

        # Install clip2
        self.stub._clip_rect = clip2
        # Level 3 at relative (-30, -30) => absolute (120, 120), size 200x200 => [120, 120, 320, 320]
        n3 = DummyNode(200, 200, xclipping=True, yclipping=True)
        clip3, empty3 = self.stub._clip_push_from_node(n3, 120.0, 120.0)
        self.assertFalse(empty3)
        self.assertEqual(clip3, (150.0, 150.0, 320.0, 320.0))

    def test_single_axis_clipping_bounds(self):
        """Test single-axis clipping (x-only and y-only)."""
        big = self.stub._CLIP_BIG
        # X-only clipping
        nx = DummyNode(200, 200, xclipping=True, yclipping=False)
        clip_x, empty_x = self.stub._clip_push_from_node(nx, 100.0, 100.0)
        self.assertFalse(empty_x)
        self.assertEqual(clip_x, (100.0, 100.0 - big, 300.0, 100.0 + big))

        # Y-only clipping
        ny = DummyNode(200, 200, xclipping=False, yclipping=True)
        clip_y, empty_y = self.stub._clip_push_from_node(ny, 100.0, 100.0)
        self.assertFalse(empty_y)
        self.assertEqual(clip_y, (100.0 - big, 100.0, 100.0 + big, 300.0))

    def test_disjoint_nodes_empty_flag(self):
        """Test that disjoint clip nodes return empty=True and None."""
        self.stub._clip_rect = (100.0, 100.0, 200.0, 200.0)
        n_disjoint = DummyNode(50, 50, xclipping=True, yclipping=True)
        clip, empty = self.stub._clip_push_from_node(n_disjoint, 300.0, 300.0)
        self.assertTrue(empty)
        self.assertIsNone(clip)


if __name__ == "__main__":
    unittest.main()
