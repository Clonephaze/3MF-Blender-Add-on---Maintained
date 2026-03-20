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
Quantization pipeline — pixel-level and region-based quantization helpers.

Contains all the pure / numpy functions for:
- Color-space conversion (``_rgb_to_hsv``)
- Perceptual distance metrics (``_hue_aware_distance``, ``_compute_neighborhood_brightness``)
- Per-pixel quantization (``_quantize_pixels``, ``_compute_palette_index_map``)
- UV island mapping (``_rasterize_island_map``, ``_uv_edge_shared``)
- Region-based segmentation (``_flood_fill_segmentation``, ``_compute_gradient_magnitude``)
- Region merging and cleanup (``_merge_small_regions``)
- Region characterisation (``_compute_region_representative_colors``, ``_match_colors_to_palette``)
- Connected-component palette regions (``_build_palette_regions``, ``_rebuild_region_palette``)
- Top-level region quantization (``_quantize_by_regions``)
- Majority filter (``_apply_majority_filter``)
"""

import bmesh
import numpy as np

from ..common.logging import debug


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """Vectorized RGB → HSV conversion.

    Operates on an (N, 3) array and returns an (N, 3) HSV array with
    H in [0, 1], S in [0, 1], V in [0, 1].

    Uses a branch-free formulation suitable for large batches.
    """
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    diff = maxc - minc

    v = maxc
    s = np.where(maxc > 0, diff / maxc, 0.0)

    # Hue computation
    h = np.zeros_like(v)
    mask_r = (maxc == r) & (diff > 0)
    mask_g = (maxc == g) & (diff > 0) & ~mask_r
    mask_b = (diff > 0) & ~mask_r & ~mask_g

    h[mask_r] = ((g[mask_r] - b[mask_r]) / diff[mask_r]) % 6.0
    h[mask_g] = ((b[mask_g] - r[mask_g]) / diff[mask_g]) + 2.0
    h[mask_b] = ((r[mask_b] - g[mask_b]) / diff[mask_b]) + 4.0
    h = h / 6.0
    h = h % 1.0

    return np.stack([h, s, v], axis=1).astype(np.float32)


def _compute_neighborhood_brightness(rgb: np.ndarray, kernel_size: int = 3) -> np.ndarray:
    """Compute average brightness in a local neighborhood.

    Returns an (H, W) float32 array of local mean brightness (max of RGB
    channels), used to bias the distance metric toward brightness matching
    in already-dark areas.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return np.zeros(rgb.shape[:2], dtype=np.float32)

    val = np.max(rgb[:, :, :3], axis=2)

    half = kernel_size // 2
    padded = np.pad(val, half, mode="edge")
    windows = sliding_window_view(padded, (kernel_size, kernel_size))

    return windows.mean(axis=(2, 3)).astype(np.float32)


def _hue_aware_distance(
    pixel_hsv: np.ndarray,
    palette_hsv: np.ndarray,
    pixel_rgb: np.ndarray,
    palette_rgb: np.ndarray,
) -> np.ndarray:
    """Compute a perceptual HSV-weighted distance between pixels and palette entries.

    The metric favours hue and saturation matching over brightness so that
    "red in shadow" matches *red*, not *black*.  For achromatic pixels
    (low saturation **or** low value) it gracefully falls back to a pure
    brightness comparison.

    :param pixel_hsv: (..., 3) HSV array of pixel colors.
    :param palette_hsv: (..., 3) HSV array of palette colors.
    :param pixel_rgb: (..., 3) RGB array (unused — kept for API symmetry).
    :param palette_rgb: (..., 3) RGB array (unused — kept for API symmetry).
    :return: (...) float32 distance array (same leading shape).
    """
    W_H = 6.0
    W_S = 4.0
    W_V = 2.0

    ph, ps, pv = pixel_hsv[..., 0], pixel_hsv[..., 1], pixel_hsv[..., 2]
    qh, qs, qv = palette_hsv[..., 0], palette_hsv[..., 1], palette_hsv[..., 2]

    dh = np.abs(ph - qh)
    dh = np.minimum(dh, 1.0 - dh)  # cyclic wrap
    ds = ps - qs
    dv = pv - qv

    chromatic_dist = W_H * dh ** 2 + W_S * ds ** 2 + W_V * dv ** 2

    # Achromatic handling: when the pixel is grey (low sat / low val),
    # match by brightness and penalise saturated palette entries.
    val_dist = dv ** 2
    palette_sat = qs
    achromatic_dist = val_dist + 2.0 * palette_sat ** 2

    # Smooth blend controlled by pixel saturation and value
    alpha = np.clip(ps / 0.08, 0.0, 1.0) * np.clip(pv / 0.12, 0.0, 1.0)

    return (alpha * chromatic_dist + (1.0 - alpha) * achromatic_dist).astype(np.float32)


