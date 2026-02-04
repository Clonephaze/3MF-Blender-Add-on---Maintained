"""
Integration tests for 3MF Materials Extension v1.2.1.

Tests round-trip preservation of ALL Materials Extension elements:
- colorgroup / color
- texture2d
- texture2dgroup / tex2coord  
- compositematerials / composite
- multiproperties / multi
- pbmetallicdisplayproperties
- pbspeculardisplayproperties
- translucentdisplayproperties
- pbmetallictexturedisplayproperties
- pbspeculartexturedisplayproperties

Run with: blender --background --python tests/run_tests.py -- test_materials_extension
"""

import sys
import unittest
import bpy
import zipfile
import xml.etree.ElementTree as ET

from test_base import Blender3mfTestCase, get_temp_test_dir


# Namespaces
NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_MATERIAL = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"


class MaterialsExtensionTestCase(Blender3mfTestCase):
    """Base class for Materials Extension tests with helper methods."""
    
    def create_test_3mf(self, model_xml, textures=None):
        """Create a test 3MF file with given model XML and optional textures.
        
        Args:
            model_xml: The complete model XML string
            textures: Optional dict of {path: bytes} for texture files
            
        Returns:
            Path to created 3MF file
        """
        filepath = get_temp_test_dir() / f"test_materials_{id(self)}.3mf"
        
        content_types = '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />
  <Default Extension="png" ContentType="image/png" />
  <Default Extension="jpeg" ContentType="image/jpeg" />
</Types>'''
        
        rels = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Target="/3D/3dmodel.model" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" />
</Relationships>'''
        
        with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', content_types)
            archive.writestr('_rels/.rels', rels)
            archive.writestr('3D/3dmodel.model', model_xml)
            
            if textures:
                for path, data in textures.items():
                    archive.writestr(path, data)
        
        return filepath
    
    def extract_model_xml(self, filepath):
        """Extract and parse model XML from a 3MF file."""
        with zipfile.ZipFile(filepath, 'r') as archive:
            model_data = archive.read('3D/3dmodel.model')
            return ET.fromstring(model_data)
    
    def find_elements(self, root, local_name):
        """Find all elements matching a local name (ignoring namespace)."""
        results = []
        for elem in root.iter():
            tag = elem.tag
            if '}' in tag:
                tag = tag.split('}')[1]
            if tag == local_name:
                results.append(elem)
        return results
    
    def get_element_attribs(self, elem):
        """Get element attributes with local names (strip namespace prefixes)."""
        result = {}
        for key, value in elem.attrib.items():
            if '}' in key:
                key = key.split('}')[1]
            result[key] = value
        return result
    
    def import_3mf(self, filepath):
        """Import a 3MF file and return success status."""
        result = bpy.ops.import_mesh.threemf(filepath=str(filepath))
        return result == {'FINISHED'}
    
    def export_3mf(self, filepath):
        """Export to 3MF file and return success status."""
        result = bpy.ops.export_mesh.threemf(filepath=str(filepath))
        return result == {'FINISHED'}


# Minimal 1x1 red PNG for texture tests
MINIMAL_PNG = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
    0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
    0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
    0x54, 0x08, 0xD7, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
    0x00, 0x00, 0x03, 0x00, 0x01, 0x00, 0x18, 0xDD,
    0x8D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45,
    0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82
])


# =============================================================================
# Simple cube mesh for all material tests
# =============================================================================
CUBE_MESH = '''
    <object id="100" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0" />
          <vertex x="10" y="0" z="0" />
          <vertex x="10" y="10" z="0" />
          <vertex x="0" y="10" z="0" />
          <vertex x="0" y="0" z="10" />
          <vertex x="10" y="0" z="10" />
          <vertex x="10" y="10" z="10" />
          <vertex x="0" y="10" z="10" />
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2" />
          <triangle v1="0" v2="2" v3="3" />
          <triangle v1="4" v2="6" v3="5" />
          <triangle v1="4" v2="7" v3="6" />
          <triangle v1="0" v2="4" v3="5" />
          <triangle v1="0" v2="5" v3="1" />
          <triangle v1="1" v2="5" v3="6" />
          <triangle v1="1" v2="6" v3="2" />
          <triangle v1="2" v2="6" v3="7" />
          <triangle v1="2" v2="7" v3="3" />
          <triangle v1="3" v2="7" v3="4" />
          <triangle v1="3" v2="4" v3="0" />
        </triangles>
      </mesh>
    </object>
'''

