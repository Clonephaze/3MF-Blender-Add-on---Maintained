"""
Integration tests using official 3MF Consortium sample files.

These tests verify that the addon can correctly import and export
real-world 3MF files from the official 3MF Consortium samples repository:
https://github.com/3MFConsortium/3mf-samples

This ensures compatibility with known-good files that conform to the spec.

License: BSD 2-Clause (3MF Consortium)
Copyright (c) 2018, 3MF Consortium
See tests/resources/3mf_consortium/LICENSE for full license terms.
"""

import bpy
import os
import tempfile
import zipfile
from pathlib import Path

from test_base import Blender3mfTestCase


class Test3MFConsortiumSamples(Blender3mfTestCase):
    """Test import/export of official 3MF Consortium sample files."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class with paths to sample files."""
        super().setUpClass()
        cls.samples_dir = Path(__file__).parent.parent / "resources" / "3mf_consortium"
        
    def _get_model_content(self, filepath):
        """Extract the main model XML content from a 3MF file."""
        with zipfile.ZipFile(filepath, 'r') as archive:
            # Find the model file
            model_path = None
            for name in archive.namelist():
                if name.lower().endswith('.model') and '3d' in name.lower():
                    model_path = name
                    break
            
            if model_path:
                return archive.read(model_path).decode('utf-8')
        return None
    
    def _count_materials_elements(self, xml_content):
        """Count Materials Extension elements in XML content using regex (handles namespace prefixes)."""
        import re
        counts = {
            'colorgroup': 0,
            'texture2d': 0,
            'texture2dgroup': 0,
            'compositematerials': 0,
            'multiproperties': 0,
            'pbmetallicdisplayproperties': 0,
            'pbspeculardisplayproperties': 0,
            'translucentdisplayproperties': 0,
        }
        
        # Use regex to count elements (handles namespace prefixes like m:colorgroup)
        for elem_name in counts:
            # Match <m:element, <element (without namespace), or {namespace}element
            pattern = rf'<(?:\w+:)?{elem_name}[\s>]'
            matches = re.findall(pattern, xml_content, re.IGNORECASE)
            counts[elem_name] = len(matches)
        
        return counts
    
    def _test_sample_file_roundtrip(self, filename, expected_elements=None):
        """
        Test that a sample file can be imported and exported with Materials Extension data preserved.
        
        Args:
            filename: Name of the 3MF file in the samples directory
            expected_elements: List of element names we expect to find (optional)
        """
        sample_path = self.samples_dir / filename
        
        if not sample_path.exists():
            self.skipTest(f"Sample file not found: {filename}")
        
        # Get original counts
        original_content = self._get_model_content(sample_path)
        self.assertIsNotNone(original_content, "Could not read model content")
        
        original_counts = self._count_materials_elements(original_content)
        
        # Verify expected elements are present
        if expected_elements:
            for elem in expected_elements:
                self.assertGreater(original_counts.get(elem, 0), 0, 
                                   f"Expected {elem} in {filename} but found none")
        
        # Clear scene
        bpy.ops.wm.read_factory_settings(use_empty=True)
        
        # Import the file
        result = bpy.ops.import_mesh.threemf(filepath=str(sample_path))
        self.assertEqual(result, {'FINISHED'}, f"Import failed for {filename}")
        
        # Verify objects were imported
        imported_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
        self.assertGreater(len(imported_objects), 0, f"No mesh objects imported from {filename}")
        
        # Export to temp file
        with tempfile.NamedTemporaryFile(suffix='.3mf', delete=False) as tmp:
            export_path = tmp.name
        
        try:
            result = bpy.ops.export_mesh.threemf(filepath=export_path)
            self.assertEqual(result, {'FINISHED'}, f"Export failed for {filename}")
            
            # Get exported counts
            exported_content = self._get_model_content(export_path)
            self.assertIsNotNone(exported_content, "Could not read exported model content")
            
            exported_counts = self._count_materials_elements(exported_content)
            
            # Verify Materials Extension elements were preserved
            for elem_name, original_count in original_counts.items():
                if original_count > 0:
                    self.assertGreaterEqual(
                        exported_counts[elem_name], 
                        original_count,
                        f"{elem_name} count decreased: original={original_count}, exported={exported_counts[elem_name]}"
                    )
        finally:
            if os.path.exists(export_path):
                os.unlink(export_path)
    
    # Individual test methods for each sample file
    
    def test_dodeca_chain_loop_color(self):
        """Test colorgroup sample file."""
        self._test_sample_file_roundtrip(
            "dodeca_chain_loop_color.3mf",
            expected_elements=['colorgroup']
        )
    
    def test_pyramid_vertexcolor(self):
        """Test vertex color (colorgroup) sample file."""
        self._test_sample_file_roundtrip(
            "pyramid_vertexcolor.3mf",
            expected_elements=['colorgroup']
        )
    
    def test_multipletextures(self):
        """Test multiple textures sample file."""
        self._test_sample_file_roundtrip(
            "multipletextures.3mf",
            expected_elements=['texture2d', 'texture2dgroup']
        )
    
    def test_sphere_logo(self):
        """Test textured sphere with colorgroup sample file."""
        self._test_sample_file_roundtrip(
            "sphere_logo.3mf",
            expected_elements=['colorgroup', 'texture2d', 'texture2dgroup']
        )
    
    def test_sphere_logo_opc_texture_structure(self):
        """Verify sphere_logo export has correct OPC texture packaging.
        
        Catches five bugs fixed in the texture export pipeline:
        1. [Content_Types].xml must use the 3MF texture OPC content type, not image/png
        2. Texture files must exist at the paths referenced in model XML
        3. 3D/_rels/3dmodel.model.rels must exist with texture relationships
        4. Rels XML must not have ns0: namespace prefixes
        5. Rels targets must point to actual archive entries
        """
        import xml.etree.ElementTree as ET
        from io_mesh_3mf.common.constants import TEXTURE_REL, RELS_NAMESPACE
        
        sample_path = self.samples_dir / "sphere_logo.3mf"
        if not sample_path.exists():
            self.skipTest("sphere_logo.3mf not found")
        
        TEXTURE_OPC_TYPE = (
            "application/vnd.ms-package.3dmanufacturing-3dmodeltexture"
        )
        
        # Check original file has the 3MF texture content type
        with zipfile.ZipFile(sample_path, 'r') as orig:
            orig_ct = orig.read('[Content_Types].xml').decode('utf-8')
            self.assertIn(TEXTURE_OPC_TYPE, orig_ct,
                          "Original sphere_logo.3mf should use 3MF texture OPC content type")
        
        # Import
        bpy.ops.wm.read_factory_settings(use_empty=True)
        result = bpy.ops.import_mesh.threemf(filepath=str(sample_path))
        self.assertEqual(result, {'FINISHED'}, "Import failed")
        
        # Export
        with tempfile.NamedTemporaryFile(suffix='.3mf', delete=False) as tmp:
            export_path = tmp.name
        
        try:
            result = bpy.ops.export_mesh.threemf(filepath=export_path)
            self.assertEqual(result, {'FINISHED'}, "Export failed")
            
            with zipfile.ZipFile(export_path, 'r') as archive:
                names = archive.namelist()
                
                # 1. Texture files should be present in the archive
                texture_files = [
                    n for n in names
                    if n.lower().endswith(('.png', '.jpg', '.jpeg'))
                    and '3d/' in n.lower()
                    and 'thumbnail' not in n.lower()
                    and '_rels' not in n.lower()
                ]
                self.assertGreater(
                    len(texture_files), 0,
                    "No texture files found in exported archive"
                )
                
                # 2. [Content_Types].xml must preserve the 3MF texture content type
                ct_xml = archive.read('[Content_Types].xml').decode('utf-8')
                self.assertIn(
                    TEXTURE_OPC_TYPE, ct_xml,
                    "[Content_Types].xml should preserve the 3MF texture OPC content type, "
                    "not replace it with image/png"
                )
                
                # 3. Texture relationships file must exist
                rels_path = '3D/_rels/3dmodel.model.rels'
                self.assertIn(
                    rels_path, names,
                    "3D/_rels/3dmodel.model.rels missing — texture relationships not written"
                )
                
                # 4. Rels XML must not have ns0: prefix
                rels_xml = archive.read(rels_path).decode('utf-8')
                self.assertNotIn(
                    'ns0:', rels_xml,
                    "Relationship XML contains ns0: prefix (namespace bug)"
                )
                
                # 5. Rels must declare TEXTURE_REL type
                self.assertIn(
                    TEXTURE_REL, rels_xml,
                    "Texture relationship type URI not found in rels file"
                )
                
                # 6. Every texture target in rels must exist in the archive
                rels_root = ET.fromstring(rels_xml.encode('utf-8'))
                for rel in rels_root.findall(f'{{{RELS_NAMESPACE}}}Relationship'):
                    target = rel.get('Target', '')
                    archive_entry = target.lstrip('/')
                    self.assertIn(
                        archive_entry, names,
                        f"Rels references '{target}' but it's not in the archive"
                    )
        finally:
            if os.path.exists(export_path):
                os.unlink(export_path)
    
    def test_multiprop_opaque(self):
        """Test multiproperties with opaque materials sample file."""
        self._test_sample_file_roundtrip(
            "multiprop-opaque.3mf",
            expected_elements=['colorgroup', 'texture2d', 'texture2dgroup', 'multiproperties']
        )
    
    def test_multiprop_metallic(self):
        """Test multiproperties with metallic PBR sample file.
        
        NOTE: The original file from 3MF Consortium had a typo (ms: instead of m: prefix).
        We've fixed this locally to enable testing of pbmetallicdisplayproperties.
        """
        self._test_sample_file_roundtrip(
            "multiprop-metallic.3mf",
            expected_elements=['texture2d', 'texture2dgroup', 'multiproperties', 'pbmetallicdisplayproperties']
        )
    
    def test_multiprop_translucent(self):
        """Test multiproperties with translucent materials sample file.
        
        NOTE: The original file from 3MF Consortium had a typo (ms: instead of m: prefix).
        We've fixed this locally to enable testing of translucentdisplayproperties.
        """
        self._test_sample_file_roundtrip(
            "multiprop-translucent.3mf",
            expected_elements=['texture2d', 'texture2dgroup', 'multiproperties', 'translucentdisplayproperties']
        )


if __name__ == '__main__':
    import unittest
    
    # Create a test suite with verbose output
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(Test3MFConsortiumSamples)
    
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)
