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
PrusaSlicer 3MF exporter.

Uses slic3rpe:mmu_segmentation attributes for per-triangle multi-material
data, compatible with PrusaSlicer and SuperSlicer.
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree
import zipfile
from typing import Set

import bpy

from ..common.constants import MODEL_NAMESPACE, MODEL_LOCATION
from ..common.logging import debug, warn
from ..common.metadata import Metadata, MetadataEntry

from .archive import write_core_properties
from .geometry import write_metadata
from .materials import collect_face_colors, write_prusa_filament_colors
from .components import collect_mesh_objects
from .standard import BaseExporter, StandardExporter
from .thumbnail import write_thumbnail


class PrusaExporter(BaseExporter):
    """Exports PrusaSlicer compatible 3MF files with mmu_segmentation."""

    def execute(
        self,
        context: bpy.types.Context,
        archive: zipfile.ZipFile,
        blender_objects,
        global_scale: float,
    ) -> Set[str]:
        """
        PrusaSlicer export with mmu_segmentation attributes.

        Uses single model file with slic3rpe:mmu_segmentation on painted triangles.
        """
        ctx = self.ctx

        # Register namespaces
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)
        xml.etree.ElementTree.register_namespace(
            "slic3rpe", "http://schemas.slic3r.org/3mf/2017/06"
        )

        # Collect face colors
        ctx.safe_report(
            {"INFO"}, "Collecting face colors for PrusaSlicer export..."
        )

        # For PAINT mode, collect colors from paint texture metadata instead of face materials
        paint_colors_collected = False
        mesh_objects = collect_mesh_objects(
            blender_objects, export_hidden=ctx.options.export_hidden
        )
        for blender_object in mesh_objects:
            original_object = blender_object
            # Handle evaluated objects
            if hasattr(blender_object, "original"):
                original_object = blender_object.original

            original_mesh_data = original_object.data
            if (
                "3mf_is_paint_texture" in original_mesh_data
                and original_mesh_data["3mf_is_paint_texture"]
            ):
                if "3mf_paint_extruder_colors" in original_mesh_data:
                    try:
                        extruder_colors_hex = ast.literal_eval(
                            original_mesh_data["3mf_paint_extruder_colors"]
                        )
                        # Add all colors from this paint texture to vertex_colors
                        for idx, hex_color in extruder_colors_hex.items():
                            if hex_color not in ctx.vertex_colors:
                                ctx.vertex_colors[hex_color] = idx
                        paint_colors_collected = True
                        debug(
                            f"Collected {len(extruder_colors_hex)} colors from paint texture metadata"
                        )
                    except Exception as e:
                        warn(f"Failed to parse extruder colors from metadata: {e}")

        # If no paint colors found, fall back to face material colors
        if not paint_colors_collected:
            ctx.vertex_colors = collect_face_colors(
                blender_objects, ctx.options.use_mesh_modifiers, ctx.safe_report
            )

        debug(f"PrusaSlicer mode enabled with {len(ctx.vertex_colors)} color zones")

        if len(ctx.vertex_colors) == 0:
            warn("No face colors found! Assign materials to faces for color zones.")
            ctx.safe_report(
                {"WARNING"},
                "No face colors detected. Assign different materials to faces in Edit mode.",
            )
        else:
            ctx.safe_report(
                {"INFO"},
                f"Detected {len(ctx.vertex_colors)} color zones for PrusaSlicer export",
            )

        # Create model root element
        root = xml.etree.ElementTree.Element(f"{{{MODEL_NAMESPACE}}}model")

        root.set("unit", "millimeter")
        root.set("{http://www.w3.org/XML/1998/namespace}lang", "en-US")

        # Add scene metadata first
        scene_metadata = Metadata()
        scene_metadata.retrieve(bpy.context.scene)

        # Add PrusaSlicer metadata if not already present in scene
        if "slic3rpe:Version3mf" not in scene_metadata:
            scene_metadata["slic3rpe:Version3mf"] = MetadataEntry(
                name="slic3rpe:Version3mf", preserve=False, datatype=None, value="1"
            )
        if "slic3rpe:MmPaintingVersion" not in scene_metadata:
            scene_metadata["slic3rpe:MmPaintingVersion"] = MetadataEntry(
                name="slic3rpe:MmPaintingVersion",
                preserve=False,
                datatype=None,
                value="1",
            )

        write_metadata(root, scene_metadata, ctx.options.use_orca_format)

        resources_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}resources"
        )

        # PrusaSlicer MMU painting doesn't use basematerials
        ctx.material_name_to_index = {}

        # Use StandardExporter's write_objects (reuse the logic)
        std_exporter = StandardExporter(ctx)
        std_exporter.write_objects(
            root, resources_element, blender_objects, global_scale
        )

        # Write filament colors to metadata for round-trip import
        write_prusa_filament_colors(archive, ctx.vertex_colors)

        document = xml.etree.ElementTree.ElementTree(root)
        with archive.open(MODEL_LOCATION, "w", force_zip64=True) as f:
            document.write(
                f,
                xml_declaration=True,
                encoding="UTF-8",
            )

        # Write OPC Core Properties
        write_core_properties(archive)

        # Write thumbnail
        write_thumbnail(archive)

        ctx._progress_update(100, "Finalizing export...")
        return ctx.finalize_export(archive, "PrusaSlicer-compatible ")
