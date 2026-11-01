"""
Integration tests for the Create Assembly operator and ``3mf_assembly``
property assemblies.

Covers:
- ``threemf.create_assembly`` parents selected meshes to a new Empty and
  keeps every world transform
- the operator clears a conflicting ``3mf_assembly`` property
- Orca export writes Empty-parented and property-defined assemblies as one
  object with several parts
"""

import re
import unittest
import zipfile

import bpy
from test_base import Blender3mfTestCase


def _add_colored_cube(name, location, color):
    bpy.ops.mesh.primitive_cube_add(location=location)
    cube = bpy.context.object
    cube.name = name
    mat = bpy.data.materials.new(f"{name}_mat")
    mat.use_nodes = True
    principled = mat.node_tree.nodes.get("Principled BSDF")
    if principled:
        principled.inputs["Base Color"].default_value = (*color, 1.0)
    cube.data.materials.append(mat)
    return cube


def _select_only(objects):
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


class CreateAssemblyOperatorTests(Blender3mfTestCase):
    def test_parents_selected_meshes_to_new_empty(self):
        cubes = [
            _add_colored_cube(f"c{i}", (i * 3, 1, 0), (1.0, 0.0, 0.0)) for i in range(3)
        ]
        _select_only(cubes)
        self.assertIn("FINISHED", bpy.ops.threemf.create_assembly())

        empties = [o for o in bpy.data.objects if o.type == "EMPTY"]
        self.assertEqual(len(empties), 1)
        for cube in cubes:
            self.assertIs(cube.parent, empties[0])

    def test_world_transforms_unchanged(self):
        cubes = [
            _add_colored_cube("a", (0, 0, 0), (1.0, 0.0, 0.0)),
            _add_colored_cube("b", (5, 2, 1), (0.0, 1.0, 0.0)),
        ]
        cubes[1].rotation_euler = (0.3, 0.2, 0.9)
        cubes[1].scale = (1.0, 2.0, 0.5)
        bpy.context.view_layer.update()
        before = [c.matrix_world.copy() for c in cubes]

        _select_only(cubes)
        bpy.ops.threemf.create_assembly()
        bpy.context.view_layer.update()

        for cube, original in zip(cubes, before):
            for row in range(4):
                for col in range(4):
                    self.assertAlmostEqual(cube.matrix_world[row][col], original[row][col], places=4)

    def test_previously_parented_object_keeps_world_transform(self):
        old_parent = bpy.data.objects.new("OldParent", None)
        bpy.context.collection.objects.link(old_parent)
        old_parent.location = (10, 0, 0)
        cube = _add_colored_cube("kid", (0, 0, 0), (1.0, 0.0, 0.0))
        cube.parent = old_parent
        bpy.context.view_layer.update()
        before = cube.matrix_world.copy()

        _select_only([cube])
        bpy.ops.threemf.create_assembly()
        bpy.context.view_layer.update()

        self.assertNotEqual(cube.parent, old_parent)
        for row in range(4):
            for col in range(4):
                self.assertAlmostEqual(cube.matrix_world[row][col], before[row][col], places=4)

    def test_clears_assembly_property(self):
        cube = _add_colored_cube("p", (0, 0, 0), (1.0, 0.0, 0.0))
        cube["3mf_assembly"] = "Other"
        _select_only([cube])
        bpy.ops.threemf.create_assembly()
        self.assertNotIn("3mf_assembly", cube)

    def test_poll_requires_selected_mesh(self):
        bpy.ops.object.select_all(action="DESELECT")
        self.assertFalse(bpy.ops.threemf.create_assembly.poll())


