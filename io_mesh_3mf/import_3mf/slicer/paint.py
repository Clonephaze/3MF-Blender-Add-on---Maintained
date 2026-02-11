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
Paint / extruder material helpers for slicer-specific per-triangle color encoding.

Orca Slicer and PrusaSlicer both store per-triangle material information
using different mechanisms:

- **Orca:** ``paint_color`` attribute on each ``<triangle>`` element.
  Short hex codes (``"4"``, ``"8"``, ``"0C"``, …) map to filament indices.
- **Prusa:** ``slic3rpe:mmu_segmentation`` attribute encodes a recursive
  subdivision tree as a hex string.

This module provides the mapping tables, parsing helpers, and material
creation for both approaches.
"""

import colorsys
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from ...common import debug
from ...common.segmentation import decode_segmentation_string, TriangleSubdivider
from ...common.types import ResourceMaterial

if TYPE_CHECKING:
    from ..context import ImportContext

__all__ = [
    "ORCA_PAINT_TO_INDEX",
    "parse_paint_color_to_index",
    "get_or_create_paint_material",
    "subdivide_prusa_segmentation",
]


# ---------------------------------------------------------------------------
# Orca Slicer paint-code → filament-index mapping
# ---------------------------------------------------------------------------

# Reverse of ORCA_FILAMENT_CODES in export_3mf.py.
# Paint codes may arrive as uppercase or lowercase — we normalize to uppercase.
ORCA_PAINT_TO_INDEX: Dict[str, int] = {
    "": 0,
    "4": 1,
    "8": 2,
    "0C": 3,
    "1C": 4,
    "2C": 5,
    "3C": 6,
    "4C": 7,
    "5C": 8,
    "6C": 9,
    "7C": 10,
    "8C": 11,
    "9C": 12,
    "AC": 13,
    "BC": 14,
    "CC": 15,
    "DC": 16,
    "EC": 17,
    "0FC": 18,
    "1FC": 19,
    "2FC": 20,
    "3FC": 21,
    "4FC": 22,
    "5FC": 23,
    "6FC": 24,
    "7FC": 25,
    "8FC": 26,
    "9FC": 27,
    "AFC": 28,
    "BFC": 29,
}


def parse_paint_color_to_index(paint_code: str) -> int:
    """Parse an Orca ``paint_color`` attribute to a 1-based filament index.

    Returns 0 if the code is not a recognised Orca paint code — the caller
    should then fall back to segmentation decoding.

    :param paint_code: The ``paint_color`` attribute value.
    :return: Filament index (1-based), or **0** if unknown.
    """
    if not paint_code:
        return 0

    # Normalize to uppercase for lookup
    normalized = paint_code.upper()
    if normalized in ORCA_PAINT_TO_INDEX:
        return ORCA_PAINT_TO_INDEX[normalized]

    # Try without normalization (already uppercase)
    if paint_code in ORCA_PAINT_TO_INDEX:
        return ORCA_PAINT_TO_INDEX[paint_code]

    # Not a known Orca paint code
    return 0


# ---------------------------------------------------------------------------
# Material creation for paint filaments
# ---------------------------------------------------------------------------

def get_or_create_paint_material(
    ctx: "ImportContext",
    filament_index: int,
    paint_code: str,
) -> ResourceMaterial:
    """Get or create a :class:`ResourceMaterial` for a paint colour / filament.

    Uses actual colours from ``ctx.orca_filament_colors`` when available,
    otherwise generates deterministic colours via golden-ratio HSV spacing.

    :param ctx: Import context.
    :param filament_index: Filament index (1-based from paint codes).
    :param paint_code: The original paint code string (for the material ID key).
    :return: A :class:`ResourceMaterial` for this paint colour.
    """
    # Unique material key for paint colours
    material_id = f"paint_{filament_index}_{paint_code}"

    if material_id not in ctx.resource_materials:
        # Try actual colour from orca_filament_colors.
        # filament_index is 1-based (paint code "4" => 1), array is 0-indexed.
        color = None
        color_name = f"Filament {filament_index}"
        array_index = filament_index - 1  # 1-based → 0-based

        if array_index >= 0 and array_index in ctx.orca_filament_colors:
            from ..materials.base import parse_hex_color
            hex_color = ctx.orca_filament_colors[array_index]
            color = parse_hex_color(hex_color)
            color_name = f"Color {hex_color}"
            debug(
                f"Using Orca filament color {filament_index} "
                f"(array index {array_index}): {hex_color}"
            )

        if color is None:
            # Fallback: deterministic colour via golden-ratio spacing
            hue = (filament_index * 0.618033988749895) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.8, 0.9)
            color = (r, g, b, 1.0)
            debug(f"Generated fallback color for filament {filament_index}")

        ctx.resource_materials[material_id] = {
            0: ResourceMaterial(
                name=color_name,
                color=color,
            ),
        }
        debug(f"Created paint material for filament {filament_index} (code: {paint_code})")

    return ctx.resource_materials[material_id][0]


# ---------------------------------------------------------------------------
# PrusaSlicer segmentation subdivision
# ---------------------------------------------------------------------------

def subdivide_prusa_segmentation(
    ctx: "ImportContext",
    v1: int,
    v2: int,
    v3: int,
    segmentation_string: str,
    vertex_list: List[Tuple[float, float, float]],
    state_materials: Dict[int, ResourceMaterial],
    source_triangle_index: int,
    default_extruder: int = 1,
) -> Tuple[List[Tuple[int, int, int]], List[Optional[ResourceMaterial]]]:
    """Subdivide a triangle according to PrusaSlicer segmentation.

    :param ctx: Import context.
    :param v1: First vertex index of the original triangle.
    :param v2: Second vertex index.
    :param v3: Third vertex index.
    :param segmentation_string: The ``slic3rpe:mmu_segmentation`` hex string.
    :param vertex_list: Vertex coordinate list (extended in-place with new
        midpoint vertices).
    :param state_materials: Dict mapping state → material (extended in-place).
    :param source_triangle_index: Index of the source triangle (for tracking).
    :param default_extruder: Object's default extruder (1-based) for state 0.
    :return: ``(sub_triangles, sub_materials)`` — new triangle index tuples
        and corresponding materials.
    """
    # Decode the segmentation tree
    tree = decode_segmentation_string(segmentation_string)
    if tree is None:
        # Failed to decode — return original triangle without material
        return [(v1, v2, v3)], [None]

    # Get vertex coordinates
    p1 = vertex_list[v1]
    p2 = vertex_list[v2]
    p3 = vertex_list[v3]

    # Subdivide using the tree
    subdivider = TriangleSubdivider()
    new_verts, sub_tris = subdivider.subdivide(p1, p2, p3, tree, source_triangle_index)

    # Add new vertices (indices 3+ in new_verts are new midpoints)
    base_vertex_idx = len(vertex_list)
    for i in range(3, len(new_verts)):
        vertex_list.append(new_verts[i])

    # Remap triangle vertex indices to global vertex list
    def _remap_idx(local_idx: int) -> int:
        if local_idx == 0:
            return v1
        elif local_idx == 1:
            return v2
        elif local_idx == 2:
            return v3
        else:
            return base_vertex_idx + (local_idx - 3)

    result_triangles: List[Tuple[int, int, int]] = []
    result_materials: List[Optional[ResourceMaterial]] = []

    for tri in sub_tris:
        result_triangles.append((_remap_idx(tri.v0), _remap_idx(tri.v1), _remap_idx(tri.v2)))

        # State 0 → object's default extruder;  State N → extruder N directly
        state = int(tri.state)
        if state == 0:
            extruder_num = default_extruder
        else:
            extruder_num = state  # state value IS the extruder number (1-based)

        if state not in state_materials:
            material = get_or_create_paint_material(
                ctx, extruder_num, f"prusa_extruder_{extruder_num}"
            )
            state_materials[state] = material
        result_materials.append(state_materials[state])

    debug(
        f"Subdivided triangle {source_triangle_index}: "
        f"{len(new_verts) - 3} new vertices, {len(result_triangles)} sub-triangles"
    )

    return result_triangles, result_materials
