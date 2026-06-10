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
Color detection helpers for the MMU Paint suite.

Extracts dominant colors from material node trees, image textures, and
vertex color attributes.  Used by ``MMU_OT_detect_material_colors`` to
auto-populate the filament palette from existing materials.

Image textures use **k-means clustering in OKLab** (perceptually uniform
color space) with oversampling and diversity selection.  This correctly
handles artistic/photographic images where the same hue appears at many
brightness levels (e.g. Starry Night's dark navy through bright sky blue).

Vertex colors use HSV histogram binning (:func:`_bin_pixels_hsv`) and
diversity-weighted selection (:func:`_select_diverse_colors`), which
works well for the simpler palettes typical of vertex-painted meshes.
"""

import numpy as np

from ..common.colors import linear_to_srgb as _linear_to_srgb
from ..common.logging import debug

from .helpers import _has_vertex_colors


# -------------------------------------------------------------------
#  Material / node-tree color detection
# -------------------------------------------------------------------


def _collect_material_colors(obj):
    """Extract unique colors from material slots on *obj*.

    Walks every material's node tree looking for Principled BSDF nodes.
    If the Base Color input is linked to a Color Ramp, all stop colors
    are collected.  Other simple nodes (RGB, Hue/Sat, Mix) yield a
    single color.  Unlinked Base Color default values are read directly.
    As a last resort the material's viewport ``diffuse_color`` is used.

    Near-duplicate colors (within ~5/255) are merged.
    """
    raw_colors = []

    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue

        found = []
        if mat.use_nodes and mat.node_tree:
            found = _extract_node_colors(mat.node_tree)

        # Fallback: viewport display color (already sRGB)
        if not found:
            dc = mat.diffuse_color
            found = [(dc[0], dc[1], dc[2])]

        raw_colors.extend(found)

    return _deduplicate_colors(raw_colors, tolerance=5.0 / 255.0)


def _extract_node_colors(node_tree):
    """Return a list of (r, g, b) sRGB tuples from the node tree.

    Looks at every Principled BSDF and extracts colors from whatever
    feeds its Base Color input.
    """
    colors = []
    for node in node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue

        base_input = node.inputs.get("Base Color")
        if base_input is None:
            continue

        if base_input.is_linked:
            linked_node = base_input.links[0].from_node
            colors.extend(_colors_from_node(linked_node))
        else:
            v = base_input.default_value
            colors.append((
                _linear_to_srgb(v[0]),
                _linear_to_srgb(v[1]),
                _linear_to_srgb(v[2]),
            ))

    return colors


def _colors_from_node(node):
    """Extract one or more (r, g, b) sRGB colors from a shader node.

    Supported node types:
    - **Color Ramp** (``VALTORGB``) — every color stop
    - **RGB** — the node's output value
    - **Hue/Sat, Gamma, Bright/Contrast** — the Color input default
    - **Mix / Mix RGB** — both A and B input defaults
    - **Separate/Combine** and other connector nodes — walks upstream
    """
    # ---- Color Ramp (most important for procedural setups) ----
    if node.type == "VALTORGB":
        found = []
        for stop in node.color_ramp.elements:
            c = stop.color  # linear RGBA
            found.append((
                _linear_to_srgb(c[0]),
                _linear_to_srgb(c[1]),
                _linear_to_srgb(c[2]),
            ))
        return found

    # ---- RGB node ----
    if node.type == "RGB":
        v = node.outputs[0].default_value
        return [(
            _linear_to_srgb(v[0]),
            _linear_to_srgb(v[1]),
            _linear_to_srgb(v[2]),
        )]

    # ---- Adjustment nodes with a Color input ----
    if node.type in ("HUE_SAT", "GAMMA", "BRIGHTCONTRAST"):
        inp = node.inputs.get("Color")
        if inp and not inp.is_linked:
            v = inp.default_value
            return [(
                _linear_to_srgb(v[0]),
                _linear_to_srgb(v[1]),
                _linear_to_srgb(v[2]),
            )]
        if inp and inp.is_linked:
            return _colors_from_node(inp.links[0].from_node)

    # ---- Mix / Mix RGB — collect both sides ----
    if node.type in ("MIX", "MIX_RGB"):
        found = []
        a_input = node.inputs.get("A") or node.inputs.get(6)
        b_input = node.inputs.get("B") or node.inputs.get(7)
        for inp in (a_input, b_input):
            if inp is None:
                continue
            if inp.is_linked:
                found.extend(_colors_from_node(inp.links[0].from_node))
            else:
                v = inp.default_value
                found.append((
                    _linear_to_srgb(v[0]),
                    _linear_to_srgb(v[1]),
                    _linear_to_srgb(v[2]),
                ))
        return found

    # ---- Walk upstream through passthrough / connector nodes ----
    color_input = node.inputs.get("Color") or node.inputs.get(0)
    if color_input and color_input.is_linked:
        return _colors_from_node(color_input.links[0].from_node)

    return []


def _deduplicate_colors(colors, tolerance=0.02):
    """Remove near-duplicate (r, g, b) tuples, preserving order."""
    unique = []
    for c in colors:
        is_dup = False
        for u in unique:
            if all(abs(a - b) < tolerance for a, b in zip(c, u)):
                is_dup = True
                break
        if not is_dup:
            unique.append(c)
    return unique


# -------------------------------------------------------------------
#  Image texture / vertex color detection helpers
# -------------------------------------------------------------------


def _get_any_image_texture(obj):
    """Find the first Image Texture node with image data on the active object.

    Unlike ``_get_paint_image`` this inspects *all* materials, skipping
    any images that are tagged as MMU paint textures (those have already
    been set up by the paint suite).
    """
    if not obj or not obj.data or not obj.data.materials:
        return None
    mesh = obj.data
    for mat in mesh.materials:
        if mat and mat.use_nodes and mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    # Skip images created by the paint suite itself
                    if node.image.name.endswith("_MMU_Paint"):
                        continue
                    return node.image
    return None


def _has_color_attribute_node(obj):
    """Return True if any material uses a Color Attribute / Attribute node
    connected to a Principled BSDF Base Color input."""
    if not obj or not obj.data or not obj.data.materials:
        return False
    for mat in obj.data.materials:
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            base_input = node.inputs.get("Base Color")
            if base_input and base_input.is_linked:
                src = base_input.links[0].from_node
                if src.type in ("ATTRIBUTE", "VERTEX_COLOR"):
                    return True
    return False


# -------------------------------------------------------------------
#  HSV histogram binning + diversity-weighted selection
# -------------------------------------------------------------------


def _srgb_to_hsv_array(srgb):
    """Convert an (N, 3) sRGB array to HSV.  H in [0, 1] (cyclic), S/V in [0, 1]."""
    r, g, b = srgb[:, 0], srgb[:, 1], srgb[:, 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    delta = maxc - minc
    v = maxc
    safe_maxc = np.where(maxc > 0, maxc, 1.0)
    s = np.where(maxc > 0, delta / safe_maxc, 0.0)
    h = np.zeros_like(maxc)
    mask = delta > 0
    idx = mask & (maxc == r)
    h[idx] = ((g[idx] - b[idx]) / delta[idx]) % 6.0
    idx = mask & (maxc == g)
    h[idx] = ((b[idx] - r[idx]) / delta[idx]) + 2.0
    idx = mask & (maxc == b)
    h[idx] = ((r[idx] - g[idx]) / delta[idx]) + 4.0
    h = h / 6.0  # normalise to [0, 1]
    return np.stack([h, s, v], axis=-1).astype(np.float32)


def _bin_pixels_hsv(srgb):
    """Bin an (N, 3) sRGB array by **Hue x Saturation**, ignoring brightness.

    Chromatic pixels (S >= 0.10) are placed into ``HUE_BINS x SAT_BINS``
    buckets.  Achromatic pixels (S < 0.10) are placed into ``VAL_BINS``
    grey-scale buckets.  This merges shadow/highlight variants of the
    same hue into one bin, which is far better for photo-realistic textures
    with baked-in lighting.

    Returns ``(bin_colors, bin_counts)`` where *bin_colors* is (M, 3) sRGB
    center-values and *bin_counts* is (M,), both sorted descending by count.
    The representative sRGB color of each HS bin is the **median** brightness
    at full saturation -> natural-looking palette entry.
    """
    HUE_BINS = 18
    SAT_BINS = 6
    VAL_BINS = 8
    ACHROMATIC_THR = 0.10

    hsv = _srgb_to_hsv_array(srgb)  # (N, 3)
    is_chromatic = hsv[:, 1] >= ACHROMATIC_THR

    # ----- chromatic bins (HS, ignore V) ---------------------------------
    chrom_hsv = hsv[is_chromatic]
    chrom_srgb = srgb[is_chromatic]

    h_idx = (chrom_hsv[:, 0] * (HUE_BINS - 0.001)).astype(np.uint16)
    s_idx = np.clip(
        ((chrom_hsv[:, 1] - ACHROMATIC_THR) / (1.0 - ACHROMATIC_THR) * (SAT_BINS - 0.001)).astype(np.uint16),
        0, SAT_BINS - 1,
    )
    chrom_bin_ids = h_idx.astype(np.uint32) * SAT_BINS + s_idx.astype(np.uint32)

    # offset of 1 so bin 0 is reserved for the achromatic range
    chrom_bin_ids += 1

    # ----- achromatic bins (value only) ----------------------------------
    achrom_hsv = hsv[~is_chromatic]
    achrom_srgb = srgb[~is_chromatic]
    v_idx = (achrom_hsv[:, 2] * (VAL_BINS - 0.001)).astype(np.uint16)
    # IDs 0 is unused; achromatic range uses negative to avoid clash --
    # actually just use a separate ID space above chromatic max.
    ACHROM_OFFSET = np.uint32(HUE_BINS * SAT_BINS + 1)
    achrom_bin_ids = v_idx.astype(np.uint32) + ACHROM_OFFSET

    # ----- merge and count -----------------------------------------------
    all_bin_ids = np.concatenate([chrom_bin_ids, achrom_bin_ids]) if len(achrom_bin_ids) else chrom_bin_ids
    all_srgb = np.concatenate([chrom_srgb, achrom_srgb]) if len(achrom_srgb) else chrom_srgb
    all_hsv = np.concatenate([chrom_hsv, achrom_hsv]) if len(achrom_hsv) else chrom_hsv

    unique_bins, inv, counts = np.unique(all_bin_ids, return_inverse=True, return_counts=True)
    order = np.argsort(-counts)
    unique_bins = unique_bins[order]
    counts = counts[order]

    # Build a representative sRGB color per bin.
    # For chromatic bins: take median V at the bin's center H/S -> vivid.
    # For achromatic bins: simple grey at the bin's center V.
    colors = np.empty((len(unique_bins), 3), dtype=np.float32)

    for out_i, bid in enumerate(unique_bins):
        mask = all_bin_ids == bid
        member_srgb = all_srgb[mask]
        member_hsv = all_hsv[mask]
        # Representative color: median hue/sat (stable), but 60th
        # percentile brightness.  On 3D models shadows cover more
        # surface area than highlights, so the median V is too dark.
        # The 60th percentile nudges toward "typical lit surface" while
        # keeping dark tones recognisably dark.
        med_srgb = np.median(member_srgb, axis=0)
        v_60 = np.percentile(member_hsv[:, 2], 60)
        med_v = np.median(member_hsv[:, 2])
        # Scale the median sRGB toward the brighter representative,
        # but cap at 1.3× to avoid washing out dark colors.
        if med_v > 0.01:
            boost = min(v_60 / med_v, 1.3)
            colors[out_i] = np.clip(med_srgb * boost, 0.0, 1.0)
        else:
            colors[out_i] = med_srgb

    debug(f"[Detect] _bin_pixels_hsv: {len(srgb)} pixels -> {len(unique_bins)} HS/V bins "
          f"(chromatic={int(np.sum(is_chromatic))}, achromatic={int(np.sum(~is_chromatic))})")
    return colors, counts


def _hs_distance(colors_a, colors_b_row):
    """Cyclic Hue-Saturation-Value distance between (N,3) and (3,) sRGB arrays.

    Converts to HSV, then computes a weighted distance using cyclic hue,
    saturation, and value.  Value is included so that dark navy vs bright
    sky blue register as distinct colors.  Achromatic entries (S < 0.10)
    use Euclidean RGB distance instead so that black/white/grey are
    handled correctly.
    """
    W_H = 6.0
    W_S = 3.0
    W_V = 2.0

    hsv_a = _srgb_to_hsv_array(colors_a)  # (N, 3)
    hsv_b = _srgb_to_hsv_array(colors_b_row.reshape(1, 3))  # (1, 3)

    dh = np.abs(hsv_a[:, 0] - hsv_b[0, 0])
    dh = np.minimum(dh, 1.0 - dh)
    ds = hsv_a[:, 1] - hsv_b[0, 1]
    dv = hsv_a[:, 2] - hsv_b[0, 2]

    hsv_dist = np.sqrt(W_H * dh ** 2 + W_S * ds ** 2 + W_V * dv ** 2)

    # RGB fallback for achromatic pixels
    rgb_dist = np.sqrt(np.sum((colors_a - colors_b_row) ** 2, axis=1))

    # Blend: use HSV for chromatic, RGB for achromatic
    sat = hsv_a[:, 1]
    alpha = np.clip(sat / 0.10, 0.0, 1.0)
    return alpha * hsv_dist + (1.0 - alpha) * rgb_dist


def _select_diverse_colors(bin_colors, bin_counts, num_colors):
    """Greedily pick *num_colors* that are both frequent AND visually diverse.

    1. First pick = most frequent bin.
    2. Each subsequent pick maximises ``frequency_weight x min_distance``
       where *min_distance* is the **HSV-dominant distance** (cyclic hue +
       saturation + value, with RGB fallback for greys) to the nearest
       already-selected color.

    Returns a list of ``(r, g, b)`` sRGB tuples.
    """
    n = len(bin_colors)
    if n == 0:
        return []
    if n <= num_colors:
        return [tuple(bin_colors[i]) for i in range(n)]

    max_count = float(bin_counts[0])
    weights = np.sqrt(bin_counts.astype(np.float64) / max_count)

    debug(f"[Detect] _select_diverse_colors: {n} bins, picking {num_colors}")
    debug("[Detect]   Top 10 bins by frequency:")
    for i in range(min(10, n)):
        c = bin_colors[i]
        debug(f"    bin[{i}] sRGB ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})  count={bin_counts[i]}  weight={weights[i]:.4f}")

    selected_indices = [0]
    min_dists = np.full(n, np.inf, dtype=np.float64)

    for step in range(num_colors - 1):
        last_sel = bin_colors[selected_indices[-1]]
        # HSV-dominant distance to the latest picked color
        dists = _hs_distance(bin_colors, last_sel)
        min_dists = np.minimum(min_dists, dists)

        scores = weights * min_dists
        for idx in selected_indices:
            scores[idx] = -1.0
        best = int(np.argmax(scores))
        c = bin_colors[best]
        debug(f"    Step {step + 1}: picked bin[{best}] sRGB ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})  "
              f"count={bin_counts[best]}  min_dist={min_dists[best]:.4f}  score={scores[best]:.4f}")
        selected_indices.append(best)

    result = [tuple(bin_colors[i]) for i in selected_indices]
    debug(f"[Detect] Final selection: {result}")
    return result


def _linear_to_srgb_array(rgb):
    """Convert an (N, 3) linear-light array to sRGB, clamped to [0, 1]."""
    srgb = np.where(
        rgb <= 0.0031308,
        rgb * 12.92,
        1.055 * np.power(np.clip(rgb, 0.0031308, None), 1.0 / 2.4) - 0.055,
    )
    return np.clip(srgb, 0.0, 1.0)


def _srgb_to_linear_array(srgb):
    """Convert an (N, 3) sRGB array to linear-light, clamped to [0, 1]."""
    return np.where(
        srgb <= 0.04045,
        srgb / 12.92,
        np.power((srgb + 0.055) / 1.055, 2.4),
    )


# -------------------------------------------------------------------
#  OKLab perceptual color space (for k-means palette extraction)
# -------------------------------------------------------------------

# OKLab M1: linear RGB -> LMS
_OKLAB_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
], dtype=np.float64)

# OKLab M2: LMS^(1/3) -> Lab
_OKLAB_M2 = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
], dtype=np.float64)

_OKLAB_M1_INV = np.linalg.inv(_OKLAB_M1)
_OKLAB_M2_INV = np.linalg.inv(_OKLAB_M2)


def _srgb_to_oklab(srgb):
    """Convert (N, 3) sRGB to OKLab.  Returns (N, 3) float64."""
    linear = _srgb_to_linear_array(srgb).astype(np.float64)
    lms = linear @ _OKLAB_M1.T
    # Clamp before cube root to handle small negative values from
    # out-of-gamut rounding
    lms_g = np.cbrt(np.maximum(lms, 0.0))
    return lms_g @ _OKLAB_M2.T


def _oklab_to_srgb(lab):
    """Convert (N, 3) OKLab to sRGB, clamped [0, 1].  Returns (N, 3) float32."""
    lms_g = lab @ _OKLAB_M2_INV.T
    lms = lms_g ** 3
    linear = lms @ _OKLAB_M1_INV.T
    srgb = np.where(
        linear <= 0.0031308,
        12.92 * linear,
        1.055 * np.power(np.maximum(linear, 0.0), 1.0 / 2.4) - 0.055,
    )
    return np.clip(srgb, 0.0, 1.0).astype(np.float32)


# -------------------------------------------------------------------
#  K-means palette extraction in OKLab
# -------------------------------------------------------------------

_MAX_PER_CELL = 200    # max pixels sampled per sRGB grid cell
_CELL_GRID = 8         # grid divisions per channel  (8^3 = 512 cells total)
_MAX_ITER = 25         # k-means iterations (converges in <15 typically)


def _spatially_balanced_sample(srgb, max_per_cell, grid_size):
    """Sample *srgb* (N, 3) with at most *max_per_cell* pixels per coarse
    sRGB grid cell (grid_size^3 cells total).

    A random global subsample is blind to rare colours: in a painting
    that is 75% blue and 5% yellow, a 50 K random draw yields ~37 500
    blue pixels and only ~2 500 yellow pixels, so k-means allocates
    almost all its clusters to blue and yellow disappears.  By capping
    each colour cell at *max_per_cell*, the dominant colour is down-
    sampled and rare colours are fully represented, giving k-means a
    balanced view of the palette.

    Returns ``(sample, n_occupied)`` where *sample* is (M, 3) float32
    and *n_occupied* is the number of non-empty cells.
    """
    q = (np.clip(srgb, 0.0, 1.0) * grid_size).astype(np.int32)
    q = np.clip(q, 0, grid_size - 1)
    cell_ids = (q[:, 0] * grid_size + q[:, 1]) * grid_size + q[:, 2]

    # Sort so all pixels in the same cell are contiguous
    sort_order = np.argsort(cell_ids, kind='stable')
    sorted_ids = cell_ids[sort_order]
    _, first_occ = np.unique(sorted_ids, return_index=True)
    boundaries = np.append(first_occ, len(sorted_ids))

    rng = np.random.default_rng(42)
    selected = []
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        cell_pixel_indices = sort_order[start:end]
        count = int(end - start)
        n_take = min(max_per_cell, count)
        if n_take < count:
            chosen = rng.choice(cell_pixel_indices, n_take, replace=False)
        else:
            chosen = cell_pixel_indices
        selected.append(chosen)

    all_idx = np.concatenate(selected)
    return srgb[all_idx], len(boundaries) - 1


def _kmeans_pp_init(data, k, rng):
    """K-means++ initialization.  *data* is (N, D), returns (k, D) centers."""
    n = len(data)
    centers = np.empty((k, data.shape[1]), dtype=data.dtype)
    centers[0] = data[rng.integers(n)]
    dist_sq = np.full(n, np.inf, dtype=np.float64)

    for i in range(1, k):
        d = np.sum((data - centers[i - 1]) ** 2, axis=1)
        dist_sq = np.minimum(dist_sq, d)
        total = dist_sq.sum()
        if total == 0.0:
            # Every sample point coincides with an already-chosen center —
            # no more distinct colors exist.  Truncate and let the caller's
            # empty-cluster discard step handle the reduced center count.
            centers = centers[:i]
            break
        prob = dist_sq / total
        centers[i] = data[rng.choice(n, p=prob)]

    return centers


def _kmeans(data, k, max_iter=_MAX_ITER, seed=42):
    """Run k-means in (N, D) space.  Returns (centers, labels, counts).

    *centers* is (k, D), *labels* is (N,) cluster assignment,
    *counts* is (k,) number of members per cluster.
    """
    rng = np.random.default_rng(seed)
    centers = _kmeans_pp_init(data, k, rng)

    labels = np.zeros(len(data), dtype=np.int32)
    for iteration in range(max_iter):
        # Assignment step: each point -> nearest center
        # Compute distances in chunks to limit memory (50K × 32 is fine)
        dists = np.sum((data[:, np.newaxis, :] - centers[np.newaxis, :, :]) ** 2, axis=2)
        new_labels = np.argmin(dists, axis=1).astype(np.int32)

        if np.array_equal(new_labels, labels):
            debug(f"[Detect]   k-means converged at iteration {iteration}")
            labels = new_labels
            break
        labels = new_labels

        # Update step: new centers = mean of assigned points
        for c in range(k):
            mask = labels == c
            if np.any(mask):
                centers[c] = data[mask].mean(axis=0)

    counts = np.bincount(labels, minlength=k)
    return centers, labels, counts


def _select_diverse_from_centers(centers_srgb, counts, num_colors):
    """Greedily pick *num_colors* from overclustered k-means centers.

    Uses OKLab Euclidean distance for perceptual uniformity.  Frequency
    weighting is intentionally weak (fourth-root) so that small but
    visually distinct clusters (e.g. yellow stars in a blue painting)
    can compete with dominant colors.

    Returns a list of ``(r, g, b)`` sRGB tuples.
    """
    n = len(centers_srgb)
    if n == 0:
        return []
    if n <= num_colors:
        return [tuple(centers_srgb[i]) for i in range(n)]

    centers_lab = _srgb_to_oklab(centers_srgb)
    max_count = float(np.max(counts))
    # Fourth-root: a cluster 10000× larger only gets 10× weight
    weights = np.power(counts.astype(np.float64) / max_count, 0.25)

    debug(f"[Detect] _select_diverse: {n} clusters, picking {num_colors}")
    order = np.argsort(-counts)
    debug("[Detect]   Top 10 clusters by count:")
    for rank in range(min(10, n)):
        i = order[rank]
        c = centers_srgb[i]
        debug(f"    cluster[{i}] sRGB ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})  "
              f"count={counts[i]}  weight={weights[i]:.4f}")

    # First pick: most frequent cluster
    selected = [int(order[0])]
    min_dists = np.full(n, np.inf, dtype=np.float64)

    for step in range(num_colors - 1):
        last_lab = centers_lab[selected[-1]]
        dists = np.sqrt(np.sum((centers_lab - last_lab) ** 2, axis=1))
        min_dists = np.minimum(min_dists, dists)

        scores = weights * min_dists
        for idx in selected:
            scores[idx] = -1.0
        best = int(np.argmax(scores))
        c = centers_srgb[best]
        debug(f"    Step {step + 1}: picked cluster[{best}] sRGB ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})  "
              f"count={counts[best]}  min_dist={min_dists[best]:.4f}  score={scores[best]:.4f}")
        selected.append(best)

    result = [tuple(centers_srgb[i]) for i in selected]
    debug(f"[Detect] Final palette: {result}")
    return result


def _extract_texture_colors(image, num_colors):
    """Extract *num_colors* dominant, visually diverse colors from *image*.

    Pipeline:
    1. Read pixels, discard transparent and near-white.
    2. Subsample to *_MAX_SAMPLE* pixels for speed.
    3. Convert to OKLab (perceptually uniform color space).
    4. K-means++ with ``k = num_colors * 3`` (overclustering).
    5. Convert cluster centers back to sRGB.
    6. Greedy diversity selection picks final *num_colors*.

    Returns a list of ``(r, g, b)`` sRGB tuples.
    """
    w, h = image.size
    debug(f"[Detect] _extract_texture_colors: image='{image.name}' size={w}x{h}")
    if w == 0 or h == 0:
        return []

    pixel_count = w * h * 4
    flat = np.empty(pixel_count, dtype=np.float32)
    image.pixels.foreach_get(flat)
    pixels = flat.reshape(-1, 4)  # (N, RGBA)
    debug(f"[Detect]   Total pixels: {len(pixels)}")

    # Discard fully transparent pixels
    opaque_mask = pixels[:, 3] >= 0.01
    rgb_linear = pixels[opaque_mask, :3]  # (M, 3) linear RGB
    debug(f"[Detect]   Opaque pixels: {len(rgb_linear)}")
    if rgb_linear.size == 0:
        return []

    srgb = _linear_to_srgb_array(rgb_linear)

    # Discard near-white pixels -- typically bare UV background
    near_white = np.all(srgb > 0.94, axis=1)
    debug(f"[Detect]   Near-white pixels discarded: {np.sum(near_white)}")
    srgb = srgb[~near_white]
    debug(f"[Detect]   Remaining pixels: {len(srgb)}")
    if len(srgb) == 0:
        return []

    # Colour-balanced sampling: cap each sRGB grid cell at _MAX_PER_CELL so
    # rare colours (e.g. yellow stars in a mostly-blue painting) are not
    # drowned out by the dominant colour in k-means cluster allocation.
    srgb_sample, n_cells = _spatially_balanced_sample(srgb, _MAX_PER_CELL, _CELL_GRID)
    debug(f"[Detect]   Balanced sample: {len(srgb_sample)} pixels from {n_cells} colour cells")

    # K-means in OKLab
    lab_sample = _srgb_to_oklab(srgb_sample)
    k = min(num_colors * 3, 32, len(srgb_sample))
    debug(f"[Detect]   Running k-means++ in OKLab (k={k}, max_iter={_MAX_ITER})")
    centers_lab, _, counts = _kmeans(lab_sample, k)

    # Discard empty clusters
    nonempty = counts > 0
    centers_lab = centers_lab[nonempty]
    counts = counts[nonempty]

    centers_srgb = _oklab_to_srgb(centers_lab)
    return _select_diverse_from_centers(centers_srgb, counts, num_colors)


def _extract_vertex_colors(obj, num_colors):
    """Extract the *num_colors* most dominant vertex colors from *obj*.

    Reads the active color attribute and applies HSV histogram binning
    + diversity-weighted selection (suitable for the simpler palettes
    typical of vertex-painted meshes).
    """
    if not _has_vertex_colors(obj):
        debug("[Detect] _extract_vertex_colors: no vertex colors on object")
        return []

    ca = obj.data.color_attributes
    color_attr = ca.active_color if ca.active_color is not None else ca[0]
    elem_count = len(color_attr.data)
    debug(f"[Detect] _extract_vertex_colors: attr='{color_attr.name}', "
          f"domain='{color_attr.domain}', data_type='{color_attr.data_type}', "
          f"elements={elem_count}")
    if elem_count == 0:
        return []

    flat = np.zeros(elem_count * 4, dtype=np.float32)
    color_attr.data.foreach_get("color", flat)
    rgb = flat.reshape(-1, 4)[:, :3]  # (N, 3) -- already linear in Blender

    # Sample first few values for debugging
    debug("[Detect]   First 5 raw linear RGB values:")
    for i in range(min(5, len(rgb))):
        debug(f"    [{i}] ({rgb[i, 0]:.4f}, {rgb[i, 1]:.4f}, {rgb[i, 2]:.4f})")

    srgb = _linear_to_srgb_array(rgb)
    debug("[Detect]   First 5 sRGB values:")
    for i in range(min(5, len(srgb))):
        debug(f"    [{i}] ({srgb[i, 0]:.4f}, {srgb[i, 1]:.4f}, {srgb[i, 2]:.4f})")

    # Discard near-white pixels (bare/untextured regions)
    near_white = np.all(srgb > 0.94, axis=1)
    debug(f"[Detect]   Near-white discarded: {np.sum(near_white)} / {len(srgb)}")
    srgb = srgb[~near_white]
    if srgb.size == 0:
        debug("[Detect]   All pixels were near-white, nothing left")
        return []

    bin_colors, bin_counts = _bin_pixels_hsv(srgb)
    debug(f"[Detect]   Unique bins: {len(bin_colors)}")
    return _select_diverse_colors(bin_colors, bin_counts, num_colors)