def _quantize_pixels(
    pixels: np.ndarray,
    filament_colors: list,
) -> int:
    """Snap every opaque pixel to the nearest filament colour (in-place).

    Uses the HSV-weighted perceptual distance from ``_hue_aware_distance``
    to decide which filament colour is closest.

    Operates on the pixels array *in-place* and returns the number of
    pixels that changed colour.  Transparent pixels (alpha < 0.01) are
    left untouched.

    :param pixels: (H, W, 4) float32 pixel array, modified in-place.
    :param filament_colors: List of (r, g, b) tuples in [0, 1].
    :return: Number of pixels changed.
    """
    height, width = pixels.shape[:2]
    palette = np.array(filament_colors, dtype=np.float32)  # (N, 3)
    palette_hsv = _rgb_to_hsv(palette)  # (N, 3)

    alpha = pixels[:, :, 3] if pixels.shape[2] > 3 else None
    opaque = (alpha >= 0.01) if alpha is not None else np.ones(
        (height, width), dtype=bool
    )

    flat_rgb = pixels[:, :, :3].reshape(-1, 3)
    flat_opaque = opaque.flatten()

    opaque_idx = np.where(flat_opaque)[0]
    if len(opaque_idx) == 0:
        return 0

    opaque_rgb = flat_rgb[opaque_idx]  # (M, 3)
    opaque_hsv = _rgb_to_hsv(opaque_rgb)

    # Compute distance to each palette color  (M, N)
    p_hsv = opaque_hsv[:, np.newaxis, :]  # (M, 1, 3)
    q_hsv = palette_hsv[np.newaxis, :, :]  # (1, N, 3)
    p_rgb = opaque_rgb[:, np.newaxis, :]
    q_rgb = palette[np.newaxis, :, :]

    dist = _hue_aware_distance(p_hsv, q_hsv, p_rgb, q_rgb)  # (M, N)
    best = np.argmin(dist, axis=1)  # (M,)
    new_colors = palette[best]

    diff_mask = np.any(np.abs(opaque_rgb - new_colors) > 0.002, axis=1)
    changed = int(np.sum(diff_mask))

    flat_rgb[opaque_idx] = new_colors
    pixels[:, :, :3] = flat_rgb.reshape(height, width, 3)
    return changed


