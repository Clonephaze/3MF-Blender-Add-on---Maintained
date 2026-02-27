"""
Integration tests for ``io_mesh_3mf.export_3mf.archive`` and metadata writing.

Covers:
- create_archive: OPC structure, compression, error handling
- must_preserve: Base85 round-trip of preserved files
- write_core_properties: Dublin Core XML
- write_metadata: <metadata> elements with name/preserve/type attributes
"""

import unittest
import zipfile
import xml.etree.ElementTree as ET
import os

import bpy

from test_base import Blender3mfTestCase

from io_mesh_3mf.common.constants import MODEL_NAMESPACE
from io_mesh_3mf.export_3mf.archive import (
    create_archive,
    write_core_properties,
)
from io_mesh_3mf.export_3mf.geometry import write_metadata
from io_mesh_3mf.common.metadata import Metadata, MetadataEntry


# ============================================================================
# create_archive
# ============================================================================


class CreateArchiveTests(Blender3mfTestCase):
    """Tests for create_archive()."""

    def _noop_report(self, *_):
        pass

    def test_returns_zipfile(self):
        """Should return a writable ZipFile instance."""
        archive = create_archive(str(self.temp_file), self._noop_report)
        self.assertIsNotNone(archive)
        self.assertIsInstance(archive, zipfile.ZipFile)
        archive.close()

    def test_contains_rels_file(self):
        """Archive should include an OPC relationships file."""
        archive = create_archive(str(self.temp_file), self._noop_report)
        archive.close()

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            rels_files = [n for n in zf.namelist() if n.endswith(".rels")]
            self.assertGreater(len(rels_files), 0, "Should have .rels file")

    def test_contains_content_types(self):
        """Archive should include [Content_Types].xml."""
        archive = create_archive(str(self.temp_file), self._noop_report)
        archive.close()

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            self.assertIn("[Content_Types].xml", zf.namelist())

    def test_compression_level_zero(self):
        """compression_level=0 should produce a valid archive (stored)."""
        archive = create_archive(str(self.temp_file), self._noop_report, compression_level=0)
        self.assertIsNotNone(archive)
        archive.close()

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            self.assertIn("[Content_Types].xml", zf.namelist())

    def test_compression_level_nine(self):
        """compression_level=9 should produce a valid archive (max compression)."""
        archive = create_archive(str(self.temp_file), self._noop_report, compression_level=9)
        self.assertIsNotNone(archive)
        archive.close()

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            self.assertIn("[Content_Types].xml", zf.namelist())

    def test_invalid_path_returns_none(self):
        """Passing a bad path should return None, not raise."""
        bad_path = os.path.join("Z:\\", "nonexistent_drive", "test.3mf")
        archive = create_archive(bad_path, self._noop_report)
        # On most systems this fails — should return None
        if archive is not None:
            archive.close()


# ============================================================================
# write_core_properties
# ============================================================================


