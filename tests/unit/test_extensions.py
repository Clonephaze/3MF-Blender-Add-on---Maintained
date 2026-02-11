"""
Unit tests for ``io_mesh_3mf.common.extensions``.

Tests Extension dataclass, ExtensionManager, and convenience functions.
Pure Python — no bpy or mathutils required (only uses xml.etree.ElementTree).
"""

import unittest
import xml.etree.ElementTree as ET

from io_mesh_3mf.common.extensions import (
    Extension,
    ExtensionType,
    ExtensionManager,
    EXTENSION_REGISTRY,
    MATERIALS_EXTENSION,
    PRODUCTION_EXTENSION,
    SLICE_EXTENSION,
    BEAM_LATTICE_EXTENSION,
    VOLUMETRIC_EXTENSION,
    TRIANGLE_SETS_EXTENSION,
    ORCA_EXTENSION,
    get_extension_by_namespace,
    get_extension_by_prefix,
    list_official_extensions,
    list_vendor_extensions,
)


# ============================================================================
# Extension dataclass
# ============================================================================


class TestExtensionDataclass(unittest.TestCase):
    """Extension dataclass fields and defaults."""

    def test_official_extension_fields(self):
        self.assertEqual(MATERIALS_EXTENSION.prefix, "m")
        self.assertEqual(MATERIALS_EXTENSION.extension_type, ExtensionType.OFFICIAL)
        self.assertFalse(MATERIALS_EXTENSION.required)

    def test_vendor_extension_fields(self):
        self.assertEqual(ORCA_EXTENSION.extension_type, ExtensionType.VENDOR)
        self.assertIsNotNone(ORCA_EXTENSION.vendor_attribute)

    def test_required_extension(self):
        self.assertTrue(PRODUCTION_EXTENSION.required)

    def test_custom_extension(self):
        ext = Extension(
            namespace="http://example.com/test",
            prefix="x",
            name="Test",
            extension_type=ExtensionType.VENDOR,
            description="A test extension",
        )
        self.assertEqual(ext.namespace, "http://example.com/test")
        self.assertFalse(ext.required)
        self.assertIsNone(ext.vendor_attribute)


# ============================================================================
# EXTENSION_REGISTRY
# ============================================================================


class TestExtensionRegistry(unittest.TestCase):
    """Global registry maps namespace URIs to Extension objects."""

    def test_all_official_registered(self):
        for ext in [
            MATERIALS_EXTENSION,
            PRODUCTION_EXTENSION,
            SLICE_EXTENSION,
            BEAM_LATTICE_EXTENSION,
            VOLUMETRIC_EXTENSION,
            TRIANGLE_SETS_EXTENSION,
        ]:
            self.assertIn(ext.namespace, EXTENSION_REGISTRY)
            self.assertIs(EXTENSION_REGISTRY[ext.namespace], ext)

    def test_vendor_registered(self):
        self.assertIn(ORCA_EXTENSION.namespace, EXTENSION_REGISTRY)

    def test_registry_count(self):
        self.assertGreaterEqual(len(EXTENSION_REGISTRY), 7)


# ============================================================================
# ExtensionManager
# ============================================================================


