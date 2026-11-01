"""
Regression tests for the Orca ``<assemble>`` section.

Orca moves every loaded mesh's origin to its bounding-box centre.  For
single-volume objects it folds that shift into the build offset but not into
the assemble transform, so the exporter must add ``linear @ mesh_center`` to
the assemble translation or the assembled view is displaced (objects whose
origin is far from their geometry end up in the wrong place).
"""

import re
import unittest
import xml.etree.ElementTree as ET
import zipfile

import bpy
import mathutils
from test_base import Blender3mfTestCase

from io_mesh_3mf.common.xml import parse_transformation


def _cube(name, location, mesh_shift=(0.0, 0.0, 0.0), color=(1.0, 0.0, 0.0)):
    bpy.ops.mesh.primitive_cube_add(location=location)
    cube = bpy.context.object
    cube.name = name
    for vertex in cube.data.vertices:  # move geometry away from the object origin
        vertex.co.x += mesh_shift[0]
        vertex.co.y += mesh_shift[1]
        vertex.co.z += mesh_shift[2]
    mat = bpy.data.materials.new(f"{name}_mat")
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = (*color, 1.0)
    cube.data.materials.append(mat)
    return cube


class AssembleTransformTests(Blender3mfTestCase):
    def _export(self, **options):
        bpy.ops.object.select_all(action="SELECT")
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO", **options
        )
        self.assertIn("FINISHED", result)
        archive = zipfile.ZipFile(str(self.temp_file))
        settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
        main = ET.fromstring(archive.read("3D/3dmodel.model"))
        return archive, settings, main

    @staticmethod
    def _assemble_translations(settings):
        return {
            int(i.get("object_id")): [float(v) for v in i.get("transform").split()][9:12]
            for i in settings.find("assemble").findall("assemble_item")
        }

    @staticmethod
    def _assemble_offsets(settings):
        return {
            int(i.get("object_id")): [float(v) for v in i.get("offset").split()]
            for i in settings.find("assemble").findall("assemble_item")
        }

    @staticmethod
    def _build_translations(main):
        ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        return {
            int(i.get("objectid")): [float(v) for v in i.get("transform").split()][9:12]
            for i in main.find("m:build", ns).findall("m:item", ns)
        }

    @staticmethod
    def _mesh_center(archive, name):
        path = next(
            n for n in archive.namelist()
            if n.startswith("3D/Objects/") and re.match(rf"3D/Objects/{name}_\d+\.model", n)
        )
        root = ET.fromstring(archive.read(path))
        verts = [
            tuple(float(v.get(a)) for a in "xyz")
            for v in root.iter("{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}vertex")
        ]
        return [(min(c) + max(c)) / 2 for c in zip(*verts)]

    def test_assemble_includes_mesh_centre_shift(self):
        _cube("shifted", (0, 0, 0), mesh_shift=(40.0, -15.0, 25.0))
        archive, settings, main = self._export()
        centre = self._mesh_center(archive, "shifted")
        self.assertGreater(abs(centre[0]), 1.0)  # geometry really is off-origin

        (object_id, assemble), = self._assemble_translations(settings).items()
        offset = self._assemble_offsets(settings)[object_id]
        build = self._build_translations(main)[object_id]
        for axis in range(3):
            self.assertAlmostEqual(assemble[axis] - build[axis], centre[axis], places=3)
            self.assertAlmostEqual(offset[axis], 0.0, places=3)

    def test_centred_mesh_has_no_extra_shift(self):
        _cube("centred", (0, 0, 0))
        archive, settings, main = self._export()
        (object_id, assemble), = self._assemble_translations(settings).items()
        offset = self._assemble_offsets(settings)[object_id]
        build = self._build_translations(main)[object_id]
        for axis in range(3):
            self.assertAlmostEqual(assemble[axis], build[axis], places=3)
            self.assertAlmostEqual(offset[axis], 0.0, places=3)

    def test_rotated_object_shift_is_rotated(self):
        cube = _cube("turned", (0, 0, 0), mesh_shift=(30.0, 0.0, 0.0))
        cube.rotation_euler = (0.0, 0.0, 1.5707963)  # 90 degrees about Z
        bpy.context.view_layer.update()
        archive, settings, main = self._export()
        centre = self._mesh_center(archive, "turned")
        (object_id, assemble), = self._assemble_translations(settings).items()
        build = self._build_translations(main)[object_id]
        # local +X shift becomes world +Y after a 90 degree Z rotation
        self.assertAlmostEqual(assemble[0] - build[0], 0.0, places=2)
        self.assertAlmostEqual(assemble[1] - build[1], centre[0], places=2)

    def test_multi_part_assembly_is_not_shifted(self):
        a = _cube("pa", (0, 0, 0), mesh_shift=(20.0, 0.0, 0.0))
        b = _cube("pb", (60, 0, 0), color=(0.0, 1.0, 0.0))
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        b.select_set(True)
        bpy.context.view_layer.objects.active = a
        bpy.ops.threemf.create_assembly()
        archive, settings, main = self._export()
        (object_id, assemble), = self._assemble_translations(settings).items()
        build = self._build_translations(main)[object_id]
        for axis in range(3):
            self.assertAlmostEqual(assemble[axis], build[axis], places=3)

    def test_single_member_assembly_includes_shift(self):
        # A lone object in an assembly is still a single-volume Orca object.
        # Its mesh (a 2 mm cube) is shifted off the object origin; after global
        # centring the cube's bounding-box centre sits at x/y 0 and z 1 (half its
        # height, resting on the bed).  The assemble translation must say so.
        a = _cube("solo", (0, 0, 0), mesh_shift=(20.0, 0.0, 10.0))
        a.scale = (0.001, 0.001, 0.001)
        bpy.context.view_layer.update()
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        bpy.context.view_layer.objects.active = a
        bpy.ops.threemf.create_assembly()
        archive, settings, main = self._export()
        (object_id, assemble), = self._assemble_translations(settings).items()
        offset = self._assemble_offsets(settings)[object_id]
        build = self._build_translations(main)[object_id]
        for axis in range(3):
            self.assertAlmostEqual(offset[axis], 0.0, places=3)
        self.assertAlmostEqual(assemble[0] - build[0], 0.0, places=2)
        self.assertAlmostEqual(assemble[1] - build[1], 0.0, places=2)
        self.assertAlmostEqual(assemble[2] - build[2], 1.0, places=2)

    def test_explosion_offsets_are_centered_and_rotation_independent(self):
        for split in (False, True):
            with self.subTest(split_assembly=split):
                self.clean_scene()
                a = _cube("a", (-0.02, 0, 0), color=(1.0, 0.0, 0.0))
                b = _cube("b", (0.02, 0, 0), color=(0.0, 0.0, 1.0))
                a.scale = b.scale = (0.001, 0.001, 0.001)
                b.rotation_euler.z = 1.5707963
                bpy.context.view_layer.update()
                if split:
                    bpy.ops.object.select_all(action="DESELECT")
                    a.select_set(True)
                    b.select_set(True)
                    bpy.context.view_layer.objects.active = a
                    bpy.ops.threemf.create_assembly()
                archive, settings, main = self._export(plate_per_material=split)
                ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
                components = {
                    obj.get("id"): parse_transformation(
                        obj.find("./m:components/m:component", ns).get("transform", "")
                    )
                    for obj in main.findall("./m:resources/m:object", ns)
                }
                points, shifts = [], []
                for item in settings.findall("./assemble/assemble_item"):
                    matrix = parse_transformation(item.get("transform"))
                    local = mathutils.Vector(tuple(float(v) for v in item.get("offset").split()))
                    points.append(matrix.translation.copy())
                    shifts.append(matrix.to_3x3() @ components[item.get("object_id")].to_3x3() @ local)
                self.assertEqual(len(points), 2)
                pivot = (points[0] + points[1]) / 2
                for point, shift in zip(points, shifts):
                    self.assertLess((shift - (point - pivot)).length, 0.002)
                archive.close()


if __name__ == "__main__":
    unittest.main()
