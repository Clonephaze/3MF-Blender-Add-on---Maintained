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
Hash-based MMU segmentation string decoder/encoder for 3D printing slicers.

This module implements parsing and encoding of multi-material segmentation using
hex-encoded binary trees. Used by PrusaSlicer (slic3rpe:mmu_segmentation attribute)
and Orca Slicer (paint_color attribute). The hash format is slicer-agnostic.

The format encodes a recursive subdivision tree with material/color assignments per region.

Format Reference (from PrusaSlicer TriangleSelector.cpp):
- Each nibble (4 bits) encodes: xxyy
  - yy = number of split sides (0=leaf, 1-3=subdivided)
  - xx = special_side (if split) OR state (if leaf with state < 3)
- If leaf AND xx == 0b11: next nibble contains (state - 3)
- Tree is traversed depth-first, children in reverse order
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, List, Tuple

from .logging import debug, warn


class TriangleState(IntEnum):
    """Triangle paint state - maps to extruder numbers.

    State 0 = object's default extruder (resolved from metadata during import)
    State N (N > 0) = Extruder N directly (1-based extruder index)
    """

    DEFAULT = 0
    EXTRUDER_1 = 1
    EXTRUDER_2 = 2
    EXTRUDER_3 = 3
    EXTRUDER_4 = 4
    EXTRUDER_5 = 5
    EXTRUDER_6 = 6
    EXTRUDER_7 = 7
    EXTRUDER_8 = 8
    EXTRUDER_9 = 9
    EXTRUDER_10 = 10
    EXTRUDER_11 = 11
    EXTRUDER_12 = 12
    EXTRUDER_13 = 13
    EXTRUDER_14 = 14
    EXTRUDER_15 = 15


@dataclass
class SegmentationNode:
    """
    A node in the triangle subdivision tree.

    Either a leaf node with a state (material), or an internal node with children.
    """

    state: TriangleState = TriangleState.DEFAULT

    split_sides: int = 0
    special_side: int = 0

    children: Optional[List[SegmentationNode]] = None

    @property
    def is_leaf(self) -> bool:
        """Check if this is a leaf node (no subdivision)."""
        return self.split_sides == 0

    @property
    def num_children(self) -> int:
        """Number of child triangles if subdivided."""
        return 0 if self.is_leaf else self.split_sides + 1


@dataclass
class SubdividedTriangle:
    """
    A triangle resulting from segmentation subdivision.

    Contains vertex indices (into the expanded vertex list) and the material state.
    """

    v0: int
    v1: int
    v2: int
    state: TriangleState

    source_triangle_index: int = -1


class SegmentationDecoder:
    """
    Decodes PrusaSlicer segmentation strings into subdivision trees.

    The hex string is stored in REVERSED order in the 3MF file, so we reverse
    it before parsing. Each nibble (hex character) encodes tree structure info.

    Usage:
        decoder = SegmentationDecoder()
        tree = decoder.decode("00000444344043040...")

        # Or decode and subdivide a triangle:
        triangles = decoder.subdivide_triangle(
            vertices=[(0,0,0), (1,0,0), (0.5,1,0)],
            segmentation_string="0004..."
        )
    """

    def __init__(self):
        self._hex_string: str = ""
        self._nibble_index: int = 0

    def _read_nibble(self) -> int:
        """Read next nibble (4 bits) from the hex string."""
        if self._nibble_index >= len(self._hex_string):
            raise ValueError(
                f"Segmentation string truncated at nibble {self._nibble_index}/{len(self._hex_string)}"
            )

        try:
            nibble = int(self._hex_string[self._nibble_index], 16)
        except ValueError as e:
            raise ValueError(
                f"Invalid hex character '{self._hex_string[self._nibble_index]}' at position {self._nibble_index}"
            ) from e

        self._nibble_index += 1
        return nibble

    def decode(self, hex_string: str) -> Optional[SegmentationNode]:
        """
        Decode a segmentation hex string into a tree structure.

        PrusaSlicer stores the hex string in REVERSED order, so we reverse it
        before parsing to read the tree root-first.

        :param hex_string: The slic3rpe:mmu_segmentation attribute value
        :return: Root node of the subdivision tree, or None if empty/invalid
        """
        if not hex_string:
            return None

        # The on-disk order is reversed; decode expects root-first order.
        self._hex_string = hex_string[::-1]
        self._nibble_index = 0

        if len(self._hex_string) < 1:
            return None

        try:
            node = self._decode_node()
            if self._nibble_index < len(self._hex_string):
                debug(
                    f"Warning: {len(self._hex_string) - self._nibble_index} unused nibbles in segmentation string"
                )
            return node
        except Exception as e:
            warn(f"Error decoding segmentation string (length {len(hex_string)}): {e}")
            return None

    def _decode_node(self) -> SegmentationNode:
        """Recursively decode a single node from the bitstream."""
        code = self._read_nibble()

        split_sides = code & 0b11
        special_side = (code >> 2) & 0b11

        if split_sides == 0:
            if special_side == 0b11:
                state = self._read_nibble() + 3
            else:
                state = special_side

            return SegmentationNode(state=TriangleState(min(state, 15)))
        else:
            num_children = split_sides + 1

            # Children are stored in the slicer order; decoder keeps that order
            # and the encoder reverses at the end to match the file format.
            children = []
            for _ in range(num_children):
                children.append(self._decode_node())

            return SegmentationNode(
                split_sides=split_sides, special_side=special_side, children=children
            )


