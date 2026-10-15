"""Orca/Bambu unit conversion must not become an instance scale (issue #38)."""

import math
import zipfile
import xml.etree.ElementTree as ET

import bpy
from mathutils import Matrix, Vector

from io_mesh_3mf.api import export_3mf
from io_mesh_3mf.common.constants import MODEL_NAMESPACE, PRODUCTION_NAMESPACE
from io_mesh_3mf.common.xml import parse_transformation
from test_base import Blender3mfTestCase


NS = f"{{{MODEL_NAMESPACE}}}"
PATH = f"{{{PRODUCTION_NAMESPACE}}}path"


class OrcaScaleTests(Blender3mfTestCase):
    def _cube(self, size, location=(0, 0, 0)):
        bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        cube = bpy.context.object
        cube.data.materials.append(self.create_red_material())
        return cube

    def _units(self, scale_length):
        units = bpy.context.scene.unit_settings
        units.system = "METRIC"
        units.scale_length = scale_length
        units.length_unit = "MILLIMETERS"

    def _export(self, **kwargs):
        result = export_3mf(
            str(self.temp_file), thumbnail_mode="NONE", **kwargs
        )
        self.assertEqual(result.status, "FINISHED")
        records = []
        with zipfile.ZipFile(self.temp_file) as archive:
            root = ET.fromstring(archive.read("3D/3dmodel.model"))
            objects = {
                obj.get("id"): obj for obj in root.findall(f"{NS}resources/{NS}object")
            }
            transforms = [
                parse_transformation(elem.get("transform", ""))
                for elem in root.iter()
                if elem.tag in (f"{NS}item", f"{NS}component")
            ]
            for item in root.findall(f"{NS}build/{NS}item"):
                item_matrix = parse_transformation(item.get("transform", ""))
                wrapper = objects[item.get("objectid")]
                for component in wrapper.findall(f"{NS}components/{NS}component"):
                    model = ET.fromstring(
                        archive.read(component.get(PATH).lstrip("/"))
                    )
                    vertices = [
                        Vector(tuple(float(v.get(axis)) for axis in "xyz"))
                        for v in model.findall(f".//{NS}vertex")
                    ]
                    matrix = item_matrix @ parse_transformation(
                        component.get("transform", "")
                    )
                    records.append({
                        "vertices": vertices,
                        "matrix": matrix,
                        "world": [matrix @ v for v in vertices],
                        "triangles": model.findall(f".//{NS}triangle"),
                    })
            settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
        return records, transforms, settings

    def _assert_matrix(self, actual, expected):
        for row in range(3):
            for col in range(3):
                self.assertAlmostEqual(actual[row][col], expected[row][col], delta=1e-6)

    def _bounds(self, vertices):
        return (
            Vector(tuple(min(v[i] for v in vertices) for i in range(3))),
            Vector(tuple(max(v[i] for v in vertices) for i in range(3))),
        )

    def _assert_vector(self, actual, expected, tolerance=1e-4):
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, delta=tolerance)

    def _assert_legacy_world(self, records, objects, unit_scale, bed=(128, 128)):
        """Compare against the pre-fix S @ matrix_world @ local convention."""
        scale = Matrix.Scale(unit_scale, 4)
        corners = [
            scale @ obj.matrix_world @ Vector(corner)
            for obj in objects for corner in obj.bound_box
        ]
        low, high = self._bounds(corners)
        offset = Vector((
            bed[0] - (low.x + high.x) / 2,
            bed[1] - (low.y + high.y) / 2,
            -low.z,
        ))
        self.assertEqual(len(records), len(objects))
        for record, obj in zip(records, objects):
            self.assertEqual(len(record["world"]), len(obj.data.vertices))
            for actual, vertex in zip(record["world"], obj.data.vertices):
                expected = scale @ obj.matrix_world @ vertex.co + offset
                self._assert_vector(actual, expected, tolerance=5e-4)

    def test_scene_unit_conversion_is_baked(self):
        for scale_length, size, expected_mm in (
            (1.0, 0.02, 20), (1.0, 2, 2000),
            (0.001, 20, 20), (0.01, 2, 20),
        ):
            with self.subTest(scale_length=scale_length, size=size):
                self.clean_scene()
                self._units(scale_length)
                cube = self._cube(size)
                records, transforms, settings = self._export()
                for matrix in transforms:
                    self._assert_matrix(matrix, Matrix.Identity(4))
                low, high = self._bounds(records[0]["vertices"])
                self._assert_vector(high - low, (expected_mm,) * 3)
                low, high = self._bounds(records[0]["world"])
                self._assert_vector(high - low, (expected_mm,) * 3)
                self._assert_legacy_world(records, [cube], scale_length / 0.001)
                assemble = settings.find("assemble/assemble_item")
                self.assertIsNotNone(assemble)
                self._assert_matrix(
                    parse_transformation(assemble.get("transform")), Matrix.Identity(4)
                )

    def test_real_object_scale_is_preserved(self):
        self._units(1.0)
        cube = self._cube(0.02)
        cube.scale = (2, 2, 2)
        bpy.context.view_layer.update()
        records, _, _ = self._export()
        self._assert_matrix(records[0]["matrix"], Matrix.Scale(2, 4))
        low, high = self._bounds(records[0]["vertices"])
        self._assert_vector(high - low, (20, 20, 20))
        low, high = self._bounds(records[0]["world"])
        self._assert_vector(high - low, (40, 40, 40))
        self._assert_legacy_world(records, [cube], 1000)

    def test_global_scale_is_baked(self):
        self._units(1.0)
        cube = self._cube(0.02)
        for global_scale in (0.5, 2.0):
            with self.subTest(global_scale=global_scale):
                records, transforms, _ = self._export(global_scale=global_scale)
                for matrix in transforms:
                    self._assert_matrix(matrix, Matrix.Identity(4))
                for key in ("vertices", "world"):
                    low, high = self._bounds(records[0][key])
                    self._assert_vector(high - low, (20 * global_scale,) * 3)
                self._assert_legacy_world(records, [cube], 1000 * global_scale)

    def test_group_and_ungrouped_positions_match_legacy(self):
        self._units(1.0)
        bpy.ops.object.empty_add(location=(0.03, -0.02, 0.01))
        parent = bpy.context.object
        first = self._cube(0.02, (0.04, 0.01, 0.03))
        second = self._cube(0.01, (-0.02, 0.03, 0.05))
        first.parent = parent
        second.parent = parent
        third = self._cube(0.015, (0.1, -0.04, 0.02))
        bpy.context.view_layer.update()
        records, transforms, _ = self._export(objects=[parent, first, second, third])
        for matrix in transforms:
            self._assert_matrix(matrix, Matrix.Identity(4))
        self._assert_legacy_world(records, [first, second, third], 1000)
        low, high = self._bounds([v for r in records for v in r["world"]])
        self._assert_vector(
            ((low.x + high.x) / 2, (low.y + high.y) / 2, low.z),
            (128, 128, 0),
        )

    def test_rotation_nonuniform_scale_and_translation(self):
        self._units(0.01)
        cube = self._cube(2, (3, -5, 7))
        cube.rotation_euler = (0.2, -0.3, math.pi / 4)
        cube.scale = (2, 0.5, 1.5)
        bpy.context.view_layer.update()
        records, _, _ = self._export()
        self._assert_matrix(records[0]["matrix"], cube.matrix_world)
        self._assert_legacy_world(records, [cube], 10)

    def test_custom_template_centers_at_origin(self):
        self._units(1.0)
        first = self._cube(0.02, (0.03, -0.02, 0.04))
        second = self._cube(0.01, (-0.01, 0.04, 0.02))
        template = self.temp_file.with_suffix(".json")
        template.write_text("{}", encoding="utf-8")
        self.addCleanup(template.unlink)
        records, _, _ = self._export(project_template=str(template))
        self._assert_legacy_world(records, [first, second], 1000, bed=(0, 0))
        low, high = self._bounds([v for r in records for v in r["world"]])
        self._assert_vector(
            ((low.x + high.x) / 2, (low.y + high.y) / 2, low.z), (0, 0, 0)
        )

    def test_coordinate_precision_remains_significant_digits(self):
        self._units(1.0)
        cube = self._cube(0.0246912)
        records, _, _ = self._export(coordinate_precision=6)
        self.assertAlmostEqual(max(v.x for v in records[0]["vertices"]), 12.3456, places=5)
        self._assert_legacy_world(records, [cube], 1000)

    def test_roundtrip_preserves_physical_size(self):
        for scale_length, size in ((1.0, 0.02), (0.001, 20), (0.01, 2)):
            with self.subTest(scale_length=scale_length):
                self.clean_scene()
                self._units(scale_length)
                self._cube(size)
                self._export()
                self.clean_scene()
                self._units(scale_length)
                result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
                self.assertIn("FINISHED", result)
                meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
                self.assertEqual(len(meshes), 1)
                self._assert_vector(meshes[0].dimensions, (size,) * 3)

    def test_evaluated_modifier_vertices_are_baked(self):
        self._units(1.0)
        cube = self._cube(0.02)
        modifier = cube.modifiers.new("Triangulate", "TRIANGULATE")
        self.assertEqual(modifier.type, "TRIANGULATE")
        records, transforms, _ = self._export(use_mesh_modifiers=True)
        for matrix in transforms:
            self._assert_matrix(matrix, Matrix.Identity(4))
        low, high = self._bounds(records[0]["vertices"])
        self._assert_vector(high - low, (20, 20, 20))
        self.assertEqual(len(records[0]["triangles"]), 12)
        self._assert_legacy_world(records, [cube], 1000)

    def test_paint_subdivision_geometry_and_encoding_are_unit_independent(self):
        self._units(1.0)
        cube = self._cube(0.02)
        image = bpy.data.images.new("ScalePaint", width=128, height=128)
        image.pixels[:] = [1.0, 0.0, 0.0, 1.0] * (128 * 128)
        image.update()
        material = cube.data.materials[0]
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        cube.data["3mf_is_paint_texture"] = True
        cube.data["3mf_paint_extruder_colors"] = "{1: '#FF0000'}"
        cube.data["3mf_paint_default_extruder"] = 1
        painted = []
        for scale_length in (1.0, 0.001):
            self._units(scale_length)
            records, transforms, _ = self._export(
                use_orca_format="PAINT", subdivision_depth=2
            )
            for matrix in transforms:
                self._assert_matrix(matrix, Matrix.Identity(4))
            low, high = self._bounds(records[0]["vertices"])
            self._assert_vector(high - low, (0.02 * scale_length / 0.001,) * 3)
            self.assertGreater(len(records[0]["triangles"]), 12)
            codes = [tri.get("paint_color") for tri in records[0]["triangles"]]
            self.assertTrue(all(codes))
            painted.append(codes)
        self.assertEqual(painted[0], painted[1])
