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
import numpy as np
from typing import Tuple, List, Dict
from .hash_segmentation import SegmentationNode, TriangleState
from .utilities import debug


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
    buf: np.ndarray,
    width: int,
    height: int,
    uv0: Tuple[float, float],
    uv1: Tuple[float, float],
    uv2: Tuple[float, float],
    color: np.ndarray,
) -> None:
    """
    Render a solid triangle to the pixel buffer using vectorized edge function rasterization.

    For triangles larger than a few pixels, uses numpy meshgrid over the bounding
    box to evaluate all three edge functions simultaneously.

    For sub-pixel or very small triangles (common at deep segmentation tree leaves
    near material boundaries), falls back to centroid point-sampling to guarantee
    at least one pixel is painted. Without this, tiny leaf sub-triangles would be
    skipped entirely, leaving scattered holes at color boundaries.

    :param buf: Numpy array of shape (H, W, 4), modified in-place
    :param width: Image width
    :param height: Image height
    :param uv0, uv1, uv2: UV coordinates (0-1)
    :param color: Numpy array of 4 floats (RGBA, 0-1)
    """
    x0, y0 = float(uv0[0]) * width, float(uv0[1]) * height
    x1, y1 = float(uv1[0]) * width, float(uv1[1]) * height
    x2, y2 = float(uv2[0]) * width, float(uv2[1]) * height

    # Signed area via cross product; skip degenerate triangles.
    area = (x2 - x0) * (y1 - y0) - (y2 - y0) * (x1 - x0)
    if abs(area) < 0.0001:
        return

    # Normalize winding so edge tests are consistent.
    if area < 0:
        x1, y1, x2, y2 = x2, y2, x1, y1

    min_x = max(0, int(min(x0, x1, x2)))
    max_x = min(width - 1, int(max(x0, x1, x2) + 1))
    min_y = max(0, int(min(y0, y1, y2)))
    max_y = min(height - 1, int(max(y0, y1, y2) + 1))

    # Sub-pixel or tiny triangle: paint centroid pixel directly.
    # This is critical for deep segmentation leaves at material boundaries
    # that are smaller than a single pixel and would otherwise be skipped.
    bbox_w = max_x - min_x + 1
    bbox_h = max_y - min_y + 1
    if bbox_w <= 2 and bbox_h <= 2:
        cx = int((x0 + x1 + x2) / 3.0)
        cy = int((y0 + y1 + y2) / 3.0)
        if 0 <= cx < width and 0 <= cy < height:
            buf[cy, cx] = color
        return

    if min_x > max_x or min_y > max_y:
        return

    # Build pixel-center grid over the bounding box.
    xs = np.arange(min_x, max_x + 1, dtype=np.float64) + 0.5
    ys = np.arange(min_y, max_y + 1, dtype=np.float64) + 0.5
    px, py = np.meshgrid(xs, ys)  # shape (ny, nx)

    # Vectorized edge functions: all pixels evaluated at once.
    e0 = (px - x0) * (y1 - y0) - (py - y0) * (x1 - x0)
    e1 = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    e2 = (px - x2) * (y0 - y2) - (py - y2) * (x0 - x2)

    # Tight threshold: minimal bleed between adjacent sub-triangles.
    # Gap closer handles any remaining single-pixel seams.
    mask = (e0 >= -0.25) & (e1 >= -0.25) & (e2 >= -0.25)

    # Write color to all inside pixels in one shot.
    buf[min_y:max_y + 1, min_x:max_x + 1][mask] = color


def _dilate_pass(buf: np.ndarray, min_neighbors: int) -> np.ndarray:
    """
    Single morphological dilation pass: fills transparent pixels that have
    at least `min_neighbors` opaque 4-connected neighbors, using the color
    of the majority neighbor.

    :param buf: Numpy array of shape (H, W, 4)
    :param min_neighbors: Minimum opaque neighbor count to trigger fill (1-4)
    :return: Modified buffer
    """
    alpha = buf[:, :, 3]
    transparent = alpha < 0.5
    if not np.any(transparent):
        return buf

    # Shift arrays to get 4-connected neighbors. Zero edges to prevent wrapping.
    left = np.roll(buf, 1, axis=1)
    left[:, 0] = 0
    right = np.roll(buf, -1, axis=1)
    right[:, -1] = 0
    up = np.roll(buf, 1, axis=0)
    up[0, :] = 0
    down = np.roll(buf, -1, axis=0)
    down[-1, :] = 0

    left_opaque = left[:, :, 3] > 0.5
    right_opaque = right[:, :, 3] > 0.5
    up_opaque = up[:, :, 3] > 0.5
    down_opaque = down[:, :, 3] > 0.5
    count = (
        left_opaque.astype(np.int8)
        + right_opaque.astype(np.int8)
        + up_opaque.astype(np.int8)
        + down_opaque.astype(np.int8)
    )

    fill_mask = transparent & (count >= min_neighbors)
    if not np.any(fill_mask):
        return buf

    # Take color from any opaque neighbor (last writer wins; order is arbitrary
    # but consistent — down, up, right, left priority).
    fill_color = np.zeros_like(buf)
    for neighbor, opaque in [
        (down, down_opaque),
        (up, up_opaque),
        (right, right_opaque),
        (left, left_opaque),
    ]:
        opaque_3d = opaque[:, :, np.newaxis]
        fill_color = np.where(opaque_3d, neighbor, fill_color)

    fill_3d = fill_mask[:, :, np.newaxis]
    buf = np.where(fill_3d, fill_color, buf)
    return buf