# Cube mesh that references basematerials id="1" index 0
CUBE_MESH_WITH_MATERIAL = '''
    <object id="100" type="model" pid="1" pindex="0">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0" />
          <vertex x="10" y="0" z="0" />
          <vertex x="10" y="10" z="0" />
          <vertex x="0" y="10" z="0" />
          <vertex x="0" y="0" z="10" />
          <vertex x="10" y="0" z="10" />
          <vertex x="10" y="10" z="10" />
          <vertex x="0" y="10" z="10" />
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2" />
          <triangle v1="0" v2="2" v3="3" />
          <triangle v1="4" v2="6" v3="5" />
          <triangle v1="4" v2="7" v3="6" />
          <triangle v1="0" v2="4" v3="5" />
          <triangle v1="0" v2="5" v3="1" />
          <triangle v1="1" v2="5" v3="6" />
          <triangle v1="1" v2="6" v3="2" />
          <triangle v1="2" v2="6" v3="7" />
          <triangle v1="2" v2="7" v3="3" />
          <triangle v1="3" v2="7" v3="4" />
          <triangle v1="3" v2="4" v3="0" />
        </triangles>
      </mesh>
    </object>
'''


# =============================================================================
# Test: Active PBR Material Rendering (values applied to Blender materials)
# =============================================================================
class TestActivePBRMaterials(MaterialsExtensionTestCase):
    """Test that PBR display properties are actually applied to Blender materials."""
    
    def _get_principled_node(self, material):
        """Get the Principled BSDF node from a material."""
        if not material.use_nodes:
            return None
        for node in material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                return node
        return None
    
    def test_metallic_properties_applied(self):
        """Metallic properties should be applied to Principled BSDF."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1" displaypropertiesid="2">
      <base name="Gold" displaycolor="#FFD700" />
    </basematerials>
    <m:pbmetallicdisplayproperties id="2">
      <m:pbmetallic name="GoldMetal" metallicness="0.95" roughness="0.1" />
    </m:pbmetallicdisplayproperties>
    {CUBE_MESH_WITH_MATERIAL}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Find the Gold material
        gold_mat = None
        for mat in bpy.data.materials:
            if mat.name == "Gold":
                gold_mat = mat
                break
        
        self.assertIsNotNone(gold_mat, "Gold material not found")
        
        node = self._get_principled_node(gold_mat)
        self.assertIsNotNone(node, "Principled BSDF not found")
        
        # Check metallic value (should be 0.95)
        metallic = node.inputs['Metallic'].default_value
        self.assertAlmostEqual(metallic, 0.95, places=2,
                              msg=f"Metallic should be 0.95, got {metallic}")
        
        # Check roughness value (should be 0.1)
        roughness = node.inputs['Roughness'].default_value
        self.assertAlmostEqual(roughness, 0.1, places=2,
                              msg=f"Roughness should be 0.1, got {roughness}")
    
    def test_per_base_displaypropertiesid(self):
        """Per-base displaypropertiesid should override group-level."""
        # Define a mesh that uses both materials (Metal=index 0, Plastic=index 1)
        two_material_mesh = '''
    <object id="100" type="model" pid="1" pindex="0">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0" />
          <vertex x="10" y="0" z="0" />
          <vertex x="10" y="10" z="0" />
          <vertex x="0" y="10" z="0" />
          <vertex x="0" y="0" z="10" />
          <vertex x="10" y="0" z="10" />
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2" pid="1" p1="0" />
          <triangle v1="0" v2="2" v3="3" pid="1" p1="1" />
          <triangle v1="4" v2="5" v3="1" pid="1" p1="0" />
          <triangle v1="4" v2="1" v3="0" pid="1" p1="1" />
        </triangles>
      </mesh>
    </object>
'''
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1">
      <base name="Metal" displaycolor="#C0C0C0" displaypropertiesid="2" />
      <base name="Plastic" displaycolor="#FF0000" />
    </basematerials>
    <m:pbmetallicdisplayproperties id="2">
      <m:pbmetallic name="MetalProp" metallicness="1.0" roughness="0.2" />
    </m:pbmetallicdisplayproperties>
    {two_material_mesh}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Find materials
        metal_mat = plastic_mat = None
        for mat in bpy.data.materials:
            if mat.name == "Metal":
                metal_mat = mat
            elif mat.name == "Plastic":
                plastic_mat = mat
        
        self.assertIsNotNone(metal_mat, "Metal material not found")
        self.assertIsNotNone(plastic_mat, "Plastic material not found")
        
        # Metal should have metallic=1.0, roughness=0.2
        metal_node = self._get_principled_node(metal_mat)
        self.assertIsNotNone(metal_node, "Principled BSDF not found for Metal")
        self.assertAlmostEqual(metal_node.inputs['Metallic'].default_value, 1.0, places=2)
        self.assertAlmostEqual(metal_node.inputs['Roughness'].default_value, 0.2, places=2)
        
        # Plastic should have default metallic=0
        plastic_node = self._get_principled_node(plastic_mat)
        self.assertIsNotNone(plastic_node, "Principled BSDF not found for Plastic")
        self.assertAlmostEqual(plastic_node.inputs['Metallic'].default_value, 0.0, places=2)
    
    def test_translucent_properties_applied(self):
        """Translucent properties should be applied to Principled BSDF."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1" displaypropertiesid="2">
      <base name="Glass" displaycolor="#FFFFFF" />
    </basematerials>
    <m:translucentdisplayproperties id="2">
      <m:translucent name="GlassProp" attenuation="#FFFFFF80" refractiveindex="1.5" roughness="0.05" />
    </m:translucentdisplayproperties>
    {CUBE_MESH_WITH_MATERIAL}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Find the Glass material
        glass_mat = None
        for mat in bpy.data.materials:
            if mat.name == "Glass":
                glass_mat = mat
                break
        
        self.assertIsNotNone(glass_mat, "Glass material not found")
        
        node = self._get_principled_node(glass_mat)
        self.assertIsNotNone(node, "Principled BSDF not found")
        
        # Check IOR (should be 1.5)
        ior = node.inputs['IOR'].default_value
        self.assertAlmostEqual(ior, 1.5, places=2,
                              msg=f"IOR should be 1.5, got {ior}")
        
        # Check transmission (should be > 0 since we have translucent properties)
        # Blender 4.0+ uses 'Transmission Weight', earlier uses 'Transmission'
        trans_input = 'Transmission Weight' if 'Transmission Weight' in node.inputs else 'Transmission'
        transmission = node.inputs[trans_input].default_value
        self.assertGreater(transmission, 0, "Transmission should be set for translucent material")

    def test_textured_pbr_metallic_creates_nodes(self):
        """Textured PBR metallic properties should create texture nodes (displaypropertiesid on base)."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1">
      <base name="TexturedMetal" displaycolor="#AABBCC" displaypropertiesid="4" />
    </basematerials>
    <m:texture2d id="2" path="/3D/Texture/metal.png" contenttype="image/png" />
    <m:texture2d id="3" path="/3D/Texture/rough.png" contenttype="image/png" />
    <m:pbmetallictexturedisplayproperties id="4" name="MetalPBR"
        metallictextureid="2" roughnesstextureid="3"
        metallicfactor="1.0" roughnessfactor="0.5" />
    {CUBE_MESH_WITH_MATERIAL}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        textures = {
            '3D/Texture/metal.png': MINIMAL_PNG,
            '3D/Texture/rough.png': MINIMAL_PNG,
        }
        
        input_path = self.create_test_3mf(model_xml, textures)
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Find the TexturedMetal material
        mat = None
        for m in bpy.data.materials:
            if m.name == "TexturedMetal":
                mat = m
                break
        
        self.assertIsNotNone(mat, "TexturedMetal material not found")
        self.assertTrue(mat.use_nodes, "Material should use nodes")
        
        # Check for Image Texture nodes
        nodes = mat.node_tree.nodes
        tex_nodes = [n for n in nodes if n.type == 'TEX_IMAGE']
        
        self.assertGreaterEqual(len(tex_nodes), 1, 
            "Should have at least one Image Texture node for PBR textures")
        
        # Check that Principled BSDF has texture inputs connected
        principled = self._get_principled_node(mat)
        self.assertIsNotNone(principled, "Principled BSDF not found")
        
        # The Metallic and/or Roughness inputs should have links
        metallic_linked = principled.inputs['Metallic'].is_linked
        roughness_linked = principled.inputs['Roughness'].is_linked
        
        self.assertTrue(metallic_linked or roughness_linked,
            "At least one of Metallic or Roughness should have texture connected")

    def test_textured_pbr_metallic_group_level_displaypropertiesid(self):
        """Textured PBR with displaypropertiesid on basematerials (not on base) should work."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1" displaypropertiesid="4">
      <base name="TexturedPBR" displaycolor="#CCCCCC" />
    </basematerials>
    <m:texture2d id="2" path="/3D/Texture/basecolor.png" contenttype="image/png" />
    <m:texture2d id="3" path="/3D/Texture/roughness.png" contenttype="image/png" />
    <m:pbmetallictexturedisplayproperties id="4" name="TexturedPBR"
        basecolortextureid="2" roughnesstextureid="3" />
    {CUBE_MESH_WITH_MATERIAL}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        textures = {
            '3D/Texture/basecolor.png': MINIMAL_PNG,
            '3D/Texture/roughness.png': MINIMAL_PNG,
        }
        
        input_path = self.create_test_3mf(model_xml, textures)
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Find the TexturedPBR material
        mat = None
        for m in bpy.data.materials:
            if m.name == "TexturedPBR":
                mat = m
                break
        
        self.assertIsNotNone(mat, "TexturedPBR material not found")
        self.assertTrue(mat.use_nodes, "Material should use nodes")
        
        # Check for Image Texture nodes - should have at least 2 (basecolor and roughness)
        nodes = mat.node_tree.nodes
        tex_nodes = [n for n in nodes if n.type == 'TEX_IMAGE']
        
        self.assertGreaterEqual(len(tex_nodes), 2,
            f"Should have at least 2 Image Texture nodes for basecolor and roughness, got {len(tex_nodes)}")
        
        # Check that Principled BSDF has texture inputs connected
        principled = self._get_principled_node(mat)
        self.assertIsNotNone(principled, "Principled BSDF not found")
        
        # Base Color should have a texture link
        basecolor_linked = principled.inputs['Base Color'].is_linked
        self.assertTrue(basecolor_linked, "Base Color should have texture connected")
        
        # Roughness should have a texture link
        roughness_linked = principled.inputs['Roughness'].is_linked
        self.assertTrue(roughness_linked, "Roughness should have texture connected")


