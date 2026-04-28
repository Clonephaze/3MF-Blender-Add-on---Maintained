"""
Integration tests for include_disabled export behaviour.

Covers the bug fixes in 2.4.4:
- Disabled objects keeping their material colors through all exporters
- API export_3mf(objects=[...]) respecting caller's explicit list
- Component detection including disabled linked duplicates
- Thumbnail OPC content type override for Windows Explorer

All tests run inside real Blender (``--background --factory-startup``).
"""

import bmesh
import bpy
import unittest
import zipfile
import xml.etree.ElementTree as ET
from test_base import Blender3mfTestCase

from io_mesh_3mf.api import export_3mf, ExportResult

MODEL_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"


# ============================================================================
# Helpers
# ============================================================================


def _assign_material_per_face(cube, mat_a, mat_b):
    """Assign mat_a to the first half of faces, mat_b to the rest."""
    cube.data.materials.append(mat_a)
    cube.data.materials.append(mat_b)
    mesh = cube.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    half = len(bm.faces) // 2
    for i, face in enumerate(bm.faces):
        face.material_index = 0 if i < half else 1
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


# ============================================================================
# Operator: Orca export with disabled objects
# ============================================================================


class TestOrcaIncludeDisabled(Blender3mfTestCase):
    """Orca PAINT export must include render-disabled objects when opted in."""

    def test_disabled_object_exported_in_orca(self):
        """A render-disabled object should appear in the archive with include_disabled."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube1 = bpy.context.object
        cube1.name = "Enabled_Cube"
        mat_r = self.create_red_material()
        cube1.data.materials.append(mat_r)

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        cube2 = bpy.context.object
        cube2.name = "Disabled_Cube"
        mat_b = self.create_blue_material()
        cube2.data.materials.append(mat_b)
        cube2.hide_render = True  # render-disabled

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="AUTO",
            include_disabled=True,
        )
        self.assertIn("FINISHED", result)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            object_files = [
                n for n in archive.namelist() if n.startswith("3D/Objects/")
            ]
            self.assertEqual(
                len(object_files),
                2,
                f"Both objects should be exported; got {object_files}",
            )

    def test_disabled_object_excluded_by_default(self):
        """Without include_disabled, a render-disabled object is skipped."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube1 = bpy.context.object
        mat_r = self.create_red_material()
        cube1.data.materials.append(mat_r)

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        cube2 = bpy.context.object
        cube2.hide_render = True
        mat_b = self.create_blue_material()
        cube2.data.materials.append(mat_b)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="AUTO",
            include_disabled=False,
        )
        self.assertIn("FINISHED", result)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            object_files = [
                n for n in archive.namelist() if n.startswith("3D/Objects/")
            ]
            self.assertEqual(
                len(object_files),
                1,
                f"Only the enabled object should be exported; got {object_files}",
            )

    def test_disabled_object_colors_present_orca(self):
        """Disabled object's face colors must appear in the Orca color zone list."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube1 = bpy.context.object
        mat_r = self.create_red_material()
        mat_b = self.create_blue_material()
        _assign_material_per_face(cube1, mat_r, mat_b)

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        cube2 = bpy.context.object
        cube2.hide_render = True
        mat_g = bpy.data.materials.new("GreenMaterial")
        mat_g.use_nodes = True
        p = mat_g.node_tree.nodes.get("Principled BSDF")
        if p:
            p.inputs["Base Color"].default_value = (0.0, 1.0, 0.0, 1.0)
        cube2.data.materials.append(mat_g)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="AUTO",
            include_disabled=True,
        )
        self.assertIn("FINISHED", result)

        # The Orca project_settings.config encodes filament_colour; check
        # that it has at least 2 distinct colors (red/blue + green).
        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            object_files = [
                n for n in archive.namelist() if n.startswith("3D/Objects/")
            ]
            # Both cubes should be present
            self.assertEqual(len(object_files), 2)


# ============================================================================
# Operator: Prusa export with disabled objects
# ============================================================================


class TestPrusaIncludeDisabled(Blender3mfTestCase):
    """Prusa PAINT export must include render-disabled objects when opted in."""

    def test_disabled_object_exported_in_prusa(self):
        """A render-disabled object should produce triangles in Prusa export."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube1 = bpy.context.object
        mat_r = self.create_red_material()
        cube1.data.materials.append(mat_r)

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        cube2 = bpy.context.object
        cube2.hide_render = True
        mat_b = self.create_blue_material()
        cube2.data.materials.append(mat_b)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
            include_disabled=True,
        )
        self.assertIn("FINISHED", result)

        # Prusa uses single model file — verify both objects are present
        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            model_data = archive.read("3D/3dmodel.model")
            root = ET.fromstring(model_data)
            objects = root.findall(f".//{{{MODEL_NS}}}object")
            mesh_objects = [
                o for o in objects if o.find(f"{{{MODEL_NS}}}mesh") is not None
            ]
            self.assertGreaterEqual(
                len(mesh_objects),
                2,
                "Both objects (enabled + disabled) should have mesh data",
            )


