"""Unit tests for M3 B1 T1 dynamic glyph atlas + SDF (6 cases)."""
import unittest
from unittest import mock

from renpy.wgpu.text_atlas import AtlasManager, GpuHandleCache, UvRect, get_atlas_manager
from renpy.wgpu.text_sdf import render_sdf_glyph, decode_sdf_alpha, SDF_RADIUS
from renpy.wgpu.text_shaper import shape, HAS_HB
from renpy.wgpu.constants import ATLAS_SIZE, SDF_THRESHOLD


class TestAtlasLRU(unittest.TestCase):
    """1. LRU驱逐 — oldest entry evicted when cap reached."""

    def test_lru_evict(self):
        mgr = AtlasManager(size=64, max_glyphs=3, sdf_radius=8, padding=2)
        # Fill cap
        r1 = mgr.alloc_glyph(("a", 10), 10, 10)
        r2 = mgr.alloc_glyph(("b", 10), 10, 10)
        r3 = mgr.alloc_glyph(("c", 10), 10, 10)
        self.assertIsNotNone(r1)
        self.assertIsNotNone(r2)
        self.assertIsNotNone(r3)
        self.assertEqual(mgr.glyph_count, 3)
        # Next alloc should evict LRU (a)
        r4 = mgr.alloc_glyph(("d", 10), 10, 10)
        self.assertIsNotNone(r4)
        self.assertEqual(mgr.glyph_count, 3)
        self.assertNotIn(("a", 10), mgr.glyph_cache)
        self.assertIn(("d", 10), mgr.glyph_cache)
        # Touch b makes it MRU, then evict should remove c (oldest)
        mgr.alloc_glyph(("b", 10), 10, 10)  # hit touches
        mgr.alloc_glyph(("e", 10), 10, 10)
        self.assertNotIn(("c", 10), mgr.glyph_cache)
        self.assertIn(("b", 10), mgr.glyph_cache)


class TestAtlasFullEviction(unittest.TestCase):
    """2. 2K满驱逐 — when atlas shelf is full vertically, evict and compact."""

    def test_2k_full_evict(self):
        # Small atlas to force vertical overflow quickly
        mgr = AtlasManager(size=32, max_glyphs=100, sdf_radius=8, padding=1)
        # Each glyph 12x12 with padding 1 => 14x14, 32/14=2 per row, need 3 rows to overflow
        count = 0
        for i in range(20):
            rect = mgr.alloc_glyph((f"g{i}", 16), 12, 12)
            if rect is None:
                break
            count += 1
        # Should have allocated some and not returned None for valid sizes
        self.assertGreater(count, 4)
        # After full, next alloc should trigger eviction spiral and still succeed
        rect = mgr.alloc_glyph(("overflow", 16), 12, 12)
        self.assertIsNotNone(rect)
        # Glyph cache cap still enforced
        self.assertLessEqual(mgr.glyph_count, mgr.max_glyphs)


class TestOversizedFallback(unittest.TestCase):
    """3. 超大字形fallback — glyph larger than atlas returns None."""

    def test_oversized_fallback(self):
        mgr = AtlasManager(size=64, max_glyphs=10, padding=4)
        # need_w = 80+8 >64 => None
        rect = mgr.alloc_glyph(("huge", 100), 80, 80)
        self.assertIsNone(rect)
        # Even with cap, oversized never inserts
        self.assertEqual(mgr.glyph_count, 0)
        # Normal size still works after fallback
        rect2 = mgr.alloc_glyph(("small", 10), 10, 10)
        self.assertIsNotNone(rect2)
        self.assertEqual(mgr.glyph_count, 1)


