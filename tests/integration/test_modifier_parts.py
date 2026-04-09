"""
Integration tests for Orca/BambuStudio modifier part subtypes.

Covers:
- Import of 3MF files with modifier_part entries in model_settings.config
- Viewport materials applied to non-normal subtypes
- Parent-child hierarchy (Empty parent for multi-part assemblies)
- Export preserves subtype from 3mf_part_subtype custom property
- All 5 subtypes: normal_part, modifier_part, support_enforcer,
  support_blocker, negative_part
- Round-trip: import → export preserves modifier subtypes and hierarchy
- API inspect_3mf returns part_subtypes
"""

import bpy
import unittest
import zipfile
import xml.etree.ElementTree as ET
from test_base import Blender3mfTestCase


class ModifierPartImportTests(Blender3mfTestCase):
    """Verify modifier part subtypes are imported from model_settings.config."""

    def test_import_modifier_part_subtype(self):
        """Importing a 3MF with modifier_part sets 3mf_part_subtype on the object."""
        test_file = self.test_resources_dir / "modifierGroup.3mf"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        result = bpy.ops.import_mesh.threemf(filepath=str(test_file))
        self.assertIn("FINISHED", result)

        modifier_objs = [
            obj for obj in bpy.data.objects
            if obj.get("3mf_part_subtype") == "modifier_part"
        ]
        self.assertGreater(
            len(modifier_objs), 0,
            "Expected at least one object with 3mf_part_subtype='modifier_part'",
        )

    def test_import_normal_part_has_no_property(self):
        """Normal parts should not have the 3mf_part_subtype property set."""
        test_file = self.test_resources_dir / "modifierGroup.3mf"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        bpy.ops.import_mesh.threemf(filepath=str(test_file))

        normal_objs = [
            obj for obj in bpy.data.objects
            if obj.type == "MESH" and "3mf_part_subtype" not in obj
        ]
        self.assertGreater(
            len(normal_objs), 0,
            "Expected at least one object without 3mf_part_subtype (normal part)",
        )

    def test_import_creates_parent_empty(self):
        """Multi-part assemblies should create a parent Empty for hierarchy."""
        test_file = self.test_resources_dir / "modifierGroup.3mf"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        bpy.ops.import_mesh.threemf(filepath=str(test_file))

        empties = [obj for obj in bpy.data.objects if obj.type == "EMPTY"]
        self.assertGreater(
            len(empties), 0,
            "Expected a parent Empty for the multi-part assembly",
        )

    def test_import_modifier_parented_to_empty(self):
        """The modifier mesh should be a child of the parent Empty."""
        test_file = self.test_resources_dir / "modifierGroup.3mf"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        bpy.ops.import_mesh.threemf(filepath=str(test_file))

        modifier_objs = [
            obj for obj in bpy.data.objects
            if obj.get("3mf_part_subtype") == "modifier_part"
        ]
        self.assertGreater(len(modifier_objs), 0)
        for obj in modifier_objs:
            self.assertIsNotNone(
                obj.parent,
                f"Modifier '{obj.name}' should have a parent Empty",
            )
            self.assertEqual(
                obj.parent.type, "EMPTY",
                f"Modifier '{obj.name}' parent should be an Empty, got {obj.parent.type}",
            )

    def test_import_modifier_has_viewport_material(self):
        """Modifier parts should get a viewport display material."""
        test_file = self.test_resources_dir / "modifierGroup.3mf"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        bpy.ops.import_mesh.threemf(filepath=str(test_file))

        modifier_objs = [
            obj for obj in bpy.data.objects
            if obj.get("3mf_part_subtype") == "modifier_part"
        ]
        self.assertGreater(len(modifier_objs), 0)
        for obj in modifier_objs:
            self.assertGreater(
                len(obj.data.materials), 0,
                f"Modifier '{obj.name}' should have a viewport material",
            )
            mat = obj.data.materials[0]
            self.assertIn("3MF", mat.name, "Material should be named '3MF ...'")


