"""Round-trip Orca/Bambu plate membership and placement through Blender."""

import json
import unittest
import xml.etree.ElementTree as ET
import zipfile

import bpy
import mathutils
from test_base import Blender3mfTestCase

from io_mesh_3mf.api import export_3mf, import_3mf
from io_mesh_3mf.common.xml import parse_transformation


MODEL_NS = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
SETTINGS_PATH = "Metadata/model_settings.config"
PROJECT_PATH = "Metadata/project_settings.config"
MODEL_PATH = "3D/3dmodel.model"


def _metadata(element):
    return {item.get("key"): item.get("value") for item in element.findall("metadata")}


def _assembled_geometry(path):
    """Reconstruct assembled vertices using Orca's single-volume centering rule."""
    with zipfile.ZipFile(path) as archive:
        settings = ET.fromstring(archive.read(SETTINGS_PATH))
        main = ET.fromstring(archive.read(MODEL_PATH))
        assemble = {
            item.get("object_id"): parse_transformation(item.get("transform"))
            for item in settings.findall("./assemble/assemble_item")
        }
        parts = {
            obj.get("id"): {
                part.get("id"): _metadata(part)["name"]
                for part in obj.findall("part")
            }
            for obj in settings.findall("object")
        }
        geometry = {}
        for obj in main.findall("./m:resources/m:object", MODEL_NS):
            object_id = obj.get("id")
            components = obj.findall("./m:components/m:component", MODEL_NS)
            for component in components:
                mesh_id = component.get("objectid")
                name = parts[object_id][mesh_id]
                mesh_path = component.get(f"{{{PRODUCTION_NS}}}path").lstrip("/")
                mesh = ET.fromstring(archive.read(mesh_path))
                vertices = [
                    mathutils.Vector(tuple(float(vertex.get(axis)) for axis in "xyz"))
                    for vertex in mesh.findall(".//m:vertex", MODEL_NS)
                ]
                component_matrix = parse_transformation(component.get("transform", ""))
                if len(components) == 1:
                    center = mathutils.Vector(
                        (min(v[axis] for v in vertices) + max(v[axis] for v in vertices)) / 2
                        for axis in range(3)
                    )
                    geometry[name] = [
                        assemble[object_id] @ (component_matrix.to_3x3() @ (v - center))
                        for v in vertices
                    ]
                else:
                    geometry[name] = [
                        assemble[object_id] @ component_matrix @ vertex
                        for vertex in vertices
                    ]
        return geometry


