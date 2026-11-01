"""Unit tests for ``io_mesh_3mf.export_3mf.material_plates``."""

import unittest

from blender_object_test import BlenderObjectTestCase
from io_mesh_3mf.export_3mf.material_plates import plan_material_plates
from io_mesh_3mf.export_3mf.plate_model import MAX_PLATES, explicit_unit_plate, layout_plates


class TestPlanMaterialPlates(unittest.TestCase):
    def test_one_plate_per_material_in_first_seen_order(self):
        units = [("#f00", "Red"), ("#00f", "Blue"), ("#f00", "Red"), ("#0f0", "Green")]
        plates, names = plan_material_plates(units, [None] * 4)
        self.assertEqual(plates, [1, 2, 1, 3])
        self.assertEqual(names, {1: "Red", 2: "Blue", 3: "Green"})

    def test_plates_are_global_across_assemblies(self):
        # two assemblies, each with red and blue parts -> still just two plates
        units = [("#f00", "Red"), ("#00f", "Blue"), ("#f00", "Red"), ("#00f", "Blue")]
        plates, _ = plan_material_plates(units, [None] * 4)
        self.assertEqual(sorted(set(plates)), [1, 2])

    def test_explicit_request_wins(self):
        units = [("#f00", "Red"), ("#00f", "Blue")]
        plates, names = plan_material_plates(units, [None, 5])
        self.assertEqual(plates, [1, 5])
        self.assertEqual(names, {1: "Red", 5: "Blue"})

    def test_auto_plates_do_not_collide_with_other_explicit_materials(self):
        units = [("#f00", "Red"), ("#00f", "Blue"), ("#f00", "Red")]
        plates, names = plan_material_plates(units, [2, None, None])
        self.assertEqual(plates, [2, 1, 2])
        self.assertEqual(names, {1: "Blue", 2: "Red"})

    def test_first_explicit_plate_is_used_by_unassigned_units_of_same_material(self):
        units = [("#f00", "Red"), ("#f00", "Red"), ("#f00", "Red")]
        plates, _ = plan_material_plates(units, [None, 4, 5])
        self.assertEqual(plates, [4, 4, 5])

    def test_no_material_shares_one_named_plate(self):
        plates, names = plan_material_plates([(None, None), ("#f00", "Red"), (None, None)], [None] * 3)
        self.assertEqual(plates, [1, 2, 1])
        self.assertEqual(names[1], "No material")

    def test_mixed_assembly_gets_its_own_plate(self):
        units = [(("mixed", "Kit"), "Kit"), ("#f00", "Red")]
        plates, names = plan_material_plates(units, [None, None])
        self.assertEqual(plates, [1, 2])
        self.assertEqual(names[1], "Kit")

    def test_clamped_to_max_plates(self):
        units = [(f"#{i:06x}", f"M{i}") for i in range(MAX_PLATES + 4)]
        plates, _ = plan_material_plates(units, [None] * len(units))
        self.assertEqual(max(plates), MAX_PLATES)

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            plan_material_plates([("#f00", "Red")], [])

    def test_empty(self):
        self.assertEqual(plan_material_plates([], []), ([], {}))


class TestExplicitUnitPlate(BlenderObjectTestCase):
    def test_none_when_unset(self):
        self.assertEqual(explicit_unit_plate(None, [self.make_object("unset")]), (None, False))

    def test_empty_wins(self):
        empty = self.make_object("empty", "EMPTY", props={"3mf_plate": 3})
        member = self.make_object("member", props={"3mf_plate": 2})
        self.assertEqual(
            explicit_unit_plate(empty, [member]),
            (3, False),
        )


class TestPlateNames(unittest.TestCase):
    def test_names_applied_to_layout(self):
        plates = layout_plates(
            [], [{"wrapper_id": 2, "plate": 1}, {"wrapper_id": 4, "plate": 2}],
            {1: "Red", 2: "Blue"},
        )
        self.assertEqual([p.name for p in plates], ["Red", "Blue"])

    def test_no_names_means_empty_names(self):
        plates = layout_plates([], [{"wrapper_id": 2}])
        self.assertEqual(plates[0].name, "")


if __name__ == "__main__":
    unittest.main()
