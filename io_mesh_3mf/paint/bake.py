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
Bake to MMU — bake any material/shader to a quantized MMU paint texture.

Provides operators and panels that let users take procedural textures,
complex shader setups, or (in the future) geometry node color outputs and
convert them into discrete-color MMU paint textures for 3MF export.

Architecture:
- ``MMU_OT_bake_to_mmu`` — main operator: bake + quantize + setup properties
- ``MMU_OT_quantize_texture`` — standalone quantize (snap existing texture to filament colors)
- ``_draw_bake_panel()`` — shared draw function used by multiple space-type panels
- ``NODE_PT_mmu_bake`` — Shader Editor sidebar panel
- Future: ``NODE_PT_mmu_bake_gn`` — Geometry Nodes sidebar panel

The bake pipeline:
1. Ensure dedicated MMU_Paint UV layer exists (Smart UV or Lightmap Pack)
2. Create a target image at the chosen resolution
3. Bake the active material's diffuse output to the target image
4. Quantize: snap every pixel to the nearest filament color (numpy vectorized)
5. Set up 3mf_* custom properties so the export pipeline recognizes it
"""

import ast
import bmesh
import numpy as np
import bpy
import bpy.props
import bpy.types

from ..common.colors import hex_to_rgb as _rgb_from_hex
from ..common.colors import rgb_to_hex as _hex_from_rgb
from ..common.logging import debug, error


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _rgb_to_hsv(rgb):
    """Convert an (..., 3) float32 RGB array to HSV in-place-safe.

    H is in [0, 1] (cyclic), S and V in [0, 1].
    Uses the standard max/min algorithm, fully vectorized with numpy.
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    delta = maxc - minc

    # Value
    v = maxc

    # Saturation (0 where maxc == 0)
    safe_maxc = np.where(maxc > 0, maxc, 1.0)
    s = np.where(maxc > 0, delta / safe_maxc, 0.0)

    # Hue
    h = np.zeros_like(maxc)
    mask = delta > 0
    # Red is max
    idx = mask & (maxc == r)
    h[idx] = ((g[idx] - b[idx]) / delta[idx]) % 6.0
    # Green is max
    idx = mask & (maxc == g)
    h[idx] = ((b[idx] - r[idx]) / delta[idx]) + 2.0
    # Blue is max
    idx = mask & (maxc == b)
    h[idx] = ((r[idx] - g[idx]) / delta[idx]) + 4.0
    h = h / 6.0  # normalise to [0, 1]

    return np.stack([h, s, v], axis=-1).astype(np.float32)


def _compute_neighborhood_brightness(pixels: np.ndarray, kernel_size: int = 9) -> np.ndarray:
    """Compute local average brightness (VALUE) for each pixel.

    Uses a box filter (uniform average) to average the V channel in a
    neighborhood. This gives context for whether a grey pixel is likely
    a shadow on a bright surface or a highlight on a dark surface.

    Implemented with numpy integral image for O(H*W) performance regardless
    of kernel size (no scipy dependency).

    :param pixels: (H, W, 4) or (H, W, 3) float32 pixel array.
    :param kernel_size: Size of the averaging window (default 9).
    :return: (H, W) float32 array of local average brightness [0, 1].
    """
    # Compute VALUE = max(R, G, B) for each pixel
    rgb = pixels[:, :, :3]
    value = np.max(rgb, axis=2).astype(np.float64)  # (H, W)

    h, w = value.shape

    # Clamp kernel_size to image dimensions
    kernel_size = min(kernel_size, min(h, w))
    if kernel_size < 1:
        return value.astype(np.float32)

    half = kernel_size // 2

    # Pad for edge handling
    padded = np.pad(value, half, mode='edge')

    # Compute integral image (cumulative sum in both axes)
    # Prepend a row/col of zeros for clean indexing
    integral = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1), dtype=np.float64)
    integral[1:, 1:] = np.cumsum(np.cumsum(padded, axis=0), axis=1)

    # Vectorized box filter using integral image
    # For each output (y, x), sum the box from (y, x) to (y+k-1, x+k-1) in padded
    # Using integral: I[y+k, x+k] - I[y, x+k] - I[y+k, x] + I[y, x]
    k = kernel_size
    br = integral[k:k + h, k:k + w]   # bottom-right: (y+k, x+k)
    tl = integral[0:h, 0:w]           # top-left: (y, x)
    tr = integral[0:h, k:k + w]       # top-right: (y, x+k)
    bl = integral[k:k + h, 0:w]       # bottom-left: (y+k, x)

    box_sum = br - tr - bl + tl
    local_avg = box_sum / (k * k)

    return local_avg.astype(np.float32)


def _hue_aware_distance(pixel_hsv, palette_hsv, pixel_rgb, palette_rgb,
                        neighborhood_brightness=None):
    """Compute a perceptual distance that strongly weights chromaticity.

    For **chromatic** pixels (saturation > 0.08) we use a weighted HSV
    distance where hue and saturation dominate:

        d² = W_H·Δh² + W_S·Δs² + W_V·Δv²

    Hue is cyclic (wraps at 1.0) so Δh = min(|h1-h2|, 1-|h1-h2|).

    For **achromatic** pixels (greys, near-black) hue is meaningless so
    we use VALUE-based distance with a **neighborhood brightness bias**:
    prefer palette entries whose brightness matches the local context.

    :param neighborhood_brightness: Optional (H, W) array of local average
        brightness. When provided, achromatic pixels bias toward palette
        entries matching the local brightness context.

    Returns an (..., N) float32 distance array (one value per palette color).
    """
    W_H = 6.0   # Hue — dominant
    W_S = 4.0   # Saturation
    W_V = 0.05  # Value/brightness — near-zero for chromatic pixels

    # pixel_hsv: (..., 1, 3)   palette_hsv: (1, 1, N, 3)
    dh = np.abs(pixel_hsv[..., 0:1] - palette_hsv[..., 0:1])
    dh = np.minimum(dh, 1.0 - dh)  # cyclic wrap
    ds = pixel_hsv[..., 1:2] - palette_hsv[..., 1:2]
    dv = pixel_hsv[..., 2:3] - palette_hsv[..., 2:3]

    hsv_dist = W_H * dh ** 2 + W_S * ds ** 2 + W_V * dv ** 2
    hsv_dist = hsv_dist[..., 0]  # squeeze last dim

    # Achromatic distance: VALUE-based with neighborhood bias
    # Instead of Euclidean RGB, compare VALUE to palette VALUE
    pixel_val = pixel_hsv[..., 0, 2:3]  # (..., 1)
    palette_val = palette_hsv[..., 2]   # (N,) or (1, 1, N)

    # Base VALUE distance
    val_dist = (pixel_val - palette_val) ** 2  # (..., N)

    # Neighborhood brightness bias for achromatic pixels
    # If local neighborhood is bright, prefer brighter palette entries
    if neighborhood_brightness is not None:
        # neighborhood_brightness is (H, W), need to match pixel shape
        # pixel_hsv shape is (chunk_h, W, 1, 3) so we need (chunk_h, W, 1)
        shape = pixel_hsv.shape[:-1]  # (..., 1)
        if len(shape) == 3:
            # (chunk_h, W, 1) - slice the brightness for this chunk
            # This is handled by the caller passing the right slice
            nb = neighborhood_brightness
            if nb.ndim == 2:
                nb = nb[:, :, np.newaxis]  # (H, W, 1)
        else:
            nb = neighborhood_brightness

        # Bias: penalize palette entries that differ from neighborhood brightness
        # This makes a grey pixel in a bright neighborhood prefer white over black
        if isinstance(palette_val, np.ndarray) and palette_val.ndim >= 3:
            palette_val_flat = palette_val.flatten()
        else:
            palette_val_flat = palette_val

        # Context bias: distance from palette brightness to neighborhood brightness
        # Shape needs broadcasting: nb is (..., 1), palette_val is (..., N) or (N,)
        context_penalty = (nb - palette_val_flat) ** 2
        val_dist = val_dist + 0.5 * context_penalty  # weight the context

    # Also penalize chromatic palette entries for achromatic pixels
    # Grey shouldn't match red/yellow
    palette_sat = palette_hsv[..., 1]  # (N,) or (1, 1, N)
    chroma_penalty = 2.0 * palette_sat ** 2  # penalize saturated palette entries
    achromatic_dist = val_dist + chroma_penalty

    # Per-pixel saturation and value
    sat = pixel_hsv[..., 0, 1]  # (...)
    val = pixel_hsv[..., 0, 2]  # (...)
    sat = np.broadcast_to(sat[..., np.newaxis], hsv_dist.shape)
    val = np.broadcast_to(val[..., np.newaxis], hsv_dist.shape)

    # Blend: chromatic pixels use HSV distance, achromatic use VALUE+context
    sat_alpha = np.clip(sat / 0.08, 0.0, 1.0)
    val_alpha = np.clip(val / 0.12, 0.0, 1.0)
    alpha = sat_alpha * val_alpha
    return (alpha * hsv_dist + (1.0 - alpha) * achromatic_dist).astype(np.float32)


def _quantize_pixels(
    pixels: np.ndarray,
    filament_colors: list,
    use_neighborhood_context: bool = True,
) -> int:
    """
    Snap every pixel in the image to the nearest filament color.

    Uses a hue-dominant perceptual distance so that shadowed regions
    (e.g. dark red in shadow) still match the correct chromaticity
    rather than being pulled toward black or grey.

    For achromatic (grey) pixels, uses neighborhood brightness context
    to bias toward palette entries matching the local environment. This
    fixes the "shadow on white glove snaps to black" problem.

    Operates in-place on the (H, W, 4) float32 array.

    :param pixels: (H, W, 4) float32 pixel array, modified in-place.
    :param filament_colors: List of (r, g, b) tuples in [0, 1] range.
    :param use_neighborhood_context: Use local brightness for achromatic pixels.
    :return: Number of pixels that changed color.
    """
    index_map = _compute_palette_index_map(
        pixels, filament_colors, use_neighborhood_context,
    )
    palette = np.array(filament_colors, dtype=np.float32)

    old_rgb = pixels[:, :, :3].copy()
    pixels[:, :, :3] = palette[index_map]

    diff = np.any(np.abs(old_rgb - pixels[:, :, :3]) > 0.002, axis=2)
    return int(np.count_nonzero(diff))


