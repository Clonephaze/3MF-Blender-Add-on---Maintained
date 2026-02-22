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
Color detection helpers for the MMU Paint suite.

Extracts dominant colors from material node trees, image textures, and
vertex color attributes.  Used by ``MMU_OT_detect_material_colors`` to
auto-populate the filament palette from existing materials.

All detection paths funnel through HSV-based histogram binning
(:func:`_bin_pixels_hsv`) and diversity-weighted selection
(:func:`_select_diverse_colors`) for perceptually distinct palettes.
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
        # Representative color: median hue/sat (stable), but 75th
        # percentile brightness.  On 3D models shadows cover more
        # surface area than highlights, so the median V is too dark.
        # The 75th percentile picks the "typical lit surface" color.
        med_srgb = np.median(member_srgb, axis=0)
        v_75 = np.percentile(member_hsv[:, 2], 75)
        med_v = np.median(member_hsv[:, 2])
        # Scale the median sRGB toward the brighter representative:
        #   if med_v > 0 boost each channel by (v_75 / med_v),
        #   clamped to [0, 1].
        if med_v > 0.01:
            boost = min(v_75 / med_v, 2.0)
            colors[out_i] = np.clip(med_srgb * boost, 0.0, 1.0)
        else:
            colors[out_i] = med_srgb

    debug(f"[Detect] _bin_pixels_hsv: {len(srgb)} pixels -> {len(unique_bins)} HS/V bins "
          f"(chromatic={int(np.sum(is_chromatic))}, achromatic={int(np.sum(~is_chromatic))})")
    return colors, counts


def _hs_distance(colors_a, colors_b_row):
    """Cyclic Hue-Saturation distance between (N,3) and (3,) sRGB arrays.

    Converts to HSV, then computes  sqrt(W_H*dh^2 + W_S*ds^2)  with cyclic
    hue wrapping.  Achromatic entries (S < 0.10) use Euclidean RGB
    distance instead so that black/white/grey are handled correctly.
    """
    W_H = 6.0
    W_S = 3.0

    hsv_a = _srgb_to_hsv_array(colors_a)  # (N, 3)
    hsv_b = _srgb_to_hsv_array(colors_b_row.reshape(1, 3))  # (1, 3)

    dh = np.abs(hsv_a[:, 0] - hsv_b[0, 0])
    dh = np.minimum(dh, 1.0 - dh)
    ds = hsv_a[:, 1] - hsv_b[0, 1]

    hsv_dist = np.sqrt(W_H * dh ** 2 + W_S * ds ** 2)

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
       where *min_distance* is the **HS-dominant distance** (cyclic hue +
       saturation, with RGB fallback for greys) to the nearest already-
       selected color.

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
        # HS-dominant distance to the latest picked color
        dists = _hs_distance(bin_colors, last_sel)
        min_dists = np.minimum(min_dists, dists)

        scores = weights * min_dists
        for idx in selected_indices:
            scores[idx] = -1.0
        best = int(np.argmax(scores))
        c = bin_colors[best]
        debug(f"    Step {step+1}: picked bin[{best}] sRGB ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})  "
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


def _extract_texture_colors(image, num_colors):
    """Extract the *num_colors* most dominant colors from *image*.

    Uses histogram binning (16 levels per RGB channel = 4096 bins)
    to group similar colors, then selects the N most frequent *and*
    visually diverse colors as sRGB (r, g, b) tuples.

    Fully transparent pixels (alpha < 0.01) and near-white pixels
    (likely untextured UV background) are ignored.
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
    rgb = pixels[opaque_mask, :3]  # (M, 3) linear RGB
    debug(f"[Detect]   Opaque pixels: {len(rgb)}")
    if rgb.size == 0:
        return []

    srgb = _linear_to_srgb_array(rgb)

    # Discard near-white pixels -- these are typically bare UV
    # background, not actual texture content (sRGB > ~0.94 per ch)
    near_white = np.all(srgb > 0.94, axis=1)
    debug(f"[Detect]   Near-white pixels discarded: {np.sum(near_white)}")
    srgb = srgb[~near_white]
    debug(f"[Detect]   Remaining pixels for binning: {len(srgb)}")
    if srgb.size == 0:
        return []

    bin_colors, bin_counts = _bin_pixels_hsv(srgb)
    debug(f"[Detect]   Unique bins: {len(bin_colors)}")
    return _select_diverse_colors(bin_colors, bin_counts, num_colors)


def _extract_vertex_colors(obj, num_colors):
    """Extract the *num_colors* most dominant vertex colors from *obj*.

    Reads the active color attribute and applies the same HSV histogram
    binning + diversity-weighted selection as ``_extract_texture_colors``.
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
        debug(f"    [{i}] ({rgb[i,0]:.4f}, {rgb[i,1]:.4f}, {rgb[i,2]:.4f})")

    srgb = _linear_to_srgb_array(rgb)
    debug("[Detect]   First 5 sRGB values:")
    for i in range(min(5, len(srgb))):
        debug(f"    [{i}] ({srgb[i,0]:.4f}, {srgb[i,1]:.4f}, {srgb[i,2]:.4f})")

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