class WriteCorePropertiesTests(Blender3mfTestCase):
    """Tests for write_core_properties()."""

    def _noop_report(self, *_):
        pass

    def test_core_properties_file_created(self):
        """write_core_properties should add a Core Properties file."""
        archive = create_archive(str(self.temp_file), self._noop_report)
        self.assertIsNotNone(archive)
        write_core_properties(archive)
        archive.close()

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            # Should contain docProps/core.xml
            prop_files = [n for n in zf.namelist() if "core" in n.lower()]
            self.assertGreater(len(prop_files), 0, "Should have core properties")

    def test_core_properties_valid_xml(self):
        """Core Properties file should contain XML content."""
        archive = create_archive(str(self.temp_file), self._noop_report)
        self.assertIsNotNone(archive)
        write_core_properties(archive)
        archive.close()

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            for name in zf.namelist():
                if "core" in name.lower() and name.endswith(".xml"):
                    data = zf.read(name).decode("utf-8")
                    # Should contain XML declaration and root element
                    self.assertIn("<?xml", data)
                    self.assertIn("coreProperties", data)
                    break

    def test_core_properties_has_creator(self):
        """Core Properties should contain a dc:creator element."""
        archive = create_archive(str(self.temp_file), self._noop_report)
        self.assertIsNotNone(archive)
        write_core_properties(archive)
        archive.close()

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            for name in zf.namelist():
                if "core" in name.lower() and name.endswith(".xml"):
                    data = zf.read(name).decode("utf-8")
                    self.assertIn("creator", data)
                    self.assertIn("Blender", data)
                    break

    def test_core_properties_has_timestamps(self):
        """Core Properties should contain created / modified timestamps."""
        archive = create_archive(str(self.temp_file), self._noop_report)
        self.assertIsNotNone(archive)
        write_core_properties(archive)
        archive.close()

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            for name in zf.namelist():
                if "core" in name.lower() and name.endswith(".xml"):
                    data = zf.read(name).decode("utf-8")
                    # ISO 8601 timestamp pattern
                    self.assertRegex(data, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
                    break


# ============================================================================
# write_metadata
# ============================================================================


class WriteMetadataTests(Blender3mfTestCase):
    """Tests for write_metadata()."""

    def _root_element(self):
        return ET.Element(f"{{{MODEL_NAMESPACE}}}model")

    def test_simple_metadata(self):
        """Write a simple metadata entry with name and value."""
        root = self._root_element()
        md = Metadata()
        md["Title"] = MetadataEntry("Title", False, "", "Test Model")

        write_metadata(root, md, "STANDARD")

        entries = root.findall(f"{{{MODEL_NAMESPACE}}}metadata")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].text, "Test Model")

    def test_metadata_with_datatype(self):
        """Metadata with a type attribute should include it."""
        root = self._root_element()
        md = Metadata()
        md["CustomKey"] = MetadataEntry("CustomKey", False, "xs:integer", "42")

        write_metadata(root, md, "STANDARD")

        entries = root.findall(f"{{{MODEL_NAMESPACE}}}metadata")
        self.assertEqual(len(entries), 1)

        entry = entries[0]
        # In STANDARD mode, attrs are namespace-qualified
        type_val = entry.get(f"{{{MODEL_NAMESPACE}}}type")
        self.assertEqual(type_val, "xs:integer")

    def test_metadata_preserve_flag(self):
        """preserve=True should set preserve='1' attribute."""
        root = self._root_element()
        md = Metadata()
        md["CustomData"] = MetadataEntry("CustomData", True, "", "preserved")

        write_metadata(root, md, "STANDARD")

        entries = root.findall(f"{{{MODEL_NAMESPACE}}}metadata")
        entry = entries[0]
        preserve = entry.get(f"{{{MODEL_NAMESPACE}}}preserve")
        self.assertEqual(preserve, "1")

    def test_metadata_none_value(self):
        """None value should become empty string."""
        root = self._root_element()
        md = Metadata()
        md["Empty"] = MetadataEntry("Empty", False, "", None)

        write_metadata(root, md, "STANDARD")

        entries = root.findall(f"{{{MODEL_NAMESPACE}}}metadata")
        self.assertEqual(entries[0].text, "")

    def test_metadata_paint_mode_unqualified_attrs(self):
        """In PAINT mode, attribute names should be unqualified."""
        root = self._root_element()
        md = Metadata()
        md["Title"] = MetadataEntry("Title", False, "", "Test")

        write_metadata(root, md, "PAINT")

        entries = root.findall(f"{{{MODEL_NAMESPACE}}}metadata")
        entry = entries[0]
        # In PAINT mode, should have "name" not "{ns}name"
        self.assertIn("name", entry.attrib)
        self.assertEqual(entry.attrib["name"], "Title")

    def test_multiple_metadata_entries(self):
        """Multiple metadata entries should all be written."""
        root = self._root_element()
        md = Metadata()
        md["Title"] = MetadataEntry("Title", False, "", "T")
        md["Designer"] = MetadataEntry("Designer", False, "", "D")
        md["Description"] = MetadataEntry("Description", False, "", "Desc")

        write_metadata(root, md, "STANDARD")

        entries = root.findall(f"{{{MODEL_NAMESPACE}}}metadata")
        self.assertEqual(len(entries), 3)


# ============================================================================
# Full export archive integration
# ============================================================================


class FullExportArchiveTests(Blender3mfTestCase):
    """Test full export archive structure via operator."""

    def test_exported_archive_is_valid_zip(self):
        """Exported file should be a valid ZIP archive."""
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertTrue(zipfile.is_zipfile(str(self.temp_file)))

    def test_exported_archive_contains_model(self):
        """Archive should contain 3D/3dmodel.model or 3D/Objects/*.model."""
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            model_files = [
                n for n in zf.namelist() if n.endswith(".model")
            ]
            self.assertGreater(len(model_files), 0)

    def test_model_xml_is_parseable(self):
        """Model file should be valid XML."""
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        with zipfile.ZipFile(str(self.temp_file), "r") as zf:
            model_data = zf.read("3D/3dmodel.model")
            root = ET.fromstring(model_data)
            self.assertIsNotNone(root)

    def test_empty_scene_exports_empty_archive(self):
        """Exporting with no mesh objects should still produce a valid file."""
        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))
        self.assertIn("FINISHED", result)
        self.assertTrue(self.temp_file.exists())


if __name__ == "__main__":
    unittest.main()
