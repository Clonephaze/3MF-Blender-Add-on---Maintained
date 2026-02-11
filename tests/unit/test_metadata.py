"""
Unit tests for ``io_mesh_3mf.common.metadata``.

Tests the ``Metadata`` container: storage, retrieval, conflict resolution,
``__contains__``, ``__len__``, ``__bool__``, ``__eq__``, and ``__delitem__``.

The Metadata *class* itself is pure Python (namedtuples / dicts).
``store()`` and ``retrieve()`` need Blender objects so they are tested
at the integration level.
"""

import unittest
from io_mesh_3mf.common.metadata import Metadata, MetadataEntry


class TestMetadataBasics(unittest.TestCase):
    """Basic dict-like behaviour of Metadata."""

    def test_empty(self):
        m = Metadata()
        self.assertEqual(len(m), 0)
        self.assertFalse(m)

    def test_set_and_get(self):
        m = Metadata()
        entry = MetadataEntry(name="Title", preserve=True, datatype="xs:string", value="Cube")
        m["Title"] = entry
        self.assertIn("Title", m)
        self.assertEqual(m["Title"].value, "Cube")

    def test_len(self):
        m = Metadata()
        m["a"] = MetadataEntry(name="a", preserve=False, datatype="", value="1")
        m["b"] = MetadataEntry(name="b", preserve=False, datatype="", value="2")
        self.assertEqual(len(m), 2)

    def test_bool_true(self):
        m = Metadata()
        m["x"] = MetadataEntry(name="x", preserve=False, datatype="", value="v")
        self.assertTrue(m)

    def test_delete(self):
        m = Metadata()
        m["key"] = MetadataEntry(name="key", preserve=False, datatype="", value="val")
        del m["key"]
        self.assertNotIn("key", m)
        self.assertEqual(len(m), 0)


class TestMetadataConflicts(unittest.TestCase):
    """Conflict resolution when the same key is set twice."""

    def test_same_value_no_conflict(self):
        m = Metadata()
        e1 = MetadataEntry(name="k", preserve=False, datatype="xs:string", value="same")
        e2 = MetadataEntry(name="k", preserve=False, datatype="xs:string", value="same")
        m["k"] = e1
        m["k"] = e2
        self.assertIn("k", m)
        self.assertEqual(m["k"].value, "same")

    def test_different_value_creates_conflict(self):
        m = Metadata()
        e1 = MetadataEntry(name="k", preserve=False, datatype="xs:string", value="aaa")
        e2 = MetadataEntry(name="k", preserve=False, datatype="xs:string", value="bbb")
        m["k"] = e1
        m["k"] = e2
        # Key is now conflicting — __contains__ returns False
        self.assertNotIn("k", m)
        with self.assertRaises(KeyError):
            _ = m["k"]

    def test_different_datatype_creates_conflict(self):
        m = Metadata()
        e1 = MetadataEntry(name="k", preserve=False, datatype="xs:string", value="x")
        e2 = MetadataEntry(name="k", preserve=False, datatype="xs:int", value="x")
        m["k"] = e1
        m["k"] = e2
        self.assertNotIn("k", m)

    def test_conflict_is_sticky(self):
        """Once conflicted, adding again doesn't un-conflict."""
        m = Metadata()
        e1 = MetadataEntry(name="k", preserve=False, datatype="", value="a")
        e2 = MetadataEntry(name="k", preserve=False, datatype="", value="b")
        e3 = MetadataEntry(name="k", preserve=False, datatype="", value="a")
        m["k"] = e1
        m["k"] = e2  # conflict
        m["k"] = e3  # attempt to re-set
        self.assertNotIn("k", m)

    def test_conflicted_entry_excluded_from_len(self):
        m = Metadata()
        m["a"] = MetadataEntry(name="a", preserve=False, datatype="", value="1")
        m["b"] = MetadataEntry(name="b", preserve=False, datatype="", value="2")
        m["b"] = MetadataEntry(name="b", preserve=False, datatype="", value="3")  # conflict
        self.assertEqual(len(m), 1)


class TestMetadataPreserve(unittest.TestCase):
    """Preserve flag upgrade."""

    def test_preserve_upgraded(self):
        """If same value is set twice and second has preserve=True, upgrade to True."""
        m = Metadata()
        e1 = MetadataEntry(name="k", preserve=False, datatype="xs:string", value="v")
        e2 = MetadataEntry(name="k", preserve=True, datatype="xs:string", value="v")
        m["k"] = e1
        m["k"] = e2
        self.assertTrue(m["k"].preserve)

    def test_preserve_not_downgraded(self):
        """preserve=True should not be downgraded by a preserve=False entry."""
        m = Metadata()
        e1 = MetadataEntry(name="k", preserve=True, datatype="xs:string", value="v")
        e2 = MetadataEntry(name="k", preserve=False, datatype="xs:string", value="v")
        m["k"] = e1
        m["k"] = e2
        # Still True because the value hasn't changed — no upgrade happens,
        # but the existing entry already has preserve=True.
        self.assertTrue(m["k"].preserve)


class TestMetadataEquality(unittest.TestCase):

    def test_equal_empty(self):
        self.assertEqual(Metadata(), Metadata())

    def test_equal_with_data(self):
        m1, m2 = Metadata(), Metadata()
        entry = MetadataEntry(name="a", preserve=False, datatype="", value="1")
        m1["a"] = entry
        m2["a"] = entry
        self.assertEqual(m1, m2)

    def test_not_equal_different_data(self):
        m1, m2 = Metadata(), Metadata()
        m1["a"] = MetadataEntry(name="a", preserve=False, datatype="", value="1")
        m2["a"] = MetadataEntry(name="a", preserve=False, datatype="", value="2")
        self.assertNotEqual(m1, m2)

    def test_not_equal_to_non_metadata(self):
        m = Metadata()
        self.assertNotEqual(m, "string")


class TestMetadataValues(unittest.TestCase):

    def test_values_iterator(self):
        m = Metadata()
        m["x"] = MetadataEntry(name="x", preserve=False, datatype="", value="1")
        m["y"] = MetadataEntry(name="y", preserve=False, datatype="", value="2")
        vals = list(m.values())
        self.assertEqual(len(vals), 2)

    def test_values_skips_conflicts(self):
        m = Metadata()
        m["x"] = MetadataEntry(name="x", preserve=False, datatype="", value="1")
        m["x"] = MetadataEntry(name="x", preserve=False, datatype="", value="2")  # conflict
        vals = list(m.values())
        self.assertEqual(len(vals), 0)


if __name__ == "__main__":
    unittest.main()
