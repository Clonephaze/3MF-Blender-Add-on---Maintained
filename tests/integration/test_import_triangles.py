"""
Integration tests for ``io_mesh_3mf.import_3mf.geometry.read_triangles``.

This is the core import function (~200 lines) that was previously untested.
Tests cover: basic triangle parsing, error handling, material resolution,
paint_color / mmu_segmentation attributes, seam/support codes, and the
``paint_only`` mode used by Production Extension external models.
"""

import unittest
import xml.etree.ElementTree as ET

from io_mesh_3mf.import_3mf.context import ImportContext, ImportOptions
from io_mesh_3mf.import_3mf.geometry import read_triangles
from io_mesh_3mf.common.constants import MODEL_NAMESPACE
from io_mesh_3mf.common.types import ResourceMaterial


def _ctx(**option_overrides) -> ImportContext:
    """Create a minimal ImportContext with optional overrides."""
    opts = ImportOptions(**option_overrides)
    return ImportContext(options=opts, operator=None)


def _obj_node(vertices_xml: str = "", triangles_xml: str = "") -> ET.Element:
    """Build a ``<object>`` element with given child XML."""
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


# Four vertices forming a tetrahedron (simplest closed mesh)
_QUAD_VERTS = (
    '<vertex x="0" y="0" z="0" />'
    '<vertex x="1" y="0" z="0" />'
    '<vertex x="0" y="1" z="0" />'
    '<vertex x="0" y="0" z="1" />'
)


class ReadTrianglesBasicTests(unittest.TestCase):
    """Basic triangle parsing without materials."""

    def test_empty_triangles(self):
        """No triangles returns empty lists."""
        node = _obj_node(vertices_xml=_QUAD_VERTS)
        ctx = _ctx()
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None
        )
        self.assertEqual(tris, [])
        self.assertEqual(mats, [])
        self.assertEqual(uvs, [])

    def test_single_triangle(self):
        """Parse one well-formed triangle."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None
        )
        self.assertEqual(len(tris), 1)
        self.assertEqual(tris[0], (0, 1, 2))

    def test_multiple_triangles(self):
        """Parse several triangles."""
        tri_xml = (
            '<triangle v1="0" v2="1" v3="2" />'
            '<triangle v1="0" v2="1" v3="3" />'
            '<triangle v1="0" v2="2" v3="3" />'
            '<triangle v1="1" v2="2" v3="3" />'
        )
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None
        )
        self.assertEqual(len(tris), 4)

    def test_default_material_assigned(self):
        """When no pid/p1, the default material is used."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        default_mat = ResourceMaterial(name="Default", color="#FF0000")
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, default_mat, None
        )
        self.assertEqual(len(mats), 1)
        self.assertIs(mats[0], default_mat)

    def test_default_extruder_is_1(self):
        """Default extruder should be 1 when not overridden."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None
        )
        self.assertEqual(defext, 1)


class ReadTrianglesErrorHandlingTests(unittest.TestCase):
    """Error paths in read_triangles."""

    def test_negative_vertex_index_skipped(self):
        """Triangles with negative indices are skipped."""
        tri_xml = '<triangle v1="-1" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None
        )
        self.assertEqual(len(tris), 0, "Negative index triangle should be skipped")

    def test_missing_vertex_key_skipped(self):
        """Triangles missing v1/v2/v3 are skipped."""
        # Only v1 and v2, missing v3
        tri_xml = '<triangle v1="0" v2="1" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None
        )
        self.assertEqual(len(tris), 0, "Triangle missing v3 should be skipped")

    def test_non_integer_vertex_skipped(self):
        """Non-integer vertex references are skipped."""
        tri_xml = '<triangle v1="abc" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None
        )
        self.assertEqual(len(tris), 0, "Non-integer vertex should be skipped")

    def test_valid_triangle_after_invalid(self):
        """Valid triangles are still parsed after invalid ones."""
        tri_xml = (
            '<triangle v1="-1" v2="1" v3="2" />'  # invalid
            '<triangle v1="0" v2="1" v3="2" />'   # valid
        )
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None
        )
        self.assertEqual(len(tris), 1, "Should still parse the valid triangle")
        self.assertEqual(tris[0], (0, 1, 2))


class ReadTrianglesMaterialTests(unittest.TestCase):
    """Material resolution via pid/p1 attributes."""

    def test_basematerial_resolution(self):
        """Triangle with pid/p1 resolves to correct material."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" pid="1" p1="0" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        red = ResourceMaterial(name="Red", color="#FF0000")
        blue = ResourceMaterial(name="Blue", color="#0000FF")
        ctx.resource_materials["1"] = {0: red, 1: blue}

        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, "1"
        )
        self.assertEqual(len(mats), 1)
        self.assertIs(mats[0], red)

    def test_basematerial_second_index(self):
        """p1=1 picks the second material in the group."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" pid="1" p1="1" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        red = ResourceMaterial(name="Red", color="#FF0000")
        blue = ResourceMaterial(name="Blue", color="#0000FF")
        ctx.resource_materials["1"] = {0: red, 1: blue}

        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, "1"
        )
        self.assertEqual(len(mats), 1)
        self.assertIs(mats[0], blue)

    def test_missing_material_uses_default(self):
        """Unknown pid falls back to default material."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" pid="999" p1="0" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        default = ResourceMaterial(name="Fallback", color="#CCCCCC")

        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, default, None
        )
        self.assertEqual(len(mats), 1)
        self.assertIs(mats[0], default)

    def test_object_level_pid_as_fallback(self):
        """material_pid is used when triangle has no pid attribute."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" p1="0" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        mat = ResourceMaterial(name="ObjMat", color="#00FF00")
        ctx.resource_materials["5"] = {0: mat}

        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, "5"
        )
        self.assertEqual(len(mats), 1)
        self.assertIs(mats[0], mat)

    def test_import_materials_none_skips_materials(self):
        """import_materials='NONE' ignores pid/p1 entirely."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" pid="1" p1="0" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx(import_materials="NONE")
        mat = ResourceMaterial(name="Red", color="#FF0000")
        ctx.resource_materials["1"] = {0: mat}
        default = ResourceMaterial(name="Default", color="#FFFFFF")

        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, default, "1"
        )
        # Should use the default, not look up the material
        self.assertIs(mats[0], default)


