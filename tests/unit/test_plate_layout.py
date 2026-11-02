"""
Unit tests for ``io_mesh_3mf.export_3mf.plate_layout``.

Pure Python — no bpy.  Expected values come from OrcaSlicer's PartPlate
grid code and a reference 5-plate project saved by Orca.
"""

import unittest

from io_mesh_3mf.export_3mf.plate_layout import plate_column_count, plate_origin


class TestColumnCount(unittest.TestCase):
    def test_known_counts(self):
        expected = {1: 1, 2: 2, 3: 2, 4: 2, 5: 3, 6: 3, 9: 3, 10: 4, 16: 4, 17: 5}
        for count, cols in expected.items():
            with self.subTest(count=count):
                self.assertEqual(plate_column_count(count), cols)

    def test_matches_ceil_sqrt(self):
        import math

        for count in range(1, 200):
            self.assertEqual(plate_column_count(count), math.ceil(math.sqrt(count)))

    def test_zero_plates(self):
        self.assertEqual(plate_column_count(0), 0)


class TestPlateOrigin(unittest.TestCase):
    def test_first_plate_at_origin(self):
        self.assertEqual(plate_origin(0, 5, 220, 220), (0.0, 0.0))

    def test_five_plate_grid_220_bed(self):
        # 5 plates -> 3 columns; stride = 1.2 * 220 = 264
        self.assertEqual(plate_origin(1, 5, 220, 220), (264.0, 0.0))
        self.assertEqual(plate_origin(2, 5, 220, 220), (528.0, 0.0))
        self.assertEqual(plate_origin(3, 5, 220, 220), (0.0, -264.0))
        self.assertEqual(plate_origin(4, 5, 220, 220), (264.0, -264.0))

    def test_non_square_bed(self):
        x, y = plate_origin(2, 4, 300, 200)  # 2 cols -> row 1, col 0
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, -240.0)

    def test_matches_reference_orca_project(self):
        # Build-item translations from the reference 5-plate project; each
        # object sits near its plate centre, so the grid origin should be
        # within a few mm of the item translation.
        items = {  # plate index -> (tx, ty)
            1: (261.2, 0.8),
            2: (527.5, 0.1),
            3: (7.1, -263.5),
            4: (275.5, -256.6),
        }
        for index, (tx, ty) in items.items():
            ox, oy = plate_origin(index, 5, 220, 220)
            with self.subTest(plate=index + 1):
                self.assertLess(abs(tx - ox), 15)
                self.assertLess(abs(ty - oy), 15)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            plate_origin(0, 0, 220, 220)
        with self.assertRaises(ValueError):
            plate_origin(-1, 3, 220, 220)


if __name__ == "__main__":
    unittest.main()