class PlateRoundTripTests(Blender3mfTestCase):
    def _assert_assembled_geometry_equal(self, before, after):
        self.assertEqual(set(before), set(after))
        anchor = sorted(before)[0]
        for name in before:
            self.assertEqual(len(before[name]), len(after[name]))
            for old, new in zip(before[name], after[name]):
                for axis in range(3):
                    self.assertAlmostEqual(
                        old[axis] - before[anchor][0][axis],
                        new[axis] - after[anchor][0][axis],
                        delta=0.002,
                        msg=f"{name} axis {axis}",
                    )

    def _export_rotated_split(self, project_template=None):
        meshes = []
        for name, location, color, shift, rotation, scale in (
            ("RedA", (0, 0, 0), (1, 0, 0, 1), (0.004, -0.002, 0.003),
             (0.1, 0.2, 0.7), (1.2, 0.8, 1.1)),
            ("RedB", (0.03, 0.01, 0.02), (1, 0, 0, 1), (-0.003, 0.001, -0.002),
             (0.2, -0.3, 0.1), (0.7, 1.1, 1.3)),
            ("Blue", (-0.02, 0.005, 0.015), (0, 0, 1, 1), (0.005, 0.003, 0.001),
             (-0.2, 0.4, -0.6), (0.8, 1.4, 0.9)),
        ):
            bpy.ops.mesh.primitive_cube_add(size=0.01, location=location)
            obj = bpy.context.object
            obj.name = name
            for vertex in obj.data.vertices:
                vertex.co += mathutils.Vector(shift)
            obj.rotation_euler = rotation
            obj.scale = scale
            material = bpy.data.materials.new(name)
            material.use_nodes = True
            material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = color
            obj.data.materials.append(material)
            meshes.append(obj)
        bpy.context.view_layer.update()
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        self.assertIn("FINISHED", bpy.ops.threemf.create_assembly())
        result = export_3mf(
            str(self.temp_file), plate_per_material=True,
            project_template=str(project_template) if project_template else None,
        )
        self.assertEqual(result.status, "FINISHED")

    def test_rotated_off_origin_split_survives_scaled_round_trip(self):
        self._export_rotated_split()
        before = _assembled_geometry(self.temp_file)
        self.assertEqual(set(before), {"RedA", "RedB", "Blue"})
        self.clean_scene()
        imported = import_3mf(str(self.temp_file), import_materials="NONE", global_scale=2.0)
        self.assertEqual(imported.status, "FINISHED")
        output = self.temp_file.with_name("rotated_assembled_roundtrip.3mf")
        exported = export_3mf(str(output), objects=imported.objects, global_scale=0.5)
        self.assertEqual(exported.status, "FINISHED")
        self._assert_assembled_geometry_equal(before, _assembled_geometry(output))

    def test_rotated_split_survives_origin_modes(self):
        self._export_rotated_split()
        before = _assembled_geometry(self.temp_file)
        printed = None
        for origin in ("KEEP", "CENTER", "BOTTOM"):
            with self.subTest(origin=origin):
                self.clean_scene()
                imported = import_3mf(
                    str(self.temp_file), import_materials="NONE", origin_to_geometry=origin,
                )
                self.assertEqual(imported.status, "FINISHED")
                bpy.context.view_layer.update()
                current = {
                    obj.name: [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
                    for obj in bpy.context.scene.objects if obj.type == "MESH"
                }
                if printed is None:
                    printed = current
                else:
                    for name in printed:
                        for old, new in zip(printed[name], current[name]):
                            self.assertLess((old - new).length, 0.000002)
                output = self.temp_file.with_name(f"origin_{origin}.3mf")
                self.assertEqual(
                    export_3mf(str(output), objects=imported.objects).status, "FINISHED"
                )
                self._assert_assembled_geometry_equal(before, _assembled_geometry(output))

    def test_custom_bed_and_scene_units_survive_round_trip(self):
        template_path = self.temp_file.with_name("wide_bed.json")
        from pathlib import Path
        import io_mesh_3mf

        builtin = Path(io_mesh_3mf.__file__).parent / "orca_project_template.json"
        template = json.loads(builtin.read_text(encoding="utf-8"))
        template["printable_area"] = ["0x0", "400x0", "400x180", "0x180"]
        template_path.write_text(json.dumps(template), encoding="utf-8")
        bpy.context.scene.unit_settings.scale_length = 0.1
        self._export_rotated_split(template_path)
        before = _assembled_geometry(self.temp_file)
        self.clean_scene()
        imported = import_3mf(str(self.temp_file), import_materials="NONE", scene_unit="FILE")
        self.assertEqual(imported.status, "FINISHED")
        output = self.temp_file.with_name("wide_bed_roundtrip.3mf")
        self.assertEqual(export_3mf(str(output), objects=imported.objects).status, "FINISHED")
        with zipfile.ZipFile(output) as archive:
            project = json.loads(archive.read(PROJECT_PATH))
        self.assertEqual(project["printable_area"], template["printable_area"])
        self._assert_assembled_geometry_equal(before, _assembled_geometry(output))

    def _export_plates(self):
        for index, plate in enumerate((1, 2, 4)):
            bpy.ops.mesh.primitive_cube_add(size=0.01)
            obj = bpy.context.object
            obj.name = f"Part{index}"
            obj["3mf_plate"] = plate
            material = bpy.data.materials.new(f"Material{index}")
            material.use_nodes = True
            obj.data.materials.append(material)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )
        self.assertIn("FINISHED", result)

        def name_plates(files):
            root = ET.fromstring(files[SETTINGS_PATH])
            names = {1: "Base", 2: "Detail", 4: "Support"}
            for plate in root.findall("plate"):
                number = int(_metadata(plate)["plater_id"])
                for entry in plate.findall("metadata"):
                    if entry.get("key") == "plater_name":
                        entry.set("value", names.get(number, ""))
            files[SETTINGS_PATH] = ET.tostring(root, encoding="utf-8")

        self._rewrite_archive(name_plates)
        self.clean_scene()

    def _export_single_plate(self, name=""):
        bpy.ops.mesh.primitive_cube_add(size=0.01)
        obj = bpy.context.object
        material = bpy.data.materials.new("Single")
        material.use_nodes = True
        obj.data.materials.append(material)
        self.assertIn(
            "FINISHED",
            bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), use_orca_format="AUTO"),
        )

        def set_name(files):
            root = ET.fromstring(files[SETTINGS_PATH])
            plates = root.findall("plate")
            self.assertEqual(len(plates), 1)
            for entry in plates[0].findall("metadata"):
                if entry.get("key") == "plater_name":
                    entry.set("value", name)
            files[SETTINGS_PATH] = ET.tostring(root, encoding="utf-8")

        self._rewrite_archive(set_name)
        self.clean_scene()

    def test_single_unnamed_plate_is_not_tagged(self):
        self._export_single_plate()
        imported = import_3mf(str(self.temp_file), import_materials="NONE")
        self.assertEqual(imported.status, "FINISHED")
        meshes = [obj for obj in imported.objects if obj.type == "MESH"]
        self.assertTrue(meshes)
        for obj in meshes:
            self.assertNotIn("3mf_plate", obj)
            self.assertNotIn("3mf_plate_name", obj)

        output = self.temp_file.with_name("single_plate_standard.3mf")
        exported = export_3mf(
            str(output), objects=imported.objects, use_orca_format="STANDARD"
        )
        self.assertEqual(exported.status, "FINISHED")
        self.assertFalse(any("Plate assignments" in w for w in exported.warnings))

    def test_single_named_plate_keeps_its_name(self):
        self._export_single_plate("Bed")
        imported = import_3mf(str(self.temp_file), import_materials="NONE")
        meshes = [obj for obj in imported.objects if obj.type == "MESH"]
        self.assertTrue(meshes)
        for obj in meshes:
            self.assertNotIn("3mf_plate", obj)
            self.assertEqual(obj["3mf_plate_name"], "Bed")

        standard = self.temp_file.with_name("single_named_standard.3mf")
        exported = export_3mf(
            str(standard), objects=imported.objects, use_orca_format="STANDARD"
        )
        self.assertEqual(exported.status, "FINISHED")
        self.assertFalse(any("Plate assignments" in w for w in exported.warnings))

        orca = self.temp_file.with_name("single_named_orca.3mf")
        material = bpy.data.materials.new("Orca trigger")
        for obj in meshes:
            obj.data.materials.append(material)
        exported = export_3mf(
            str(orca), objects=imported.objects, use_orca_format="AUTO"
        )
        self.assertEqual(exported.status, "FINISHED")
        with zipfile.ZipFile(orca) as archive:
            root = ET.fromstring(archive.read(SETTINGS_PATH))
        plates = root.findall("plate")
        self.assertEqual(len(plates), 1)
        names = {
            entry.get("value")
            for entry in plates[0].findall("metadata")
            if entry.get("key") == "plater_name"
        }
        self.assertEqual(names, {"Bed"})

    def _rewrite_archive(self, update):
        with zipfile.ZipFile(self.temp_file) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        update(files)
        with zipfile.ZipFile(self.temp_file, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)

    def _assert_local_plates(self, objects):
        by_plate = {obj.get("3mf_plate"): obj for obj in objects if obj.type == "MESH"}
        self.assertEqual(set(by_plate), {1, 2, 4})
        self.assertEqual(by_plate[1]["3mf_plate_name"], "Base")
        self.assertEqual(by_plate[2]["3mf_plate_name"], "Detail")
        self.assertEqual(by_plate[4]["3mf_plate_name"], "Support")
        bpy.context.view_layer.update()
        centers = {
            plate: obj.matrix_world @ mathutils.Vector((0, 0, 0))
            for plate, obj in by_plate.items()
        }
        for plate in (2, 4):
            for axis in (0, 1):
                self.assertAlmostEqual(centers[plate][axis], centers[1][axis], places=5)

    def test_operator_imports_plates_without_materials(self):
        self._export_plates()
        result = bpy.ops.import_mesh.threemf(
            filepath=str(self.temp_file), import_materials="NONE"
        )
        self.assertIn("FINISHED", result)
        self._assert_local_plates(bpy.context.scene.objects)

    def test_api_round_trip_preserves_plate_names_and_assignments(self):
        self._export_plates()
        result = import_3mf(
            str(self.temp_file), import_materials="NONE", global_scale=2.0
        )
        self.assertEqual(result.status, "FINISHED")
        self._assert_local_plates(result.objects)

        output = self.temp_file.with_name("plate_roundtrip.3mf")
        exported = export_3mf(str(output), objects=result.objects, global_scale=0.5)
        self.assertEqual(exported.status, "FINISHED")
        with zipfile.ZipFile(output) as archive:
            root = ET.fromstring(archive.read(SETTINGS_PATH))
        plates = {int(_metadata(plate)["plater_id"]): plate for plate in root.findall("plate")}
        self.assertEqual(sorted(plates), [1, 2, 3, 4])
        self.assertEqual(_metadata(plates[1])["plater_name"], "Base")
        self.assertEqual(_metadata(plates[2])["plater_name"], "Detail")
        self.assertEqual(_metadata(plates[4])["plater_name"], "Support")
        self.assertEqual(len(plates[3].findall("model_instance")), 0)

    def _reuse_first_object_on_second_plate(self, shift_mesh=False):
        def reuse_first_object(files):
            settings = ET.fromstring(files[SETTINGS_PATH])
            plates = {int(_metadata(plate)["plater_id"]): plate for plate in settings.findall("plate")}
            first = plates[1].find("model_instance")
            second = plates[2].find("model_instance")
            first_id = _metadata(first)["object_id"]
            second_id = _metadata(second)["object_id"]
            for entry in second.findall("metadata"):
                if entry.get("key") == "object_id":
                    entry.set("value", first_id)
                elif entry.get("key") == "instance_id":
                    entry.set("value", "1")
            model = ET.fromstring(files[MODEL_PATH])
            if shift_mesh:
                first_object = next(
                    obj for obj in model.findall("./m:resources/m:object", MODEL_NS)
                    if obj.get("id") == first_id
                )
                component = first_object.find("./m:components/m:component", MODEL_NS)
                mesh_path = component.get(f"{{{PRODUCTION_NS}}}path").lstrip("/")
                mesh = ET.fromstring(files[mesh_path])
                for vertex in mesh.findall(".//m:vertex", MODEL_NS):
                    for axis, offset in zip("xyz", (7.0, -3.0, 2.0)):
                        vertex.set(axis, str(float(vertex.get(axis)) + offset))
                files[mesh_path] = ET.tostring(mesh, encoding="utf-8")
            for item in model.find("m:build", MODEL_NS).findall("m:item", MODEL_NS):
                if item.get("objectid") == second_id:
                    item.set("objectid", first_id)
            files[SETTINGS_PATH] = ET.tostring(settings, encoding="utf-8")
            files[MODEL_PATH] = ET.tostring(model, encoding="utf-8")

        self._rewrite_archive(reuse_first_object)

    def test_repeated_build_instances_keep_separate_plate_assignments(self):
        self._export_plates()
        self._reuse_first_object_on_second_plate()
        result = import_3mf(str(self.temp_file), import_materials="NONE")
        self.assertEqual(result.status, "FINISHED")
        self._assert_local_plates(result.objects)
        by_plate = {obj["3mf_plate"]: obj for obj in result.objects if obj.type == "MESH"}
        self.assertIs(by_plate[1].data, by_plate[2].data)

    def test_repeated_instances_keep_shared_mesh_and_positions_with_origin_changes(self):
        self._export_plates()
        self._reuse_first_object_on_second_plate(shift_mesh=True)
        expected = None
        for origin in ("KEEP", "CENTER", "BOTTOM"):
            with self.subTest(origin=origin):
                self.clean_scene()
                result = import_3mf(
                    str(self.temp_file), import_materials="NONE", origin_to_geometry=origin,
                )
                self.assertEqual(result.status, "FINISHED")
                by_plate = {obj["3mf_plate"]: obj for obj in result.objects if obj.type == "MESH"}
                self.assertIs(by_plate[1].data, by_plate[2].data)
                bpy.context.view_layer.update()
                points = {
                    plate: [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
                    for plate, obj in by_plate.items() if plate in (1, 2)
                }
                if expected is None:
                    expected = points
                else:
                    for plate in expected:
                        for old, new in zip(expected[plate], points[plate]):
                            self.assertLess((old - new).length, 0.000002)

    def test_missing_bed_dimensions_warns_and_keeps_global_positions(self):
        self._export_plates()
        self._rewrite_archive(lambda files: files.pop(PROJECT_PATH))
        warnings = []
        result = import_3mf(
            str(self.temp_file), import_materials="NONE", on_warning=warnings.append
        )
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("bed" in message.lower() for message in warnings))
        by_plate = {obj["3mf_plate"]: obj for obj in result.objects if obj.type == "MESH"}
        self.assertGreater(abs(by_plate[2].location.x - by_plate[1].location.x), 0.1)

    def test_inch_model_plate_offsets_use_millimeter_bed_dimensions(self):
        self._export_plates()

        def convert_models_to_inches(files):
            settings = ET.fromstring(files[SETTINGS_PATH])
            settings.remove(settings.find("assemble"))
            files[SETTINGS_PATH] = ET.tostring(settings, encoding="utf-8")
            for name in tuple(files):
                if not name.endswith(".model"):
                    continue
                model = ET.fromstring(files[name])
                model.set("unit", "inch")
                for vertex in model.findall(".//m:vertex", MODEL_NS):
                    for axis in "xyz":
                        vertex.set(axis, f"{float(vertex.get(axis)) / 25.4:.12f}")
                for element in model.iter():
                    transform = element.get("transform")
                    if transform:
                        values = [float(value) for value in transform.split()]
                        values[9:12] = [value / 25.4 for value in values[9:12]]
                        element.set("transform", " ".join(f"{value:.12f}" for value in values))
                files[name] = ET.tostring(model, encoding="utf-8")

        self._rewrite_archive(convert_models_to_inches)
        result = import_3mf(str(self.temp_file), import_materials="NONE", global_scale=2.0)
        self.assertEqual(result.status, "FINISHED")
        self._assert_local_plates(result.objects)
        bpy.context.view_layer.update()
        for obj in result.objects:
            if obj.type == "MESH":
                for dimension in obj.dimensions:
                    self.assertAlmostEqual(dimension, 0.02, places=5)

    def test_import_result_includes_bed_warning_without_callback(self):
        self._export_plates()
        self._rewrite_archive(lambda files: files.pop(PROJECT_PATH))
        result = import_3mf(str(self.temp_file), import_materials="NONE")
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("bed" in message.lower() for message in result.warnings))

    def test_invalid_and_clamped_plate_properties_are_reported(self):
        bpy.ops.mesh.primitive_cube_add(size=0.01)
        obj = bpy.context.object
        for value, expected in (("bad", "invalid"), (99, "36")):
            with self.subTest(value=value):
                obj["3mf_plate"] = value
                result = export_3mf(str(self.temp_file), objects=[obj], thumbnail_mode="NONE")
                self.assertEqual(result.status, "FINISHED")
                self.assertTrue(any(expected in message.lower() for message in result.warnings))

    def test_conflicting_names_within_one_assembly_are_reported(self):
        meshes = []
        for name in ("First", "Second"):
            bpy.ops.mesh.primitive_cube_add(size=0.01)
            obj = bpy.context.object
            obj["3mf_assembly"] = "Kit"
            obj["3mf_plate"] = 2
            obj["3mf_plate_name"] = name
            meshes.append(obj)
        result = export_3mf(str(self.temp_file), objects=meshes, thumbnail_mode="NONE")
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("conflicting names" in message.lower() for message in result.warnings))

    def test_invalid_assemble_metadata_is_reported(self):
        self._export_plates()

        def invalidate_assemble(files):
            settings = ET.fromstring(files[SETTINGS_PATH])
            settings.find("./assemble/assemble_item").set("transform", "1 0 nan")
            files[SETTINGS_PATH] = ET.tostring(settings, encoding="utf-8")

        self._rewrite_archive(invalidate_assemble)
        imported = import_3mf(str(self.temp_file), import_materials="NONE")
        self.assertEqual(imported.status, "FINISHED")
        self.assertTrue(any("invalid assemble" in message.lower() for message in imported.warnings))
        mesh = next(obj for obj in imported.objects if obj.type == "MESH")
        mesh["3mf_assemble_transform"] = [1.0, 0.0, 0.0]
        exported = export_3mf(
            str(self.temp_file.with_name("invalid_delta.3mf")),
            objects=imported.objects, thumbnail_mode="NONE",
        )
        self.assertEqual(exported.status, "FINISHED")
        self.assertTrue(any("invalid 3mf_assemble_transform" in message.lower()
                            for message in exported.warnings))

    def test_conflicting_saved_assembly_frames_are_reported(self):
        objects = []
        for index in range(2):
            bpy.ops.mesh.primitive_cube_add(size=0.01, location=(index * 0.02, 0, 0))
            obj = bpy.context.object
            obj["3mf_assembly"] = "Kit"
            obj["3mf_plate"] = 1
            objects.append(obj)
        objects[0]["3mf_assemble_transform"] = [
            1.0, 0.0, 0.0, 0.1,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]
        result = export_3mf(str(self.temp_file), objects=objects, thumbnail_mode="NONE")
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("different saved assembled frames" in message.lower()
                            for message in result.warnings))

    def test_more_than_36_automatic_materials_warns(self):
        meshes = []
        for index in range(37):
            bpy.ops.mesh.primitive_cube_add(size=0.002)
            obj = bpy.context.object
            obj["3mf_material_group"] = f"Material {index}"
            meshes.append(obj)
        result = export_3mf(
            str(self.temp_file), objects=meshes,
            plate_per_material=True, thumbnail_mode="NONE",
        )
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(any("36" in message and "material" in message.lower()
                            for message in result.warnings))

    def test_assembly_plate_is_restored_without_importing_materials(self):
        meshes = []
        for x in (0.0, 0.02):
            bpy.ops.mesh.primitive_cube_add(size=0.01, location=(x, 0, 0))
            meshes.append(bpy.context.object)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        self.assertIn("FINISHED", bpy.ops.threemf.create_assembly())
        bpy.context.object["3mf_plate"] = 3
        self.assertIn(
            "FINISHED",
            bpy.ops.export_mesh.threemf(filepath=str(self.temp_file), use_orca_format="AUTO"),
        )
        self.clean_scene()

        self.assertIn(
            "FINISHED",
            bpy.ops.import_mesh.threemf(filepath=str(self.temp_file), import_materials="NONE"),
        )
        assemblies = [obj for obj in bpy.context.scene.objects if obj.type == "EMPTY"]
        self.assertEqual(len(assemblies), 1)
        self.assertEqual(assemblies[0]["3mf_plate"], 3)
        self.assertEqual(len([obj for obj in assemblies[0].children if obj.type == "MESH"]), 2)

    def test_round_trip_preserves_split_assembled_positions(self):
        meshes = []
        for name, location, color in (
            ("Red", (0, 0, 0), (1, 0, 0, 1)),
            ("Blue", (0.03, 0.01, 0.02), (0, 0, 1, 1)),
        ):
            bpy.ops.mesh.primitive_cube_add(size=0.01, location=location)
            obj = bpy.context.object
            material = bpy.data.materials.new(name)
            material.use_nodes = True
            material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = color
            obj.data.materials.append(material)
            meshes.append(obj)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in meshes:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        bpy.ops.threemf.create_assembly()
        self.assertIn(
            "FINISHED",
            bpy.ops.export_mesh.threemf(
                filepath=str(self.temp_file),
                use_orca_format="AUTO",
                plate_per_material=True,
            ),
        )

        def assembled_positions(path):
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read(SETTINGS_PATH))
            names = {
                _metadata(plate)["plater_name"]:
                _metadata(plate.find("model_instance"))["object_id"]
                for plate in root.findall("plate")
                if plate.find("model_instance") is not None
            }
            transforms = {
                item.get("object_id"): [float(v) for v in item.get("transform").split()][9:12]
                for item in root.find("assemble").findall("assemble_item")
            }
            return {name: transforms[object_id] for name, object_id in names.items()}

        before = assembled_positions(self.temp_file)
        self.clean_scene()
        imported = import_3mf(str(self.temp_file), import_materials="NONE")
        self.assertEqual(imported.status, "FINISHED")
        output = self.temp_file.with_name("assembled_roundtrip.3mf")
        self.assertEqual(export_3mf(str(output), objects=imported.objects).status, "FINISHED")
        after = assembled_positions(output)
        for axis in range(3):
            self.assertAlmostEqual(
                after["Blue"][axis] - after["Red"][axis],
                before["Blue"][axis] - before["Red"][axis],
                places=3,
            )


if __name__ == "__main__":
    unittest.main()