class TriangleSubdivider:
    """
    Subdivides triangles based on segmentation trees.

    This class takes the decoded segmentation tree and the original triangle
    vertices, then produces a list of leaf triangles with their states.
    """

    def __init__(self):
        self._vertices: List[Tuple[float, float, float]] = []
        self._vertex_map: dict = {}
        self._result_triangles: List[SubdividedTriangle] = []

    def subdivide(
        self,
        v0: Tuple[float, float, float],
        v1: Tuple[float, float, float],
        v2: Tuple[float, float, float],
        tree: SegmentationNode,
        source_triangle_index: int = -1,
    ) -> Tuple[List[Tuple[float, float, float]], List[SubdividedTriangle]]:
        """
        Subdivide a triangle according to the segmentation tree.

        :param v0, v1, v2: Original triangle vertices
        :param tree: Decoded segmentation tree
        :param source_triangle_index: Index of original triangle (for roundtrip tracking)
        :return: Tuple of (all vertices, list of leaf triangles)
        """
        self._vertices = [v0, v1, v2]
        self._vertex_map = {}
        self._result_triangles = []

        self._subdivide_node(tree, 0, 1, 2, source_triangle_index)

        return self._vertices, self._result_triangles

    def _get_midpoint(self, idx1: int, idx2: int) -> int:
        """Get or create vertex at midpoint of edge (idx1, idx2)."""
        key = (min(idx1, idx2), max(idx1, idx2))

        if key in self._vertex_map:
            return self._vertex_map[key]

        v1 = self._vertices[idx1]
        v2 = self._vertices[idx2]
        midpoint = ((v1[0] + v2[0]) / 2.0, (v1[1] + v2[1]) / 2.0, (v1[2] + v2[2]) / 2.0)

        new_idx = len(self._vertices)
        self._vertices.append(midpoint)
        self._vertex_map[key] = new_idx

        return new_idx

    def _subdivide_node(
        self,
        node: SegmentationNode,
        i0: int,
        i1: int,
        i2: int,
        source_triangle_index: int,
    ):
        """
        Recursively subdivide based on a node.

        :param node: Current tree node
        :param i0, i1, i2: Vertex indices of current triangle
        :param source_triangle_index: Original triangle index for tracking
        """
        if node.is_leaf:
            self._result_triangles.append(
                SubdividedTriangle(
                    v0=i0,
                    v1=i1,
                    v2=i2,
                    state=node.state,
                    source_triangle_index=source_triangle_index,
                )
            )
            return

        split_sides = node.split_sides
        special = node.special_side

        # special_side rotates which edge is treated as the first split edge.
        # This matches slicer behavior when encoding non-3-way splits.
        verts = [i0, i1, i2]
        rotated = [verts[(special + j) % 3] for j in range(3)]
        r0, r1, r2 = rotated[0], rotated[1], rotated[2]

        # Children are encoded in forward order but stored reversed in the file.
        # Reverse here so subdivision order matches the original slicer output.
        children = node.children[::-1]

        if split_sides == 1:
            m = self._get_midpoint(r1, r2)

            self._subdivide_node(children[0], r0, r1, m, source_triangle_index)
            self._subdivide_node(children[1], m, r2, r0, source_triangle_index)

        elif split_sides == 2:
            m01 = self._get_midpoint(r0, r1)
            m20 = self._get_midpoint(r2, r0)

            self._subdivide_node(children[0], r0, m01, m20, source_triangle_index)
            self._subdivide_node(children[1], m01, r1, m20, source_triangle_index)
            self._subdivide_node(children[2], r1, r2, m20, source_triangle_index)

        elif split_sides == 3:
            m01 = self._get_midpoint(r0, r1)
            m12 = self._get_midpoint(r1, r2)
            m20 = self._get_midpoint(r2, r0)

            self._subdivide_node(children[0], r0, m01, m20, source_triangle_index)
            self._subdivide_node(children[1], m01, r1, m12, source_triangle_index)
            self._subdivide_node(children[2], m12, r2, m20, source_triangle_index)
            self._subdivide_node(children[3], m01, m12, m20, source_triangle_index)


