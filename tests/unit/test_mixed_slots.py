"""
Unit tests for io_mesh_3mf.common.mixed_slots (OrcaSlicer per-slot mixed filaments)
and the 32-state segmentation escape encoding.

Runs inside Blender's Python environment (real bpy available — no mocks needed).
"""

import copy
import math
import unittest

from io_mesh_3mf.common.mixed_slots import (
    CurvePoint,
    MIXED_KEYS,
    check_type_consistency,
    find_fixed_mixed_slot,
    format_components,
    format_ratios,
    mixed_slot_flags,
    parse_components,
    parse_gradient_curve,
    parse_mixed_slots,
    parse_ratios,
    remap_components_on_delete,
    resize_slot_arrays,
    sample_gradient_curve,
    sanitize_gradient_curve_array,
    serialize_gradient_curve,
    strip_fullspectrum_keys,
    validate_mixed_filament_params,
    write_mixed_slots,
)
from io_mesh_3mf.common.segmentation import (
    SegmentationEncoder,
    SegmentationNode,
    TriangleState,
    decode_segmentation_string,
)
from io_mesh_3mf.import_3mf.segmentation import _get_leaf_state
from io_mesh_3mf.import_3mf.slicer.paint import ORCA_PAINT_TO_INDEX
from io_mesh_3mf.export_3mf.materials.base import ORCA_FILAMENT_CODES


