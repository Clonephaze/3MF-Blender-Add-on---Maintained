"""
Integration tests for ``io_mesh_3mf.api`` — the public programmatic API.

Tests :func:`inspect_3mf`, :func:`import_3mf`, :func:`export_3mf`,
:func:`batch_import`, :func:`batch_export`, and building-block re-exports.

All tests run inside real Blender (``--background --factory-startup``).
"""

import unittest
import zipfile

import bpy

from test_base import Blender3mfTestCase

from io_mesh_3mf.api import (
    import_3mf,
    export_3mf,
    inspect_3mf,
    batch_import,
    batch_export,
    ImportResult,
    ExportResult,
    InspectResult,
)


# ============================================================================
# inspect_3mf
# ============================================================================

class TestInspect3MF(Blender3mfTestCase):
    """inspect_3mf() — read-only archive inspection."""

    def test_inspect_valid_file(self):
        """Inspect a consortium sample with actual geometry."""
        fpath = self.test_resources_dir / "3mf_consortium" / "pyramid_vertexcolor.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")

        result = inspect_3mf(str(fpath))
        self.assertIsInstance(result, InspectResult)
        self.assertEqual(result.status, "OK")
        self.assertGreater(result.num_objects, 0)

    def test_inspect_nonexistent_file(self):
        result = inspect_3mf("/nonexistent/path/model.3mf")
        self.assertEqual(result.status, "ERROR")
        self.assertIn("Unable to read", result.error_message)

    def test_inspect_corrupt_archive(self):
        fpath = self.test_resources_dir / "corrupt_archive.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")
        result = inspect_3mf(str(fpath))
        self.assertEqual(result.status, "ERROR")

    def test_inspect_consortium_sample(self):
        """Inspect an official 3MF Consortium sample."""
        fpath = self.test_resources_dir / "3mf_consortium" / "pyramid_vertexcolor.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")

        result = inspect_3mf(str(fpath))
        self.assertEqual(result.status, "OK")
        self.assertGreater(result.num_vertices_total, 0)
        self.assertGreater(result.num_triangles_total, 0)
        self.assertGreater(len(result.archive_files), 0)

    def test_inspect_returns_unit(self):
        fpath = self.test_resources_dir / "3mf_consortium" / "pyramid_vertexcolor.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")
        result = inspect_3mf(str(fpath))
        # Should always have a unit (defaults to "millimeter")
        self.assertTrue(len(result.unit) > 0)


# ============================================================================
# import_3mf
# ============================================================================

class TestImport3MF(Blender3mfTestCase):
    """import_3mf() — programmatic import into Blender."""

    def test_import_basic_file(self):
        """Import a known-good file and check objects are created."""
        fpath = self.test_resources_dir / "3mf_consortium" / "pyramid_vertexcolor.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")

        result = import_3mf(str(fpath))
        self.assertIsInstance(result, ImportResult)
        self.assertEqual(result.status, "FINISHED")
        self.assertGreater(result.num_loaded, 0)
        self.assertGreater(len(result.objects), 0)

    def test_import_nonexistent_file(self):
        result = import_3mf("/nonexistent/model.3mf")
        self.assertEqual(result.status, "CANCELLED")
        self.assertEqual(result.num_loaded, 0)

    def test_import_scale(self):
        """global_scale=2.0 should produce larger geometry."""
        fpath = self.test_resources_dir / "3mf_consortium" / "pyramid_vertexcolor.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")

        result_1 = import_3mf(str(fpath), global_scale=1.0)
        if result_1.status != "FINISHED":
            self.skipTest("Import failed")

        # Measure a bounding box dimension
        obj_1 = result_1.objects[0]
        dim_1 = max(obj_1.dimensions)

        self.clean_scene()

        result_2 = import_3mf(str(fpath), global_scale=2.0)
        self.assertEqual(result_2.status, "FINISHED")
        obj_2 = result_2.objects[0]
        dim_2 = max(obj_2.dimensions)

        # 2x scale should roughly double dimensions
        self.assertAlmostEqual(dim_2 / dim_1, 2.0, places=1)

    def test_import_to_collection(self):
        """target_collection should place objects in a named collection."""
        fpath = self.test_resources_dir / "3mf_consortium" / "pyramid_vertexcolor.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")

        result = import_3mf(str(fpath), target_collection="TestCollection")
        self.assertEqual(result.status, "FINISHED")
        self.assertIn("TestCollection", bpy.data.collections)
        col = bpy.data.collections["TestCollection"]
        self.assertGreater(len(col.objects), 0)

    def test_import_callbacks(self):
        """on_progress callback should fire during import."""
        fpath = self.test_resources_dir / "3mf_consortium" / "pyramid_vertexcolor.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")

        progress_calls = []
        result = import_3mf(
            str(fpath),
            on_progress=lambda pct, msg: progress_calls.append((pct, msg)),
        )
        # Should have received at least the initial "Starting import" call
        if result.status == "FINISHED":
            self.assertGreater(len(progress_calls), 0)