class ClearAssemblyOperatorTests(Blender3mfTestCase):
    def test_selected_member_keeps_world_transform_and_inherits_plate(self):
        meshes = [
            _add_colored_cube("a", (1, 2, 3), (1.0, 0.0, 0.0)),
            _add_colored_cube("b", (4, 5, 6), (0.0, 1.0, 0.0)),
        ]
        _select_only(meshes)
        bpy.ops.threemf.create_assembly()
        empty = bpy.context.object
        empty.rotation_euler = (0.2, 0.1, 0.5)
        empty["3mf_plate"] = 3
        empty["3mf_plate_name"] = "Detail"
        delta = [1.0, 0.0, 0.0, 0.2, 0.0, 1.0, 0.0, 0.1,
                 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        empty["3mf_assemble_transform"] = delta
        bpy.context.view_layer.update()
        before = meshes[0].matrix_world.copy()

        _select_only([meshes[0]])
        self.assertIn("FINISHED", bpy.ops.threemf.clear_assembly())
        bpy.context.view_layer.update()
        self.assertIsNone(meshes[0].parent)
        self.assertIs(meshes[1].parent, empty)
        self.assertIn(empty.name, bpy.data.objects)
        self.assertEqual(meshes[0]["3mf_plate"], 3)
        self.assertEqual(meshes[0]["3mf_plate_name"], "Detail")
        self.assertEqual(list(meshes[0]["3mf_assemble_transform"]), delta)
        for row in range(4):
            for col in range(4):
                self.assertAlmostEqual(meshes[0].matrix_world[row][col], before[row][col], places=5)

    def test_selected_empty_leaves_unrelated_and_nested_objects_alone(self):
        empty = bpy.data.objects.new("UserEmpty", None)
        nested = bpy.data.objects.new("Nested", None)
        camera_data = bpy.data.cameras.new("Camera")
        camera = bpy.data.objects.new("Camera", camera_data)
        for obj in (empty, nested, camera):
            bpy.context.collection.objects.link(obj)
        nested.parent = empty
        camera.parent = empty
        direct = _add_colored_cube("direct", (1, 0, 0), (1.0, 0.0, 0.0))
        child = _add_colored_cube("nested", (2, 0, 0), (0.0, 1.0, 0.0))
        direct.parent = empty
        child.parent = nested
        _select_only([empty])
        self.assertIn("FINISHED", bpy.ops.threemf.clear_assembly())
        self.assertIsNone(direct.parent)
        self.assertIs(child.parent, nested)
        self.assertIs(nested.parent, empty)
        self.assertIs(camera.parent, empty)
        self.assertIn(empty.name, bpy.data.objects)

    def test_clears_only_selected_property_group_members(self):
        a = _add_colored_cube("a", (0, 0, 0), (1.0, 0.0, 0.0))
        b = _add_colored_cube("b", (1, 0, 0), (0.0, 1.0, 0.0))
        a["3mf_assembly"] = b["3mf_assembly"] = "Kit"
        _select_only([a])
        self.assertIn("FINISHED", bpy.ops.threemf.clear_assembly())
        self.assertNotIn("3mf_assembly", a)
        self.assertEqual(b["3mf_assembly"], "Kit")

    def test_nested_mesh_assignment_and_clear_target_nearest_empty(self):
        empty = bpy.data.objects.new("Assembly", None)
        bpy.context.collection.objects.link(empty)
        parent = _add_colored_cube("parent", (0, 0, 0), (1.0, 0.0, 0.0))
        child = _add_colored_cube("child", (1, 0, 0), (0.0, 1.0, 0.0))
        parent.parent = empty
        child.parent = parent
        child["3mf_plate"] = 2
        _select_only([child])
        self.assertIn("FINISHED", bpy.ops.threemf.assign_plate(plate=4))
        self.assertEqual(empty["3mf_plate"], 4)
        self.assertNotIn("3mf_plate", child)
        child["3mf_plate"] = 2
        self.assertIn("FINISHED", bpy.ops.threemf.clear_plate())
        self.assertNotIn("3mf_plate", empty)
        self.assertNotIn("3mf_plate", child)


class AssemblyExportTests(Blender3mfTestCase):
    def _export_settings(self):
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(str(self.temp_file)) as archive:
            return archive.read("Metadata/model_settings.config").decode("utf-8")

    def _objects(self, settings):
        """Return a list of part counts, one per <object> in model_settings."""
        return [
            len(re.findall(r"<part ", block))
            for block in re.findall(r"<object .*?</object>", settings, re.S)
        ]

    def test_button_assembly_exports_one_object_with_parts(self):
        cubes = [
            _add_colored_cube("a", (0, 0, 0), (1.0, 0.0, 0.0)),
            _add_colored_cube("b", (3, 0, 0), (0.0, 1.0, 0.0)),
            _add_colored_cube("c", (6, 0, 0), (0.0, 0.0, 1.0)),
        ]
        _select_only(cubes)
        bpy.ops.threemf.create_assembly()
        bpy.ops.object.select_all(action="SELECT")
        self.assertEqual(self._objects(self._export_settings()), [3])

    def test_property_assembly_exports_one_object_with_parts(self):
        a = _add_colored_cube("a", (0, 0, 0), (1.0, 0.0, 0.0))
        b = _add_colored_cube("b", (3, 0, 0), (0.0, 1.0, 0.0))
        _add_colored_cube("c", (6, 0, 0), (0.0, 0.0, 1.0))
        a["3mf_assembly"] = "Kit"
        b["3mf_assembly"] = "Kit"
        bpy.ops.object.select_all(action="SELECT")
        counts = sorted(self._objects(self._export_settings()))
        self.assertEqual(counts, [1, 2])

    def test_empty_name_cannot_collide_with_property_group_key(self):
        empty = bpy.data.objects.new("prop:Kit", None)
        bpy.context.collection.objects.link(empty)
        for index in range(2):
            cube = _add_colored_cube(f"empty{index}", (index * 0.01, 0, 0), (1.0, 0.0, 0.0))
            cube.parent = empty
        for index in range(2):
            cube = _add_colored_cube(f"property{index}", (index * 0.01, 0.02, 0), (0.0, 0.0, 1.0))
            cube["3mf_assembly"] = "Kit"
        bpy.ops.object.select_all(action="SELECT")
        self.assertEqual(sorted(self._objects(self._export_settings())), [2, 2])

    def test_operator_auto_exports_material_free_property_assembly(self):
        for index in range(2):
            bpy.ops.mesh.primitive_cube_add(size=0.01, location=(index * 0.02, 0, 0))
            bpy.context.object["3mf_assembly"] = "Kit"
        bpy.ops.object.select_all(action="SELECT")
        self.assertEqual(self._objects(self._export_settings()), [2])

    def test_api_auto_exports_material_free_property_assembly(self):
        from io_mesh_3mf.api import export_3mf

        for index in range(2):
            bpy.ops.mesh.primitive_cube_add(size=0.01, location=(index * 0.02, 0, 0))
            bpy.context.object["3mf_assembly"] = "Kit"
        result = export_3mf(str(self.temp_file), thumbnail_mode="NONE")
        self.assertEqual(result.status, "FINISHED")
        with zipfile.ZipFile(str(self.temp_file)) as archive:
            settings = archive.read("Metadata/model_settings.config").decode("utf-8")
        self.assertEqual(self._objects(settings), [2])

    def test_api_warns_when_standard_export_ignores_property_assembly(self):
        from io_mesh_3mf.api import export_3mf

        bpy.ops.mesh.primitive_cube_add(size=0.01)
        bpy.context.object["3mf_assembly"] = "Kit"
        result = export_3mf(
            str(self.temp_file), use_orca_format="STANDARD", thumbnail_mode="NONE"
        )
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("3mf_assembly" in message and "ignored" in message
                            for message in result.warnings))

    def test_api_warns_when_prusa_export_ignores_property_assembly(self):
        from io_mesh_3mf.api import export_3mf

        bpy.ops.mesh.primitive_cube_add(size=0.01)
        bpy.context.object["3mf_assembly"] = "Kit"
        warnings = []
        result = export_3mf(
            str(self.temp_file), use_orca_format="PAINT", mmu_slicer_format="PRUSA",
            thumbnail_mode="NONE", on_warning=warnings.append,
        )
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("3mf_assembly" in message and "ignored" in message
                            for message in result.warnings))
        self.assertTrue(any("3mf_assembly" in message for message in warnings))

    def test_api_warns_for_invalid_property_assembly(self):
        from io_mesh_3mf.api import export_3mf

        bpy.ops.mesh.primitive_cube_add(size=0.01)
        bpy.context.object["3mf_assembly"] = 42
        result = export_3mf(str(self.temp_file), thumbnail_mode="NONE")
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("Invalid 3mf_assembly" in message for message in result.warnings))

    def test_selected_nested_empty_exports_its_meshes_as_an_assembly(self):
        root = bpy.data.objects.new("Root", None)
        nested = bpy.data.objects.new("Nested", None)
        bpy.context.collection.objects.link(root)
        bpy.context.collection.objects.link(nested)
        nested.parent = root
        for i in range(2):
            cube = _add_colored_cube(f"c{i}", (i * 3, 0, 0), (1.0, 0.0, 0.0))
            cube.parent = nested
        _select_only([root])
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO", use_selection=True
        )
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(str(self.temp_file)) as archive:
            settings = archive.read("Metadata/model_settings.config").decode("utf-8")
        self.assertEqual(self._objects(settings), [2])

    def test_selecting_only_children_exports_standalone_parts(self):
        root = bpy.data.objects.new("Root", None)
        bpy.context.collection.objects.link(root)
        cubes = []
        for i in range(2):
            cube = _add_colored_cube(f"c{i}", (i * 3, 0, 0), (1.0, 0.0, 0.0))
            cube.parent = root
            cubes.append(cube)
        root["3mf_plate"] = 2
        _select_only(cubes)
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO", use_selection=True
        )
        self.assertIn("FINISHED", result)
        with zipfile.ZipFile(str(self.temp_file)) as archive:
            settings = archive.read("Metadata/model_settings.config").decode("utf-8")
        self.assertEqual(self._objects(settings), [1, 1])
        self.assertNotIn('key="plater_id" value="2"', settings)

    def test_no_assembly_means_separate_objects(self):
        for i in range(3):
            _add_colored_cube(f"c{i}", (i * 3, 0, 0), (float(i % 2), 0.5, 0.0))
        bpy.ops.object.select_all(action="SELECT")
        self.assertEqual(self._objects(self._export_settings()), [1, 1, 1])


if __name__ == "__main__":
    unittest.main()
