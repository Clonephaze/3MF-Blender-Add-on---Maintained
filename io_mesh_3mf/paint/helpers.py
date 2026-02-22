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

"""
Shared helper functions for the MMU Paint suite.

Provides utility functions used by operators, panels, and the bake module.
Extracted from the monolithic ``panel.py`` for DRY reuse across the paint
package.
"""

import ast

import bpy

from ..common.colors import hex_to_rgb as _rgb_from_hex
from ..common.colors import rgb_to_hex as _hex_from_rgb
from ..common.colors import srgb_to_linear as _srgb_to_linear


# ---------------------------------------------------------------------------
#  Default palette — visually distinct colors for up to 16 filaments
# ---------------------------------------------------------------------------

DEFAULT_PALETTE = [
    (0.800, 0.800, 0.800),  # 1: Light gray (typical default/base)
    (0.900, 0.200, 0.100),  # 2: Red
    (0.100, 0.600, 0.200),  # 3: Green
    (0.200, 0.400, 0.900),  # 4: Blue
    (0.950, 0.750, 0.100),  # 5: Yellow
    (0.900, 0.400, 0.900),  # 6: Magenta
    (0.100, 0.800, 0.800),  # 7: Cyan
    (0.950, 0.550, 0.100),  # 8: Orange
    (0.500, 0.250, 0.600),  # 9: Purple
    (0.400, 0.250, 0.150),  # 10: Brown
    (0.950, 0.450, 0.550),  # 11: Pink
    (0.350, 0.650, 0.450),  # 12: Teal
    (0.600, 0.050, 0.050),  # 13: Dark red
    (0.050, 0.350, 0.550),  # 14: Navy
    (0.450, 0.500, 0.100),  # 15: Olive
    (0.200, 0.200, 0.200),  # 16: Dark gray
]


# ===================================================================
#  Utility helpers
# ===================================================================


def _get_paint_image(obj):
    """Find the MMU paint texture image on the object's material, or None."""
    if not obj or not obj.data or not obj.data.materials:
        return None
    for mat in obj.data.materials:
        if mat and mat.use_nodes:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    return node.image
    return None


def _get_paint_mesh(context):
    """Return the active mesh if it has MMU paint data, else None."""
    obj = context.active_object
    if obj and obj.type == "MESH" and obj.data.get("3mf_is_paint_texture"):
        return obj.data
    return None


def _sync_filaments_from_mesh(context):
    """
    Load the filament palette from the active mesh's custom properties
    into the scene-level MMUPaintSettings collection.
    """
    settings = context.scene.mmu_paint
    mesh = _get_paint_mesh(context)

    if mesh is None:
        settings.filaments.clear()
        settings.loaded_mesh_name = ""
        return

    # Already in sync?
    if settings.loaded_mesh_name == mesh.name and len(settings.filaments) > 0:
        return

    colors_str = mesh.get("3mf_paint_extruder_colors", "")
    if not colors_str:
        settings.filaments.clear()
        settings.loaded_mesh_name = ""
        return

    try:
        colors_dict = ast.literal_eval(colors_str)
    except (ValueError, SyntaxError):
        settings.filaments.clear()
        settings.loaded_mesh_name = ""
        return

    settings.filaments.clear()
    for idx in sorted(colors_dict.keys()):
        item = settings.filaments.add()
        item.index = idx
        item.name = f"Filament {idx + 1}"
        hex_col = colors_dict[idx]
        rgb = _rgb_from_hex(hex_col)
        item.color = rgb

    settings.loaded_mesh_name = mesh.name
    # Clamp active index
    if settings.active_filament_index >= len(settings.filaments):
        settings.active_filament_index = 0


def _write_colors_to_mesh(context):
    """Write the current filament palette back to the mesh custom property."""
    mesh = _get_paint_mesh(context)
    if mesh is None:
        return
    settings = context.scene.mmu_paint
    colors_dict = {}
    for item in settings.filaments:
        colors_dict[item.index] = _hex_from_rgb(*item.color)
    mesh["3mf_paint_extruder_colors"] = str(colors_dict)