def _compute_palette_index_map(
    pixels: np.ndarray,
    filament_colors: list,
    use_neighborhood_context: bool = True,
) -> np.ndarray:
    """Assign each pixel to its nearest filament color and return the index map.

    Uses the same HSV-weighted perceptual distance as the per-pixel
    quantizer (``_hue_aware_distance``), with optional neighborhood
    brightness context for achromatic pixels.

    This is the shared foundation for both ``_quantize_pixels`` (which
    applies the palette colours directly) and the region-based pipeline
    (which groups same-assignment pixels into regions first).

    :param pixels: (H, W, 4) or (H, W, 3) float32 pixel array (read-only).
    :param filament_colors: List of (r, g, b) tuples in [0, 1] range.
    :param use_neighborhood_context: Use local brightness for achromatic pixels.
    :return: (H, W) int32 array of palette indices.
    """
    height, width = pixels.shape[:2]

    palette = np.array(filament_colors, dtype=np.float32)  # (N, 3)
    n_colors = len(palette)
    palette_hsv = _rgb_to_hsv(palette)  # (N, 3)

    neighborhood_brightness = None
    if use_neighborhood_context:
        neighborhood_brightness = _compute_neighborhood_brightness(
            pixels, kernel_size=15,
        )

    index_map = np.zeros((height, width), dtype=np.int32)
    chunk_size = 256

    for y_start in range(0, height, chunk_size):
        y_end = min(y_start + chunk_size, height)
        chunk_rgb = pixels[y_start:y_end, :, :3]  # (chunk_h, W, 3)
        chunk_h = chunk_rgb.shape[0]

        chunk_hsv = _rgb_to_hsv(chunk_rgb)
        expanded_hsv = chunk_hsv.reshape(chunk_h, width, 1, 3)
        expanded_rgb = chunk_rgb.reshape(chunk_h, width, 1, 3)
        palette_hsv_exp = palette_hsv.reshape(1, 1, n_colors, 3)
        palette_rgb_exp = palette.reshape(1, 1, n_colors, 3)

        nb_chunk = None
        if neighborhood_brightness is not None:
            nb_chunk = neighborhood_brightness[y_start:y_end, :, np.newaxis]

        dists = _hue_aware_distance(
            expanded_hsv, palette_hsv_exp,
            expanded_rgb, palette_rgb_exp,
            neighborhood_brightness=nb_chunk,
        )
        index_map[y_start:y_end] = np.argmin(dists, axis=2)

    return index_map


# ---------------------------------------------------------------------------
#  UV Island Map — geometry-aware constraint for region segmentation
# ---------------------------------------------------------------------------

def _rasterize_island_map(mesh_data, uv_layer_name, width, height):
    """Rasterize a per-pixel UV island ID map from mesh geometry.

    Each pixel gets the island ID (1-based) of the UV polygon that covers
    it in texture space.  Pixels not covered by any polygon get 0.

    This allows the region segmentation to enforce hard boundaries at UV
    island edges, preventing regions from bleeding across unrelated parts
    of the model that happen to be adjacent in texture space.

    :param mesh_data: ``bpy.types.Mesh``.
    :param uv_layer_name: Name of the UV layer to read.
    :param width: Image width in pixels.
    :param height: Image height in pixels.
    :return: (H, W) int32 array of island IDs (0 = background).
    """
    uv_layer = mesh_data.uv_layers.get(uv_layer_name)
    if uv_layer is None:
        return np.zeros((height, width), dtype=np.int32)

    # Use bmesh to compute UV island indices per-face
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh_data)
        bm.faces.ensure_lookup_table()
        uv_lay = bm.loops.layers.uv.get(uv_layer_name)
        if uv_lay is None:
            return np.zeros((height, width), dtype=np.int32)

        # BFS over UV-connected faces to assign island IDs
        face_island = np.zeros(len(bm.faces), dtype=np.int32)
        island_id = 0
        for face in bm.faces:
            if face_island[face.index] != 0:
                continue
            island_id += 1
            queue = [face]
            face_island[face.index] = island_id
            while queue:
                current = queue.pop()
                for edge in current.edges:
                    if not edge.is_manifold:
                        continue
                    for linked in edge.link_faces:
                        if face_island[linked.index] != 0:
                            continue
                        # Faces sharing an edge with different UVs at
                        # that edge belong to different UV islands.
                        if _uv_edge_shared(current, linked, edge, uv_lay):
                            face_island[linked.index] = island_id
                            queue.append(linked)
    finally:
        bm.free()

    if island_id == 0:
        return np.zeros((height, width), dtype=np.int32)

    debug(f"Island map: found {island_id} UV islands")

    # Rasterize: for each polygon, fill its UV footprint with its island ID
    n_loops = len(uv_layer.data)
    uv_flat = np.empty(n_loops * 2, dtype=np.float64)
    uv_layer.data.foreach_get("uv", uv_flat)
    uvs = uv_flat.reshape(n_loops, 2)
    uvs[:, 0] *= width
    uvs[:, 1] *= height

    island_map = np.zeros((height, width), dtype=np.int32)

    for poly in mesh_data.polygons:
        iid = int(face_island[poly.index])
        if iid == 0:
            continue
        li = list(poly.loop_indices)
        v0x, v0y = uvs[li[0]]
        for k in range(1, len(li) - 1):
            v1x, v1y = uvs[li[k]]
            v2x, v2y = uvs[li[k + 1]]

            min_x = max(int(min(v0x, v1x, v2x)), 0)
            max_x = min(int(max(v0x, v1x, v2x)) + 1, width)
            min_y = max(int(min(v0y, v1y, v2y)), 0)
            max_y = min(int(max(v0y, v1y, v2y)) + 1, height)
            if min_x >= max_x or min_y >= max_y:
                continue

            denom = (v1y - v2y) * (v0x - v2x) + (v2x - v1x) * (v0y - v2y)
            if abs(denom) < 1e-10:
                continue

            ys, xs = np.mgrid[min_y:max_y, min_x:max_x]
            xs = xs.astype(np.float64) + 0.5
            ys = ys.astype(np.float64) + 0.5

            a = ((v1y - v2y) * (xs - v2x) + (v2x - v1x) * (ys - v2y)) / denom
            b = ((v2y - v0y) * (xs - v2x) + (v0x - v2x) * (ys - v2y)) / denom
            c = 1.0 - a - b

            inside = (a >= -0.001) & (b >= -0.001) & (c >= -0.001)
            island_map[min_y:max_y, min_x:max_x] = np.where(
                inside, iid, island_map[min_y:max_y, min_x:max_x]
            )

    return island_map


def _uv_edge_shared(face_a, face_b, edge, uv_lay):
    """Check if two bmesh faces share a UV-continuous edge.

    Two faces are UV-connected along *edge* if both endpoints have matching
    UV coordinates on both sides.  Differing UVs indicate a seam.
    """
    tol = 1e-5
    v0, v1 = edge.verts

    def _get_uv(face, vert):
        for loop in face.loops:
            if loop.vert == vert:
                return loop[uv_lay].uv
        return None

    uv_a0 = _get_uv(face_a, v0)
    uv_a1 = _get_uv(face_a, v1)
    uv_b0 = _get_uv(face_b, v0)
    uv_b1 = _get_uv(face_b, v1)

    if uv_a0 is None or uv_a1 is None or uv_b0 is None or uv_b1 is None:
        return False

    return (
        abs(uv_a0.x - uv_b0.x) < tol and abs(uv_a0.y - uv_b0.y) < tol
        and abs(uv_a1.x - uv_b1.x) < tol and abs(uv_a1.y - uv_b1.y) < tol
    )


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


# ---------------------------------------------------------------------------
#  Region-Based Quantization
# ---------------------------------------------------------------------------

