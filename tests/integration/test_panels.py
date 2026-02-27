"""
Integration tests for 3MF metadata and triangle-sets panel helpers.

Covers pure helpers from:
- ``io_mesh_3mf.panels.metadata`` — _format_count, _detect_vendor_from_scene,
    _parse_paint_colors, _get_metadata_value, _get_triangle_set_counts
- ``io_mesh_3mf.panels.triangle_sets`` — _load_set_names, _get_set_name,
    _set_set_name, _format_count, _get_face_set_info
"""

import bpy
import json
import unittest
from test_base import Blender3mfTestCase


# ===========================================================================
#  Metadata panel helpers
# ===========================================================================

class FormatCountTests(unittest.TestCase):
    """Tests for panels.metadata._format_count()."""

    def setUp(self):
        from io_mesh_3mf.panels.metadata import _format_count
        self._format_count = _format_count

    def test_small_number(self):
        self.assertEqual(self._format_count(42), "42")

    def test_thousands(self):
        self.assertEqual(self._format_count(1234), "1,234")

    def test_millions(self):
        self.assertEqual(self._format_count(1234567), "1,234,567")

    def test_zero(self):
        self.assertEqual(self._format_count(0), "0")


class GetMetadataValueTests(Blender3mfTestCase):
    """Tests for panels.metadata._get_metadata_value()."""

    def setUp(self):
        super().setUp()
        from io_mesh_3mf.panels.metadata import _get_metadata_value
        self._get_metadata_value = _get_metadata_value

    def test_title_returns_scene_name(self):
        """'Title' key returns the scene name."""
        scene = bpy.context.scene
        scene.name = "MyTestScene"
        value, is_meta = self._get_metadata_value(scene, "Title")
        self.assertEqual(value, "MyTestScene")
        self.assertTrue(is_meta)

    def test_metadata_entry_idproperty(self):
        """A proper metadata entry (IDPropertyGroup) returns its value."""
        scene = bpy.context.scene
        scene["Designer"] = {
            "datatype": "xs:string",
            "preserve": True,
            "value": "TestDesigner",
        }
        value, is_meta = self._get_metadata_value(scene, "Designer")
        self.assertEqual(value, "TestDesigner")
        self.assertTrue(is_meta)

    def test_missing_key_returns_none(self):
        """A missing key returns (None, False)."""
        scene = bpy.context.scene
        value, is_meta = self._get_metadata_value(scene, "NonexistentKey")
        self.assertIsNone(value)
        self.assertFalse(is_meta)


class DetectVendorFromSceneTests(Blender3mfTestCase):
    """Tests for panels.metadata._detect_vendor_from_scene()."""

    def setUp(self):
        super().setUp()
        from io_mesh_3mf.panels.metadata import _detect_vendor_from_scene
        self._detect_vendor_from_scene = _detect_vendor_from_scene

    def test_no_metadata_returns_none(self):
        """A fresh scene with no metadata returns None."""
        scene = bpy.context.scene
        result = self._detect_vendor_from_scene(scene)
        self.assertIsNone(result)

    def test_orca_application(self):
        """Application containing 'orca' is detected as Orca."""
        scene = bpy.context.scene
        scene["Application"] = {
            "datatype": "xs:string",
            "preserve": True,
            "value": "OrcaSlicer 2.0",
        }
        result = self._detect_vendor_from_scene(scene)
        self.assertIn("Orca", result)

    def test_prusa_application(self):
        """Application containing 'prusa' is detected as PrusaSlicer."""
        scene = bpy.context.scene
        scene["Application"] = {
            "datatype": "xs:string",
            "preserve": True,
            "value": "PrusaSlicer 2.7",
        }
        result = self._detect_vendor_from_scene(scene)
        self.assertIn("Prusa", result)

    def test_bambu_version_fallback(self):
        """BambuStudio:3mfVersion present → Orca / BambuStudio."""
        scene = bpy.context.scene
        scene["BambuStudio:3mfVersion"] = {
            "datatype": "xs:string",
            "preserve": True,
            "value": "1",
        }
        result = self._detect_vendor_from_scene(scene)
        self.assertIsNotNone(result)
        self.assertIn("Bambu", result)


class ParsePaintColorsTests(Blender3mfTestCase):
    """Tests for panels.metadata._parse_paint_colors()."""

    def setUp(self):
        super().setUp()
        from io_mesh_3mf.panels.metadata import _parse_paint_colors
        self._parse_paint_colors = _parse_paint_colors

    def test_valid_colors(self):
        """A valid stringified dict is parsed correctly."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        mesh["3mf_paint_extruder_colors"] = str({0: "#FF0000", 1: "#0000FF"})

        result = self._parse_paint_colors(mesh)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], "#FF0000")
        self.assertEqual(result[1], "#0000FF")

    def test_empty_string(self):
        """Empty property returns empty dict."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        mesh["3mf_paint_extruder_colors"] = ""

        result = self._parse_paint_colors(mesh)
        self.assertEqual(result, {})

    def test_no_property(self):
        """Missing property returns empty dict."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        result = self._parse_paint_colors(mesh)
        self.assertEqual(result, {})

    def test_invalid_syntax(self):
        """Invalid syntax returns empty dict (no crash)."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        mesh["3mf_paint_extruder_colors"] = "not a dict"

        result = self._parse_paint_colors(mesh)
        self.assertEqual(result, {})


