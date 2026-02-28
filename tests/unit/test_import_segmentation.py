"""
Unit tests for import segmentation UV rendering functions.

Covers ``io_mesh_3mf.import_3mf.segmentation``:
- subdivide_in_uv_space — recursive UV subdivision from segmentation trees
- render_triangle_to_image — numpy rasterization of triangles
- _dilate_pass — morphological gap-filling
- close_gaps_in_texture — multi-pass dilation
"""

import unittest
import numpy as np

from io_mesh_3mf.common.segmentation import SegmentationNode
from io_mesh_3mf.import_3mf.segmentation import (
    subdivide_in_uv_space,
    render_triangle_to_image,
    _dilate_pass,
    close_gaps_in_texture,
)


class SubdivideInUvSpaceTests(unittest.TestCase):
    """Tests for subdivide_in_uv_space()."""

    def test_leaf_node_returns_single_triangle(self):
        """A leaf node returns the input triangle with the leaf state."""
        node = SegmentationNode()
        node.state = 2
        uv0, uv1, uv2 = (0.0, 0.0), (1.0, 0.0), (0.5, 1.0)

        result = subdivide_in_uv_space(uv0, uv1, uv2, node)

        self.assertEqual(len(result), 1)
        tri_uv0, tri_uv1, tri_uv2, state = result[0]
        self.assertEqual(state, 2)
        self.assertAlmostEqual(tri_uv0[0], 0.0)
        self.assertAlmostEqual(tri_uv0[1], 0.0)
        self.assertAlmostEqual(tri_uv1[0], 1.0)
        self.assertAlmostEqual(tri_uv1[1], 0.0)

    def test_none_node_returns_empty(self):
        """A None node returns empty list."""
        result = subdivide_in_uv_space((0, 0), (1, 0), (0.5, 1), None)
        self.assertEqual(len(result), 0)

    def test_split_sides_1_produces_two_triangles(self):
        """A node with split_sides=1 produces 2 child triangles."""
        child1 = SegmentationNode()
        child1.state = 1
        child2 = SegmentationNode()
        child2.state = 2

        parent = SegmentationNode()
        parent.split_sides = 1
        parent.special_side = 0
        parent.children = [child1, child2]

        uv0, uv1, uv2 = (0.0, 0.0), (1.0, 0.0), (0.5, 1.0)
        result = subdivide_in_uv_space(uv0, uv1, uv2, parent)

        self.assertEqual(len(result), 2)
        states = {r[3] for r in result}
        self.assertEqual(states, {1, 2})

    def test_split_sides_2_produces_three_triangles(self):
        """A node with split_sides=2 produces 3 child triangles."""
        children = []
        for s in [1, 2, 3]:
            child = SegmentationNode()
            child.state = s
            children.append(child)

        parent = SegmentationNode()
        parent.split_sides = 2
        parent.special_side = 0
        parent.children = children

        uv0, uv1, uv2 = (0.0, 0.0), (1.0, 0.0), (0.5, 1.0)
        result = subdivide_in_uv_space(uv0, uv1, uv2, parent)

        self.assertEqual(len(result), 3)
        states = {r[3] for r in result}
        self.assertEqual(states, {1, 2, 3})

    def test_split_sides_3_produces_four_triangles(self):
        """A node with split_sides=3 produces 4 child triangles."""
        children = []
        for s in [1, 2, 3, 4]:
            child = SegmentationNode()
            child.state = s
            children.append(child)

        parent = SegmentationNode()
        parent.split_sides = 3
        parent.special_side = 0
        parent.children = children

        uv0, uv1, uv2 = (0.0, 0.0), (1.0, 0.0), (0.5, 1.0)
        result = subdivide_in_uv_space(uv0, uv1, uv2, parent)

        self.assertEqual(len(result), 4)
        states = {r[3] for r in result}
        self.assertEqual(states, {1, 2, 3, 4})

    def test_special_side_rotates_vertices(self):
        """special_side > 0 rotates the vertex assignment."""
        child1 = SegmentationNode()
        child1.state = 1
        child2 = SegmentationNode()
        child2.state = 2

        # With special_side=0
        parent0 = SegmentationNode()
        parent0.split_sides = 1
        parent0.special_side = 0
        parent0.children = [child1, child2]

        result0 = subdivide_in_uv_space(
            (0.0, 0.0), (1.0, 0.0), (0.5, 1.0), parent0
        )

        # With special_side=1 — should produce different UV coords
        child3 = SegmentationNode()
        child3.state = 1
        child4 = SegmentationNode()
        child4.state = 2

        parent1 = SegmentationNode()
        parent1.split_sides = 1
        parent1.special_side = 1
        parent1.children = [child3, child4]

        result1 = subdivide_in_uv_space(
            (0.0, 0.0), (1.0, 0.0), (0.5, 1.0), parent1
        )

        # The UV coordinates should differ due to vertex rotation
        self.assertEqual(len(result0), len(result1))
        # At least one coordinate should differ
        coords_differ = False
        for r0, r1 in zip(result0, result1):
            if r0[0] != r1[0] or r0[1] != r1[1] or r0[2] != r1[2]:
                coords_differ = True
                break
        self.assertTrue(coords_differ,
                        "Different special_side should produce different UV coords")

    def test_recursive_subdivision(self):
        """Nested subdivision produces correct number of leaf triangles."""
        # Create a 2-level tree: parent splits into 2, one child splits into 2
        grandchild1 = SegmentationNode()
        grandchild1.state = 1
        grandchild2 = SegmentationNode()
        grandchild2.state = 2

        child_with_children = SegmentationNode()
        child_with_children.split_sides = 1
        child_with_children.special_side = 0
        child_with_children.children = [grandchild1, grandchild2]

        child_leaf = SegmentationNode()
        child_leaf.state = 3

        parent = SegmentationNode()
        parent.split_sides = 1
        parent.special_side = 0
        parent.children = [child_with_children, child_leaf]

        result = subdivide_in_uv_space(
            (0.0, 0.0), (1.0, 0.0), (0.5, 1.0), parent
        )

        # Should produce 3 leaf triangles: 2 from first child + 1 from second
        self.assertEqual(len(result), 3)