class SegmentationEncoder:
    """
    Encodes subdivision trees back to PrusaSlicer hex strings.

    For roundtrip preservation, we can store the original string and avoid
    re-encoding entirely. But this class allows generating new strings from
    modified subdivision data.
    """

    def __init__(self):
        self._nibbles: List[int] = []

    def encode(self, tree: SegmentationNode) -> str:
        """
        Encode a segmentation tree to a hex string.

        Matches PrusaSlicer's serialization format exactly.
        The output is reversed to match the stored format in 3MF files.

        :param tree: Root node of the subdivision tree
        :return: Hex string suitable for slic3rpe:mmu_segmentation attribute
        """
        self._nibbles = []
        self._encode_node(tree)

        # Reverse at the end to match slic3rpe:mmu_segmentation storage order.
        hex_str = "".join(format(n, "X") for n in self._nibbles)
        return hex_str[::-1]

    def _encode_node(self, node: SegmentationNode):
        """Recursively encode a node to nibbles."""
        if node.is_leaf:
            state = int(node.state)
            if state >= 3:
                self._nibbles.append(0b1100)
                self._nibbles.append(state - 3)
            else:
                self._nibbles.append((state << 2) | 0)
        else:
            code = (node.special_side << 2) | node.split_sides
            self._nibbles.append(code)

            # Encode in forward order; reversal happens on the final hex string.
            for child in node.children:
                self._encode_node(child)


def decode_segmentation_string(hex_string: str) -> Optional[SegmentationNode]:
    """
    Convenience function to decode a segmentation string.

    :param hex_string: The slic3rpe:mmu_segmentation attribute value
    :return: Root node of the subdivision tree
    """
    decoder = SegmentationDecoder()
    return decoder.decode(hex_string)


def subdivide_triangle_with_segmentation(
    vertices: List[Tuple[float, float, float]],
    v0_idx: int,
    v1_idx: int,
    v2_idx: int,
    hex_string: str,
    source_triangle_index: int = -1,
) -> Tuple[List[Tuple[float, float, float]], List[SubdividedTriangle]]:
    """
    Decode segmentation string and subdivide a triangle accordingly.

    :param vertices: List of all vertices (will be extended with new midpoints)
    :param v0_idx, v1_idx, v2_idx: Indices into vertices for the triangle
    :param hex_string: The slic3rpe:mmu_segmentation attribute value
    :param source_triangle_index: Original triangle index for roundtrip tracking
    :return: Tuple of (updated vertices list, list of resulting triangles)
    """
    tree = decode_segmentation_string(hex_string)
    if tree is None:
        # No segmentation string means the triangle stays intact.
        return vertices, [
            SubdividedTriangle(
                v0=v0_idx,
                v1=v1_idx,
                v2=v2_idx,
                state=TriangleState.NONE,
                source_triangle_index=source_triangle_index,
            )
        ]

    v0 = vertices[v0_idx]
    v1 = vertices[v1_idx]
    v2 = vertices[v2_idx]

    subdivider = TriangleSubdivider()
    new_verts, sub_tris = subdivider.subdivide(v0, v1, v2, tree, source_triangle_index)

    base_idx = len(vertices)

    for v in new_verts[3:]:
        vertices.append(v)

    def remap_idx(local_idx: int) -> int:
        if local_idx == 0:
            return v0_idx
        elif local_idx == 1:
            return v1_idx
        elif local_idx == 2:
            return v2_idx
        else:
            return base_idx + (local_idx - 3)

    result_tris = []
    for tri in sub_tris:
        result_tris.append(
            SubdividedTriangle(
                v0=remap_idx(tri.v0),
                v1=remap_idx(tri.v1),
                v2=remap_idx(tri.v2),
                state=tri.state,
                source_triangle_index=tri.source_triangle_index,
            )
        )

    return vertices, result_tris
