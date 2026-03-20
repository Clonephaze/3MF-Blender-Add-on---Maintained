# Blender add-on to import and export 3MF files.
# Copyright (C) 2026 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Vertex Color Fast Path — detect and rasterize vertex/corner color data.

Provides functions to bypass Cycles baking entirely when the material's
Base Color is driven by a Color Attribute node.  Instead, per-face filament
assignments are computed directly from vertex colors and rasterized as flat
per-face textures.

Functions:
- ``_detect_vertex_color_source`` — check if Base Color uses vertex colors
- ``_rasterize_vertex_colors`` — barycentric rasterization of corner colors
- ``_compute_face_filaments`` — per-polygon nearest-filament assignment
- ``_rasterize_face_colors`` — flat per-face colour fill via UV triangles
"""

import numpy as np

from ..common.logging import debug
from .quantize import _rgb_to_hsv


# ---------------------------------------------------------------------------
#  Vertex Color Fast Path — skip Cycles bake entirely
# ---------------------------------------------------------------------------

def _detect_vertex_color_source(obj):
    """Detect if a material's Base Color is driven by a Color Attribute node.

    Returns the name of the color attribute layer, or ``None`` if the
    material doesn't use vertex colors.

    Checks every material slot — returns the first vertex-color source
    found (models with vertex colors typically have a single material).
    """
    if not obj or not obj.data or not obj.data.materials:
        return None

    # Blender node types that read vertex/corner color data
    vc_types = {"VERTEX_COLOR", "COLOR_ATTRIBUTE"}
    vc_idnames = {"ShaderNodeVertexColor", "ShaderNodeColorAttribute"}
    attr_types = {"ATTRIBUTE"}
    attr_idnames = {"ShaderNodeAttribute"}

    for mat in obj.data.materials:
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            base_input = node.inputs.get("Base Color")
            if not base_input or not base_input.is_linked:
                continue
            src = base_input.links[0].from_node
            debug(f"[Detect VC] Base Color source: type='{src.type}', "
                  f"bl_idname='{src.bl_idname}'")

            attr_name = ""
            if src.type in vc_types or src.bl_idname in vc_idnames:
                attr_name = str(getattr(src, "layer_name", ""))
            elif src.type in attr_types or src.bl_idname in attr_idnames:
                attr_name = str(getattr(src, "attribute_name", ""))
            else:
                continue

            # Empty name means "use the active color attribute"
            if not attr_name:
                ca = getattr(obj.data, "color_attributes", None)
                if ca and ca.active_color:
                    attr_name = str(ca.active_color.name)
                    debug(f"[Detect VC] Empty layer name, "
                          f"using active: '{attr_name}'")

            if attr_name:
                debug(f"[Detect VC] Found vertex color attr: '{attr_name}'")
                return attr_name

    debug("[Detect VC] No vertex color source found")
    return None


def _rasterize_vertex_colors(mesh, uv_layer_name, color_attr_name, width, height):
    """Rasterize per-corner vertex colors to a UV texture image.

    Reads the color attribute data (CORNER or POINT domain) and the UV
    coordinates, then fills the texture by barycentric interpolation of
    the three corner colors for each triangle.

    Uses ``mesh.loop_triangles`` for fast tessellation, batches triangles
    by bounding-box area to limit per-triangle Python overhead.

    :param mesh: ``bpy.types.Mesh``.
    :param uv_layer_name: Name of the UV layer to use for rasterization.
    :param color_attr_name: Name of the color attribute to read.
    :param width: Target image width.
    :param height: Target image height.
    :return: (H, W, 4) float32 pixel array (linear RGB + alpha=1).
    """
    uv_layer = mesh.uv_layers.get(uv_layer_name)
    if uv_layer is None:
        return None

    ca = mesh.color_attributes.get(color_attr_name)
    if ca is None:
        ca = mesh.color_attributes.active_color
    if ca is None or len(ca.data) == 0:
        return None

    # Read UV coordinates — per-loop, scaled to pixel space
    n_loops = len(uv_layer.data)
    uv_flat = np.empty(n_loops * 2, dtype=np.float64)
    uv_layer.data.foreach_get("uv", uv_flat)
    uvs_px = uv_flat.reshape(n_loops, 2).copy()
    uvs_px[:, 0] *= width
    uvs_px[:, 1] *= height

    # Read color data
    n_elems = len(ca.data)
    color_flat = np.empty(n_elems * 4, dtype=np.float32)
    ca.data.foreach_get("color", color_flat)
    colors = color_flat.reshape(n_elems, 4)[:, :3]  # (N, 3) RGB

    is_corner = (ca.domain == "CORNER")
    if not is_corner:
        loop_vert_idx = np.empty(n_loops, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_vert_idx)

    # Use Blender's tessellated loop triangles for speed
    mesh.calc_loop_triangles()
    n_tris = len(mesh.loop_triangles)
    if n_tris == 0:
        return None

    tri_loops = np.empty(n_tris * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    tri_loops = tri_loops.reshape(n_tris, 3)  # (T, 3) loop indices

    debug(f"[Rasterize VC] {n_tris} triangles, {width}x{height} texture")

    # Pre-fetch all triangle UV coords and colors
    # UV: (T, 3, 2)
    uv0 = uvs_px[tri_loops[:, 0]]  # (T, 2)
    uv1 = uvs_px[tri_loops[:, 1]]
    uv2 = uvs_px[tri_loops[:, 2]]

    if is_corner:
        c0 = colors[tri_loops[:, 0]]  # (T, 3)
        c1 = colors[tri_loops[:, 1]]
        c2 = colors[tri_loops[:, 2]]
    else:
        c0 = colors[loop_vert_idx[tri_loops[:, 0]]]
        c1 = colors[loop_vert_idx[tri_loops[:, 1]]]
        c2 = colors[loop_vert_idx[tri_loops[:, 2]]]

    # Bounding boxes per triangle
    all_x = np.stack([uv0[:, 0], uv1[:, 0], uv2[:, 0]], axis=1)
    all_y = np.stack([uv0[:, 1], uv1[:, 1], uv2[:, 1]], axis=1)
    bb_min_x = np.clip(np.floor(all_x.min(axis=1)).astype(np.int32), 0, width - 1)
    bb_max_x = np.clip(np.floor(all_x.max(axis=1)).astype(np.int32) + 1, 0, width)
    bb_min_y = np.clip(np.floor(all_y.min(axis=1)).astype(np.int32), 0, height - 1)
    bb_max_y = np.clip(np.floor(all_y.max(axis=1)).astype(np.int32) + 1, 0, height)

    # Denominator for barycentric coordinates
    denom = (
        (uv1[:, 1] - uv2[:, 1]) * (uv0[:, 0] - uv2[:, 0])
        + (uv2[:, 0] - uv1[:, 0]) * (uv0[:, 1] - uv2[:, 1])
    )

    # Filter degenerate triangles and empty bounding boxes
    valid = (
        (np.abs(denom) > 1e-10)
        & (bb_min_x < bb_max_x)
        & (bb_min_y < bb_max_y)
    )
    indices = np.where(valid)[0]
    debug(f"[Rasterize VC] {len(indices)} valid triangles")

    # Output image
    pixels = np.zeros((height, width, 4), dtype=np.float32)

    # Separate tiny (≤2px wide/tall) vs large triangles.
    # On high-poly meshes the vast majority of triangles cover only 1–4
    # pixels in UV space. We handle those with a single vectorized centroid
    # fill — no per-triangle mgrid needed.
    bb_w = bb_max_x[indices] - bb_min_x[indices]
    bb_h = bb_max_y[indices] - bb_min_y[indices]
    tiny_mask = (bb_w <= 2) & (bb_h <= 2)
    tiny_idx = indices[tiny_mask]
    large_idx = indices[~tiny_mask]
    debug(f"[Rasterize VC] tiny={len(tiny_idx)}, large={len(large_idx)}")

    # --- Fast path: tiny triangles — fill centroid pixel with avg color ---
    if len(tiny_idx) > 0:
        cx = ((uv0[tiny_idx, 0] + uv1[tiny_idx, 0] + uv2[tiny_idx, 0])
              / 3.0).astype(np.int32)
        cy = ((uv0[tiny_idx, 1] + uv1[tiny_idx, 1] + uv2[tiny_idx, 1])
              / 3.0).astype(np.int32)
        cx = np.clip(cx, 0, width - 1)
        cy = np.clip(cy, 0, height - 1)
        avg_r = (c0[tiny_idx, 0] + c1[tiny_idx, 0] + c2[tiny_idx, 0]) / 3.0
        avg_g = (c0[tiny_idx, 1] + c1[tiny_idx, 1] + c2[tiny_idx, 1]) / 3.0
        avg_b = (c0[tiny_idx, 2] + c1[tiny_idx, 2] + c2[tiny_idx, 2]) / 3.0
        # Use np.maximum.at to handle overlaps (last write wins is fine)
        pixels[cy, cx, 0] = avg_r
        pixels[cy, cx, 1] = avg_g
        pixels[cy, cx, 2] = avg_b
        pixels[cy, cx, 3] = 1.0

    # --- Standard path: large triangles — barycentric rasterization ---
    for idx, ti in enumerate(large_idx):
        x0, x1 = int(bb_min_x[ti]), int(bb_max_x[ti])
        y0, y1 = int(bb_min_y[ti]), int(bb_max_y[ti])
        d = denom[ti]

        ys, xs = np.mgrid[y0:y1, x0:x1]
        px = xs + 0.5
        py = ys + 0.5

        a = ((uv1[ti, 1] - uv2[ti, 1]) * (px - uv2[ti, 0])
             + (uv2[ti, 0] - uv1[ti, 0]) * (py - uv2[ti, 1])) / d
        b = ((uv2[ti, 1] - uv0[ti, 1]) * (px - uv2[ti, 0])
             + (uv0[ti, 0] - uv2[ti, 0]) * (py - uv2[ti, 1])) / d
        c_bary = 1.0 - a - b

        inside = (a >= -0.001) & (b >= -0.001) & (c_bary >= -0.001)
        if not np.any(inside):
            continue

        r = (a * c0[ti, 0] + b * c1[ti, 0] + c_bary * c2[ti, 0])
        g = (a * c0[ti, 1] + b * c1[ti, 1] + c_bary * c2[ti, 1])
        bl = (a * c0[ti, 2] + b * c1[ti, 2] + c_bary * c2[ti, 2])

        patch = pixels[y0:y1, x0:x1]
        patch[:, :, 0] = np.where(inside, r, patch[:, :, 0])
        patch[:, :, 1] = np.where(inside, g, patch[:, :, 1])
        patch[:, :, 2] = np.where(inside, bl, patch[:, :, 2])
        patch[:, :, 3] = np.where(inside, 1.0, patch[:, :, 3])

    return pixels


def _compute_face_filaments(mesh, color_attr_name, filament_colors):
    """Assign each polygon to its nearest filament from vertex/corner colors.

    For each polygon the corner colours are averaged, then the closest
    palette entry is found using an HSV-weighted perceptual distance
    consistent with ``_hue_aware_distance``.  Fully vectorised —
    handles 350K+ faces in milliseconds.

    :param mesh: ``bpy.types.Mesh``.
    :param color_attr_name: Name of the color attribute to read.
    :param filament_colors: List of (r, g, b) tuples in [0, 1] range.
    :return: (F,) int32 array of filament indices per polygon, or None.
    """
    ca = mesh.color_attributes.get(color_attr_name)
    if ca is None:
        ca = mesh.color_attributes.active_color
    if ca is None or len(ca.data) == 0:
        return None

    n_polys = len(mesh.polygons)
    if n_polys == 0:
        return None

    # Read colour data
    n_elems = len(ca.data)
    color_flat = np.empty(n_elems * 4, dtype=np.float32)
    ca.data.foreach_get("color", color_flat)
    colors = color_flat.reshape(n_elems, 4)[:, :3]  # (N, 3) RGB

    is_corner = (ca.domain == "CORNER")

    # Polygon structure
    loop_starts = np.empty(n_polys, dtype=np.int32)
    loop_totals = np.empty(n_polys, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", loop_starts)
    mesh.polygons.foreach_get("loop_total", loop_totals)

    # Map each loop → face index
    face_idx = np.repeat(np.arange(n_polys, dtype=np.int32), loop_totals)
    n_loops_total = int(loop_totals.sum())

    # Sum colours per face
    face_sums = np.zeros((n_polys, 3), dtype=np.float64)
    if is_corner:
        np.add.at(face_sums, face_idx, colors[:n_loops_total])
    else:
        n_loops = len(mesh.loops)
        loop_vert_idx = np.empty(n_loops, dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_vert_idx)
        np.add.at(face_sums, face_idx, colors[loop_vert_idx[:n_loops_total]])

    face_avg = (face_sums / loop_totals[:, np.newaxis]).astype(np.float32)

    # HSV-weighted nearest-palette lookup (vectorised)
    palette = np.array(filament_colors, dtype=np.float32)
    palette_hsv = _rgb_to_hsv(palette)
    face_hsv = _rgb_to_hsv(face_avg)

    f_h = face_hsv[:, np.newaxis, :]   # (F, 1, 3)
    p_h = palette_hsv[np.newaxis, :, :]  # (1, N, 3)

    dh = np.abs(f_h[..., 0] - p_h[..., 0])
    dh = np.minimum(dh, 1.0 - dh)
    ds = f_h[..., 1] - p_h[..., 1]
    dv = f_h[..., 2] - p_h[..., 2]
    hsv_dist = 6.0 * dh ** 2 + 4.0 * ds ** 2 + 0.05 * dv ** 2

    # Achromatic handling
    face_v = face_hsv[:, np.newaxis, 2]
    pal_v = palette_hsv[np.newaxis, :, 2]
    val_dist = (face_v - pal_v) ** 2
    pal_sat = palette_hsv[np.newaxis, :, 1]
    achromatic_dist = val_dist + 2.0 * pal_sat ** 2

    f_sat = face_hsv[:, 1:2]
    f_val = face_hsv[:, 2:3]
    alpha = np.clip(f_sat / 0.08, 0.0, 1.0) * np.clip(f_val / 0.12, 0.0, 1.0)

    dist = alpha * hsv_dist + (1.0 - alpha) * achromatic_dist
    debug(f"[Face filaments] {n_polys} polygons assigned in one pass")
    return np.argmin(dist, axis=1).astype(np.int32)


def _rasterize_face_colors(mesh, uv_layer_name, face_colors, width, height):
    """Fill a UV texture with flat per-face colours (fully vectorised).

    Each polygon's UV footprint is filled with a single solid colour —
    no barycentric colour interpolation.  Triangles are bucketed by
    bounding-box size and each bucket is processed as a single NumPy
    batch, making this fast even for 350K+ faces.

    :param mesh: ``bpy.types.Mesh``.
    :param uv_layer_name: Name of the UV layer.
    :param face_colors: (F, 3) float32 RGB colours, one per polygon.
    :param width: Target image width.
    :param height: Target image height.
    :return: (H, W, 4) float32 pixel array, or None.
    """
    uv_layer = mesh.uv_layers.get(uv_layer_name)
    if uv_layer is None:
        return None

    n_loops = len(uv_layer.data)
    uv_flat = np.empty(n_loops * 2, dtype=np.float64)
    uv_layer.data.foreach_get("uv", uv_flat)
    uvs_px = uv_flat.reshape(n_loops, 2).copy()
    uvs_px[:, 0] *= width
    uvs_px[:, 1] *= height

    mesh.calc_loop_triangles()
    n_tris = len(mesh.loop_triangles)
    if n_tris == 0:
        return None

    tri_loops = np.empty(n_tris * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    tri_loops = tri_loops.reshape(n_tris, 3)

    tri_polys = np.empty(n_tris, dtype=np.int32)
    mesh.loop_triangles.foreach_get("polygon_index", tri_polys)
    tri_rgb = face_colors[tri_polys]  # (T, 3) — flat colour per tri

    uv0 = uvs_px[tri_loops[:, 0]]
    uv1 = uvs_px[tri_loops[:, 1]]
    uv2 = uvs_px[tri_loops[:, 2]]

    all_x = np.stack([uv0[:, 0], uv1[:, 0], uv2[:, 0]], axis=1)
    all_y = np.stack([uv0[:, 1], uv1[:, 1], uv2[:, 1]], axis=1)
    x_lo = np.clip(np.floor(all_x.min(axis=1)).astype(np.int32), 0, width - 1)
    x_hi = np.clip(np.floor(all_x.max(axis=1)).astype(np.int32) + 1, 0, width)
    y_lo = np.clip(np.floor(all_y.min(axis=1)).astype(np.int32), 0, height - 1)
    y_hi = np.clip(np.floor(all_y.max(axis=1)).astype(np.int32) + 1, 0, height)

    bb_w = x_hi - x_lo
    bb_h = y_hi - y_lo
    den = (
        (uv1[:, 1] - uv2[:, 1]) * (uv0[:, 0] - uv2[:, 0])
        + (uv2[:, 0] - uv1[:, 0]) * (uv0[:, 1] - uv2[:, 1])
    )

    valid = (np.abs(den) > 1e-10) & (bb_w > 0) & (bb_h > 0)
    v_idx = np.where(valid)[0]

    pixels = np.zeros((height, width, 4), dtype=np.float32)
    max_dim = np.maximum(bb_w[v_idx], bb_h[v_idx])

    # --- Tiny triangles (≤ 2 px) — vectorised centroid fill -----------
    tiny_mask = max_dim <= 2
    tiny = v_idx[tiny_mask]
    if len(tiny) > 0:
        cx = ((uv0[tiny, 0] + uv1[tiny, 0] + uv2[tiny, 0])
              / 3.0).astype(np.int32)
        cy = ((uv0[tiny, 1] + uv1[tiny, 1] + uv2[tiny, 1])
              / 3.0).astype(np.int32)
        np.clip(cx, 0, width - 1, out=cx)
        np.clip(cy, 0, height - 1, out=cy)
        pixels[cy, cx, :3] = tri_rgb[tiny]
        pixels[cy, cx, 3] = 1.0

    # --- Larger triangles — bucketed vectorised fill ------------------
    large = v_idx[~tiny_mask]
    large_dims = max_dim[~tiny_mask]
    debug(f"[Face fill] {len(tiny)} tiny, {len(large)} large triangles")

    buckets = [(3, 4), (5, 8), (9, 16), (17, 32), (33, 64), (65, 128)]
    for lo, hi in buckets:
        bmask = (large_dims >= lo) & (large_dims <= hi)
        bi = large[bmask]
        if len(bi) == 0:
            continue

        g = hi  # pad to bucket ceiling
        ys_r, xs_r = np.mgrid[0:g, 0:g]  # (g, g) relative offsets

        ax = x_lo[bi, None, None] + xs_r[None, :, :]  # (n, g, g)
        ay = y_lo[bi, None, None] + ys_r[None, :, :]
        px = ax.astype(np.float64) + 0.5
        py = ay.astype(np.float64) + 0.5

        u0x = uv0[bi, 0][:, None, None]
        u0y = uv0[bi, 1][:, None, None]
        u1x = uv1[bi, 0][:, None, None]
        u1y = uv1[bi, 1][:, None, None]
        u2x = uv2[bi, 0][:, None, None]
        u2y = uv2[bi, 1][:, None, None]
        d = den[bi][:, None, None]

        a = ((u1y - u2y) * (px - u2x) + (u2x - u1x) * (py - u2y)) / d
        b_bc = ((u2y - u0y) * (px - u2x) + (u0x - u2x) * (py - u2y)) / d
        c_bc = 1.0 - a - b_bc

        inside = (a >= -0.001) & (b_bc >= -0.001) & (c_bc >= -0.001)
        in_bounds = (
            (ax >= 0) & (ax < width) & (ay >= 0) & (ay < height)
            & (xs_r[None, :, :] < bb_w[bi, None, None])
            & (ys_r[None, :, :] < bb_h[bi, None, None])
        )
        fill = inside & in_bounds
        ti, gy, gx = np.where(fill)
        fy = ay[ti, gy, gx]
        fx = ax[ti, gy, gx]
        cols = tri_rgb[bi[ti]]
        pixels[fy, fx, :3] = cols
        pixels[fy, fx, 3] = 1.0

    # Very large triangles (> 128 px bbox) — rare, per-triangle fallback
    vl = large[large_dims > 128]
    for ti in vl:
        x0, x1 = int(x_lo[ti]), int(x_hi[ti])
        y0, y1 = int(y_lo[ti]), int(y_hi[ti])
        ys, xs = np.mgrid[y0:y1, x0:x1]
        px = xs + 0.5
        py = ys + 0.5
        d = den[ti]
        a = ((uv1[ti, 1] - uv2[ti, 1]) * (px - uv2[ti, 0])
             + (uv2[ti, 0] - uv1[ti, 0]) * (py - uv2[ti, 1])) / d
        b_bc = ((uv2[ti, 1] - uv0[ti, 1]) * (px - uv2[ti, 0])
                + (uv0[ti, 0] - uv2[ti, 0]) * (py - uv2[ti, 1])) / d
        c_bc = 1.0 - a - b_bc
        inside = (a >= -0.001) & (b_bc >= -0.001) & (c_bc >= -0.001)
        if not np.any(inside):
            continue
        col = tri_rgb[ti]
        patch = pixels[y0:y1, x0:x1]
        patch[:, :, 0] = np.where(inside, col[0], patch[:, :, 0])
        patch[:, :, 1] = np.where(inside, col[1], patch[:, :, 1])
        patch[:, :, 2] = np.where(inside, col[2], patch[:, :, 2])
        patch[:, :, 3] = np.where(inside, 1.0, patch[:, :, 3])

    # --- UV bleed: dilate filled pixels into empty neighbours -----------
    # Prevents visible seams at UV island edges during texture sampling.
    # Each pass expands the border by 1 pixel; 4 passes = 4px bleed.
    filled = pixels[:, :, 3] > 0
    for _ in range(4):
        empty = ~filled
        if not np.any(empty):
            break
        count = np.zeros_like(filled, dtype=np.float32)
        rgb_sum = np.zeros((height, width, 3), dtype=np.float32)
        for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            shifted = np.roll(np.roll(filled, dy, axis=0), dx, axis=1)
            shifted_rgb = np.roll(
                np.roll(pixels[:, :, :3], dy, axis=0), dx, axis=1,
            )
            mask = shifted & empty
            count += mask
            rgb_sum += mask[:, :, np.newaxis] * shifted_rgb
        expand = count > 0
        safe_count = np.where(expand, count, 1.0)
        pixels[:, :, :3] = np.where(
            expand[:, :, np.newaxis],
            rgb_sum / safe_count[:, :, np.newaxis],
            pixels[:, :, :3],
        )
        pixels[:, :, 3] = np.where(expand, 1.0, pixels[:, :, 3])
        filled = filled | expand

    return pixels
