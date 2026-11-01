"""Unit tests for ``io_mesh_3mf.export_3mf.plate_model``."""

import unittest

from blender_object_test import BlenderObjectTestCase
from io_mesh_3mf.export_3mf.plate_model import (
    MAX_PLATES,
    PLATE_PROPERTY,
    PlateInstance,
    PlateLayout,
    layout_plates,
    plate_property,
    resolve_unit_plate,
    single_plate_layout,
)


class TestSinglePlateLayout(unittest.TestCase):
    def test_one_plate_groups_then_ungrouped(self):
        groups = [{"wrapper_id": 7}, {"wrapper_id": 8}]
        ungrouped = [{"wrapper_id": 2}, {"wrapper_id": 4}]
        plates = single_plate_layout(groups, ungrouped)
        self.assertEqual(len(plates), 1)
        self.assertEqual(plates[0].plater_id, 1)
        self.assertEqual(plates[0].name, "")
        self.assertEqual([i.object_id for i in plates[0].instances], [7, 8, 2, 4])

    def test_identify_id_matches_wrapper_id_and_instance_zero(self):
        plates = single_plate_layout([], [{"wrapper_id": 6}])
        inst = plates[0].instances[0]
        self.assertEqual((inst.object_id, inst.instance_id, inst.identify_id), (6, 0, 6))

    def test_empty_input_still_yields_one_plate(self):
        plates = single_plate_layout([], [])
        self.assertEqual(len(plates), 1)
        self.assertEqual(plates[0].instances, [])

    def test_plater_id_is_index_plus_one(self):
        self.assertEqual(PlateLayout(index=4).plater_id, 5)
        self.assertEqual(PlateInstance(object_id=3).instance_id, 0)


class TestPlateProperty(BlenderObjectTestCase):
    def test_valid_values(self):
        self.assertEqual(plate_property(self.make_object("int", props={PLATE_PROPERTY: 3})), 3)
        self.assertEqual(plate_property(self.make_object("float", props={PLATE_PROPERTY: 2.0})), 2)

    def test_invalid_values_are_unset(self):
        for bad in (0, -1, 1.5, "2", True):
            with self.subTest(value=bad):
                obj = self.make_object("invalid", props={PLATE_PROPERTY: bad})
                self.assertIsNone(plate_property(obj))
        self.assertIsNone(plate_property(self.make_object("unset")))

    def test_clamped_to_max(self):
        obj = self.make_object("large", props={PLATE_PROPERTY: 999})
        self.assertEqual(plate_property(obj), MAX_PLATES)


class TestResolveUnitPlate(BlenderObjectTestCase):
    def test_default_is_plate_one(self):
        self.assertEqual(resolve_unit_plate(None, [self.make_object("unset")]), (1, False))
        self.assertEqual(resolve_unit_plate(None, []), (1, False))

    def test_empty_property_wins_over_members(self):
        empty = self.make_object("empty", "EMPTY", props={PLATE_PROPERTY: 4})
        members = [self.make_object("member", props={PLATE_PROPERTY: 2})]
        self.assertEqual(resolve_unit_plate(empty, members), (4, False))

    def test_first_member_used_and_conflict_flagged(self):
        members = [
            self.make_object("unset"),
            self.make_object("first", props={PLATE_PROPERTY: 2}),
            self.make_object("second", props={PLATE_PROPERTY: 3}),
        ]
        self.assertEqual(resolve_unit_plate(None, members), (2, True))

    def test_agreeing_members_are_not_a_conflict(self):
        members = [
            self.make_object("first", props={PLATE_PROPERTY: 2}),
            self.make_object("second", props={PLATE_PROPERTY: 2}),
        ]
        self.assertEqual(resolve_unit_plate(None, members), (2, False))


class TestLayoutPlates(unittest.TestCase):
    def test_units_distributed_in_order(self):
        groups = [{"wrapper_id": 9, "plate": 2}]
        ungrouped = [
            {"wrapper_id": 2, "plate": 1},
            {"wrapper_id": 4, "plate": 2},
            {"wrapper_id": 6},
        ]
        plates = layout_plates(groups, ungrouped)
        self.assertEqual([p.plater_id for p in plates], [1, 2])
        self.assertEqual([i.object_id for i in plates[0].instances], [2, 6])
        self.assertEqual([i.object_id for i in plates[1].instances], [9, 4])

    def test_gap_creates_empty_plate(self):
        plates = layout_plates([], [{"wrapper_id": 2, "plate": 1}, {"wrapper_id": 4, "plate": 3}])
        self.assertEqual([p.plater_id for p in plates], [1, 2, 3])
        self.assertEqual(plates[1].instances, [])

    def test_only_later_plate_still_creates_plate_one(self):
        plates = layout_plates([], [{"wrapper_id": 2, "plate": 2}])
        self.assertEqual(len(plates), 2)
        self.assertEqual(plates[0].instances, [])


if __name__ == "__main__":
    unittest.main()