# =============================================================================
# Test: Colorgroup
# =============================================================================
class TestColorgroup(MaterialsExtensionTestCase):
    """Test colorgroup/color element round-trip."""
    
    def test_colorgroup_roundtrip(self):
        """Colorgroup with multiple colors should round-trip correctly."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:colorgroup id="1">
      <m:color color="#FF0000FF" />
      <m:color color="#00FF00FF" />
      <m:color color="#0000FFFF" />
      <m:color color="#FFFF00FF" />
      <m:color color="#FF00FFFF" />
    </m:colorgroup>
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        output_path = get_temp_test_dir() / "colorgroup_out.3mf"
        
        # Import
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Export
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        # Verify
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        orig_colorgroups = self.find_elements(original, 'colorgroup')
        exp_colorgroups = self.find_elements(exported, 'colorgroup')
        
        self.assertEqual(len(orig_colorgroups), len(exp_colorgroups),
                        "Colorgroup count mismatch")
        
        # Check colors
        orig_colors = self.find_elements(original, 'color')
        exp_colors = self.find_elements(exported, 'color')
        
        self.assertEqual(len(orig_colors), len(exp_colors),
                        "Color count mismatch")
        
        # Verify color values preserved
        orig_values = [self.get_element_attribs(c).get('color') for c in orig_colors]
        exp_values = [self.get_element_attribs(c).get('color') for c in exp_colors]
        
        self.assertEqual(sorted(orig_values), sorted(exp_values),
                        "Color values not preserved")


# =============================================================================
# Test: Texture2D and Texture2DGroup
# =============================================================================
class TestTexture2D(MaterialsExtensionTestCase):
    """Test texture2d and texture2dgroup element round-trip."""
    
    def test_texture2d_roundtrip(self):
        """Texture2d element should round-trip with correct attributes."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:texture2d id="1" path="/3D/Texture/diffuse.png" contenttype="image/png" 
                 tilestyleu="wrap" tilestylev="mirror" filter="auto" />
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml, {'3D/Texture/diffuse.png': MINIMAL_PNG})
        output_path = get_temp_test_dir() / "texture2d_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        orig_textures = self.find_elements(original, 'texture2d')
        exp_textures = self.find_elements(exported, 'texture2d')
        
        self.assertEqual(len(orig_textures), len(exp_textures),
                        "Texture2d count mismatch")
        
        # Check attributes
        orig_attrs = self.get_element_attribs(orig_textures[0])
        exp_attrs = self.get_element_attribs(exp_textures[0])
        
        self.assertEqual(orig_attrs.get('contenttype'), exp_attrs.get('contenttype'))
    
    def test_texture2dgroup_roundtrip(self):
        """Texture2dgroup with tex2coord elements should round-trip."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:texture2d id="1" path="/3D/Texture/test.png" contenttype="image/png" />
    <m:texture2dgroup id="2" texid="1">
      <m:tex2coord u="0.0" v="0.0" />
      <m:tex2coord u="1.0" v="0.0" />
      <m:tex2coord u="1.0" v="1.0" />
      <m:tex2coord u="0.0" v="1.0" />
      <m:tex2coord u="0.5" v="0.5" />
    </m:texture2dgroup>
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml, {'3D/Texture/test.png': MINIMAL_PNG})
        output_path = get_temp_test_dir() / "texture2dgroup_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        # Check texture2dgroup
        orig_groups = self.find_elements(original, 'texture2dgroup')
        exp_groups = self.find_elements(exported, 'texture2dgroup')
        
        self.assertEqual(len(orig_groups), len(exp_groups),
                        "Texture2dgroup count mismatch")
        
        # Check tex2coord
        orig_coords = self.find_elements(original, 'tex2coord')
        exp_coords = self.find_elements(exported, 'tex2coord')
        
        self.assertEqual(len(orig_coords), len(exp_coords),
                        "Tex2coord count mismatch")


# =============================================================================
# Test: Compositematerials (Passthrough)
# =============================================================================
class TestCompositeMaterials(MaterialsExtensionTestCase):
    """Test compositematerials/composite element round-trip."""
    
    def test_compositematerials_roundtrip(self):
        """Compositematerials should round-trip as passthrough data."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1">
      <base name="Red" displaycolor="#FF0000" />
      <base name="Blue" displaycolor="#0000FF" />
      <base name="Green" displaycolor="#00FF00" />
    </basematerials>
    <m:compositematerials id="2" matid="1" matindices="0 1 2">
      <m:composite values="0.5 0.3 0.2" />
      <m:composite values="0.33 0.33 0.34" />
      <m:composite values="1.0 0.0 0.0" />
    </m:compositematerials>
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        output_path = get_temp_test_dir() / "composite_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Verify passthrough data stored in scene
        scene = bpy.context.scene
        self.assertIn("3mf_compositematerials", scene.keys(),
                     "Composite materials not stored in scene")
        
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        # Check compositematerials
        orig_comp = self.find_elements(original, 'compositematerials')
        exp_comp = self.find_elements(exported, 'compositematerials')
        
        self.assertEqual(len(orig_comp), len(exp_comp),
                        "Compositematerials count mismatch")
        
        # Check attributes
        orig_attrs = self.get_element_attribs(orig_comp[0])
        exp_attrs = self.get_element_attribs(exp_comp[0])
        
        self.assertEqual(orig_attrs.get('matid'), exp_attrs.get('matid'))
        self.assertEqual(orig_attrs.get('matindices'), exp_attrs.get('matindices'))
        
        # Check composite children
        orig_composites = self.find_elements(orig_comp[0], 'composite')
        exp_composites = self.find_elements(exp_comp[0], 'composite')
        
        self.assertEqual(len(orig_composites), len(exp_composites),
                        "Composite count mismatch")
        
        # Verify values preserved
        for orig_c, exp_c in zip(orig_composites, exp_composites):
            orig_vals = self.get_element_attribs(orig_c).get('values')
            exp_vals = self.get_element_attribs(exp_c).get('values')
            self.assertEqual(orig_vals, exp_vals, "Composite values not preserved")


