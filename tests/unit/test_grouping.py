"""Unit tests for ``io_mesh_3mf.export_3mf.grouping`` with Blender objects."""

import unittest

from blender_object_test import BlenderObjectTestCase
from io_mesh_3mf.export_3mf.grouping import (
    ASSEMBLY_PROPERTY,
    resolve_assembly_groups,
)


class TestEmptyGroups(BlenderObjectTestCase):
    def test_no_groups(self):
        a, b = self.make_object("a"), self.make_object("b")
        r = resolve_assembly_groups([a, b], [a, b])
        self.assertEqual(r.groups, [])
        self.assertEqual(r.mesh_to_key, {})

    def test_empty_with_children_is_a_group(self):
        e = self.make_object("Rig", "EMPTY")
        a, b = self.make_object("a", parent=e), self.make_object("b", parent=e)
        r = resolve_assembly_groups([e, a, b], [a, b])
        self.assertEqual([g.key for g in r.groups], ["empty:Rig"])
        self.assertIs(r.groups[0].empty, e)
        self.assertEqual(r.mesh_to_key, {"a": "empty:Rig", "b": "empty:Rig"})

    def test_childless_empty_ignored_and_non_mesh_children_ignored(self):
        lone = self.make_object("Lone", "EMPTY")
        e = self.make_object("E", "EMPTY")
        self.make_object("cam", "CAMERA", parent=e)
        r = resolve_assembly_groups([lone, e], [])
        self.assertEqual([g.key for g in r.groups], ["empty:E"])
        self.assertEqual(r.mesh_to_key, {})

    def test_group_order_follows_input(self):
        e1 = self.make_object("B", "EMPTY")
        e2 = self.make_object("A", "EMPTY")
        self.make_object("x", parent=e1)
        self.make_object("y", parent=e2)
        r = resolve_assembly_groups([e1, e2], [])
        self.assertEqual([g.key for g in r.groups], ["empty:B", "empty:A"])

    def test_unexported_parent_empty_does_not_group(self):
        e = self.make_object("Rig", "EMPTY")
        a, b = self.make_object("a", parent=e), self.make_object("b", parent=e)
        r = resolve_assembly_groups([a, b], [a, b])
        self.assertEqual(r.groups, [])
        self.assertEqual(r.mesh_to_key, {})

    def test_nested_empty_under_exported_parent_groups(self):
        root = self.make_object("Root", "EMPTY")
        nested = self.make_object("Nested", "EMPTY", parent=root)
        a = self.make_object("a", parent=nested)
        r = resolve_assembly_groups([root], [a])
        self.assertEqual(r.mesh_to_key, {"a": "empty:Nested"})


class TestPropertyGroups(BlenderObjectTestCase):
    def test_shared_property_forms_group_without_empty(self):
        a = self.make_object("a", props={ASSEMBLY_PROPERTY: "Robot"})
        b = self.make_object("b", props={ASSEMBLY_PROPERTY: "Robot"})
        c = self.make_object("c")
        r = resolve_assembly_groups([a, b, c], [a, b, c])
        self.assertEqual(len(r.groups), 1)
        self.assertEqual(r.groups[0].name, "Robot")
        self.assertIsNone(r.groups[0].empty)
        self.assertEqual(set(r.mesh_to_key), {"a", "b"})

    def test_property_overrides_empty_parent(self):
        e = self.make_object("Rig", "EMPTY")
        a = self.make_object("a", parent=e, props={ASSEMBLY_PROPERTY: "Other"})
        b = self.make_object("b", parent=e)
        r = resolve_assembly_groups([e, a, b], [a, b])
        self.assertEqual(r.mesh_to_key["a"], "prop:Other")
        self.assertEqual(r.mesh_to_key["b"], "empty:Rig")

    def test_property_group_name_may_match_an_empty_name(self):
        a = self.make_object("a", props={ASSEMBLY_PROPERTY: "Rig"})
        e = self.make_object("Rig", "EMPTY")
        self.make_object("b", parent=e)
        r = resolve_assembly_groups([e, a], [a])
        self.assertEqual(len({g.key for g in r.groups}), 2)

    def test_blank_or_non_string_property_ignored(self):
        objs = [
            self.make_object("a", props={ASSEMBLY_PROPERTY: ""}),
            self.make_object("b", props={ASSEMBLY_PROPERTY: "   "}),
            self.make_object("c", props={ASSEMBLY_PROPERTY: 5}),
        ]
        r = resolve_assembly_groups(objs, objs)
        self.assertEqual(r.groups, [])

    def test_property_groups_after_empty_groups(self):
        a = self.make_object("a", props={ASSEMBLY_PROPERTY: "P"})
        e = self.make_object("E", "EMPTY")
        self.make_object("b", parent=e)
        r = resolve_assembly_groups([a, e], [a])
        self.assertEqual([g.key for g in r.groups], ["empty:E", "prop:P"])


if __name__ == "__main__":
    unittest.main()
