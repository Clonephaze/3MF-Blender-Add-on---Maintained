"""
Integration tests for ``io_mesh_3mf.export_3mf.geometry``.

Tests geometry export functions using real Blender mesh objects.
Runs inside real Blender (``--background --factory-startup``).
"""

import unittest
import xml.etree.ElementTree as ET

import bpy

from test_base import Blender3mfTestCase

from io_mesh_3mf.common.constants import MODEL_NAMESPACE
from io_mesh_3mf.export_3mf.geometry import (
    write_vertices,
    check_non_manifold_geometry,
)


# ============================================================================
# write_vertices
# ============================================================================

class TestWriteVertices(Blender3mfTestCase):
    """write_vertices() with real Blender mesh data."""

    def _create_mesh_element(self):
        """Create an empty <mesh> XML element."""
        return ET.SubElement(
            ET.Element(f"{{{MODEL_NAMESPACE}}}root"),
            f"{{{MODEL_NAMESPACE}}}mesh",
        )

    def test_cube_vertices(self):
        """Writing a cube's vertices should produce 8 vertex elements."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        mesh_element = self._create_mesh_element()
        write_vertices(mesh_element, mesh.vertices, "STANDARD", 6)

        verts_elem = mesh_element.find(f"{{{MODEL_NAMESPACE}}}vertices")
        self.assertIsNotNone(verts_elem)
        vertex_elements = verts_elem.findall(f"{{{MODEL_NAMESPACE}}}vertex")
        self.assertEqual(len(vertex_elements), 8)

    def test_coordinates_present(self):
        """Each vertex element should have x, y, z attributes."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        mesh_element = self._create_mesh_element()
        write_vertices(mesh_element, mesh.vertices, "PAINT", 6)

        verts_elem = mesh_element.find(f"{{{MODEL_NAMESPACE}}}vertices")
        vertex_elements = verts_elem.findall(f"{{{MODEL_NAMESPACE}}}vertex")

        for ve in vertex_elements:
            # In PAINT mode, attributes are unqualified ("x" not "{ns}x")
            self.assertIn("x", ve.attrib)
            self.assertIn("y", ve.attrib)
            self.assertIn("z", ve.attrib)

    def test_coordinate_precision(self):
        """Coordinates should respect the precision parameter."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        mesh_element = self._create_mesh_element()
        write_vertices(mesh_element, mesh.vertices, "PAINT", 3)

        verts_elem = mesh_element.find(f"{{{MODEL_NAMESPACE}}}vertices")
        ve = verts_elem.findall(f"{{{MODEL_NAMESPACE}}}vertex")[0]
        x_val = ve.attrib["x"]
        # At most 3 decimal digits
        if "." in x_val:
            decimal_part = x_val.split(".")[1]
            self.assertLessEqual(len(decimal_part), 3)

    def test_standard_mode_namespaced_attrs(self):
        """In STANDARD mode, attributes should be namespace-qualified."""
        bpy.ops.mesh.primitive_cube_add()
        mesh = bpy.context.object.data

        mesh_element = self._create_mesh_element()
        write_vertices(mesh_element, mesh.vertices, "STANDARD", 6)

        verts_elem = mesh_element.find(f"{{{MODEL_NAMESPACE}}}vertices")
        ve = verts_elem.findall(f"{{{MODEL_NAMESPACE}}}vertex")[0]
        # In STANDARD mode, attributes are {namespace}x
        self.assertIn(f"{{{MODEL_NAMESPACE}}}x", ve.attrib)


# ============================================================================
# check_non_manifold_geometry
# ============================================================================

class TestCheckNonManifoldGeometry(Blender3mfTestCase):
    """check_non_manifold_geometry() with real Blender meshes."""

    def test_manifold_cube(self):
        """A default cube should be manifold."""
        bpy.ops.mesh.primitive_cube_add()
        objects = [bpy.context.object]
        result = check_non_manifold_geometry(objects, use_mesh_modifiers=False)
        self.assertEqual(result, [])

    def test_manifold_sphere(self):
        """A UV sphere should be manifold."""
        bpy.ops.mesh.primitive_uv_sphere_add()
        objects = [bpy.context.object]
        result = check_non_manifold_geometry(objects, use_mesh_modifiers=False)
        self.assertEqual(result, [])

    def test_non_mesh_ignored(self):
        """Non-mesh objects (empties, cameras) should be skipped."""
        bpy.ops.object.empty_add()
        objects = [bpy.context.object]
        result = check_non_manifold_geometry(objects, use_mesh_modifiers=False)
        self.assertEqual(result, [])

    def test_non_manifold_detected(self):
        """A plane (open surface) should be detected as non-manifold."""
        bpy.ops.mesh.primitive_plane_add()
        objects = [bpy.context.object]
        result = check_non_manifold_geometry(objects, use_mesh_modifiers=False)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
