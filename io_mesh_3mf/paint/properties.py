# Blender add-on to import and export 3MF files.
# Copyright (C) 2025 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Blender PropertyGroup definitions for the MMU Paint suite.

Contains all scene-level and per-filament property containers registered
on ``bpy.types.Scene.mmu_paint``.
"""

import bpy
import bpy.props
import bpy.types

from .helpers import _on_active_filament_changed


# ===================================================================
#  PropertyGroups
# ===================================================================


class MMUFilamentItem(bpy.types.PropertyGroup):
    """One filament/extruder entry in the palette list."""

    color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype="COLOR_GAMMA",
        size=3,
        min=0.0,
        max=1.0,
        default=(0.8, 0.8, 0.8),
        description="Filament swatch color (read-only display, sRGB)",
    )
    index: bpy.props.IntProperty(
        name="Extruder Index",
        description="0-based extruder index",
        default=0,
    )


class MMUInitFilamentItem(bpy.types.PropertyGroup):
    """Filament entry for initialization setup (editable color)."""

    color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype="COLOR_GAMMA",
        size=3,
        min=0.0,
        max=1.0,
        default=(0.8, 0.8, 0.8),
        description="Filament color for initialization (sRGB)",
    )
    name: bpy.props.StringProperty(
        name="Name",
        default="Filament",
    )


class MMUPaintSettings(bpy.types.PropertyGroup):
    """Per-scene settings for the MMU Paint panel."""

    filaments: bpy.props.CollectionProperty(type=MMUFilamentItem)
    active_filament_index: bpy.props.IntProperty(
        name="Active Filament",
        default=0,
        update=lambda self, ctx: _on_active_filament_changed(self, ctx),
    )

    # Initialization setup
    init_filaments: bpy.props.CollectionProperty(type=MMUInitFilamentItem)
    active_init_filament_index: bpy.props.IntProperty(
        name="Active Init Filament",
        default=0,
    )

    # UV method for painting / baking
    uv_method: bpy.props.EnumProperty(
        name="UV Method",
        description=(
            "UV unwrap strategy for MMU paint textures.\n"
            "Smart UV Project shares edges between faces for seamless painting.\n"
            "Lightmap Pack gives every face its own rectangle — best for "
            "procedural bakes but may show edge bleed when hand-painting"
        ),
        items=[
            ("SMART", "Smart UV Project",
             "Groups adjacent coplanar faces into shared islands. "
             "Best for hand-painting (shared edges reduce bleed)"),
            ("LIGHTMAP", "Lightmap Pack",
             "Every face gets its own UV rectangle. "
             "Best for procedural/baked textures"),
        ],
        default="SMART",
    )
    lightmap_divisions: bpy.props.IntProperty(
        name="Lightmap Divisions",
        description="Grid divisions for Lightmap Pack (higher = more UV precision, slower)",
        min=1,
        max=48,
        default=12,
    )

    # Quantization method - controls how baked pixels are mapped to filament colors
    quantize_method: bpy.props.EnumProperty(
        name="Quantization Method",
        description="How to map baked colors to filament palette",
        items=[
            ("REGION", "Region-Based (Recommended)",
             "Segments texture into connected regions, then assigns each region "
             "to a single filament. Handles shadows/highlights correctly"),
            ("PIXEL", "Per-Pixel",
             "Matches each pixel independently to the nearest filament color. "
             "May incorrectly assign shadows to wrong colors"),
        ],
        default="REGION",
    )
    region_similarity: bpy.props.FloatProperty(
        name="Region Similarity",
        description=(
            "Color similarity threshold for region segmentation. "
            "Lower = more regions (finer detail). Higher = fewer regions (more merging). "
            "Default 0.20 works well for most textures"
        ),
        min=0.05,
        max=0.5,
        default=0.20,
        precision=2,
    )
    min_region_size: bpy.props.IntProperty(
        name="Min Region Size",
        description=(
            "Regions smaller than this many pixels get merged into their "
            "largest neighbor. Eliminates banding artifacts from gradient "
            "transitions. Higher = more aggressive cleanup"
        ),
        min=0,
        max=2000,
        default=200,
    )

    # Legacy spatial smoothing (deprecated, kept for compatibility)
    use_spatial_smoothing: bpy.props.BoolProperty(
        name="UV Spatial Smoothing (Deprecated)",
        description=(
            "DEPRECATED: Use Region-Based quantization instead. "
            "This UV-space filter can cause artifacts at island boundaries"
        ),
        default=False,
    )
    smoothing_kernel_size: bpy.props.IntProperty(
        name="Kernel Size",
        description="Legacy smoothing kernel size",
        min=3,
        max=11,
        default=5,
    )
    smoothing_passes: bpy.props.IntProperty(
        name="Passes",
        description="Legacy smoothing passes",
        min=1,
        max=10,
        default=3,
    )

    # Active paint layer for layer switching
    active_paint_layer: bpy.props.EnumProperty(
        name="Paint Layer",
        description="Which paint data layer is currently active",
        items=[
            ("COLOR", "Color", "MMU filament color painting"),
            ("SEAM", "Seam", "Seam enforce/block painting"),
            ("SUPPORT", "Support", "Support enforce/block painting"),
        ],
        default="COLOR",
    )

    # Internal: tracks which mesh the filament list was loaded from
    loaded_mesh_name: bpy.props.StringProperty(default="")
