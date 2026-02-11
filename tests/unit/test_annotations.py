"""
Unit tests for ``io_mesh_3mf.common.annotations``.

Tests Relationship/ContentType namedtuples, ConflictingContentType sentinel,
and Annotations class methods that don't require Blender scene text blocks.
Tests that need ``bpy.data.texts`` (store/retrieve) are in integration tests.
"""

import io
import unittest
import xml.etree.ElementTree as ET

from io_mesh_3mf.common.annotations import (
    Annotations,
    Relationship,
    ContentType,
    ConflictingContentType,
)


# ============================================================================
# Namedtuple basics
# ============================================================================


class TestRelationship(unittest.TestCase):
    """Relationship namedtuple."""

    def test_fields(self):
        rel = Relationship(namespace="http://example.com/type", source="/")
        self.assertEqual(rel.namespace, "http://example.com/type")
        self.assertEqual(rel.source, "/")

    def test_equality(self):
        a = Relationship("ns", "/")
        b = Relationship("ns", "/")
        self.assertEqual(a, b)

    def test_hashable(self):
        rel = Relationship("ns", "/")
        s = {rel, rel}
        self.assertEqual(len(s), 1)


class TestContentType(unittest.TestCase):
    """ContentType namedtuple."""

    def test_fields(self):
        ct = ContentType(mime_type="image/png")
        self.assertEqual(ct.mime_type, "image/png")

    def test_equality(self):
        a = ContentType("image/png")
        b = ContentType("image/png")
        self.assertEqual(a, b)

    def test_different(self):
        a = ContentType("image/png")
        b = ContentType("image/jpeg")
        self.assertNotEqual(a, b)


class TestConflictingContentType(unittest.TestCase):
    """ConflictingContentType is a sentinel object."""

    def test_is_unique(self):
        self.assertIs(ConflictingContentType, ConflictingContentType)

    def test_is_not_content_type(self):
        self.assertNotIsInstance(ConflictingContentType, ContentType)


# ============================================================================
# Annotations — basic dict operations
# ============================================================================


class TestAnnotationsInit(unittest.TestCase):
    """Annotations initialization."""

    def test_starts_empty(self):
        ann = Annotations()
        self.assertEqual(ann.annotations, {})


# ============================================================================
# Annotations.add_content_types()
# ============================================================================


class _FakeFile:
    """Minimal file-like with a .name attribute."""

    def __init__(self, name):
        self.name = name


class TestAddContentTypes(unittest.TestCase):
    """add_content_types() populates annotations from file classification."""

    def test_adds_content_type(self):
        ann = Annotations()
        f = _FakeFile("textures/logo.png")
        ann.add_content_types({"image/png": {f}})
        self.assertIn("textures/logo.png", ann.annotations)
        types = [a for a in ann.annotations["textures/logo.png"] if isinstance(a, ContentType)]
        self.assertEqual(len(types), 1)
        self.assertEqual(types[0].mime_type, "image/png")

    def test_skips_empty_content_type(self):
        ann = Annotations()
        f = _FakeFile("somefile")
        ann.add_content_types({"": {f}})
        self.assertNotIn("somefile", ann.annotations)

    def test_skips_rels_mimetype(self):
        """RELS and MODEL MIME types are skipped."""
        from io_mesh_3mf.common.constants import RELS_MIMETYPE, MODEL_MIMETYPE

        ann = Annotations()
        f1 = _FakeFile("a.rels")
        f2 = _FakeFile("b.model")
        ann.add_content_types({RELS_MIMETYPE: {f1}, MODEL_MIMETYPE: {f2}})
        self.assertNotIn("a.rels", ann.annotations)
        self.assertNotIn("b.model", ann.annotations)

    def test_conflicting_content_types(self):
        """Same file with two different MIME types → ConflictingContentType."""
        ann = Annotations()
        f = _FakeFile("data.bin")
        ann.add_content_types({"application/octet-stream": {f}})
        ann.add_content_types({"text/plain": {f}})
        self.assertIn(ConflictingContentType, ann.annotations["data.bin"])
        # Original ContentType should be removed
        ct_entries = [
            a for a in ann.annotations["data.bin"] if isinstance(a, ContentType)
        ]
        self.assertEqual(len(ct_entries), 0)


# ============================================================================
# Annotations.add_rels()
# ============================================================================


class TestAddRels(unittest.TestCase):
    """add_rels() parses .rels XML streams."""

    def _make_rels_stream(self, relationships, name="_rels/.rels"):
        """Build a minimal .rels XML in a BytesIO with a .name attribute."""
        ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        root = ET.Element(f"{{{ns}}}Relationships")
        for target, rel_type in relationships:
            ET.SubElement(
                root,
                f"{{{ns}}}Relationship",
                attrib={"Target": target, "Type": rel_type, "Id": "r0"},
            )
        data = ET.tostring(root, encoding="unicode")
        stream = io.BytesIO(data.encode("utf-8"))
        stream.name = name
        return stream

    def test_parses_relationship(self):
        stream = self._make_rels_stream(
            [("/extra/data.xml", "http://example.com/custom")]
        )
        ann = Annotations()
        ann.add_rels(stream)
        # Should have added a Relationship for extra/data.xml
        self.assertIn("extra/data.xml", ann.annotations)

    def test_skips_model_rel_type(self):
        """MODEL_REL relationships are skipped."""
        from io_mesh_3mf.common.constants import MODEL_REL

        stream = self._make_rels_stream(
            [("/3D/3dmodel.model", MODEL_REL)]
        )
        ann = Annotations()
        ann.add_rels(stream)
        self.assertNotIn("3D/3dmodel.model", ann.annotations)

    def test_skips_texture_rel_type(self):
        """TEXTURE_REL relationships are skipped."""
        from io_mesh_3mf.common.constants import TEXTURE_REL

        stream = self._make_rels_stream(
            [("/textures/tex.png", TEXTURE_REL)]
        )
        ann = Annotations()
        ann.add_rels(stream)
        self.assertNotIn("textures/tex.png", ann.annotations)

    def test_malformed_xml(self):
        """Malformed XML should not crash — logs a warning."""
        stream = io.BytesIO(b"<<<not xml>>>")
        stream.name = "_rels/.rels"
        ann = Annotations()
        ann.add_rels(stream)  # Should not raise
        self.assertEqual(ann.annotations, {})


if __name__ == "__main__":
    unittest.main()
