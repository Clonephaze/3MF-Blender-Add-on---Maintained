"""
Unit tests for ``io_mesh_3mf.common.types``.

Tests dataclass creation, defaults, equality, and hashing.
All types are pure Python — no bpy or mathutils required.
"""

import unittest
from io_mesh_3mf.common.types import (
    ResourceObject,
    Component,
    ResourceMaterial,
    ResourceTexture,
    ResourceTextureGroup,
    ResourceComposite,
    ResourceMultiproperties,
    ResourcePBRTextureDisplay,
    ResourceColorgroup,
    ResourcePBRDisplayProps,
)


class TestResourceObject(unittest.TestCase):
    """ResourceObject dataclass."""

    def test_create_minimal(self):
        obj = ResourceObject(vertices=[], triangles=[], materials={}, components=[])
        self.assertEqual(obj.vertices, [])
        self.assertEqual(obj.triangles, [])
        self.assertIsNone(obj.metadata)
        self.assertIsNone(obj.triangle_sets)
        self.assertIsNone(obj.triangle_uvs)
        self.assertIsNone(obj.segmentation_strings)
        self.assertIsNone(obj.default_extruder)

    def test_create_with_data(self):
        verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        tris = [(0, 1, 2)]
        obj = ResourceObject(
            vertices=verts,
            triangles=tris,
            materials={},
            components=[],
            default_extruder=2,
        )
        self.assertEqual(len(obj.vertices), 3)
        self.assertEqual(obj.default_extruder, 2)


class TestComponent(unittest.TestCase):
    """Component dataclass."""

    def test_create(self):
        comp = Component(resource_object="5")
        self.assertEqual(comp.resource_object, "5")
        self.assertIsNone(comp.transformation)
        self.assertIsNone(comp.path)

    def test_create_with_path(self):
        comp = Component(resource_object="3", path="/3D/Objects/part.model")
        self.assertEqual(comp.path, "/3D/Objects/part.model")


class TestResourceMaterial(unittest.TestCase):
    """ResourceMaterial dataclass with custom __eq__ and __hash__."""

    def test_defaults(self):
        mat = ResourceMaterial()
        self.assertIsNone(mat.name)
        self.assertIsNone(mat.color)
        self.assertIsNone(mat.metallic)
        self.assertIsNone(mat.roughness)
        self.assertIsNone(mat.texture_id)

    def test_equality_by_name_and_color(self):
        """Equality is based on (name, color) only."""
        a = ResourceMaterial(name="Red", color=(1.0, 0.0, 0.0, 1.0))
        b = ResourceMaterial(name="Red", color=(1.0, 0.0, 0.0, 1.0), metallic=0.5)
        self.assertEqual(a, b)

    def test_inequality(self):
        a = ResourceMaterial(name="Red", color=(1.0, 0.0, 0.0, 1.0))
        b = ResourceMaterial(name="Blue", color=(0.0, 0.0, 1.0, 1.0))
        self.assertNotEqual(a, b)

    def test_hashable(self):
        """Can be used as dict key / in sets."""
        mat = ResourceMaterial(name="X", color=(0.5, 0.5, 0.5, 1.0))
        d = {mat: "value"}
        self.assertEqual(d[mat], "value")

    def test_hash_stability(self):
        """Two equal materials hash the same."""
        a = ResourceMaterial(name="Y", color=(0.1, 0.2, 0.3, 1.0))
        b = ResourceMaterial(name="Y", color=(0.1, 0.2, 0.3, 1.0))
        self.assertEqual(hash(a), hash(b))

    def test_eq_not_implemented_for_other_types(self):
        mat = ResourceMaterial(name="X")
        self.assertIs(mat.__eq__("string"), NotImplemented)


class TestResourceTexture(unittest.TestCase):
    """ResourceTexture dataclass."""

    def test_defaults(self):
        tex = ResourceTexture(path="Textures/logo.png", contenttype="image/png")
        self.assertEqual(tex.tilestyleu, "wrap")
        self.assertEqual(tex.tilestylev, "wrap")
        self.assertEqual(tex.filter, "auto")
        self.assertIsNone(tex.blender_image)


class TestResourceTextureGroup(unittest.TestCase):
    """ResourceTextureGroup dataclass."""

    def test_defaults(self):
        tg = ResourceTextureGroup(texid="10")
        self.assertEqual(tg.texid, "10")
        self.assertEqual(tg.tex2coords, [])
        self.assertIsNone(tg.displaypropertiesid)

    def test_with_coords(self):
        tg = ResourceTextureGroup(
            texid="5", tex2coords=[(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]
        )
        self.assertEqual(len(tg.tex2coords), 3)


class TestResourceComposite(unittest.TestCase):
    """ResourceComposite dataclass."""

    def test_defaults(self):
        comp = ResourceComposite(matid="7")
        self.assertEqual(comp.matindices, "")
        self.assertEqual(comp.composites, [])
        self.assertIsNone(comp.displaypropertiesid)


class TestResourceMultiproperties(unittest.TestCase):
    """ResourceMultiproperties dataclass."""

    def test_defaults(self):
        mp = ResourceMultiproperties(pids="1 2 3")
        self.assertIsNone(mp.blendmethods)
        self.assertEqual(mp.multis, [])


class TestResourcePBRTextureDisplay(unittest.TestCase):
    """ResourcePBRTextureDisplay dataclass."""

    def test_metallic(self):
        pbr = ResourcePBRTextureDisplay(type="metallic", primary_texid="10")
        self.assertEqual(pbr.type, "metallic")
        self.assertEqual(pbr.factors, {})

    def test_specular_with_factors(self):
        pbr = ResourcePBRTextureDisplay(
            type="specular", factors={"glossiness": "0.8"}
        )
        self.assertEqual(pbr.factors["glossiness"], "0.8")


class TestResourceColorgroup(unittest.TestCase):
    """ResourceColorgroup dataclass."""

    def test_create(self):
        cg = ResourceColorgroup(colors=["#FF0000", "#00FF00", "#0000FF"])
        self.assertEqual(len(cg.colors), 3)
        self.assertIsNone(cg.displaypropertiesid)


class TestResourcePBRDisplayProps(unittest.TestCase):
    """ResourcePBRDisplayProps dataclass."""

    def test_create(self):
        props = ResourcePBRDisplayProps(type="translucent")
        self.assertEqual(props.type, "translucent")
        self.assertEqual(props.properties, [])

    def test_with_properties(self):
        props = ResourcePBRDisplayProps(
            type="metallic",
            properties=[{"metallicness": "0.9", "roughness": "0.1"}],
        )
        self.assertEqual(len(props.properties), 1)


if __name__ == "__main__":
    unittest.main()
