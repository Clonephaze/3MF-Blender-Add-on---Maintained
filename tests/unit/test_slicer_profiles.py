"""
Unit tests for slicer profile storage.

Covers ``io_mesh_3mf.slicer_profiles.storage``:
- _sanitize_filename — unsafe character replacement
- _extract_machine_name — Orca/Prusa config parsing
- save_profile / load_profile / delete_profile / rename_profile — CRUD ops
- get_profile_config — base85 decode of stored configs
- extract_from_3mf — vendor detection + config extraction

Uses a monkeypatched ``get_profiles_dir()`` to isolate tests from the
real Blender config directory.
"""

import base64
import json
import os
import tempfile
import shutil
import unittest
import zipfile
import xml.etree.ElementTree as ET


class SanitizeFilenameTests(unittest.TestCase):
    """Tests for _sanitize_filename()."""

    def setUp(self):
        from io_mesh_3mf.slicer_profiles.storage import _sanitize_filename
        self._sanitize_filename = _sanitize_filename

    def test_clean_name_unchanged(self):
        """A safe name is returned as-is."""
        self.assertEqual(self._sanitize_filename("My Profile"), "My Profile")

    def test_special_chars_replaced(self):
        """Characters <, >, :, \", /, \\, |, ?, * are replaced with _."""
        result = self._sanitize_filename('a<b>c:d"e/f\\g|h?i*j')
        self.assertNotIn('<', result)
        self.assertNotIn('>', result)
        self.assertNotIn(':', result)
        self.assertNotIn('"', result)
        self.assertNotIn('/', result)
        self.assertNotIn('\\', result)
        self.assertNotIn('|', result)
        self.assertNotIn('?', result)
        self.assertNotIn('*', result)

    def test_dots_stripped(self):
        """Leading/trailing dots and spaces are stripped."""
        result = self._sanitize_filename("...profile...")
        self.assertFalse(result.startswith('.'))
        self.assertFalse(result.endswith('.'))

    def test_empty_returns_default(self):
        """Empty/all-special input returns 'profile'."""
        self.assertEqual(self._sanitize_filename("..."), "profile")
        self.assertEqual(self._sanitize_filename(""), "profile")


class ExtractMachineNameTests(unittest.TestCase):
    """Tests for _extract_machine_name()."""

    def setUp(self):
        from io_mesh_3mf.slicer_profiles.storage import _extract_machine_name
        self._extract_machine_name = _extract_machine_name

    def test_orca_json(self):
        """Extracts printer_model from Orca-style JSON config."""
        config_data = json.dumps({"printer_model": "Bambu X1C"}).encode("utf-8")
        encoded = base64.b85encode(config_data).decode("UTF-8")
        configs = {"Metadata/project_settings.config": encoded}

        result = self._extract_machine_name(configs)
        self.assertEqual(result, "Bambu X1C")

    def test_prusa_ini(self):
        """Extracts printer_model from PrusaSlicer INI-style config."""
        ini = "# config\nprinter_model = MK3S+\nsome_other = value\n"
        encoded = base64.b85encode(ini.encode("utf-8")).decode("UTF-8")
        configs = {"Metadata/Slic3r_PE.config": encoded}

        result = self._extract_machine_name(configs)
        self.assertEqual(result, "MK3S+")

    def test_no_configs(self):
        """Empty configs returns empty string."""
        result = self._extract_machine_name({})
        self.assertEqual(result, "")

    def test_invalid_json(self):
        """Invalid JSON config returns empty string gracefully."""
        encoded = base64.b85encode(b"not json").decode("UTF-8")
        configs = {"Metadata/project_settings.config": encoded}

        result = self._extract_machine_name(configs)
        self.assertEqual(result, "")

    def test_json_without_printer_model(self):
        """JSON config without printer_model key returns empty string."""
        config_data = json.dumps({"other_field": "value"}).encode("utf-8")
        encoded = base64.b85encode(config_data).decode("UTF-8")
        configs = {"Metadata/project_settings.config": encoded}

        result = self._extract_machine_name(configs)
        self.assertEqual(result, "")


