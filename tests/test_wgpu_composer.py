"""Unit tests for renpy.wgpu.composer and native bridge integration contracts."""

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


if __name__ == "__main__":
    unittest.main()
