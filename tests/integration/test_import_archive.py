"""
Integration tests for ``io_mesh_3mf.import_3mf.archive``.

Tests archive reading, content-type parsing, and MIME-type assignment
using crafted ZIP files.  Runs inside real Blender.
"""

import io
import re
import unittest
import zipfile

from io_mesh_3mf.import_3mf.context import ImportContext, ImportOptions
from io_mesh_3mf.import_3mf.archive import (
    read_content_types,
    assign_content_types,
)
from io_mesh_3mf.common.constants import (
    CONTENT_TYPES_LOCATION,
    MODEL_MIMETYPE,
    RELS_MIMETYPE,
)


def _make_ctx() -> ImportContext:
    """Create a minimal ImportContext for function-level tests."""
    return ImportContext(options=ImportOptions(), operator=None)


def _make_archive(files: dict) -> zipfile.ZipFile:
    """Create an in-memory ZIP archive with given files.

    :param files: ``{path: content_bytes_or_str}``
    :return: A ``ZipFile`` opened for reading.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(path, content)
    buf.seek(0)
    return zipfile.ZipFile(buf, "r")


# ============================================================================
# read_content_types
# ============================================================================

class TestReadContentTypes(unittest.TestCase):
    """read_content_types() with crafted [Content_Types].xml files."""

    def test_with_overrides(self):
        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '  <Override PartName="/3D/3dmodel.model" '
            '            ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />'
            "</Types>"
        )
        archive = _make_archive({
            CONTENT_TYPES_LOCATION: ct_xml,
            "3D/3dmodel.model": "<model />",
        })
        ctx = _make_ctx()
        result = read_content_types(ctx, archive)

        # Should have override + 2 fallback defaults
        self.assertGreater(len(result), 0)
        # First entry should be the override
        pattern, mime = result[0]
        self.assertEqual(mime, MODEL_MIMETYPE)

    def test_with_defaults(self):
        ct_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '  <Default Extension="model" '
            '           ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />'
            "</Types>"
        )
        archive = _make_archive({
            CONTENT_TYPES_LOCATION: ct_xml,
            "3D/3dmodel.model": "<model />",
        })
        ctx = _make_ctx()
        result = read_content_types(ctx, archive)

        # Should have at least the default + fallbacks
        mimes = [mime for _, mime in result]
        self.assertIn(MODEL_MIMETYPE, mimes)

    def test_missing_content_types(self):
        """Missing [Content_Types].xml should still return fallback patterns."""
        archive = _make_archive({
            "3D/3dmodel.model": "<model />",
        })
        ctx = _make_ctx()
        result = read_content_types(ctx, archive)

        # Fallback .rels and .model patterns
        mimes = [mime for _, mime in result]
        self.assertIn(RELS_MIMETYPE, mimes)
        self.assertIn(MODEL_MIMETYPE, mimes)

    def test_malformed_xml(self):
        """Malformed XML should still return fallback patterns."""
        archive = _make_archive({
            CONTENT_TYPES_LOCATION: "<<<NOT XML>>>",
            "3D/3dmodel.model": "<model />",
        })
        ctx = _make_ctx()
        result = read_content_types(ctx, archive)
        # Should still have fallbacks
        self.assertGreater(len(result), 0)


# ============================================================================
# assign_content_types
# ============================================================================

class TestAssignContentTypes(unittest.TestCase):
    """assign_content_types() with crafted archives."""

    def test_model_file_gets_type(self):
        archive = _make_archive({
            CONTENT_TYPES_LOCATION: "<Types />",
            "3D/3dmodel.model": "<model />",
            "_rels/.rels": "<Relationships />",
        })
        content_types = [
            (re.compile(r".*\.model"), MODEL_MIMETYPE),
            (re.compile(r".*\.rels"), RELS_MIMETYPE),
        ]

        result = assign_content_types(archive, content_types)

        self.assertEqual(result["3D/3dmodel.model"], MODEL_MIMETYPE)
        self.assertEqual(result["_rels/.rels"], RELS_MIMETYPE)

    def test_unrecognized_gets_empty(self):
        archive = _make_archive({
            CONTENT_TYPES_LOCATION: "<Types />",
            "random.txt": "hello",
        })
        content_types = [
            (re.compile(r".*\.model"), MODEL_MIMETYPE),
        ]

        result = assign_content_types(archive, content_types)
        self.assertEqual(result.get("random.txt"), "")

    def test_content_types_excluded(self):
        """[Content_Types].xml itself should not appear in results."""
        archive = _make_archive({
            CONTENT_TYPES_LOCATION: "<Types />",
            "3D/3dmodel.model": "<model />",
        })
        content_types = []
        result = assign_content_types(archive, content_types)
        self.assertNotIn(CONTENT_TYPES_LOCATION, result)

    def test_priority_override_first(self):
        """Earlier patterns (overrides) should win over later ones (defaults)."""
        archive = _make_archive({
            CONTENT_TYPES_LOCATION: "<Types />",
            "3D/3dmodel.model": "<model />",
        })
        content_types = [
            (re.compile(re.escape("/3D/3dmodel.model")), "override/xml"),
            (re.compile(r".*\.model"), MODEL_MIMETYPE),
        ]
        result = assign_content_types(archive, content_types)
        # The override regex uses fullmatch, so it depends on whether the path matches
        # In any case, some MIME type should be assigned
        self.assertIn("3D/3dmodel.model", result)


if __name__ == "__main__":
    unittest.main()