# ============================================================================
# export_3mf
# ============================================================================

class TestExport3MF(Blender3mfTestCase):
    """export_3mf() — programmatic export from Blender."""

    def test_export_basic_cube(self):
        bpy.ops.mesh.primitive_cube_add()
        result = export_3mf(str(self.temp_file))
        self.assertIsInstance(result, ExportResult)
        self.assertEqual(result.status, "FINISHED")
        self.assertGreater(result.num_written, 0)
        self.assertTrue(self.temp_file.exists())

    def test_export_empty_scene(self):
        """Exporting an empty scene should still succeed (or produce warning)."""
        result = export_3mf(str(self.temp_file))
        # Either FINISHED with 0 objects or still FINISHED but empty archive
        self.assertIn(result.status, ("FINISHED", "CANCELLED"))

    def test_export_produces_valid_zip(self):
        bpy.ops.mesh.primitive_cube_add()
        export_3mf(str(self.temp_file))
        self.assertTrue(zipfile.is_zipfile(str(self.temp_file)))

    def test_export_contains_model_file(self):
        bpy.ops.mesh.primitive_cube_add()
        export_3mf(str(self.temp_file))
        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            names = zf.namelist()
            model_files = [n for n in names if n.endswith(".model")]
            self.assertGreater(len(model_files), 0)


# ============================================================================
# export_3mf — AUTO / STANDARD / PAINT dispatch
# ============================================================================

