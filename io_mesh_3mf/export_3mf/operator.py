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

This module contains the main ``Export3MF`` operator that handles the UI and
dispatches to format-specific exporters (Standard, Orca Slicer, PrusaSlicer).
"""

from __future__ import annotations

import mathutils
from typing import Dict, Set

import bpy
import bpy.props
import bpy.types
import bpy_extras.io_utils

from ..common.extensions import ExtensionManager
from ..common.logging import warn, error
from ..common.units import export_unit_scale

from .archive import create_archive
from .components import collect_mesh_objects
from .context import ExportContext, ExportOptions
from .geometry import check_non_manifold_geometry
from .orca import OrcaExporter
from .prusa import PrusaExporter
from .standard import StandardExporter

# IDE and Documentation support.
__all__ = ["Export3MF"]


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
    use_orca_format: bpy.props.EnumProperty(
        name="Material Export Mode",
        description="How to export material and color data",
        items=[
            (
                "STANDARD",
                "Standard 3MF",
                "Export geometry with material colors when present (spec-compliant)",
            ),
            (
                "PAINT",
                "Paint Segmentation",
                "Export UV-painted regions as hash segmentation for multi-material printing"
                " (experimental, may be slow)",
            ),
        ],
        default="STANDARD",
    )

    use_components: bpy.props.BoolProperty(
        name="Use Components",
        description="Export linked duplicates as component instances for smaller file sizes. "
        "When objects share the same mesh data (Alt+D duplicates), the mesh is exported "
        "once and referenced multiple times. Dramatically reduces file size for assemblies "
        "with repeated parts.",
        default=True,
    )

    mmu_slicer_format: bpy.props.EnumProperty(
        name="Slicer Format",
        description="Target slicer format for multi-material export",
        items=[
            (
                "ORCA",
                "Orca Slicer / BambuStudio",
                "Use Production Extension with paint_color attributes (Orca Slicer, BambuStudio, Handy)",
            ),
            (
                "PRUSA",
                "PrusaSlicer / SuperSlicer",
                "Use mmu_segmentation attributes (PrusaSlicer, SuperSlicer)",
            ),
        ],
        default="ORCA",
    )

    subdivision_depth: bpy.props.IntProperty(
        name="Subdivision Depth",
        description=(
            "Maximum recursive subdivision depth for paint segmentation export. "
            "Higher values capture finer color boundaries but increase export time. "
            "Each level quadruples the potential leaf nodes per triangle"
        ),
        default=7,
        min=4,
        max=10,
    )

    def invoke(self, context, event):
        """Initialize properties from preferences when the export dialog is opened."""
        prefs = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
        if prefs and prefs.preferences:
            self.coordinate_precision = prefs.preferences.default_coordinate_precision
            self.export_hidden = prefs.preferences.default_export_hidden
            self.use_mesh_modifiers = prefs.preferences.default_apply_modifiers
            self.global_scale = prefs.preferences.default_global_scale
            self.use_orca_format = prefs.preferences.default_multi_material_export
            self.subdivision_depth = prefs.preferences.default_subdivision_depth
        self.report({"INFO"}, "Exporting, please wait...")
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
        orca_header.label(text="Multi-Color Printing", icon="COLORSET_01_VEC")
        orca_row = orca_box.row()
        orca_row.prop(self, "use_orca_format")
        if self.use_orca_format == "PAINT":
            # Slicer format dropdown — only relevant for MMU paint export
            format_row = orca_box.row()
            format_row.prop(self, "mmu_slicer_format", text="Slicer")
            depth_row = orca_box.row()
            depth_row.prop(self, "subdivision_depth")

        # Tips for material modes
        if self.use_orca_format == "STANDARD":
            info_col = orca_box.column(align=True)
            info_col.scale_y = 0.7
            info_col.label(
                text="Tip: Assign materials to faces in Edit Mode",
                icon="INFO",
            )
            info_col.label(text="Each color = filament slot in slicer")

        layout.separator()

        # Standard options
        layout.prop(self, "use_selection")
        layout.prop(self, "export_hidden")
        layout.prop(self, "use_mesh_modifiers")
        layout.prop(self, "use_components")
        layout.prop(self, "global_scale")
        layout.prop(self, "coordinate_precision")

    def safe_report(self, level: Set[str], message: str) -> None:
        """
        Safely report a message, using Blender's report system if available, otherwise just logging.
        This allows the class to work both as a Blender operator and in unit tests.
        :param level: The report level (e.g., {'ERROR'}, {'WARNING'}, {'INFO'})
        :param message: The message to report
        """
        if hasattr(self, "report") and callable(getattr(self, "report", None)):
            self.report(level, message)

    def _build_context(self) -> ExportContext:
        """Create an ExportContext from the current operator properties."""
        options = ExportOptions(
            use_selection=self.use_selection,
            export_hidden=self.export_hidden,
            global_scale=self.global_scale,
            use_mesh_modifiers=self.use_mesh_modifiers,
            coordinate_precision=self.coordinate_precision,
            use_orca_format=self.use_orca_format,
            use_components=self.use_components,
            mmu_slicer_format=self.mmu_slicer_format,
            subdivision_depth=self.subdivision_depth,
        )
        return ExportContext(
            options=options,
            operator=self,
            filepath=self.filepath,
            extension_manager=ExtensionManager(),
        )

    def execute(self, context: bpy.types.Context) -> Set[str]:
        """
        The main routine that writes the 3MF archive.

        This function serves as a high-level overview of the steps involved to write a 3MF file.
        :param context: The Blender context.
        :return: A set of status flags to indicate whether the write succeeded or not.
        """
        ctx = self._build_context()
        ctx._progress_begin(context, "Exporting 3MF...")

        try:
            archive = create_archive(self.filepath, ctx.safe_report)
            if archive is None:
                return {"CANCELLED"}

            ctx._progress_update(5, "Preparing export...")

            if ctx.options.use_selection:
                blender_objects = context.selected_objects
                # Validate that at least one mesh object is in the selection
                # (recursively, since meshes may be parented to empties)
                mesh_objects = collect_mesh_objects(
                    blender_objects, export_hidden=True
                )
                if not mesh_objects:
                    ctx.safe_report(
                        {"ERROR"},
                        "No mesh objects selected. Select at least one mesh object to export.",
                    )
                    error("Export cancelled: No mesh objects in selection")
                    return {"CANCELLED"}
            else:
                blender_objects = context.scene.objects

            # Check for non-manifold geometry before export
            mesh_objects = collect_mesh_objects(
                blender_objects, export_hidden=ctx.options.export_hidden
            )
            if mesh_objects:
                non_manifold_objects = check_non_manifold_geometry(
                    mesh_objects, ctx.options.use_mesh_modifiers
                )
                if non_manifold_objects:
                    ctx.safe_report(
                        {"WARNING"},
                        "Exported geometry contains non-manifold issues. "
                        "This may cause warnings in some slicers.",
                    )
                    warn(f"Non-manifold geometry detected in: {non_manifold_objects[0]}")

            global_scale = export_unit_scale(context, ctx.options.global_scale)

            # Dispatch to format-specific exporter
            if ctx.options.use_orca_format == "PAINT":
                if ctx.options.mmu_slicer_format == "ORCA":
                    exporter = OrcaExporter(ctx)
                    return exporter.execute(context, archive, blender_objects, global_scale)
                elif ctx.options.mmu_slicer_format == "PRUSA":
                    exporter = PrusaExporter(ctx)
                    return exporter.execute(context, archive, blender_objects, global_scale)

            # Standard 3MF export — handles geometry, materials, and texture export
            exporter = StandardExporter(ctx)
            return exporter.execute(context, archive, blender_objects, global_scale)
        finally:
            ctx._progress_end()

    # =========================================================================
    # Backward-compatible wrapper methods for unit tests
    # These methods delegate to the refactored utility modules while maintaining
    # the original method signatures that unit tests expect.
    # =========================================================================

    def create_archive(self, filepath: str):
        """Create a 3MF archive. Backward-compatible wrapper."""
        return create_archive(filepath, self.safe_report)

    def unit_scale(self, context):
        """Calculate unit scale. Backward-compatible wrapper."""
        return export_unit_scale(context, self.global_scale)

    def format_transformation(self, transformation: mathutils.Matrix) -> str:
        """Format transformation matrix. Backward-compatible wrapper."""
        from ..common.xml import format_transformation as _format_transformation
        return _format_transformation(transformation)

    def write_materials(self, resources_element, blender_objects) -> Dict[str, int]:
        """
        Write materials to the 3MF document. Backward-compatible wrapper.

        Returns just the name_to_index dict for backward compatibility.
        Updates self.next_resource_id and self.material_resource_id internally.
        """
        from .materials import write_materials as _write_materials

        name_to_index, self.next_resource_id, self.material_resource_id, _ = _write_materials(
            resources_element,
            blender_objects,
            self.use_orca_format,
            getattr(self, "vertex_colors", {}),
            getattr(self, "next_resource_id", 1),
        )
        self.material_name_to_index = name_to_index
        return name_to_index

    def write_vertices(self, mesh_element, vertices) -> None:
        """Write vertices to mesh element. Backward-compatible wrapper."""
        from .geometry import write_vertices as _write_vertices

        _write_vertices(
            mesh_element, vertices, self.use_orca_format, self.coordinate_precision
        )

    def write_triangles(
        self,
        mesh_element,
        triangles,
        default_material,
        material_slots,
        mesh=None,
        blender_object=None,
    ) -> None:
        """Write triangles to mesh element. Backward-compatible wrapper."""
        from .geometry import write_triangles as _write_triangles

        _write_triangles(
            mesh_element,
            triangles,
            default_material,
            material_slots,
            getattr(self, "material_name_to_index", {}),
            self.use_orca_format,
            getattr(self, "mmu_slicer_format", "ORCA"),
            getattr(self, "vertex_colors", {}),
            mesh,
            blender_object,
            getattr(self, "texture_groups", None),
            (
                str(self.material_resource_id)
                if hasattr(self, "material_resource_id") and self.material_resource_id
                else None
            ),
            None,  # segmentation_strings - not used in this wrapper
        )

    def write_objects(self, root, resources_element, blender_objects, global_scale: float) -> None:
        """Write objects to 3MF document. Backward-compatible wrapper."""
        ctx = self._build_context()
        exporter = StandardExporter(ctx)
        exporter.write_objects(root, resources_element, blender_objects, global_scale)

    def write_object_resource(self, resources_element, blender_object):
        """Write a single object resource. Backward-compatible wrapper."""
        ctx = self._build_context()
        exporter = StandardExporter(ctx)
        return exporter.write_object_resource(resources_element, blender_object)
