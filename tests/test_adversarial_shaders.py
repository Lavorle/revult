"""Adversarial stress tests for renpy.wgpu.composer, shaders, and arithmetic edge cases."""

import unittest
from renpy.wgpu.composer import (
    ComposerError,
    ComposerResult,
    compose_shader,
    emit_wgsl,
    get_shader_cache,
    register_shader_part,
    _cache_key,
    _normalize_partnames,
)
from renpy.wgpu.shaders import register_builtin_core, assert_pipeline_map_honest


class TestAdversarialShaders(unittest.TestCase):
    def setUp(self):
        register_builtin_core()

    def test_pipeline_map_honesty_preserved(self):
        """Verify that Snippet IR pipeline honesty is strictly maintained."""
        assert_pipeline_map_honest()

    def test_deterministic_cache_keys_under_shuffling(self):
        """Verify that cache key is completely invariant to part ordering and duplicates after normalization."""
        parts_1 = ["renpy.matrixcolor", "renpy.texture", "renpy.ftl"]
        parts_2 = ["renpy.ftl", "renpy.matrixcolor", "renpy.texture"]
        parts_3 = ["renpy.texture", "renpy.ftl", "renpy.matrixcolor", "renpy.matrixcolor"]

        k1 = _cache_key(_normalize_partnames(parts_1))
        k2 = _cache_key(_normalize_partnames(parts_2))
        k3 = _cache_key(_normalize_partnames(parts_3))

        self.assertEqual(k1, k2)
        self.assertEqual(k1, k3)
        self.assertTrue(k1.startswith("composed:"))

    def test_adversarial_custom_parts_with_extreme_priorities(self):
        """Test shader generation with extreme priorities (-1000 to +10000)."""
        register_shader_part(
            name="custom.p_neg",
            tex_count=0,
            uniform_layout_id="none",
            fragment_hooks=[(-500, "/* early hook */ color = color + vec4<f32>(0.1);")],
            atomic=False,
            composition_only=False,
        )
        register_shader_part(
            name="custom.p_pos",
            tex_count=0,
            uniform_layout_id="none",
            fragment_hooks=[(5000, "/* late hook */ color = clamp(color, vec4(0.0), vec4(1.0));")],
            atomic=False,
            composition_only=False,
        )

        res = compose_shader(["renpy.texture", "custom.p_pos", "custom.p_neg"], hard_fail=False, has_texture=True)
        self.assertIsNotNone(res)
        wgsl = res.wgsl
        idx_early = wgsl.find("/* early hook */")
        idx_late = wgsl.find("/* late hook */")
        self.assertNotEqual(idx_early, -1)
        self.assertNotEqual(idx_late, -1)
        self.assertTrue(idx_early < idx_late, "Early hook must appear before late hook in generated WGSL")

    def test_conflicting_uniform_layouts_rejected(self):
        """Test that parts requesting conflicting uniform layouts raise ComposerError."""
        register_shader_part(
            name="custom.layout_a",
            tex_count=0,
            uniform_layout_id="layout_a_64b",
            fragment_hooks=[(100, "color = color;")],
            atomic=False,
            composition_only=False,
        )
        register_shader_part(
            name="custom.layout_b",
            tex_count=0,
            uniform_layout_id="layout_b_64b",
            fragment_hooks=[(100, "color = color;")],
            atomic=False,
            composition_only=False,
        )

        with self.assertRaises(ComposerError) as ctx:
            compose_shader(["custom.layout_a", "custom.layout_b"], hard_fail=True, has_texture=False)
        self.assertIn("conflicting uniform layouts", str(ctx.exception))

    def test_atomic_parts_rejected_from_multi_composition(self):
        """Atomic parts (dissolve, imagedissolve) must raise ComposerError if combined."""
        with self.assertRaises(ComposerError) as ctx:
            compose_shader(["renpy.dissolve", "renpy.matrixcolor"], hard_fail=True, has_texture=True)
        self.assertIn("atomic", str(ctx.exception).lower())

        with self.assertRaises(ComposerError) as ctx:
            compose_shader(["renpy.imagedissolve", "renpy.blur"], hard_fail=True, has_texture=True)
        self.assertIn("atomic", str(ctx.exception).lower())

    def test_wgsl_pma_and_clamp_invariants(self):
        """Verify that emitted WGSL strictly clamps alpha and performs pre-multiplied alpha calculation."""
        res = compose_shader(["renpy.texture"], hard_fail=False, has_texture=True)
        self.assertIsNotNone(res)
        wgsl = res.wgsl
        self.assertIn("clamp(color.a, 0.0, 1.0)", wgsl)
        self.assertIn("vec4<f32>(color.rgb * a, a)", wgsl)