class RenderTriangleToImageTests(unittest.TestCase):
    """Tests for render_triangle_to_image()."""

    def _make_buffer(self, size=64):
        """Create a transparent RGBA buffer."""
        return np.zeros((size, size, 4), dtype=np.float32)

    def test_large_triangle_paints_pixels(self):
        """A large triangle covering the image paints multiple pixels."""
        buf = self._make_buffer(32)
        color = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)

        # Triangle covers most of the image
        render_triangle_to_image(
            buf, 32, 32,
            (0.1, 0.1), (0.9, 0.1), (0.5, 0.9),
            color,
        )

        # Many pixels should now be red
        painted = np.sum(buf[:, :, 3] > 0.5)
        self.assertGreater(painted, 50, "Large triangle should paint many pixels")

    def test_degenerate_triangle_skipped(self):
        """A degenerate (zero-area) triangle paints nothing."""
        buf = self._make_buffer(32)
        color = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)

        # Collinear points
        render_triangle_to_image(
            buf, 32, 32,
            (0.1, 0.5), (0.5, 0.5), (0.9, 0.5),
            color,
        )

        painted = np.sum(buf[:, :, 3] > 0.5)
        self.assertEqual(painted, 0, "Degenerate triangle should paint nothing")

    def test_tiny_triangle_centroid_fallback(self):
        """A sub-pixel triangle still paints at least one pixel (centroid)."""
        buf = self._make_buffer(64)
        color = np.array([0.0, 1.0, 0.0, 1.0], dtype=np.float32)

        # Tiny triangle in a small area
        render_triangle_to_image(
            buf, 64, 64,
            (0.50, 0.50), (0.51, 0.50), (0.505, 0.51),
            color,
        )

        painted = np.sum(buf[:, :, 3] > 0.5)
        self.assertGreaterEqual(painted, 1,
                                "Tiny triangle should paint at least 1 pixel via centroid")

    def test_out_of_bounds_triangle_safe(self):
        """Triangle outside the image does not crash or corrupt."""
        buf = self._make_buffer(16)
        color = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)

        # Entirely outside the image
        render_triangle_to_image(
            buf, 16, 16,
            (-1.0, -1.0), (-0.5, -1.0), (-0.75, -0.5),
            color,
        )

        painted = np.sum(buf[:, :, 3] > 0.5)
        self.assertEqual(painted, 0, "Out-of-bounds triangle should paint nothing")

    def test_expand_px_paints_wider_region(self):
        """expand_px > 0 paints a wider region than the tight triangle."""
        size = 64
        color = np.array([1.0, 0.0, 0.0, 1.0], dtype=np.float32)

        # Tight triangle
        buf_tight = self._make_buffer(size)
        render_triangle_to_image(
            buf_tight, size, size,
            (0.3, 0.3), (0.7, 0.3), (0.5, 0.7),
            color, expand_px=0.0,
        )

        # Expanded triangle
        buf_expanded = self._make_buffer(size)
        render_triangle_to_image(
            buf_expanded, size, size,
            (0.3, 0.3), (0.7, 0.3), (0.5, 0.7),
            color, expand_px=2.0,
        )

        tight_count = np.sum(buf_tight[:, :, 3] > 0.5)
        expanded_count = np.sum(buf_expanded[:, :, 3] > 0.5)

        self.assertGreater(expanded_count, tight_count,
                           "Expanded triangle should paint more pixels")

    def test_color_values_correct(self):
        """Painted pixels have the exact specified color."""
        buf = self._make_buffer(32)
        color = np.array([0.2, 0.4, 0.6, 1.0], dtype=np.float32)

        render_triangle_to_image(
            buf, 32, 32,
            (0.1, 0.1), (0.9, 0.1), (0.5, 0.9),
            color,
        )

        # Find a painted pixel
        painted_mask = buf[:, :, 3] > 0.5
        self.assertTrue(np.any(painted_mask))

        painted_pixels = buf[painted_mask]
        # All painted pixels should have the exact color
        for px in painted_pixels[:5]:
            np.testing.assert_array_almost_equal(px, color, decimal=5)


