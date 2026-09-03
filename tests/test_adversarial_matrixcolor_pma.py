"""Adversarial tests for MatrixColor pipelines, combinations, and Premultiplied Alpha (PMA) math."""

import math
import textwrap
import unittest
from pathlib import Path

from renpy.color import Color
from renpy.display.matrix import Matrix
from renpy.wgpu import shaders as sh
from renpy.wgpu.composer import compose_shader, get_shader_cache
from renpy.wgpu.draw_pipeline import PipelineMixin


class TestAdversarialMatrixColorPMA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load 00matrixcolor.rpy definitions dynamically
        root = Path(__file__).resolve().parent.parent
        code = (root / "renpy" / "common" / "00matrixcolor.rpy").read_text(encoding="utf-8")
        pycode = code[code.find("init -1500 python:") + len("init -1500 python:"):]
        cls.ns = {
            "Matrix": Matrix,
            "Color": Color,
            "DictEquality": object,
            "math": math,
        }
        exec(textwrap.dedent(pycode), cls.ns)
        sh.register_builtin_core()
        cls.pm = PipelineMixin()

    def _to_floats(self, m):
        if hasattr(m, "__call__"):
            m = m(None, 1.0)
        return self.pm._matrix_to_floats(m)

    def _cpu_transform(self, mat_16, r, g, b, a):
        """CPU reference: straight RGBA transformed by col-major mat4x4, clamped & PMA output."""
        c_r = mat_16[0] * r + mat_16[4] * g + mat_16[8] * b + mat_16[12] * a
        c_g = mat_16[1] * r + mat_16[5] * g + mat_16[9] * b + mat_16[13] * a
        c_b = mat_16[2] * r + mat_16[6] * g + mat_16[10] * b + mat_16[14] * a
        c_a = mat_16[3] * r + mat_16[7] * g + mat_16[11] * b + mat_16[15] * a

        ca = max(0.0, min(1.0, c_a))
        out_r = max(0.0, min(1.0, c_r * ca))
        out_g = max(0.0, min(1.0, c_g * ca))
        out_b = max(0.0, min(1.0, c_b * ca))
        return out_r, out_g, out_b, ca

    def test_matrix_to_floats_column_major_layout(self):
        """Verify _matrix_to_floats packs elements into column-major order for WGSL mat4x4(col0..col3)."""
        m = Matrix([
            1.0, 2.0, 3.0, 4.0,
            5.0, 6.0, 7.0, 8.0,
            9.0, 10.0, 11.0, 12.0,
            13.0, 14.0, 15.0, 16.0,
        ])
        floats = self._to_floats(m)
        self.assertEqual(len(floats), 16)
        # col0 = [xdx, ydx, zdx, wdx] = [1, 5, 9, 13]
        self.assertEqual(floats[0:4], [1.0, 5.0, 9.0, 13.0])
        # col1 = [xdy, ydy, zdy, wdy] = [2, 6, 10, 14]
        self.assertEqual(floats[4:8], [2.0, 6.0, 10.0, 14.0])
        # col2 = [xdz, ydz, zdz, wdz] = [3, 7, 11, 15]
        self.assertEqual(floats[8:12], [3.0, 7.0, 11.0, 15.0])
        # col3 = [xdw, ydw, zdw, wdw] = [4, 8, 12, 16]
        self.assertEqual(floats[12:16], [4.0, 8.0, 12.0, 16.0])

    def test_dusk_matrix_combination(self):
        """Test DuskMatrix compound matrix properties."""
        TintMatrix = self.ns["TintMatrix"]
        BrightnessMatrix = self.ns["BrightnessMatrix"]
        SaturationMatrix = self.ns["SaturationMatrix"]

        def DuskMatrix(tint="#ffeedd", brightness=-0.1, saturation=0.8):
            return TintMatrix(tint) * BrightnessMatrix(brightness) * SaturationMatrix(saturation)

        dusk = DuskMatrix()
        mat_16 = self._to_floats(dusk)
        self.assertEqual(len(mat_16), 16)

        # Apply to white opaque pixel (1.0, 1.0, 1.0, 1.0)
        r, g, b, a = self._cpu_transform(mat_16, 1.0, 1.0, 1.0, 1.0)
        self.assertAlmostEqual(a, 1.0, places=5)
        self.assertTrue(r > g > b, f"Expected warm dusk tint (R > G > B), got R={r}, G={g}, B={b}")
        self.assertTrue(r < 1.0 and g < 1.0 and b < 1.0, "Dusk must be darker than full white")

    def test_sepia_matrix_properties(self):
        """Test SepiaMatrix transforms color to golden sepia tone."""
        SepiaMatrix = self.ns["SepiaMatrix"]
        sepia = SepiaMatrix()
        mat_16 = self._to_floats(sepia)

        # Pure red input (1, 0, 0, 1) -> sepia tone
        r, g, b, a = self._cpu_transform(mat_16, 1.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(a, 1.0, places=5)
        self.assertTrue(r > g > b, "Sepia must produce warm brown-amber tone")

    def test_brightness_matrix_positive_and_negative(self):
        """Test BrightnessMatrix offsets RGB channels uniformly without altering alpha."""
        BrightnessMatrix = self.ns["BrightnessMatrix"]
        bright_pos = BrightnessMatrix(0.2)
        bright_neg = BrightnessMatrix(-0.3)

        mat_pos = self._to_floats(bright_pos)
        mat_neg = self._to_floats(bright_neg)

        # Mid-gray input (0.5, 0.5, 0.5, 1.0)
        r_p, g_p, b_p, a_p = self._cpu_transform(mat_pos, 0.5, 0.5, 0.5, 1.0)
        self.assertAlmostEqual(r_p, 0.7, places=5)
        self.assertAlmostEqual(g_p, 0.7, places=5)
        self.assertAlmostEqual(b_p, 0.7, places=5)
        self.assertAlmostEqual(a_p, 1.0, places=5)

        r_n, g_n, b_n, a_n = self._cpu_transform(mat_neg, 0.5, 0.5, 0.5, 1.0)
        self.assertAlmostEqual(r_n, 0.2, places=5)
        self.assertAlmostEqual(g_n, 0.2, places=5)
        self.assertAlmostEqual(b_n, 0.2, places=5)
        self.assertAlmostEqual(a_n, 1.0, places=5)

    def test_saturation_matrix_grayscale_and_boost(self):
        """Test SaturationMatrix desaturation to luminance and saturation boost."""
        SaturationMatrix = self.ns["SaturationMatrix"]
        sat_zero = SaturationMatrix(0.0)
        sat_boost = SaturationMatrix(2.0)

        mat_zero = self._to_floats(sat_zero)
        mat_boost = self._to_floats(sat_boost)

        # Pure green input (0, 1, 0, 1) -> luminance is 0.7152
        r_z, g_z, b_z, a_z = self._cpu_transform(mat_zero, 0.0, 1.0, 0.0, 1.0)
        self.assertAlmostEqual(r_z, 0.7152, places=4)
        self.assertAlmostEqual(g_z, 0.7152, places=4)
        self.assertAlmostEqual(b_z, 0.7152, places=4)

        # Pure green boosted saturation (boosts green beyond 1.0, clamped to 1.0, suppresses other channels)
        r_b, g_b, b_b, a_b = self._cpu_transform(mat_boost, 0.2, 0.8, 0.2, 1.0)
        self.assertTrue(g_b > 0.8)
        self.assertTrue(r_b < 0.2)

    def test_colorize_matrix_remapping(self):
        """Test ColorizeMatrix maps black and white points accurately."""
        ColorizeMatrix = self.ns["ColorizeMatrix"]
        cm = ColorizeMatrix("#0000ff", "#ff0000")  # black->blue, white->red
        mat_16 = self._to_floats(cm)

        # Black input (0, 0, 0, 1) -> pure blue (0, 0, 1, 1)
        r_k, g_k, b_k, a_k = self._cpu_transform(mat_16, 0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(r_k, 0.0, places=5)
        self.assertAlmostEqual(g_k, 0.0, places=5)
        self.assertAlmostEqual(b_k, 1.0, places=5)
        self.assertAlmostEqual(a_k, 1.0, places=5)

        # White input (1, 1, 1, 1) -> pure red (1, 0, 0, 1)
        r_w, g_w, b_w, a_w = self._cpu_transform(mat_16, 1.0, 1.0, 1.0, 1.0)
        self.assertAlmostEqual(r_w, 1.0, places=5)
        self.assertAlmostEqual(g_w, 0.0, places=5)
        self.assertAlmostEqual(b_w, 0.0, places=5)
        self.assertAlmostEqual(a_w, 1.0, places=5)

    def test_tint_matrix_channel_scaling(self):
        """Test TintMatrix scales RGB channels by target color."""
        TintMatrix = self.ns["TintMatrix"]
        tm = TintMatrix("#804020")
        mat_16 = self._to_floats(tm)

        r, g, b, a = self._cpu_transform(mat_16, 1.0, 1.0, 1.0, 1.0)
        self.assertAlmostEqual(r, 128 / 255.0, places=4)
        self.assertAlmostEqual(g, 64 / 255.0, places=4)
        self.assertAlmostEqual(b, 32 / 255.0, places=4)

    def test_pma_zero_alpha_invariant_across_all_matrices(self):
        """Adversarial: When alpha is 0.0, PMA output MUST be strictly (0, 0, 0, 0) for ANY matrix."""
        matrices = [
            self.ns["IdentityMatrix"](),
            self.ns["BrightnessMatrix"](1.0),
            self.ns["BrightnessMatrix"](-1.0),
            self.ns["SaturationMatrix"](2.5),
            self.ns["SaturationMatrix"](0.0),
            self.ns["ColorizeMatrix"]("#ffffff", "#ffffff"),
            self.ns["TintMatrix"]("#ff0000"),
            self.ns["ContrastMatrix"](2.0),
            self.ns["InvertMatrix"](1.0),
            self.ns["HueMatrix"](180),
            self.ns["SepiaMatrix"](),
            self.ns["TintMatrix"]("#ffeedd") * self.ns["BrightnessMatrix"](0.5),
        ]
        for m in matrices:
            mat_16 = self._to_floats(m)
            # Test non-zero RGB colors with zero alpha
            for r_in, g_in, b_in in [(1.0, 1.0, 1.0), (1.0, 0.0, 0.0), (0.5, 0.2, 0.8)]:
                out_r, out_g, out_b, out_a = self._cpu_transform(mat_16, r_in, g_in, b_in, 0.0)
                self.assertEqual(out_r, 0.0, f"Leaked red on alpha=0 with matrix {m}")
                self.assertEqual(out_g, 0.0, f"Leaked green on alpha=0 with matrix {m}")
                self.assertEqual(out_b, 0.0, f"Leaked blue on alpha=0 with matrix {m}")
                self.assertEqual(out_a, 0.0, f"Non-zero alpha on alpha=0 with matrix {m}")

    def test_pma_semi_transparent_scaling(self):
        """Verify that semi-transparent pixels scale PMA output exactly proportional to clamped alpha."""
        BrightnessMatrix = self.ns["BrightnessMatrix"]
        bm = BrightnessMatrix(0.5)
        mat_16 = self._to_floats(bm)

        out_r, out_g, out_b, out_a = self._cpu_transform(mat_16, 0.2, 0.4, 0.6, 0.5)
        self.assertAlmostEqual(out_r, 0.225, places=5)
        self.assertAlmostEqual(out_g, 0.325, places=5)
        self.assertAlmostEqual(out_b, 0.425, places=5)
        self.assertAlmostEqual(out_a, 0.5, places=5)

    def test_composer_texture_matrixcolor_emission(self):
        """Verify composer combines texture and matrixcolor into a single WGSL shader."""
        res = compose_shader(["renpy.texture", "renpy.matrixcolor"], hard_fail=False, has_texture=True)
        self.assertIsNotNone(res)
        self.assertEqual(res.uniform_layout_id, "matrixcolor16")
        self.assertTrue(res.has_uniforms)
        self.assertEqual(set(res.partnames), {"renpy.texture", "renpy.matrixcolor"})

        wgsl = res.wgsl
        self.assertIn("struct Params", wgsl)
        self.assertIn("col0: vec4<f32>", wgsl)
        self.assertIn("textureSample(t_color, s_color, v.uv)", wgsl)
        self.assertIn("mat4x4<f32>(u.col0, u.col1, u.col2, u.col3)", wgsl)
        self.assertIn("clamp(color.a, 0.0, 1.0)", wgsl)
        self.assertIn("vec4<f32>(color.rgb * a, a)", wgsl)