def _configure_paint_brush(context):
    """
    Configure or create a texture paint brush for MMU painting.

    Blender 4.x: Create/get a custom '3MF Paint' brush and assign it.
    Blender 5.0+: Configure the currently active brush (read-only assignment).

    Returns the brush object or None.
    """
    ts = context.tool_settings

    if bpy.app.version >= (5, 0, 0):
        # Blender 5.0+: Configure active brush (read-only assignment)
        brush = ts.image_paint.brush if ts.image_paint else None
        if brush is None:
            return None
    else:
        # Blender 4.x: Create/get custom brush and assign it
        brush_name = "3MF Paint"
        brush = bpy.data.brushes.get(brush_name)
        if brush is None:
            brush = bpy.data.brushes.new(name=brush_name, mode="TEXTURE_PAINT")
        # Try to assign (writable in 4.x)
        try:
            ts.image_paint.brush = brush
        except AttributeError:
            pass  # Fall back to active brush if assignment fails

    # Configure brush settings (common to both versions)
    if brush:
        brush.blend = "MIX"
        brush.strength = 1.0
        brush.curve_distance_falloff_preset = "CONSTANT"

    return brush


def _set_brush_color(context, color_rgb):
    """Set the active texture paint brush color to the given (r, g, b) sRGB tuple.

    The palette stores colors as raw sRGB values (matching the hex colors in the
    3MF file).  Blender's brush.color expects **linear** values — it will convert
    linear → sRGB internally when writing to an sRGB-tagged image.  We therefore
    convert sRGB → linear here so that the painted pixels end up with the same
    raw sRGB values that the import renderer wrote via foreach_set.

    CRITICAL: Blender has a "Unified Color" system where the paint color can be
    stored either in the brush OR in the unified paint settings (shared across all
    brushes).  We set BOTH to ensure the color updates correctly.
    """
    # Ensure we have a proper 3-element tuple
    color_rgb = tuple(color_rgb[:3])

    # Convert sRGB → linear so Blender's paint system round-trips correctly.
    linear_rgb = (
        _srgb_to_linear(color_rgb[0]),
        _srgb_to_linear(color_rgb[1]),
        _srgb_to_linear(color_rgb[2]),
    )

    ts = context.tool_settings
    if not ts.image_paint:
        return

    brush = ts.image_paint.brush
    if not brush:
        return

    try:
        # 1. Set brush color (used when unified color is OFF)
        brush.color = linear_rgb

        # 2. Set unified paint settings color (used when unified color is ON)
        # This is the key - most users have "Unified Color" enabled by default
        # ts.image_paint is the Paint settings object with unified_paint_settings
        ups = ts.image_paint.unified_paint_settings
        if ups:
            # ALWAYS set the unified color - this is what actually controls the paint color
            # when "use_unified_color" is enabled (which is the default)
            ups.color = linear_rgb

        # 3. Force UI refresh to show the new color
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    except Exception:
        pass


def _has_vertex_colors(obj):
    """Return True if *obj* has a non-empty color attribute."""
    if not obj or not obj.data:
        return False
    if not hasattr(obj.data, "color_attributes"):
        return False
    ca = obj.data.color_attributes
    # active_color can be None even when color data exists, so
    # just check if there are any color attributes at all.
    return len(ca) > 0


# ===================================================================
#  Property update callbacks
# ===================================================================


def _on_active_filament_changed(self, context):
    """When user selects a different filament in the list, update brush color."""
    try:
        settings = context.scene.mmu_paint
        idx = settings.active_filament_index
        if 0 <= idx < len(settings.filaments):
            color = tuple(settings.filaments[idx].color[:])
            _set_brush_color(context, color)
    except Exception:
        pass  # Silently ignore context errors during undo/redo