class ReadTrianglesPaintCodeTests(unittest.TestCase):
    """Paint code / segmentation attribute handling."""

    def test_paint_mode_stores_segmentation_string(self):
        """In PAINT mode, paint_color value is stored as segmentation string."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" paint_color="4" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx(import_materials="PAINT")
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]

        tris, mats, uvs, verts_out, seg, seam, support, defext = read_triangles(
            ctx, node, None, None, vertex_coords=verts
        )
        self.assertEqual(len(tris), 1)
        self.assertIn(0, seg, "Segmentation string should be stored at face index 0")
        self.assertEqual(seg[0], "4")

    def test_paint_mode_seam_support_stored(self):
        """In PAINT mode, seam/support codes are stored alongside paint_color."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" paint_color="4" paint_seam="AA" paint_supports="BB" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx(import_materials="PAINT")
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]

        tris, mats, uvs, verts_out, seg, seam, support, defext = read_triangles(
            ctx, node, None, None, vertex_coords=verts
        )
        self.assertIn(0, seam)
        self.assertEqual(seam[0], "AA")
        self.assertIn(0, support)
        self.assertEqual(support[0], "BB")

    def test_paint_only_mode(self):
        """paint_only=True still parses paint_color for external models."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" paint_color="8" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx(import_materials="PAINT")
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]

        tris, mats, uvs, verts_out, seg, seam, support, defext = read_triangles(
            ctx, node, None, None, vertex_coords=verts, paint_only=True
        )
        self.assertEqual(len(tris), 1)
        self.assertIn(0, seg)
        self.assertEqual(seg[0], "8")

    def test_no_paint_code_no_segmentation(self):
        """Triangles without paint_color have no segmentation entry."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx(import_materials="PAINT")
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]

        tris, mats, uvs, verts_out, seg, seam, support, defext = read_triangles(
            ctx, node, None, None, vertex_coords=verts
        )
        self.assertEqual(len(seg), 0)

    def test_default_extruder_from_context(self):
        """Default extruder is read from ctx.object_default_extruders."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        ctx.object_default_extruders["42"] = 3

        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None, object_id="42"
        )
        self.assertEqual(defext, 3)

    def test_seam_support_without_paint(self):
        """Seam/support on triangles without paint_color in PAINT mode."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" paint_seam="CC" paint_supports="DD" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx(import_materials="PAINT")
        verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]

        tris, mats, uvs, verts_out, seg, seam, support, defext = read_triangles(
            ctx, node, None, None, vertex_coords=verts
        )
        # Triangle is added via normal path, seam/support stored at face index
        self.assertEqual(len(tris), 1)
        self.assertIn(0, seam)
        self.assertEqual(seam[0], "CC")
        self.assertIn(0, support)
        self.assertEqual(support[0], "DD")


class ReadTrianglesReturnStructureTests(unittest.TestCase):
    """Verify the 8-tuple return structure is correct."""

    def test_return_type(self):
        """All 8 return values have correct types."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        result = read_triangles(ctx, node, None, None)

        self.assertEqual(len(result), 8)
        self.assertIsInstance(result[0], list)  # triangles
        self.assertIsInstance(result[1], list)  # materials
        self.assertIsInstance(result[2], list)  # triangle_uvs
        self.assertIsInstance(result[3], list)  # vertex_list
        self.assertIsInstance(result[4], dict)  # segmentation_strings
        self.assertIsInstance(result[5], dict)  # seam_strings
        self.assertIsInstance(result[6], dict)  # support_strings
        self.assertIsInstance(result[7], int)   # default_extruder

    def test_vertex_list_propagated(self):
        """Vertex coords passed in are returned (possibly extended)."""
        tri_xml = '<triangle v1="0" v2="1" v3="2" />'
        node = _obj_node(vertices_xml=_QUAD_VERTS, triangles_xml=tri_xml)
        ctx = _ctx()
        input_verts = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]

        tris, mats, uvs, verts, seg, seam, support, defext = read_triangles(
            ctx, node, None, None, vertex_coords=input_verts
        )
        self.assertGreaterEqual(len(verts), 3)


if __name__ == "__main__":
    unittest.main()