def _compute_palette_index_map(
    pixels: np.ndarray,
    filament_colors: list,
    chunk_size: int = 200_000,
) -> np.ndarray:
    """Assign every opaque pixel to its nearest palette index (chunked).

    Returns a (H, W) int32 array with the palette index for each pixel
    (0 for transparent pixels that have no assignment).

    Processing is done in chunks to limit peak memory when textures are
    large (8192×8192 = 67M pixels × N palette entries).
    """
    height, width = pixels.shape[:2]
    palette = np.array(filament_colors, dtype=np.float32)
    palette_hsv = _rgb_to_hsv(palette)

    alpha = pixels[:, :, 3] if pixels.shape[2] > 3 else None
    opaque = ((alpha >= 0.01) if alpha is not None
              else np.ones((height, width), dtype=bool))

    flat_rgb = pixels[:, :, :3].reshape(-1, 3)
    flat_opaque = opaque.ravel()
    opaque_idx = np.where(flat_opaque)[0]

    index_map = np.zeros(height * width, dtype=np.int32)
    if len(opaque_idx) == 0:
        return index_map.reshape(height, width)

    opaque_rgb = flat_rgb[opaque_idx]
    opaque_hsv = _rgb_to_hsv(opaque_rgb)

    n_opaque = len(opaque_idx)
    best = np.empty(n_opaque, dtype=np.int32)

    for start in range(0, n_opaque, chunk_size):
        end = min(start + chunk_size, n_opaque)
        p_hsv = opaque_hsv[start:end, np.newaxis, :]
        q_hsv = palette_hsv[np.newaxis, :, :]
        p_rgb = opaque_rgb[start:end, np.newaxis, :]
        q_rgb = palette[np.newaxis, :, :]
        dist = _hue_aware_distance(p_hsv, q_hsv, p_rgb, q_rgb)
        best[start:end] = np.argmin(dist, axis=1)

    index_map[opaque_idx] = best
    return index_map.reshape(height, width)


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
        ADJACENT pixels as "similar" and part of the same region.
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

    Uses fully vectorized border detection and neighbor counting via
    numpy operations — no Python loops over individual pixels or border
    pairs.  Only the merge-decision loop iterates in Python over small-
    region IDs (typically hundreds, not millions).

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

        # --- Vectorized best-neighbor computation -------------------------
        # For each small region, find its neighbor with the longest border.
        # We process both directions (a->b where b is small, and b->a where
        # a is small) in bulk, then take the maximum per region.
        best_neighbor = np.zeros(max_id + 1, dtype=np.int32)
        best_border = np.zeros(max_id + 1, dtype=np.int64)

        # Direction 1: pair_a is small, neighbor is pair_b
        mask_a_small = small_mask[pair_a]
        if np.any(mask_a_small):
            ids_a = pair_a[mask_a_small]
            neighbors_a = pair_b[mask_a_small]
            counts_a = border_counts[mask_a_small].astype(np.int64)
            # For duplicate IDs keep only the one with max count
            order = np.argsort(-counts_a)  # descending by count
            ids_sorted = ids_a[order]
            _, first_idx = np.unique(ids_sorted, return_index=True)
            best_neighbor[ids_sorted[first_idx]] = neighbors_a[order][first_idx]
            best_border[ids_sorted[first_idx]] = counts_a[order][first_idx]

        # Direction 2: pair_b is small, neighbor is pair_a
        mask_b_small = small_mask[pair_b]
        if np.any(mask_b_small):
            ids_b = pair_b[mask_b_small]
            neighbors_b = pair_a[mask_b_small]
            counts_b = border_counts[mask_b_small].astype(np.int64)
            order = np.argsort(-counts_b)
            ids_sorted = ids_b[order]
            _, first_idx = np.unique(ids_sorted, return_index=True)
            # Only overwrite if this direction found a longer border
            better = counts_b[order][first_idx] > best_border[ids_sorted[first_idx]]
            update_ids = ids_sorted[first_idx][better]
            best_neighbor[update_ids] = neighbors_b[order][first_idx][better]
            best_border[update_ids] = counts_b[order][first_idx][better]

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
) -> np.ndarray:
    """Determine each region's palette index by majority vote.

    After ``_merge_small_regions``, a region may now contain pixels that
    were originally assigned to different palette indices.  This function
    counts palette index occurrences per region and picks the dominant one.

    :param region_map: (H, W) int32 region IDs (1-based).
    :param index_map: (H, W) int32 original per-pixel palette assignments.
    :param num_regions: Number of regions (max region ID).
    :return: int32 LUT of shape (num_regions + 1,) mapping region_id → palette_index.
             Index 0 (background) maps to 0.
    """
    mask = region_map > 0
    rids = region_map[mask].astype(np.int64)
    pidxs = index_map[mask].astype(np.int64)

    lut = np.zeros(num_regions + 1, dtype=np.int32)
    if len(pidxs) == 0:
        return lut

    max_palette = int(pidxs.max()) + 1

    # Encode (region_id, palette_idx) into a single int for bincount
    combined = rids * max_palette + pidxs
    counts = np.bincount(
        combined, minlength=(num_regions + 1) * max_palette,
    )
    counts = counts.reshape(num_regions + 1, max_palette)

    # Vectorized argmax for all regions at once
    lut[1:] = np.argmax(counts[1:], axis=1)
    return lut


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
    region_lut = _rebuild_region_palette(
        region_map, index_map, num_regions,
    )
    _progress(85)

    # Step 5: Apply palette colors via LUT  [85% -> 100%]
    # region_map → palette index → RGB, all in two vectorized lookups
    palette = np.array(filament_colors, dtype=np.float32)
    pal_idx_map = region_lut[region_map]  # (H, W) palette indices
    new_rgb = palette[pal_idx_map]        # (H, W, 3)

    opaque_3d = opaque[:, :, np.newaxis]
    old_rgb = pixels[:, :, :3]
    diff = np.any(np.abs(old_rgb - new_rgb) > 0.002, axis=2)
    changed = int(np.count_nonzero(diff & opaque))

    # Only write opaque pixels (leave transparent background alone)
    pixels[:, :, :3] = np.where(opaque_3d, new_rgb, old_rgb)
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
