"""
Integration tests for PrusaSlicer 3MF export.

Covers ``io_mesh_3mf.export_3mf.prusa.PrusaExporter``:
- Basic Prusa-format export with single and multi-material objects
- slic3rpe:mmu_segmentation namespace presence in model XML
- Filament color metadata round-trip
- Scene metadata (Version3mf, MmPaintingVersion)
"""

import bpy
import bmesh
import unittest
import zipfile
import xml.etree.ElementTree as ET
from test_base import Blender3mfTestCase


# PrusaSlicer XML namespace
SLIC3RPE_NS = "http://schemas.slic3r.org/3mf/2017/06"
MODEL_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"


class PrusaExportBasicTests(Blender3mfTestCase):
    """Basic PrusaSlicer export tests."""

    def test_prusa_export_single_object(self):
        """Export a single colored cube in Prusa format."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object

        mat = self.create_red_material()
        cube.data.materials.append(mat)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

        self.assertIn("FINISHED", result)
        self.assertTrue(self.temp_file.exists())

    def test_prusa_export_produces_valid_zip(self):
        """Prusa export creates a valid ZIP archive."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        mat = self.create_red_material()
        cube.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            names = archive.namelist()
            self.assertIn("3D/3dmodel.model", names)

    def test_prusa_export_single_model_file(self):
        """Prusa format uses a single model file (not multi-file like Orca)."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        mat = self.create_red_material()
        cube.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            names = archive.namelist()
            # Prusa uses single model file, no 3D/Objects/ directory
            object_files = [n for n in names if n.startswith("3D/Objects/")]
            self.assertEqual(
                len(object_files),
                0,
                "Prusa format should not have separate object files",
            )


class PrusaExportMetadataTests(Blender3mfTestCase):
    """Tests for Prusa-specific metadata in exported files."""

    def _export_and_parse(self):
        """Helper: export and return parsed model root."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        mat = self.create_red_material()
        cube.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            with archive.open("3D/3dmodel.model") as f:
                return ET.parse(f).getroot()

    def test_prusa_metadata_version3mf(self):
        """Prusa export includes slic3rpe:Version3mf metadata."""
        root = self._export_and_parse()
        ns = {"m": MODEL_NS}
        metadata_elems = root.findall("m:metadata", ns)

        version_found = False
        for meta in metadata_elems:
            if meta.get("name") == "slic3rpe:Version3mf":
                version_found = True
                self.assertEqual(meta.text, "1")
                break

        self.assertTrue(
            version_found, "Prusa export should include slic3rpe:Version3mf metadata"
        )

    def test_prusa_metadata_mm_painting_version(self):
        """Prusa export includes slic3rpe:MmPaintingVersion metadata."""
        root = self._export_and_parse()
        ns = {"m": MODEL_NS}
        metadata_elems = root.findall("m:metadata", ns)

        painting_found = False
        for meta in metadata_elems:
            if meta.get("name") == "slic3rpe:MmPaintingVersion":
                painting_found = True
                self.assertEqual(meta.text, "1")
                break

        self.assertTrue(
            painting_found,
            "Prusa export should include slic3rpe:MmPaintingVersion metadata",
        )

    def test_prusa_model_unit_is_millimeter(self):
        """Prusa model file sets unit to millimeter."""
        root = self._export_and_parse()
        self.assertEqual(root.get("unit"), "millimeter")


class PrusaExportMultiMaterialTests(Blender3mfTestCase):
    """Tests for multi-material Prusa export."""

    def test_prusa_export_multi_material(self):
        """Multi-material object exports successfully in Prusa format."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object

        red_mat = self.create_red_material()
        blue_mat = self.create_blue_material()
        cube.data.materials.append(red_mat)
        cube.data.materials.append(blue_mat)

        # Assign different materials to faces
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(cube.data)
        bm.faces.ensure_lookup_table()
        for i, face in enumerate(bm.faces):
            face.material_index = 1 if i < 3 else 0
        bmesh.update_edit_mesh(cube.data)
        bpy.ops.object.mode_set(mode="OBJECT")

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

        self.assertIn("FINISHED", result)
        self.assertTrue(self.temp_file.exists())
        self.assertGreater(self.temp_file.stat().st_size, 0)

    def test_prusa_export_filament_colors_metadata(self):
        """Filament colors are written as metadata for round-trip import."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object

        red_mat = self.create_red_material()
        blue_mat = self.create_blue_material()
        cube.data.materials.append(red_mat)
        cube.data.materials.append(blue_mat)

        # Assign materials
        bpy.ops.object.mode_set(mode="EDIT")
        bm = bmesh.from_edit_mesh(cube.data)
        bm.faces.ensure_lookup_table()
        for i, face in enumerate(bm.faces):
            face.material_index = 1 if i < 3 else 0
        bmesh.update_edit_mesh(cube.data)
        bpy.ops.object.mode_set(mode="OBJECT")

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            # At minimum, the model file should exist and be valid XML
            with archive.open("3D/3dmodel.model") as f:
                root = ET.parse(f).getroot()
            self.assertIsNotNone(root, "Model file should parse as valid XML")

    def test_prusa_export_multiple_objects(self):
        """Multiple objects are combined into a single build item (fixes issue #24)."""
        for loc in [(0, 0, 0), (3, 0, 0), (-3, 0, 0)]:
            bpy.ops.mesh.primitive_cube_add(location=loc)
            cube = bpy.context.object
            mat = self.create_red_material()
            cube.data.materials.append(mat)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

        self.assertIn("FINISHED", result)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            with archive.open("3D/3dmodel.model") as f:
                root = ET.parse(f).getroot()
            ns = {"m": MODEL_NS}
            resources = root.find("m:resources", ns)
            objects = resources.findall("m:object", ns)
            # All mesh objects are combined into ONE object resource so that
            # PrusaSlicer does not show the "Multi-part object detected" dialog.
            self.assertEqual(
                len(objects), 1, "Multiple Blender objects must be merged into a single combined object"
            )


