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

from .helpers import _on_active_filament_changed, _on_active_mix_filament_changed


# ===================================================================
#  Mixed filament ui_type helpers
# ===================================================================

# Stable module-level tuples — Blender requires these to be kept alive
# for dynamic enum callbacks to avoid garbage-collection crashes.
_UI_TYPE_ITEMS_BASE = [
    ("gradient", "Gradient", "Blend component A and B by a factor"),
    ("pattern",  "Pattern",  "Multi-filament manual pattern string"),
]
# Imported-only modes — only appear in the dropdown when the entry already uses them
_UI_TYPE_ITEMS_LAYER_CYCLE   = _UI_TYPE_ITEMS_BASE + [("layer_cycle",  "Layer Cycle (Imported)",  "Round-trip only")]
_UI_TYPE_ITEMS_POINTILLISM   = _UI_TYPE_ITEMS_BASE + [("pointillism",  "Pointillism (Imported)",  "Round-trip only")]
_UI_TYPE_ITEMS_BOTH_IMPORTED = _UI_TYPE_ITEMS_BASE + [
    ("layer_cycle", "Layer Cycle (Imported)",  "Round-trip only"),
    ("pointillism", "Pointillism (Imported)",  "Round-trip only"),
]


def _ui_type_items(self, context):
    """Return enum items; expose imported-only modes only when the entry already uses one."""
    ut = self.ui_type
    if ut == "layer_cycle" and ut == "pointillism":
        return _UI_TYPE_ITEMS_BOTH_IMPORTED
    if ut == "layer_cycle":
        return _UI_TYPE_ITEMS_LAYER_CYCLE
    if ut == "pointillism":
        return _UI_TYPE_ITEMS_POINTILLISM
    return _UI_TYPE_ITEMS_BASE


def _ui_type_update(self, context):
    """Keep distribution_mode in sync and clear stale fields when the user changes the type."""
    if self.ui_type == "layer_cycle":
        self.distribution_mode = "0"
    elif self.ui_type == "pointillism":
        self.distribution_mode = "1"
    else:  # gradient or pattern — both use mode 2
        self.distribution_mode = "2"
    # Switching to gradient removes any leftover pattern so it doesn't export
    if self.ui_type == "gradient":
        self.manual_pattern = ""


# ===================================================================
#  PropertyGroups
# ===================================================================


class MMUMixedFilamentItem(bpy.types.PropertyGroup):
    """One virtual mixed filament entry (OrcaSlicer-FullSpectrum).

    Mirrors the fields from :class:`~io_mesh_3mf.common.mixed_filaments.MixedFilament`
    that are relevant to the UI.
    """

    component_a: bpy.props.IntProperty(
        name="Component A",
        description="1-based physical filament index for component A",
        default=1,
        min=1,
        max=16,
    )
    component_b: bpy.props.IntProperty(
        name="Component B",
        description="1-based physical filament index for component B",
        default=2,
        min=1,
        max=16,
    )
    mix_b_percent: bpy.props.IntProperty(
        name="Mix B %",
        description="Percentage of component B in the blend (0-100)",
        default=50,
        min=0,
        max=100,
        subtype="PERCENTAGE",
    )
    display_color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype="COLOR_GAMMA",
        size=3,
        min=0.0,
        max=1.0,
        default=(0.15, 0.65, 0.6),  # Fallback teal #26A69A
        description="Computed blended display color (sRGB)",
    )
    distribution_mode: bpy.props.StringProperty(
        name="Distribution Mode (internal)",
        description="Raw mode token for round-trip serialization (do not edit manually)",
        default="2",
    )
    ui_type: bpy.props.EnumProperty(
        name="Type",
        description="How this virtual filament blends its components",
        items=_ui_type_items,
        update=_ui_type_update,
        default=None,
        options={"SKIP_SAVE"},
    )
    manual_pattern: bpy.props.StringProperty(
        name="Pattern",
        description="Manual layer pattern string (digits 1-9, commas for groups)",
        default="",
    )
    stable_id: bpy.props.IntProperty(
        name="Stable ID",
        description="Persistent round-trip identity (do not edit manually)",
        default=0,
    )
    enabled: bpy.props.BoolProperty(
        name="Enabled",
        default=True,
    )
    deleted: bpy.props.BoolProperty(
        name="Deleted",
        description="Soft-deleted (hidden but preserved for round-trip)",
        default=False,
    )
    palette_index: bpy.props.IntProperty(
        name="Palette Index",
        description="Index of this mix entry in settings.filaments (-1 if not active)",
        default=-1,
    )


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
    is_virtual: bpy.props.BoolProperty(
        name="Is Virtual",
        description="True for mixed-filament virtual slots (not shown in main palette)",
        default=False,
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

    # Skip dissolve — preserve original mesh topology
    skip_dissolve: bpy.props.BoolProperty(
        name="Skip Dissolve",
        description=(
            "Skip the Limited Dissolve step that merges coplanar faces. "
            "Preserves original mesh detail at the cost of potentially "
            "smaller UV islands per face"
        ),
        default=False,
    )

    # Internal: tracks which mesh the filament list was loaded from
    loaded_mesh_name: bpy.props.StringProperty(default="")

    # --- Mixed filaments (OrcaSlicer-FullSpectrum) ---
    has_mixed_filaments: bpy.props.BoolProperty(
        name="Has Mixed Filaments",
        description="True when mixed filament definitions are present (import or user-created)",
        default=False,
    )
    mixed_filaments: bpy.props.CollectionProperty(type=MMUMixedFilamentItem)
    active_mixed_filament_index: bpy.props.IntProperty(
        name="Active Mixed Filament",
        default=0,
        update=lambda self, ctx: _on_active_mix_filament_changed(self, ctx),
    )
    num_physical_filaments: bpy.props.IntProperty(
        name="Physical Filament Count",
        description="Number of physical (non-virtual) filaments in the palette",
        default=0,
    )
