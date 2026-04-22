"""
Integration tests for OrcaSlicer-FullSpectrum import/export round-trip.

Verifies that importing a FullSpectrum .3mf file and re-exporting it
preserves the ``mixed_filament_definitions`` string and all related
FullSpectrum project_settings keys without data loss.

Requires the PeggyPalette reference file at:
    tests/resources/PeggyPalette38+Mini+BRYW.3mf

Run with:
    blender --background --factory-startup --python-exit-code 1 \\
            -noaudio -q --python tests/run_tests.py -- test_fullspectrum_roundtrip
"""

import json
import zipfile
from pathlib import Path

import bpy

from test_base import Blender3mfTestCase

# The 13 FullSpectrum keys that must survive the round-trip
_FULLSPECTRUM_KEYS = {
    "mixed_filament_definitions",
    "mixed_filament_gradient_mode",
    "mixed_filament_height_lower_bound",
    "mixed_filament_height_upper_bound",
    "mixed_filament_advanced_dithering",
    "mixed_filament_component_bias_enabled",
    "mixed_filament_surface_indentation",
    "mixed_filament_region_collapse",
    "mixed_color_layer_height_a",
    "mixed_color_layer_height_b",
    "dithering_z_step_size",
    "dithering_local_z_mode",
    "dithering_step_painted_zones_only",
}


def _read_project_settings(path: Path) -> dict:
    """Read and parse Metadata/project_settings.config from a .3mf archive."""
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        config_name = next(
            (n for n in names if n.lower() == "metadata/project_settings.config"),
            None,
        )
        if config_name is None:
            return {}
        with zf.open(config_name) as f:
            return json.loads(f.read().decode("utf-8"))


class FullSpectrumDetectionTests(Blender3mfTestCase):
    """Unit-style checks for detect_fullspectrum(), run inside Blender."""

    def test_detect_fullspectrum_true(self):
        """detect_fullspectrum() returns True for a non-empty definitions string."""
        from io_mesh_3mf.import_3mf.slicer.detection import detect_fullspectrum
        self.assertTrue(detect_fullspectrum({"mixed_filament_definitions": "1,2,1,1,50,0,g,w,m2"}))

    def test_detect_fullspectrum_false_empty(self):
        """detect_fullspectrum() returns False when definitions is an empty string."""
        from io_mesh_3mf.import_3mf.slicer.detection import detect_fullspectrum
        self.assertFalse(detect_fullspectrum({"mixed_filament_definitions": ""}))

    def test_detect_fullspectrum_false_missing(self):
        """detect_fullspectrum() returns False when key is absent."""
        from io_mesh_3mf.import_3mf.slicer.detection import detect_fullspectrum
        self.assertFalse(detect_fullspectrum({}))

    def test_detect_fullspectrum_false_whitespace(self):
        """detect_fullspectrum() returns False for whitespace-only value."""
        from io_mesh_3mf.import_3mf.slicer.detection import detect_fullspectrum
        self.assertFalse(detect_fullspectrum({"mixed_filament_definitions": "   "}))


class PeggyPaletteImportTests(Blender3mfTestCase):
    """Import tests for the PeggyPalette FullSpectrum reference file."""

    def _peggy_path(self):
        path = self.test_resources_dir / "PeggyPalette38+Mini+BRYW.3mf"
        if not path.exists():
            self.skipTest(f"Resource not found: {path}")
        return path

    def test_import_sets_has_mixed_filaments(self):
        """Importing PeggyPalette sets has_mixed_filaments on MMUPaintSettings."""
        result = bpy.ops.import_mesh.threemf(filepath=str(self._peggy_path()))
        self.assertIn("FINISHED", result)
        settings = bpy.context.scene.mmu_paint
        self.assertTrue(
            settings.has_mixed_filaments,
            "has_mixed_filaments should be True after importing FullSpectrum file",
        )

    def test_import_stores_scene_property(self):
        """Importing PeggyPalette stores 3mf_mixed_filament_definitions on the scene."""
        result = bpy.ops.import_mesh.threemf(filepath=str(self._peggy_path()))
        self.assertIn("FINISHED", result)
        raw = bpy.context.scene.get("3mf_mixed_filament_definitions", "")
        self.assertTrue(raw, "3mf_mixed_filament_definitions should be non-empty")
        rows = [r for r in raw.split(";") if r.strip()]
        self.assertEqual(len(rows), 40, f"Expected 40 rows, got {len(rows)}")

    def test_import_populates_mixed_filaments_collection(self):
        """Importing PeggyPalette fills settings.mixed_filaments with 40 entries."""
        result = bpy.ops.import_mesh.threemf(filepath=str(self._peggy_path()))
        self.assertIn("FINISHED", result)
        settings = bpy.context.scene.mmu_paint
        self.assertEqual(
            len(settings.mixed_filaments),
            40,
            f"Expected 40 mixed_filaments entries, got {len(settings.mixed_filaments)}",
        )

    def test_import_sets_has_mixed_flag_on_scene(self):
        """Importing PeggyPalette sets 3mf_has_mixed_filaments scene property."""
        result = bpy.ops.import_mesh.threemf(filepath=str(self._peggy_path()))
        self.assertIn("FINISHED", result)
        self.assertTrue(bpy.context.scene.get("3mf_has_mixed_filaments", False))


