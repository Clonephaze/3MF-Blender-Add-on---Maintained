"""
Unit tests for ``io_mesh_3mf.common.xml``.

Tests XML transformation parsing/formatting and extension prefix resolution.
Requires ``mathutils`` (runs inside Blender).
"""

import unittest
import xml.etree.ElementTree as ET

import mathutils

from io_mesh_3mf.common.xml import (
    parse_transformation,
    format_transformation,
    resolve_extension_prefixes,
    is_supported,
    read_metadata,
)
from io_mesh_3mf.common.constants import (
    PRODUCTION_NAMESPACE,
    MATERIAL_NAMESPACE,
    MODEL_NAMESPACE,
)


# ============================================================================
# parse_transformation
# ============================================================================

class TestParseTransformation(unittest.TestCase):

    def test_empty_string(self):
        result = parse_transformation("")
        self.assertEqual(result, mathutils.Matrix.Identity(4))

    def test_identity(self):
        # 3MF identity: 12 values, column-major (without last row)
        identity_str = "1 0 0 0 1 0 0 0 1 0 0 0"
        result = parse_transformation(identity_str)
        for r in range(4):
            for c in range(4):
                expected = 1.0 if r == c else 0.0
                self.assertAlmostEqual(result[r][c], expected, places=5)

    def test_translation(self):
        # Translation (10, 20, 30)
        trans_str = "1 0 0 0 1 0 0 0 1 10 20 30"
        result = parse_transformation(trans_str)
        self.assertAlmostEqual(result[0][3], 10.0)
        self.assertAlmostEqual(result[1][3], 20.0)
        self.assertAlmostEqual(result[2][3], 30.0)

    def test_scale(self):
        # Uniform scale 2x
        scale_str = "2 0 0 0 2 0 0 0 2 0 0 0"
        result = parse_transformation(scale_str)
        self.assertAlmostEqual(result[0][0], 2.0)
        self.assertAlmostEqual(result[1][1], 2.0)
        self.assertAlmostEqual(result[2][2], 2.0)

    def test_returns_matrix_type(self):
        result = parse_transformation("1 0 0 0 1 0 0 0 1 0 0 0")
        self.assertIsInstance(result, mathutils.Matrix)


# ============================================================================
# format_transformation
# ============================================================================

class TestFormatTransformation(unittest.TestCase):

    def test_identity(self):
        result = format_transformation(mathutils.Matrix.Identity(4))
        parts = result.split()
        self.assertEqual(len(parts), 12)

    def test_round_trip(self):
        """parse → format → parse should preserve the matrix."""
        original_str = "1 0 0 0 1 0 0 0 1 5.5 -3.2 7.1"
        mat = parse_transformation(original_str)
        formatted = format_transformation(mat)
        mat2 = parse_transformation(formatted)
        for r in range(4):
            for c in range(4):
                self.assertAlmostEqual(mat[r][c], mat2[r][c], places=5)

    def test_translation_preserved(self):
        mat = mathutils.Matrix.Identity(4)
        mat[0][3] = 100.0
        mat[1][3] = 200.0
        mat[2][3] = 300.0
        formatted = format_transformation(mat)
        restored = parse_transformation(formatted)
        self.assertAlmostEqual(restored[0][3], 100.0, places=5)
        self.assertAlmostEqual(restored[1][3], 200.0, places=5)
        self.assertAlmostEqual(restored[2][3], 300.0, places=5)


# ============================================================================
# resolve_extension_prefixes
# ============================================================================

class TestResolveExtensionPrefixes(unittest.TestCase):

    def _make_root(self, **xmlns_attrs):
        """Create a dummy XML root element with xmlns attributes."""
        xml_str = "<model"
        for prefix, uri in xmlns_attrs.items():
            xml_str += f' xmlns:{prefix}="{uri}"'
        xml_str += " />"
        return ET.fromstring(xml_str)

    def test_empty_string(self):
        root = self._make_root()
        result = resolve_extension_prefixes(root, "")
        self.assertEqual(result, set())

    def test_known_prefix_p(self):
        """Known prefix 'p' should resolve to PRODUCTION_NAMESPACE."""
        root = self._make_root()
        result = resolve_extension_prefixes(root, "p")
        self.assertIn(PRODUCTION_NAMESPACE, result)

    def test_known_prefix_m(self):
        root = self._make_root()
        result = resolve_extension_prefixes(root, "m")
        self.assertIn(MATERIAL_NAMESPACE, result)

    def test_multiple_prefixes(self):
        root = self._make_root()
        result = resolve_extension_prefixes(root, "p m")
        self.assertEqual(len(result), 2)


# ============================================================================
# is_supported
# ============================================================================

class TestIsSupported(unittest.TestCase):

    def test_no_required_extensions(self):
        self.assertTrue(is_supported(""))

    def test_supported_extension(self):
        root = ET.fromstring("<model />")
        self.assertTrue(is_supported("p", root))

    def test_unsupported_extension(self):
        self.assertFalse(is_supported("http://example.com/unknown", root=None))


# ============================================================================
# read_metadata
# ============================================================================

class TestReadMetadata(unittest.TestCase):

    def _make_node_with_metadata(self, entries):
        """Create an XML node with <metadata> children."""
        ns = MODEL_NAMESPACE
        xml_str = f'<root xmlns="{ns}">'
        for e in entries:
            attrs = f'name="{e["name"]}"'
            if "preserve" in e:
                attrs += f' preserve="{e["preserve"]}"'
            if "type" in e:
                attrs += f' type="{e["type"]}"'
            value = e.get("value", "")
            xml_str += f"<metadata {attrs}>{value}</metadata>"
        xml_str += "</root>"
        return ET.fromstring(xml_str)

    def test_empty_node(self):
        node = ET.fromstring("<root />")
        metadata = read_metadata(node)
        self.assertEqual(len(metadata), 0)

    def test_single_entry(self):
        node = self._make_node_with_metadata([
            {"name": "Title", "value": "Test Model"},
        ])
        metadata = read_metadata(node)
        self.assertIn("Title", metadata)
        self.assertEqual(metadata["Title"].value, "Test Model")

    def test_preserve_flag(self):
        node = self._make_node_with_metadata([
            {"name": "CustomKey", "preserve": "1", "value": "keep"},
        ])
        metadata = read_metadata(node)
        self.assertTrue(metadata["CustomKey"].preserve)

    def test_missing_name_discarded(self):
        """Metadata entry without name attribute is silently discarded."""
        ns = MODEL_NAMESPACE
        xml_str = f'<root xmlns="{ns}"><metadata>no name</metadata></root>'
        node = ET.fromstring(xml_str)
        metadata = read_metadata(node)
        self.assertEqual(len(metadata), 0)


if __name__ == "__main__":
    unittest.main()
