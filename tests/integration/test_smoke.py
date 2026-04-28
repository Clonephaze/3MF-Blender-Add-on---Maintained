"""
Smoke tests for Blender 3MF addon - fast tests for basic functionality.

These tests should run quickly (<1s each) to provide rapid feedback.
"""

import bpy
import unittest
from test_base import Blender3mfTestCase


class SmokeTests(Blender3mfTestCase):
    """Fast smoke tests for basic addon functionality."""

    def test_blender_version(self):
        """Verify we're running in Blender 4.2+."""
        version = bpy.app.version
        self.assertGreaterEqual(version[0], 4, f"Expected Blender 4.x+, got {version}")
        if version[0] == 4:
            self.assertGreaterEqual(
                version[1], 2, f"Expected Blender 4.2+, got {version}"
            )

    def test_addon_can_import(self):
        """Verify the addon module can be imported."""
        import io_mesh_3mf

        self.assertIsNotNone(io_mesh_3mf)
        # Verify key components exist
        self.assertTrue(hasattr(io_mesh_3mf, "register"))
        self.assertTrue(hasattr(io_mesh_3mf, "unregister"))
        self.assertTrue(hasattr(io_mesh_3mf, "Import3MF"))
        self.assertTrue(hasattr(io_mesh_3mf, "Export3MF"))

    def test_export_operator_registered(self):
        """Verify export operator is available."""
        self.assertTrue(hasattr(bpy.ops.export_mesh, "threemf"))

    def test_import_operator_registered(self):
        """Verify import operator is available."""
        self.assertTrue(hasattr(bpy.ops.import_mesh, "threemf"))

    def test_export_simple_cube(self):
        """Test exporting a simple cube - most basic export test."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn("FINISHED", result)
        self.assertTrue(self.temp_file.exists())
        self.assertGreater(self.temp_file.stat().st_size, 0)

    def test_import_basic_file(self):
        """Test importing a basic 3MF file."""
        test_file = self.test_resources_dir / "only_3dmodel_file.3mf"

        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        result = bpy.ops.import_mesh.threemf(filepath=str(test_file))

        self.assertIn("FINISHED", result)

    def test_clean_scene(self):
        """Verify clean_scene works."""
        # Scene should be empty after setUp
        self.assertEqual(len(bpy.data.objects), 0)

        # Add an object
        bpy.ops.mesh.primitive_cube_add()
        self.assertEqual(len(bpy.data.objects), 1)

    def test_material_helpers(self):
        """Verify material helper methods work."""
        red_mat = self.create_red_material()
        blue_mat = self.create_blue_material()

        self.assertEqual(red_mat.name, "RedMaterial")
        self.assertEqual(blue_mat.name, "BlueMaterial")
        self.assertTrue(red_mat.use_nodes)
        self.assertTrue(blue_mat.use_nodes)


if __name__ == "__main__":
    unittest.main()