class ProfileCRUDTests(unittest.TestCase):
    """Tests for save/load/delete/rename profile operations.

    Monkeypatches ``get_profiles_dir`` to use a temp directory, avoiding
    side effects on the real Blender config.
    """

    def setUp(self):
        self._temp_dir = tempfile.mkdtemp(prefix="3mf_profiles_test_")

        import io_mesh_3mf.slicer_profiles.storage as storage_mod
        self._storage = storage_mod
        self._original_get_profiles_dir = storage_mod.get_profiles_dir

        # Monkeypatch to use temp dir
        storage_mod.get_profiles_dir = lambda: self._temp_dir

    def tearDown(self):
        self._storage.get_profiles_dir = self._original_get_profiles_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_save_and_load(self):
        """Save a profile and load it back by name."""
        self._storage.save_profile(
            name="Test Profile",
            vendor="Orca Slicer",
            source_file="test.3mf",
            configs={"Metadata/project_settings.config": "encoded_data"},
            machine="Bambu X1C",
        )

        loaded = self._storage.load_profile("Test Profile")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["name"], "Test Profile")
        self.assertEqual(loaded["vendor"], "Orca Slicer")
        self.assertEqual(loaded["machine"], "Bambu X1C")
        self.assertIn("Metadata/project_settings.config", loaded["configs"])

    def test_list_profiles(self):
        """list_profiles returns saved profiles sorted by name."""
        self._storage.save_profile("Zebra", "Orca", "z.3mf", {})
        self._storage.save_profile("Alpha", "Prusa", "a.3mf", {})

        profiles = self._storage.list_profiles()
        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0].name, "Alpha")
        self.assertEqual(profiles[1].name, "Zebra")

    def test_list_profiles_empty(self):
        """list_profiles returns empty list on fresh directory."""
        profiles = self._storage.list_profiles()
        self.assertEqual(len(profiles), 0)

    def test_delete_profile(self):
        """delete_profile removes the file and load returns None."""
        self._storage.save_profile("ToDelete", "Orca", "d.3mf", {})
        self.assertTrue(self._storage.delete_profile("ToDelete"))
        self.assertIsNone(self._storage.load_profile("ToDelete"))

    def test_delete_nonexistent(self):
        """delete_profile returns False for nonexistent profile."""
        self.assertFalse(self._storage.delete_profile("ghost"))

    def test_rename_profile(self):
        """rename_profile updates name and removes old file."""
        self._storage.save_profile("OldName", "Orca", "f.3mf", {})
        self.assertTrue(self._storage.rename_profile("OldName", "NewName"))

        self.assertIsNone(self._storage.load_profile("OldName"))
        loaded = self._storage.load_profile("NewName")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["name"], "NewName")

    def test_rename_nonexistent(self):
        """rename_profile returns False for missing profile."""
        self.assertFalse(self._storage.rename_profile("ghost", "new"))

    def test_save_duplicate_name_auto_suffix(self):
        """Saving with a duplicate name auto-appends a numeric suffix."""
        self._storage.save_profile("Dup", "Orca", "a.3mf", {})
        self._storage.save_profile("Dup", "Orca", "b.3mf", {})

        profiles = self._storage.list_profiles()
        names = [p.name for p in profiles]
        self.assertIn("Dup", names)
        self.assertIn("Dup (2)", names)


class GetProfileConfigTests(unittest.TestCase):
    """Tests for get_profile_config() base85 decode."""

    def setUp(self):
        self._temp_dir = tempfile.mkdtemp(prefix="3mf_profiles_test_")

        import io_mesh_3mf.slicer_profiles.storage as storage_mod
        self._storage = storage_mod
        self._original_get_profiles_dir = storage_mod.get_profiles_dir
        storage_mod.get_profiles_dir = lambda: self._temp_dir

    def tearDown(self):
        self._storage.get_profiles_dir = self._original_get_profiles_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_decode_config(self):
        """get_profile_config decodes base85-encoded config bytes."""
        raw = b"[printer_settings]\nprinter_model = MK3S+"
        encoded = base64.b85encode(raw).decode("UTF-8")

        self._storage.save_profile(
            "DecodeTest", "Prusa", "test.3mf",
            {"Metadata/Slic3r_PE.config": encoded},
        )

        result = self._storage.get_profile_config(
            "DecodeTest", "Metadata/Slic3r_PE.config"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result, raw)

    def test_missing_config_path(self):
        """get_profile_config returns None for a config path not in the profile."""
        self._storage.save_profile("Empty", "Orca", "x.3mf", {})

        result = self._storage.get_profile_config(
            "Empty", "Metadata/project_settings.config"
        )
        self.assertIsNone(result)

    def test_missing_profile(self):
        """get_profile_config returns None for a nonexistent profile."""
        result = self._storage.get_profile_config(
            "ghost", "Metadata/project_settings.config"
        )
        self.assertIsNone(result)


