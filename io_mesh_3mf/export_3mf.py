# Blender add-on to import and export 3MF files.
# Copyright (C) 2020 Ghostkeeper
# Copyright (C) 2025 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# <pep8 compliant>

"""
3MF Export operator for Blender.

This module contains the main Export3MF operator that handles the UI and dispatches
to format-specific exporters (Standard, Orca Slicer, PrusaSlicer).
"""

import itertools
import logging
import mathutils
import zipfile
from typing import Optional, Set, Dict, List

import bpy
import bpy.props
import bpy.types
import bpy_extras.io_utils
import bpy_extras.node_shader_utils

from .constants import MODEL_NAMESPACE
from .extensions import ExtensionManager
from .export_utils import (
    create_archive as _create_archive,
    unit_scale as _unit_scale,
    check_non_manifold_geometry,
    write_materials as _write_materials,
    write_vertices as _write_vertices,
    write_triangles as _write_triangles,
    format_transformation as _format_transformation,
)
from .export_formats import (
    StandardExporter,
    OrcaExporter,
    PrusaExporter,
)

# IDE and Documentation support.
__all__ = ["Export3MF"]

log = logging.getLogger(__name__)


class Export3MF(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    """
    Operator that exports a 3MF file from Blender.
    """

    # Metadata.
    bl_idname = "export_mesh.threemf"
    bl_label = "Export 3MF"
    bl_description = "Save the current scene to 3MF"
    filename_ext = ".3mf"

    # Options for the user.
    filter_glob: bpy.props.StringProperty(default="*.3mf", options={"HIDDEN"})
    use_selection: bpy.props.BoolProperty(
        name="Selection Only",
        description="Export selected objects only.",
        default=False,
    )
    export_hidden: bpy.props.BoolProperty(
        name="Export hidden objects",
        description="Export objects hidden in the viewport",
        default=False,
    )
    global_scale: bpy.props.FloatProperty(
        name="Scale", default=1.0, soft_min=0.001, soft_max=1000.0, min=1e-6, max=1e6
    )
    use_mesh_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers",
        description="Apply the modifiers before saving.",
        default=True,
    )
    coordinate_precision: bpy.props.IntProperty(
        name="Precision",
        description="The number of decimal digits to use in coordinates in the file.",
        default=9,
        min=0,
        max=12,
    )
    use_orca_format: bpy.props.BoolProperty(
        name="Multi-Material Color Zones",
        description="Export per-face materials as multi-material filament zones for Orca Slicer, "
                    "BambuStudio, and PrusaSlicer. Each material color becomes a separate filament slot. "
                    "Compatible with multi-material printing workflows",
        default=False,
    )

    export_triangle_sets: bpy.props.BoolProperty(
        name="Export Triangle Sets",
        description="Export Blender face maps as 3MF triangle sets. "
                    "Triangle sets group triangles for selection workflows and property assignment. "
                    "Not compatible with multi-material color zone export.",
        default=False,
    )

    mmu_slicer_format: bpy.props.EnumProperty(
        name="Slicer Format",
        description="Target slicer format for multi-material export",
        items=[
            ('ORCA', "Orca Slicer / BambuStudio",
             "Use Production Extension with paint_color attributes (Orca Slicer, BambuStudio, Handy)"),
            ('PRUSA', "PrusaSlicer / SuperSlicer", "Use mmu_segmentation attributes (PrusaSlicer, SuperSlicer)"),
        ],
        default='ORCA',
    )

    def invoke(self, context, event):
        """Initialize properties from preferences when the export dialog is opened."""
        prefs = context.preferences.addons.get(__package__)
        if prefs and prefs.preferences:
            self.coordinate_precision = prefs.preferences.default_coordinate_precision
            self.export_hidden = prefs.preferences.default_export_hidden
            self.use_mesh_modifiers = prefs.preferences.default_apply_modifiers
            self.global_scale = prefs.preferences.default_global_scale
            self.use_orca_format = prefs.preferences.default_multi_material_export
            self.export_triangle_sets = prefs.preferences.default_export_triangle_sets
        self.report({'INFO'}, "Exporting, please wait...")
        return super().invoke(context, event)

    def draw(self, context):
        """Custom draw method for the export dialog."""
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        # Multi-color printing section
        orca_box = layout.box()
        orca_box.use_property_split = False
        orca_header = orca_box.row()
        orca_header.label(text="Multi-Color Printing", icon='COLORSET_01_VEC')
        orca_row = orca_box.row()
        orca_row.prop(self, "use_orca_format")
        if self.use_orca_format:
            # Slicer format dropdown
            format_row = orca_box.row()
            format_row.prop(self, "mmu_slicer_format", text="Slicer")

            # Tips
            info_col = orca_box.column(align=True)
            info_col.scale_y = 0.7
            info_col.label(text="Tip: Assign different materials to faces in Edit Mode", icon='INFO')
            info_col.label(text="Each unique color becomes a filament slot in your slicer")

        layout.separator()

        # Standard options
        layout.prop(self, "use_selection")
        layout.prop(self, "export_hidden")
        layout.prop(self, "use_mesh_modifiers")

        # Triangle Sets - disabled when using Multi-Material (not supported by slicers)
        triangle_sets_row = layout.row()
        triangle_sets_row.enabled = not self.use_orca_format
        triangle_sets_row.prop(self, "export_triangle_sets")
        layout.prop(self, "global_scale")
        layout.prop(self, "coordinate_precision")

    def safe_report(self, level: Set[str], message: str) -> None:
        """
        Safely report a message, using Blender's report system if available, otherwise just logging.
        This allows the class to work both as a Blender operator and in unit tests.
        :param level: The report level (e.g., {'ERROR'}, {'WARNING'}, {'INFO'})
        :param message: The message to report
        """
        if hasattr(self, 'report') and callable(getattr(self, 'report', None)):
            self.report(level, message)
        # If report is not available, the message has already been logged via the log module

    def _progress_begin(self, context: bpy.types.Context, message: str) -> None:
        self._progress_context = context
        self._progress_value = 0
        window_manager = getattr(context, "window_manager", None)
        if window_manager:
            if hasattr(window_manager, "progress_begin"):
                window_manager.progress_begin(0, 100)
            if hasattr(window_manager, "status_text_set"):
                window_manager.status_text_set(message)

    def _progress_update(self, value: int, message: Optional[str] = None) -> None:
        context = getattr(self, "_progress_context", None)
        if not context:
            return
        current_value = getattr(self, "_progress_value", 0)
        new_value = max(current_value, value)
        self._progress_value = new_value
        window_manager = getattr(context, "window_manager", None)
        if window_manager and hasattr(window_manager, "progress_update"):
            window_manager.progress_update(new_value)
        if message and window_manager and hasattr(window_manager, "status_text_set"):
            window_manager.status_text_set(message)

    def _progress_end(self) -> None:
        context = getattr(self, "_progress_context", None)
        if not context:
            return
        window_manager = getattr(context, "window_manager", None)
        if window_manager:
            if hasattr(window_manager, "progress_end"):
                window_manager.progress_end()
            if hasattr(window_manager, "status_text_set"):
                window_manager.status_text_set(None)
        self._progress_context = None

    def _finalize_export(self, archive: zipfile.ZipFile, format_name: str = "") -> Set[str]:
        """
        Finalize an export by closing the archive and reporting results.

        :param archive: The 3MF archive to close.
        :param format_name: Optional format suffix for log message
                            (e.g., "Orca-compatible ", "PrusaSlicer-compatible ").
        :return: {"FINISHED"} on success, {"CANCELLED"} on failure.
        """
        try:
            archive.close()
        except EnvironmentError as e:
            log.error(f"Unable to complete writing to 3MF archive: {e}")
            self.safe_report({'ERROR'}, f"Unable to complete writing to 3MF archive: {e}")
            return {"CANCELLED"}

        log.info(f"Exported {self.num_written} objects to {format_name}3MF archive {self.filepath}.")
        self.safe_report({'INFO'}, f"Exported {self.num_written} objects to {self.filepath}")
        return {"FINISHED"}

    def execute(self, context: bpy.types.Context) -> Set[str]:
        """
        The main routine that writes the 3MF archive.

        This function serves as a high-level overview of the steps involved to write a 3MF file.
        :param context: The Blender context.
        :return: A set of status flags to indicate whether the write succeeded or not.
        """
        self._progress_begin(context, "Exporting 3MF...")
        try:
            # Reset state.
            self.next_resource_id = 1  # Starts counting at 1 for some inscrutable reason.
            self.material_resource_id = "-1"
            self.num_written = 0
            self.vertex_colors = {}  # Maps color hex values to filament indices for Orca export
            self.orca_object_files = []  # List of (path, uuid) for each object model file
            self.material_name_to_index = {}

            # Initialize extension manager
            self.extension_manager = ExtensionManager()

            archive = _create_archive(self.filepath, self.safe_report)
            if archive is None:
                return {"CANCELLED"}

            self._progress_update(5, "Preparing export...")

            if self.use_selection:
                blender_objects = context.selected_objects
                # Validate that at least one mesh object is selected
                mesh_objects = [obj for obj in blender_objects if obj.type == 'MESH']
                if not mesh_objects:
                    self.safe_report(
                        {'ERROR'},
                        "No mesh objects selected. Select at least one mesh object to export."
                    )
                    log.error("Export cancelled: No mesh objects in selection")
                    return {"CANCELLED"}
            else:
                blender_objects = context.scene.objects

            # Check for non-manifold geometry before export
            mesh_objects = [obj for obj in blender_objects if obj.type == 'MESH']
            if mesh_objects:
                non_manifold_objects = check_non_manifold_geometry(mesh_objects, self.use_mesh_modifiers)
                if non_manifold_objects:
                    obj_names = ", ".join(non_manifold_objects[:3])
                    if len(non_manifold_objects) > 3:
                        obj_names += f", and {len(non_manifold_objects) - 3} more"
                    self.safe_report(
                        {'WARNING'},
                        f"Non-manifold geometry detected in: {obj_names}. "
                        "This may cause problems in slicers."
                    )
                    log.warning(f"Non-manifold geometry found in {len(non_manifold_objects)} object(s): "
                                f"{', '.join(non_manifold_objects)}")

            global_scale = _unit_scale(context, self.global_scale)

            # Dispatch to format-specific exporter
            if self.use_orca_format:
                if self.mmu_slicer_format == 'ORCA':
                    exporter = OrcaExporter(self)
                    return exporter.execute(context, archive, blender_objects, global_scale)
                elif self.mmu_slicer_format == 'PRUSA':
                    exporter = PrusaExporter(self)
                    return exporter.execute(context, archive, blender_objects, global_scale)

            # Standard 3MF export (original behavior)
            exporter = StandardExporter(self)
            return exporter.execute(context, archive, blender_objects, global_scale)
        finally:
            self._progress_end()

    # =========================================================================
    # Backward-compatible wrapper methods for unit tests
    # These methods delegate to the refactored utility modules while maintaining
    # the original method signatures that unit tests expect.
    # =========================================================================

    def create_archive(self, filepath: str):
        """Create a 3MF archive. Backward-compatible wrapper."""
        return _create_archive(filepath, self.safe_report)

    def unit_scale(self, context):
        """Calculate unit scale. Backward-compatible wrapper."""
        return _unit_scale(context, self.global_scale)

    def format_transformation(self, transformation: mathutils.Matrix) -> str:
        """Format transformation matrix. Backward-compatible wrapper."""
        return _format_transformation(transformation)

    def write_materials(self, resources_element, blender_objects) -> Dict[str, int]:
        """
        Write materials to the 3MF document. Backward-compatible wrapper.

        Returns just the name_to_index dict for backward compatibility.
        Updates self.next_resource_id and self.material_resource_id internally.
        """
        name_to_index, self.next_resource_id, self.material_resource_id = _write_materials(
            resources_element,
            blender_objects,
            self.use_orca_format,
            self.vertex_colors,
            self.next_resource_id
        )
        self.material_name_to_index = name_to_index
        return name_to_index

    def write_vertices(self, mesh_element, vertices) -> None:
        """Write vertices to mesh element. Backward-compatible wrapper."""
        _write_vertices(mesh_element, vertices, self.use_orca_format, self.coordinate_precision)

    def write_triangles(self, mesh_element, triangles, default_material, material_slots,
                        mesh=None, blender_object=None) -> None:
        """Write triangles to mesh element. Backward-compatible wrapper."""
        _write_triangles(
            mesh_element,
            triangles,
            default_material,
            material_slots,
            self.material_name_to_index,
            self.use_orca_format,
            getattr(self, 'mmu_slicer_format', 'ORCA'),
            self.vertex_colors,
            mesh,
            blender_object
        )

    def write_objects(self, root, resources_element, blender_objects, global_scale: float) -> None:
        """Write objects to 3MF document. Backward-compatible wrapper."""
        exporter = StandardExporter(self)
        exporter.write_objects(root, resources_element, blender_objects, global_scale)

    def write_object_resource(self, resources_element, blender_object):
        """Write a single object resource. Backward-compatible wrapper."""
        exporter = StandardExporter(self)
        return exporter.write_object_resource(resources_element, blender_object)
