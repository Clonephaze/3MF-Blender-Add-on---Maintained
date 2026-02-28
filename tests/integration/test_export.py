"""
Export tests for Blender 3MF addon using real Blender API.

Covers export functionality without mocking.
"""

import bpy
import unittest
import zipfile
import xml.etree.ElementTree as ET
from test_base import Blender3mfTestCase


class ExportBasicTests(Blender3mfTestCase):
    """Basic export functionality tests."""

    def test_export_simple_cube(self):
        """Export a simple cube."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        cube.name = "TestCube"

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())
        self.assertGreater(self.temp_file.stat().st_size, 0)

    def test_export_multiple_objects(self):
        """Export multiple objects."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        bpy.ops.mesh.primitive_uv_sphere_add(location=(3, 0, 0))
        bpy.ops.mesh.primitive_cone_add(location=(-3, 0, 0))

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_export_empty_scene(self):
        """Export with no objects - should handle gracefully."""
        # Scene already empty from clean_scene
        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        # Should complete without error
        self.assertIn('FINISHED', result)

    def test_export_selection_only(self):
        """Export only selected objects."""
        # Create two cubes
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube1 = bpy.context.object
        cube1.name = "Cube1"

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        cube2 = bpy.context.object
        cube2.name = "Cube2"

        # Select only first cube
        bpy.ops.object.select_all(action='DESELECT')
        cube1.select_set(True)
        bpy.context.view_layer.objects.active = cube1

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_selection=True
        )

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_export_with_modifiers(self):
        """Export with modifiers applied."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        # Add subdivision modifier
        modifier = cube.modifiers.new(name="Subsurf", type='SUBSURF')
        modifier.levels = 2

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_mesh_modifiers=True
        )

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_export_nested_objects(self):
        """Export parent-child hierarchy."""
        # Create parent
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        parent = bpy.context.object
        parent.name = "Parent"

        # Create child
        bpy.ops.mesh.primitive_cube_add(location=(2, 0, 0))
        child = bpy.context.object
        child.name = "Child"
        child.parent = parent

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_export_with_transformation(self):
        """Export object with transformations."""
        bpy.ops.mesh.primitive_cube_add(location=(5, 10, 15))
        cube = bpy.context.object
        cube.scale = (2, 2, 2)
        cube.rotation_euler = (0.5, 0.5, 0.5)

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())


class ExportMaterialTests(Blender3mfTestCase):
    """Material-related export tests."""

    def test_export_with_material(self):
        """Export object with a material."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        mat = self.create_red_material()
        cube.data.materials.append(mat)

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_export_with_none_material(self):
        """Export object with empty material slot - validates bug fix."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        # Add empty material slot
        cube.data.materials.append(None)

        # Should not crash
        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_export_multiple_materials(self):
        """Export object with multiple materials."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        red_mat = self.create_red_material()
        blue_mat = self.create_blue_material()

        cube.data.materials.append(red_mat)
        cube.data.materials.append(blue_mat)

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_export_multi_material_uses_orca_format(self):
        """Multi-material face assignments should export with Orca paint_color attributes.

        When an object has multiple materials assigned to different faces, the
        exporter should auto-detect this and use the Orca Production Extension
        format (multi-file with paint_color per triangle) instead of spec
        basematerials, which slicers like Orca Slicer and BambuStudio ignore.
        """
        # Create a cube with two materials assigned to different faces
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        red_mat = self.create_red_material()
        blue_mat = self.create_blue_material()

        cube.data.materials.append(red_mat)
        cube.data.materials.append(blue_mat)

        # Assign blue material to some faces in edit mode
        bpy.ops.object.mode_set(mode='EDIT')
        import bmesh
        bm = bmesh.from_edit_mesh(cube.data)
        bm.faces.ensure_lookup_table()
        # Assign material index 1 (blue) to the first 4 faces
        for i, face in enumerate(bm.faces):
            face.material_index = 1 if i < 4 else 0
        bmesh.update_edit_mesh(cube.data)
        bpy.ops.object.mode_set(mode='OBJECT')

        # Export in AUTO mode (the default)
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format='AUTO',
        )

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

        # Verify the archive uses Orca Production Extension structure
        with zipfile.ZipFile(self.temp_file, 'r') as archive:
            files = archive.namelist()

            # Should have individual object model files in 3D/Objects/
            object_files = [f for f in files if f.startswith('3D/Objects/')]
            self.assertGreater(
                len(object_files), 0,
                "Multi-material export should use Orca multi-file structure "
                "with object files in 3D/Objects/"
            )

            # Read the individual object model and check for paint_color
            object_model_data = archive.read(object_files[0]).decode('UTF-8')
            root = ET.fromstring(object_model_data)

            # Find all triangle elements (no namespace prefix in Orca object files)
            triangles = root.findall('.//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}triangle')
            if not triangles:
                # Try without namespace (Orca format uses plain element names)
                triangles = root.findall('.//triangle')

            self.assertGreater(len(triangles), 0, "Should have triangle elements")

            # At least some triangles should have paint_color attributes
            paint_color_triangles = [
                t for t in triangles if t.get('paint_color')
            ]
            self.assertGreater(
                len(paint_color_triangles), 0,
                "Multi-material faces should produce paint_color attributes "
                "on triangles for slicer compatibility"
            )

            # Verify no <basematerials> in the object model (Orca doesn't use them)
            basematerials = root.findall(
                './/{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}basematerials'
            )
            if not basematerials:
                basematerials = root.findall('.//basematerials')
            self.assertEqual(
                len(basematerials), 0,
                "Orca object model files should not contain basematerials elements"
            )

            # Verify Orca metadata files exist
            has_model_settings = 'Metadata/model_settings.config' in files
            has_project_settings = 'Metadata/project_settings.config' in files
            self.assertTrue(
                has_model_settings,
                "Orca export should include model_settings.config"
            )
            self.assertTrue(
                has_project_settings,
                "Orca export should include project_settings.config"
            )

    def test_export_single_material_uses_orca_for_slicer_compat(self):
        """Single-material objects auto-promote to Orca format for slicer compatibility.

        The exporter uses OrcaExporter whenever materials are present because
        Orca/BambuStudio ignore core-spec <basematerials> and only read
        colorgroup/paint_color attributes.
        """
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        red_mat = self.create_red_material()
        cube.data.materials.append(red_mat)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format='AUTO',
        )

        self.assertIn('FINISHED', result)

        with zipfile.ZipFile(self.temp_file, 'r') as archive:
            files = archive.namelist()

            # Materials detected → OrcaExporter is used for slicer compatibility,
            # so individual object files appear under 3D/Objects/.
            object_files = [f for f in files if f.startswith('3D/Objects/')]
            self.assertGreaterEqual(
                len(object_files), 1,
                "Material present: Orca multi-file structure expected for slicer compat"
            )

            # The main model file should still exist
            self.assertIn('3D/3dmodel.model', files)

    def test_export_mixed_none_materials(self):
        """Export with mix of materials and None slots."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object

        red_mat = self.create_red_material()

        cube.data.materials.append(red_mat)
        cube.data.materials.append(None)
        cube.data.materials.append(None)

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())


class ExportArchiveTests(Blender3mfTestCase):
    """Tests verifying 3MF archive structure."""

    def test_archive_structure(self):
        """Verify exported file has correct 3MF structure."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        # Verify it's a valid zip
        self.assertTrue(zipfile.is_zipfile(self.temp_file))

        with zipfile.ZipFile(self.temp_file, 'r') as archive:
            files = archive.namelist()

            # Required 3MF files
            self.assertIn('[Content_Types].xml', files)
            self.assertIn('3D/3dmodel.model', files)
            self.assertIn('_rels/.rels', files)

    def test_valid_xml(self):
        """Verify exported model contains valid XML."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        with zipfile.ZipFile(self.temp_file, 'r') as archive:
            model_data = archive.read('3D/3dmodel.model')

            # Should parse without error
            root = ET.fromstring(model_data)

            # Verify namespace
            self.assertIn('http://schemas.microsoft.com/3dmanufacturing/core/2015/02', root.tag)

    def test_contains_vertices(self):
        """Verify exported model contains vertex data."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        with zipfile.ZipFile(self.temp_file, 'r') as archive:
            model_data = archive.read('3D/3dmodel.model')
            root = ET.fromstring(model_data)

            ns = {'m': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
            vertices = root.findall('.//m:vertex', ns)

            # Cube has 8 vertices
            self.assertGreaterEqual(len(vertices), 8)

    def test_contains_triangles(self):
        """Verify exported model contains triangle data."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        with zipfile.ZipFile(self.temp_file, 'r') as archive:
            model_data = archive.read('3D/3dmodel.model')
            root = ET.fromstring(model_data)

            ns = {'m': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
            triangles = root.findall('.//m:triangle', ns)

            # Cube has 12 triangles
            self.assertGreaterEqual(len(triangles), 12)


class ExportEdgeCaseTests(Blender3mfTestCase):
    """Edge case and error handling tests."""

    def test_export_non_mesh_objects(self):
        """Export scene with non-mesh objects (should skip them)."""
        # Add camera
        bpy.ops.object.camera_add(location=(0, 0, 10))

        # Add mesh
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        self.assertIn('FINISHED', result)
        self.assertTrue(self.temp_file.exists())

    def test_export_object_no_faces(self):
        """Export object with vertices but no faces."""
        mesh = bpy.data.meshes.new("EmptyMesh")
        obj = bpy.data.objects.new("EmptyObject", mesh)
        bpy.context.collection.objects.link(obj)

        # Add vertices but no faces
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [])

        result = bpy.ops.export_mesh.threemf(filepath=str(self.temp_file))

        # Should complete (may skip object or create empty file)
        self.assertIn('FINISHED', result)


if __name__ == '__main__':
    unittest.main()