class PeggyPaletteRoundTripTests(Blender3mfTestCase):
    """Export round-trip tests for the PeggyPalette FullSpectrum reference file."""

    def _peggy_path(self):
        path = self.test_resources_dir / "PeggyPalette38+Mini+BRYW.3mf"
        if not path.exists():
            self.skipTest(f"Resource not found: {path}")
        return path

    def _import_and_export(self):
        """Import PeggyPalette, capture the raw definitions, then export."""
        result = bpy.ops.import_mesh.threemf(filepath=str(self._peggy_path()))
        self.assertIn("FINISHED", result, "Import did not finish")
        original_raw = str(bpy.context.scene.get("3mf_mixed_filament_definitions", ""))
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            use_orca_format="ORCA",
        )
        self.assertIn("FINISHED", result, "Export did not finish")
        self.assertTrue(self.temp_file.exists())
        return original_raw

    def test_roundtrip_definitions_entry_count(self):
        """Exported mixed_filament_definitions has the same number of entries as imported."""
        original_raw = self._import_and_export()
        self.assertTrue(original_raw, "No definitions stored after import")

        exported = _read_project_settings(self.temp_file)
        self.assertIn("mixed_filament_definitions", exported)

        from io_mesh_3mf.common.mixed_filaments import parse_mixed_filament_definitions
        orig_count = len(parse_mixed_filament_definitions(original_raw))
        exp_count = len(parse_mixed_filament_definitions(exported["mixed_filament_definitions"]))
        self.assertEqual(orig_count, exp_count, f"Entry count: imported {orig_count}, exported {exp_count}")

    def test_roundtrip_stable_ids_preserved(self):
        """Stable IDs are unchanged through the export round-trip."""
        original_raw = self._import_and_export()
        exported = _read_project_settings(self.temp_file)

        from io_mesh_3mf.common.mixed_filaments import parse_mixed_filament_definitions
        orig_ids = {e.stable_id for e in parse_mixed_filament_definitions(original_raw)}
        exp_ids = {e.stable_id for e in parse_mixed_filament_definitions(
            exported.get("mixed_filament_definitions", "")
        )}
        self.assertEqual(orig_ids, exp_ids, "Stable IDs changed during export")

    def test_roundtrip_patterns_preserved(self):
        """Manual pattern strings survive the export round-trip."""
        original_raw = self._import_and_export()
        exported = _read_project_settings(self.temp_file)

        from io_mesh_3mf.common.mixed_filaments import parse_mixed_filament_definitions
        orig = {e.stable_id: e.manual_pattern
                for e in parse_mixed_filament_definitions(original_raw) if e.manual_pattern}
        exp = {e.stable_id: e.manual_pattern
               for e in parse_mixed_filament_definitions(
                   exported.get("mixed_filament_definitions", "")
               ) if e.manual_pattern}
        self.assertEqual(orig, exp, "Manual patterns changed during export")

    def test_roundtrip_fullspectrum_keys_present(self):
        """All FullSpectrum keys that were in the source file appear in the export."""
        peggy = self._peggy_path()
        original = _read_project_settings(peggy)
        source_fs_keys = _FULLSPECTRUM_KEYS & set(original.keys())
        self.assertGreater(len(source_fs_keys), 0, "Source file has no FullSpectrum keys")

        self._import_and_export()
        exported = _read_project_settings(self.temp_file)
        for key in source_fs_keys:
            self.assertIn(key, exported, f"FullSpectrum key '{key}' missing from export")

    def test_roundtrip_scalar_fullspectrum_keys_unchanged(self):
        """Scalar FullSpectrum config values are unchanged after export."""
        peggy = self._peggy_path()
        original = _read_project_settings(peggy)

        self._import_and_export()
        exported = _read_project_settings(self.temp_file)

        skip = {"mixed_filament_definitions"}
        for key in _FULLSPECTRUM_KEYS - skip:
            if key not in original:
                continue
            orig_val = original[key]
            if isinstance(orig_val, list):
                continue
            self.assertIn(key, exported, f"Key '{key}' missing from export")
            self.assertEqual(
                str(exported[key]), str(orig_val),
                f"Key '{key}': {orig_val!r} → {exported[key]!r}",
            )

    def test_roundtrip_physical_filament_colors_present(self):
        """The 4 physical filament colors appear at the start of filament_colour."""
        self._import_and_export()
        exported = _read_project_settings(self.temp_file)
        colours = exported.get("filament_colour", [])
        self.assertGreaterEqual(len(colours), 4, f"Expected ≥4 filament_colour entries, got {len(colours)}")
        physical = {"#0000FF", "#FF0000", "#FFFF00", "#FFFFFF"}
        exported_set = {c.upper() for c in colours[:4]}
        self.assertEqual(exported_set, physical, f"Physical colors: {exported_set} vs {physical}")
