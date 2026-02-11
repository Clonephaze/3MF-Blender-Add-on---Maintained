"""
Unit tests for ``io_mesh_3mf.common.colors``.

Tests hex/RGB conversions and sRGB <-> linear colour space transforms.
All functions are pure Python — no bpy or mathutils required.
"""

import unittest
from io_mesh_3mf.common.colors import (
    srgb_to_linear,
    linear_to_srgb,
    hex_to_rgb,
    hex_to_linear_rgb,
    rgb_to_hex,
    linear_rgb_to_hex,
)


class TestSRGBToLinear(unittest.TestCase):
    """srgb_to_linear()"""

    def test_zero(self):
        self.assertAlmostEqual(srgb_to_linear(0.0), 0.0)

    def test_one(self):
        self.assertAlmostEqual(srgb_to_linear(1.0), 1.0)

    def test_mid_grey(self):
        # 0.5 sRGB ≈ 0.214 linear
        result = srgb_to_linear(0.5)
        self.assertAlmostEqual(result, 0.214, places=2)

    def test_low_value_linear_region(self):
        # Below the 0.04045 knee, the mapping is c / 12.92
        self.assertAlmostEqual(srgb_to_linear(0.04), 0.04 / 12.92, places=6)

    def test_above_knee(self):
        # 0.05 is just above the knee
        expected = pow((0.05 + 0.055) / 1.055, 2.4)
        self.assertAlmostEqual(srgb_to_linear(0.05), expected, places=6)


class TestLinearToSRGB(unittest.TestCase):
    """linear_to_srgb()"""

    def test_zero(self):
        self.assertAlmostEqual(linear_to_srgb(0.0), 0.0)

    def test_one(self):
        self.assertAlmostEqual(linear_to_srgb(1.0), 1.0)

    def test_low_value_linear_region(self):
        self.assertAlmostEqual(linear_to_srgb(0.002), 0.002 * 12.92, places=6)

    def test_round_trip_mid(self):
        """srgb_to_linear then linear_to_srgb should be identity."""
        for v in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0):
            with self.subTest(v=v):
                self.assertAlmostEqual(linear_to_srgb(srgb_to_linear(v)), v, places=5)

    def test_round_trip_linear_first(self):
        """linear_to_srgb then srgb_to_linear should be identity."""
        for v in (0.0, 0.01, 0.1, 0.5, 1.0):
            with self.subTest(v=v):
                self.assertAlmostEqual(srgb_to_linear(linear_to_srgb(v)), v, places=5)


class TestHexToRGB(unittest.TestCase):
    """hex_to_rgb() — raw sRGB, no gamma conversion."""

    def test_black(self):
        self.assertEqual(hex_to_rgb("#000000"), (0.0, 0.0, 0.0))

    def test_white(self):
        self.assertEqual(hex_to_rgb("#FFFFFF"), (1.0, 1.0, 1.0))

    def test_red(self):
        r, g, b = hex_to_rgb("#FF0000")
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 0.0)
        self.assertAlmostEqual(b, 0.0)

    def test_green(self):
        r, g, b = hex_to_rgb("#00FF00")
        self.assertAlmostEqual(r, 0.0)
        self.assertAlmostEqual(g, 1.0)
        self.assertAlmostEqual(b, 0.0)

    def test_blue(self):
        r, g, b = hex_to_rgb("#0000FF")
        self.assertAlmostEqual(r, 0.0)
        self.assertAlmostEqual(g, 0.0)
        self.assertAlmostEqual(b, 1.0)

    def test_no_hash_prefix(self):
        """Leading '#' is optional."""
        self.assertEqual(hex_to_rgb("CC3319"), hex_to_rgb("#CC3319"))

    def test_specific_value(self):
        r, g, b = hex_to_rgb("#80C040")
        self.assertAlmostEqual(r, 128 / 255.0, places=3)
        self.assertAlmostEqual(g, 192 / 255.0, places=3)
        self.assertAlmostEqual(b, 64 / 255.0, places=3)


class TestHexToLinearRGB(unittest.TestCase):
    """hex_to_linear_rgb() — returns linear-space values."""

    def test_black(self):
        self.assertEqual(hex_to_linear_rgb("#000000"), (0.0, 0.0, 0.0))

    def test_white(self):
        r, g, b = hex_to_linear_rgb("#FFFFFF")
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 1.0)
        self.assertAlmostEqual(b, 1.0)

    def test_result_is_linear(self):
        """Linear values should be lower than raw sRGB for mid-tones."""
        r_linear, _, _ = hex_to_linear_rgb("#808080")
        r_srgb, _, _ = hex_to_rgb("#808080")
        self.assertLess(r_linear, r_srgb)


class TestRGBToHex(unittest.TestCase):
    """rgb_to_hex() — raw sRGB floats to hex string."""

    def test_black(self):
        self.assertEqual(rgb_to_hex(0.0, 0.0, 0.0), "#000000")

    def test_white(self):
        self.assertEqual(rgb_to_hex(1.0, 1.0, 1.0), "#FFFFFF")

    def test_red(self):
        self.assertEqual(rgb_to_hex(1.0, 0.0, 0.0), "#FF0000")

    def test_clamping_over(self):
        """Values > 1.0 should clamp to FF."""
        self.assertEqual(rgb_to_hex(1.5, 0.0, 0.0), "#FF0000")

    def test_clamping_under(self):
        """Negative values should clamp to 00."""
        self.assertEqual(rgb_to_hex(-0.1, 0.0, 0.0), "#000000")

    def test_round_trip(self):
        """hex -> rgb -> hex should be identity."""
        for hex_str in ("#CC3319", "#1A2B3C", "#AABBCC", "#000000", "#FFFFFF"):
            with self.subTest(hex_str=hex_str):
                r, g, b = hex_to_rgb(hex_str)
                self.assertEqual(rgb_to_hex(r, g, b), hex_str)


class TestLinearRGBToHex(unittest.TestCase):
    """linear_rgb_to_hex() — linear floats -> sRGB hex."""

    def test_black(self):
        self.assertEqual(linear_rgb_to_hex(0.0, 0.0, 0.0), "#000000")

    def test_white(self):
        self.assertEqual(linear_rgb_to_hex(1.0, 1.0, 1.0), "#FFFFFF")

    def test_round_trip_via_linear(self):
        """hex -> linear -> hex should be identity (within rounding)."""
        for hex_str in ("#CC3319", "#1A2B3C", "#AABBCC"):
            with self.subTest(hex_str=hex_str):
                r, g, b = hex_to_linear_rgb(hex_str)
                result = linear_rgb_to_hex(r, g, b)
                self.assertEqual(result, hex_str)


if __name__ == "__main__":
    unittest.main()