def _flood_fill_segmentation(
    pixels: np.ndarray,
    similarity_threshold: float = 0.25,
    island_map: np.ndarray = None,
) -> tuple[np.ndarray, int]:
    """
    Segment an image into connected regions of similar color.

    This is the foundation of region-based quantization.  Instead of matching
    each pixel independently to a palette, we first identify contiguous
    regions of similar color, then characterize and quantize each region as
    a whole.

    Uses **edge-aware** neighbor comparison: the effective threshold is
    reduced at pixels with high local gradient magnitude (strong edges),
    allowing the algorithm to use a generous base threshold for shadow
    gradients while still respecting sharp material boundaries (e.g.
    skin-to-black, red-to-white).

    Implementation: fully-vectorized connected-components via iterative
    min-label propagation with pointer jumping.  All hot paths are numpy
    array operations — no per-pixel Python loops.

    :param pixels: (H, W, 4) or (H, W, 3) float32 pixel array.
    :param similarity_threshold: Max RGB distance (0–1 scale) to consider
        ADJACENT pixels as “similar” and part of the same region.
    :param island_map: Optional (H, W) int32 array of UV island IDs.
        When provided, pixels in different islands are never connected,
        preventing regions from bleeding across UV island boundaries.
    :return: Tuple of (region_map, num_regions) where *region_map* is
        (H, W) int32 with region IDs (0 = background/transparent,
        1…N = region IDs).
    """
    height, width = pixels.shape[:2]
    rgb = pixels[:, :, :3].astype(np.float32)
    alpha = pixels[:, :, 3] if pixels.shape[2] > 3 else None
    opaque = (alpha >= 0.01) if alpha is not None else np.ones(
        (height, width), dtype=bool
    )

    # --- vectorized edge-aware connectivity masks --------------------------
    gradient_mag = _compute_gradient_magnitude(rgb)
    safe_thresh = max(similarity_threshold, 1e-9)

    # Horizontal edges: pixel (y, x) ↔ pixel (y, x+1)
    h_diff = np.sqrt(
        np.sum((rgb[:, 1:] - rgb[:, :-1]) ** 2, axis=2)
    )  # (H, W-1)
    h_grad = np.maximum(gradient_mag[:, :-1], gradient_mag[:, 1:])
    h_factor = np.clip(1.0 - (h_grad / safe_thresh) * 0.7, 0.3, 1.0)
    h_connected = (
        (h_diff < similarity_threshold * h_factor)
        & opaque[:, :-1]
        & opaque[:, 1:]
    )

    # Vertical edges: pixel (y, x) ↔ pixel (y+1, x)
    v_diff = np.sqrt(
        np.sum((rgb[1:, :] - rgb[:-1, :]) ** 2, axis=2)
    )  # (H-1, W)
    v_grad = np.maximum(gradient_mag[:-1, :], gradient_mag[1:, :])
    v_factor = np.clip(1.0 - (v_grad / safe_thresh) * 0.7, 0.3, 1.0)
    v_connected = (
        (v_diff < similarity_threshold * v_factor)
        & opaque[:-1, :]
        & opaque[1:, :]
    )

    # --- UV island boundary enforcement -----------------------------------
    if island_map is not None:
        h_connected &= (island_map[:, :-1] == island_map[:, 1:])
        v_connected &= (island_map[:-1, :] == island_map[1:, :])

    # --- Phase 1: row-local labeling via cumsum ----------------------------
    # A new label starts at every pixel that is NOT horizontally connected
    # to its left neighbor.  np.cumsum turns this into sequential IDs.
    break_mask = np.ones((height, width), dtype=bool)
    if width > 1:
        break_mask[:, 1:] = ~h_connected
    labels = np.cumsum(break_mask.ravel()).astype(np.int32).reshape(
        height, width
    )
    max_label = int(labels.max())

    # --- Phase 2: merge row-segments via vertical connectivity -------------
    if height > 1:
        vy, vx = np.where(v_connected)
        if vy.size > 0:
            top_lab = labels[vy, vx]
            bot_lab = labels[vy + 1, vx]
            diff = top_lab != bot_lab

            if np.any(diff):
                edge_a = top_lab[diff]
                edge_b = bot_lab[diff]

                # Union-find on the compact parent array (size = num
                # row-segments, NOT H*W) via min-propagation + pointer
                # jumping.  Converges in ~10 iterations.
                parent = np.arange(max_label + 1, dtype=np.int32)

                for _it in range(100):
                    old_p = parent.copy()
                    np.minimum.at(parent, edge_a, parent[edge_b])
                    np.minimum.at(parent, edge_b, parent[edge_a])
                    parent = parent[parent]  # pointer jumping
                    if np.array_equal(parent, old_p):
                        break

                # Apply remapping to full image (one vectorized LUT call)
                labels = parent[labels]

    # --- relabel to contiguous 1..K (0 = transparent background) ----------
    labels[~opaque] = 0

    unique_labels = np.unique(labels[labels > 0])
    if len(unique_labels) == 0:
        return np.zeros((height, width), dtype=np.int32), 0

    max_lab = int(unique_labels.max())
    lut = np.zeros(max_lab + 1, dtype=np.int32)
    lut[unique_labels] = np.arange(
        1, len(unique_labels) + 1, dtype=np.int32
    )
    region_map = np.where(labels > 0, lut[np.clip(labels, 0, max_lab)], 0)

    return region_map.astype(np.int32), int(len(unique_labels))


def _compute_gradient_magnitude(rgb: np.ndarray) -> np.ndarray:
    """Compute per-pixel gradient magnitude from the RGB image.

    Uses central differences on each channel, takes the max across channels.
    This detects sharp color edges (skin→black, red→white) but is low for
    smooth shadow gradients.

    :param rgb: (H, W, 3) float32 image.
    :return: (H, W) float32 gradient magnitude in [0, ~1] range.
    """
    h, w = rgb.shape[:2]
    grad = np.zeros((h, w), dtype=np.float32)

    # Horizontal gradient (max across channels)
    if w > 2:
        dx = np.abs(rgb[:, 2:, :] - rgb[:, :-2, :])  # central diff
        grad[:, 1:-1] = np.max(dx, axis=2)

    # Vertical gradient (max across channels), take max with horizontal
    if h > 2:
        dy = np.abs(rgb[2:, :, :] - rgb[:-2, :, :])
        grad[1:-1, :] = np.maximum(grad[1:-1, :], np.max(dy, axis=2))

    return grad