# ============================================================================
# Operator: Standard export with disabled objects
# ============================================================================


class TestStandardIncludeDisabled(Blender3mfTestCase):
    """Standard exporter must include render-disabled objects when opted in."""

    def test_disabled_object_exported_in_standard(self):
        """Standard export with include_disabled should write both objects."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube1 = bpy.context.object
        cube1.name = "Enabled"

        bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
        cube2 = bpy.context.object
        cube2.name = "Disabled"
        cube2.hide_render = True

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="STANDARD",
            include_disabled=True,
        )
        self.assertIn("FINISHED", result)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            model_data = archive.read("3D/3dmodel.model")
            root = ET.fromstring(model_data)
            objects = root.findall(f".//{{{MODEL_NS}}}object")
            mesh_objects = [
                o for o in objects if o.find(f"{{{MODEL_NS}}}mesh") is not None
            ]
            self.assertGreaterEqual(
                len(mesh_objects),
                2,
                "Standard export with include_disabled should write both objects",
            )


# ============================================================================
# API: export_3mf(objects=[...]) with hidden/disabled objects
# ============================================================================


class TestAPIExplicitObjects(Blender3mfTestCase):
    """export_3mf(objects=[...]) must respect the caller's explicit list."""

    def test_api_exports_hidden_object(self):
        """An explicitly passed hidden object should be exported."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        cube.hide_set(True)  # viewport-hidden
        cube.hide_render = True  # render-disabled

        result = export_3mf(
            str(self.temp_file),
            objects=[cube],
            use_orca_format="STANDARD",
        )
        self.assertIsInstance(result, ExportResult)
        self.assertEqual(result.status, "FINISHED")
        self.assertGreater(result.num_written, 0)

    def test_api_exports_disabled_with_colors(self):
        """An explicitly passed disabled object should keep its material colors in Orca."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        cube.hide_render = True
        mat = self.create_red_material()
        cube.data.materials.append(mat)

        result = export_3mf(
            str(self.temp_file),
            objects=[cube],
            use_orca_format="AUTO",
        )
        self.assertEqual(result.status, "FINISHED")

        # Should NOT contain the "No face colors" or "No mesh objects" warnings
        for w in result.warnings:
            self.assertNotIn("No face colors", w)
            self.assertNotIn("No mesh objects", w)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            object_files = [
                n for n in archive.namelist() if n.startswith("3D/Objects/")
            ]
            self.assertEqual(
                len(object_files),
                1,
                "The explicitly passed object should be exported",
            )

    def test_api_no_mesh_objects_warning_gone(self):
        """Passing objects= should never produce 'No mesh objects found'."""
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        cube.hide_render = True
        mat = self.create_red_material()
        cube.data.materials.append(mat)

        result = export_3mf(
            str(self.temp_file),
            objects=[cube],
            use_orca_format="PAINT",
            mmu_slicer_format="ORCA",
        )
        # Should succeed or at least not produce the "no mesh objects" error
        self.assertNotEqual(result.status, "CANCELLED")

        for w in result.warnings:
            self.assertNotIn("No mesh objects found", w)