# =============================================================================
# Test: Multiproperties (Passthrough)
# =============================================================================
class TestMultiproperties(MaterialsExtensionTestCase):
    """Test multiproperties/multi element round-trip."""
    
    def test_multiproperties_roundtrip(self):
        """Multiproperties should round-trip as passthrough data."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1">
      <base name="Red" displaycolor="#FF0000" />
      <base name="Blue" displaycolor="#0000FF" />
    </basematerials>
    <m:colorgroup id="2">
      <m:color color="#FFFF00FF" />
      <m:color color="#FF00FFFF" />
    </m:colorgroup>
    <m:multiproperties id="3" pids="1 2" blendmethods="mix">
      <m:multi pindices="0 0" />
      <m:multi pindices="1 1" />
      <m:multi pindices="0 1" />
    </m:multiproperties>
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        output_path = get_temp_test_dir() / "multiprops_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Verify passthrough data stored in scene
        scene = bpy.context.scene
        self.assertIn("3mf_multiproperties", scene.keys(),
                     "Multiproperties not stored in scene")
        
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        # Check multiproperties
        orig_multi = self.find_elements(original, 'multiproperties')
        exp_multi = self.find_elements(exported, 'multiproperties')
        
        self.assertEqual(len(orig_multi), len(exp_multi),
                        "Multiproperties count mismatch")
        
        # Check attributes
        orig_attrs = self.get_element_attribs(orig_multi[0])
        exp_attrs = self.get_element_attribs(exp_multi[0])
        
        self.assertEqual(orig_attrs.get('pids'), exp_attrs.get('pids'))
        self.assertEqual(orig_attrs.get('blendmethods'), exp_attrs.get('blendmethods'))
        
        # Check multi children
        orig_multis = self.find_elements(orig_multi[0], 'multi')
        exp_multis = self.find_elements(exp_multi[0], 'multi')
        
        self.assertEqual(len(orig_multis), len(exp_multis),
                        "Multi count mismatch")
    
    def test_multiproperties_multiply_blend(self):
        """Multiproperties with multiply blendmethod should preserve."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1">
      <base name="White" displaycolor="#FFFFFF" />
    </basematerials>
    <m:texture2d id="2" path="/3D/Texture/tex.png" contenttype="image/png" />
    <m:texture2dgroup id="3" texid="2">
      <m:tex2coord u="0" v="0" />
      <m:tex2coord u="1" v="1" />
    </m:texture2dgroup>
    <m:multiproperties id="4" pids="1 3" blendmethods="multiply">
      <m:multi pindices="0 0" />
      <m:multi pindices="0 1" />
    </m:multiproperties>
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml, {'3D/Texture/tex.png': MINIMAL_PNG})
        output_path = get_temp_test_dir() / "multiprops_multiply_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        exported = self.extract_model_xml(output_path)
        exp_multi = self.find_elements(exported, 'multiproperties')
        
        self.assertEqual(len(exp_multi), 1)
        exp_attrs = self.get_element_attribs(exp_multi[0])
        self.assertEqual(exp_attrs.get('blendmethods'), 'multiply')

    def test_multiproperties_resolves_to_basematerial(self):
        """Triangles referencing multiproperties should get the underlying basematerial."""
        # This test mimics the 3MF Consortium multiprop-metallic.3mf structure
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <basematerials id="1">
      <base name="Metal" displaycolor="#C0C0C0" displaypropertiesid="9" />
    </basematerials>
    <m:texture2d id="2" path="/3D/Texture/star.png" contenttype="image/png" />
    <m:texture2dgroup id="4" texid="2">
      <m:tex2coord u="0" v="0" />
      <m:tex2coord u="1" v="0" />
      <m:tex2coord u="0" v="1" />
      <m:tex2coord u="1" v="1" />
    </m:texture2dgroup>
    <m:multiproperties id="6" pids="1 4">
      <m:multi pindices="0 0" />
      <m:multi pindices="0 1" />
      <m:multi pindices="0 2" />
      <m:multi pindices="0 3" />
    </m:multiproperties>
    <m:pbmetallicdisplayproperties id="9">
      <m:pbmetallic name="MetalProp" metallicness="1" roughness="0.2" />
    </m:pbmetallicdisplayproperties>
    <object id="100" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0" />
          <vertex x="10" y="0" z="0" />
          <vertex x="10" y="10" z="0" />
          <vertex x="0" y="10" z="0" />
          <vertex x="0" y="0" z="10" />
          <vertex x="10" y="0" z="10" />
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2" pid="6" p1="0" p2="1" p3="2" />
          <triangle v1="0" v2="2" v3="3" pid="6" p1="0" p2="2" p3="3" />
          <triangle v1="4" v2="5" v3="1" pid="6" p1="0" />
          <triangle v1="4" v2="1" v3="0" pid="6" p1="1" />
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml, {'3D/Texture/star.png': MINIMAL_PNG})
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Find the Metal material - it should exist because multiproperties resolved
        metal_mat = None
        for mat in bpy.data.materials:
            if mat.name == "Metal":
                metal_mat = mat
                break
        
        self.assertIsNotNone(metal_mat, 
            "Metal material not found - multiproperties should resolve to basematerial")
        
        # The material should have PBR properties applied (metallic=1, roughness=0.2)
        node = self._get_principled_node(metal_mat)
        self.assertIsNotNone(node, "Principled BSDF not found")
        
        metallic = node.inputs['Metallic'].default_value
        roughness = node.inputs['Roughness'].default_value
        
        self.assertAlmostEqual(metallic, 1.0, places=2,
            msg=f"Metal should have metallic=1.0 from displaypropertiesid, got {metallic}")
        self.assertAlmostEqual(roughness, 0.2, places=2,
            msg=f"Metal should have roughness=0.2 from displaypropertiesid, got {roughness}")
    
    def _get_principled_node(self, material):
        """Get the Principled BSDF node from a material."""
        if not material.use_nodes:
            return None
        for node in material.node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                return node
        return None