class DilatePassTests(unittest.TestCase):
    """Tests for _dilate_pass()."""

    def test_no_transparent_pixels_unchanged(self):
        """A fully opaque buffer is returned unchanged."""
        buf = np.ones((8, 8, 4), dtype=np.float32)
        result = _dilate_pass(buf, min_neighbors=1)
        np.testing.assert_array_equal(result, buf)

    def test_single_transparent_pixel_filled(self):
        """A transparent pixel surrounded by opaque neighbors gets filled."""
        buf = np.ones((8, 8, 4), dtype=np.float32)
        buf[4, 4] = [0, 0, 0, 0]  # create transparent hole

        result = _dilate_pass(buf, min_neighbors=1)

        # The hole should be filled
        self.assertGreater(result[4, 4, 3], 0.5,
                           "Transparent pixel with opaque neighbors should be filled")

    def test_isolated_transparent_pixel_not_filled_min2(self):
        """A transparent pixel with only 1 neighbor is not filled when min_neighbors=2."""
        buf = np.zeros((8, 8, 4), dtype=np.float32)
        # Place a single opaque pixel
        buf[3, 3] = [1, 0, 0, 1]

        result = _dilate_pass(buf, min_neighbors=2)

        # Neighbors of the single pixel have only 1 opaque neighbor
        # so they should NOT be filled with min_neighbors=2
        self.assertLess(result[3, 4, 3], 0.5,
                        "Pixel with only 1 opaque neighbor shouldn't be filled at min=2")

    def test_dilate_preserves_opaque_pixels(self):
        """Existing opaque pixels are not altered during dilation."""
        buf = np.zeros((8, 8, 4), dtype=np.float32)
        buf[2:6, 2:6] = [1, 0.5, 0, 1]  # opaque red block

        original_opaque = buf.copy()
        result = _dilate_pass(buf, min_neighbors=1)

        # Original opaque pixels should be unchanged
        opaque_mask = original_opaque[:, :, 3] > 0.5
        np.testing.assert_array_almost_equal(
            result[opaque_mask], original_opaque[opaque_mask]
        )


class CloseGapsInTextureTests(unittest.TestCase):
    """Tests for close_gaps_in_texture()."""

    def test_smart_uv_two_passes(self):
        """SMART mode performs limited dilation (fewer passes)."""
        buf = np.zeros((16, 16, 4), dtype=np.float32)
        # Create a small opaque region
        buf[6:10, 6:10] = [1, 0, 0, 1]

        result = close_gaps_in_texture(buf, 16, 16, uv_method="SMART")

        # Should have some dilation but not too aggressive
        opaque_count = np.sum(result[:, :, 3] > 0.5)
        self.assertGreater(opaque_count, 16, "Should have dilated somewhat")

    def test_lightmap_more_dilation(self):
        """LIGHTMAP mode performs more aggressive dilation than SMART."""
        # Create identical starting buffers
        buf_smart = np.zeros((16, 16, 4), dtype=np.float32)
        buf_smart[7:9, 7:9] = [1, 0, 0, 1]

        buf_lightmap = buf_smart.copy()

        result_smart = close_gaps_in_texture(buf_smart, 16, 16, uv_method="SMART")
        result_lightmap = close_gaps_in_texture(
            buf_lightmap, 16, 16, uv_method="LIGHTMAP"
        )

        smart_count = np.sum(result_smart[:, :, 3] > 0.5)
        lightmap_count = np.sum(result_lightmap[:, :, 3] > 0.5)

        self.assertGreater(lightmap_count, smart_count,
                           "LIGHTMAP should dilate more aggressively than SMART")

    def test_fully_opaque_unchanged(self):
        """A fully opaque buffer is returned unchanged regardless of mode."""
        buf = np.ones((8, 8, 4), dtype=np.float32)
        result = close_gaps_in_texture(buf.copy(), 8, 8, uv_method="SMART")
        np.testing.assert_array_equal(result, buf)


if __name__ == "__main__":
    unittest.main()
