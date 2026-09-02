"""Unit tests for renpy.wgpu.composer and native bridge integration contracts.

Thresholds (MAE gate, ADR §4.3.1 — docs/aegis/baseline/wgpu-golden-baseline.md):
  mean MAE ≤ 2/255 (≈0.007843), max channel delta ≤ 16, fail-closed.
  Baseline policy: first baselines from wgpu after visual QA; lavapipe CI
  may use separate tolerance tier if documented (do not silent-resign).
  Capture is pre-present game RT (Rgba8Unorm+PMA, One/OneMinusSrcAlpha).
"""

import unittest
from renpy.wgpu.composer import (
    ComposerError,
    ComposerResult,
    compose_shader,
    get_shader_cache,
    register_shader_part,
)
from renpy.wgpu.shaders import register_builtin_core


class TestWgpuComposerIntegration(unittest.TestCase):
    def setUp(self):
        register_builtin_core()

    def test_composer_result_unpacking_and_aliases(self):
        res = ComposerResult(
            pipeline=42,
            key="composed:abc123",
            partnames=["renpy.texture", "renpy.matrixcolor"],
            tex_count=1,
            uniform_layout_id="params16",
            has_uniforms=True,
            wgsl="@vertex fn vs() {} @fragment fn fs() {}",
            residual=None,
        )
        self.assertEqual(res.pipeline_handle, 42)
        self.assertEqual(res.wgsl_source, "@vertex fn vs() {} @fragment fn fs() {}")

        pipe, key, tex_cnt, layout_id, has_u, wgsl_code = res
        self.assertEqual(pipe, 42)
        self.assertEqual(key, "composed:abc123")
        self.assertEqual(tex_cnt, 1)
        self.assertEqual(layout_id, "params16")
        self.assertTrue(has_u)
    def test_custom_shader_part_registration_and_composition(self):
        custom_part = "custom.invert"
        register_shader_part(
            name=custom_part,
            tex_count=0,
            uniform_layout_id="none",
            fragment_hooks=[(400, "color = vec4<f32>(1.0 - color.rgb, color.a);")],
            atomic=False,
            composition_only=False,
        )

        res = compose_shader(["renpy.texture", custom_part], hard_fail=False, has_texture=True)
        self.assertIsNotNone(res)
        self.assertIn("custom.invert", res.partnames)
        self.assertIn("1.0 - color.rgb", res.wgsl)
        self.assertEqual(res.tex_count, 1)

    def test_pipeline_caching_identity(self):
        cache = get_shader_cache()
        res1 = cache.get(["renpy.texture", "renpy.matrixcolor"], hard_fail=False)
        res2 = cache.get(["renpy.matrixcolor", "renpy.texture"], hard_fail=False)
        self.assertIsNotNone(res1)
        self.assertIsNotNone(res2)
        self.assertEqual(res1.key, res2.key)
        self.assertEqual(res1.wgsl, res2.wgsl)

    def test_fail_closed_on_atomic_conflict(self):
        with self.assertRaises(ComposerError):
            compose_shader(["renpy.dissolve", "renpy.imagedissolve"], hard_fail=True)

    def test_create_pipeline_from_parts_contract(self):
        # When renpy_host is not present in pure test environment, soft compose fallback works
        cache = get_shader_cache()
        res = cache.get(["renpy.texture", "renpy.matrixcolor"], hard_fail=False, has_texture=True)
        self.assertIsNotNone(res)
        pipe, key, tex_cnt, layout_id, has_u, wgsl_code = res
        self.assertTrue(isinstance(pipe, int))
        self.assertTrue(isinstance(key, str))
        self.assertEqual(layout_id, "matrixcolor16")
        self.assertIn("@fragment", wgsl_code)

    def test_pipeline_map_honesty(self):
        from renpy.wgpu.shaders import assert_pipeline_map_honest
        problems = assert_pipeline_map_honest()
        self.assertEqual(problems, [], f"assert_pipeline_map_honest reported violations: {problems}")

    def test_snippet_ir_registrations_completeness(self):
        from renpy.wgpu.shaders import get_snippet_ir, is_atomic, is_mergeable

        # blur
        blur_ir = get_snippet_ir("renpy.blur")
        self.assertIsNotNone(blur_ir)
        self.assertEqual(blur_ir["tex_count"], 1)
        self.assertEqual(blur_ir["uniform_layout_id"], "params16")
        self.assertFalse(blur_ir["atomic"])
        self.assertTrue(is_mergeable("renpy.blur"))
        self.assertIn("0.29411765", blur_ir["fragment_hooks"][0]["body"])
        self.assertIn("0.17647059", blur_ir["fragment_hooks"][0]["body"])
        self.assertNotIn("norm = norm", blur_ir["fragment_hooks"][0]["body"])

        # matrixcolor
        mc_ir = get_snippet_ir("renpy.matrixcolor")
        self.assertIsNotNone(mc_ir)
        self.assertEqual(mc_ir["tex_count"], 1)
        self.assertEqual(mc_ir["uniform_layout_id"], "matrixcolor16")
        self.assertFalse(mc_ir["atomic"])
        self.assertTrue(is_mergeable("renpy.matrixcolor"))

        # dissolve & imagedissolve (atomic transitions)
        dissolve_ir = get_snippet_ir("renpy.dissolve")
        self.assertIsNotNone(dissolve_ir)
        self.assertEqual(dissolve_ir["tex_count"], 2)
        self.assertEqual(dissolve_ir["uniform_layout_id"], "params16")
        self.assertTrue(dissolve_ir["atomic"])
        self.assertTrue(is_atomic("renpy.dissolve"))

        idissolve_ir = get_snippet_ir("renpy.imagedissolve")
        self.assertIsNotNone(idissolve_ir)
        self.assertEqual(idissolve_ir["tex_count"], 3)
        self.assertEqual(idissolve_ir["uniform_layout_id"], "params16")
        self.assertTrue(idissolve_ir["atomic"])
        self.assertTrue(is_atomic("renpy.imagedissolve"))

        # alpha_mask & mask
        alpha_mask_ir = get_snippet_ir("renpy.alpha_mask")
        self.assertIsNotNone(alpha_mask_ir)
        self.assertEqual(alpha_mask_ir["tex_count"], 2)
        self.assertEqual(alpha_mask_ir["uniform_layout_id"], "none")
        self.assertTrue(alpha_mask_ir["atomic"])

        mask_ir = get_snippet_ir("renpy.mask")
        self.assertIsNotNone(mask_ir)
        self.assertEqual(mask_ir["tex_count"], 2)
        self.assertEqual(mask_ir["uniform_layout_id"], "params16")
        self.assertTrue(mask_ir["atomic"])

        # live2d.*
        l2d_mask = get_snippet_ir("live2d.mask")
        self.assertIsNotNone(l2d_mask)
        self.assertEqual(l2d_mask["tex_count"], 2)
        self.assertEqual(l2d_mask["uniform_layout_id"], "params16")
        self.assertTrue(l2d_mask["atomic"])

        l2d_inv = get_snippet_ir("live2d.inverted_mask")
        self.assertIsNotNone(l2d_inv)
        self.assertEqual(l2d_inv["tex_count"], 2)
        self.assertEqual(l2d_inv["uniform_layout_id"], "params16")
        self.assertTrue(l2d_inv["atomic"])

        l2d_colors = get_snippet_ir("live2d.colors")
        self.assertIsNotNone(l2d_colors)
        self.assertEqual(l2d_colors["tex_count"], 1)
        self.assertEqual(l2d_colors["uniform_layout_id"], "params16")
        self.assertTrue(l2d_colors["atomic"])

        l2d_flip = get_snippet_ir("live2d.flip_texture")
        self.assertIsNotNone(l2d_flip)
        self.assertEqual(l2d_flip["tex_count"], 1)
        self.assertEqual(l2d_flip["uniform_layout_id"], "none")

        # text_sdf
        sdf_ir = get_snippet_ir("renpy.text_sdf")
        self.assertIsNotNone(sdf_ir)
        self.assertEqual(sdf_ir["tex_count"], 1)
        self.assertEqual(sdf_ir["uniform_layout_id"], "params16")
        self.assertEqual(sdf_ir["pipeline"], "text_sdf_pipeline")


if __name__ == "__main__":
    unittest.main()
