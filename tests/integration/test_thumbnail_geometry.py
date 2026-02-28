"""
Integration tests for thumbnail helper functions.

Covers ``io_mesh_3mf.export_3mf.thumbnail._compute_world_bbox`` with
various object setups. The existing test_thumbnail.py covers operator
and API paths; this file tests the geometry helper directly.
"""

import bpy
import unittest
from test_base import Blender3mfTestCase

from io_mesh_3mf.export_3mf.thumbnail import _compute_world_bbox


class ComputeWorldBboxTests(Blender3mfTestCase):
    """Tests for _compute_world_bbox()."""

    def test_single_cube_at_origin(self):
        """Bounding box of a unit cube at the origin."""
        bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
        cube = bpy.context.object

        bb_min, bb_max = _compute_world_bbox([cube])

        self.assertIsNotNone(bb_min)
        self.assertIsNotNone(bb_max)
        self.assertAlmostEqual(bb_min.x, -1.0, places=3)
        self.assertAlmostEqual(bb_min.y, -1.0, places=3)
        self.assertAlmostEqual(bb_min.z, -1.0, places=3)
        self.assertAlmostEqual(bb_max.x, 1.0, places=3)
        self.assertAlmostEqual(bb_max.y, 1.0, places=3)
        self.assertAlmostEqual(bb_max.z, 1.0, places=3)

    def test_translated_object(self):
        """Bounding box accounts for world-space translation."""
        bpy.ops.mesh.primitive_cube_add(size=2, location=(10, 20, 30))
        cube = bpy.context.object
        # Force depsgraph update
        bpy.context.view_layer.update()

        bb_min, bb_max = _compute_world_bbox([cube])

        self.assertAlmostEqual(bb_min.x, 9.0, places=3)
        self.assertAlmostEqual(bb_max.x, 11.0, places=3)
        self.assertAlmostEqual(bb_min.y, 19.0, places=3)
        self.assertAlmostEqual(bb_max.y, 21.0, places=3)

    def test_multiple_objects(self):
        """Bounding box encloses all objects."""
        bpy.ops.mesh.primitive_cube_add(size=2, location=(-5, 0, 0))
        cube1 = bpy.context.object
        bpy.ops.mesh.primitive_cube_add(size=2, location=(5, 0, 0))
        cube2 = bpy.context.object
        bpy.context.view_layer.update()

        bb_min, bb_max = _compute_world_bbox([cube1, cube2])

        self.assertAlmostEqual(bb_min.x, -6.0, places=3)
        self.assertAlmostEqual(bb_max.x, 6.0, places=3)

    def test_empty_list_returns_none(self):
        """No objects returns (None, None)."""
        bb_min, bb_max = _compute_world_bbox([])
        self.assertIsNone(bb_min)
        self.assertIsNone(bb_max)

    def test_none_uses_visible_scene_objects(self):
        """None input uses visible scene objects."""
        bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
        bpy.context.view_layer.update()

        bb_min, bb_max = _compute_world_bbox(None)
        # Should find the cube
        self.assertIsNotNone(bb_min)

    def test_non_mesh_objects_skipped(self):
        """Non-mesh objects (lights, cameras) are skipped."""
        bpy.ops.object.light_add(type='POINT', location=(0, 0, 0))
        light = bpy.context.object
        bpy.ops.object.camera_add(location=(5, 5, 5))
        camera = bpy.context.object

        bb_min, bb_max = _compute_world_bbox([light, camera])
        self.assertIsNone(bb_min)

    def test_scaled_object(self):
        """Bounding box accounts for object scale."""
        bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
        cube = bpy.context.object
        cube.scale = (3, 3, 3)
        bpy.context.view_layer.update()

        bb_min, bb_max = _compute_world_bbox([cube])

        self.assertAlmostEqual(bb_min.x, -3.0, places=2)
        self.assertAlmostEqual(bb_max.x, 3.0, places=2)


if __name__ == "__main__":
    unittest.main()
