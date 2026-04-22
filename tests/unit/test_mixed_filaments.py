"""
Unit tests for io_mesh_3mf.common.mixed_filaments parse/serialize round-trips.

Runs inside Blender's Python environment (real bpy available — no mocks needed).
"""

import unittest

from io_mesh_3mf.common.mixed_filaments import (
    parse_mixed_filament_definitions,
    serialize_mixed_filament_definitions,
)


class TestMixedFilamentsRoundTrip(unittest.TestCase):

    # ------------------------------------------------------------------
    # Normalisation of old-style rows (no z/xa/xb in source)
    # ------------------------------------------------------------------

    def test_old_style_row_fields(self):
        """Parse a row lacking z/xa/xb tokens and check all fields."""
        old_row = "1,2,0,0,50,0,g,w,m2,d1,o1,u1"
        entries = parse_mixed_filament_definitions(old_row)
        self.assertEqual(len(entries), 1)
        mf = entries[0]
        self.assertEqual(mf.component_a, 1)
        self.assertEqual(mf.component_b, 2)
        self.assertFalse(mf.enabled)      # deleted forces enabled=False
        self.assertFalse(mf.custom)
        self.assertEqual(mf.mix_b_percent, 50)
        self.assertTrue(mf.deleted)
        self.assertTrue(mf.origin_auto)
        self.assertEqual(mf.stable_id, 1)
        self.assertEqual(mf.distribution_mode, 2)

    def test_old_style_row_normalises_serialization(self):
        """Old-style row should serialise with z/xa/xb tokens added."""
        entries = parse_mixed_filament_definitions("1,2,0,0,50,0,g,w,m2,d1,o1,u1")
        got = serialize_mixed_filament_definitions(entries)
        self.assertEqual(got, "1,2,0,0,50,0,g,w,m2,z0,xa0.0,xb0.0,d1,o1,u1")

    # ------------------------------------------------------------------
    # Full-field rows — exact round-trip expected
    # ------------------------------------------------------------------

    def _assert_exact_roundtrip(self, row):
        entries = parse_mixed_filament_definitions(row)
        self.assertEqual(serialize_mixed_filament_definitions(entries), row)

    def test_exact_roundtrip_enabled_row(self):
        self._assert_exact_roundtrip("1,2,1,1,50,1,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u3")

    def test_exact_roundtrip_manual_pattern(self):
        self._assert_exact_roundtrip("1,2,1,1,33,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u19,123")

    def test_exact_roundtrip_deleted_full(self):
        self._assert_exact_roundtrip("2,3,0,0,50,0,g,w,m2,z0,xa0.0,xb0.0,d1,o1,u4")

    def test_exact_roundtrip_gradient_ids_and_weights(self):
        self._assert_exact_roundtrip("1,3,1,1,30,0,g123,w50/25/25,m0,z0,xa0.0,xb0.0,d0,o0,u99")

    def test_exact_roundtrip_pattern_with_perimeter_groups(self):
        self._assert_exact_roundtrip("1,2,1,1,0,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u44,14343434")

    # ------------------------------------------------------------------
    # Multi-row round-trip
    # ------------------------------------------------------------------

    def test_multi_row_count(self):
        multi = (
            "1,2,1,1,50,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u1;"
            "2,3,1,1,30,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u2,123"
        )
        entries = parse_mixed_filament_definitions(multi)
        self.assertEqual(len(entries), 2)

    def test_multi_row_exact_roundtrip(self):
        multi = (
            "1,2,1,1,50,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u1;"
            "2,3,1,1,30,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u2,123"
        )
        entries = parse_mixed_filament_definitions(multi)
        self.assertEqual(serialize_mixed_filament_definitions(entries), multi)

    # ------------------------------------------------------------------
    # Legacy 4-token format
    # ------------------------------------------------------------------

    def test_legacy_4_token_parses(self):
        legacy = parse_mixed_filament_definitions("1,2,1,50")
        self.assertEqual(len(legacy), 1)
        self.assertTrue(legacy[0].custom)
        self.assertEqual(legacy[0].mix_b_percent, 50)

    # ------------------------------------------------------------------
    # Invalid rows are skipped
    # ------------------------------------------------------------------

    def test_invalid_pair_skipped(self):
        """A row with component_a == component_b should be discarded."""
        data = (
            "0,0,1,1,50,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u1;"
            "1,2,1,1,50,0,g,w,m2,z0,xa0.0,xb0.0,d0,o0,u99"
        )
        entries = parse_mixed_filament_definitions(data)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].stable_id, 99)


if __name__ == "__main__":
    unittest.main()