class ExtractFrom3mfTests(unittest.TestCase):
    """Tests for extract_from_3mf() — vendor detection + config extraction."""

    def setUp(self):
        from io_mesh_3mf.slicer_profiles.storage import extract_from_3mf
        self._extract_from_3mf = extract_from_3mf
        self._temp_dir = tempfile.mkdtemp(prefix="3mf_extract_test_")

    def tearDown(self):
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def _make_3mf(self, configs=None, application=None):
        """Helper: create a minimal 3MF ZIP for testing."""
        path = os.path.join(self._temp_dir, "test.3mf")
        with zipfile.ZipFile(path, 'w') as archive:
            # Write config files
            if configs:
                for config_path, content in configs.items():
                    with archive.open(config_path, 'w') as f:
                        f.write(content)

            # Write minimal model XML
            ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
            root = ET.Element(f"{{{ns}}}model")
            root.set("unit", "millimeter")
            if application:
                meta = ET.SubElement(root, f"{{{ns}}}metadata")
                meta.set("name", "Application")
                meta.text = application
            tree = ET.ElementTree(root)
            with archive.open('3D/3dmodel.model', 'w') as f:
                tree.write(f, xml_declaration=True, encoding="UTF-8")
        return path

    def test_detect_orca_from_config(self):
        """Detects Orca from project_settings.config presence."""
        path = self._make_3mf(
            configs={"Metadata/project_settings.config": b'{"some": "data"}'}
        )
        vendor, machine, configs, labels = self._extract_from_3mf(path)
        self.assertEqual(vendor, "Orca Slicer")
        self.assertIn("Metadata/project_settings.config", configs)
        self.assertIn("Project Settings", labels)

    def test_detect_prusa_from_config(self):
        """Detects Prusa from Slic3r_PE.config presence."""
        path = self._make_3mf(
            configs={"Metadata/Slic3r_PE.config": b"printer_model = MK3S+\n"}
        )
        vendor, machine, configs, labels = self._extract_from_3mf(path)
        self.assertEqual(vendor, "PrusaSlicer")
        self.assertIn("Metadata/Slic3r_PE.config", configs)

    def test_detect_orca_from_application_xml(self):
        """Application metadata in XML refines vendor detection."""
        path = self._make_3mf(
            configs={"Metadata/project_settings.config": b"{}"},
            application="OrcaSlicer 2.2.0",
        )
        vendor, machine, configs, labels = self._extract_from_3mf(path)
        self.assertEqual(vendor, "Orca Slicer")

    def test_detect_bambu_from_application_xml(self):
        """BambuStudio is detected from Application metadata."""
        path = self._make_3mf(
            configs={"Metadata/project_settings.config": b"{}"},
            application="BambuStudio 1.9",
        )
        vendor, machine, configs, labels = self._extract_from_3mf(path)
        self.assertEqual(vendor, "BambuStudio")

    def test_no_configs(self):
        """A 3MF without slicer configs returns empty data."""
        path = self._make_3mf()
        vendor, machine, configs, labels = self._extract_from_3mf(path)
        self.assertEqual(vendor, "")
        self.assertEqual(configs, {})
        self.assertEqual(labels, [])

    def test_machine_extracted(self):
        """Machine name is extracted from config data."""
        config_data = json.dumps({"printer_model": "X1 Carbon"}).encode("utf-8")
        path = self._make_3mf(
            configs={"Metadata/project_settings.config": config_data}
        )
        vendor, machine, configs, labels = self._extract_from_3mf(path)
        self.assertEqual(machine, "X1 Carbon")


if __name__ == "__main__":
    unittest.main()