class ModifierPartAllSubtypesTests(Blender3mfTestCase):
    """Test all 5 subtypes using CubesWithAll5Modifiers.3mf."""

    def _import_all5(self):
        test_file = self.test_resources_dir / "CubesWithAll5Modifiers.3mf"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")
        result = bpy.ops.import_mesh.threemf(filepath=str(test_file))
        self.assertIn("FINISHED", result)
        return result

    def test_import_all_subtypes_present(self):
        """File with all 5 types imports each non-normal subtype correctly."""
        self._import_all5()

        found = set()
        for obj in bpy.data.objects:
            subtype = obj.get("3mf_part_subtype")
            if subtype:
                found.add(subtype)

        for expected in ("modifier_part", "support_enforcer", "support_blocker", "negative_part"):
            self.assertIn(
                expected, found,
                f"Expected subtype '{expected}' in imported objects, found: {found}",
            )

    def test_import_creates_5_parent_empties(self):
        """Each wrapper object should produce a parent Empty (5 total)."""
        self._import_all5()
        empties = [obj for obj in bpy.data.objects if obj.type == "EMPTY"]
        self.assertEqual(
            len(empties), 5,
            f"Expected 5 parent Empties, got {len(empties)}",
        )

    def test_each_subtype_has_material(self):
        """Each non-normal modifier object should have a viewport material."""
        self._import_all5()
        for subtype in ("modifier_part", "support_enforcer", "support_blocker", "negative_part"):
            objs = [
                obj for obj in bpy.data.objects
                if obj.get("3mf_part_subtype") == subtype
            ]
            self.assertGreater(len(objs), 0, f"No objects with subtype {subtype}")
            for obj in objs:
                self.assertGreater(
                    len(obj.data.materials), 0,
                    f"Object with subtype '{subtype}' should have a material",
                )

    def test_no_subtype_collision(self):
        """Part IDs reused across wrappers should not collide."""
        self._import_all5()
        # Each type should appear exactly once (one non-normal per group)
        type_counts = {}
        for obj in bpy.data.objects:
            subtype = obj.get("3mf_part_subtype")
            if subtype:
                type_counts[subtype] = type_counts.get(subtype, 0) + 1

        for subtype in ("modifier_part", "support_enforcer", "support_blocker", "negative_part"):
            self.assertEqual(
                type_counts.get(subtype, 0), 1,
                f"Expected exactly 1 '{subtype}', got {type_counts.get(subtype, 0)}",
            )


class ModifierPartExportTests(Blender3mfTestCase):
    """Verify modifier part subtype is written to model_settings.config."""

    def _export_with_modifier(self, subtype="modifier_part"):
        """Create a cube + modifier child and export in Orca mode."""
        # Create parent empty
        bpy.ops.object.empty_add(location=(0, 0, 0))
        parent = bpy.context.object
        parent.name = "Group"

        # Create main cube
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        cube.name = "MainPart"
        cube.parent = parent
        mat_a = bpy.data.materials.new("Mat_A")
        mat_a.use_nodes = True
        cube.data.materials.append(mat_a)

        # Create modifier cylinder
        bpy.ops.mesh.primitive_cylinder_add(location=(1, 0, 0))
        modifier = bpy.context.object
        modifier.name = "ModifierMesh"
        modifier.parent = parent
        modifier["3mf_part_subtype"] = subtype
        mat_b = bpy.data.materials.new("Mat_B")
        mat_b.use_nodes = True
        modifier.data.materials.append(mat_b)

        # Select all for export
        bpy.ops.object.select_all(action="SELECT")

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="AUTO",
        )
        self.assertIn("FINISHED", result)
        return self.temp_file

    def test_export_modifier_subtype_in_config(self):
        """Export writes correct subtype in model_settings.config."""
        self._export_with_modifier("modifier_part")

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            config_data = archive.read("Metadata/model_settings.config")
            root = ET.fromstring(config_data)

            subtypes = []
            for part in root.findall(".//part"):
                subtypes.append(part.get("subtype", ""))

            self.assertIn(
                "modifier_part", subtypes,
                f"Expected 'modifier_part' in config subtypes, got {subtypes}",
            )

    def test_export_normal_part_default(self):
        """Objects without 3mf_part_subtype export as 'normal_part'."""
        # Create a simple cube without the property
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        mat = bpy.data.materials.new("SimpleMat")
        mat.use_nodes = True
        bpy.context.object.data.materials.append(mat)

        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="AUTO",
        )

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            config_data = archive.read("Metadata/model_settings.config")
            root = ET.fromstring(config_data)

            for part in root.findall(".//part"):
                self.assertEqual(
                    part.get("subtype"), "normal_part",
                    "Parts without property should be 'normal_part'",
                )

    def test_export_support_enforcer_subtype(self):
        """Export writes 'support_enforcer' subtype correctly."""
        self._export_with_modifier("support_enforcer")

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            config_data = archive.read("Metadata/model_settings.config")
            root = ET.fromstring(config_data)

            subtypes = [p.get("subtype", "") for p in root.findall(".//part")]
            self.assertIn("support_enforcer", subtypes)

    def test_export_support_blocker_subtype(self):
        """Export writes 'support_blocker' subtype correctly."""
        self._export_with_modifier("support_blocker")

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            config_data = archive.read("Metadata/model_settings.config")
            root = ET.fromstring(config_data)

            subtypes = [p.get("subtype", "") for p in root.findall(".//part")]
            self.assertIn("support_blocker", subtypes)

    def test_export_negative_part_subtype(self):
        """Export writes 'negative_part' subtype correctly."""
        self._export_with_modifier("negative_part")

        with zipfile.ZipFile(str(self.temp_file), "r") as archive:
            config_data = archive.read("Metadata/model_settings.config")
            root = ET.fromstring(config_data)

            subtypes = [p.get("subtype", "") for p in root.findall(".//part")]
            self.assertIn("negative_part", subtypes)


