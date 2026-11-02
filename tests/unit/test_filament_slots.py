"""
Unit tests for io_mesh_3mf.export_3mf.filament_slots.FilamentSlotTable.

Runs inside Blender's Python environment (real bpy available — no mocks needed).
"""

import unittest

from io_mesh_3mf.export_3mf.filament_slots import FilamentSlotTable

FIXTURE_COLOURS = [
    "#FFBBDD", "#000000", "#EC008C", "#00C1AE", "#00C1AE",
    "#6A5F8B", "#680450", "#874386", "#CB2291", "#BA77BD",
]
FIXTURE_MIXED = [False] * 5 + [True] * 5


class TestFilamentSlotTable(unittest.TestCase):
    def table(self):
        return FilamentSlotTable(FIXTURE_COLOURS, FIXTURE_MIXED)

    def test_base_table_keeps_every_slot(self):
        t = self.table()
        self.assertEqual(t.colors, FIXTURE_COLOURS)
        self.assertEqual(t.sources, list(range(10)))
        self.assertTrue(t.is_mixed(6))
        self.assertFalse(t.is_mixed(5))
        self.assertFalse(t.is_mixed(11))

    def test_explicit_slots_with_same_hex_stay_distinct(self):
        t = self.table()
        self.assertEqual(t.assign_slot(4, "#00c1ae"), 4)
        self.assertEqual(t.assign_slot(5, "#00C1AE"), 5)
        self.assertEqual(len(t), 10)

    def test_first_claim_recolours_physical_slot(self):
        t = self.table()
        self.assertEqual(t.assign_slot(2, "#112233"), 2)
        self.assertEqual(t.colors[1], "#112233")
        self.assertEqual(t.sources[1], 1)

    def test_mixed_slot_keeps_stored_colour(self):
        t = self.table()
        self.assertEqual(t.assign_slot(7, "#FFFFFF"), 7)
        self.assertEqual(t.colors[6], "#680450")

    def test_conflicting_claim_falls_back_to_colour(self):
        t = self.table()
        t.assign_slot(2, "#000000")
        self.assertEqual(t.assign_slot(2, "#FFBBDD"), 1)
        self.assertEqual(t.assign_slot(2, "#123456"), 11)
        self.assertIsNone(t.sources[10])

    def test_out_of_range_slot_without_extend_matches_colour(self):
        t = self.table()
        self.assertEqual(t.assign_slot(40, "#EC008C"), 3)

    def test_extend_creates_palette_slots(self):
        t = FilamentSlotTable()
        self.assertEqual(t.assign_slot(1, "#FF0000", extend=True), 1)
        self.assertEqual(t.assign_slot(3, "#0000FF", extend=True), 3)
        self.assertEqual(len(t), 3)
        self.assertEqual(t.colors[2], "#0000FF")

    def test_assign_colour_never_reuses_mixed_slot(self):
        t = self.table()
        self.assertEqual(t.assign_color("#680450"), 11)

    def test_assign_colour_reuses_unclaimed_physical(self):
        t = self.table()
        self.assertEqual(t.assign_color("#00C1AE"), 4)
        self.assertEqual(t.assign_color("#00C1AE"), 4)

    def test_assign_colour_on_empty_table_appends_in_order(self):
        t = FilamentSlotTable()
        self.assertEqual(t.assign_color("#AA0000"), 1)
        self.assertEqual(t.assign_color("#00AA00"), 2)
        self.assertEqual(t.assign_color("#aa0000"), 1)
        self.assertEqual(t.colors, ["#AA0000", "#00AA00"])

    def test_colour_lookup_prefers_physical_slots(self):
        t = FilamentSlotTable(["#111111", "#222222", "#111111"], [True, False, False])
        mapping = t.color_to_slot()
        self.assertEqual(mapping["#111111"], 3)
        self.assertEqual(mapping["#222222"], 2)

    def test_short_mixed_flags_are_padded(self):
        t = FilamentSlotTable(["#111111", "#222222"], [True])
        self.assertEqual(t.mixed, [True, False])


if __name__ == "__main__":
    unittest.main()
