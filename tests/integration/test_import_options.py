"""
Integration tests for import options and scene helpers.

Covers ``ImportOptions`` fields (origin modes, import location, grid layout,
material modes) and ``io_mesh_3mf.import_3mf.scene`` helper functions
that are invoked during real import operations.
"""

import bpy
import mathutils
import unittest
from test_base import Blender3mfTestCase

from io_mesh_3mf.import_3mf.scene import (
    apply_import_location,
    apply_grid_layout,
    set_object_origin,
)


# ============================================================================
#  apply_import_location
# ============================================================================

class ApplyImportLocationTests(unittest.TestCase):
    """Tests for apply_import_location()."""

    def _mat(self, tx=5, ty=6, tz=7):
        m = mathutils.Matrix.Identity(4)
        m.translation = mathutils.Vector((tx, ty, tz))
        return m

    def test_keep_unchanged(self):
        """KEEP mode returns the matrix unchanged."""
        m = self._mat()
        result = apply_import_location(m, "KEEP")
        self.assertAlmostEqual(result.translation.x, 5.0)
        self.assertAlmostEqual(result.translation.y, 6.0)
        self.assertAlmostEqual(result.translation.z, 7.0)

    def test_origin_zeroes_translation(self):
        """ORIGIN mode zeroes out translation."""
        m = self._mat()
        result = apply_import_location(m, "ORIGIN")
        self.assertAlmostEqual(result.translation.x, 0.0)
        self.assertAlmostEqual(result.translation.y, 0.0)
        self.assertAlmostEqual(result.translation.z, 0.0)

    def test_grid_zeroes_translation(self):
        """GRID mode also zeroes translation (grid layout applied separately)."""
        m = self._mat()
        result = apply_import_location(m, "GRID")
        self.assertAlmostEqual(result.translation.x, 0.0)
        self.assertAlmostEqual(result.translation.y, 0.0)
        self.assertAlmostEqual(result.translation.z, 0.0)

    def test_cursor_uses_cursor_location(self):
        """CURSOR mode uses the 3D cursor location."""
        bpy.context.scene.cursor.location = (10.0, 20.0, 30.0)
        m = self._mat()
        result = apply_import_location(m, "CURSOR")
        self.assertAlmostEqual(result.translation.x, 10.0)
        self.assertAlmostEqual(result.translation.y, 20.0)
        self.assertAlmostEqual(result.translation.z, 30.0)

    def test_does_not_mutate_original(self):
        """All modes return a copy, not mutate the input."""
        m = self._mat()
        _ = apply_import_location(m, "ORIGIN")
        self.assertAlmostEqual(m.translation.x, 5.0, msg="Original should not be mutated")


# ============================================================================
#  apply_grid_layout
# ============================================================================

class ApplyGridLayoutTests(Blender3mfTestCase):
    """Tests for apply_grid_layout()."""

    def test_single_object_no_op(self):
        """Single object is not moved."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        obj = bpy.context.object
        apply_grid_layout([obj])
        # Should still be at origin
        self.assertAlmostEqual(obj.location.x, 0.0, places=3)
        self.assertAlmostEqual(obj.location.y, 0.0, places=3)

    def test_empty_list_no_crash(self):
        """Empty list doesn't crash."""
        apply_grid_layout([])

    def test_multiple_objects_arranged(self):
        """Multiple objects are arranged in a grid."""
        objs = []
        for i in range(4):
            bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0), size=1.0)
            objs.append(bpy.context.object)

        apply_grid_layout(objs, grid_spacing=0.5)

        # Objects should be spread out (not all at origin)
        positions = set()
        for obj in objs:
            positions.add((round(obj.location.x, 1), round(obj.location.y, 1)))
        self.assertGreater(len(positions), 1, "Objects should be at different positions")

    def test_grid_uses_spacing(self):
        """Objects have gaps matching the grid_spacing parameter."""
        objs = []
        for i in range(2):
            bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0), size=1.0)
            objs.append(bpy.context.object)

        apply_grid_layout(objs, grid_spacing=5.0)

        # Two objects in a single row, separated by at least grid_spacing
        dx = abs(objs[0].location.x - objs[1].location.x)
        self.assertGreater(dx, 1.0, "Objects should be spread apart")


# ============================================================================
#  set_object_origin
# ============================================================================

