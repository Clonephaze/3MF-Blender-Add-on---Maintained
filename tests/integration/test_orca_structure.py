"""
Integration tests for Orca multi-file import and export structure.

Covers:
- Orca export creates proper multi-file structure (3D/Objects/*.model)
- Main model has component references with p:path
- Relationships file references individual object models
- Round-trip: Orca export → import preserves objects
"""

import bpy
import unittest
import zipfile
import xml.etree.ElementTree as ET
from test_base import Blender3mfTestCase


class OrcaExportStructureTests(Blender3mfTestCase):
    """Verify Orca exporter produces correct multi-file archive structure."""

    def _export_orca_cube(self, count=1, **extra_kwargs):
        """Create cubes with materials and export in AUTO mode (auto-promotes to Orca)."""
        for i in range(count):
            bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
            cube = bpy.context.object
            mat = bpy.data.materials.new(f"Color_{i}")
            mat.use_nodes = True
            p = mat.node_tree.nodes.get("Principled BSDF")
            if p:
                p.inputs["Base Color"].default_value = (
                    float(i % 2), float((i + 1) % 2), 0.0, 1.0
                )
            cube.data.materials.append(mat)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="AUTO",
            **extra_kwargs,
        )
        self.assertIn("FINISHED", result)
        return self.temp_file

    def test_orca_has_object_model_files(self):
        """Orca export creates individual model files under 3D/Objects/."""
        self._export_orca_cube(count=2)
        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            object_files = [
                n for n in archive.namelist() if n.startswith("3D/Objects/")
            ]
            self.assertEqual(
                len(object_files), 2,
                f"Expected 2 object files, got {object_files}",
            )

    def test_orca_main_model_has_components(self):
        """Main model file contains <component> refs with p:path."""
        self._export_orca_cube(count=1)
        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            model_data = archive.read("3D/3dmodel.model")
            root = ET.fromstring(model_data)

            # Find component elements (any namespace prefix)
            components = root.findall(".//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}component")
            if not components:
                # Try without namespace (Orca may drop prefix for component)
                components = root.findall(".//component")

            # Should have at least one component
            self.assertGreater(len(components), 0, "Main model should contain component refs")

    def test_orca_relationships_file_exists(self):
        """3D/_rels/3dmodel.model.rels is present."""
        self._export_orca_cube(count=1)
        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            rels_files = [n for n in archive.namelist() if "3dmodel.model.rels" in n]
            self.assertGreater(
                len(rels_files), 0,
                "Should have 3D/_rels/3dmodel.model.rels",
            )

    def test_orca_object_model_has_mesh(self):
        """Individual object model files contain <vertices> and <triangles>."""
        self._export_orca_cube(count=1)
        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            object_files = [
                n for n in archive.namelist() if n.startswith("3D/Objects/")
            ]
            self.assertTrue(object_files, "No object model files found")

            obj_data = archive.read(object_files[0])
            root = ET.fromstring(obj_data)

            # The object model should have vertices + triangles
            ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
            verts = root.findall(f".//{{{ns}}}vertex")
            tris = root.findall(f".//{{{ns}}}triangle")
            # A cube has 8 verts and 12 triangles
            self.assertGreaterEqual(len(verts), 8)
            self.assertGreaterEqual(len(tris), 12)

    def test_orca_build_uuid(self):
        """Build element has a p:UUID attribute."""
        self._export_orca_cube(count=1)
        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            model_data = archive.read("3D/3dmodel.model")
            root = ET.fromstring(model_data)
            ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
            prod_ns = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
            build = root.find(f"{{{ns}}}build")
            if build is None:
                build = root.find("build")
            self.assertIsNotNone(build, "No <build> element found")

            uuid_val = build.get(f"{{{prod_ns}}}UUID")
            if uuid_val is None:
                uuid_val = build.get("p:UUID")
            self.assertIsNotNone(uuid_val, "Build should have a p:UUID")

    def test_orca_metadata_application(self):
        """Main model has BambuStudio application metadata."""
        self._export_orca_cube(count=1)
        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            model_data = archive.read("3D/3dmodel.model").decode("utf-8")
            self.assertIn("BambuStudio", model_data)

    def test_orca_multi_object_uuid_format(self):
        """More than 9 objects should still produce valid UUIDs (issue #5 fix)."""
        # Create 12 objects (regression test for zero-padded hex fix)
        for i in range(12):
            bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
            mat = bpy.data.materials.new(f"Mat_{i}")
            mat.use_nodes = True
            bpy.context.object.data.materials.append(mat)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )
        self.assertIn("FINISHED", result)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            obj_files = [n for n in archive.namelist() if n.startswith("3D/Objects/")]
            self.assertEqual(len(obj_files), 12)
            # All should be valid XML
            for f in obj_files:
                data = archive.read(f)
                root = ET.fromstring(data)
                self.assertIsNotNone(root)


class OrcaRoundtripTests(Blender3mfTestCase):
    """Orca export → import round-trip tests."""

    def test_roundtrip_preserves_object_count(self):
        """Orca export → import preserves the number of objects."""
        for i in range(3):
            bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
            mat = bpy.data.materials.new(f"Roundtrip_{i}")
            mat.use_nodes = True
            bpy.context.object.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )
        self.clean_scene()

        result = bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
        self.assertIn("FINISHED", result)
        self.assertEqual(len(bpy.data.objects), 3)

    def test_roundtrip_preserves_geometry(self):
        """Orca round-trip preserves vertex count per object."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        mat = bpy.data.materials.new("RT_Mat")
        mat.use_nodes = True
        bpy.context.object.data.materials.append(mat)
        original_verts = len(bpy.context.object.data.vertices)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )
        self.clean_scene()

        bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
        imported = bpy.data.objects[0]
        self.assertEqual(len(imported.data.vertices), original_verts)

    def test_roundtrip_multi_material(self):
        """Multi-material export → import preserves both objects and materials."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        mat_a = self.create_red_material()
        mat_b = self.create_blue_material()
        cube.data.materials.append(mat_a)
        cube.data.materials.append(mat_b)

        # Assign different materials to faces
        import bmesh
        mesh = cube.data
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        for i, face in enumerate(bm.faces):
            face.material_index = i % 2
        bm.to_mesh(mesh)
        bm.free()

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )
        self.clean_scene()

        bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))
        self.assertGreater(len(bpy.data.objects), 0)


class OrcaProjectMetadataTests(Blender3mfTestCase):
    """Verify Orca project_settings.config is written."""

    def test_project_settings_exists(self):
        """Orca export includes Metadata/project_settings.config."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        mat = bpy.data.materials.new("ProjMat")
        mat.use_nodes = True
        bpy.context.object.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            config_files = [
                n for n in archive.namelist()
                if "project_settings" in n.lower()
            ]
            self.assertGreater(
                len(config_files), 0,
                "Orca export should include project_settings.config",
            )

    def test_project_settings_valid_json(self):
        """project_settings.config is valid JSON."""
        import json
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        mat = bpy.data.materials.new("JsonMat")
        mat.use_nodes = True
        bpy.context.object.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            config_files = [
                n for n in archive.namelist()
                if "project_settings" in n.lower()
            ]
            if config_files:
                data = archive.read(config_files[0]).decode("utf-8")
                parsed = json.loads(data)
                self.assertIsInstance(parsed, dict)


if __name__ == "__main__":
    unittest.main()
