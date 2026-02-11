"""
Unit tests for ``io_mesh_3mf.common.segmentation``.

Tests the hash-based MMU segmentation decoder/encoder — the codec that
converts between hex strings and recursive subdivision trees.

The segmentation module is pure Python (no bpy / mathutils).
"""

import unittest
from io_mesh_3mf.common.segmentation import (
    TriangleState,
    SegmentationNode,
    SegmentationDecoder,
    SegmentationEncoder,
    TriangleSubdivider,
    decode_segmentation_string,
)


# ============================================================================
# TriangleState enum
# ============================================================================

class TestTriangleState(unittest.TestCase):
    """TriangleState IntEnum values."""

    def test_default_is_zero(self):
        self.assertEqual(TriangleState.DEFAULT, 0)

    def test_extruder_values(self):
        self.assertEqual(TriangleState.EXTRUDER_1, 1)
        self.assertEqual(TriangleState.EXTRUDER_15, 15)

    def test_castable_from_int(self):
        self.assertEqual(TriangleState(3), TriangleState.EXTRUDER_3)


# ============================================================================
# SegmentationNode
# ============================================================================

class TestSegmentationNode(unittest.TestCase):
    """SegmentationNode dataclass and properties."""

    def test_leaf_node(self):
        node = SegmentationNode(state=TriangleState.EXTRUDER_1)
        self.assertTrue(node.is_leaf)
        self.assertEqual(node.num_children, 0)

    def test_split_node(self):
        child1 = SegmentationNode(state=TriangleState.EXTRUDER_1)
        child2 = SegmentationNode(state=TriangleState.EXTRUDER_2)
        node = SegmentationNode(
            split_sides=1, special_side=0, children=[child1, child2]
        )
        self.assertFalse(node.is_leaf)
        self.assertEqual(node.num_children, 2)

    def test_three_way_split(self):
        children = [SegmentationNode() for _ in range(4)]
        node = SegmentationNode(split_sides=3, children=children)
        self.assertEqual(node.num_children, 4)


# ============================================================================
# SegmentationDecoder
# ============================================================================

class TestSegmentationDecoder(unittest.TestCase):
    """SegmentationDecoder.decode()."""

    def setUp(self):
        self.decoder = SegmentationDecoder()

    def test_empty_string(self):
        self.assertIsNone(self.decoder.decode(""))

    def test_leaf_state_0(self):
        """Nibble 0x0 → leaf, state DEFAULT (xx=00, yy=00)."""
        # Encoding for state-0 leaf: nibble = 0b0000 = 0x0
        # On-disk format is reversed, so writing "0" reversed is still "0"
        encoder = SegmentationEncoder()
        node = SegmentationNode(state=TriangleState.DEFAULT)
        hex_str = encoder.encode(node)

        result = self.decoder.decode(hex_str)
        self.assertIsNotNone(result)
        self.assertTrue(result.is_leaf)
        self.assertEqual(result.state, TriangleState.DEFAULT)

    def test_leaf_state_1(self):
        """Leaf with state EXTRUDER_1 (xx=01, yy=00 → nibble 0x4)."""
        encoder = SegmentationEncoder()
        node = SegmentationNode(state=TriangleState.EXTRUDER_1)
        hex_str = encoder.encode(node)

        result = self.decoder.decode(hex_str)
        self.assertIsNotNone(result)
        self.assertTrue(result.is_leaf)
        self.assertEqual(result.state, TriangleState.EXTRUDER_1)

    def test_leaf_state_2(self):
        """Leaf with state EXTRUDER_2 (xx=10, yy=00 → nibble 0x8)."""
        encoder = SegmentationEncoder()
        node = SegmentationNode(state=TriangleState.EXTRUDER_2)
        hex_str = encoder.encode(node)

        result = self.decoder.decode(hex_str)
        self.assertIsNotNone(result)
        self.assertEqual(result.state, TriangleState.EXTRUDER_2)

    def test_leaf_state_high(self):
        """Leaf with state >= 3 uses extra nibble."""
        encoder = SegmentationEncoder()
        node = SegmentationNode(state=TriangleState.EXTRUDER_5)
        hex_str = encoder.encode(node)

        result = self.decoder.decode(hex_str)
        self.assertIsNotNone(result)
        self.assertEqual(result.state, TriangleState.EXTRUDER_5)

    def test_decode_encode_round_trip_simple(self):
        """Encode then decode a simple subdivision tree."""
        child_a = SegmentationNode(state=TriangleState.EXTRUDER_1)
        child_b = SegmentationNode(state=TriangleState.EXTRUDER_2)
        tree = SegmentationNode(split_sides=1, special_side=0, children=[child_a, child_b])

        encoder = SegmentationEncoder()
        hex_str = encoder.encode(tree)

        result = self.decoder.decode(hex_str)
        self.assertIsNotNone(result)
        self.assertFalse(result.is_leaf)
        self.assertEqual(result.split_sides, 1)
        self.assertEqual(len(result.children), 2)

    def test_decode_invalid_hex(self):
        """Non-hex characters should return None (graceful failure)."""
        result = self.decoder.decode("ZZZZ")
        self.assertIsNone(result)