# =============================================================================
# Test: PB Metallic Display Properties
# =============================================================================
class TestPBMetallicDisplayProperties(MaterialsExtensionTestCase):
    """Test pbmetallicdisplayproperties element round-trip."""
    
    def test_pbmetallic_roundtrip(self):
        """PB metallic display properties should round-trip."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:pbmetallicdisplayproperties id="1">
      <m:pbmetallic name="Gold" metallicness="0.95" roughness="0.1" />
      <m:pbmetallic name="BrushedSteel" metallicness="0.8" roughness="0.4" />
      <m:pbmetallic name="Copper" metallicness="0.9" roughness="0.25" />
    </m:pbmetallicdisplayproperties>
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        output_path = get_temp_test_dir() / "pbmetallic_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        orig_props = self.find_elements(original, 'pbmetallicdisplayproperties')
        exp_props = self.find_elements(exported, 'pbmetallicdisplayproperties')
        
        self.assertEqual(len(orig_props), len(exp_props),
                        "PB metallic display properties count mismatch")
        
        # Check pbmetallic children
        orig_metals = self.find_elements(original, 'pbmetallic')
        exp_metals = self.find_elements(exported, 'pbmetallic')
        
        self.assertEqual(len(orig_metals), len(exp_metals),
                        "PB metallic count mismatch")


# =============================================================================
# Test: PB Specular Display Properties
# =============================================================================
class TestPBSpecularDisplayProperties(MaterialsExtensionTestCase):
    """Test pbspeculardisplayproperties element round-trip."""
    
    def test_pbspecular_roundtrip(self):
        """PB specular display properties should round-trip."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:pbspeculardisplayproperties id="1">
      <m:pbspecular name="Shiny" specularcolor="#FFFFFF" glossiness="0.9" />
      <m:pbspecular name="Matte" specularcolor="#808080" glossiness="0.2" />
    </m:pbspeculardisplayproperties>
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        output_path = get_temp_test_dir() / "pbspecular_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        orig_props = self.find_elements(original, 'pbspeculardisplayproperties')
        exp_props = self.find_elements(exported, 'pbspeculardisplayproperties')
        
        self.assertEqual(len(orig_props), len(exp_props),
                        "PB specular display properties count mismatch")


