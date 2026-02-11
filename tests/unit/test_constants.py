"""
Unit tests for ``io_mesh_3mf.common.constants``.

Validates that namespace URIs, MIME types, and other spec-level constants
have the correct values and types.  Pure Python — no bpy.
"""

import unittest
from io_mesh_3mf.common.constants import (
    SPEC_VERSION,
    SUPPORTED_EXTENSIONS,
    MODEL_LOCATION,
    CONTENT_TYPES_LOCATION,
    RELS_FOLDER,
    RELS_MIMETYPE,
    MODEL_MIMETYPE,
    TEXTURE_MIMETYPE_PNG,
    TEXTURE_MIMETYPE_JPEG,
    MODEL_NAMESPACE,
    SLIC3RPE_NAMESPACE,
    TRIANGLE_SETS_NAMESPACE,
    MODEL_NAMESPACES,
    MODEL_DEFAULT_UNIT,
    MATERIAL_NAMESPACE,
    PRODUCTION_NAMESPACE,
    BAMBU_NAMESPACE,
)


class TestSpecVersion(unittest.TestCase):

    def test_is_string(self):
        self.assertIsInstance(SPEC_VERSION, str)

    def test_has_three_components(self):
        parts = SPEC_VERSION.split(".")
        self.assertEqual(len(parts), 3, f"Expected 'major.minor.patch', got '{SPEC_VERSION}'")


class TestSupportedExtensions(unittest.TestCase):

    def test_is_set(self):
        self.assertIsInstance(SUPPORTED_EXTENSIONS, set)

    def test_contains_material_namespace(self):
        self.assertIn(MATERIAL_NAMESPACE, SUPPORTED_EXTENSIONS)

    def test_contains_production_namespace(self):
        self.assertIn(PRODUCTION_NAMESPACE, SUPPORTED_EXTENSIONS)

    def test_contains_triangle_sets_namespace(self):
        self.assertIn(TRIANGLE_SETS_NAMESPACE, SUPPORTED_EXTENSIONS)


class TestFileLocations(unittest.TestCase):

    def test_model_location(self):
        self.assertEqual(MODEL_LOCATION, "3D/3dmodel.model")

    def test_content_types_location(self):
        self.assertEqual(CONTENT_TYPES_LOCATION, "[Content_Types].xml")

    def test_rels_folder(self):
        self.assertEqual(RELS_FOLDER, "_rels")


class TestMIMETypes(unittest.TestCase):

    def test_model_mimetype(self):
        self.assertIn("3dmanufacturing", MODEL_MIMETYPE)

    def test_rels_mimetype(self):
        self.assertIn("relationships", RELS_MIMETYPE)

    def test_png(self):
        self.assertEqual(TEXTURE_MIMETYPE_PNG, "image/png")

    def test_jpeg(self):
        self.assertEqual(TEXTURE_MIMETYPE_JPEG, "image/jpeg")


class TestNamespaces(unittest.TestCase):

    def test_model_namespace_is_url(self):
        self.assertTrue(MODEL_NAMESPACE.startswith("http"))

    def test_material_namespace(self):
        self.assertIn("material", MATERIAL_NAMESPACE)

    def test_production_namespace(self):
        self.assertIn("production", PRODUCTION_NAMESPACE)

    def test_slic3rpe_namespace(self):
        self.assertIn("slic3r", SLIC3RPE_NAMESPACE)

    def test_bambu_namespace(self):
        self.assertIn("bambulab", BAMBU_NAMESPACE)

    def test_model_namespaces_dict(self):
        self.assertIsInstance(MODEL_NAMESPACES, dict)
        self.assertIn("3mf", MODEL_NAMESPACES)
        self.assertEqual(MODEL_NAMESPACES["3mf"], MODEL_NAMESPACE)


class TestDefaults(unittest.TestCase):

    def test_default_unit(self):
        self.assertEqual(MODEL_DEFAULT_UNIT, "millimeter")


if __name__ == "__main__":
    unittest.main()