# ============================================================================
# SegmentationEncoder
# ============================================================================

class TestSegmentationEncoder(unittest.TestCase):
    """SegmentationEncoder.encode()."""

    def setUp(self):
        self.encoder = SegmentationEncoder()

    def test_encode_leaf_default(self):
        node = SegmentationNode(state=TriangleState.DEFAULT)
        result = self.encoder.encode(node)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_encode_leaf_state_1(self):
        node = SegmentationNode(state=TriangleState.EXTRUDER_1)
        result = self.encoder.encode(node)
        self.assertIsInstance(result, str)

    def test_encode_leaf_high_state(self):
        """State >= 3 uses the extra-nibble encoding."""
        node = SegmentationNode(state=TriangleState.EXTRUDER_7)
        result = self.encoder.encode(node)
        # Should be 2 nibbles: 0xC (marker) + state-3 = 4
        self.assertEqual(len(result), 2)

    def test_encode_subdivided(self):
        """Subdivided tree produces longer hex string."""
        children = [
            SegmentationNode(state=TriangleState.EXTRUDER_1),
            SegmentationNode(state=TriangleState.EXTRUDER_2),
        ]
        tree = SegmentationNode(split_sides=1, special_side=0, children=children)
        result = self.encoder.encode(tree)
        self.assertGreater(len(result), 1)


# ============================================================================
# Round-trip: Encode → Decode
# ============================================================================

class TestRoundTrip(unittest.TestCase):
    """Full encode → decode round-trip fidelity."""

    def _round_trip(self, tree: SegmentationNode) -> SegmentationNode:
        encoder = SegmentationEncoder()
        hex_str = encoder.encode(tree)
        decoder = SegmentationDecoder()
        return decoder.decode(hex_str)

    def test_single_leaf(self):
        for state in (TriangleState.DEFAULT, TriangleState.EXTRUDER_1,
                       TriangleState.EXTRUDER_5, TriangleState.EXTRUDER_15):
            with self.subTest(state=state):
                result = self._round_trip(SegmentationNode(state=state))
                self.assertEqual(result.state, state)

    def test_two_way_split(self):
        tree = SegmentationNode(
            split_sides=1, special_side=0,
            children=[
                SegmentationNode(state=TriangleState.EXTRUDER_1),
                SegmentationNode(state=TriangleState.EXTRUDER_2),
            ],
        )
        result = self._round_trip(tree)
        self.assertEqual(result.split_sides, 1)
        self.assertEqual(len(result.children), 2)

    def test_three_way_split(self):
        tree = SegmentationNode(
            split_sides=2, special_side=1,
            children=[
                SegmentationNode(state=TriangleState.EXTRUDER_1),
                SegmentationNode(state=TriangleState.EXTRUDER_2),
                SegmentationNode(state=TriangleState.EXTRUDER_3),
            ],
        )
        result = self._round_trip(tree)
        self.assertEqual(result.split_sides, 2)
        self.assertEqual(len(result.children), 3)

    def test_four_way_split(self):
        tree = SegmentationNode(
            split_sides=3, special_side=0,
            children=[
                SegmentationNode(state=TriangleState.EXTRUDER_1),
                SegmentationNode(state=TriangleState.DEFAULT),
                SegmentationNode(state=TriangleState.EXTRUDER_2),
                SegmentationNode(state=TriangleState.EXTRUDER_3),
            ],
        )
        result = self._round_trip(tree)
        self.assertEqual(result.split_sides, 3)
        self.assertEqual(len(result.children), 4)

    def test_nested_subdivision(self):
        """A tree with a subdivided child inside a subdivided parent."""
        inner = SegmentationNode(
            split_sides=1, special_side=0,
            children=[
                SegmentationNode(state=TriangleState.EXTRUDER_1),
                SegmentationNode(state=TriangleState.EXTRUDER_2),
            ],
        )
        tree = SegmentationNode(
            split_sides=1, special_side=0,
            children=[inner, SegmentationNode(state=TriangleState.EXTRUDER_3)],
        )
        result = self._round_trip(tree)
        self.assertFalse(result.is_leaf)
        self.assertFalse(result.children[0].is_leaf)


