"""
Integration tests for multi-plate Orca export (``3mf_plate`` property).

Covers:
- default export still writes exactly one plate
- ``3mf_plate`` creates one ``<plate>`` per plate (gaps give empty plates)
- build items are positioned inside their plate's grid cell, since Orca
  assigns objects to plates by position
- assemblies move as one unit and keep their assembled position
"""

import json
import math
import re
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import bpy
import mathutils
from test_base import Blender3mfTestCase

import io_mesh_3mf
from io_mesh_3mf.api import export_3mf
from io_mesh_3mf.common.plates import plate_origin
from io_mesh_3mf.common.xml import parse_transformation

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"


def _add_cube(name, location, color, plate=None):
    bpy.ops.mesh.primitive_cube_add(location=location)
    cube = bpy.context.object
    cube.name = name
    mat = bpy.data.materials.new(f"{name}_mat")
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = (*color, 1.0)
    cube.data.materials.append(mat)
    if plate is not None:
        cube["3mf_plate"] = plate
    return cube


class OrcaPlateTests(Blender3mfTestCase):
    def _export(self):
        bpy.ops.object.select_all(action="SELECT")
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(str(self.temp_file)) as archive:
            settings = archive.read("Metadata/model_settings.config").decode("utf-8")
            main = archive.read("3D/3dmodel.model").decode("utf-8")
            project = json.loads(archive.read("Metadata/project_settings.config"))
        return settings, main, project

    @staticmethod
    def _plates(settings):
        """Return {plater_id: [object_id, ...]} from model_settings."""
        root = ET.fromstring(settings.encode("utf-8"))
        result = {}
        for plate in root.findall("plate"):
            pid = int(next(m.get("value") for m in plate.findall("metadata") if m.get("key") == "plater_id"))
            result[pid] = [
                int(next(m.get("value") for m in inst.findall("metadata") if m.get("key") == "object_id"))
                for inst in plate.findall("model_instance")
            ]
        return result

    @staticmethod
    def _build_translations(main):
        root = ET.fromstring(main.encode("utf-8"))
        items = root.find("m:build", NS).findall("m:item", NS)
        return {
            int(item.get("objectid")): [float(v) for v in item.get("transform").split()][9:12]
            for item in items
        }

    @staticmethod
    def _bed_size(project):
        pts = [tuple(float(v) for v in p.split("x")) for p in project["printable_area"]]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return max(xs) - min(xs), max(ys) - min(ys)

    def test_default_is_single_plate(self):
        for i in range(3):
            _add_cube(f"c{i}", (i * 3, 0, 0), (float(i % 2), 0.5, 0.0))
        settings, _, _ = self._export()
        plates = self._plates(settings)
        self.assertEqual(list(plates), [1])
        self.assertEqual(len(plates[1]), 3)

    def test_property_creates_one_plate_each(self):
        _add_cube("a", (0, 0, 0), (1.0, 0.0, 0.0), plate=1)
        _add_cube("b", (3, 0, 0), (0.0, 1.0, 0.0), plate=2)
        _add_cube("c", (6, 0, 0), (0.0, 0.0, 1.0), plate=2)
        settings, _, _ = self._export()
        plates = self._plates(settings)
        self.assertEqual(sorted(plates), [1, 2])
        self.assertEqual(len(plates[1]), 1)
        self.assertEqual(len(plates[2]), 2)

    def test_gap_creates_empty_plate(self):
        _add_cube("a", (0, 0, 0), (1.0, 0.0, 0.0), plate=1)
        _add_cube("b", (3, 0, 0), (0.0, 1.0, 0.0), plate=3)
        settings, _, _ = self._export()
        plates = self._plates(settings)
        self.assertEqual(sorted(plates), [1, 2, 3])
        self.assertEqual(plates[2], [])

    def test_build_items_sit_in_their_plate_cell(self):
        _add_cube("a", (0, 0, 0), (1.0, 0.0, 0.0), plate=1)
        _add_cube("b", (0, 0, 0), (0.0, 1.0, 0.0), plate=2)
        _add_cube("c", (0, 0, 0), (0.0, 0.0, 1.0), plate=4)
        settings, main, project = self._export()
        plates = self._plates(settings)
        translations = self._build_translations(main)
        width, depth = self._bed_size(project)
        stride = width * 1.2

        first = translations[plates[1][0]]
        second = translations[plates[2][0]]
        fourth = translations[plates[4][0]]
        # 4 plates -> 2 columns: plate 2 is one column right, plate 4 one row down
        self.assertAlmostEqual(second[0] - first[0], stride, places=3)
        self.assertAlmostEqual(second[1] - first[1], 0.0, places=3)
        self.assertAlmostEqual(fourth[0] - first[0], stride, places=3)
        self.assertAlmostEqual(fourth[1] - first[1], -depth * 1.2, places=3)

    def test_assembly_moves_as_one_unit(self):
        a = _add_cube("a", (0, 0, 0), (1.0, 0.0, 0.0))
        b = _add_cube("b", (3, 0, 0), (0.0, 1.0, 0.0))
        _add_cube("loose", (9, 0, 0), (0.0, 0.0, 1.0), plate=1)
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        b.select_set(True)
        bpy.context.view_layer.objects.active = a
        bpy.ops.threemf.create_assembly()
        empty = next(o for o in bpy.data.objects if o.type == "EMPTY")
        empty["3mf_plate"] = 2
        settings, _, _ = self._export()
        plates = self._plates(settings)
        self.assertEqual(sorted(plates), [1, 2])
        self.assertEqual(len(plates[1]), 1)
        self.assertEqual(len(plates[2]), 1)
        # the single entry on plate 2 is the 2-part assembly
        blocks = re.findall(r"<object .*?</object>", settings, re.S)
        wrapper = plates[2][0]
        block = next(b for b in blocks if f'id="{wrapper}"' in b.split(">")[0])
        self.assertEqual(len(re.findall(r"<part ", block)), 2)

    def test_custom_bed_layout_contains_each_objects_geometry(self):
        builtin = Path(io_mesh_3mf.__file__).parent / "orca_project_template.json"
        template = json.loads(builtin.read_text(encoding="utf-8"))
        areas = {
            "corner": ["0x0", "400x0", "400x180", "0x180"],
            "centered": ["-110x-110", "110x-110", "110x110", "-110x110"],
            "circular": ["0x-100", "100x0", "0x100", "-100x0"],
        }
        for label, area in areas.items():
            with self.subTest(bed=label):
                self.clean_scene()
                objects = []
                for plate in (1, 2):
                    bpy.ops.mesh.primitive_cube_add(size=0.01)
                    obj = bpy.context.object
                    obj["3mf_plate"] = plate
                    objects.append(obj)
                template["printable_area"] = area
                template_path = self.temp_file.with_name(f"{label}.json")
                template_path.write_text(json.dumps(template), encoding="utf-8")
                result = export_3mf(
                    str(self.temp_file), objects=objects,
                    project_template=str(template_path), thumbnail_mode="NONE",
                )
                self.assertEqual(result.status, "FINISHED")
                with zipfile.ZipFile(self.temp_file) as archive:
                    settings = archive.read("Metadata/model_settings.config").decode("utf-8")
                    main = ET.fromstring(archive.read("3D/3dmodel.model"))
                    plates = self._plates(settings)
                    resources = {
                        obj.get("id"): obj for obj in main.findall("./m:resources/m:object", NS)
                    }
                    builds = {
                        item.get("objectid"): parse_transformation(item.get("transform"))
                        for item in main.findall("./m:build/m:item", NS)
                    }
                    points = [tuple(float(v) for v in point.split("x")) for point in area]
                    min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
                    min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
                    for number in (1, 2):
                        object_id = str(plates[number][0])
                        component = resources[object_id].find("./m:components/m:component", NS)
                        mesh_path = component.get(f"{{{PRODUCTION_NS}}}path").lstrip("/")
                        mesh = ET.fromstring(archive.read(mesh_path))
                        transform = builds[object_id] @ parse_transformation(
                            component.get("transform", "")
                        )
                        origin_x, origin_y = plate_origin(
                            number - 1, 2, max_x - min_x, max_y - min_y
                        )
                        for vertex in mesh.findall(".//m:vertex", NS):
                            point = transform @ mathutils.Vector(
                                tuple(float(vertex.get(axis)) for axis in "xyz")
                            )
                            x, y = point.x - origin_x, point.y - origin_y
                            self.assertGreaterEqual(x, min_x - 0.001)
                            self.assertLessEqual(x, max_x + 0.001)
                            self.assertGreaterEqual(y, min_y - 0.001)
                            self.assertLessEqual(y, max_y + 0.001)
                            if label == "circular":
                                self.assertLessEqual(math.hypot(x, y), 100.001)


if __name__ == "__main__":
    unittest.main()
