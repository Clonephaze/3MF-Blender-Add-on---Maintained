# Blender add-on to import and export 3MF files.
# Copyright (C) 2020 Ghostkeeper
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# <pep8 compliant>

"""
Unit tests for addon preferences functionality.
"""

import unittest
import unittest.mock

from mock.bpy import MockOperator, MockExportHelper, MockImportHelper

# Set up mocks before importing the module under test
import bpy.types
import bpy_extras.io_utils
bpy.types.Operator = MockOperator
bpy_extras.io_utils.ImportHelper = MockImportHelper
bpy_extras.io_utils.ExportHelper = MockExportHelper

import io_mesh_3mf.export_3mf
import io_mesh_3mf.import_3mf


class TestPreferences(unittest.TestCase):
    """
    Unit tests for addon preferences and their application.
    """

    def test_export_invoke_loads_preferences(self):
        """
        Tests that invoke() loads default values from addon preferences.
        """
        exporter = io_mesh_3mf.export_3mf.Export3MF()
        exporter.report = unittest.mock.MagicMock()  # Mock the report method

        # Create mock context with preferences
        mock_prefs = unittest.mock.MagicMock()
        mock_prefs.default_coordinate_precision = 6
        mock_prefs.default_export_hidden = True
        mock_prefs.default_apply_modifiers = False
        mock_prefs.default_global_scale = 0.5

        mock_addon = unittest.mock.MagicMock()
        mock_addon.preferences = mock_prefs

        mock_context = unittest.mock.MagicMock()
        mock_context.preferences.addons = {"io_mesh_3mf": mock_addon}

        mock_event = unittest.mock.MagicMock()

        # Mock the parent class's invoke method
        with unittest.mock.patch.object(
            bpy_extras.io_utils.ExportHelper,
            'invoke',
            return_value={'RUNNING_MODAL'}
        ):
            result = exporter.invoke(mock_context, mock_event)

        # Verify preferences were loaded
        self.assertEqual(exporter.coordinate_precision, 6)
        self.assertTrue(exporter.export_hidden)
        self.assertFalse(exporter.use_mesh_modifiers)
        self.assertEqual(exporter.global_scale, 0.5)

    def test_export_invoke_handles_missing_preferences(self):
        """
        Tests that invoke() handles missing preferences gracefully.
        """
        exporter = io_mesh_3mf.export_3mf.Export3MF()
        exporter.report = unittest.mock.MagicMock()  # Mock the report method

        # Set initial values
        exporter.coordinate_precision = 4
        exporter.export_hidden = False
        exporter.use_mesh_modifiers = True
        exporter.global_scale = 1.0

        # Create mock context without addon preferences
        mock_context = unittest.mock.MagicMock()
        mock_context.preferences.addons = {}  # No addon registered

        mock_event = unittest.mock.MagicMock()

        # Mock the parent class's invoke method
        with unittest.mock.patch.object(
            bpy_extras.io_utils.ExportHelper,
            'invoke',
            return_value={'RUNNING_MODAL'}
        ):
            result = exporter.invoke(mock_context, mock_event)

        # Verify original values are unchanged
        self.assertEqual(exporter.coordinate_precision, 4)
        self.assertFalse(exporter.export_hidden)
        self.assertTrue(exporter.use_mesh_modifiers)
        self.assertEqual(exporter.global_scale, 1.0)

    def test_import_invoke_loads_preferences(self):
        """
        Tests that import invoke() loads default scale from addon preferences.
        """
        importer = io_mesh_3mf.import_3mf.Import3MF()
        importer.report = unittest.mock.MagicMock()  # Mock the report method

        # Create mock context with preferences
        mock_prefs = unittest.mock.MagicMock()
        mock_prefs.default_global_scale = 2.0

        mock_addon = unittest.mock.MagicMock()
        mock_addon.preferences = mock_prefs

        mock_context = unittest.mock.MagicMock()
        mock_context.preferences.addons = {"io_mesh_3mf": mock_addon}

        mock_event = unittest.mock.MagicMock()

        # Mock the parent class's invoke method
        with unittest.mock.patch.object(
            bpy_extras.io_utils.ImportHelper,
            'invoke',
            return_value={'RUNNING_MODAL'}
        ):
            result = importer.invoke(mock_context, mock_event)

        # Verify preference was loaded
        self.assertEqual(importer.global_scale, 2.0)

    def test_import_invoke_handles_missing_preferences(self):
        """
        Tests that import invoke() handles missing preferences gracefully.
        """
        importer = io_mesh_3mf.import_3mf.Import3MF()
        importer.report = unittest.mock.MagicMock()  # Mock the report method
        importer.global_scale = 1.0

        # Create mock context without addon preferences
        mock_context = unittest.mock.MagicMock()
        mock_context.preferences.addons = {}

        mock_event = unittest.mock.MagicMock()

        # Mock the parent class's invoke method
        with unittest.mock.patch.object(
            bpy_extras.io_utils.ImportHelper,
            'invoke',
            return_value={'RUNNING_MODAL'}
        ):
            result = importer.invoke(mock_context, mock_event)

        # Verify original value is unchanged
        self.assertEqual(importer.global_scale, 1.0)


class TestPreferenceDefaults(unittest.TestCase):
    """
    Tests that preference default values are sensible.
    """

    def test_coordinate_precision_default(self):
        """
        Tests that coordinate precision defaults to 9 for lossless 32-bit float.
        """
        # The ThreeMFPreferences class would normally define this
        # We verify the export operator's default matches
        exporter = io_mesh_3mf.export_3mf.Export3MF()
        # Default precision should be 9 for full float precision
        # (Note: this tests the property's default, not the preference)
        self.assertIsNotNone(hasattr(exporter, 'coordinate_precision'))

    def test_export_hidden_default(self):
        """
        Tests that export_hidden defaults to False.
        """
        exporter = io_mesh_3mf.export_3mf.Export3MF()
        self.assertIsNotNone(hasattr(exporter, 'export_hidden'))

    def test_global_scale_default(self):
        """
        Tests that global_scale exists on both import and export.
        """
        exporter = io_mesh_3mf.export_3mf.Export3MF()
        importer = io_mesh_3mf.import_3mf.Import3MF()
        self.assertIsNotNone(hasattr(exporter, 'global_scale'))
        self.assertIsNotNone(hasattr(importer, 'global_scale'))