# ============================================================================
# TriangleSubdivider
# ============================================================================

class TestTriangleSubdivider(unittest.TestCase):
    """TriangleSubdivider.subdivide()."""

    def test_leaf_no_subdivision(self):
        """A leaf node should return the original triangle unchanged."""
        tree = SegmentationNode(state=TriangleState.EXTRUDER_1)
        sub = TriangleSubdivider()
        verts, tris = sub.subdivide(
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), tree
        )
        self.assertEqual(len(tris), 1)
        self.assertEqual(tris[0].state, TriangleState.EXTRUDER_1)

    def test_one_split_produces_two_triangles(self):
        """split_sides=1 should produce 2 leaf triangles."""
        tree = SegmentationNode(
            split_sides=1, special_side=0,
            children=[
                SegmentationNode(state=TriangleState.EXTRUDER_1),
                SegmentationNode(state=TriangleState.EXTRUDER_2),
            ],
        )
        sub = TriangleSubdivider()
        verts, tris = sub.subdivide(
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), tree
        )
        self.assertEqual(len(tris), 2)
        # One midpoint vertex should be added
        self.assertEqual(len(verts), 4)

    def test_four_way_split_produces_four_triangles(self):
        """split_sides=3 should produce 4 leaf triangles."""
        tree = SegmentationNode(
            split_sides=3, special_side=0,
            children=[SegmentationNode(state=TriangleState(i)) for i in range(4)],
        )
        sub = TriangleSubdivider()
        verts, tris = sub.subdivide(
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), tree
        )
        self.assertEqual(len(tris), 4)
        # 3 midpoint vertices added to the original 3
        self.assertEqual(len(verts), 6)

    def test_midpoint_accuracy(self):
        """Midpoints should be geometric averages."""
        tree = SegmentationNode(
            split_sides=1, special_side=0,
            children=[
                SegmentationNode(state=TriangleState.DEFAULT),
                SegmentationNode(state=TriangleState.DEFAULT),
            ],
        )
        sub = TriangleSubdivider()
        verts, tris = sub.subdivide(
            (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0), tree
        )
        # The midpoint of edge (2,0,0)-(0,2,0) should be (1,1,0)
        self.assertIn((1.0, 1.0, 0.0), verts)


# ============================================================================
# Convenience functions
# ============================================================================

class TestDecodeSegmentationString(unittest.TestCase):
    """decode_segmentation_string() convenience wrapper."""

    def test_empty(self):
        self.assertIsNone(decode_segmentation_string(""))

    def test_returns_node(self):
        encoder = SegmentationEncoder()
        node = SegmentationNode(state=TriangleState.EXTRUDER_1)
        hex_str = encoder.encode(node)
        result = decode_segmentation_string(hex_str)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, SegmentationNode)


if __name__ == "__main__":
    unittest.main()