class TestExtensionManager(unittest.TestCase):
    """ExtensionManager activation, deactivation, and queries."""

    def setUp(self):
        self.mgr = ExtensionManager()

    def test_initially_empty(self):
        self.assertEqual(self.mgr.get_active_extensions(), [])

    def test_activate(self):
        self.mgr.activate(MATERIALS_EXTENSION.namespace)
        self.assertTrue(self.mgr.is_active(MATERIALS_EXTENSION.namespace))

    def test_activate_unknown_raises(self):
        with self.assertRaises(ValueError):
            self.mgr.activate("http://example.com/unknown")

    def test_deactivate(self):
        self.mgr.activate(MATERIALS_EXTENSION.namespace)
        self.mgr.deactivate(MATERIALS_EXTENSION.namespace)
        self.assertFalse(self.mgr.is_active(MATERIALS_EXTENSION.namespace))

    def test_deactivate_inactive_is_noop(self):
        # Should not raise
        self.mgr.deactivate(MATERIALS_EXTENSION.namespace)

    def test_clear(self):
        self.mgr.activate(MATERIALS_EXTENSION.namespace)
        self.mgr.activate(PRODUCTION_EXTENSION.namespace)
        self.mgr.clear()
        self.assertEqual(self.mgr.get_active_extensions(), [])

    def test_get_active_extensions(self):
        self.mgr.activate(MATERIALS_EXTENSION.namespace)
        self.mgr.activate(ORCA_EXTENSION.namespace)
        active = self.mgr.get_active_extensions()
        self.assertEqual(len(active), 2)
        namespaces = {e.namespace for e in active}
        self.assertIn(MATERIALS_EXTENSION.namespace, namespaces)
        self.assertIn(ORCA_EXTENSION.namespace, namespaces)

    def test_get_required_extensions_string(self):
        self.mgr.activate(MATERIALS_EXTENSION.namespace)  # not required
        self.mgr.activate(PRODUCTION_EXTENSION.namespace)  # required
        result = self.mgr.get_required_extensions_string()
        self.assertIn(PRODUCTION_EXTENSION.namespace, result)
        self.assertNotIn(MATERIALS_EXTENSION.namespace, result)

    def test_get_required_extensions_string_empty(self):
        self.assertEqual(self.mgr.get_required_extensions_string(), "")

    def test_get_vendor_attributes(self):
        self.mgr.activate(ORCA_EXTENSION.namespace)
        attrs = self.mgr.get_vendor_attributes()
        self.assertIn(ORCA_EXTENSION.vendor_attribute, attrs)
        self.assertEqual(attrs[ORCA_EXTENSION.vendor_attribute], "1")

    def test_get_vendor_attributes_empty_for_official(self):
        self.mgr.activate(MATERIALS_EXTENSION.namespace)
        attrs = self.mgr.get_vendor_attributes()
        self.assertEqual(attrs, {})

    def test_register_namespaces(self):
        self.mgr.activate(MATERIALS_EXTENSION.namespace)
        # register_namespace is idempotent; just verify it doesn't crash
        self.mgr.register_namespaces(ET)


# ============================================================================
# Convenience functions
# ============================================================================


class TestConvenienceFunctions(unittest.TestCase):
    """get_extension_by_namespace/prefix, list_official/vendor."""

    def test_get_by_namespace_found(self):
        ext = get_extension_by_namespace(MATERIALS_EXTENSION.namespace)
        self.assertIs(ext, MATERIALS_EXTENSION)

    def test_get_by_namespace_not_found(self):
        self.assertIsNone(get_extension_by_namespace("http://example.com/nope"))

    def test_get_by_prefix_found(self):
        ext = get_extension_by_prefix("m")
        self.assertIs(ext, MATERIALS_EXTENSION)

    def test_get_by_prefix_not_found(self):
        self.assertIsNone(get_extension_by_prefix("zzz"))

    def test_list_official(self):
        officials = list_official_extensions()
        self.assertTrue(len(officials) >= 6)
        for ext in officials:
            self.assertEqual(ext.extension_type, ExtensionType.OFFICIAL)

    def test_list_vendor(self):
        vendors = list_vendor_extensions()
        self.assertTrue(len(vendors) >= 1)
        for ext in vendors:
            self.assertEqual(ext.extension_type, ExtensionType.VENDOR)

    def test_official_and_vendor_are_disjoint(self):
        official_ns = {e.namespace for e in list_official_extensions()}
        vendor_ns = {e.namespace for e in list_vendor_extensions()}
        self.assertEqual(official_ns & vendor_ns, set())

    def test_together_cover_registry(self):
        all_ns = {e.namespace for e in list_official_extensions()} | {
            e.namespace for e in list_vendor_extensions()
        }
        self.assertEqual(all_ns, set(EXTENSION_REGISTRY.keys()))


if __name__ == "__main__":
    unittest.main()
