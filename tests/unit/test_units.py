"""
Unit tests for ``io_mesh_3mf.common.units``.

Tests unit conversion tables and scale-factor computation.
Requires ``bpy`` (runs inside Blender).
"""

import unittest
import xml.etree.ElementTree as ET

import bpy

from io_mesh_3mf.common.units import (
    blender_to_metre,
    threemf_to_metre,
    import_unit_scale,
    export_unit_scale,
)


class TestBlenderToMetre(unittest.TestCase):
    """blender_to_metre conversion table."""

    def test_millimeters(self):
        self.assertAlmostEqual(blender_to_metre["MILLIMETERS"], 0.001)

    def test_meters(self):
        self.assertAlmostEqual(blender_to_metre["METERS"], 1.0)

    def test_inches(self):
        self.assertAlmostEqual(blender_to_metre["INCHES"], 0.0254)

    def test_feet(self):
        self.assertAlmostEqual(blender_to_metre["FEET"], 0.3048)

    def test_centimeters(self):
        self.assertAlmostEqual(blender_to_metre["CENTIMETERS"], 0.01)

    def test_all_positive(self):
        for unit, scale in blender_to_metre.items():
            with self.subTest(unit=unit):
                self.assertGreater(scale, 0.0)


class TestThreeMFToMetre(unittest.TestCase):
    """threemf_to_metre conversion table."""

    def test_millimeter(self):
        self.assertAlmostEqual(threemf_to_metre["millimeter"], 0.001)

    def test_meter(self):
        self.assertAlmostEqual(threemf_to_metre["meter"], 1.0)

    def test_inch(self):
        self.assertAlmostEqual(threemf_to_metre["inch"], 0.0254)

    def test_micron(self):
        self.assertAlmostEqual(threemf_to_metre["micron"], 0.000001)

    def test_all_positive(self):
        for unit, scale in threemf_to_metre.items():
            with self.subTest(unit=unit):
                self.assertGreater(scale, 0.0)


class TestImportUnitScale(unittest.TestCase):
    """import_unit_scale() with real Blender context."""

    def _make_root(self, unit=None):
        """Create a minimal 3MF model root element."""
        xml_str = "<model"
        if unit:
            xml_str += f' unit="{unit}"'
        xml_str += " />"
        return ET.fromstring(xml_str)

    def test_default_unit_millimeter(self):
        """No unit attribute → defaults to millimeter."""
        root = self._make_root()
        scale = import_unit_scale(bpy.context, root, global_scale=1.0)
        self.assertGreater(scale, 0.0)

    def test_meter_unit_larger_scale(self):
        """meter unit should produce a larger scale factor than millimeter."""
        root_mm = self._make_root("millimeter")
        root_m = self._make_root("meter")
        scale_mm = import_unit_scale(bpy.context, root_mm)
        scale_m = import_unit_scale(bpy.context, root_m)
        self.assertGreater(scale_m, scale_mm)

    def test_global_scale_multiplied(self):
        """global_scale should multiply the result."""
        root = self._make_root("millimeter")
        scale_1 = import_unit_scale(bpy.context, root, global_scale=1.0)
        scale_2 = import_unit_scale(bpy.context, root, global_scale=2.0)
        self.assertAlmostEqual(scale_2, scale_1 * 2.0, places=6)


class TestExportUnitScale(unittest.TestCase):
    """export_unit_scale() with real Blender context."""

    def test_positive_scale(self):
        scale = export_unit_scale(bpy.context)
        self.assertGreater(scale, 0.0)

    def test_global_scale_multiplied(self):
        scale_1 = export_unit_scale(bpy.context, global_scale=1.0)
        scale_3 = export_unit_scale(bpy.context, global_scale=3.0)
        self.assertAlmostEqual(scale_3, scale_1 * 3.0, places=6)


if __name__ == "__main__":
    unittest.main()
