"""
Unit tests for API versioning and discovery.

Tests the API registry, version checking, and capability detection
added to ``io_mesh_3mf.api``.
"""

import unittest

import bpy

from io_mesh_3mf.api import (
    API_VERSION,
    API_VERSION_STRING,
    API_CAPABILITIES,
    is_available,
    get_api,
    has_capability,
    check_version,
    _REGISTRY_KEY,
)


class APIVersionTests(unittest.TestCase):
    """Tests for API version constants."""

    def test_version_is_tuple(self):
        """API_VERSION should be a 3-tuple of ints."""
        self.assertIsInstance(API_VERSION, tuple)
        self.assertEqual(len(API_VERSION), 3)
        self.assertTrue(all(isinstance(v, int) for v in API_VERSION))

    def test_version_string_format(self):
        """API_VERSION_STRING should be 'X.Y.Z' format."""
        self.assertIsInstance(API_VERSION_STRING, str)
        parts = API_VERSION_STRING.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts))

    def test_version_string_matches_tuple(self):
        """Version string should match the tuple values."""
        expected = ".".join(str(v) for v in API_VERSION)
        self.assertEqual(API_VERSION_STRING, expected)


class APICapabilitiesTests(unittest.TestCase):
    """Tests for API capabilities."""

    def test_capabilities_is_frozenset(self):
        """API_CAPABILITIES should be an immutable frozenset."""
        self.assertIsInstance(API_CAPABILITIES, frozenset)

    def test_core_capabilities_present(self):
        """Core capabilities should be defined."""
        core = {"import", "export", "inspect", "batch", "callbacks"}
        self.assertTrue(core.issubset(API_CAPABILITIES))

    def test_format_capabilities_present(self):
        """Format-specific capabilities should be defined."""
        formats = {"orca_format", "prusa_format", "paint_mode"}
        self.assertTrue(formats.issubset(API_CAPABILITIES))

    def test_has_capability_returns_bool(self):
        """has_capability should return boolean."""
        self.assertIsInstance(has_capability("import"), bool)
        self.assertIsInstance(has_capability("nonexistent_xyz"), bool)

    def test_has_capability_true_for_known(self):
        """has_capability should return True for known capabilities."""
        self.assertTrue(has_capability("import"))
        self.assertTrue(has_capability("export"))
        self.assertTrue(has_capability("inspect"))

    def test_has_capability_false_for_unknown(self):
        """has_capability should return False for unknown capabilities."""
        self.assertFalse(has_capability("nonexistent_capability_xyz"))
        self.assertFalse(has_capability(""))


class APIRegistryTests(unittest.TestCase):
    """Tests for API registry in bpy.app.driver_namespace."""

    def test_registry_key_is_string(self):
        """Registry key should be a string."""
        self.assertIsInstance(_REGISTRY_KEY, str)
        self.assertEqual(_REGISTRY_KEY, "io_mesh_3mf")

    def test_is_available_returns_bool(self):
        """is_available should return boolean."""
        self.assertIsInstance(is_available(), bool)

    def test_get_api_returns_module_or_none(self):
        """get_api should return the api module or None."""
        result = get_api()
        # During tests, the addon should be registered
        if is_available():
            self.assertIsNotNone(result)
            # Should have the expected functions
            self.assertTrue(hasattr(result, "import_3mf"))
            self.assertTrue(hasattr(result, "export_3mf"))
            self.assertTrue(hasattr(result, "inspect_3mf"))

    def test_registry_contains_api_module(self):
        """After import, API should be in driver_namespace."""
        # The api module auto-registers on import
        self.assertIn(_REGISTRY_KEY, bpy.app.driver_namespace)


class CheckVersionTests(unittest.TestCase):
    """Tests for check_version function."""

    def test_check_version_returns_bool(self):
        """check_version should return boolean."""
        self.assertIsInstance(check_version((0, 0, 0)), bool)

    def test_check_version_true_for_lower(self):
        """check_version should return True for lower versions."""
        self.assertTrue(check_version((0, 0, 0)))
        self.assertTrue(check_version((0, 0, 1)))
        self.assertTrue(check_version((0, 1, 0)))

    def test_check_version_true_for_equal(self):
        """check_version should return True for equal version."""
        self.assertTrue(check_version(API_VERSION))

    def test_check_version_false_for_higher(self):
        """check_version should return False for higher versions."""
        higher_major = (API_VERSION[0] + 1, 0, 0)
        self.assertFalse(check_version(higher_major))

        higher_minor = (API_VERSION[0], API_VERSION[1] + 1, 0)
        self.assertFalse(check_version(higher_minor))

        higher_patch = (API_VERSION[0], API_VERSION[1], API_VERSION[2] + 1)
        self.assertFalse(check_version(higher_patch))


class DiscoveryHelperModuleTests(unittest.TestCase):
    """Tests for the standalone discovery helper module."""

    def test_discovery_helper_exists(self):
        """The discovery helper module should exist."""
        from io_mesh_3mf import threemf_discovery
        self.assertIsNotNone(threemf_discovery)

    def test_discovery_helper_functions(self):
        """Discovery helper should have expected functions."""
        from io_mesh_3mf import threemf_discovery

        self.assertTrue(hasattr(threemf_discovery, "is_threemf_available"))
        self.assertTrue(hasattr(threemf_discovery, "get_threemf_api"))
        self.assertTrue(hasattr(threemf_discovery, "get_threemf_version"))
        self.assertTrue(hasattr(threemf_discovery, "check_threemf_version"))
        self.assertTrue(hasattr(threemf_discovery, "has_threemf_capability"))

    def test_discovery_helper_is_threemf_available(self):
        """Discovery helper is_threemf_available should work."""
        from io_mesh_3mf import threemf_discovery

        result = threemf_discovery.is_threemf_available()
        self.assertIsInstance(result, bool)
        # Should be True since we just imported the api module
        self.assertTrue(result)

    def test_discovery_helper_get_api(self):
        """Discovery helper get_threemf_api should return the API."""
        from io_mesh_3mf import threemf_discovery

        api = threemf_discovery.get_threemf_api()
        self.assertIsNotNone(api)
        self.assertTrue(hasattr(api, "import_3mf"))

    def test_discovery_helper_get_version(self):
        """Discovery helper get_threemf_version should return tuple."""
        from io_mesh_3mf import threemf_discovery

        version = threemf_discovery.get_threemf_version()
        self.assertIsNotNone(version)
        self.assertIsInstance(version, tuple)
        self.assertEqual(len(version), 3)


if __name__ == "__main__":
    unittest.main()