def fixture_settings():
    """Mixed arrays from tests/resources/MixedFilamentSupport.3mf (OrcaSlicer 2.4)."""
    return {
        "filament_colour": ["#FFBBDD", "#000000", "#EC008C", "#00C1AE", "#00C1AE",
                            "#6A5F8B", "#680450", "#874386", "#CB2291", "#BA77BD"],
        "filament_type": ["PLA"] * 10,
        "filament_is_mixed": ["0", "0", "0", "0", "0", "1", "1", "1", "1", "1"],
        "filament_mixed_components": ["", "", "", "", "", "1,2", "2,3", "1,2,3", "1,2,3", "3,5,1"],
        "filament_mixed_sublayer_ratios": ["", "", "", "", "", "0.5000,0.5000", "0.5000,0.5000",
                                           "0.4000,0.4000,0.2000", "0.2600,0.1500,0.5900",
                                           "0.2500,0.2500,0.5000"],
        "filament_mixed_gradient": ["0", "0", "0", "0", "0", "1", "0", "0", "0", "0"],
        "filament_mixed_gradient_range": ["", "", "", "", "", "0.9000,0.1000", "", "", "", ""],
        "filament_mixed_gradient_curve": ["", "", "", "", "", "0.0000,0.1377|1.0000,0.9000",
                                          "", "", "", ""],
        "filament_mixed_gradient_per_part": ["0", "0", "0", "0", "0", "1", "0", "0", "0", "0"],
        "filament_self_index": [str(i // 2 + 1) for i in range(20)],
        "nozzle_temperature": [str(200 + i // 2) for i in range(20)],
        "filament_map": ["1"] * 10,
    }


class TestScalarHelpers(unittest.TestCase):
    def test_components(self):
        self.assertEqual(parse_components("3,5,1"), [3, 5, 1])
        self.assertEqual(parse_components(" 1, x ,2"), [1, 2])
        self.assertEqual(parse_components(""), [])
        self.assertEqual(format_components([1, 3]), "1,3")

    def test_format_ratios_matches_upstream(self):
        self.assertEqual(format_ratios([50, 50]), "0.5000,0.5000")
        self.assertEqual(format_ratios([40, 40, 20]), "0.4000,0.4000,0.2000")
        self.assertEqual(format_ratios([26, 15, 59]), "0.2600,0.1500,0.5900")
        self.assertEqual(format_ratios([1, 2]), "0.3333,0.6667")

    def test_parse_ratios_normalizes_and_falls_back(self):
        self.assertEqual(parse_ratios("1,1", 2), [0.5, 0.5])
        self.assertEqual(parse_ratios("0.5000,0.5000", 3), [1 / 3] * 3)
        self.assertEqual(parse_ratios("", 2), [0.5, 0.5])


class TestGradientCurve(unittest.TestCase):
    def test_round_trip_two_field(self):
        text = "0.0000,0.1377|1.0000,0.9000"
        self.assertEqual(serialize_gradient_curve(parse_gradient_curve(text)), text)

    def test_round_trip_tangents_with_nan(self):
        text = "0.0000,0.2000,,0.5000|0.5000,0.6000|1.0000,0.8000,0.1000,"
        pts = parse_gradient_curve(text)
        self.assertTrue(math.isnan(pts[0].m_in))
        self.assertAlmostEqual(pts[0].m_out, 0.5)
        self.assertEqual(serialize_gradient_curve(pts), text)

    def test_clamps_sorts_and_rejects(self):
        pts = parse_gradient_curve("1.5,0.95|-1,0.0")
        self.assertEqual([(p.x, p.y) for p in pts], [(0.0, 0.1), (1.0, 0.9)])
        self.assertEqual(parse_gradient_curve("0.5,0.5"), [])
        self.assertEqual(parse_gradient_curve("0.5,0.5|bad|1,2,3"), [])

    def test_sanitize(self):
        self.assertEqual(sanitize_gradient_curve_array(["", "0.5,0.5", "0,0.2|1,0.8"]),
                         ["", "", "0,0.2|1,0.8"])

    def test_sampling_linear_and_monotone(self):
        line = [CurvePoint(0.0, 0.2), CurvePoint(1.0, 0.8)]
        self.assertAlmostEqual(sample_gradient_curve(line, 0.5), 0.5)
        self.assertAlmostEqual(sample_gradient_curve(line, -1), 0.2)
        self.assertAlmostEqual(sample_gradient_curve(line, 2), 0.8)
        curve = [CurvePoint(0, 0.1), CurvePoint(0.3, 0.8), CurvePoint(1, 0.9)]
        samples = [sample_gradient_curve(curve, i / 50) for i in range(51)]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(samples, samples[1:])))
        self.assertTrue(all(0.1 <= s <= 0.9 for s in samples))
        self.assertEqual(sample_gradient_curve([], 0.3), 0.5)

    def test_pchip_samples_match_upstream_vectors(self):
        curve = [CurvePoint(0.0, 0.1), CurvePoint(0.3, 0.8), CurvePoint(1.0, 0.9)]
        for t, expected in (
            (0.15, 0.5215344992449283),
            (0.45, 0.8499919450106443),
            (0.65, 0.8829544310457251),
        ):
            with self.subTest(t=t):
                self.assertAlmostEqual(sample_gradient_curve(curve, t), expected, places=12)

        custom = [
            CurvePoint(0.0, 0.2, m_out=0.5),
            CurvePoint(0.5, 0.7, m_in=0.1, m_out=0.3),
            CurvePoint(1.0, 0.8, m_in=0.25),
        ]
        self.assertAlmostEqual(sample_gradient_curve(custom, 0.25), 0.475, places=12)
        self.assertAlmostEqual(sample_gradient_curve(custom, 0.75), 0.753125, places=12)


class TestSlotModel(unittest.TestCase):
    def test_parse_fixture(self):
        slots = parse_mixed_slots(fixture_settings())
        self.assertEqual([s.slot for s in slots], [5, 6, 7, 8, 9])
        self.assertEqual(slots[0].components, [1, 2])
        self.assertTrue(slots[0].gradient and slots[0].per_part)
        self.assertEqual(slots[0].gradient_range, (0.9, 0.1))
        self.assertEqual(len(slots[0].gradient_curve), 2)
        self.assertEqual(slots[4].components, [3, 5, 1])
        self.assertAlmostEqual(sum(slots[3].ratios), 1.0)

    def test_clean_slots_write_back_verbatim(self):
        settings = fixture_settings()
        original = copy.deepcopy(settings)
        write_mixed_slots(settings, parse_mixed_slots(settings), 10)
        for key in MIXED_KEYS:
            self.assertEqual(settings[key], original[key], key)

    def test_dirty_slot_serializes(self):
        settings = fixture_settings()
        slots = parse_mixed_slots(settings)
        slots[1].ratios = [0.42, 0.58]
        slots[1].dirty = True
        write_mixed_slots(settings, slots, 10)
        self.assertEqual(settings["filament_mixed_sublayer_ratios"][6], "0.4200,0.5800")
        self.assertEqual(settings["filament_mixed_components"][6], "2,3")
        self.assertEqual(settings["filament_mixed_gradient"][6], "0")

    def test_write_noop_for_plain_config(self):
        settings = {"filament_colour": ["#FF0000"]}
        write_mixed_slots(settings, [], 1)
        self.assertEqual(settings, {"filament_colour": ["#FF0000"]})

    def test_flags(self):
        self.assertEqual(mixed_slot_flags({"filament_is_mixed": ["0", "1"]}, 3), [False, True, False])


class TestValidation(unittest.TestCase):
    def test_fixture_is_valid(self):
        self.assertEqual(validate_mixed_filament_params(fixture_settings()), {})

    def test_errors(self):
        cases = [
            (7, "1,1", "duplicate"),
            (7, "1", "at least 2"),
            (7, "1,6", "out of range"),
            (7, "1,x", "invalid component"),
        ]
        for slot, comps, needle in cases:
            s = fixture_settings()
            s["filament_mixed_components"][slot] = comps
            errors = validate_mixed_filament_params(s)
            self.assertIn(needle, errors.get("filament_mixed_components", ""), comps)

    def test_ratio_count_and_gradient_rules(self):
        s = fixture_settings()
        s["filament_mixed_sublayer_ratios"][6] = "0.5000"
        self.assertIn("ratio count", validate_mixed_filament_params(s)["filament_mixed_sublayer_ratios"])
        s = fixture_settings()
        s["filament_mixed_gradient"][7] = "1"
        self.assertIn("exactly 2", validate_mixed_filament_params(s)["filament_mixed_gradient"])
        s = fixture_settings()
        s["filament_mixed_gradient_range"][5] = "0.0,1.0"
        self.assertIn("(0, 1)", validate_mixed_filament_params(s)["filament_mixed_gradient_range"])

    def test_params_without_flags(self):
        s = {"filament_is_mixed": ["0", "0"], "filament_mixed_components": ["", "1,2"]}
        self.assertIn("filament_is_mixed", validate_mixed_filament_params(s))


class TestEditingHelpers(unittest.TestCase):
    def test_remap_on_delete(self):
        comps = ["", "", "1,3", "2,3"]
        remap_components_on_delete([False, False, True, True], comps, 2)
        self.assertEqual(comps, ["", "", "1,2", "0,2"])

    def test_find_fixed_skips_gradient(self):
        s = fixture_settings()
        self.assertEqual(find_fixed_mixed_slot(s, [2, 3], [50, 50]), 6)
        self.assertEqual(find_fixed_mixed_slot(s, [1, 2], [50, 50]), -1)  # slot 6 is a gradient
        self.assertEqual(find_fixed_mixed_slot(s, [1, 2, 3], [40, 40, 20]), 7)

    def test_type_consistency(self):
        flags = [False, False, False, True, True]
        comps = ["", "", "", "1,2", "1,3"]
        self.assertEqual(check_type_consistency(flags, comps, ["PLA", "PLA", "PETG"]), [4])


class TestConfigHousekeeping(unittest.TestCase):
    def test_strip_fullspectrum(self):
        s = {"mixed_filament_definitions": "x", "dithering_step": "1",
             "filament_is_mixed": ["0"], "silent_mode": "0"}
        self.assertEqual(sorted(strip_fullspectrum_keys(s)), ["dithering_step", "mixed_filament_definitions"])
        self.assertEqual(sorted(s), ["filament_is_mixed", "silent_mode"])

    def test_resize_identity_is_noop(self):
        s = fixture_settings()
        original = copy.deepcopy(s)
        resize_slot_arrays(s, 10, list(range(10)))
        self.assertEqual(s, original)

    def test_resize_append_physical_slot(self):
        s = fixture_settings()
        s["filament_map"][4] = "2"
        s["flush_multiplier"] = ["1"]
        s["flush_volumes_vector"] = [str(v) for v in range(20)]
        s["flush_volumes_matrix"] = [str(i * 10 + j) for i in range(10) for j in range(10)]
        resize_slot_arrays(s, 10, list(range(10)) + [4], new_slots=[10])
        self.assertEqual(len(s["filament_colour"]), 11)
        self.assertEqual(s["filament_colour"][10], "#00C1AE")
        self.assertEqual(s["filament_map"][10], "1")  # upstream default, not copied
        self.assertEqual(s["filament_is_mixed"][10], "0")
        self.assertEqual(s["filament_mixed_components"][10], "")
        self.assertEqual(s["filament_mixed_components"][9], "3,5,1")
        self.assertEqual(s["filament_self_index"][-2:], ["11", "11"])
        self.assertEqual(s["nozzle_temperature"][-2:], ["204", "204"])  # variant group of slot 5
        self.assertEqual(s["flush_volumes_vector"][-2:], ["0", "1"])
        m = s["flush_volumes_matrix"]
        self.assertEqual(len(m), 121)
        self.assertEqual(m[0 * 11 + 3], "3")      # kept
        self.assertEqual(m[10 * 11 + 10], "0")    # diagonal
        self.assertEqual(m[10 * 11 + 2], str(0 + 5))  # vec[2*10] + vec[2*2+1]

    def test_resize_without_variants_renumbers_self_index(self):
        s = {"filament_colour": ["#111111", "#222222"], "filament_self_index": ["1", "2"]}
        resize_slot_arrays(s, 2, [0, 1, 1], new_slots=[2])
        self.assertEqual(s["filament_self_index"], ["1", "2", "3"])
        self.assertEqual(s["filament_colour"], ["#111111", "#222222", "#222222"])

    def test_resize_ignores_unrelated_keys(self):
        s = fixture_settings()
        s["wall_loops"] = ["2"] * 10
        resize_slot_arrays(s, 10, list(range(10)) + [0], new_slots=[10])
        self.assertEqual(len(s["wall_loops"]), 10)


class TestSegmentationEscape(unittest.TestCase):
    def test_paint_tables_cover_32(self):
        self.assertEqual(len(ORCA_FILAMENT_CODES), 33)
        self.assertEqual(max(ORCA_PAINT_TO_INDEX.values()), 32)
        for index, code in enumerate(ORCA_FILAMENT_CODES):
            if code:
                self.assertEqual(ORCA_PAINT_TO_INDEX[code], index)

    def test_leaf_codes_match_upstream_table(self):
        encoder = SegmentationEncoder()
        for state in range(1, 33):
            code = encoder.encode(SegmentationNode(state=TriangleState(state)))
            self.assertEqual(code, ORCA_FILAMENT_CODES[state], state)
            self.assertEqual(int(decode_segmentation_string(code).state), state)
            self.assertEqual(_get_leaf_state(code), state)

    def test_split_tree_round_trip_high_states(self):
        children = [SegmentationNode(state=TriangleState(s)) for s in (2, 17, 18, 32)]
        tree = SegmentationNode(split_sides=3, special_side=0, children=children)
        code = SegmentationEncoder().encode(tree)
        decoded = decode_segmentation_string(code)
        self.assertEqual([int(c.state) for c in decoded.children], [2, 17, 18, 32])
        self.assertIsNone(_get_leaf_state(code))


if __name__ == "__main__":
    unittest.main()