# =============================================================================
# Test: Translucent Display Properties
# =============================================================================
class TestTranslucentDisplayProperties(MaterialsExtensionTestCase):
    """Test translucentdisplayproperties element round-trip."""
    
    def test_translucent_roundtrip(self):
        """Translucent display properties should round-trip."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:translucentdisplayproperties id="1">
      <m:translucent name="Glass" attenuation="#FFFFFF80" refractiveindex="1.5" roughness="0.05" />
      <m:translucent name="Jade" attenuation="#00FF0040" refractiveindex="1.6" roughness="0.2" />
    </m:translucentdisplayproperties>
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        input_path = self.create_test_3mf(model_xml)
        output_path = get_temp_test_dir() / "translucent_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        orig_props = self.find_elements(original, 'translucentdisplayproperties')
        exp_props = self.find_elements(exported, 'translucentdisplayproperties')
        
        self.assertEqual(len(orig_props), len(exp_props),
                        "Translucent display properties count mismatch")


# =============================================================================
# Test: PB Metallic Textured Display Properties (Passthrough)
# =============================================================================
class TestPBMetallicTexturedDisplayProperties(MaterialsExtensionTestCase):
    """Test pbmetallictexturedisplayproperties element round-trip."""
    
    def test_pbmetallic_textured_roundtrip(self):
        """PB metallic textured display properties should round-trip."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:texture2d id="1" path="/3D/Texture/metal.png" contenttype="image/png" />
    <m:texture2d id="2" path="/3D/Texture/rough.png" contenttype="image/png" />
    <m:pbmetallictexturedisplayproperties id="3" name="TexturedMetal"
        metallictextureid="1" roughnesstextureid="2"
        basecolorfactor="#DDEEFF" metallicfactor="0.9" roughnessfactor="0.15" />
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        textures = {
            '3D/Texture/metal.png': MINIMAL_PNG,
            '3D/Texture/rough.png': MINIMAL_PNG,
        }
        
        input_path = self.create_test_3mf(model_xml, textures)
        output_path = get_temp_test_dir() / "pbmetallic_tex_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        
        # Verify passthrough data stored
        scene = bpy.context.scene
        self.assertIn("3mf_pbr_texture_displays", scene.keys(),
                     "PBR texture displays not stored in scene")
        
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        orig_props = self.find_elements(original, 'pbmetallictexturedisplayproperties')
        exp_props = self.find_elements(exported, 'pbmetallictexturedisplayproperties')
        
        self.assertEqual(len(orig_props), len(exp_props),
                        "PB metallic textured display properties count mismatch")
        
        # Check attributes
        orig_attrs = self.get_element_attribs(orig_props[0])
        exp_attrs = self.get_element_attribs(exp_props[0])
        
        self.assertEqual(orig_attrs.get('name'), exp_attrs.get('name'))
        self.assertEqual(orig_attrs.get('basecolorfactor'), exp_attrs.get('basecolorfactor'))
        self.assertEqual(orig_attrs.get('metallicfactor'), exp_attrs.get('metallicfactor'))
        self.assertEqual(orig_attrs.get('roughnessfactor'), exp_attrs.get('roughnessfactor'))


