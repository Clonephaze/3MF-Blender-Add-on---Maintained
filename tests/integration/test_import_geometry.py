"""
Integration tests for ``io_mesh_3mf.import_3mf.geometry``.

Tests vertex and triangle parsing from crafted XML snippets using a
minimal :class:`ImportContext`.  Runs inside real Blender.
"""

import unittest
import xml.etree.ElementTree as ET

from io_mesh_3mf.import_3mf.context import ImportContext, ImportOptions
from io_mesh_3mf.import_3mf.geometry import read_vertices
from io_mesh_3mf.common.constants import MODEL_NAMESPACE


def _make_ctx() -> ImportContext:
    """Create a minimal ImportContext for function-level tests."""
    return ImportContext(options=ImportOptions(), operator=None)


def _make_object_node(vertices_xml: str = "", triangles_xml: str = "") -> ET.Element:
    """Build a minimal ``<object>`` element with given child XML.

    :param vertices_xml: ``<vertex ... />`` tags (without wrapper).
    :param triangles_xml: ``<triangle ... />`` tags (without wrapper).
    :return: Parsed ``<object>`` element.
    """
    ns = MODEL_NAMESPACE
    xml_str = (
        f'<object xmlns="{ns}">'
        f"  <mesh>"
        f"    <vertices>{vertices_xml}</vertices>"
        f"    <triangles>{triangles_xml}</triangles>"
        f"  </mesh>"
        f"</object>"
    )
    return ET.fromstring(xml_str)


# ============================================================================
# read_vertices
# ============================================================================

class TestReadVertices(unittest.TestCase):
    """read_vertices() with crafted XML."""

    def test_empty_vertices(self):
        node = _make_object_node()
        ctx = _make_ctx()
        result = read_vertices(ctx, node)
        self.assertEqual(result, [])

    def test_single_vertex(self):
        verts_xml = '<vertex x="1.0" y="2.0" z="3.0" />'
        node = _make_object_node(vertices_xml=verts_xml)
        ctx = _make_ctx()
        result = read_vertices(ctx, node)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0][0], 1.0)
        self.assertAlmostEqual(result[0][1], 2.0)
        self.assertAlmostEqual(result[0][2], 3.0)

    def test_multiple_vertices(self):
        verts_xml = (
            '<vertex x="0" y="0" z="0" />'
            '<vertex x="1" y="0" z="0" />'
            '<vertex x="0" y="1" z="0" />'
        )
        node = _make_object_node(vertices_xml=verts_xml)
        ctx = _make_ctx()
        result = read_vertices(ctx, node)
        self.assertEqual(len(result), 3)

    def test_missing_coordinate_defaults_to_zero(self):
        """Vertex missing an attribute should default to 0."""
        verts_xml = '<vertex x="5" z="10" />'  # missing y
        node = _make_object_node(vertices_xml=verts_xml)
        ctx = _make_ctx()
        result = read_vertices(ctx, node)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0][0], 5.0)
        self.assertAlmostEqual(result[0][1], 0.0)  # defaulted
        self.assertAlmostEqual(result[0][2], 10.0)

    def test_non_numeric_coordinate(self):
        """Non-numeric coordinate should default to 0."""
        verts_xml = '<vertex x="abc" y="2" z="3" />'
        node = _make_object_node(vertices_xml=verts_xml)
        ctx = _make_ctx()
        result = read_vertices(ctx, node)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0][0], 0.0)  # invalid → default

    def test_float_precision(self):
        verts_xml = '<vertex x="1.123456789" y="-0.000001" z="99999.99" />'
        node = _make_object_node(vertices_xml=verts_xml)
        ctx = _make_ctx()
        result = read_vertices(ctx, node)
        self.assertAlmostEqual(result[0][0], 1.123456789, places=6)
        self.assertAlmostEqual(result[0][1], -0.000001, places=6)
        self.assertAlmostEqual(result[0][2], 99999.99, places=2)

    def test_negative_coordinates(self):
        verts_xml = '<vertex x="-1" y="-2" z="-3" />'
        node = _make_object_node(vertices_xml=verts_xml)
        ctx = _make_ctx()
        result = read_vertices(ctx, node)
        self.assertAlmostEqual(result[0][0], -1.0)
        self.assertAlmostEqual(result[0][1], -2.0)
        self.assertAlmostEqual(result[0][2], -3.0)


if __name__ == "__main__":
    unittest.main()