def _merge_small_regions(
    region_map: np.ndarray,
    num_regions: int,
    min_region_size: int = 100,
) -> tuple[np.ndarray, int]:
    """
    Merge regions below a minimum pixel count into their largest neighbor.

    This eliminates banding artifacts (narrow gradient rings between
    materials) and fixes tiny mismatched regions.  Each small region is
    absorbed by its most common adjacent region (measured by shared border
    length).

    Uses vectorized border detection: all border pixel-pairs across the
    entire image are collected in one numpy pass, then counted via
    integer-encoding + ``np.unique``.  Only the merge-decision loop
    iterates in Python (over unique border *pairs* and small-region IDs,
    typically 100s–1000s of items — not millions of pixels).

    :param region_map: (H, W) int32 region IDs (0 = background).
    :param num_regions: Number of regions.
    :param min_region_size: Minimum pixel count for a region to survive.
    :return: (merged_region_map, new_num_regions).
    """
    if min_region_size <= 0:
        return region_map, num_regions

    merged = region_map.copy()

    for _pass in range(50):
        max_id = int(merged.max())
        if max_id == 0:
            break

        sizes = np.bincount(merged.ravel(), minlength=max_id + 1)

        # Identify small regions (skip background id=0)
        small_mask = np.zeros(max_id + 1, dtype=bool)
        small_mask[1:] = (sizes[1:] > 0) & (sizes[1:] < min_region_size)

        if not np.any(small_mask):
            break

        # --- find ALL border edges at once (vectorized) --------------------
        left, right = merged[:, :-1], merged[:, 1:]
        top, bottom = merged[:-1, :], merged[1:, :]
        h_border = (left != right) & (left > 0) & (right > 0)
        v_border = (top != bottom) & (top > 0) & (bottom > 0)

        pairs_list = []
        if np.any(h_border):
            pairs_list.append(
                np.stack([left[h_border], right[h_border]], axis=1)
            )
        if np.any(v_border):
            pairs_list.append(
                np.stack([top[v_border], bottom[v_border]], axis=1)
            )
        if not pairs_list:
            break

        all_pairs = np.concatenate(pairs_list, axis=0)  # (E, 2)

        # Count unique undirected pairs via integer encoding + np.unique
        p_min = np.minimum(all_pairs[:, 0], all_pairs[:, 1])
        p_max = np.maximum(all_pairs[:, 0], all_pairs[:, 1])
        encoded = p_min.astype(np.int64) * (max_id + 1) + p_max
        unique_enc, border_counts = np.unique(encoded, return_counts=True)
        pair_a = (unique_enc // (max_id + 1)).astype(np.int32)
        pair_b = (unique_enc % (max_id + 1)).astype(np.int32)

        # For each small region, find its neighbor with longest border
        # (iterating over unique border-pairs, NOT pixels)
        best_neighbor = np.zeros(max_id + 1, dtype=np.int32)
        best_border = np.zeros(max_id + 1, dtype=np.int32)

        for i in range(len(pair_a)):
            a, b, c = int(pair_a[i]), int(pair_b[i]), int(border_counts[i])
            if small_mask[a] and c > best_border[a]:
                best_border[a] = c
                best_neighbor[a] = b
            if small_mask[b] and c > best_border[b]:
                best_border[b] = c
                best_neighbor[b] = a

        # Build remap — process smallest regions first to avoid cycles
        remap = np.arange(max_id + 1, dtype=np.int32)
        small_ids = np.where(small_mask)[0]
        small_ids = small_ids[np.argsort(sizes[small_ids])]  # ascending

        current_sizes = sizes.copy()
        merged_any = False

        for rid in small_ids:
            # Follow remap chain to canonical ID
            canonical = int(rid)
            while remap[canonical] != canonical:
                canonical = int(remap[canonical])
            if current_sizes[canonical] >= min_region_size:
                continue  # already absorbed into something big enough
            if best_border[rid] == 0:
                continue

            # Follow target's remap chain
            target = int(best_neighbor[rid])
            while remap[target] != target:
                target = int(remap[target])

            if target == canonical:
                continue  # would merge into itself

            # Always merge the smaller side into the larger
            if current_sizes[canonical] <= current_sizes[target]:
                remap[canonical] = target
                current_sizes[target] += current_sizes[canonical]
                current_sizes[canonical] = 0
            else:
                remap[target] = canonical
                current_sizes[canonical] += current_sizes[target]
                current_sizes[target] = 0
            merged_any = True

        if not merged_any:
            break

        # Resolve all remap chains (path compression)
        for i in range(1, len(remap)):
            r = int(i)
            while remap[r] != r:
                remap[r] = remap[remap[r]]
                r = int(remap[r])
            remap[i] = r

        # Apply remap to entire image (single vectorized LUT operation)
        merged = remap[merged]

    # Renumber to contiguous 1…K
    unique_ids = np.unique(merged)
    unique_ids = unique_ids[unique_ids > 0]

    if len(unique_ids) == 0:
        return np.zeros_like(merged), 0

    max_id = int(unique_ids.max())
    lut = np.zeros(max_id + 1, dtype=np.int32)
    lut[unique_ids] = np.arange(1, len(unique_ids) + 1, dtype=np.int32)
    new_map = np.where(merged > 0, lut[np.clip(merged, 0, max_id)], 0)

    return new_map.astype(np.int32), int(len(unique_ids))


def _compute_region_representative_colors(
    pixels: np.ndarray,
    region_map: np.ndarray,
    num_regions: int,
) -> dict:
    """
    Compute a representative color for each region.

    Uses a two-stage approach:
    1. Compute the **mode cluster** — bin pixel brightnesses and pick the
       bin with the most pixels.  This usually corresponds to the "true"
       material color without shadow or highlight contamination.
    2. Average the colors within that mode bin.

    For very small regions (< 30 pixels), uses the median color instead.

    This is superior to percentile-based approaches because it correctly
    handles bimodal distributions (e.g., an object that's half in shadow)
    by picking the larger mode rather than an arbitrary percentile.

    :param pixels: (H, W, 4) float32 pixel array.
    :param region_map: (H, W) int32 region IDs from flood_fill_segmentation.
    :param num_regions: Number of regions (max region ID).
    :return: Dict mapping region_id (1..N) -> (r, g, b) representative color.
    """
    rgb = pixels[:, :, :3]

    # Flatten for easier indexing
    flat_rgb = rgb.reshape(-1, 3)
    flat_regions = region_map.flatten()

    representative_colors = {}

    for rid in range(1, num_regions + 1):
        mask = flat_regions == rid
        count = np.sum(mask)

        if count == 0:
            continue

        region_pixels = flat_rgb[mask]  # (N, 3)

        if count < 30:
            # Small region: just use median
            representative = np.median(region_pixels, axis=0)
        else:
            # Compute brightness (max of RGB = VALUE in HSV)
            brightness = np.max(region_pixels, axis=1)

            # Bin brightness into 10 bins and find the mode bin
            # This finds the "most common brightness level"
            num_bins = min(10, max(3, count // 20))
            bin_edges = np.linspace(brightness.min(), brightness.max() + 1e-6, num_bins + 1)
            bin_indices = np.digitize(brightness, bin_edges) - 1
            bin_indices = np.clip(bin_indices, 0, num_bins - 1)

            # Find which bin has the most pixels
            bin_counts = np.bincount(bin_indices, minlength=num_bins)
            mode_bin = np.argmax(bin_counts)

            # Average the colors in the mode bin
            mode_mask = bin_indices == mode_bin
            if np.sum(mode_mask) > 3:
                representative = np.mean(region_pixels[mode_mask], axis=0)
            else:
                representative = np.median(region_pixels, axis=0)

        representative_colors[rid] = tuple(representative)

    return representative_colors


def _match_colors_to_palette(
    representative_colors: dict,
    filament_colors: list,
) -> dict:
    """
    Match each region's representative color to the nearest palette color.

    Uses weighted HSV distance to favor hue/saturation matching over brightness.
    This ensures a "red in shadow" region matches red, not black.

    :param representative_colors: Dict mapping region_id -> (r, g, b).
    :param filament_colors: List of (r, g, b) tuples in [0, 1] range.
    :return: Dict mapping region_id -> palette_index.
    """
    palette = np.array(filament_colors, dtype=np.float32)  # (N, 3)
    palette_hsv = _rgb_to_hsv(palette)  # (N, 3)

    region_to_palette = {}

    for rid, rep_rgb in representative_colors.items():
        rep = np.array(rep_rgb, dtype=np.float32).reshape(1, 3)
        rep_hsv = _rgb_to_hsv(rep)[0]  # (3,)

        # Compute distance to each palette color
        # Weight hue most heavily, saturation second, value least
        W_H, W_S, W_V = 6.0, 4.0, 0.5

        best_idx = 0
        best_dist = float('inf')

        for i, pal_hsv in enumerate(palette_hsv):
            dh = abs(rep_hsv[0] - pal_hsv[0])
            dh = min(dh, 1.0 - dh)  # cyclic wrap
            ds = rep_hsv[1] - pal_hsv[1]
            dv = rep_hsv[2] - pal_hsv[2]

            # For achromatic colors, use brightness directly
            if rep_hsv[1] < 0.08:
                # Grey pixel - match by value, penalize saturated palette entries
                dist = (rep_hsv[2] - pal_hsv[2]) ** 2 + 2.0 * pal_hsv[1] ** 2
            else:
                # Chromatic pixel - weight hue heavily
                dist = W_H * dh ** 2 + W_S * ds ** 2 + W_V * dv ** 2

            if dist < best_dist:
                best_dist = dist
                best_idx = i

        region_to_palette[rid] = best_idx

    return region_to_palette


def _build_palette_regions(
    index_map: np.ndarray,
    opaque_mask: np.ndarray,
    island_map: np.ndarray = None,
) -> tuple[np.ndarray, int]:
    """Build connected components from a palette index map.

    Two adjacent pixels are connected if and only if they have the **same
    palette index**, are both opaque, and (optionally) belong to the same
    UV island.  This produces regions that are homogeneous by assignment —
    every pixel in a region was independently matched to the same filament.

    Uses the same vectorized min-label propagation as
    ``_flood_fill_segmentation`` but with a simpler connectivity criterion.

    :param index_map: (H, W) int32 array of palette indices.
    :param opaque_mask: (H, W) bool array (True for visible pixels).
    :param island_map: Optional (H, W) int32 UV island IDs.
    :return: (region_map, num_regions) — region IDs are 1-based.
    """
    height, width = index_map.shape

    # --- connectivity masks ------------------------------------------------
    h_connected = (
        (index_map[:, :-1] == index_map[:, 1:])
        & opaque_mask[:, :-1]
        & opaque_mask[:, 1:]
    )
    v_connected = (
        (index_map[:-1, :] == index_map[1:, :])
        & opaque_mask[:-1, :]
        & opaque_mask[1:, :]
    )

    if island_map is not None:
        h_connected &= (island_map[:, :-1] == island_map[:, 1:])
        v_connected &= (island_map[:-1, :] == island_map[1:, :])

    # --- Phase 1: row-local labels via cumsum ------------------------------
    break_mask = np.ones((height, width), dtype=bool)
    if width > 1:
        break_mask[:, 1:] = ~h_connected
    labels = np.cumsum(break_mask.ravel()).astype(np.int32).reshape(
        height, width,
    )
    max_label = int(labels.max())

    # --- Phase 2: merge row-segments via vertical connectivity -------------
    if height > 1:
        vy, vx = np.where(v_connected)
        if vy.size > 0:
            top_lab = labels[vy, vx]
            bot_lab = labels[vy + 1, vx]
            diff = top_lab != bot_lab

            if np.any(diff):
                edge_a = top_lab[diff]
                edge_b = bot_lab[diff]

                parent = np.arange(max_label + 1, dtype=np.int32)
                for _it in range(100):
                    old_p = parent.copy()
                    np.minimum.at(parent, edge_a, parent[edge_b])
                    np.minimum.at(parent, edge_b, parent[edge_a])
                    parent = parent[parent]
                    if np.array_equal(parent, old_p):
                        break
                labels = parent[labels]

    # --- relabel to contiguous 1..K ----------------------------------------
    labels[~opaque_mask] = 0
    unique_labels = np.unique(labels[labels > 0])
    if len(unique_labels) == 0:
        return np.zeros((height, width), dtype=np.int32), 0

    max_lab = int(unique_labels.max())
    lut = np.zeros(max_lab + 1, dtype=np.int32)
    lut[unique_labels] = np.arange(
        1, len(unique_labels) + 1, dtype=np.int32,
    )
    region_map = np.where(
        labels > 0, lut[np.clip(labels, 0, max_lab)], 0,
    )

    return region_map.astype(np.int32), int(len(unique_labels))


def _rebuild_region_palette(
    region_map: np.ndarray,
    index_map: np.ndarray,
    num_regions: int,
) -> dict:
    """Determine each region's palette index by majority vote.

    After ``_merge_small_regions``, a region may now contain pixels that
    were originally assigned to different palette indices.  This function
    counts palette index occurrences per region and picks the dominant one.

    :param region_map: (H, W) int32 region IDs (1-based).
    :param index_map: (H, W) int32 original per-pixel palette assignments.
    :param num_regions: Number of regions (max region ID).
    :return: Dict mapping region_id → palette_index.
    """
    mask = region_map > 0
    rids = region_map[mask].astype(np.int64)
    pidxs = index_map[mask].astype(np.int64)

    if len(pidxs) == 0:
        return {}

    max_palette = int(pidxs.max()) + 1

    # Encode (region_id, palette_idx) into a single int for bincount
    combined = rids * max_palette + pidxs
    counts = np.bincount(
        combined, minlength=(num_regions + 1) * max_palette,
    )
    counts = counts.reshape(num_regions + 1, max_palette)

    region_palette = {}
    for rid in range(1, num_regions + 1):
        row = counts[rid]
        if row.sum() > 0:
            region_palette[rid] = int(np.argmax(row))

    return region_palette


def _quantize_by_regions(
    pixels: np.ndarray,
    filament_colors: list,
    similarity_threshold: float = 0.25,
    min_region_size: int = 100,
    progress_callback=None,
    mesh_data=None,
    uv_layer_name: str = None,
) -> int:
    """
    Quantize an image using per-pixel palette matching with region cleanup.

    This is the recommended quantization method for textures with baked
    lighting, shadows, or other brightness variations within materials.

    Algorithm (color-first, clean-second):
      1. Assign each pixel to its nearest filament using HSV-weighted
         perceptual distance (hue-dominant, achromatic-aware).
      2. Build connected components from same-assignment pixels —
         regions are homogeneous by target color by construction.
      3. Merge small noise regions into their largest-border neighbor.
      4. Rebuild region→palette mapping via majority vote (handles
         cases where a merge absorbed differently-assigned pixels).
      5. Fill entire regions with their final palette color.

    This correctly handles complex baked textures because color matching
    is done **per-pixel first** using HSV distance (dark-red stays red,
    dark-grey stays black), and the region step only cleans up noise —
    it never assigns colors based on RGB similarity.

    Operates in-place on the (H, W, 4) float32 array.

    :param pixels: (H, W, 4) float32 pixel array, modified in-place.
    :param filament_colors: List of (r, g, b) tuples in [0, 1] range.
    :param similarity_threshold: Unused (kept for API compatibility).
    :param min_region_size: Regions below this pixel count get merged into neighbors.
    :param progress_callback: Optional callable(int) accepting 0-100 progress.
    :param mesh_data: Optional ``bpy.types.Mesh``.  When provided together
        with *uv_layer_name*, a UV island map is computed so that regions
        never span across UV island boundaries.
    :param uv_layer_name: Name of the UV layer to read island info from.
    :return: Number of pixels that changed color.
    """
    def _progress(value):
        if progress_callback is not None:
            progress_callback(int(value))

    debug(f"Region quantization: min_size={min_region_size}")

    height, width = pixels.shape[:2]
    alpha = pixels[:, :, 3] if pixels.shape[2] > 3 else None
    opaque = (alpha >= 0.01) if alpha is not None else np.ones(
        (height, width), dtype=bool,
    )

    # Step 0: Build UV island map if mesh data is available
    island_map = None
    if mesh_data is not None and uv_layer_name is not None:
        island_map = _rasterize_island_map(
            mesh_data, uv_layer_name, width, height,
        )
        debug(f"Region quantization: built island map "
              f"({int(island_map.max())} islands)")
    _progress(5)

    # Step 1: Per-pixel palette assignment  [5% -> 40%]
    index_map = _compute_palette_index_map(pixels, filament_colors)
    debug("Region quantization: per-pixel assignment complete")
    _progress(40)

    # Step 2: Connected components from same-palette pixels  [40% -> 60%]
    region_map, num_regions = _build_palette_regions(
        index_map, opaque, island_map,
    )
    debug(f"Region quantization: {num_regions} regions (before merge)")
    _progress(60)

    if num_regions == 0:
        _progress(100)
        return 0

    # Step 3: Merge small noise regions  [60% -> 80%]
    if min_region_size > 0:
        region_map, num_regions = _merge_small_regions(
            region_map, num_regions, min_region_size,
        )
        debug(f"Region quantization: {num_regions} regions after merge")
    _progress(80)

    # Step 4: Rebuild region→palette mapping (majority vote)  [80% -> 85%]
    region_palette = _rebuild_region_palette(
        region_map, index_map, num_regions,
    )
    _progress(85)

    # Step 5: Apply palette colors  [85% -> 100%]
    palette = np.array(filament_colors, dtype=np.float32)
    old_rgb = pixels[:, :, :3].copy()

    for rid, pal_idx in region_palette.items():
        mask = region_map == rid
        pixels[mask, :3] = palette[pal_idx]

    diff = np.any(np.abs(old_rgb - pixels[:, :, :3]) > 0.002, axis=2)
    changed = int(np.count_nonzero(diff & opaque))

    debug(f"Region quantization: changed {changed} pixels")
    _progress(100)
    return changed


# ---------------------------------------------------------------------------
#  Legacy: Majority Filter (UV-space smoothing)
# ---------------------------------------------------------------------------


def _apply_majority_filter(
    pixels: np.ndarray,
    filament_colors: list,
    kernel_size: int = 5,
    passes: int = 3,
) -> int:
    """
    Apply spatial coherence by flipping outlier pixels to the majority neighbor color.

    For each pixel, examine its (kernel_size × kernel_size) neighborhood. If the
    pixel's color differs from the majority of its neighbors, flip it to match.
    This fixes shadow bands that incorrectly snap to the wrong base color
    (e.g., shadow strips on a white glove snapping to black).

    Multiple passes allow wider shadow regions to erode inward: each pass flips
    the "frontier" pixels at the edge of the wrong-color region, eventually
    collapsing even 10-20 pixel wide bands.

    Operates in-place on the (H, W, 4) float32 array.

    :param pixels: (H, W, 4) float32 pixel array, modified in-place.
    :param filament_colors: List of (r, g, b) tuples in [0, 1] range.
    :param kernel_size: Size of the neighborhood window (must be odd, default 5).
    :param passes: Number of filter passes (default 3). More passes = wider bands fixed.
    :return: Total number of pixels that were flipped across all passes.
    """
    height, width = pixels.shape[:2]
    if kernel_size < 3 or kernel_size % 2 == 0:
        kernel_size = 5
    half = kernel_size // 2

    # Build palette array
    palette = np.array(filament_colors, dtype=np.float32)  # (N, 3)
    n_colors = len(palette)

    from numpy.lib.stride_tricks import sliding_window_view

    total_flipped = 0

    for pass_num in range(passes):
        # Map each pixel to its nearest palette index
        rgb = pixels[:, :, :3]
        diff = np.abs(rgb[:, :, np.newaxis, :] - palette[np.newaxis, np.newaxis, :, :])
        dist = np.sum(diff, axis=-1)  # (H, W, N)
        idx_map = np.argmin(dist, axis=2).astype(np.int32)  # (H, W)

        # Pad the index map for neighborhood lookup
        padded = np.pad(idx_map, half, mode="edge")
        windows = sliding_window_view(padded, (kernel_size, kernel_size))  # (H, W, k, k)

        flipped = 0
        new_idx_map = idx_map.copy()
        threshold = (kernel_size * kernel_size) // 2 + 1

        for y in range(height):
            for x in range(width):
                window = windows[y, x].flatten()
                center_idx = idx_map[y, x]

                counts = np.bincount(window, minlength=n_colors)
                majority_idx = np.argmax(counts)
                majority_count = counts[majority_idx]

                if center_idx != majority_idx and majority_count >= threshold:
                    new_idx_map[y, x] = majority_idx
                    flipped += 1

        if flipped > 0:
            pixels[:, :, :3] = palette[new_idx_map]
            total_flipped += flipped
        else:
            # No changes this pass, converged early
            break

    return total_flipped


def _ensure_uv_unwrap(obj, context):
    """Ensure the object has a dedicated MMU_Paint UV layer.

    Uses the UV method selected in MMUPaintSettings (Smart UV Project by
    default, Lightmap Pack as an option).

    A Limited Dissolve pass (angle ~0.5°) is applied first to merge coplanar
    triangles — this gives each remaining face more UV space and reduces
    blurriness, especially with Lightmap Pack.

    Any existing UVs (e.g. hand-crafted unwraps) are left untouched.
    The ``MMU_Paint`` layer is set as the **active render** layer so the
    bake writes to it; the caller is responsible for restoring the
    original active layer afterward if desired.

    Returns the name of the previously active UV layer (or ``None``)
    so the caller can restore it.
    """
    mesh = obj.data
    settings = context.scene.mmu_paint
    uv_method = settings.uv_method

    # Remember which UV layer was active before (if any)
    prev_active_name = None
    if mesh.uv_layers.active:
        prev_active_name = str(mesh.uv_layers.active.name)

    # Create or reuse the dedicated MMU_Paint UV layer
    mmu_layer = mesh.uv_layers.get("MMU_Paint")
    if mmu_layer is None:
        mmu_layer = mesh.uv_layers.new(name="MMU_Paint")

    # Set it as both the active and active-render layer
    mesh.uv_layers.active = mmu_layer
    mmu_layer.active_render = True

    context.view_layer.objects.active = obj

    # Limited Dissolve merges coplanar triangles, giving each face more
    # UV space and reducing blurriness.  ~0.5° is tight enough to only
    # merge truly flat faces while leaving curved surfaces intact.
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.dissolve_limit(
        bm, angle_limit=0.00873,
        verts=bm.verts, edges=bm.edges,
    )
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    # Must be in edit mode for UV operators
    prev_mode = obj.mode
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    if uv_method == "LIGHTMAP":
        bpy.ops.uv.lightmap_pack(
            PREF_CONTEXT="ALL_FACES",
            PREF_PACK_IN_ONE=True,
            PREF_NEW_UVLAYER=False,
            PREF_BOX_DIV=settings.lightmap_divisions,
            PREF_MARGIN_DIV=0.05,
        )
    else:
        bpy.ops.uv.smart_project(
            angle_limit=1.15192,
            margin_method="SCALED",
            rotate_method="AXIS_ALIGNED",
            island_margin=0.002,
            area_weight=0.6,
            correct_aspect=True,
            scale_to_bounds=False,
        )

    bpy.ops.object.mode_set(mode=prev_mode)

    return prev_active_name


def _get_texture_size(mesh, override_size=0):
    """Determine texture size based on triangle count or user override."""
    if override_size > 0:
        return override_size
    tri_count = len(mesh.polygons)
    if tri_count < 5000:
        return 2048
    elif tri_count < 20000:
        return 4096
    else:
        return 8192


def _get_filament_colors_from_settings(context):
    """Read the init_filaments list from MMUPaintSettings.

    Returns list of (r, g, b) tuples in **sRGB**, matching the colour
    space of ``image.pixels`` for sRGB-tagged images (Blender's default
    for newly-created images).
    """
    settings = context.scene.mmu_paint
    colors = []
    for item in settings.init_filaments:
        colors.append(tuple(item.color[:3]))
    return colors


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _cleanup_per_mat_state(per_mat_state):
    """Remove all temporary bake nodes and restore original wiring for every material.

    Called after a successful bake (Step 8) or on error (Step 7) to
    ensure each material's node tree is returned to its pre-bake state.
    """
    for state in per_mat_state:
        if state is None:
            continue
        mat = state["mat"]
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Remove temp UV Map nodes
        for uv_node in state.get("temp_uv_nodes", []):
            try:
                nodes.remove(uv_node)
            except Exception:
                pass

        # Restore original Material Output → Surface wiring
        if state.get("emit_node"):
            if state.get("original_surface_socket"):
                output_node = None
                for n in nodes:
                    if n.type == "OUTPUT_MATERIAL" and n.is_active_output:
                        output_node = n
                        break
                if output_node:
                    links.new(
                        state["original_surface_socket"],
                        output_node.inputs["Surface"],
                    )
            try:
                nodes.remove(state["emit_node"])
            except Exception:
                pass

        # Remove temp RGB node (if created for solid-color materials)
        if state.get("rgb_node"):
            try:
                nodes.remove(state["rgb_node"])
            except Exception:
                pass

        # Remove the bake target Image Texture node
        if state.get("bake_node"):
            try:
                nodes.remove(state["bake_node"])
            except Exception:
                pass


# ---------------------------------------------------------------------------
#  Operators
# ---------------------------------------------------------------------------

class MMU_OT_bake_to_mmu(bpy.types.Operator):
    """Bake the active material to a quantized MMU paint texture for 3MF export"""

    bl_idname = "mmu.bake_to_mmu"
    bl_label = "Bake to MMU Paint"
    bl_description = (
        "Bake the current material output to a texture, then quantize all pixels "
        "to the nearest filament color. The result is a discrete-color paint "
        "texture ready for multi-material 3MF export"
    )
    bl_options = {"REGISTER", "UNDO"}

    texture_size: bpy.props.EnumProperty(
        name="Texture Size",
        description="Resolution of the baked texture",
        items=[
            ("0", "Auto", "Automatic based on triangle count (2K/4K/8K)"),
            ("1024", "1024", "1024×1024 (fast bake, lower detail)"),
            ("2048", "2048", "2048×2048 (good for simple models)"),
            ("4096", "4096", "4096×4096 (recommended for most models)"),
            ("8192", "8192", "8192×8192 (high detail, slower bake)"),
        ],
        default="0",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            return False
        # Must have at least one material to bake from
        if not obj.data.materials or not obj.data.materials[0]:
            return False
        # Must NOT already be an MMU paint texture (use quantize for that)
        if obj.data.get("3mf_is_paint_texture"):
            return False
        # Must have filaments defined
        settings = context.scene.mmu_paint
        return len(settings.init_filaments) >= 2

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mmu_paint
        obj = context.active_object

        layout.label(text="Bake material to MMU paint texture", icon="BRUSH_DATA")
        layout.separator()

        # Detect vertex-color fast path to show a streamlined panel
        is_vc = obj and _detect_vertex_color_source(obj) is not None

        layout.prop(self, "texture_size")

        if is_vc:
            # Vertex-colour fast path — UV method still applies,
            # but quantization is always per-pixel (instant).
            layout.prop(settings, "uv_method")
            if settings.uv_method == "LIGHTMAP":
                layout.prop(settings, "lightmap_divisions")

            layout.separator()
            info = layout.box()
            info.label(text="Vertex Color Detected", icon="CHECKMARK")
            col = info.column(align=True)
            col.scale_y = 0.7
            col.label(text="Fast path: per-face colour assignment,")
            col.label(text="no Cycles bake needed.")
        else:
            layout.prop(settings, "uv_method")
            if settings.uv_method == "LIGHTMAP":
                layout.prop(settings, "lightmap_divisions")

            # Quantization method (only relevant for Cycles bake)
            layout.separator()
            quant_box = layout.box()
            quant_box.label(text="Quantization Method:", icon="COLOR")
            quant_box.prop(settings, "quantize_method", text="")
            if settings.quantize_method == "REGION":
                quant_box.prop(settings, "region_similarity", slider=True)
                quant_box.prop(settings, "min_region_size")
                info = quant_box.column(align=True)
                info.scale_y = 0.7
                info.label(text="Region-based: segments texture into areas,")
                info.label(text="handles shadows/highlights correctly.")

        # Show the filament palette being used
        layout.separator()
        box = layout.box()
        box.label(text=f"Quantizing to {len(settings.init_filaments)} filament colors:")
        flow = box.grid_flow(row_major=True, columns=4, align=True)
        for i, item in enumerate(settings.init_filaments):
            row = flow.row(align=True)
            swatch = row.row()
            swatch.ui_units_x = 1.2
            swatch.enabled = False
            swatch.prop(item, "color", text="")
            row.label(text=f"{i + 1}")

        layout.separator()
        layout.label(text="This will replace the current material setup.", icon="INFO")

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        settings = context.scene.mmu_paint

        filament_colors = _get_filament_colors_from_settings(context)
        if len(filament_colors) < 2:
            self.report({"ERROR"}, "At least 2 filaments required")
            return {"CANCELLED"}

        # --- Vertex-color fast path (before UV unwrap) ---
        # Detect FIRST — Limited Dissolve in _ensure_uv_unwrap would
        # destroy vertex color data and is extremely slow on high-poly
        # meshes.  The fast path skips dissolve entirely.
        vc_attr_name = _detect_vertex_color_source(obj)
        if vc_attr_name:
            debug(f"Bake to MMU: vertex color fast path, attr='{vc_attr_name}'")
            self.report({"INFO"}, "Converting vertex colors (fast path)...")

            wm = context.window_manager
            wm.progress_begin(0, 100)

            # A — Per-face filament assignment (vectorised, instant)
            self.report({"INFO"}, "Assigning filament colours per face...")
            face_filaments = _compute_face_filaments(
                mesh, vc_attr_name, filament_colors,
            )
            if face_filaments is None:
                self.report({"ERROR"}, "Failed to read vertex color data")
                wm.progress_end()
                return {"CANCELLED"}
            wm.progress_update(10)

            # B — Lightweight UV unwrap (no Limited Dissolve)
            self.report({"INFO"}, "Creating UV map...")
            mmu_layer = mesh.uv_layers.get("MMU_Paint")
            if mmu_layer is None:
                mmu_layer = mesh.uv_layers.new(name="MMU_Paint")
            mesh.uv_layers.active = mmu_layer
            mmu_layer.active_render = True
            context.view_layer.objects.active = obj

            prev_mode = obj.mode
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            uv_method = settings.uv_method
            if uv_method == "LIGHTMAP":
                bpy.ops.uv.lightmap_pack(
                    PREF_CONTEXT="ALL_FACES",
                    PREF_PACK_IN_ONE=True,
                    PREF_NEW_UVLAYER=False,
                    PREF_BOX_DIV=settings.lightmap_divisions,
                    PREF_MARGIN_DIV=0.05,
                )
            else:
                bpy.ops.uv.smart_project(
                    angle_limit=1.15192,
                    margin_method="SCALED",
                    rotate_method="AXIS_ALIGNED",
                    island_margin=0.002,
                    area_weight=0.6,
                    correct_aspect=True,
                    scale_to_bounds=False,
                )
            bpy.ops.object.mode_set(mode=prev_mode)
            wm.progress_update(40)

            # C — Texture size
            tex_size = _get_texture_size(mesh, int(self.texture_size))
            debug(f"Bake to MMU (VC): texture size {tex_size}x{tex_size}")

            # D — Fill texture with flat per-face filament colours
            self.report({"INFO"}, "Filling texture...")
            palette = np.array(filament_colors, dtype=np.float32)
            face_rgb = palette[face_filaments]
            pixels = _rasterize_face_colors(
                mesh, "MMU_Paint", face_rgb, tex_size, tex_size,
            )
            if pixels is None:
                self.report({"ERROR"}, "Failed to rasterize face colours")
                wm.progress_end()
                return {"CANCELLED"}
            wm.progress_update(60)

            # E — Per-pixel quantize (always — region method is overkill
            #     here since the texture already contains near-exact
            #     filament colours from the face assignment)
            self.report({"INFO"}, "Snapping pixels to filament colours...")
            changed = _quantize_pixels(pixels, filament_colors)
            wm.progress_update(85)
            debug(f"Bake to MMU (VC): pixel quantize changed {changed} px")

            # F — Create image + finalize
            image_name = f"{mesh.name}_MMU_Paint"
            existing = bpy.data.images.get(image_name)
            if existing:
                bpy.data.images.remove(existing)
            image = bpy.data.images.new(
                image_name, width=tex_size, height=tex_size, alpha=True,
            )
            image.pixels.foreach_set(pixels.ravel())
            image.update()
            image.pack()
            wm.progress_update(90)

            return self._finalize_mmu_paint(
                context, obj, mesh, settings, image, image_name,
                filament_colors, tex_size, changed,
            )

        # --- Step 1: Ensure UV unwrap ---
        self.report({"INFO"}, "Ensuring UV map...")
        prev_uv_name = _ensure_uv_unwrap(obj, context)

        # --- Step 2: Determine texture size ---
        tex_size = _get_texture_size(mesh, int(self.texture_size))
        debug(f"Bake to MMU: texture size {tex_size}x{tex_size}")

        # --- Step 3: Save reference to original material ---
        original_materials = [slot.material for slot in obj.material_slots]
        if not original_materials or not original_materials[0]:
            self.report({"ERROR"}, "No material found to bake")
            return {"CANCELLED"}

        # --- Step 4: Create the bake target image ---
        image_name = f"{mesh.name}_MMU_Paint"
        # Remove existing image with same name if present
        existing = bpy.data.images.get(image_name)
        if existing:
            bpy.data.images.remove(existing)

        image = bpy.data.images.new(
            image_name, width=tex_size, height=tex_size, alpha=True
        )

        # --- Step 5: Prepare ALL materials for baking ---
        # Blender's bake uses per-face material assignments. Each material
        # needs a bake-target Image Texture node (set as the active node)
        # so pixels from *all* material slots are captured.
        #
        # We also rewire each material's Base Color → Emission → Material
        # Output for an EMIT bake, which is much faster than DIFFUSE because
        # it skips all lighting calculations.
        #
        # Track per-material state so we can clean up afterwards.
        _per_mat_state = []  # list of dicts, one per material slot
        bake_type = "EMIT"
        bake_pass_filter = set()
        all_emit_ok = True  # Will any material need DIFFUSE fallback?

        for slot_idx, mat in enumerate(original_materials):
            if mat is None:
                _per_mat_state.append(None)
                continue

            if not mat.use_nodes:
                mat.use_nodes = True

            state = {
                "mat": mat,
                "bake_node": None,
                "emit_node": None,
                "rgb_node": None,
                "original_surface_socket": None,
                "temp_uv_nodes": [],
            }

            nodes = mat.node_tree.nodes
            links = mat.node_tree.links

            # Add bake target image node (must be active for bake to write to it)
            bake_node = nodes.new("ShaderNodeTexImage")
            bake_node.image = image
            bake_node.name = "_MMU_Bake_Target"
            bake_node.label = "MMU Bake Target"
            bake_node.location = (-600, -300)
            nodes.active = bake_node
            state["bake_node"] = bake_node

            # Find Principled BSDF and Material Output
            principled = None
            output_node = None
            for node in nodes:
                if node.type == "BSDF_PRINCIPLED" and principled is None:
                    principled = node
                if node.type == "OUTPUT_MATERIAL" and node.is_active_output:
                    output_node = node

            if principled and output_node:
                # Remember what was wired into Material Output → Surface
                for link in links:
                    if (
                        link.to_node == output_node
                        and link.to_socket.name == "Surface"
                    ):
                        state["original_surface_socket"] = link.from_socket
                        break

                # Find what drives Base Color
                base_color_source = None
                for link in links:
                    if link.to_node == principled and link.to_socket.name == "Base Color":
                        base_color_source = link.from_socket
                        break

                # Create Emission node
                emit_node = nodes.new("ShaderNodeEmission")
                emit_node.name = "_MMU_Temp_Emission"
                emit_node.location = (
                    principled.location.x,
                    principled.location.y - 200,
                )
                state["emit_node"] = emit_node

                if base_color_source:
                    # Base Color has an input link (texture, noise, etc.)
                    links.new(base_color_source, emit_node.inputs["Color"])
                else:
                    # Solid color only — extract the default value and feed it
                    # through an RGB node so Emission still captures it.
                    default_color = principled.inputs["Base Color"].default_value
                    rgb_node = nodes.new("ShaderNodeRGB")
                    rgb_node.name = "_MMU_Temp_RGB"
                    rgb_node.outputs[0].default_value = (
                        default_color[0], default_color[1],
                        default_color[2], 1.0,
                    )
                    rgb_node.location = (
                        emit_node.location.x - 200,
                        emit_node.location.y,
                    )
                    state["rgb_node"] = rgb_node
                    links.new(rgb_node.outputs[0], emit_node.inputs["Color"])

                links.new(
                    emit_node.outputs["Emission"],
                    output_node.inputs["Surface"],
                )
                debug(f"Bake to MMU: slot {slot_idx} '{mat.name}' wired for EMIT bake")
            else:
                # No Principled BSDF — fall back to DIFFUSE for this material.
                all_emit_ok = False
                debug(
                    f"Bake to MMU: slot {slot_idx} '{mat.name}' has no Principled BSDF, "
                    "falling back to DIFFUSE bake"
                )

            # Pin Image Texture UVs to the original UV layer so textures
            # sample with correct coordinates after the MMU_Paint UV was
            # set as active.
            if prev_uv_name and mesh.uv_layers.get(prev_uv_name):
                for node in list(nodes):
                    if node.type != "TEX_IMAGE" or node == bake_node:
                        continue
                    uv_input = node.inputs.get("Vector")
                    if uv_input and not uv_input.is_linked:
                        uv_node = nodes.new("ShaderNodeUVMap")
                        uv_node.uv_map = prev_uv_name
                        uv_node.name = "_MMU_Temp_UV"
                        uv_node.location = (
                            node.location.x - 200,
                            node.location.y - 100,
                        )
                        links.new(uv_node.outputs["UV"], uv_input)
                        state["temp_uv_nodes"].append(uv_node)
                        debug(f"Bake to MMU: pinned '{node.name}' UV to '{prev_uv_name}'")

            _per_mat_state.append(state)

        # If any material couldn't use EMIT, fall back to DIFFUSE COLOR
        if not all_emit_ok:
            bake_type = "DIFFUSE"
            bake_pass_filter = {"COLOR"}
            # Undo Emission rewiring for materials that had it
            for state in _per_mat_state:
                if state is None:
                    continue
                mat = state["mat"]
                nodes = mat.node_tree.nodes
                links = mat.node_tree.links
                if state["emit_node"]:
                    if state["original_surface_socket"]:
                        output_node = [
                            n for n in nodes
                            if n.type == "OUTPUT_MATERIAL" and n.is_active_output
                        ][0]
                        links.new(
                            state["original_surface_socket"],
                            output_node.inputs["Surface"],
                        )
                    nodes.remove(state["emit_node"])
                    state["emit_node"] = None
                if state["rgb_node"]:
                    nodes.remove(state["rgb_node"])
                    state["rgb_node"] = None
        else:
            debug(f"Bake to MMU: all {len(original_materials)} materials use EMIT bake")

        # --- Step 6: Switch to Cycles for baking ---
        original_engine = context.scene.render.engine
        context.scene.render.engine = "CYCLES"

        # --- Step 6b: Optimize Cycles settings for fast procedural bake ---
        cycles = context.scene.cycles
        original_samples = cycles.samples
        original_device = cycles.device

        # 1 sample is sufficient — we're baking flat procedural color, not lighting
        cycles.samples = 1

        # Try GPU compute if available (much faster for large textures)
        try:
            cycles_prefs = context.preferences.addons.get("cycles")
            if cycles_prefs and cycles_prefs.preferences:
                cprefs = cycles_prefs.preferences
                if hasattr(cprefs, "get_devices"):
                    cprefs.get_devices()
                # Check if any GPU device is enabled
                has_gpu = False
                if hasattr(cprefs, "devices"):
                    for dev in cprefs.devices:
                        if dev.type != "CPU" and dev.use:
                            has_gpu = True
                            break
                if has_gpu:
                    cycles.device = "GPU"
                    debug("Bake to MMU: using GPU compute")
        except Exception:
            pass  # Fall back to whatever was configured

        # Ensure we're in Object mode for baking
        prev_mode = obj.mode
        if prev_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        # Ensure only this object is selected and active
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        # --- Step 7: Bake ---
        self.report({"INFO"}, "Baking texture...")
        try:
            bake_kwargs = {
                "type": bake_type,
                "use_clear": True,
                "margin": 2,
                "margin_type": "EXTEND",
            }
            if bake_pass_filter:
                bake_kwargs["pass_filter"] = bake_pass_filter
            bpy.ops.object.bake(**bake_kwargs)
        except RuntimeError as e:
            error(f"Bake failed: {e}")
            self.report({"ERROR"}, f"Bake failed: {e}")
            # Clean up temp nodes and settings from ALL materials
            _cleanup_per_mat_state(_per_mat_state)
            cycles.samples = original_samples
            cycles.device = original_device
            context.scene.render.engine = original_engine
            # Restore original active UV layer on failure
            if prev_uv_name and mesh.uv_layers.get(prev_uv_name):
                mesh.uv_layers.active = mesh.uv_layers[prev_uv_name]
            if prev_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=prev_mode)
            return {"CANCELLED"}

        # --- Step 8: Restore render engine and Cycles settings ---
        # Tear down all temporary nodes from ALL materials
        _cleanup_per_mat_state(_per_mat_state)

        cycles.samples = original_samples
        cycles.device = original_device
        context.scene.render.engine = original_engine

        # --- Step 9: Quantize the baked texture ---
        self.report({"INFO"}, "Quantizing to filament colors...")
        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.progress_update(5)

        pixel_count = tex_size * tex_size * 4
        pixels_flat = np.empty(pixel_count, dtype=np.float32)
        image.pixels.foreach_get(pixels_flat)
        pixels = pixels_flat.reshape(tex_size, tex_size, 4)

        wm.progress_update(10)

        # Use the selected quantization method
        if settings.quantize_method == "REGION":
            # Region-based quantization: segment → merge → characterize → quantize
            self.report({"INFO"}, "Segmenting into color regions...")
            # Map quantization sub-steps to progress 10-85

            def _bake_progress(pct):
                wm.progress_update(10 + int(pct * 0.75))
            changed = _quantize_by_regions(
                pixels, filament_colors,
                similarity_threshold=settings.region_similarity,
                min_region_size=settings.min_region_size,
                progress_callback=_bake_progress,
                mesh_data=mesh,
                uv_layer_name="MMU_Paint",
            )
        else:
            # Legacy per-pixel quantization
            changed = _quantize_pixels(pixels, filament_colors)

        debug(f"Bake to MMU: quantized {changed} pixels")
        wm.progress_update(85)

        # Apply spatial smoothing (disabled by default, experimental)
        if settings.use_spatial_smoothing:
            self.report({"INFO"}, "Applying UV spatial smoothing (experimental)...")
            flipped = _apply_majority_filter(
                pixels, filament_colors,
                kernel_size=settings.smoothing_kernel_size,
                passes=settings.smoothing_passes,
            )
            debug(f"Bake to MMU: spatial smoothing flipped {flipped} pixels")

        wm.progress_update(90)
        image.pixels.foreach_set(pixels.ravel())
        image.update()
        image.pack()
        wm.progress_update(100)
        wm.progress_end()

        # Finalize: replace material, set properties, switch to paint
        return self._finalize_mmu_paint(
            context, obj, mesh, settings, image, image_name,
            filament_colors, tex_size, changed,
        )

    def _finalize_mmu_paint(
        self, context, obj, mesh, settings, image, image_name,
        filament_colors, tex_size, changed,
    ):
        """Shared finalization for both the Cycles bake and vertex-color fast paths."""
        # --- Replace material with MMU paint material ---
        mmu_mat = bpy.data.materials.new(name=image_name)
        mmu_mat.use_nodes = True
        mmu_nodes = mmu_mat.node_tree.nodes
        mmu_links = mmu_mat.node_tree.links
        mmu_nodes.clear()

        tex_node = mmu_nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        tex_node.location = (-300, 0)

        bsdf = mmu_nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (100, 0)

        output = mmu_nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)

        mmu_links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        mmu_links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        # Replace materials on the mesh
        mesh.materials.clear()
        mesh.materials.append(mmu_mat)
        num_faces = len(mesh.polygons)
        if num_faces > 0:
            material_indices = [0] * num_faces
            mesh.polygons.foreach_set("material_index", material_indices)

        # --- Set up 3mf custom properties ---
        colors_dict = {}
        for i, color in enumerate(filament_colors):
            colors_dict[i] = _hex_from_rgb(*color)

        mesh["3mf_is_paint_texture"] = True
        mesh["3mf_paint_default_extruder"] = 1  # 1-based
        mesh["3mf_paint_extruder_colors"] = str(colors_dict)

        # --- Sync the paint panel ---
        settings.loaded_mesh_name = ""  # Force reload
        from .helpers import _sync_filaments_from_mesh
        _sync_filaments_from_mesh(context)

        # Set active node so texture paint can find the image
        mmu_mat.node_tree.nodes.active = tex_node

        # --- Switch to Texture Paint mode ---
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
        from .helpers import _configure_paint_brush
        _configure_paint_brush(context)

        ts = context.tool_settings
        if hasattr(ts.image_paint, "canvas"):
            ts.image_paint.canvas = image

        # Set brush to first filament color
        if len(settings.filaments) > 0:
            from .helpers import _set_brush_color
            _set_brush_color(context, settings.filaments[0].color[:])

        self.report(
            {"INFO"},
            f"Baked and quantized to {len(filament_colors)} filament colors "
            f"at {tex_size}×{tex_size} ({changed} pixels adjusted)",
        )
        return {"FINISHED"}


class MMU_OT_quantize_texture(bpy.types.Operator):
    """Quantize an existing paint texture to snap all pixels to the nearest filament color"""

    bl_idname = "mmu.quantize_texture"
    bl_label = "Quantize to Filaments"
    bl_description = (
        "Snap every pixel in the current MMU paint texture to the nearest "
        "filament color. Useful for cleaning up anti-aliased edges or "
        "slightly off-color painted regions"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            return False
        # Must already have MMU paint data
        return bool(obj.data.get("3mf_is_paint_texture"))

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data

        # Get the paint image
        from .helpers import _get_paint_image
        image = _get_paint_image(obj)
        if image is None:
            self.report({"ERROR"}, "No paint texture found")
            return {"CANCELLED"}

        # Get filament colors from mesh properties
        colors_str = mesh.get("3mf_paint_extruder_colors", "")
        if not colors_str:
            self.report({"ERROR"}, "No filament colors stored on mesh")
            return {"CANCELLED"}

        try:
            colors_dict = ast.literal_eval(colors_str)
        except (ValueError, SyntaxError):
            self.report({"ERROR"}, "Failed to parse filament colors")
            return {"CANCELLED"}

        filament_colors = []
        for idx in sorted(colors_dict.keys()):
            rgb = _rgb_from_hex(colors_dict[idx])
            filament_colors.append(rgb)

        if len(filament_colors) < 2:
            self.report({"ERROR"}, "Need at least 2 filament colors")
            return {"CANCELLED"}

        # Quantize
        w, h = image.size
        pixel_count = w * h * 4
        pixels_flat = np.empty(pixel_count, dtype=np.float32)
        image.pixels.foreach_get(pixels_flat)
        pixels = pixels_flat.reshape(h, w, 4)

        settings = context.scene.mmu_paint

        # Use the selected quantization method
        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.progress_update(5)

        if settings.quantize_method == "REGION":
            def _quant_progress(pct):
                wm.progress_update(5 + int(pct * 0.85))
            uv_name = mesh.uv_layers.active.name if mesh.uv_layers.active else None
            changed = _quantize_by_regions(
                pixels, filament_colors,
                similarity_threshold=settings.region_similarity,
                min_region_size=settings.min_region_size,
                progress_callback=_quant_progress,
                mesh_data=mesh,
                uv_layer_name=uv_name,
            )
        else:
            changed = _quantize_pixels(pixels, filament_colors)

        wm.progress_update(90)

        # Apply spatial smoothing (legacy, deprecated)
        flipped = 0
        if settings.use_spatial_smoothing:
            flipped = _apply_majority_filter(
                pixels, filament_colors,
                kernel_size=settings.smoothing_kernel_size,
                passes=settings.smoothing_passes,
            )

        image.pixels.foreach_set(pixels.ravel())
        image.update()
        wm.progress_update(100)
        wm.progress_end()

        self.report(
            {"INFO"},
            f"Quantized {changed} pixels ({flipped} spatially smoothed) to {len(filament_colors)} colors",
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
#  Shared panel draw function
# ---------------------------------------------------------------------------

def _draw_bake_panel(layout, context):
    """
    Shared draw logic for the Bake to MMU panel.

    Used by the Shader Editor panel and (in the future) the Geometry Nodes panel.
    Can also be called from the 3D Viewport paint panel's uninitialized state.

    :param layout: The Blender UI layout to draw into.
    :param context: The current Blender context.
    """
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        layout.label(text="Select a mesh object", icon="INFO")
        return

    settings = context.scene.mmu_paint
    mesh = obj.data
    has_paint = bool(mesh.get("3mf_is_paint_texture"))

    if has_paint:
        # Already has MMU paint — show quantize option
        box = layout.box()
        box.label(text="MMU Paint Active", icon="CHECKMARK")
        box.label(text="Texture paint is set up on this object.")
        box.separator()
        box.operator("mmu.quantize_texture", icon="BRUSH_DATA")
        box.separator()
        info = box.column(align=True)
        info.scale_y = 0.7
        info.label(text="Tip: Use Quantize after painting to clean up", icon="INFO")
        info.label(text="anti-aliased edges or off-color pixels.")
    else:
        # Show bake setup
        box = layout.box()
        box.label(text="Bake Material to MMU", icon="RENDER_STILL")

        # Material status
        has_material = bool(obj.data.materials and obj.data.materials[0])
        if has_material:
            mat = obj.data.materials[0]
            mat_row = box.row()
            mat_row.label(text=f"Material: {mat.name}", icon="MATERIAL")
        else:
            box.label(text="No material assigned", icon="ERROR")
            return

        box.separator()

        # Filament palette setup — reuse the init_filaments from MMUPaintSettings
        if len(settings.init_filaments) == 0:
            box.operator(
                "mmu.reset_init_filaments",
                text="Create Default Palette",
                icon="ADD",
            )
        else:
            box.label(text="Filament Colors:")
            row = box.row()
            row.template_list(
                "MMU_UL_init_filaments",
                "bake_filaments",
                settings,
                "init_filaments",
                settings,
                "active_init_filament_index",
                rows=3,
                maxrows=8,
            )

            # Add/Remove buttons
            col = row.column(align=True)
            col.operator("mmu.add_init_filament", icon="ADD", text="")
            col.operator("mmu.remove_init_filament", icon="REMOVE", text="")

            box.separator()

            # Bake button (settings shown in the operator's dialog)
            bake_row = box.row(align=True)
            bake_row.scale_y = 1.4
            bake_row.operator("mmu.bake_to_mmu", icon="RENDER_STILL")

            # Detect + Reset row
            util_row = box.row(align=True)
            util_row.operator(
                "mmu.detect_material_colors", icon="MATERIAL",
            )
            util_row.operator("mmu.reset_init_filaments", icon="FILE_REFRESH")

        box.separator()
        info = box.column(align=True)
        info.scale_y = 0.7
        info.label(text="Bakes the material output to a texture,", icon="INFO")
        info.label(text="then snaps every pixel to the nearest")
        info.label(text="filament color for clean 3MF export.")


# ---------------------------------------------------------------------------
#  Panels
# ---------------------------------------------------------------------------

class NODE_PT_mmu_bake(bpy.types.Panel):
    """Bake to MMU Paint — Shader Editor sidebar panel."""

    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "Bake to MMU"

    @classmethod
    def poll(cls, context):
        # Only show in Shader Editor (not Geometry Nodes or Compositor)
        if not hasattr(context, "space_data") or context.space_data is None:
            return False
        space = context.space_data
        if space.type != "NODE_EDITOR":
            return False
        return space.tree_type == "ShaderNodeTree"

    def draw(self, context):
        _draw_bake_panel(self.layout, context)


# Future: Geometry Nodes panel
# class NODE_PT_mmu_bake_gn(bpy.types.Panel):
#     """Bake to MMU Paint — Geometry Nodes sidebar panel."""
#
#     bl_space_type = "NODE_EDITOR"
#     bl_region_type = "UI"
#     bl_category = "3MF"
#     bl_label = "Bake to MMU"
#
#     @classmethod
#     def poll(cls, context):
#         if not hasattr(context, "space_data") or context.space_data is None:
#             return False
#         space = context.space_data
#         if space.type != "NODE_EDITOR":
#             return False
#         return space.tree_type == "GeometryNodeTree"
#
#     def draw(self, context):
#         # GN-specific extraction would go here:
#         # - Detect color attributes from Store Named Attribute nodes
#         # - Detect material assignments
#         # - Offer appropriate bake/extract path
#         _draw_bake_panel(self.layout, context)


# ===================================================================
#  Registration
# ===================================================================

bake_classes = (
    MMU_OT_bake_to_mmu,
    MMU_OT_quantize_texture,
    NODE_PT_mmu_bake,
)


def register():
    for cls in bake_classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(bake_classes):
        bpy.utils.unregister_class(cls)