# =============================================================================
# Test: PB Specular Textured Display Properties (Passthrough)
# =============================================================================
class TestPBSpecularTexturedDisplayProperties(MaterialsExtensionTestCase):
    """Test pbspeculartexturedisplayproperties element round-trip."""
    
    def test_pbspecular_textured_roundtrip(self):
        """PB specular textured display properties should round-trip."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter">
  <resources>
    <m:texture2d id="1" path="/3D/Texture/specular.png" contenttype="image/png" />
    <m:texture2d id="2" path="/3D/Texture/gloss.png" contenttype="image/png" />
    <m:pbspeculartexturedisplayproperties id="3" name="ShinyTextured"
        speculartextureid="1" glossinesstextureid="2"
        diffusefactor="#AABBCC" specularfactor="#112233" glossinessfactor="0.85" />
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        textures = {
            '3D/Texture/specular.png': MINIMAL_PNG,
            '3D/Texture/gloss.png': MINIMAL_PNG,
        }
        
        input_path = self.create_test_3mf(model_xml, textures)
        output_path = get_temp_test_dir() / "pbspecular_tex_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        original = self.extract_model_xml(input_path)
        exported = self.extract_model_xml(output_path)
        
        orig_props = self.find_elements(original, 'pbspeculartexturedisplayproperties')
        exp_props = self.find_elements(exported, 'pbspeculartexturedisplayproperties')
        
        self.assertEqual(len(orig_props), len(exp_props),
                        "PB specular textured display properties count mismatch")
        
        # Check attributes
        orig_attrs = self.get_element_attribs(orig_props[0])
        exp_attrs = self.get_element_attribs(exp_props[0])
        
        self.assertEqual(orig_attrs.get('name'), exp_attrs.get('name'))
        self.assertEqual(orig_attrs.get('diffusefactor'), exp_attrs.get('diffusefactor'))
        self.assertEqual(orig_attrs.get('specularfactor'), exp_attrs.get('specularfactor'))
        self.assertEqual(orig_attrs.get('glossinessfactor'), exp_attrs.get('glossinessfactor'))


# =============================================================================
# Test: Combined Materials Extension (All Elements)
# =============================================================================
class TestCombinedMaterialsExtension(MaterialsExtensionTestCase):
    """Test all Materials Extension elements together."""
    
    def test_all_materials_extension_elements(self):
        """All Materials Extension elements should round-trip together."""
        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS_CORE}" xmlns:m="{NS_MATERIAL}" unit="millimeter"
       requiredextensions="m">
  <resources>
    <!-- Base materials (core spec) -->
    <basematerials id="1">
      <base name="Red" displaycolor="#FF0000" />
      <base name="Blue" displaycolor="#0000FF" />
      <base name="Green" displaycolor="#00FF00" />
    </basematerials>
    
    <!-- Colorgroup -->
    <m:colorgroup id="2">
      <m:color color="#FFFF00FF" />
      <m:color color="#FF00FFFF" />
      <m:color color="#00FFFFFF" />
    </m:colorgroup>
    
    <!-- Texture2d -->
    <m:texture2d id="3" path="/3D/Texture/diffuse.png" contenttype="image/png" />
    <m:texture2d id="4" path="/3D/Texture/normal.png" contenttype="image/png" />
    
    <!-- Texture2dgroup -->
    <m:texture2dgroup id="5" texid="3">
      <m:tex2coord u="0" v="0" />
      <m:tex2coord u="1" v="0" />
      <m:tex2coord u="1" v="1" />
      <m:tex2coord u="0" v="1" />
    </m:texture2dgroup>
    
    <!-- Compositematerials -->
    <m:compositematerials id="6" matid="1" matindices="0 1 2">
      <m:composite values="0.5 0.3 0.2" />
      <m:composite values="0.33 0.33 0.34" />
    </m:compositematerials>
    
    <!-- Multiproperties -->
    <m:multiproperties id="7" pids="1 2" blendmethods="mix">
      <m:multi pindices="0 0" />
      <m:multi pindices="1 1" />
    </m:multiproperties>
    
    <!-- PB Metallic Display Properties -->
    <m:pbmetallicdisplayproperties id="8">
      <m:pbmetallic name="Gold" metallicness="0.95" roughness="0.1" />
    </m:pbmetallicdisplayproperties>
    
    <!-- PB Specular Display Properties -->
    <m:pbspeculardisplayproperties id="9">
      <m:pbspecular name="Shiny" specularcolor="#FFFFFF" glossiness="0.9" />
    </m:pbspeculardisplayproperties>
    
    <!-- Translucent Display Properties -->
    <m:translucentdisplayproperties id="10">
      <m:translucent name="Glass" attenuation="#FFFFFF80" refractiveindex="1.5" roughness="0.05" />
    </m:translucentdisplayproperties>
    
    <!-- PB Metallic Textured Display Properties -->
    <m:pbmetallictexturedisplayproperties id="11" name="TexturedMetal"
        metallictextureid="3" roughnesstextureid="4"
        basecolorfactor="#DDEEFF" metallicfactor="0.9" roughnessfactor="0.15" />
    
    <!-- PB Specular Textured Display Properties -->
    <m:pbspeculartexturedisplayproperties id="12" name="ShinyTextured"
        speculartextureid="3" glossinesstextureid="4"
        diffusefactor="#AABBCC" specularfactor="#112233" glossinessfactor="0.85" />
    
    {CUBE_MESH}
  </resources>
  <build>
    <item objectid="100" />
  </build>
</model>'''
        
        textures = {
            '3D/Texture/diffuse.png': MINIMAL_PNG,
            '3D/Texture/normal.png': MINIMAL_PNG,
        }
        
        input_path = self.create_test_3mf(model_xml, textures)
        output_path = get_temp_test_dir() / "all_materials_out.3mf"
        
        self.assertTrue(self.import_3mf(input_path), "Import failed")
        self.assertTrue(self.export_3mf(output_path), "Export failed")
        
        exported = self.extract_model_xml(output_path)
        
        # Verify all element types are present in export
        element_types = [
            ('colorgroup', 1),
            ('color', 3),
            ('texture2d', 2),
            ('texture2dgroup', 1),
            ('tex2coord', 4),
            ('compositematerials', 1),
            ('composite', 2),
            ('multiproperties', 1),
            ('multi', 2),
            ('pbmetallicdisplayproperties', 1),
            ('pbmetallic', 1),
            ('pbspeculardisplayproperties', 1),
            ('pbspecular', 1),
            ('translucentdisplayproperties', 1),
            ('translucent', 1),
            ('pbmetallictexturedisplayproperties', 1),
            ('pbspeculartexturedisplayproperties', 1),
        ]
        
        for element_name, expected_count in element_types:
            exp_elements = self.find_elements(exported, element_name)
            self.assertEqual(
                len(exp_elements), expected_count,
                f"Expected {expected_count} {element_name} elements, found {len(exp_elements)}"
            )


if __name__ == '__main__':
    import sys
    
    # Run tests
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    sys.exit(0 if result.wasSuccessful() else 1)