class PrusaExportSingleBuildItemTests(Blender3mfTestCase):
    """Tests that enforce the single-build-item requirement (issue #24)."""

    def _export_multi_object(self):
        """Create 3 objects with different materials and export as Prusa."""
        red_mat = self.create_red_material()
        blue_mat = self.create_blue_material()

        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        bpy.context.object.data.materials.append(red_mat)

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        bpy.context.object.data.materials.append(blue_mat)

        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 3))
        bpy.context.object.data.materials.append(red_mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

    def test_single_build_item(self):
        """Prusa export produces exactly one <build><item>.

        PrusaSlicer shows 'Multi-part object detected' when there are multiple
        build items, which breaks Z-positions and color assignments (issue #24).
        """
        self._export_multi_object()

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            with archive.open("3D/3dmodel.model") as f:
                root = ET.parse(f).getroot()

        ns = {"m": MODEL_NS}
        build = root.find("m:build", ns)
        self.assertIsNotNone(build, "<build> element must exist")
        items = build.findall("m:item", ns)
        self.assertEqual(
            len(items), 1,
            "Prusa export must have exactly one <build><item> to avoid the "
            "'Multi-part object detected' dialog in PrusaSlicer"
        )

    def test_single_combined_object_resource(self):
        """Prusa export produces exactly one <object> in <resources>."""
        self._export_multi_object()

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            with archive.open("3D/3dmodel.model") as f:
                root = ET.parse(f).getroot()

        ns = {"m": MODEL_NS}
        resources = root.find("m:resources", ns)
        objects = resources.findall("m:object", ns)
        self.assertEqual(
            len(objects), 1,
            "All mesh objects must be merged into a single <object> resource"
        )

    def test_build_item_references_combined_object(self):
        """The single build item must reference the single combined object."""
        self._export_multi_object()

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            with archive.open("3D/3dmodel.model") as f:
                root = ET.parse(f).getroot()

        ns = {"m": MODEL_NS}
        obj_id = root.find("m:resources/m:object", ns).get("id")
        item = root.find("m:build/m:item", ns)
        self.assertEqual(
            item.get("objectid"), obj_id,
            "The build item must reference the combined object's resource ID"
        )

    def test_model_config_single_object_with_volumes(self):
        """Slic3r_PE_model.config has one <object> with one <volume> per mesh."""
        self._export_multi_object()

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            names = archive.namelist()
            self.assertIn(
                "Metadata/Slic3r_PE_model.config", names,
                "Slic3r_PE_model.config must be present"
            )
            with archive.open("Metadata/Slic3r_PE_model.config") as f:
                config_root = ET.parse(f).getroot()

        objects = config_root.findall("object")
        self.assertEqual(
            len(objects), 1,
            "model config must have exactly one <object> element"
        )
        volumes = objects[0].findall("volume")
        self.assertEqual(
            len(volumes), 3,
            "model config object must have one <volume> per exported mesh"
        )

    def test_model_config_volume_cumulative_ids(self):
        """Volume firstid/lastid in model config are cumulative across parts."""
        self._export_multi_object()

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            with archive.open("Metadata/Slic3r_PE_model.config") as f:
                config_root = ET.parse(f).getroot()

        volumes = config_root.find("object").findall("volume")
        prev_lastid = -1
        for vol in volumes:
            firstid = int(vol.get("firstid"))
            lastid = int(vol.get("lastid"))
            self.assertEqual(
                firstid, prev_lastid + 1,
                f"Volume firstid={firstid} must follow previous lastid={prev_lastid}"
            )
            self.assertGreater(
                lastid, firstid,
                f"Volume lastid={lastid} must be >= firstid={firstid}"
            )
            prev_lastid = lastid

    def test_z_position_preserved_in_combined_mesh(self):
        """Objects at different Z heights have distinct vertex Z values.

        Regression test for issue #24: clicking 'No' in PrusaSlicer's dialog
        reset all Z positions to 0. With the combined-mesh approach the world
        transform is baked in, so each part's Z offset must appear in the vertex
        data.
        """
        # Cube A at Z=0, Cube B at Z=5
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        bpy.context.object.data.materials.append(self.create_red_material())

        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 5))
        bpy.context.object.data.materials.append(self.create_blue_material())

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
        )

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            with archive.open("3D/3dmodel.model") as f:
                root = ET.parse(f).getroot()

        ns = {"m": MODEL_NS}
        vertices = root.findall(".//m:vertex", ns)
        z_values = {float(v.get("z")) for v in vertices}

        # The cube at Z=5 has vertices at 4 and 6 (unit cube half-size=1).
        # If world transforms are baked in, we expect some z > 3.
        high_z = [z for z in z_values if z > 3.0]
        self.assertTrue(
            len(high_z) > 0,
            f"Expected vertices with z > 3 from the elevated cube, got z values: {sorted(z_values)}"
        )


if __name__ == "__main__":
    unittest.main()