def close_gaps_in_texture(
    buf: np.ndarray, width: int, height: int
) -> np.ndarray:
    """
    Two-pass morphological dilation to seal edge gaps between triangles.

    With two-pass rendering (parent fill + painted overdraw), internal face gaps
    are already eliminated. This dilation catches single-pixel seams at triangle
    edges that the rasterizer misses due to floating-point precision.

    Pass 1: Fill pixels with >= 2 opaque neighbors (safe, directional consensus).
    Pass 2: Fill pixels with >= 1 opaque neighbor  (catches remaining edge pixels).

    This expands by at most 2 pixels total — enough to seal rasterization seams
    but not enough to bleed across UV island gaps.

    :param buf: Numpy array of shape (H, W, 4)
    :param width: Image width
    :param height: Image height
    :return: Modified buffer
    """
    buf = _dilate_pass(buf, min_neighbors=2)
    buf = _dilate_pass(buf, min_neighbors=1)
    return buf


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

    Optimized path: numpy (H, W, 4) pixel buffer, bulk UV reads via
    foreach_get, vectorized rasterization and gap closing.

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
    # angle_limit is in RADIANS (1.15192 rad ≈ 66°).
    # area_weight=0.9 allocates UV space proportional to 3D face area,
    # giving larger (more visible) faces more texture pixels.
    # margin_method='SCALED' scales margins with island size for efficient packing.
    bpy.ops.uv.smart_project(
        angle_limit=1.15192,
        margin_method='SCALED',
        rotate_method='AXIS_ALIGNED',
        island_margin=0.002,
        area_weight=0.6,
        correct_aspect=True,
        scale_to_bounds=False,
    )

    bpy.ops.object.mode_set(mode="OBJECT")

    # Bulk-read all UVs into a numpy array instead of per-loop attribute access.
    uv_layer_data = mesh.uv_layers.active.data
    num_loops = len(uv_layer_data)
    uv_flat = np.zeros(num_loops * 2, dtype=np.float64)
    uv_layer_data.foreach_get("uv", uv_flat)
    all_uvs = uv_flat.reshape(-1, 2)

    image_name = f"{obj.name}_segmentation"
    image = bpy.data.images.new(
        image_name, width=texture_size, height=texture_size, alpha=True
    )

    # Pre-build color lookup table for fast indexed access.
    max_color_idx = max(extruder_colors.keys()) if extruder_colors else 0
    color_table = np.full((max_color_idx + 2, 4), [0.5, 0.5, 0.5, 1.0], dtype=np.float32)
    for idx, col in extruder_colors.items():
        color_table[idx] = col

    default_color_index = default_extruder - 1
    default_color = color_table[min(default_color_index, len(color_table) - 1)]

    # Numpy pixel buffer: (H, W, 4) — pre-fill with the default/base color so
    # the entire texture starts as the base filament.  Any UV regions not
    # covered by segmentation data will naturally show the base color.
    buf = np.empty((texture_size, texture_size, 4), dtype=np.float32)
    buf[:] = default_color

    decode_failures = 0
    subdivision_failures = 0

    for face_idx, seg_string in seg_strings.items():
        if face_idx >= len(mesh.polygons):
            continue

        poly = mesh.polygons[face_idx]
        if len(poly.loop_indices) != 3:
            continue

        loop_indices = list(poly.loop_indices)
        uv0 = all_uvs[loop_indices[0]]
        uv1 = all_uvs[loop_indices[1]]
        uv2 = all_uvs[loop_indices[2]]

        tree = decode_segmentation_string(seg_string)
        if tree is None:
            decode_failures += 1
            debug(
                f"    WARNING: Failed to decode segmentation for face {face_idx}: '{seg_string}'"
            )
            render_triangle_to_image(
                buf, texture_size, texture_size, uv0, uv1, uv2, default_color
            )
            continue

        sub_triangles = subdivide_in_uv_space(uv0, uv1, uv2, tree)

        if not sub_triangles:
            subdivision_failures += 1
            debug(f"    WARNING: Subdivision produced no triangles for face {face_idx}")
            render_triangle_to_image(
                buf, texture_size, texture_size, uv0, uv1, uv2, default_color
            )
            continue

        # Two-pass rendering for clean material boundaries:
        # Pass 1: Fill entire parent triangle with default color (guarantees
        #         zero gaps within the face — every pixel is covered).
        # Pass 2: Overdraw only the non-default (painted) sub-triangles on top
        #         so paint always wins at ambiguous boundary pixels.
        render_triangle_to_image(
            buf, texture_size, texture_size, uv0, uv1, uv2, default_color
        )

        for sub_uv0, sub_uv1, sub_uv2, state in sub_triangles:
            if state == TriangleState.DEFAULT or state == 0:
                continue  # Already covered by pass 1
            ci = int(state) - 1
            if 0 <= ci < len(color_table):
                color = color_table[ci]
            else:
                color = np.array([0.5, 0.5, 0.5, 1.0], dtype=np.float32)

            render_triangle_to_image(
                buf, texture_size, texture_size, sub_uv0, sub_uv1, sub_uv2, color
            )

    # Fill any faces without segmentation with the default color.
    for face_idx, poly in enumerate(mesh.polygons):
        if face_idx in seg_strings:
            continue
        if len(poly.loop_indices) != 3:
            continue

        loop_indices = list(poly.loop_indices)
        uv0 = all_uvs[loop_indices[0]]
        uv1 = all_uvs[loop_indices[1]]
        uv2 = all_uvs[loop_indices[2]]
        render_triangle_to_image(
            buf, texture_size, texture_size, uv0, uv1, uv2, default_color
        )

    if decode_failures > 0 or subdivision_failures > 0:
        debug(
            f"  Segmentation processing: {decode_failures} decode failures, {subdivision_failures} subdivision failures"
        )

    buf = close_gaps_in_texture(buf, texture_size, texture_size)

    # Bulk-write pixels to Blender image.
    image.pixels.foreach_set(buf.ravel())
    image.pack()

    return image
