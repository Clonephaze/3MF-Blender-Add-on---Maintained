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
Export module for converting UV textures back to hash-based MMU segmentation strings.

This module handles the reverse process of import, converting painted textures back to
the universal hex hash format used by PrusaSlicer, Orca Slicer, and other slicers.

Process:
1. Pre-compute entire texture → state map (numpy vectorized, done ONCE)
2. Sample state map at triangle corners and interior points
3. Recursively build segmentation tree from state differences
4. Encode tree to hex hash string (reversed nibbles)
5. Simplify tree to reduce string length

The output format is slicer-agnostic - works for both paint_color and mmu_segmentation.
"""

import numpy as np
from typing import Tuple, List, Dict
from .hash_segmentation import SegmentationNode, SegmentationEncoder

# Maximum subdivision depth (6 gives 4^6 = 4096 potential leaf nodes per triangle)
MAX_SUBDIVISION_DEPTH = 6


def _build_state_map(
    pixels: np.ndarray,
    color_to_extruder: Dict[Tuple[int, int, int], int],
    default_extruder: int,
) -> np.ndarray:
    """
    Convert entire pixel array to a state map using numpy vectorized ops.

    Key insight: per-sample color matching inside recursion was the hot path.
    By converting the full texture once, recursion becomes just
    `state_map[y, x]` lookups with no RGB distance work.

    :param pixels: (H, W, 4) float32 array
    :param color_to_extruder: RGB(0-255) -> 0-based extruder index
    :param default_extruder: 1-based default extruder number
    :return: (H, W) uint8 array of state values
    """
    height, width = pixels.shape[:2]

    # Convert float [0,1] to uint8 [0,255] for RGB channels only.
    rgb_int = (pixels[:, :, :3] * 255).astype(np.uint8)

    known_rgbs = []
    known_states = []
    for rgb, ext_idx in color_to_extruder.items():
        known_rgbs.append(rgb)
        ext_num = ext_idx + 1
        state = 0 if ext_num == default_extruder else ext_num
        known_states.append(state)

    known_rgbs = np.array(known_rgbs, dtype=np.int16)
    known_states = np.array(known_states, dtype=np.uint8)
    n_colors = len(known_rgbs)

    state_map = np.empty((height, width), dtype=np.uint8)

    # Chunking keeps peak memory bounded (important for 4K/8K textures).
    chunk_size = 256
    for y_start in range(0, height, chunk_size):
        y_end = min(y_start + chunk_size, height)
        chunk = rgb_int[y_start:y_end]
        chunk_h = chunk.shape[0]

        chunk_expanded = chunk.reshape(chunk_h, width, 1, 3).astype(np.int16)
        colors_expanded = known_rgbs.reshape(1, 1, n_colors, 3)

        dists = np.sum(np.abs(chunk_expanded - colors_expanded), axis=3)

        nearest_idx = np.argmin(dists, axis=2)

        state_map[y_start:y_end] = known_states[nearest_idx]

    return state_map


def _analyze_recursive(
    state_map: np.ndarray,
    width: int,
    height: int,
    u0: float,
    v0: float,
    u1: float,
    v1: float,
    u2: float,
    v2: float,
    max_depth: int,
) -> SegmentationNode:
    """
    Recursively analyze a triangle's segmentation from the pre-computed state map.

    Insight: passing tuples and callbacks was expensive at this depth.
    Inlining floats and sampling directly keeps recursion overhead low.
    """
    # Inline UV->pixel conversion with rounding to avoid bias.
    wm1 = width - 1
    hm1 = height - 1

    x0 = max(0, min(wm1, int(max(0.0, min(1.0, u0)) * wm1 + 0.5)))
    y0 = max(0, min(hm1, int(max(0.0, min(1.0, v0)) * hm1 + 0.5)))
    s0 = int(state_map[y0, x0])

    x1 = max(0, min(wm1, int(max(0.0, min(1.0, u1)) * wm1 + 0.5)))
    y1 = max(0, min(hm1, int(max(0.0, min(1.0, v1)) * hm1 + 0.5)))
    s1 = int(state_map[y1, x1])

    x2 = max(0, min(wm1, int(max(0.0, min(1.0, u2)) * wm1 + 0.5)))
    y2 = max(0, min(hm1, int(max(0.0, min(1.0, v2)) * hm1 + 0.5)))
    s2 = int(state_map[y2, x2])

    # Even if corners match, interior stripes can cross a triangle.
    # Sample center + edges + quarter points before treating as uniform.
    if s0 == s1 == s2:
        cu = (u0 + u1 + u2) * 0.3333333333333333
        cv = (v0 + v1 + v2) * 0.3333333333333333
        cx = max(0, min(wm1, int(max(0.0, min(1.0, cu)) * wm1 + 0.5)))
        cy = max(0, min(hm1, int(max(0.0, min(1.0, cv)) * hm1 + 0.5)))
        if int(state_map[cy, cx]) != s0:
            pass
        else:
            m01u = (u0 + u1) * 0.5
            m01v = (v0 + v1) * 0.5
            mx = max(0, min(wm1, int(max(0.0, min(1.0, m01u)) * wm1 + 0.5)))
            my = max(0, min(hm1, int(max(0.0, min(1.0, m01v)) * hm1 + 0.5)))
            if int(state_map[my, mx]) != s0:
                pass
            else:
                m12u = (u1 + u2) * 0.5
                m12v = (v1 + v2) * 0.5
                mx = max(0, min(wm1, int(max(0.0, min(1.0, m12u)) * wm1 + 0.5)))
                my = max(0, min(hm1, int(max(0.0, min(1.0, m12v)) * hm1 + 0.5)))
                if int(state_map[my, mx]) != s0:
                    pass
                else:
                    m20u = (u2 + u0) * 0.5
                    m20v = (v2 + v0) * 0.5
                    mx = max(0, min(wm1, int(max(0.0, min(1.0, m20u)) * wm1 + 0.5)))
                    my = max(0, min(hm1, int(max(0.0, min(1.0, m20v)) * hm1 + 0.5)))
                    if int(state_map[my, mx]) != s0:
                        pass
                    else:
                        qu = (u0 * 2 + cu) * 0.3333333333333333
                        qv = (v0 * 2 + cv) * 0.3333333333333333
                        qx = max(0, min(wm1, int(max(0.0, min(1.0, qu)) * wm1 + 0.5)))
                        qy = max(0, min(hm1, int(max(0.0, min(1.0, qv)) * hm1 + 0.5)))
                        if int(state_map[qy, qx]) != s0:
                            pass
                        else:
                            qu = (u1 * 2 + cu) * 0.3333333333333333
                            qv = (v1 * 2 + cv) * 0.3333333333333333
                            qx = max(
                                0, min(wm1, int(max(0.0, min(1.0, qu)) * wm1 + 0.5))
                            )
                            qy = max(
                                0, min(hm1, int(max(0.0, min(1.0, qv)) * hm1 + 0.5))
                            )
                            if int(state_map[qy, qx]) != s0:
                                pass
                            else:
                                qu = (u2 * 2 + cu) * 0.3333333333333333
                                qv = (v2 * 2 + cv) * 0.3333333333333333
                                qx = max(
                                    0, min(wm1, int(max(0.0, min(1.0, qu)) * wm1 + 0.5))
                                )
                                qy = max(
                                    0, min(hm1, int(max(0.0, min(1.0, qv)) * hm1 + 0.5))
                                )
                                if int(state_map[qy, qx]) != s0:
                                    pass
                                else:
                                    return SegmentationNode(
                                        state=s0,
                                        split_sides=0,
                                        special_side=0,
                                        children=[],
                                    )

    # Max depth reached: collapse to the most common corner state.
    if max_depth <= 0:
        if s0 == s1 or s0 == s2:
            return SegmentationNode(
                state=s0, split_sides=0, special_side=0, children=[]
            )
        elif s1 == s2:
            return SegmentationNode(
                state=s1, split_sides=0, special_side=0, children=[]
            )
        else:
            return SegmentationNode(
                state=s0, split_sides=0, special_side=0, children=[]
            )

    # Standard 3-edge split for recursive subdivision.
    m01u = (u0 + u1) * 0.5
    m01v = (v0 + v1) * 0.5
    m12u = (u1 + u2) * 0.5
    m12v = (v1 + v2) * 0.5
    m20u = (u2 + u0) * 0.5
    m20v = (v2 + v0) * 0.5

    nd = max_depth - 1
    c0 = _analyze_recursive(
        state_map, width, height, u0, v0, m01u, m01v, m20u, m20v, nd
    )
    c1 = _analyze_recursive(
        state_map, width, height, m01u, m01v, u1, v1, m12u, m12v, nd
    )
    c2 = _analyze_recursive(
        state_map, width, height, m12u, m12v, u2, v2, m20u, m20v, nd
    )
    c3 = _analyze_recursive(
        state_map, width, height, m01u, m01v, m12u, m12v, m20u, m20v, nd
    )

    if not c0.children and not c1.children and not c2.children and not c3.children:
        if c0.state == c1.state == c2.state == c3.state:
            return SegmentationNode(
                state=c0.state, split_sides=0, special_side=0, children=[]
            )

    return SegmentationNode(
        state=c0.state if c0 else 0,
        split_sides=3,
        special_side=0,
        children=[c3, c2, c1, c0],
    )


def texture_to_segmentation(
    obj, image, extruder_colors: Dict[int, List[float]], default_extruder: int = 1
) -> Dict[int, str]:
    """
    Convert object's UV texture to PrusaSlicer segmentation strings.

    :param obj: Blender object with UV-mapped mesh
    :param image: Painted texture image
    :param extruder_colors: Mapping from extruder index to RGBA color
    :param default_extruder: Default extruder index
    :return: Dict mapping face_index -> segmentation_hex_string
    """
    import time

    t_start = time.perf_counter()

    mesh = obj.data
    width, height = image.size

    # One-time read from Blender image into numpy for fast access.
    print(f"  Caching {width}x{height} texture data as numpy array...")
    pixel_count = width * height * 4
    pixels_flat = np.empty(pixel_count, dtype=np.float32)
    image.pixels.foreach_get(pixels_flat)
    pixels = pixels_flat.reshape(height, width, 4)
    t_cache = time.perf_counter()
    print(f"  Cached pixels in {t_cache - t_start:.2f}s")

    color_to_extruder = {}
    print(f"  Building color->extruder map from {len(extruder_colors)} colors:")
    for extruder, rgba in extruder_colors.items():
        rgb = (int(rgba[0] * 255), int(rgba[1] * 255), int(rgba[2] * 255))
        color_to_extruder[rgb] = extruder
        print(f"    Extruder {extruder}: RGB {rgb}")
    print(f"  Default extruder: {default_extruder}")

    # Pre-compute the entire texture to states (critical performance win).
    print(f"  Building state map ({width}x{height})...")
    state_map = _build_state_map(pixels, color_to_extruder, default_extruder)
    t_state = time.perf_counter()

    unique_states = np.unique(state_map)
    print(
        f"  State map built in {t_state - t_cache:.2f}s, unique states: {list(unique_states)}"
    )

    if not mesh.uv_layers or not mesh.uv_layers.active:
        return {}

    uv_layer = mesh.uv_layers.active.data
    seg_strings = {}

    total_faces = sum(1 for p in mesh.polygons if len(p.loop_indices) == 3)
    print(
        f"  Processing {total_faces} triangles (max_depth={MAX_SUBDIVISION_DEPTH})..."
    )

    encoder = SegmentationEncoder()

    processed = 0
    for poly in mesh.polygons:
        if len(poly.loop_indices) != 3:
            continue

        processed += 1
        if processed % 2000 == 0:
            elapsed = time.perf_counter() - t_state
            rate = processed / elapsed if elapsed > 0 else 0
            remaining = (total_faces - processed) / rate if rate > 0 else 0
            print(
                f"    {processed}/{total_faces} ({rate:.0f}/s, ~{remaining:.0f}s left)"
            )

        li = poly.loop_indices
        uv0 = uv_layer[li[0]].uv
        uv1 = uv_layer[li[1]].uv
        uv2 = uv_layer[li[2]].uv

        if processed <= 3:
            wm1 = width - 1
            hm1 = height - 1
            px0 = pixels[
                max(0, min(hm1, round(uv0[1] * hm1))),
                max(0, min(wm1, round(uv0[0] * wm1))),
            ]
            px1 = pixels[
                max(0, min(hm1, round(uv1[1] * hm1))),
                max(0, min(wm1, round(uv1[0] * wm1))),
            ]
            px2 = pixels[
                max(0, min(hm1, round(uv2[1] * hm1))),
                max(0, min(wm1, round(uv2[0] * wm1))),
            ]
            print(
                f"      [Triangle {poly.index}] Corner colors: "
                f"RGB({int(px0[0] * 255)},{int(px0[1] * 255)},{int(px0[2] * 255)}), "
                f"RGB({int(px1[0] * 255)},{int(px1[1] * 255)},{int(px1[2] * 255)}), "
                f"RGB({int(px2[0] * 255)},{int(px2[1] * 255)},{int(px2[2] * 255)})"
            )

        tree = _analyze_recursive(
            state_map,
            width,
            height,
            uv0[0],
            uv0[1],
            uv1[0],
            uv1[1],
            uv2[0],
            uv2[1],
            MAX_SUBDIVISION_DEPTH,
        )

        if processed <= 10:
            has_children = len(tree.children) if tree.children else 0
            will_encode = bool(tree.children or tree.state != 0)
            print(
                f"    Triangle poly.index={poly.index}: state={tree.state}, children={has_children}, will_encode={will_encode}"
            )

        if tree.children or tree.state != 0:
            encoder._nibbles = []
            hex_string = encoder.encode(tree)
            if hex_string and hex_string != "0":
                seg_strings[poly.index] = hex_string
                if processed <= 10:
                    print(
                        f"      -> Stored key {poly.index}, encoded '{hex_string[:20]}...'"
                    )

    t_end = time.perf_counter()
    print(
        f"  Processed {total_faces} triangles in {t_end - t_state:.1f}s, found {len(seg_strings)} with segmentation"
    )
    print(f"  Total export time: {t_end - t_start:.1f}s")

    return seg_strings