class GetTriangleSetCountsTests(Blender3mfTestCase):
    """Tests for panels.metadata._get_triangle_set_counts()."""

    def setUp(self):
        super().setUp()
        from io_mesh_3mf.panels.metadata import _get_triangle_set_counts
        self._get_triangle_set_counts = _get_triangle_set_counts

    def test_no_attribute(self):
        """Mesh without 3mf_triangle_set returns empty dict."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        result = self._get_triangle_set_counts(mesh)
        self.assertEqual(result, {})

    def test_with_triangle_set_attribute(self):
        """Mesh with 3mf_triangle_set attribute returns correct counts."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        # Create the attribute
        attr = mesh.attributes.new(
            name="3mf_triangle_set", type="INT", domain="FACE"
        )
        num_faces = len(mesh.polygons)
        # Assign set 1 to first half, set 2 to rest
        values = [1 if i < num_faces // 2 else 2 for i in range(num_faces)]
        attr.data.foreach_set("value", values)

        result = self._get_triangle_set_counts(mesh)
        self.assertIn(1, result)
        self.assertIn(2, result)
        self.assertEqual(result[1] + result[2], num_faces)


# ===========================================================================
#  Triangle sets panel helpers
# ===========================================================================

class TriangleSetFormatCountTests(unittest.TestCase):
    """Tests for panels.triangle_sets._format_count()."""

    def test_basic(self):
        from io_mesh_3mf.panels.triangle_sets import _format_count
        self.assertEqual(_format_count(12345), "12,345")


class LoadSetNamesTests(Blender3mfTestCase):
    """Tests for panels.triangle_sets._load_set_names()."""

    def setUp(self):
        super().setUp()
        from io_mesh_3mf.panels.triangle_sets import _load_set_names
        self._load_set_names = _load_set_names

    def test_no_property(self):
        """Missing property returns empty list."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        result = self._load_set_names(mesh)
        self.assertEqual(result, [])

    def test_valid_json(self):
        """Valid JSON list is parsed correctly."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        mesh["3mf_triangle_set_names"] = json.dumps(["SetA", "SetB", "SetC"])

        result = self._load_set_names(mesh)
        self.assertEqual(result, ["SetA", "SetB", "SetC"])

    def test_invalid_json(self):
        """Invalid JSON returns empty list."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        mesh["3mf_triangle_set_names"] = "not json"

        result = self._load_set_names(mesh)
        self.assertEqual(result, [])

    def test_empty_string(self):
        """Empty string returns empty list."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        mesh["3mf_triangle_set_names"] = ""

        result = self._load_set_names(mesh)
        self.assertEqual(result, [])


class GetSetNameTests(Blender3mfTestCase):
    """Tests for panels.triangle_sets._get_set_name()."""

    def setUp(self):
        super().setUp()
        from io_mesh_3mf.panels.triangle_sets import _get_set_name
        self._get_set_name = _get_set_name

    def test_valid_set_id(self):
        """Retrieves name for a valid set ID."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        mesh["3mf_triangle_set_names"] = json.dumps(["First", "Second"])

        self.assertEqual(self._get_set_name(mesh, 1), "First")
        self.assertEqual(self._get_set_name(mesh, 2), "Second")

    def test_out_of_range(self):
        """Out-of-range set ID returns empty string."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        mesh["3mf_triangle_set_names"] = json.dumps(["First"])

        self.assertEqual(self._get_set_name(mesh, 5), "")

    def test_no_names(self):
        """No names stored returns empty string."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data
        self.assertEqual(self._get_set_name(mesh, 1), "")


class SetSetNameTests(Blender3mfTestCase):
    """Tests for panels.triangle_sets._set_set_name()."""

    def setUp(self):
        super().setUp()
        from io_mesh_3mf.panels.triangle_sets import _set_set_name, _get_set_name
        self._set_set_name = _set_set_name
        self._get_set_name = _get_set_name

    def test_set_and_retrieve(self):
        """Setting a name and retrieving it round-trips correctly."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        self._set_set_name(mesh, 1, "MySet")
        self.assertEqual(self._get_set_name(mesh, 1), "MySet")

    def test_grow_list(self):
        """Setting a high set ID auto-pads the list."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        self._set_set_name(mesh, 3, "Third")

        # Set IDs 1 and 2 should be empty strings
        self.assertEqual(self._get_set_name(mesh, 1), "")
        self.assertEqual(self._get_set_name(mesh, 2), "")
        self.assertEqual(self._get_set_name(mesh, 3), "Third")

    def test_overwrite_existing(self):
        """Overwriting an existing name works."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        self._set_set_name(mesh, 1, "OldName")
        self._set_set_name(mesh, 1, "NewName")
        self.assertEqual(self._get_set_name(mesh, 1), "NewName")

    def test_stored_as_json(self):
        """Names are stored as valid JSON in the mesh property."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        self._set_set_name(mesh, 1, "Test")
        raw = mesh["3mf_triangle_set_names"]
        parsed = json.loads(raw)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0], "Test")


if __name__ == "__main__":
    unittest.main()
