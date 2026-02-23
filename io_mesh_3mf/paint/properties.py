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

    # Internal: tracks which mesh the filament list was loaded from
    loaded_mesh_name: bpy.props.StringProperty(default="")