class ModifierPartRoundtripTests(Blender3mfTestCase):
    """Modifier part round-trip tests."""

    def test_roundtrip_modifier_subtype(self):
        """Export with modifier_part → import preserves the subtype and hierarchy."""
        # Create parent empty
        bpy.ops.object.empty_add(location=(0, 0, 0))
        parent = bpy.context.object
        parent.name = "RTGroup"

        # Create main part
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        cube = bpy.context.object
        cube.parent = parent
        mat = bpy.data.materials.new("RT_A")
        mat.use_nodes = True
        cube.data.materials.append(mat)

        # Create modifier part
        bpy.ops.mesh.primitive_cylinder_add(location=(1, 0, 0))
        modifier = bpy.context.object
        modifier.parent = parent
        modifier["3mf_part_subtype"] = "modifier_part"
        mat2 = bpy.data.materials.new("RT_B")
        mat2.use_nodes = True
        modifier.data.materials.append(mat2)

        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file), use_orca_format="AUTO"
        )
        self.clean_scene()

        bpy.ops.import_mesh.threemf(filepath=str(self.temp_file))

        modifier_objs = [
            obj for obj in bpy.data.objects
            if obj.get("3mf_part_subtype") == "modifier_part"
        ]
        self.assertGreater(
            len(modifier_objs), 0,
            "Round-trip should preserve modifier_part subtype",
        )

        # Verify hierarchy is preserved — modifier should have a parent
        for obj in modifier_objs:
            self.assertIsNotNone(
                obj.parent,
                "Round-trip should preserve parent-child hierarchy",
            )


class ModifierPartInspectTests(Blender3mfTestCase):
    """Verify inspect_3mf reports part subtypes."""

    def test_inspect_reports_modifier_subtypes(self):
        """inspect_3mf returns part_subtypes for Orca files with modifiers."""
        test_file = self.test_resources_dir / "modifierGroup.3mf"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        from io_mesh_3mf.api import inspect_3mf

        info = inspect_3mf(str(test_file))
        self.assertEqual(info.status, "OK")
        self.assertGreater(
            len(info.part_subtypes), 0,
            "Expected part_subtypes from modifierGroup.3mf",
        )

        # Should have at least one modifier_part entry
        modifier_entries = [
            p for p in info.part_subtypes if p["subtype"] == "modifier_part"
        ]
        self.assertGreater(
            len(modifier_entries), 0,
            "Expected at least one modifier_part in inspect result",
        )

    def test_inspect_modifier_has_name(self):
        """Modifier part entries include the part name."""
        test_file = self.test_resources_dir / "modifierGroup.3mf"
        if not test_file.exists():
            self.skipTest(f"Test file not found: {test_file}")

        from io_mesh_3mf.api import inspect_3mf

        info = inspect_3mf(str(test_file))
        modifier_entries = [
            p for p in info.part_subtypes if p["subtype"] == "modifier_part"
        ]
        if modifier_entries:
            self.assertIn("name", modifier_entries[0])
            self.assertTrue(
                modifier_entries[0]["name"],
                "Modifier part should have a name",
            )


if __name__ == "__main__":
    unittest.main()
