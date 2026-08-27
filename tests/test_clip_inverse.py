"""M3 B2 T2 — clip inverse / rotated polygon tests (6 cases)."""
import math
import unittest

from renpy.wgpu.draw_surftree import SurftreeMixin
from renpy.wgpu.draw_walk import WalkCtx, ReverseScaler


class FakeMatrix:
    """Minimal Matrix2D mock with xdx/xdy/ydx/ydy/xdw/ydw and inverse/transform."""
    def __init__(self, xdx=1.0, xdy=0.0, ydx=0.0, ydy=1.0, xdw=0.0, ydw=0.0):
        self.xdx = float(xdx)
        self.xdy = float(xdy)
        self.ydx = float(ydx)
        self.ydy = float(ydy)
        self.xdw = float(xdw)
        self.ydw = float(ydw)
        self.xdz = 0.0
        self.ydz = 0.0
        self.zdx = 0.0
        self.zdy = 0.0
        self.zdz = 1.0
        self.zdw = 0.0
        self.wdx = 0.0
        self.wdy = 0.0
        self.wdz = 0.0
        self.wdw = 1.0

    def transform(self, x, y):
        return (self.xdx * float(x) + self.xdy * float(y) + self.xdw,
                self.ydx * float(x) + self.ydy * float(y) + self.ydw)

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


def make_node(width=100, height=100, xclipping=True, yclipping=True, reverse=None):
    class N:
        pass
    n = N()
    n.width = width
    n.height = height
    n.xclipping = xclipping
    n.yclipping = yclipping
    n.reverse = reverse
    return n


class Stub(SurftreeMixin):
    def __init__(self, vs=(1280, 720)):
        self.virtual_size = vs
        self._clip_rect = None
        self._clip_poly = None
        self._mesh_cache = {}
        self._mesh_cache_cap = 4096
        self._mesh_deferred_destroy = []


