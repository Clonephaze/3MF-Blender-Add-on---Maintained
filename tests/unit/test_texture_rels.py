"""
Unit tests for ``io_mesh_3mf.export_3mf.materials.textures.write_texture_relationships``.

Verifies that the texture relationship file (``3D/_rels/3dmodel.model.rels``)
is written with correct XML structure, namespace handling, and content.
These tests catch the ns0:-prefix bug and missing/overwritten rels issues.
"""

import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from io_mesh_3mf.export_3mf.materials.textures import write_texture_relationships
from io_mesh_3mf.common.constants import TEXTURE_REL, RELS_NAMESPACE


RELS_PATH = "3D/_rels/3dmodel.model.rels"


class TestWriteTextureRelationships(unittest.TestCase):
    """Tests for write_texture_relationships()."""

    def _write_and_read_rels(self, image_to_path):
        """Write texture rels to a temp archive and return the raw bytes."""
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with zipfile.ZipFile(tmp_path, "w") as archive:
                write_texture_relationships(archive, image_to_path)

            with zipfile.ZipFile(tmp_path, "r") as archive:
                return archive.read(RELS_PATH)
        finally:
            os.unlink(tmp_path)

    # ------------------------------------------------------------------
    # Namespace / serialisation correctness
    # ------------------------------------------------------------------

    def test_no_ns0_prefix(self):
        """Rels XML must not contain ns0: prefixed elements or attributes."""
        data = self._write_and_read_rels({"img": "/3D/Textures/img.png"})
        text = data.decode("utf-8")
        self.assertNotIn("ns0:", text)

    def test_has_default_xmlns_declaration(self):
        """Root <Relationships> element should declare xmlns= for the OPC rels namespace."""
        data = self._write_and_read_rels({"img": "/3D/Textures/img.png"})
        text = data.decode("utf-8")
        self.assertIn(f'xmlns="{RELS_NAMESPACE}"', text)

    def test_produces_valid_xml(self):
        """Output should be parseable XML with Relationships root element."""
        data = self._write_and_read_rels({"img": "/3D/Textures/img.png"})
        root = ET.fromstring(data)
        local_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
        self.assertEqual(local_tag, "Relationships")

    # ------------------------------------------------------------------
    # Relationship content
    # ------------------------------------------------------------------

    def test_correct_relationship_count(self):
        """Should produce one Relationship element per texture."""
        data = self._write_and_read_rels(
            {
                "img1": "/3D/Textures/a.png",
                "img2": "/3D/Textures/b.png",
                "img3": "/3D/Textures/c.png",
            }
        )
        root = ET.fromstring(data)
        rels = root.findall(f"{{{RELS_NAMESPACE}}}Relationship")
        self.assertEqual(len(rels), 3)

    def test_relationship_type_is_texture_rel(self):
        """Each Relationship should have Type set to the 3MF texture rel URI."""
        data = self._write_and_read_rels({"img": "/3D/Textures/img.png"})
        root = ET.fromstring(data)
        rels = root.findall(f"{{{RELS_NAMESPACE}}}Relationship")
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0].get("Type"), TEXTURE_REL)

    def test_relationship_target_matches_path(self):
        """Relationship Target should match the archive path provided."""
        data = self._write_and_read_rels({"img": "/3D/Textures/logo.png"})
        root = ET.fromstring(data)
        rels = root.findall(f"{{{RELS_NAMESPACE}}}Relationship")
        self.assertEqual(rels[0].get("Target"), "/3D/Textures/logo.png")

    def test_relationship_has_id(self):
        """Each Relationship should have a non-empty Id attribute."""
        data = self._write_and_read_rels({"img": "/3D/Textures/img.png"})
        root = ET.fromstring(data)
        rels = root.findall(f"{{{RELS_NAMESPACE}}}Relationship")
        self.assertTrue(rels[0].get("Id"), "Relationship Id should not be empty")

    def test_unique_ids(self):
        """Multiple relationships should have unique Id values."""
        data = self._write_and_read_rels(
            {
                "img1": "/3D/Textures/a.png",
                "img2": "/3D/Textures/b.png",
            }
        )
        root = ET.fromstring(data)
        rels = root.findall(f"{{{RELS_NAMESPACE}}}Relationship")
        ids = [r.get("Id") for r in rels]
        self.assertEqual(len(ids), len(set(ids)), "Relationship Ids must be unique")

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_dict_writes_nothing(self):
        """Empty image_to_path should not create a rels file at all."""
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with zipfile.ZipFile(tmp_path, "w") as archive:
                write_texture_relationships(archive, {})

            with zipfile.ZipFile(tmp_path, "r") as archive:
                self.assertNotIn(RELS_PATH, archive.namelist())
        finally:
            os.unlink(tmp_path)

    def test_plural_textures_path_preserved(self):
        """Paths using /3D/Textures/ (plural, per spec) should be preserved."""
        data = self._write_and_read_rels({"img": "/3D/Textures/logo.png"})
        root = ET.fromstring(data)
        rels = root.findall(f"{{{RELS_NAMESPACE}}}Relationship")
        target = rels[0].get("Target")
        self.assertIn("/3D/Textures/", target)

    def test_does_not_corrupt_global_namespace_registry(self):
        """Writing rels must not pollute the global ET namespace registry.

        A previous bug used xml.etree.ElementTree.register_namespace()
        which is process-wide and corrupted model XML output.
        """
        # Capture current default namespace behaviour
        test_elem = ET.Element("{http://test.example.com}Root")
        ET.SubElement(test_elem, "{http://test.example.com}Child")
        before = ET.tostring(test_elem, encoding="unicode")

        # Write texture rels
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with zipfile.ZipFile(tmp_path, "w") as archive:
                write_texture_relationships(archive, {"img": "/3D/Textures/a.png"})
        finally:
            os.unlink(tmp_path)

        # Verify global state unchanged
        after = ET.tostring(test_elem, encoding="unicode")
        self.assertEqual(
            before,
            after,
            "write_texture_relationships() corrupted the global ET namespace registry",
        )


if __name__ == "__main__":
    unittest.main()
