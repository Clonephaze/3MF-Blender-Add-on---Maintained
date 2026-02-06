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
Import module for rendering hash-based MMU segmentation to UV textures.

This module handles importing multi-material segmentation data as editable UV textures.
Used by both PrusaSlicer (slic3rpe:mmu_segmentation) and Orca Slicer (paint_color).

Process:
1. Decode hex hash strings to subdivision trees
2. Subdivide triangles in UV space matching tree structure
3. Render segmentation patterns as colored regions in texture
4. Apply gap filling to prevent visual seams

The hash format is slicer-agnostic - only the XML attribute names differ.
"""

import bpy
from typing import Tuple, List, Dict
from .hash_segmentation import SegmentationNode, TriangleState


def subdivide_in_uv_space(
    uv0: Tuple[float, float],
    uv1: Tuple[float, float],
    uv2: Tuple[float, float],
    node: SegmentationNode,
) -> List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float], int]]:
    """
    Recursively subdivide a UV triangle according to the segmentation tree.
    Mirrors the exact logic from TriangleSubdivider to ensure correct subdivision.

    Insight: the slicer encodes children in reverse order and uses special_side
    to rotate edges. We mirror that ordering here to preserve exact roundtrip.

    :param uv0, uv1, uv2: UV coordinates of triangle vertices
    :param node: Segmentation tree node
    :return: List of (uv0, uv1, uv2, state) tuples for each leaf sub-triangle
    """
    result = []

    if node is None:
        return result

    if node.is_leaf:
        return [(uv0, uv1, uv2, node.state)]

    split_sides = node.split_sides
    special = node.special_side

    # special_side rotates which edge is treated as the first split edge.
    verts = [uv0, uv1, uv2]
    rotated = [verts[(special + j) % 3] for j in range(3)]
    r0, r1, r2 = rotated[0], rotated[1], rotated[2]

    # Children are stored reversed in the segmentation string.
    children = node.children[::-1]

    if split_sides == 1:
        m = ((r1[0] + r2[0]) / 2, (r1[1] + r2[1]) / 2)

        if len(children) > 0:
            result.extend(subdivide_in_uv_space(r0, r1, m, children[0]))
        if len(children) > 1:
            result.extend(subdivide_in_uv_space(m, r2, r0, children[1]))

    elif split_sides == 2:
        m01 = ((r0[0] + r1[0]) / 2, (r0[1] + r1[1]) / 2)
        m20 = ((r2[0] + r0[0]) / 2, (r2[1] + r0[1]) / 2)

        if len(children) > 0:
            result.extend(subdivide_in_uv_space(r0, m01, m20, children[0]))
        if len(children) > 1:
            result.extend(subdivide_in_uv_space(m01, r1, m20, children[1]))
        if len(children) > 2:
            result.extend(subdivide_in_uv_space(r1, r2, m20, children[2]))

    elif split_sides == 3:
        m01 = ((r0[0] + r1[0]) / 2, (r0[1] + r1[1]) / 2)
        m12 = ((r1[0] + r2[0]) / 2, (r1[1] + r2[1]) / 2)
        m20 = ((r2[0] + r0[0]) / 2, (r2[1] + r0[1]) / 2)

        if len(children) > 0:
            result.extend(subdivide_in_uv_space(r0, m01, m20, children[0]))
        if len(children) > 1:
            result.extend(subdivide_in_uv_space(m01, r1, m12, children[1]))
        if len(children) > 2:
            result.extend(subdivide_in_uv_space(m12, r2, m20, children[2]))
        if len(children) > 3:
            result.extend(subdivide_in_uv_space(m01, m12, m20, children[3]))

    return result


def render_triangle_to_image(
    pixels: List[float],
    width: int,
    height: int,
    uv0: Tuple[float, float],
    uv1: Tuple[float, float],
    uv2: Tuple[float, float],
    color: List[float],
) -> None:
    """
    Render a solid triangle to the pixel array using edge function rasterization.

    :param pixels: Flat list of RGBA values (modified in-place)
    :param width: Image width
    :param height: Image height
    :param uv0, uv1, uv2: UV coordinates (0-1)
    :param color: RGBA color list (0-1)
    """
    x0, y0 = uv0[0] * width, uv0[1] * height
    x1, y1 = uv1[0] * width, uv1[1] * height
    x2, y2 = uv2[0] * width, uv2[1] * height

    # Edge function gives signed area; consistent sign means inside.
    def edge_function(ax, ay, bx, by, px, py):
        return (px - ax) * (by - ay) - (py - ay) * (bx - ax)

    area = edge_function(x0, y0, x1, y1, x2, y2)
    if abs(area) < 0.0001:
        return

    # Normalize winding to keep edge tests consistent.
    if area < 0:
        x1, y1, x2, y2 = x2, y2, x1, y1
        area = -area

    min_x = max(0, int(min(x0, x1, x2) - 1))
    max_x = min(width - 1, int(max(x0, x1, x2) + 2))
    min_y = max(0, int(min(y0, y1, y2) - 1))
    max_y = min(height - 1, int(max(y0, y1, y2) + 2))

    # Slight negative threshold avoids pinholes on shared edges.
    threshold = -1.5

    for y in range(min_y, max_y + 1):
        py = y + 0.5
        for x in range(min_x, max_x + 1):
            px = x + 0.5

            e0 = edge_function(x0, y0, x1, y1, px, py)
            e1 = edge_function(x1, y1, x2, y2, px, py)
            e2 = edge_function(x2, y2, x0, y0, px, py)

            if e0 >= threshold and e1 >= threshold and e2 >= threshold:
                idx = (y * width + x) * 4
                if 0 <= idx < len(pixels) - 3:
                    pixels[idx] = color[0]
                    pixels[idx + 1] = color[1]
                    pixels[idx + 2] = color[2]
                    pixels[idx + 3] = color[3]


def close_gaps_in_texture(
    pixels: List[float], width: int, height: int, iterations: int = 1
) -> List[float]:
    """
    Apply morphological dilation to close small gaps in the texture.
    For each transparent/background pixel, check neighbors and fill with most common color.

    :param pixels: Flat list of RGBA values
    :param width: Image width
    :param height: Image height
    :param iterations: Number of dilation passes
    :return: Modified pixel list
    """
    result = list(pixels)

    for iteration in range(iterations):
        temp = list(result)

        for y in range(height):
            for x in range(width):
                idx = (y * width + x) * 4

                if result[idx + 3] > 0.5:
                    continue

                neighbors = []

                if x > 0:
                    left_idx = (y * width + (x - 1)) * 4
                    if result[left_idx + 3] > 0.5:
                        neighbors.append(
                            (
                                result[left_idx],
                                result[left_idx + 1],
                                result[left_idx + 2],
                                result[left_idx + 3],
                            )
                        )

                if x < width - 1:
                    right_idx = (y * width + (x + 1)) * 4
                    if result[right_idx + 3] > 0.5:
                        neighbors.append(
                            (
                                result[right_idx],
                                result[right_idx + 1],
                                result[right_idx + 2],
                                result[right_idx + 3],
                            )
                        )

                if y > 0:
                    up_idx = ((y - 1) * width + x) * 4
                    if result[up_idx + 3] > 0.5:
                        neighbors.append(
                            (
                                result[up_idx],
                                result[up_idx + 1],
                                result[up_idx + 2],
                                result[up_idx + 3],
                            )
                        )

                if y < height - 1:
                    down_idx = ((y + 1) * width + x) * 4
                    if result[down_idx + 3] > 0.5:
                        neighbors.append(
                            (
                                result[down_idx],
                                result[down_idx + 1],
                                result[down_idx + 2],
                                result[down_idx + 3],
                            )
                        )

                # Require at least two neighbors to avoid smearing isolated pixels.
                if len(neighbors) >= 2:
                    if neighbors:
                        temp[idx] = neighbors[0][0]
                        temp[idx + 1] = neighbors[0][1]
                        temp[idx + 2] = neighbors[0][2]
                        temp[idx + 3] = neighbors[0][3]

        result = temp

    return result


def render_segmentation_to_texture(
    obj,
    seg_strings: Dict[int, str],
    extruder_colors: Dict[int, List[float]],
    texture_size: int = 2048,
    default_extruder: int = 1,
    bpy=None,
) -> "bpy.types.Image":
    """
    Render segmentation strings to a UV texture.

    Uses Smart UV Project to create a good UV layout, then renders
    segmentation patterns as colored triangles in UV space.

    :param obj: Blender mesh object
    :param seg_strings: Dict mapping face_index -> segmentation_string
    :param extruder_colors: Dict mapping extruder index -> RGBA color list
    :param texture_size: Size of square texture
    :param default_extruder: Default extruder for state 0
    :param bpy: Blender Python module (for testing)
    :return: Blender Image object
    """
    from .hash_segmentation import decode_segmentation_string

    if bpy is None:
        import bpy

    mesh = obj.data

    if not mesh.uv_layers:
        mesh.uv_layers.new(name="UVMap")

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")

    bpy.ops.mesh.select_all(action="SELECT")
    # Smart UV is the most robust for arbitrary meshes; keeps islands compact.
    bpy.ops.uv.smart_project(
        angle_limit=66.0,
        island_margin=0.02,
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=False,
    )

    bpy.ops.object.mode_set(mode="OBJECT")

    uv_layer_data = mesh.uv_layers.active.data

    image_name = f"{obj.name}_segmentation"
    image = bpy.data.images.new(
        image_name, width=texture_size, height=texture_size, alpha=True
    )

    pixels = [0.0, 0.0, 0.0, 0.0] * (texture_size * texture_size)

    decode_failures = 0
    subdivision_failures = 0

    for face_idx, seg_string in seg_strings.items():
        if face_idx >= len(mesh.polygons):
            continue

        poly = mesh.polygons[face_idx]
        if len(poly.loop_indices) != 3:
            continue

        loop_indices = list(poly.loop_indices)
        uv0 = tuple(uv_layer_data[loop_indices[0]].uv)
        uv1 = tuple(uv_layer_data[loop_indices[1]].uv)
        uv2 = tuple(uv_layer_data[loop_indices[2]].uv)

        tree = decode_segmentation_string(seg_string)
        if tree is None:
            decode_failures += 1
            print(
                f"    WARNING: Failed to decode segmentation for face {face_idx}: '{seg_string}'"
            )
            # Fallback to default extruder so the face remains printable.
            default_color_index = default_extruder - 1
            fallback_color = list(
                extruder_colors.get(default_color_index, [0.5, 0.5, 0.5, 1.0])
            )
            render_triangle_to_image(
                pixels, texture_size, texture_size, uv0, uv1, uv2, fallback_color
            )
            continue

        sub_triangles = subdivide_in_uv_space(uv0, uv1, uv2, tree)

        if not sub_triangles:
            subdivision_failures += 1
            print(f"    WARNING: Subdivision produced no triangles for face {face_idx}")
            # Fallback keeps visuals intact even if a tree is malformed.
            default_color_index = default_extruder - 1
            fallback_color = list(
                extruder_colors.get(default_color_index, [0.5, 0.5, 0.5, 1.0])
            )
            render_triangle_to_image(
                pixels, texture_size, texture_size, uv0, uv1, uv2, fallback_color
            )
            continue

        for sub_uv0, sub_uv1, sub_uv2, state in sub_triangles:
            if state == TriangleState.DEFAULT or state == 0:
                color_index = default_extruder - 1
            else:
                color_index = (
                    int(state) - 1
                )
            color = list(extruder_colors.get(color_index, [0.5, 0.5, 0.5, 1.0]))

            render_triangle_to_image(
                pixels, texture_size, texture_size, sub_uv0, sub_uv1, sub_uv2, color
            )

    default_color_index = default_extruder - 1
    default_col = list(extruder_colors.get(default_color_index, [0.5, 0.5, 0.5, 1.0]))
    # Fill any faces without segmentation with the default color.
    for face_idx, poly in enumerate(mesh.polygons):
        if face_idx in seg_strings:
            continue
        if len(poly.loop_indices) != 3:
            continue

        loop_indices = list(poly.loop_indices)
        uv0 = tuple(uv_layer_data[loop_indices[0]].uv)
        uv1 = tuple(uv_layer_data[loop_indices[1]].uv)
        uv2 = tuple(uv_layer_data[loop_indices[2]].uv)
        render_triangle_to_image(
            pixels, texture_size, texture_size, uv0, uv1, uv2, default_col
        )

    if decode_failures > 0 or subdivision_failures > 0:
        print(
            f"  Segmentation processing: {decode_failures} decode failures, {subdivision_failures} subdivision failures"
        )

    pixels = close_gaps_in_texture(pixels, texture_size, texture_size, iterations=3)

    image.pixels[:] = pixels
    image.pack()

    return image