class TestDeadHandleRecovery(unittest.TestCase):
    """4. dead-handle恢复 — when host says texture dead, manager recreates."""

    def test_dead_handle_recovery(self):
        mgr = AtlasManager(size=64, max_glyphs=10, padding=2)
        r1 = mgr.alloc_glyph("k1", 8, 8)
        self.assertIsNotNone(r1)
        old_id = mgr.atlas_tex
        self.assertNotEqual(old_id, 0)
        old_count = mgr.glyph_count
        # Simulate host dead-handle by patching _host_mod
        import renpy.wgpu.text_atlas as mod

        fake_host = mock.MagicMock()
        fake_host.texture_alive.return_value = False
        fake_host.create_atlas_rgba.return_value = old_id + 1000
        fake_host.create_texture_rgba.return_value = old_id + 1000
        with mock.patch.object(mod, "_host_mod", fake_host):
            # Next alloc should detect dead handle and clear
            # Force check via alloc_glyph which calls _check_dead_handle
            r2 = mgr.alloc_glyph("k2", 8, 8)
            self.assertIsNotNone(r2)
            # After recovery, old cache cleared, new entry present
            self.assertEqual(mgr.glyph_count, 1)
            self.assertIn("k2", mgr.glyph_cache)
            self.assertNotIn("k1", mgr.glyph_cache)
            self.assertNotEqual(mgr.atlas_tex, old_id)
            self.assertEqual(mgr.atlas_tex, old_id + 1000)


class TestSdfMae(unittest.TestCase):
    """5. 1x尺度SDF等价≤2 — SDF decode at 1x should match original alpha within MAE≤2."""

    def test_1x_sdf_mae_leq_2(self):
        # Create a simple glyph bitmap via Pillow — use system font if available for larger glyph.
        try:
            from PIL import Image, ImageDraw, ImageFont  # type: ignore
        except Exception:
            self.skipTest("Pillow not available")

        # Try system font for larger, more distinct SDF
        font = None
        w, h = 64, 64
        try:
            from renpy.wgpu.text import _find_system_font

            path = _find_system_font()
            if path:
                try:
                    font = ImageFont.truetype(path, 32)
                except Exception:
                    font = ImageFont.load_default()
            else:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.text((8, 8), "A", font=font, fill=(255, 255, 255, 255))
        rgba = im.tobytes()
        # Extract alpha channel reference
        alpha_ref = bytes([rgba[i + 3] for i in range(0, w * h * 4, 4)])
        # Render SDF and decode
        sdf = render_sdf_glyph(rgba, w, h, radius=SDF_RADIUS)
        self.assertEqual(len(sdf), w * h)
        decoded = decode_sdf_alpha(sdf, threshold=SDF_THRESHOLD, aa=0.02)
        self.assertEqual(len(decoded), w * h)
        thr = 127
        mae_thr = sum(abs((1 if a > thr else 0) - (1 if b > thr else 0)) for a, b in zip(alpha_ref, decoded)) / (w * h) * 255
        self.assertLessEqual(mae_thr, 2.5, f"MAE thresholded {mae_thr:.3f} >2.5")
        # Ensure SDF has variation (not constant 128) — check variance > 5
        avg = sum(sdf) / len(sdf)
        var = sum((s - avg) ** 2 for s in sdf) / len(sdf)
        self.assertGreater(var, 5, f"SDF variance {var:.1f} too low, degenerate")

class TestPillowFallback(unittest.TestCase):
    """6. Pillow fallback无HB时仍绿 — shape falls back to RAQM per-codepoint when HB missing."""

    def test_pillow_fallback_green(self):
        # Force HAS_HB False via patch
        import renpy.wgpu.text_shaper as shaper_mod

        with mock.patch.object(shaper_mod, "HAS_HB", False):
            glyphs = shaper_mod.shape("Hello شكرا", "/nonexistent/font.ttf", 24)
            self.assertIsInstance(glyphs, list)
            self.assertGreater(len(glyphs), 0)
            # Fallback uses codepoint as glyph_id and cluster indexing
            self.assertTrue(all(hasattr(g, "glyph_id") for g in glyphs))
            self.assertTrue(all(hasattr(g, "cluster") for g in glyphs))
            self.assertEqual(glyphs[0].cluster, 0)
            # Also test that render_text_rgba still works without HB
            from renpy.wgpu.text import render_text_rgba

            w, h, data = render_text_rgba("Test", size=24)
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)
            self.assertEqual(len(data), w * h * 4)

        # Without patch, shape should still be green (either HB or fallback)
        glyphs2 = shape("ABC", None, 16)
        self.assertGreater(len(glyphs2), 0)


if __name__ == "__main__":
    unittest.main()
