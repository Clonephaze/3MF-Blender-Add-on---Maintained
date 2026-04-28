"""
Import tests for Blender 3MF addon using real Blender API.

Covers import functionality without mocking.
"""

import bpy
import unittest
from test_base import Blender3mfTestCase


class ImportBasicTests(Blender3mfTestCase):
    """Basic import functionality tests."""

    def test_import_basic_file(self):
        """Import a basic 3MF file."""
        test_file = self.test_resources_dir / "only_3dmodel_file.3mf"

        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        result = bpy.ops.import_mesh.threemf(filepath=str(test_file))

        self.assertIn("FINISHED", result)

    def test_import_nonexistent_file(self):
        """Import a file that doesn't exist."""
        fake_file = self.temp_file.parent / "nonexistent.3mf"

        # Import operator raises RuntimeError for missing files
        with self.assertRaises(RuntimeError):
            bpy.ops.import_mesh.threemf(filepath=str(fake_file))

    def test_import_corrupt_archive(self):
        """Import corrupt archive - should not crash."""
        test_file = self.test_resources_dir / "corrupt_archive.3mf"

        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        # Import operator raises RuntimeError for corrupt files
        with self.assertRaises(RuntimeError):
            bpy.ops.import_mesh.threemf(filepath=str(test_file))

    def test_import_empty_archive(self):
        """Import empty archive - should not crash."""
        test_file = self.test_resources_dir / "empty_archive.zip"

        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        # Empty archives complete but import nothing
        result = bpy.ops.import_mesh.threemf(filepath=str(test_file))

        self.assertIn("FINISHED", result)


class RoundtripTests(Blender3mfTestCase):
    """Export then import tests."""

    def test_roundtrip_cube(self):
        """Export then import a cube."""
        # Create and export
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        original = bpy.context.object
        original_verts = len(original.data.vertices)
        original_faces = len(original.data.polygons)

        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        # Clear scene
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        # Import back
        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn("FINISHED", result)
        self.assertGreater(len(bpy.data.objects), 0)

        # Verify geometry preserved
        imported = bpy.data.objects[0]
        self.assertEqual(len(imported.data.vertices), original_verts)
        # Face count may differ due to triangulation
        self.assertGreaterEqual(len(imported.data.polygons), original_faces)

    def test_roundtrip_with_material(self):
        """Export then import with material."""
        # Create cube with material
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        mat = self.create_red_material()
        cube.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        # Clear scene
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        # Import back
        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn("FINISHED", result)
        self.assertGreater(len(bpy.data.objects), 0)

        # Verify material imported
        imported = bpy.data.objects[0]
        self.assertGreater(len(imported.data.materials), 0)

    def test_roundtrip_multiple_objects(self):
        """Export then import multiple objects."""
        # Create multiple objects
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        bpy.ops.mesh.primitive_uv_sphere_add(location=(3, 0, 0))
        bpy.ops.mesh.primitive_cone_add(location=(-3, 0, 0))

        original_count = len(bpy.data.objects)

        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        # Clear scene
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        # Import back
        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn("FINISHED", result)
        self.assertEqual(len(bpy.data.objects), original_count)

    def test_roundtrip_preserves_dimensions(self):
        """Verify dimensions preserved through export/import."""
        # Create cube with specific size
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0), size=2.0)
        cube = bpy.context.object
        original_dimensions = tuple(cube.dimensions)

        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        # Clear and reimport
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn("FINISHED", result)

        # Check dimensions (with tolerance for floating point)
        imported = bpy.data.objects[0]
        for i in range(3):
            self.assertAlmostEqual(
                imported.dimensions[i],
                original_dimensions[i],
                places=2,
                msg=f"Dimension {i} not preserved",
            )

    def test_roundtrip_preserves_dimensions_scale_units(self):
        """Verify dimensions preserved through export/import, non-default unit
        / scale."""
        # Create cube with specific size
        bpy.context.scene.unit_settings.length_unit = "MILLIMETERS"
        bpy.context.scene.unit_settings.scale_length = 0.001
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0), size=2.0)
        cube = bpy.context.object
        original_dimensions = tuple(cube.dimensions)

        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        # Clear and reimport
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn("FINISHED", result)

        # Check dimensions (with tolerance for floating point)
        imported = bpy.data.objects[0]
        for i in range(3):
            self.assertAlmostEqual(
                imported.dimensions[i],
                original_dimensions[i],
                places=2,
                msg=f"Dimension {i} not preserved",
            )


class APICompatibilityTests(Blender3mfTestCase):
    """Verify Blender 4.2+ API compatibility."""

    def test_principled_bsdf_wrapper(self):
        """Verify PrincipledBSDFWrapper available."""
        from bpy_extras.node_shader_utils import PrincipledBSDFWrapper

        mat = bpy.data.materials.new("TestMaterial")
        mat.use_nodes = True

        wrapper = PrincipledBSDFWrapper(mat, is_readonly=False)
        self.assertIsNotNone(wrapper)

    def test_depsgraph_evaluated_get(self):
        """Verify depsgraph.objects.get() works."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = depsgraph.objects.get(cube.name)

        self.assertIsNotNone(evaluated)

    def test_mesh_loop_triangles(self):
        """Verify mesh.loop_triangles works."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        mesh = cube.data
        mesh.calc_loop_triangles()

        self.assertGreater(len(mesh.loop_triangles), 0)
        # Cube should have 12 triangles
        self.assertEqual(len(mesh.loop_triangles), 12)


if __name__ == "__main__":
    unittest.main()
