"""Milestone 2 tests: Multi-texture Dissolve, ImageDissolve ATL node detection, and MatrixColor packing."""

import unittest
from pathlib import Path
from renpy.wgpu.draw import WgpuDraw
from renpy.wgpu.draw_surftree import SurftreeMixin
from renpy.wgpu.draw_pipeline import PipelineMixin
from renpy.wgpu.shaders import register_builtin_core, host_pipeline_key


class _MockNode:
    def __init__(self, shaders=None, uniforms=None, operation=None):
        self.shaders = shaders
        self.uniforms = uniforms
        self.operation = operation
        self.children = []
        self.mesh = None


class TestM2DissolveAndSurftree(unittest.TestCase):
    def setUp(self):
        register_builtin_core()
        self.draw = WgpuDraw()

    def test_is_imagedissolve_node_with_single_animation_uniform(self):
        """Verify that ATL nodes animating only u_animation across keyframes are detected."""
        # Keyframe with both uniforms
        node_both = _MockNode(uniforms={"u_transition": 0.2, "u_animation": 0.5})
        self.assertTrue(self.draw._is_imagedissolve_node(node_both))

        # Keyframe with ONLY u_animation (the core M2 bug fix)
        node_anim_only = _MockNode(uniforms={"u_animation": 0.75})
        self.assertTrue(self.draw._is_imagedissolve_node(node_anim_only))

        # Keyframe with ONLY u_transition
        node_trans_only = _MockNode(uniforms={"u_transition": 0.2})
        self.assertTrue(self.draw._is_imagedissolve_node(node_trans_only))

    def test_is_imagedissolve_node_distinguishes_plain_dissolve(self):
        """Plain dissolve uniforms (u_renpy_dissolve) must not be treated as imagedissolve."""
        node_plain = _MockNode(uniforms={"u_renpy_dissolve": 0.5})
        self.assertFalse(self.draw._is_imagedissolve_node(node_plain))
        self.assertTrue(self.draw._is_dissolve_node(node_plain))

    def test_is_imagedissolve_node_shader_and_operation_stamps(self):
        """Shader names and operation enum values for imagedissolve."""
        node_shader1 = _MockNode(shaders=("image_dissolve",))
        self.assertTrue(self.draw._is_imagedissolve_node(node_shader1))

        node_shader2 = _MockNode(shaders=("renpy.imagedissolve",))
        self.assertTrue(self.draw._is_imagedissolve_node(node_shader2))

        node_op_str = _MockNode(operation="IMAGEDISSOLVE")
        self.assertTrue(self.draw._is_imagedissolve_node(node_op_str))

        node_op_int = _MockNode(operation=2)
        self.assertTrue(self.draw._is_imagedissolve_node(node_op_int))

    def test_image_dissolve_alias_uniform_packing(self):
        """Verify product alias u_animation is packed to offset/multiplier with red channel enabled."""
        uniforms = {"u_transition": 0.2, "u_animation": 0.5}
        packed = self.draw._pack_uniforms(uniforms, ("image_dissolve",))
        self.assertIsNotNone(packed)
        self.assertEqual(len(packed), 16)
        # data0.x = -1.0 + 2.0 * 0.5 = 0.0
        self.assertAlmostEqual(packed[0], 0.0, places=5)
        # data0.y = 1.0 (multiplier)
        self.assertAlmostEqual(packed[1], 1.0, places=5)
        # data0.z = 1.0 (use_red channel enabled)
        self.assertAlmostEqual(packed[2], 1.0, places=5)

    def test_dissolve_wgsl_source_invariants_in_arena(self):
        """Verify Rust arena.rs source has corrected early-return branches."""
        arena_rs_path = Path(__file__).resolve().parent.parent / "host" / "renpy-host" / "src" / "arena.rs"
        content = arena_rs_path.read_text(encoding="utf-8")

        # Find DISSOLVE_WGSL definition
        self.assertIn("const DISSOLVE_WGSL: &str", content)

        # Check early return logic
        dissolve_section = content[content.find("const DISSOLVE_WGSL"):content.find("const IMAGEDISSOLVE_WGSL")]
        self.assertIn("if (amt <= 0.0) {", dissolve_section)
        self.assertIn("let c0 = textureSample(t_old, s_color, v.uv) * v.color;", dissolve_section)
        self.assertIn("if (amt >= 1.0) {", dissolve_section)
        self.assertIn("let c1 = textureSample(t_new, s_color, v.uv) * v.color;", dissolve_section)

    def test_pipeline_mappings_for_m2_shaders(self):
        """Ensure shader registry correctly resolves M2 pipelines."""
        self.assertEqual(host_pipeline_key("image_dissolve"), "imagedissolve_pipeline")
        self.assertEqual(host_pipeline_key("renpy.imagedissolve"), "imagedissolve_pipeline")
        self.assertEqual(host_pipeline_key("renpy.dissolve"), "dissolve_pipeline")
        self.assertEqual(host_pipeline_key("renpy.matrixcolor"), "matrixcolor_pipeline")