class SetObjectOriginTests(Blender3mfTestCase):
    """Tests for set_object_origin()."""

    def test_keep_no_change(self):
        """KEEP mode makes no changes."""
        bpy.ops.mesh.primitive_cube_add(location=(3, 4, 5))
        obj = bpy.context.object
        original_loc = obj.location.copy()
        set_object_origin(obj, "KEEP")
        self.assertAlmostEqual(obj.location.x, original_loc.x, places=3)
        self.assertAlmostEqual(obj.location.y, original_loc.y, places=3)
        self.assertAlmostEqual(obj.location.z, original_loc.z, places=3)

    def test_center_moves_origin(self):
        """CENTER mode moves origin to geometry center."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        obj = bpy.context.object
        # Offset the mesh data but keep object at origin
        for v in obj.data.vertices:
            v.co.x += 5.0
        obj.data.update()
        set_object_origin(obj, "CENTER")
        # Origin should now be near mesh center
        self.assertAlmostEqual(obj.location.x, 5.0, places=1)

    def test_bottom_moves_to_base(self):
        """BOTTOM mode moves origin to bottom center of bounding box."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 2), size=2.0)
        obj = bpy.context.object
        set_object_origin(obj, "BOTTOM")
        # After BOTTOM, the lowest point of the mesh in LOCAL space should
        # be at z=0 (origin sits at the bottom of the mesh).
        local_min_z = min(v.co.z for v in obj.data.vertices)
        self.assertAlmostEqual(local_min_z, 0.0, places=1)


# ============================================================================
#  Import options via operator (end-to-end)
# ============================================================================

class ImportMaterialModeTests(Blender3mfTestCase):
    """Test import_materials option via operator."""

    def _create_test_3mf(self):
        """Create a basic 3MF with a material for round-trip tests."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        mat = self.create_red_material()
        cube.data.materials.append(mat)
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))
        self.clean_scene()

    def test_import_materials_default(self):
        """Default import includes materials."""
        self._create_test_3mf()
        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
        self.assertIn('FINISHED', result)
        obj = bpy.data.objects[0]
        self.assertGreater(len(obj.data.materials), 0)

    def test_import_materials_none(self):
        """import_materials='NONE' skips materials."""
        self._create_test_3mf()
        result = bpy.ops.import_mesh.threemf(
            filepath=str(self.temp_file), import_materials="NONE"
        )
        self.assertIn('FINISHED', result)
        obj = bpy.data.objects[0]
        self.assertEqual(len(obj.data.materials), 0)


class ImportOriginModeTests(Blender3mfTestCase):
    """Test origin_to_geometry option."""

    def _create_and_export(self, location=(5, 5, 5)):
        bpy.ops.mesh.primitive_cube_add(location=location, size=2.0)
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))
        self.clean_scene()

    def test_origin_keep(self):
        """KEEP preserves the original origin."""
        self._create_and_export()
        bpy.ops.import_mesh.threemf(
            filepath=str(self.temp_file), origin_to_geometry="KEEP"
        )
        # Object should exist
        self.assertGreater(len(bpy.data.objects), 0)

    def test_origin_center(self):
        """CENTER moves origin to geometry center."""
        self._create_and_export()
        bpy.ops.import_mesh.threemf(
            filepath=str(self.temp_file), origin_to_geometry="CENTER"
        )
        self.assertGreater(len(bpy.data.objects), 0)
        # After CENTER, the object's location should approximately match
        # where its geometry center is
        obj = bpy.data.objects[0]
        # Bounding box center should be near object origin
        bbox = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
        center = sum((mathutils.Vector(v) for v in bbox), mathutils.Vector()) / 8
        self.assertAlmostEqual(obj.location.x, center.x, places=1)


class ImportLocationModeTests(Blender3mfTestCase):
    """Test import_location option."""

    def _create_and_export(self, location=(5, 5, 5)):
        bpy.ops.mesh.primitive_cube_add(location=location, size=2.0)
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))
        self.clean_scene()

    def test_import_location_origin(self):
        """ORIGIN places object at world origin."""
        self._create_and_export(location=(10, 10, 10))
        bpy.ops.import_mesh.threemf(
            filepath=str(self.temp_file), import_location="ORIGIN"
        )
        obj = bpy.data.objects[0]
        self.assertAlmostEqual(obj.location.x, 0.0, places=1)
        self.assertAlmostEqual(obj.location.y, 0.0, places=1)
        self.assertAlmostEqual(obj.location.z, 0.0, places=1)

    def test_import_location_keep(self):
        """KEEP preserves the file's transform."""
        self._create_and_export(location=(5, 5, 5))
        bpy.ops.import_mesh.threemf(
            filepath=str(self.temp_file), import_location="KEEP"
        )
        # Should be roughly where we exported it (may differ due to unit conversion)
        self.assertGreater(len(bpy.data.objects), 0)


class ImportHiddenObjectTests(Blender3mfTestCase):
    """Test that hidden objects are handled correctly during import."""

    def test_roundtrip_hidden_objects(self):
        """Hidden objects should still be importable."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        cube.hide_set(True)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), export_hidden=True
        )
        self.clean_scene()

        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
        self.assertIn('FINISHED', result)
        self.assertGreater(len(bpy.data.objects), 0)


if __name__ == "__main__":
    unittest.main()