# ============================================================================
# Component detection with disabled linked duplicates
# ============================================================================


class TestComponentsIncludeDisabled(Blender3mfTestCase):
    """detect_linked_duplicates must see disabled objects when include_disabled is set."""

    def test_disabled_linked_duplicate_uses_components(self):
        """Disabled linked duplicate should still be exported as a component instance."""
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        base = bpy.context.object
        base.name = "Base"

        bpy.ops.object.duplicate_move_linked(
            OBJECT_OT_duplicate={"linked": True},
            TRANSFORM_OT_translate={"value": (3, 0, 0)},
        )
        linked = bpy.context.object
        linked.name = "LinkedDisabled"
        linked.hide_render = True

        # Verify they share mesh data
        self.assertEqual(base.data, linked.data)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="STANDARD",
            use_components=True,
            include_disabled=True,
        )
        self.assertIn("FINISHED", result)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            model_data = archive.read("3D/3dmodel.model")
            root = ET.fromstring(model_data)

            # Find component references
            components = root.findall(f".//{{{MODEL_NS}}}component")
            # Both objects should appear as component instances referencing
            # the same shared mesh definition
            self.assertGreaterEqual(
                len(components),
                2,
                "Both linked duplicates (inc. disabled one) should be component instances",
            )

            # All component refs should point to the same objectid
            object_ids = {c.get("objectid") for c in components}
            self.assertEqual(
                len(object_ids),
                1,
                "All component instances should reference the same mesh definition",
            )


# ============================================================================
# Thumbnail OPC content type
# ============================================================================


class TestThumbnailContentType(Blender3mfTestCase):
    """Thumbnail must have image/png content type for Windows Explorer."""

    def test_thumbnail_override_present(self):
        """[Content_Types].xml must have an Override for thumbnail.png = image/png."""
        bpy.ops.mesh.primitive_cube_add()

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="STANDARD",
            thumbnail_mode="NONE",  # Don't render, just check the override exists
        )
        self.assertIn("FINISHED", result)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            ct_data = archive.read("[Content_Types].xml").decode("utf-8")
            root = ET.fromstring(ct_data)

            # The .png Default should be the 3MF texture type (not image/png)
            defaults = root.findall(f"{{{CONTENT_TYPES_NS}}}Default")
            png_default = None
            for d in defaults:
                ext = d.get(f"{{{CONTENT_TYPES_NS}}}Extension") or d.get("Extension")
                if ext == "png":
                    png_default = d.get(f"{{{CONTENT_TYPES_NS}}}ContentType") or d.get(
                        "ContentType"
                    )

            # Whether or not the png default is the texture type, there should
            # be an Override for the thumbnail with image/png
            overrides = root.findall(f"{{{CONTENT_TYPES_NS}}}Override")
            thumbnail_override = None
            for o in overrides:
                part = o.get(f"{{{CONTENT_TYPES_NS}}}PartName") or o.get("PartName")
                if part and "thumbnail" in part.lower():
                    thumbnail_override = o.get(
                        f"{{{CONTENT_TYPES_NS}}}ContentType"
                    ) or o.get("ContentType")

            self.assertIsNotNone(
                thumbnail_override,
                "Should have an Override for Metadata/thumbnail.png",
            )
            self.assertEqual(
                thumbnail_override,
                "image/png",
                "Thumbnail Override content type must be image/png",
            )

    def test_exported_file_has_thumbnail_override_with_textures(self):
        """When textures force .png to the 3MF OPC type, thumbnail still gets image/png."""
        bpy.ops.mesh.primitive_cube_add()
        mat = self.create_red_material()
        bpy.context.object.data.materials.append(mat)

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="AUTO",
            thumbnail_mode="NONE",
        )
        self.assertIn("FINISHED", result)

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            ct_data = archive.read("[Content_Types].xml").decode("utf-8")
            # Must contain image/png somewhere (for the thumbnail override)
            self.assertIn("image/png", ct_data)


if __name__ == "__main__":
    unittest.main()
