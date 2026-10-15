"""Unit conversion and transform regressions for Standard 3MF export."""

import zipfile
import xml.etree.ElementTree as ET

import bpy
from mathutils import Matrix, Vector

from io_mesh_3mf.api import export_3mf
from io_mesh_3mf.common.constants import MODEL_NAMESPACE
from io_mesh_3mf.common.xml import parse_transformation
from test_base import Blender3mfTestCase


NS = f"{{{MODEL_NAMESPACE}}}"


class StandardScaleTests(Blender3mfTestCase):
    def _cube(self, size=0.02, location=(0, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        return bpy.context.object

    def _export(self, **kwargs):
        result = export_3mf(str(self.temp_file), thumbnail_mode="NONE", **kwargs)
        self.assertEqual(result.status, "FINISHED")
        with zipfile.ZipFile(self.temp_file) as archive:
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
            self.assertFalse(any(n.startswith("3D/Objects/") for n in archive.namelist()))
        objects = {
            obj.get("id"): obj for obj in root.findall(f"{NS}resources/{NS}object")
        }
        world = []

        def visit(object_id, matrix):
            obj = objects[object_id]
            for vertex in obj.findall(f"{NS}mesh/{NS}vertices/{NS}vertex"):
                co = Vector(tuple(float(vertex.get(axis)) for axis in "xyz"))
                world.append(matrix @ co)
            for component in obj.findall(f"{NS}components/{NS}component"):
                visit(
                    component.get("objectid"),
                    matrix @ parse_transformation(component.get("transform", "")),
                )

        for item in root.findall(f"{NS}build/{NS}item"):
            visit(item.get("objectid"), parse_transformation(item.get("transform", "")))
        return root, world

    def _assert_linear(self, matrix, expected):
        for row in range(3):
            for col in range(3):
                self.assertAlmostEqual(matrix[row][col], expected[row][col], delta=1e-6)

    def _assert_identity_transforms(self, root):
        for elem in root.iter():
            if elem.tag in (f"{NS}item", f"{NS}component"):
                self._assert_linear(
                    parse_transformation(elem.get("transform", "")), Matrix.Identity(4)
                )

    def _assert_legacy_world(self, world, meshes, scale):
        """World geometry stays identical to the previous scaled-transform convention."""
        corners = [
            obj.matrix_world @ Vector(corner) * scale
            for obj in meshes for corner in obj.bound_box
        ]
        low = Vector(tuple(min(v[i] for v in corners) for i in range(3)))
        high = Vector(tuple(max(v[i] for v in corners) for i in range(3)))
        offset = Vector((-(low.x + high.x) / 2, -(low.y + high.y) / 2, -low.z))
        expected = [
            obj.matrix_world @ vertex.co * scale + offset
            for obj in meshes for vertex in obj.data.vertices
        ]
        self.assertEqual(len(world), len(expected))
        for vertex in expected:
            self.assertLess(min((vertex - actual).length for actual in world), 5e-4)

    def test_default_auto_export_bakes_units(self):
        cube = self._cube()
        root, world = self._export()
        self._assert_identity_transforms(root)
        vertices = root.findall(f".//{NS}vertex")
        self.assertEqual(len(vertices), 8)
        self.assertAlmostEqual(max(float(v.get("x")) for v in vertices), 10, places=5)
        self._assert_legacy_world(world, [cube], 1000)

    def test_units_and_global_scale(self):
        for scale_length in (1.0, 0.001, 0.01):
            for global_scale in (0.5, 1.0, 2.0):
                with self.subTest(scale_length=scale_length, global_scale=global_scale):
                    self.clean_scene()
                    bpy.context.scene.unit_settings.scale_length = scale_length
                    cube = self._cube(0.02 / scale_length, (0.03, -0.04, 0.05))
                    root, world = self._export(
                        use_orca_format="STANDARD", global_scale=global_scale
                    )
                    self._assert_identity_transforms(root)
                    self._assert_legacy_world(
                        world, [cube], global_scale * scale_length / 0.001
                    )
                    for axis in range(3):
                        extent = max(v[axis] for v in world) - min(v[axis] for v in world)
                        self.assertAlmostEqual(extent, 20 * global_scale, delta=1e-4)

    def test_object_rotation_and_scale_are_preserved(self):
        cube = self._cube(location=(0.03, -0.04, 0.05))
        cube.scale = (2, 0.5, 1.5)
        cube.rotation_euler = (0.2, 0.3, 0.4)
        bpy.context.view_layer.update()
        root, world = self._export()
        item = root.find(f"{NS}build/{NS}item")
        self._assert_linear(parse_transformation(item.get("transform")), cube.matrix_world)
        self._assert_legacy_world(world, [cube], 1000)

    def test_nested_parenting_and_flattening(self):
        bpy.ops.object.empty_add(location=(0.03, -0.04, 0.05))
        parent = bpy.context.object
        parent.rotation_euler = (0.1, 0.2, 0.3)
        parent.scale = (2, 0.5, 1.5)
        bpy.ops.object.empty_add(location=(0.01, 0.02, -0.01))
        nested = bpy.context.object
        nested.parent = parent
        first = self._cube(location=(0.03, 0.01, 0.02))
        first.parent = nested
        second = self._cube(location=(-0.01, 0.02, 0.03))
        second.parent = parent
        bpy.context.view_layer.update()
        for flatten in (False, True):
            with self.subTest(flatten=flatten):
                root, world = self._export(flatten_hierarchy=flatten)
                self._assert_legacy_world(world, [first, second], 1000)
                if flatten:
                    self.assertFalse(root.findall(f".//{NS}component"))

    def test_mesh_parent_with_mesh_child(self):
        parent = self._cube(location=(0.03, 0, 0.01))
        child = self._cube(location=(0.02, 0.03, 0.04))
        child.parent = parent
        bpy.context.view_layer.update()
        root, world = self._export()
        self._assert_identity_transforms(root)
        self._assert_legacy_world(world, [parent, child], 1000)

    def test_linked_instances_bake_shared_geometry_once(self):
        first = self._cube()
        second = bpy.data.objects.new("Linked", first.data)
        bpy.context.collection.objects.link(second)
        second.location = (0.03, 0.04, 0.05)
        bpy.context.view_layer.update()
        for use_components in (False, True):
            with self.subTest(use_components=use_components):
                root, world = self._export(use_components=use_components)
                self._assert_identity_transforms(root)
                self._assert_legacy_world(world, [first, second], 1000)
                meshes = root.findall(f"{NS}resources/{NS}object/{NS}mesh")
                self.assertEqual(len(meshes), 1 if use_components else 2)

    def test_roundtrip_preserves_size_and_linked_meshes(self):
        for scale_length in (1.0, 0.001, 0.01):
            with self.subTest(scale_length=scale_length):
                self.clean_scene()
                bpy.context.scene.unit_settings.scale_length = scale_length
                size = 0.02 / scale_length
                first = self._cube(size)
                second = bpy.data.objects.new("Linked", first.data)
                bpy.context.collection.objects.link(second)
                second.location.x = size * 2
                bpy.context.view_layer.update()
                self._export()
                self.clean_scene()
                bpy.context.scene.unit_settings.scale_length = scale_length
                result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
                self.assertIn("FINISHED", result)
                meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
                self.assertEqual(len(meshes), 2)
                self.assertEqual(meshes[0].data, meshes[1].data)
                for mesh in meshes:
                    for dimension in mesh.dimensions:
                        self.assertAlmostEqual(dimension, size, delta=1e-4)
