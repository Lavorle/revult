"""Adversarial challenger tests for Milestone 2: DISSOLVE_WGSL boundaries, rule transitions, and shader packing."""

import unittest
from pathlib import Path
from renpy.wgpu.draw import WgpuDraw
from renpy.wgpu.shaders import register_builtin_core, host_pipeline_key


class _MockRenderNode:
    def __init__(self, shaders=None, uniforms=None, operation=None):
        self.shaders = shaders
        self.uniforms = uniforms
        self.operation = operation
        self.children = []
        self.mesh = None


class TestM2ChallengerEmpirical(unittest.TestCase):
    def setUp(self):
        register_builtin_core()
        self.draw = WgpuDraw()

    def test_dissolve_uniform_packing_boundaries(self):
        """Verify uniform packing for DISSOLVE boundaries [-10.0 .. 10.0]."""
        test_values = [-10.0, -1.0, 0.0, 0.001, 0.5, 0.999, 1.0, 2.0, 10.0]
        for val in test_values:
            packed = self.draw._pack_uniforms({"u_renpy_dissolve": val}, ("renpy.dissolve",))
            self.assertIsNotNone(packed)
            self.assertEqual(len(packed), 16)
            self.assertAlmostEqual(packed[0], val, places=5)

    def test_imagedissolve_alias_packing_boundaries(self):
        """Verify uniform packing for image_dissolve product alias with edge animation keyframes and clamping."""
        anim_values = [-1.0, 0.0, 0.001, 0.5, 0.999, 1.0, 2.0]
        for anim in anim_values:
            packed = self.draw._pack_uniforms(
                {"u_transition": 0.2, "u_animation": anim},
                ("image_dissolve",)
            )
            self.assertIsNotNone(packed)
            self.assertEqual(len(packed), 16)
            # data0.x = -1.0 + 2.0 * clamp(anim, 0.0, 1.0)
            clamped_anim = max(0.0, min(1.0, anim))
            expected_offset = -1.0 + 2.0 * clamped_anim
            self.assertAlmostEqual(packed[0], expected_offset, places=5)
            # data0.y = 1.0 (multiplier)
            self.assertAlmostEqual(packed[1], 1.0, places=5)
            # data0.z = 1.0 (red channel)
            self.assertAlmostEqual(packed[2], 1.0, places=5)

    def test_atl_node_uniform_variations(self):
        """Test variations of ATL node uniform key presence."""
        # 1. only u_animation
        node1 = _MockRenderNode(uniforms={"u_animation": 0.5})
        self.assertTrue(self.draw._is_imagedissolve_node(node1))

        # 2. only u_transition
        node2 = _MockRenderNode(uniforms={"u_transition": 0.2})
        self.assertTrue(self.draw._is_imagedissolve_node(node2))

        # 3. standard dissolve offset / multiplier
        node3 = _MockRenderNode(uniforms={"u_renpy_dissolve_offset": -0.5, "u_renpy_dissolve_multiplier": 2.0})
        self.assertTrue(self.draw._is_imagedissolve_node(node3))

        # 4. plain dissolve should not match imagedissolve
        node4 = _MockRenderNode(uniforms={"u_renpy_dissolve": 0.5})
        self.assertFalse(self.draw._is_imagedissolve_node(node4))

    def test_all_m2_pipelines_registered(self):
        """Verify all M2 pipelines are properly registered in the host pipeline registry."""
        self.assertEqual(host_pipeline_key("renpy.dissolve"), "dissolve_pipeline")
        self.assertEqual(host_pipeline_key("renpy.imagedissolve"), "imagedissolve_pipeline")
        self.assertEqual(host_pipeline_key("image_dissolve"), "imagedissolve_pipeline")
        self.assertEqual(host_pipeline_key("renpy.matrixcolor"), "matrixcolor_pipeline")