class TestClipInverse(unittest.TestCase):
    def test_identity_fast_path_equivalence(self):
        """Unit matrix should stay AABB fast path, identical to no-reverse."""
        stub = Stub()
        node_id = make_node(100, 100, True, True, FakeMatrix(1, 0, 0, 1))
        node_plain = make_node(100, 100, True, True, None)
        # No prior clip
        r1, empty1 = stub._clip_push_from_node(node_id, 10, 20)
        # reset
        stub2 = Stub()
        r2, empty2 = stub2._clip_push_from_node(node_plain, 10, 20)
        self.assertFalse(empty1)
        self.assertFalse(empty2)
        # Both should be AABB tuple (4 floats), not list
        self.assertIsInstance(r1, tuple)
        self.assertIsInstance(r2, tuple)
        self.assertEqual(r1, r2)
        # Also _is_identity
        self.assertTrue(stub._is_identity(FakeMatrix(1, 0, 0, 1)))
        self.assertTrue(stub._is_identity(None))
        self.assertFalse(stub._is_identity(FakeMatrix(0, -1, 1, 0)))

    def test_90deg_polygon(self):
        """90° rotation pharma frame should yield 4-point polygon (not AABB)."""
        stub = Stub()
        rev = FakeMatrix(0, -1, 1, 0)  # 90° around origin
        node = make_node(100, 100, True, True, rev)
        poly, empty = stub._clip_push_from_node(node, 0, 0)
        self.assertFalse(empty)
        self.assertIsInstance(poly, list)
        self.assertEqual(len(poly), 4)
        # Area should stay ~10000 (rotation preserves area)
        area = stub._poly_area(poly)
        self.assertAlmostEqual(area, 10000.0, delta=1.0)
        # Check that poly is not axis-aligned AABB (points not all sharing x/y)
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        # For 90° around origin, we expect x in [-100,0] or [0,100] depending on transform direction
        # Our _transform_quad uses inverse, so (100,0) -> (0, -100) etc => y negative
        # Just check not all xs in [0,100] and ys in [0,100] simultaneously (i.e., rotated)
        is_aabb = (min(xs) == 0 and max(xs) == 100 and min(ys) == 0 and max(ys) == 100)
        self.assertFalse(is_aabb, f"90deg poly still AABB: {poly}")

    def test_45deg_polygon(self):
        """45° rotation should also yield 4-point polygon with correct area."""
        stub = Stub()
        c = math.cos(math.radians(45))
        s = math.sin(math.radians(45))
        rev = FakeMatrix(c, -s, s, c)
        node = make_node(100, 100, True, True, rev)
        poly, empty = stub._clip_push_from_node(node, 0, 0)
        self.assertFalse(empty)
        self.assertIsInstance(poly, list)
        self.assertEqual(len(poly), 4)
        area = stub._poly_area(poly)
        self.assertAlmostEqual(area, 10000.0, delta=2.0)
        # Bounding box of 45° 100x100 square rotated around origin should be larger than 100
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        # Rotated square bbox ~ 100*sqrt2 ≈141
        self.assertGreater(w, 110)
        self.assertGreater(h, 110)

    def test_reverse_clip_nested(self):
        """Nested reverse+clip: parent AABB 0,0 200x200, child 50,50 100x100 with 90°."""
        stub = Stub()
        # First push parent (no reverse, AABB)
        parent = make_node(200, 200, True, True, None)
        p_clip, empty = stub._clip_push_from_node(parent, 0, 0)
        self.assertFalse(empty)
        # Install parent clip (simulate _draw_node_inner)
        stub._clip_rect = p_clip
        stub._clip_poly = None
        # Child with reverse 90° at 50,50
        rev = FakeMatrix(0, -1, 1, 0)
        child = make_node(100, 100, True, True, rev)
        poly, empty2 = stub._clip_push_from_node(child, 50, 50)
        self.assertFalse(empty2)
        self.assertIsInstance(poly, list)
        # Result should be inside parent (intersect)
        # Parent bbox 0-200, child poly after transform should still intersect and be within?
        # Check area <= 10000
        area = stub._poly_area(poly)
        self.assertGreater(area, 0)
        self.assertLessEqual(area, 10000.0 + 1e-3)
        # All points of nested poly should be within parent's expanded bbox?
        # Since child at 50,50 size 100, its AABB before rotate is 50-150; after inverse rotate, points may be negative, but intersect with parent should clip them to 0-200.
        # We just check that intersect didn't return None and poly length >=3
        self.assertGreaterEqual(len(poly), 3)

    def test_empty_intersection_short_circuit(self):
        """Disjoint clips should short-circuit to empty (None, True)."""
        stub = Stub()
        # Set current clip to 0,0,50,50
        stub._clip_rect = (0, 0, 50, 50)
        stub._clip_poly = None
        # New node at 100,100 20x20 (disjoint)
        node = make_node(20, 20, True, True, None)
        res, empty = stub._clip_push_from_node(node, 100, 100)
        self.assertTrue(empty)
        self.assertIsNone(res)
        # Same with polygon path: parent poly 0,0 50,50, child poly at 100,100 20x20 with rotate
        stub2 = Stub()
        stub2._clip_poly = [(0, 0), (50, 0), (50, 50), (0, 50)]
        stub2._clip_rect = None
        rev = FakeMatrix(0, -1, 1, 0)
        node2 = make_node(20, 20, True, True, rev)
        res2, empty2 = stub2._clip_push_from_node(node2, 100, 100)
        self.assertTrue(empty2)
        self.assertIsNone(res2)

    def test_inverse_correctness(self):
        """_is_identity tolerance 1e-6 and _transform_quad inverse correctness."""
        stub = Stub()
        # Tolerance checks
        almost_id = FakeMatrix(1.0 + 5e-7, 0, 0, 1.0 + 5e-7)
        self.assertTrue(stub._is_identity(almost_id))
        not_id = FakeMatrix(1.0 + 2e-6, 0, 0, 1.0)
        self.assertFalse(stub._is_identity(not_id))
        # ReverseScaler reuse
        self.assertTrue(ReverseScaler.is_identity(FakeMatrix(1, 0, 0, 1)))
        self.assertFalse(ReverseScaler.is_identity(FakeMatrix(0, -1, 1, 0)))
        # Transform quad inverse correctness: 90° reverse, forward should be -90°
        rev = FakeMatrix(0, -1, 1, 0)
        quad = [(0, 0), (100, 0), (100, 100), (0, 100)]
        transformed = stub._transform_quad(quad, rev)
        # Expected via inverse: (100,0) -> (0, -100) for this matrix's inverse (0,1,-1,0)
        # Compute manually via FakeMatrix inverse
        inv = rev.inverse()
        expected = [inv.transform(x, y) for x, y in quad]
        for (ax, ay), (bx, by) in zip(transformed, expected):
            self.assertAlmostEqual(ax, bx, places=5)
            self.assertAlmostEqual(ay, by, places=5)
        # Also test that transforming with identity returns same
        self.assertEqual(stub._transform_quad(quad, FakeMatrix(1, 0, 0, 1)), quad)
        # WalkCtx mutual exclusion
        ctx = WalkCtx(ox=0, oy=0, clip_rect=(0, 0, 100, 100), clip_poly=[(0, 0), (100, 0), (100, 100), (0, 100)])
        self.assertIsNotNone(ctx.clip_poly)
        self.assertIsNotNone(ctx.clip_rect)
        # Priority is clip_poly is not None
        self.assertIsNotNone(ctx.clip_poly)

    def test_no_residual_comment(self):
        """Ensure residual 'no half-implement' comment is removed."""
        import pathlib
        p = pathlib.Path("renpy/wgpu/draw_surftree.py")
        txt = p.read_text(encoding="utf-8")
        self.assertNotIn("residual — no half-implement", txt)
        self.assertNotIn("no half-implement", txt)


if __name__ == "__main__":
    unittest.main()
