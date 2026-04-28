"""
Integration test for component/instance support in 3MF export/import.

Tests that linked duplicates (Alt+D in Blender) are exported as component instances
and imported back as linked duplicates.
"""

import unittest
import os
import tempfile
import zipfile
import xml.etree.ElementTree as ET

try:
    import bpy
except ImportError:
    bpy = None


@unittest.skipIf(bpy is None, "Blender API not available")
class TestComponents(unittest.TestCase):
    """Test component/instance export and import."""

    def setUp(self):
        """Set up test scene with linked duplicates."""
        # Clear the scene
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        # Create a base cube
        bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
        self.base_cube = bpy.context.active_object
        self.base_cube.name = "Cube_Original"

        # Create linked duplicates (Alt+D)
        # These should share mesh data
        bpy.ops.object.duplicate_move_linked(
            OBJECT_OT_duplicate={"linked": True},
            TRANSFORM_OT_translate={"value": (3, 0, 0)},
        )
        self.linked_cube1 = bpy.context.active_object
        self.linked_cube1.name = "Cube_Instance_1"

        bpy.ops.object.duplicate_move_linked(
            OBJECT_OT_duplicate={"linked": True},
            TRANSFORM_OT_translate={"value": (3, 0, 0)},
        )
        self.linked_cube2 = bpy.context.active_object
        self.linked_cube2.name = "Cube_Instance_2"

        # Verify they share mesh data
        self.assertEqual(
            self.base_cube.data,
            self.linked_cube1.data,
            "Linked duplicates should share mesh data",
        )
        self.assertEqual(
            self.base_cube.data,
            self.linked_cube2.data,
            "Linked duplicates should share mesh data",
        )

        # Create temp file for export
        self.temp_file = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
        self.temp_file.close()

    def tearDown(self):
        """Clean up temp file."""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)

    def test_component_export_structure(self):
        """Test that linked duplicates are exported as component references."""
        # Export with component optimization enabled
        bpy.ops.export_mesh.threemf(
            filepath=self.temp_file.name, use_components=True, use_selection=False
        )

        # Verify the 3MF structure
        with zipfile.ZipFile(self.temp_file.name, "r") as archive:
            # Read the main model file
            with archive.open("3D/3dmodel.model") as model_file:
                tree = ET.parse(model_file)
                root = tree.getroot()

                # Find namespace
                ns = {
                    "3mf": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
                }

                # Find all objects in resources
                resources = root.find(".//3mf:resources", ns)
                objects = resources.findall(".//3mf:object", ns)

                # Should have:
                # 1. Component definition (the shared mesh)
                # 2. Instance container 1 (references component)
                # 3. Instance container 2 (references component)
                self.assertGreaterEqual(
                    len(objects),
                    3,
                    "Should have at least 3 objects: 1 component definition + 2 instances",
                )

                # Find component definition (object with mesh element)
                component_def = None
                instance_containers = []

                for obj in objects:
                    mesh_elem = obj.find(".//3mf:mesh", ns)
                    components_elem = obj.find(".//3mf:components", ns)

                    if mesh_elem is not None and components_elem is None:
                        # This is a component definition (has mesh, no components)
                        component_def = obj
                    elif components_elem is not None:
                        # This is an instance container (has component reference)
                        instance_containers.append(obj)

                self.assertIsNotNone(
                    component_def, "Should have a component definition with mesh data"
                )
                self.assertEqual(
                    len(instance_containers),
                    3,
                    "Should have 3 instance containers (including original)",
                )

                # Verify instances reference the same component
                component_id = component_def.get("id")
                for container in instance_containers:
                    components = container.find(".//3mf:components", ns)
                    component = components.find(".//3mf:component", ns)
                    referenced_id = component.get("objectid")
                    self.assertEqual(
                        referenced_id,
                        component_id,
                        f"Instance should reference component {component_id}",
                    )

    def test_component_import_as_linked(self):
        """Test that component instances are imported as linked duplicates."""
        # Export with components
        bpy.ops.export_mesh.threemf(
            filepath=self.temp_file.name, use_components=True, use_selection=False
        )

        # Clear scene
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()

        # Import
        bpy.ops.import_mesh.threemf(filepath=self.temp_file.name)

        # Get imported objects
        imported_objects = [obj for obj in bpy.data.objects if obj.type == "MESH"]
        self.assertEqual(len(imported_objects), 3, "Should have imported 3 objects")

        # Verify they share mesh data (linked duplicates)
        mesh_data = imported_objects[0].data
        for obj in imported_objects[1:]:
            self.assertEqual(
                obj.data,
                mesh_data,
                f"Object {obj.name} should share mesh data with {imported_objects[0].name}",
            )

    def test_component_export_disabled(self):
        """Test that with use_components=False, objects are exported normally."""
        # Export with component optimization disabled
        bpy.ops.export_mesh.threemf(
            filepath=self.temp_file.name, use_components=False, use_selection=False
        )

        # Verify the 3MF structure - each object should have its own mesh
        with zipfile.ZipFile(self.temp_file.name, "r") as archive:
            with archive.open("3D/3dmodel.model") as model_file:
                tree = ET.parse(model_file)
                root = tree.getroot()

                ns = {
                    "3mf": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
                }
                resources = root.find(".//3mf:resources", ns)
                objects = resources.findall(".//3mf:object", ns)

                # Each object should have mesh data inline (no component references)
                mesh_objects = 0
                for obj in objects:
                    mesh_elem = obj.find(".//3mf:mesh", ns)
                    if mesh_elem is not None:
                        mesh_objects += 1

                self.assertEqual(
                    mesh_objects,
                    3,
                    "All 3 objects should have inline mesh data (no components)",
                )

    def test_single_instance_not_optimized(self):
        """Test that single instances are not unnecessarily optimized as components."""
        # Clear scene and create a single object
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        bpy.ops.mesh.primitive_cube_add()

        # Export
        bpy.ops.export_mesh.threemf(
            filepath=self.temp_file.name, use_components=True, use_selection=False
        )

        # Verify no component structure (single object exported inline)
        with zipfile.ZipFile(self.temp_file.name, "r") as archive:
            with archive.open("3D/3dmodel.model") as model_file:
                tree = ET.parse(model_file)
                root = tree.getroot()

                ns = {
                    "3mf": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
                }
                resources = root.find(".//3mf:resources", ns)
                objects = resources.findall(".//3mf:object", ns)

                # Should have exactly 1 object with mesh
                self.assertEqual(len(objects), 1, "Should have 1 object")

                mesh_elem = objects[0].find(".//3mf:mesh", ns)
                components_elem = objects[0].find(".//3mf:components", ns)

                self.assertIsNotNone(mesh_elem, "Object should have mesh")
                self.assertIsNone(
                    components_elem, "Single object should not use components"
                )


if __name__ == "__main__":
    unittest.main()
