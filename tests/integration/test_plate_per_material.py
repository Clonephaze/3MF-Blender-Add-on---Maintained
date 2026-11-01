"""
Integration tests for Plate per Material export and the sidebar plate tools.

Covers:
- default export is unaffected by the option
- one plate per material, named after the material
- plates are global across assemblies
- assemblies with several materials are split, keeping assembled positions
- split off keeps a mixed assembly whole
- explicit ``3mf_plate`` wins over the material plate
- ``threemf.assign_plate`` / ``threemf.clear_plate``
"""

import unittest
import xml.etree.ElementTree as ET
import zipfile

import bpy
from test_base import Blender3mfTestCase

NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
RED, GREEN, BLUE = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)


def _cube(name, location, color, material_name):
    bpy.ops.mesh.primitive_cube_add(location=location)
    cube = bpy.context.object
    cube.name = name
    mat = bpy.data.materials.new(material_name)
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = (*color, 1.0)
    cube.data.materials.append(mat)
    return cube


def _assemble(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.threemf.create_assembly()
    return next(o for o in bpy.data.objects if o.type == "EMPTY" and objects[0].parent is o)


class PlatePerMaterialTests(Blender3mfTestCase):
    def _export(self, **kwargs):
        bpy.ops.object.select_all(action="SELECT")
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO", **kwargs
        )
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(str(self.temp_file)) as archive:
            settings = archive.read("Metadata/model_settings.config").decode("utf-8")
            main = archive.read("3D/3dmodel.model").decode("utf-8")
        return ET.fromstring(settings.encode("utf-8")), main

    @staticmethod
    def _plates(root):
        """{plater_id: (name, [object_id, ...])}"""
        result = {}
        for plate in root.findall("plate"):
            meta = {m.get("key"): m.get("value") for m in plate.findall("metadata")}
            ids = [
                int({m.get("key"): m.get("value") for m in inst.findall("metadata")}["object_id"])
                for inst in plate.findall("model_instance")
            ]
            result[int(meta["plater_id"])] = (meta["plater_name"], ids)
        return result

    @staticmethod
    def _parts(root, object_id):
        for obj in root.findall("object"):
            if int(obj.get("id")) == object_id:
                return len(obj.findall("part"))
        return None

    def test_option_off_keeps_single_plate(self):
        _cube("a", (0, 0, 0), RED, "Red")
        _cube("b", (3, 0, 0), BLUE, "Blue")
        root, _ = self._export()
        self.assertEqual(list(self._plates(root)), [1])

    def test_one_named_plate_per_material(self):
        _cube("a", (0, 0, 0), RED, "Red")
        _cube("b", (3, 0, 0), BLUE, "Blue")
        _cube("c", (6, 0, 0), RED, "Red")
        root, _ = self._export(plate_per_material=True)
        plates = self._plates(root)
        self.assertEqual(sorted(plates), [1, 2])
        by_name = {name: ids for name, ids in plates.values()}
        self.assertEqual(set(by_name), {"Red", "Blue"})
        self.assertEqual(len(by_name["Red"]), 2)
        self.assertEqual(len(by_name["Blue"]), 1)

    def test_assembly_is_split_by_material(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        b = _cube("b", (3, 0, 0), BLUE, "Blue")
        c = _cube("c", (6, 0, 0), RED, "Red")
        _assemble([a, b, c])
        root, _ = self._export(plate_per_material=True)
        plates = self._plates(root)
        self.assertEqual(sorted(plates), [1, 2])
        parts = sorted(
            self._parts(root, oid) for _, ids in plates.values() for oid in ids
        )
        self.assertEqual(parts, [1, 2])  # blue alone, red pair

    def test_plates_are_global_across_assemblies(self):
        first = [_cube("a1", (0, 0, 0), RED, "Red"), _cube("b1", (3, 0, 0), BLUE, "Blue")]
        second = [_cube("a2", (0, 9, 0), RED, "Red"), _cube("b2", (3, 9, 0), BLUE, "Blue")]
        _assemble(first)
        _assemble(second)
        root, _ = self._export(plate_per_material=True)
        plates = self._plates(root)
        self.assertEqual(sorted(plates), [1, 2])
        self.assertEqual([len(ids) for _, ids in plates.values()], [2, 2])

    def test_split_off_keeps_mixed_assembly_whole(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        b = _cube("b", (3, 0, 0), BLUE, "Blue")
        _assemble([a, b])
        root, _ = self._export(plate_per_material=True, split_assemblies_by_material=False)
        plates = self._plates(root)
        self.assertEqual(sorted(plates), [1])
        self.assertEqual(self._parts(root, plates[1][1][0]), 2)

    def test_split_off_keeps_same_named_mixed_assemblies_on_separate_plates(self):
        first = [_cube("a", (0, 0, 0), RED, "Red"), _cube("b", (3, 0, 0), BLUE, "Blue")]
        empty = _assemble(first)
        empty.name = "Kit"
        second = [_cube("c", (0, 9, 0), RED, "Red"), _cube("d", (3, 9, 0), BLUE, "Blue")]
        for obj in second:
            obj["3mf_assembly"] = "Kit"
        root, _ = self._export(plate_per_material=True, split_assemblies_by_material=False)
        plates = self._plates(root)
        self.assertEqual(sorted(plates), [1, 2])
        self.assertEqual([len(ids) for _, ids in plates.values()], [1, 1])
        self.assertEqual(
            [self._parts(root, ids[0]) for _, ids in plates.values()], [2, 2]
        )

    def test_explicit_plate_wins_over_material(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        _cube("b", (3, 0, 0), BLUE, "Blue")
        a["3mf_plate"] = 4
        root, _ = self._export(plate_per_material=True)
        plates = self._plates(root)
        self.assertEqual(sorted(plates), [1, 2, 3, 4])
        self.assertEqual(len(plates[4][1]), 1)

    def test_material_group_can_join_different_materials(self):
        red = _cube("red", (0, 0, 0), RED, "Red")
        blue = _cube("blue", (3, 0, 0), BLUE, "Blue")
        red["3mf_material_group"] = "Shared"
        blue["3mf_material_group"] = "Shared"
        root, _ = self._export(plate_per_material=True)
        plates = self._plates(root)
        self.assertEqual(len(plates), 1)
        self.assertEqual(plates[1][0], "Shared")
        self.assertEqual(len(plates[1][1]), 2)

    def test_material_group_can_separate_matching_materials(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        b = _cube("b", (3, 0, 0), RED, "Red")
        a["3mf_material_group"] = "First"
        b["3mf_material_group"] = "Second"
        root, _ = self._export(plate_per_material=True)
        self.assertEqual(
            {name for name, _ in self._plates(root).values()}, {"First", "Second"}
        )

    def test_assembled_positions_survive_split(self):
        """Single-volume sub-assemblies retain their relative positions."""
        a = _cube("a", (0, 0, 0), RED, "Red")
        b = _cube("b", (0.03, 0.01, 0.02), BLUE, "Blue")
        a.scale = b.scale = (0.001, 0.001, 0.001)
        bpy.context.view_layer.update()
        _assemble([a, b])
        root, _ = self._export(plate_per_material=True)
        assemble = root.find("assemble")
        self.assertIsNotNone(assemble)
        items = {
            int(item.get("object_id")): item
            for item in assemble.findall("assemble_item")
        }
        self.assertEqual(len(items), 2)
        plates = {name: ids[0] for name, ids in self._plates(root).values()}
        positions = {}
        offsets = {}
        for name, object_id in plates.items():
            item = items[object_id]
            positions[name] = [float(v) for v in item.get("transform").split()][9:12]
            offsets[name] = [float(v) for v in item.get("offset").split()]
        for axis, expected in enumerate((30.0, 10.0, 20.0)):
            self.assertAlmostEqual(
                positions["Blue"][axis] - positions["Red"][axis], expected, places=3
            )
            pivot = (positions["Blue"][axis] + positions["Red"][axis]) / 2
            for name in positions:
                self.assertAlmostEqual(
                    offsets[name][axis] * 0.001, positions[name][axis] - pivot, places=3
                )

    def test_each_split_subassembly_is_floored_independently(self):
        first = [
            _cube("a1", (0, 0, 0.005), RED, "Red"),
            _cube("b1", (0.01, 0, 0.015), BLUE, "Blue"),
        ]
        second = [
            _cube("a2", (0.04, 0, 0.045), RED, "Red"),
            _cube("b2", (0.05, 0, 0.055), BLUE, "Blue"),
        ]
        for obj in first + second:
            obj.scale = (0.001, 0.001, 0.001)
        bpy.context.view_layer.update()
        _assemble(first)
        _assemble(second)
        _, main = self._export(plate_per_material=True)
        model = ET.fromstring(main)
        resources = {
            obj.get("id"): obj
            for obj in model.find("m:resources", NS).findall("m:object", NS)
        }
        items = model.find("m:build", NS).findall("m:item", NS)
        self.assertEqual(len(items), 4)
        for item in items:
            wrapper = resources[item.get("objectid")]
            component, = wrapper.find("m:components", NS).findall("m:component", NS)
            build_z = float(item.get("transform").split()[11])
            component_z = float(component.get("transform").split()[11])
            self.assertAlmostEqual(build_z + component_z - 1.0, 0.0, places=3)

    def test_plate_properties_route_material_free_objects_to_orca(self):
        bpy.ops.mesh.primitive_cube_add()
        first = bpy.context.object
        first["3mf_plate"] = 1
        bpy.ops.mesh.primitive_cube_add(location=(0.01, 0, 0))
        second = bpy.context.object
        second["3mf_plate"] = 2
        root, _ = self._export()
        self.assertEqual(sorted(self._plates(root)), [1, 2])

    def test_api_routes_material_free_plate_objects_to_orca(self):
        from io_mesh_3mf.api import export_3mf

        bpy.ops.mesh.primitive_cube_add()
        first = bpy.context.object
        first["3mf_plate"] = 1
        bpy.ops.mesh.primitive_cube_add(location=(0.01, 0, 0))
        second = bpy.context.object
        second["3mf_plate"] = 2
        result = export_3mf(str(self.temp_file), use_orca_format="AUTO")
        self.assertEqual(result.status, "FINISHED")
        with zipfile.ZipFile(str(self.temp_file)) as archive:
            root = ET.fromstring(archive.read("Metadata/model_settings.config"))
        self.assertEqual(sorted(self._plates(root)), [1, 2])

    def test_api_warns_when_standard_export_ignores_plate_settings(self):
        from io_mesh_3mf.api import export_3mf

        bpy.ops.mesh.primitive_cube_add()
        bpy.context.object["3mf_plate"] = 2
        result = export_3mf(str(self.temp_file), use_orca_format="STANDARD")
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("ignored" in warning for warning in result.warnings))


class PlateToolTests(Blender3mfTestCase):
    def test_assign_plate_sets_property(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        bpy.context.view_layer.objects.active = a
        self.assertIn("FINISHED", bpy.ops.threemf.assign_plate(plate=3))
        self.assertEqual(a["3mf_plate"], 3)

    def test_each_on_own_plate_counts_up(self):
        cubes = [_cube(f"c{i}", (i * 3, 0, 0), RED, "Red") for i in range(3)]
        bpy.ops.object.select_all(action="DESELECT")
        for c in cubes:
            c.select_set(True)
        bpy.context.view_layer.objects.active = cubes[0]
        bpy.ops.threemf.assign_plate(plate=2, each_on_own_plate=True)
        self.assertEqual(sorted(c["3mf_plate"] for c in cubes), [2, 3, 4])

    def test_assembly_member_assigns_to_the_empty(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        b = _cube("b", (3, 0, 0), BLUE, "Blue")
        empty = _assemble([a, b])
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        bpy.context.view_layer.objects.active = a
        bpy.ops.threemf.assign_plate(plate=5)
        self.assertEqual(empty["3mf_plate"], 5)
        self.assertNotIn("3mf_plate", a)

    def test_clear_plate_removes_property(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        a["3mf_plate"] = 2
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        bpy.context.view_layer.objects.active = a
        self.assertIn("FINISHED", bpy.ops.threemf.clear_plate())
        self.assertNotIn("3mf_plate", a)

    def test_clear_plate_from_selected_assembly_member(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        b = _cube("b", (3, 0, 0), BLUE, "Blue")
        empty = _assemble([a, b])
        empty["3mf_plate"] = 2
        empty["3mf_plate_name"] = "Old name"
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        bpy.context.view_layer.objects.active = a
        self.assertTrue(bpy.ops.threemf.clear_plate.poll())
        self.assertIn("FINISHED", bpy.ops.threemf.clear_plate())
        self.assertNotIn("3mf_plate", empty)
        self.assertNotIn("3mf_plate_name", empty)

    def test_reassigning_plate_removes_stale_name(self):
        a = _cube("a", (0, 0, 0), RED, "Red")
        a["3mf_plate"] = 2
        a["3mf_plate_name"] = "Old name"
        bpy.ops.object.select_all(action="DESELECT")
        a.select_set(True)
        bpy.context.view_layer.objects.active = a
        self.assertIn("FINISHED", bpy.ops.threemf.assign_plate(plate=3))
        self.assertEqual(a["3mf_plate"], 3)
        self.assertNotIn("3mf_plate_name", a)

    def test_assign_poll_needs_selection(self):
        bpy.ops.object.select_all(action="DESELECT")
        self.assertFalse(bpy.ops.threemf.assign_plate.poll())


if __name__ == "__main__":
    unittest.main()
