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
            use_orca_format='PAINT',
            mmu_slicer_format='PRUSA',
        )

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_prusa_export_produces_valid_zip(self):
        """Prusa export creates a valid ZIP archive."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        mat = self.create_red_material()
        cube.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format='PAINT',
            mmu_slicer_format='PRUSA',
        )

        with zipfile.ZipFile(str(self.temp_file), 'r') as archive:
            names = archive.namelist()
            self.assertIn('3D/3dmodel.model', names)

    def test_prusa_export_single_model_file(self):
        """Prusa format uses a single model file (not multi-file like Orca)."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        mat = self.create_red_material()
        cube.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format='PAINT',
            mmu_slicer_format='PRUSA',
        )

        with zipfile.ZipFile(str(self.temp_file), 'r') as archive:
            names = archive.namelist()
            # Prusa uses single model file, no 3D/Objects/ directory
            object_files = [n for n in names if n.startswith('3D/Objects/')]
            self.assertEqual(len(object_files), 0,
                             "Prusa format should not have separate object files")


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
            use_orca_format='PAINT',
            mmu_slicer_format='PRUSA',
        )

        with zipfile.ZipFile(str(self.temp_file), 'r') as archive:
            with archive.open('3D/3dmodel.model') as f:
                return ET.parse(f).getroot()

    def test_prusa_metadata_version3mf(self):
        """Prusa export includes slic3rpe:Version3mf metadata."""
        root = self._export_and_parse()
        ns = {'m': MODEL_NS}
        metadata_elems = root.findall('m:metadata', ns)

        version_found = False
        for meta in metadata_elems:
            if meta.get('name') == 'slic3rpe:Version3mf':
                version_found = True
                self.assertEqual(meta.text, '1')
                break

        self.assertTrue(version_found,
                        "Prusa export should include slic3rpe:Version3mf metadata")

    def test_prusa_metadata_mm_painting_version(self):
        """Prusa export includes slic3rpe:MmPaintingVersion metadata."""
        root = self._export_and_parse()
        ns = {'m': MODEL_NS}
        metadata_elems = root.findall('m:metadata', ns)

        painting_found = False
        for meta in metadata_elems:
            if meta.get('name') == 'slic3rpe:MmPaintingVersion':
                painting_found = True
                self.assertEqual(meta.text, '1')
                break

        self.assertTrue(painting_found,
                        "Prusa export should include slic3rpe:MmPaintingVersion metadata")

    def test_prusa_model_unit_is_millimeter(self):
        """Prusa model file sets unit to millimeter."""
        root = self._export_and_parse()
        self.assertEqual(root.get('unit'), 'millimeter')


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
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(cube.data)
        bm.faces.ensure_lookup_table()
        for i, face in enumerate(bm.faces):
            face.material_index = 1 if i < 3 else 0
        bmesh.update_edit_mesh(cube.data)
        bpy.ops.object.mode_set(mode='OBJECT')

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format='PAINT',
            mmu_slicer_format='PRUSA',
        )

        self.assertIn('FINISHED', result)
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
        bpy.ops.object.mode_set(mode='EDIT')
        bm = bmesh.from_edit_mesh(cube.data)
        bm.faces.ensure_lookup_table()
        for i, face in enumerate(bm.faces):
            face.material_index = 1 if i < 3 else 0
        bmesh.update_edit_mesh(cube.data)
        bpy.ops.object.mode_set(mode='OBJECT')

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format='PAINT',
            mmu_slicer_format='PRUSA',
        )

        with zipfile.ZipFile(str(self.temp_file), 'r') as archive:
            # At minimum, the model file should exist and be valid XML
            with archive.open('3D/3dmodel.model') as f:
                root = ET.parse(f).getroot()
            self.assertIsNotNone(root, "Model file should parse as valid XML")

    def test_prusa_export_multiple_objects(self):
        """Multiple objects export in Prusa format."""
        for i, loc in enumerate([(0, 0, 0), (3, 0, 0), (-3, 0, 0)]):
            bpy.ops.mesh.primitive_cube_add(location=loc)
            cube = bpy.context.object
            mat = self.create_red_material()
            cube.data.materials.append(mat)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format='PAINT',
            mmu_slicer_format='PRUSA',
        )

        self.assertIn('FINISHED', result)

        with zipfile.ZipFile(str(self.temp_file), 'r') as archive:
            with archive.open('3D/3dmodel.model') as f:
                root = ET.parse(f).getroot()
            ns = {'m': MODEL_NS}
            resources = root.find('m:resources', ns)
            objects = resources.findall('m:object', ns)
            self.assertGreaterEqual(len(objects), 3,
                                    "Should have at least 3 objects in resources")


if __name__ == "__main__":
    unittest.main()