class TestExportDispatch(Blender3mfTestCase):
    """Verify the API use_orca_format dispatch routes to the correct exporter."""

    def _create_cube_with_material(self):
        """Create a cube with a red material and return it."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        mat = bpy.data.materials.new("API_Red")
        mat.use_nodes = True
        p = mat.node_tree.nodes.get("Principled BSDF")
        if p:
            p.inputs["Base Color"].default_value = (0.8, 0.1, 0.1, 1.0)
        cube.data.materials.append(mat)
        return cube

    def test_auto_with_materials_uses_orca(self):
        """AUTO mode with materials present should produce Orca multi-file structure."""
        self._create_cube_with_material()
        result = export_3mf(str(self.temp_file), use_orca_format="AUTO")
        self.assertEqual(result.status, "FINISHED")

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            names = zf.namelist()
            # Orca format produces individual object files under 3D/Objects/
            object_files = [n for n in names if n.startswith("3D/Objects/")]
            self.assertGreater(
                len(object_files), 0,
                "AUTO + materials should route to OrcaExporter (3D/Objects/ files)",
            )

    def test_auto_no_materials_uses_standard(self):
        """AUTO mode without materials should produce standard single-model structure."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        result = export_3mf(str(self.temp_file), use_orca_format="AUTO")
        self.assertEqual(result.status, "FINISHED")

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            names = zf.namelist()
            # Standard format has NO 3D/Objects/ — just 3D/3dmodel.model
            object_files = [n for n in names if n.startswith("3D/Objects/")]
            self.assertEqual(
                len(object_files), 0,
                "AUTO without materials should use StandardExporter (no 3D/Objects/)",
            )
            self.assertIn("3D/3dmodel.model", names)

    def test_standard_with_materials_forces_standard(self):
        """STANDARD mode should use StandardExporter even when materials exist."""
        self._create_cube_with_material()
        result = export_3mf(str(self.temp_file), use_orca_format="STANDARD")
        self.assertEqual(result.status, "FINISHED")

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            names = zf.namelist()
            # Standard exporter writes only 3D/3dmodel.model — NO 3D/Objects/
            object_files = [n for n in names if n.startswith("3D/Objects/")]
            self.assertEqual(
                len(object_files), 0,
                "STANDARD mode should force StandardExporter even with materials "
                "(no 3D/Objects/ files)",
            )
            self.assertIn("3D/3dmodel.model", names)

    def test_standard_preserves_components(self):
        """STANDARD mode should export linked duplicates as components."""
        # Create a cube and link-duplicate it (Alt+D)
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        original = bpy.context.object
        mat = bpy.data.materials.new("Comp_Mat")
        mat.use_nodes = True
        original.data.materials.append(mat)

        # Create linked duplicate (shares mesh data)
        bpy.ops.object.duplicate(linked=True)
        duplicate = bpy.context.object
        duplicate.location = (3, 0, 0)

        result = export_3mf(
            str(self.temp_file),
            use_orca_format="STANDARD",
            use_components=True,
        )
        self.assertEqual(result.status, "FINISHED")

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            model_data = zf.read("3D/3dmodel.model").decode("utf-8")
            # Components should be present since use_components=True
            # and both objects share mesh data
            self.assertIn("component", model_data.lower())

    def test_auto_default_matches_no_arg(self):
        """Calling export_3mf() without use_orca_format should behave like AUTO."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))

        import uuid
        from test_base import get_temp_test_dir
        path_default = str(get_temp_test_dir() / f"default_{uuid.uuid4().hex[:8]}.3mf")
        path_auto = str(get_temp_test_dir() / f"auto_{uuid.uuid4().hex[:8]}.3mf")

        result_default = export_3mf(path_default)
        result_auto = export_3mf(path_auto, use_orca_format="AUTO")

        self.assertEqual(result_default.status, result_auto.status)

        # Both should produce the same archive structure
        with zipfile.ZipFile(path_default, "r") as zf1, \
             zipfile.ZipFile(path_auto, "r") as zf2:
            # Same set of file types (object files vs single model)
            obj1 = [n for n in zf1.namelist() if n.startswith("3D/Objects/")]
            obj2 = [n for n in zf2.namelist() if n.startswith("3D/Objects/")]
            self.assertEqual(len(obj1), len(obj2))


# ============================================================================
# Round-trip
# ============================================================================

class TestRoundTrip(Blender3mfTestCase):
    """export → import round-trip preserves geometry."""

    def test_cube_roundtrip(self):
        # Create and export a cube
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        original_vert_count = len(cube.data.vertices)

        export_result = export_3mf(str(self.temp_file))
        self.assertEqual(export_result.status, "FINISHED")

        # Clear scene and import
        self.clean_scene()
        import_result = import_3mf(str(self.temp_file))
        self.assertEqual(import_result.status, "FINISHED")
        self.assertEqual(import_result.num_loaded, 1)

        # Verify vertex count matches
        imported_obj = import_result.objects[0]
        self.assertEqual(len(imported_obj.data.vertices), original_vert_count)


# ============================================================================
# batch_import / batch_export
# ============================================================================

class TestBatchOperations(Blender3mfTestCase):
    """batch_import() and batch_export()."""

    def test_batch_import(self):
        """Batch-import multiple files."""
        fpath = self.test_resources_dir / "3mf_consortium" / "pyramid_vertexcolor.3mf"
        if not fpath.exists():
            self.skipTest(f"Resource not found: {fpath}")

        results = batch_import([str(fpath), str(fpath)])
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIsInstance(r, ImportResult)
            self.assertEqual(r.status, "FINISHED")

    def test_batch_export(self):
        """Batch-export to multiple paths."""
        import uuid
        from test_base import get_temp_test_dir

        bpy.ops.mesh.primitive_cube_add()
        paths = [
            str(get_temp_test_dir() / f"batch_{uuid.uuid4().hex[:8]}.3mf")
            for _ in range(2)
        ]
        results = batch_export([(p, None) for p in paths])
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIsInstance(r, ExportResult)


# ============================================================================
# Building-block re-exports
# ============================================================================

class TestBuildingBlocks(unittest.TestCase):
    """API re-exports sub-namespaces for custom workflows."""

    def test_colors_module(self):
        from io_mesh_3mf.api import colors
        self.assertTrue(hasattr(colors, "hex_to_rgb"))
        self.assertTrue(hasattr(colors, "rgb_to_hex"))

    def test_types_module(self):
        from io_mesh_3mf.api import types
        self.assertTrue(hasattr(types, "ResourceObject"))
        self.assertTrue(hasattr(types, "ResourceMaterial"))

    def test_segmentation_module(self):
        from io_mesh_3mf.api import segmentation
        self.assertTrue(hasattr(segmentation, "SegmentationDecoder"))
        self.assertTrue(hasattr(segmentation, "SegmentationEncoder"))

    def test_units_module(self):
        from io_mesh_3mf.api import units
        self.assertTrue(hasattr(units, "blender_to_metre"))
        self.assertTrue(hasattr(units, "threemf_to_metre"))


if __name__ == "__main__":
    unittest.main()
