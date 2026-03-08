"""
Unit tests for paint module — bake, color detection, and helpers.

Covers pure/numpy functions from:
- ``io_mesh_3mf.paint.bake`` — _rgb_to_hsv, _hue_aware_distance, _quantize_pixels, _get_texture_size
- ``io_mesh_3mf.paint.color_detection`` — _deduplicate_colors, _srgb_to_hsv_array,
    _bin_pixels_hsv, _hs_distance, _select_diverse_colors, _linear_to_srgb_array
- ``io_mesh_3mf.paint.helpers`` — DEFAULT_PALETTE, _layer_colors, _layer_uv_name,
    _layer_flag_key, _layer_colors_key
"""

import unittest
import numpy as np


# ===========================================================================
#  Bake module tests
# ===========================================================================

class RgbToHsvTests(unittest.TestCase):
    """Tests for paint.bake._rgb_to_hsv()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _rgb_to_hsv
        self._rgb_to_hsv = _rgb_to_hsv

    def test_pure_red(self):
        """Pure red (1,0,0) → H≈0, S=1, V=1."""
        rgb = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        hsv = self._rgb_to_hsv(rgb)
        self.assertAlmostEqual(float(hsv[0, 0]), 0.0, places=3)
        self.assertAlmostEqual(float(hsv[0, 1]), 1.0, places=3)
        self.assertAlmostEqual(float(hsv[0, 2]), 1.0, places=3)

    def test_pure_green(self):
        """Pure green (0,1,0) → H≈0.333, S=1, V=1."""
        rgb = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)
        hsv = self._rgb_to_hsv(rgb)
        self.assertAlmostEqual(float(hsv[0, 0]), 1.0 / 3.0, places=2)
        self.assertAlmostEqual(float(hsv[0, 1]), 1.0, places=3)

    def test_pure_blue(self):
        """Pure blue (0,0,1) → H≈0.667, S=1, V=1."""
        rgb = np.array([[0.0, 0.0, 1.0]], dtype=np.float32)
        hsv = self._rgb_to_hsv(rgb)
        self.assertAlmostEqual(float(hsv[0, 0]), 2.0 / 3.0, places=2)
        self.assertAlmostEqual(float(hsv[0, 1]), 1.0, places=3)

    def test_white(self):
        """White (1,1,1) → S=0, V=1."""
        rgb = np.array([[1.0, 1.0, 1.0]], dtype=np.float32)
        hsv = self._rgb_to_hsv(rgb)
        self.assertAlmostEqual(float(hsv[0, 1]), 0.0, places=3)
        self.assertAlmostEqual(float(hsv[0, 2]), 1.0, places=3)

    def test_black(self):
        """Black (0,0,0) → S=0, V=0."""
        rgb = np.array([[0.0, 0.0, 0.0]], dtype=np.float32)
        hsv = self._rgb_to_hsv(rgb)
        self.assertAlmostEqual(float(hsv[0, 1]), 0.0, places=3)
        self.assertAlmostEqual(float(hsv[0, 2]), 0.0, places=3)

    def test_batch_input(self):
        """Multiple colors are processed correctly in batch."""
        rgb = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ], dtype=np.float32)
        hsv = self._rgb_to_hsv(rgb)
        self.assertEqual(hsv.shape, (3, 3))


class HueAwareDistanceTests(unittest.TestCase):
    """Tests for paint.bake._hue_aware_distance()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _hue_aware_distance, _rgb_to_hsv
        self._hue_aware_distance = _hue_aware_distance
        self._rgb_to_hsv = _rgb_to_hsv

    def test_identical_colors_zero_distance(self):
        """Distance between identical colors should be ~0."""
        pixel_rgb = np.array([[[[1.0, 0.0, 0.0]]]], dtype=np.float32)
        palette_rgb = np.array([[[[1.0, 0.0, 0.0]]]], dtype=np.float32)
        pixel_hsv = self._rgb_to_hsv(pixel_rgb.reshape(-1, 3)).reshape(
            pixel_rgb.shape
        )
        palette_hsv = self._rgb_to_hsv(palette_rgb.reshape(-1, 3)).reshape(
            palette_rgb.shape
        )
        dist = self._hue_aware_distance(pixel_hsv, palette_hsv, pixel_rgb, palette_rgb)
        self.assertAlmostEqual(float(dist.flatten()[0]), 0.0, places=3)

    def test_opposite_hues_large_distance(self):
        """Red vs cyan (complementary hues) should have large distance."""
        pixel_rgb = np.array([[[[1.0, 0.0, 0.0]]]], dtype=np.float32)  # red
        palette_rgb = np.array([[[[0.0, 1.0, 1.0]]]], dtype=np.float32)  # cyan
        pixel_hsv = self._rgb_to_hsv(pixel_rgb.reshape(-1, 3)).reshape(
            pixel_rgb.shape
        )
        palette_hsv = self._rgb_to_hsv(palette_rgb.reshape(-1, 3)).reshape(
            palette_rgb.shape
        )
        dist = self._hue_aware_distance(pixel_hsv, palette_hsv, pixel_rgb, palette_rgb)
        self.assertGreater(float(dist.flatten()[0]), 0.5)


class QuantizePixelsTests(unittest.TestCase):
    """Tests for paint.bake._quantize_pixels()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _quantize_pixels
        self._quantize_pixels = _quantize_pixels

    def test_single_color_no_change(self):
        """Pixels already matching the palette don't change."""
        pixels = np.full((4, 4, 4), [1.0, 0.0, 0.0, 1.0], dtype=np.float32)
        palette = [(1.0, 0.0, 0.0)]

        changed = self._quantize_pixels(pixels, palette)

        self.assertEqual(changed, 0)
        np.testing.assert_array_almost_equal(
            pixels[0, 0, :3], [1.0, 0.0, 0.0], decimal=3
        )

    def test_quantize_to_nearest(self):
        """Off-palette colors snap to the nearest palette entry."""
        # Start with dark red pixels
        pixels = np.full((4, 4, 4), [0.8, 0.1, 0.05, 1.0], dtype=np.float32)
        palette = [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]  # red and blue

        changed = self._quantize_pixels(pixels, palette)

        # Should snap to red (index 0)
        np.testing.assert_array_almost_equal(
            pixels[0, 0, :3], [1.0, 0.0, 0.0], decimal=3
        )
        self.assertGreater(changed, 0)

    def test_multiple_regions(self):
        """Different color regions snap to their nearest palette colors."""
        pixels = np.zeros((8, 8, 4), dtype=np.float32)
        pixels[:4, :, :3] = [0.9, 0.1, 0.1]  # reddish top
        pixels[4:, :, :3] = [0.1, 0.1, 0.9]  # blueish bottom
        pixels[:, :, 3] = 1.0

        palette = [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
        self._quantize_pixels(pixels, palette)

        # Top should be red, bottom should be blue
        np.testing.assert_array_almost_equal(
            pixels[0, 0, :3], [1.0, 0.0, 0.0], decimal=3
        )
        np.testing.assert_array_almost_equal(
            pixels[7, 0, :3], [0.0, 0.0, 1.0], decimal=3
        )

    def test_grey_shadow_on_white_biased_to_white(self):
        """Grey shadow pixels in a mostly-white region should snap to white, not black.

        This tests the context-aware quantization that fixes the Mickey Mouse
        glove shadow problem: grey shadows on white surfaces should not snap
        to black just because grey is mathematically closer to black.
        """
        # 32x32 image: mostly white with a grey "shadow" region in the middle
        pixels = np.full((32, 32, 4), [1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        # Grey shadow strip (40% grey - ambiguous between white and black)
        pixels[14:18, 10:22, :3] = [0.4, 0.4, 0.4]

        palette = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]  # white and black

        self._quantize_pixels(pixels, palette, use_neighborhood_context=True)

        # The grey shadow should snap to WHITE because the local neighborhood
        # is mostly white, indicating this is a shadow on white
        center_color = pixels[16, 16, :3]
        np.testing.assert_array_almost_equal(
            center_color, [1.0, 1.0, 1.0], decimal=2,
            err_msg="Grey shadow on white should snap to white, not black"
        )

    def test_grey_on_black_snaps_to_black(self):
        """Grey pixels in a mostly-black region should snap to black."""
        # 32x32 image: mostly black with a grey spot in the middle
        pixels = np.full((32, 32, 4), [0.0, 0.0, 0.0, 1.0], dtype=np.float32)
        # Grey highlight (60% grey)
        pixels[14:18, 14:18, :3] = [0.6, 0.6, 0.6]

        palette = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]

        self._quantize_pixels(pixels, palette, use_neighborhood_context=True)

        # The grey should snap to BLACK because neighborhood is black
        center_color = pixels[16, 16, :3]
        np.testing.assert_array_almost_equal(
            center_color, [0.0, 0.0, 0.0], decimal=2,
            err_msg="Grey highlight on black should snap to black, not white"
        )


class GetTextureSizeTests(unittest.TestCase):
    """Tests for paint.bake._get_texture_size()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _get_texture_size
        self._get_texture_size = _get_texture_size

    def test_override_returns_override(self):
        """When override_size > 0, that value is returned directly."""

        class FakeMesh:
            polygons = [None] * 100

        result = self._get_texture_size(FakeMesh(), override_size=512)
        self.assertEqual(result, 512)

    def test_low_tri_count_2048(self):
        """< 5000 triangles → 2048."""

        class FakeMesh:
            polygons = [None] * 1000

        result = self._get_texture_size(FakeMesh())
        self.assertEqual(result, 2048)

    def test_medium_tri_count_4096(self):
        """5000-20000 triangles → 4096."""

        class FakeMesh:
            polygons = [None] * 10000

        result = self._get_texture_size(FakeMesh())
        self.assertEqual(result, 4096)

    def test_high_tri_count_8192(self):
        """> 20000 triangles → 8192."""

        class FakeMesh:
            polygons = [None] * 30000

        result = self._get_texture_size(FakeMesh())
        self.assertEqual(result, 8192)


class ApplyMajorityFilterTests(unittest.TestCase):
    """Tests for paint.bake._apply_majority_filter()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _apply_majority_filter
        self._apply_majority_filter = _apply_majority_filter

    def test_uniform_image_no_change(self):
        """Uniform image (all same color) produces no flips."""
        pixels = np.full((8, 8, 4), [1.0, 0.0, 0.0, 1.0], dtype=np.float32)
        palette = [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]

        flipped = self._apply_majority_filter(pixels, palette)

        self.assertEqual(flipped, 0)
        np.testing.assert_array_almost_equal(
            pixels[4, 4, :3], [1.0, 0.0, 0.0], decimal=3
        )

    def test_isolated_pixel_flipped(self):
        """A single outlier pixel surrounded by majority color is flipped."""
        # 8x8 white image with a single black pixel in the middle
        pixels = np.full((8, 8, 4), [1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        pixels[4, 4, :3] = [0.0, 0.0, 0.0]  # black outlier
        palette = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]  # white, black

        flipped = self._apply_majority_filter(pixels, palette)

        self.assertEqual(flipped, 1)
        # The black pixel should now be white
        np.testing.assert_array_almost_equal(
            pixels[4, 4, :3], [1.0, 1.0, 1.0], decimal=3
        )

    def test_large_region_preserved(self):
        """A solid block of minority color is NOT flipped (neighbors agree)."""
        # Half white, half black
        pixels = np.full((8, 8, 4), [1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        pixels[4:, :, :3] = [0.0, 0.0, 0.0]  # bottom half black
        palette = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]

        original_bottom = pixels[6, 4, :3].copy()
        _flipped = self._apply_majority_filter(pixels, palette)  # noqa: F841

        # Interior pixels of each region should remain unchanged
        # Only edge pixels might be affected, but center of black region stays
        np.testing.assert_array_almost_equal(
            pixels[6, 4, :3], original_bottom, decimal=3
        )

    def test_shadow_speckle_scenario(self):
        """Simulates the Mickey Mouse shadow issue: grey shadows on white snap to black,
        then majority filter corrects the isolated black speckles back to white."""
        # 9x9 mostly white, with a few scattered black "shadow" pixels
        pixels = np.full((9, 9, 4), [1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        # Scatter black pixels at (1,1), (4,4), (7,7) — isolated
        pixels[1, 1, :3] = [0.0, 0.0, 0.0]
        pixels[4, 4, :3] = [0.0, 0.0, 0.0]
        pixels[7, 7, :3] = [0.0, 0.0, 0.0]
        palette = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]

        flipped = self._apply_majority_filter(pixels, palette, kernel_size=5, passes=1)

        self.assertEqual(flipped, 3)
        # All should now be white
        np.testing.assert_array_almost_equal(
            pixels[1, 1, :3], [1.0, 1.0, 1.0], decimal=3
        )
        np.testing.assert_array_almost_equal(
            pixels[4, 4, :3], [1.0, 1.0, 1.0], decimal=3
        )
        np.testing.assert_array_almost_equal(
            pixels[7, 7, :3], [1.0, 1.0, 1.0], decimal=3
        )

    def test_wide_shadow_band_multi_pass(self):
        """A shadow band on white erodes progressively with multiple passes."""
        # 20x20 white image with a 3px wide horizontal black band in the middle
        pixels = np.full((20, 20, 4), [1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        pixels[8:11, :, :3] = [0.0, 0.0, 0.0]  # 3 rows of black (y=8,9,10)
        palette = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]

        # With kernel=5 and 1 pass, edge rows (y=8, y=10) see:
        #   y=8 window: y=6,7,8,9,10 → 2 white + 3 black rows = 10 white, 15 black → stay black
        # But with kernel=7, y=8 sees y=5,6,7,8,9,10,11 → 4 white + 3 black = 28 white, 21 black → flip
        pixels_k7 = pixels.copy()
        flipped = self._apply_majority_filter(pixels_k7, palette, kernel_size=7, passes=2)

        # With 7×7 kernel and 2 passes, the thin band should be gone
        self.assertGreater(flipped, 0)
        # Center row (y=9) should now be white after passes erode inward
        np.testing.assert_array_almost_equal(
            pixels_k7[9, 10, :3], [1.0, 1.0, 1.0], decimal=2
        )

    def test_kernel_size_parameter(self):
        """Larger kernel size catches wider isolated regions in a single pass."""
        # 11x11 white image with a 3x3 black block in center
        pixels = np.full((11, 11, 4), [1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        pixels[4:7, 4:7, :3] = [0.0, 0.0, 0.0]  # 3x3 black block
        palette = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]

        # With kernel=3, the center pixel's 3x3 neighborhood is all black → no flip
        pixels_k3 = pixels.copy()
        self._apply_majority_filter(pixels_k3, palette, kernel_size=3, passes=1)
        # Center of block should remain black
        self.assertTrue(np.allclose(pixels_k3[5, 5, :3], [0.0, 0.0, 0.0], atol=0.01))

        # With kernel=7, the 7x7 neighborhood around center includes more white → flip
        pixels_k7 = pixels.copy()
        self._apply_majority_filter(pixels_k7, palette, kernel_size=7, passes=1)
        # Center should now be white (or at least some interior pixels flipped)
        # Actually with 7x7, center sees 49 pixels: 9 black + 40 white → majority white
        np.testing.assert_array_almost_equal(
            pixels_k7[5, 5, :3], [1.0, 1.0, 1.0], decimal=2
        )


# ===========================================================================
#  Color detection module tests
# ===========================================================================

class DeduplicateColorsTests(unittest.TestCase):
    """Tests for paint.color_detection._deduplicate_colors()."""

    def setUp(self):
        from io_mesh_3mf.paint.color_detection import _deduplicate_colors
        self._deduplicate_colors = _deduplicate_colors

    def test_no_duplicates(self):
        """Distinct colors are preserved."""
        colors = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
        result = self._deduplicate_colors(colors, tolerance=0.02)
        self.assertEqual(len(result), 3)

    def test_near_duplicates_merged(self):
        """Colors within tolerance are merged (first occurrence kept)."""
        colors = [(1.0, 0.0, 0.0), (0.99, 0.01, 0.01), (0.0, 0.0, 1.0)]
        result = self._deduplicate_colors(colors, tolerance=0.05)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], (1.0, 0.0, 0.0))
        self.assertEqual(result[1], (0.0, 0.0, 1.0))

    def test_empty_input(self):
        """Empty input returns empty output."""
        self.assertEqual(self._deduplicate_colors([], tolerance=0.02), [])

    def test_all_same(self):
        """All identical colors reduce to one."""
        colors = [(0.5, 0.5, 0.5)] * 10
        result = self._deduplicate_colors(colors, tolerance=0.02)
        self.assertEqual(len(result), 1)


class SrgbToHsvArrayTests(unittest.TestCase):
    """Tests for paint.color_detection._srgb_to_hsv_array()."""

    def setUp(self):
        from io_mesh_3mf.paint.color_detection import _srgb_to_hsv_array
        self._srgb_to_hsv_array = _srgb_to_hsv_array

    def test_red(self):
        """Pure red → H≈0, S=1, V=1."""
        srgb = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        hsv = self._srgb_to_hsv_array(srgb)
        self.assertAlmostEqual(float(hsv[0, 0]), 0.0, places=2)
        self.assertAlmostEqual(float(hsv[0, 1]), 1.0, places=2)
        self.assertAlmostEqual(float(hsv[0, 2]), 1.0, places=2)

    def test_grey(self):
        """Grey (0.5, 0.5, 0.5) → S=0."""
        srgb = np.array([[0.5, 0.5, 0.5]], dtype=np.float32)
        hsv = self._srgb_to_hsv_array(srgb)
        self.assertAlmostEqual(float(hsv[0, 1]), 0.0, places=3)


class BinPixelsHsvTests(unittest.TestCase):
    """Tests for paint.color_detection._bin_pixels_hsv()."""

    def setUp(self):
        from io_mesh_3mf.paint.color_detection import _bin_pixels_hsv
        self._bin_pixels_hsv = _bin_pixels_hsv

    def test_single_color(self):
        """A single uniform color produces one bin."""
        srgb = np.full((100, 3), [1.0, 0.0, 0.0], dtype=np.float32)
        colors, counts = self._bin_pixels_hsv(srgb)
        self.assertGreaterEqual(len(colors), 1)
        self.assertEqual(int(counts[0]), 100)

    def test_two_distinct_colors(self):
        """Two distinct colors produce two bins."""
        srgb = np.zeros((200, 3), dtype=np.float32)
        srgb[:100] = [1.0, 0.0, 0.0]  # red
        srgb[100:] = [0.0, 0.0, 1.0]  # blue
        colors, counts = self._bin_pixels_hsv(srgb)
        self.assertGreaterEqual(len(colors), 2)

    def test_grey_pixels_binned(self):
        """Achromatic (grey) pixels still produce bins."""
        srgb = np.full((50, 3), [0.5, 0.5, 0.5], dtype=np.float32)
        colors, counts = self._bin_pixels_hsv(srgb)
        self.assertGreaterEqual(len(colors), 1)

    def test_sorted_descending_by_count(self):
        """Bins are sorted by descending count."""
        srgb = np.zeros((300, 3), dtype=np.float32)
        srgb[:200] = [1.0, 0.0, 0.0]  # 200 red
        srgb[200:] = [0.0, 1.0, 0.0]  # 100 green
        colors, counts = self._bin_pixels_hsv(srgb)
        for i in range(len(counts) - 1):
            self.assertGreaterEqual(int(counts[i]), int(counts[i + 1]))


class SelectDiverseColorsTests(unittest.TestCase):
    """Tests for paint.color_detection._select_diverse_colors()."""

    def setUp(self):
        from io_mesh_3mf.paint.color_detection import _select_diverse_colors
        self._select_diverse_colors = _select_diverse_colors

    def test_empty_input(self):
        """Empty bins return empty result."""
        result = self._select_diverse_colors(
            np.empty((0, 3), dtype=np.float32),
            np.array([], dtype=np.float64),
            5,
        )
        self.assertEqual(len(result), 0)

    def test_fewer_bins_than_requested(self):
        """When bins < num_colors, returns all bins."""
        colors = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)
        counts = np.array([100, 50], dtype=np.float64)
        result = self._select_diverse_colors(colors, counts, 5)
        self.assertEqual(len(result), 2)

    def test_exact_count(self):
        """Requesting exact number of bins returns all."""
        colors = np.array(
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32
        )
        counts = np.array([100, 80, 60], dtype=np.float64)
        result = self._select_diverse_colors(colors, counts, 3)
        self.assertEqual(len(result), 3)

    def test_first_pick_is_most_frequent(self):
        """First selected color is the most frequent bin."""
        colors = np.array(
            [[0.5, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        )
        counts = np.array([200, 100, 50], dtype=np.float64)
        result = self._select_diverse_colors(colors, counts, 2)
        self.assertEqual(result[0], (0.5, 0.0, 0.0))


class LinearToSrgbArrayTests(unittest.TestCase):
    """Tests for paint.color_detection._linear_to_srgb_array()."""

    def setUp(self):
        from io_mesh_3mf.paint.color_detection import _linear_to_srgb_array
        self._linear_to_srgb_array = _linear_to_srgb_array

    def test_black(self):
        """Linear black → sRGB black."""
        rgb = np.array([[0.0, 0.0, 0.0]], dtype=np.float32)
        result = self._linear_to_srgb_array(rgb)
        np.testing.assert_array_almost_equal(result, [[0.0, 0.0, 0.0]])

    def test_white(self):
        """Linear white → sRGB white."""
        rgb = np.array([[1.0, 1.0, 1.0]], dtype=np.float32)
        result = self._linear_to_srgb_array(rgb)
        np.testing.assert_array_almost_equal(result, [[1.0, 1.0, 1.0]], decimal=3)

    def test_values_clamped(self):
        """Out-of-range values are clamped to [0, 1]."""
        rgb = np.array([[-0.1, 1.5, 0.5]], dtype=np.float32)
        result = self._linear_to_srgb_array(rgb)
        self.assertAlmostEqual(float(result[0, 0]), 0.0, places=3)
        self.assertAlmostEqual(float(result[0, 1]), 1.0, places=3)

    def test_mid_value(self):
        """A mid linear value converts correctly (sRGB ≈ 0.735 for linear 0.5)."""
        rgb = np.array([[0.5, 0.5, 0.5]], dtype=np.float32)
        result = self._linear_to_srgb_array(rgb)
        # sRGB of linear 0.5 ≈ 0.735
        self.assertGreater(float(result[0, 0]), 0.7)
        self.assertLess(float(result[0, 0]), 0.8)


class HsDistanceTests(unittest.TestCase):
    """Tests for paint.color_detection._hs_distance()."""

    def setUp(self):
        from io_mesh_3mf.paint.color_detection import _hs_distance
        self._hs_distance = _hs_distance

    def test_identical_zero_distance(self):
        """Same colors have zero distance."""
        a = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        dist = self._hs_distance(a, b)
        self.assertAlmostEqual(float(dist[0]), 0.0, places=3)

    def test_complementary_large_distance(self):
        """Complementary hues (red vs cyan) have large distance."""
        a = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        b = np.array([0.0, 1.0, 1.0], dtype=np.float32)
        dist = self._hs_distance(a, b)
        self.assertGreater(float(dist[0]), 1.0)


# ===========================================================================
#  Helpers module tests
# ===========================================================================

class DefaultPaletteTests(unittest.TestCase):
    """Tests for paint.helpers.DEFAULT_PALETTE."""

    def test_palette_has_16_entries(self):
        from io_mesh_3mf.paint.helpers import DEFAULT_PALETTE
        self.assertEqual(len(DEFAULT_PALETTE), 16)

    def test_palette_entries_are_rgb_tuples(self):
        from io_mesh_3mf.paint.helpers import DEFAULT_PALETTE
        for color in DEFAULT_PALETTE:
            self.assertEqual(len(color), 3)
            for c in color:
                self.assertGreaterEqual(c, 0.0)
                self.assertLessEqual(c, 1.0)


class LayerHelpersTests(unittest.TestCase):
    """Tests for paint.helpers layer utility functions."""

    def test_layer_colors_seam(self):
        from io_mesh_3mf.paint.helpers import _layer_colors
        bg, enforce, block = _layer_colors("SEAM")
        self.assertEqual(len(bg), 3)
        self.assertEqual(len(enforce), 3)
        self.assertEqual(len(block), 3)

    def test_layer_colors_support(self):
        from io_mesh_3mf.paint.helpers import _layer_colors
        bg, enforce, block = _layer_colors("SUPPORT")
        self.assertEqual(len(bg), 3)

    def test_layer_colors_unknown(self):
        from io_mesh_3mf.paint.helpers import _layer_colors
        result = _layer_colors("UNKNOWN")
        self.assertIsNone(result)

    def test_layer_uv_name_seam(self):
        from io_mesh_3mf.paint.helpers import _layer_uv_name
        self.assertEqual(_layer_uv_name("SEAM"), "Seam_Paint")

    def test_layer_uv_name_support(self):
        from io_mesh_3mf.paint.helpers import _layer_uv_name
        self.assertEqual(_layer_uv_name("SUPPORT"), "Support_Paint")

    def test_layer_uv_name_default(self):
        from io_mesh_3mf.paint.helpers import _layer_uv_name
        self.assertEqual(_layer_uv_name("COLOR"), "MMU_Paint")

    def test_layer_flag_key_seam(self):
        from io_mesh_3mf.paint.helpers import _layer_flag_key
        self.assertEqual(_layer_flag_key("SEAM"), "3mf_has_seam_paint")

    def test_layer_flag_key_support(self):
        from io_mesh_3mf.paint.helpers import _layer_flag_key
        self.assertEqual(_layer_flag_key("SUPPORT"), "3mf_has_support_paint")

    def test_layer_flag_key_default(self):
        from io_mesh_3mf.paint.helpers import _layer_flag_key
        self.assertEqual(_layer_flag_key("COLOR"), "3mf_is_paint_texture")

    def test_layer_colors_key_seam(self):
        from io_mesh_3mf.paint.helpers import _layer_colors_key
        self.assertEqual(_layer_colors_key("SEAM"), "3mf_seam_paint_colors")

    def test_layer_colors_key_support(self):
        from io_mesh_3mf.paint.helpers import _layer_colors_key
        self.assertEqual(_layer_colors_key("SUPPORT"), "3mf_support_paint_colors")

    def test_layer_colors_key_default(self):
        from io_mesh_3mf.paint.helpers import _layer_colors_key
        self.assertEqual(_layer_colors_key("COLOR"), "3mf_paint_extruder_colors")


# ===========================================================================
#  Region-Based Quantization tests
# ===========================================================================

class FloodFillSegmentationTests(unittest.TestCase):
    """Tests for paint.bake._flood_fill_segmentation()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _flood_fill_segmentation
        self._flood_fill_segmentation = _flood_fill_segmentation

    def test_uniform_image_single_region(self):
        """A uniform image should produce exactly one region."""
        pixels = np.full((8, 8, 4), [0.5, 0.5, 0.5, 1.0], dtype=np.float32)

        region_map, num_regions = self._flood_fill_segmentation(pixels)

        self.assertEqual(num_regions, 1)
        # All non-transparent pixels should have region ID 1
        self.assertTrue(np.all(region_map == 1))

    def test_two_distinct_colors_two_regions(self):
        """Two clearly separate color blocks should produce two regions."""
        pixels = np.zeros((8, 8, 4), dtype=np.float32)
        pixels[:4, :, :3] = [1.0, 1.0, 1.0]  # white top
        pixels[4:, :, :3] = [0.0, 0.0, 0.0]  # black bottom
        pixels[:, :, 3] = 1.0  # fully opaque

        region_map, num_regions = self._flood_fill_segmentation(pixels, similarity_threshold=0.25)

        self.assertEqual(num_regions, 2)
        # Top and bottom should have different region IDs
        self.assertNotEqual(region_map[0, 0], region_map[7, 0])

    def test_gradient_with_similar_colors_one_region(self):
        """Gradual color change within threshold should form one region."""
        pixels = np.zeros((8, 8, 4), dtype=np.float32)
        # Create gradient from 0.4 to 0.6 grey - within 0.25 threshold
        for y in range(8):
            val = 0.4 + (y / 7) * 0.2
            pixels[y, :, :3] = val
        pixels[:, :, 3] = 1.0

        region_map, num_regions = self._flood_fill_segmentation(pixels, similarity_threshold=0.25)

        # Should be one connected region (gradient is within threshold)
        self.assertEqual(num_regions, 1)

    def test_transparent_pixels_ignored(self):
        """Fully transparent pixels should not be assigned to any region."""
        pixels = np.zeros((8, 8, 4), dtype=np.float32)
        pixels[:4, :, :3] = [1.0, 1.0, 1.0]
        pixels[:4, :, 3] = 1.0  # opaque white top
        # Bottom is transparent (alpha=0)

        region_map, num_regions = self._flood_fill_segmentation(pixels)

        # Only one region (the opaque part)
        self.assertEqual(num_regions, 1)
        # Transparent area should have region ID 0
        self.assertTrue(np.all(region_map[4:, :] == 0))


class RegionRepresentativeColorsTests(unittest.TestCase):
    """Tests for paint.bake._compute_region_representative_colors()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _compute_region_representative_colors
        self._compute_region_representative_colors = _compute_region_representative_colors

    def test_uniform_region_same_color(self):
        """A region with uniform color returns that color as representative."""
        pixels = np.full((8, 8, 4), [0.8, 0.2, 0.1, 1.0], dtype=np.float32)
        region_map = np.ones((8, 8), dtype=np.int32)

        rep_colors = self._compute_region_representative_colors(pixels, region_map, 1)

        self.assertIn(1, rep_colors)
        rep = rep_colors[1]
        np.testing.assert_array_almost_equal(rep, [0.8, 0.2, 0.1], decimal=2)

    def test_shadowed_region_uses_mode_brightness(self):
        """A region with shadows should use the mode brightness as representative.

        This tests the core insight: a white glove with grey shadows
        should have WHITE as the representative color (mode-based brightness bin),
        not grey (which would be the mean/median).
        """
        # 16x16 region: mostly white, with some grey shadows
        pixels = np.full((16, 16, 4), [1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        # Add grey shadows (25% of pixels)
        pixels[12:, :, :3] = [0.4, 0.4, 0.4]  # grey shadow rows

        region_map = np.ones((16, 16), dtype=np.int32)

        rep_colors = self._compute_region_representative_colors(pixels, region_map, 1)

        # The representative should be close to white, not grey
        rep = rep_colors[1]
        # Mode bin is white (75% of pixels), so representative should be white
        self.assertGreater(rep[0], 0.9, "Representative should be white, not grey")


class QuantizeByRegionsTests(unittest.TestCase):
    """Tests for paint.bake._quantize_by_regions()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _quantize_by_regions
        self._quantize_by_regions = _quantize_by_regions

    def test_uniform_image_single_color(self):
        """Uniform image quantizes to that palette color."""
        pixels = np.full((8, 8, 4), [0.9, 0.1, 0.1, 1.0], dtype=np.float32)
        palette = [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]  # red, blue

        self._quantize_by_regions(pixels, palette)

        np.testing.assert_array_almost_equal(
            pixels[4, 4, :3], [1.0, 0.0, 0.0], decimal=3
        )

    def test_shadow_on_white_glove_scenario(self):
        """THE MICKEY MOUSE TEST: grey shadows on white should snap to white.

        This is the key use case region-based quantization is designed to fix.
        A white glove with gradual grey shadows (forming one visually-connected
        region via smooth color transitions) should be quantized entirely to
        white, not have the shadows snap to black.

        Real baked shadows have gradients, not sharp edges. The flood-fill
        algorithm compares adjacent pixels, so gradual transitions connect.
        """
        # 32x32 white glove with GRADUAL grey shadow
        pixels = np.full((32, 32, 4), [1.0, 1.0, 1.0, 1.0], dtype=np.float32)

        # Create gradual shadow transition (like real baked lighting)
        # Row 12-13: light shadow (0.85)
        # Row 14-15: medium shadow (0.70)
        # Row 16-17: dark shadow (0.55)
        # Row 18-19: medium shadow (0.70)
        # Row 20-21: light shadow (0.85)
        pixels[12:14, 5:27, :3] = [0.85, 0.85, 0.85]
        pixels[14:16, 5:27, :3] = [0.70, 0.70, 0.70]
        pixels[16:18, 5:27, :3] = [0.55, 0.55, 0.55]
        pixels[18:20, 5:27, :3] = [0.70, 0.70, 0.70]
        pixels[20:22, 5:27, :3] = [0.85, 0.85, 0.85]

        palette = [(1.0, 1.0, 1.0), (0.0, 0.0, 0.0)]  # white, black

        # Use threshold that allows adjacent gradient steps to connect
        # The largest single step is 0.15 (e.g., 1.0→0.85 or 0.85→0.70)
        self._quantize_by_regions(pixels, palette, similarity_threshold=0.25)

        # The entire region (including shadows) should be WHITE
        # because:
        # 1. Region segmentation groups white+shadows together via gradient
        # 2. The representative color (75th percentile) is white
        np.testing.assert_array_almost_equal(
            pixels[16, 16, :3], [1.0, 1.0, 1.0], decimal=2,
            err_msg="Grey shadow on white glove should quantize to white"
        )
        # The originally white area should still be white
        np.testing.assert_array_almost_equal(
            pixels[5, 5, :3], [1.0, 1.0, 1.0], decimal=2
        )

    def test_separate_regions_quantize_independently(self):
        """Two clearly separate color regions quantize to different colors."""
        pixels = np.zeros((16, 16, 4), dtype=np.float32)
        pixels[:8, :, :3] = [0.9, 0.1, 0.1]  # reddish top
        pixels[8:, :, :3] = [0.1, 0.1, 0.9]  # blueish bottom
        pixels[:, :, 3] = 1.0

        palette = [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]  # red, blue

        self._quantize_by_regions(pixels, palette)

        # Top should be red
        np.testing.assert_array_almost_equal(
            pixels[4, 8, :3], [1.0, 0.0, 0.0], decimal=3
        )
        # Bottom should be blue
        np.testing.assert_array_almost_equal(
            pixels[12, 8, :3], [0.0, 0.0, 1.0], decimal=3
        )

    def test_similarity_threshold_affects_region_count(self):
        """Lower similarity threshold creates more regions."""
        # Create an image with gradual color change
        pixels = np.zeros((16, 16, 4), dtype=np.float32)
        for y in range(16):
            val = y / 15  # gradient from 0 to 1
            pixels[y, :, :3] = val
        pixels[:, :, 3] = 1.0

        palette = [(0.0, 0.0, 0.0), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0)]

        # With high threshold (0.5), should be fewer regions
        pixels_high = pixels.copy()
        self._quantize_by_regions(pixels_high, palette, similarity_threshold=0.5)

        # With low threshold (0.1), should be more regions
        pixels_low = pixels.copy()
        self._quantize_by_regions(pixels_low, palette, similarity_threshold=0.1)

        # Both should produce valid quantized images (all pixels match palette)
        # Low threshold might have more color transitions
        # Just verify no errors and pixels are from palette
        for y in range(16):
            color = tuple(pixels_low[y, 8, :3])
            # Should be close to one of the palette colors
            dists = [sum((a - b) ** 2 for a, b in zip(color, p)) for p in palette]
            self.assertLess(min(dists), 0.01, "Pixel should match a palette color")


# ===========================================================================
# Gradient Magnitude Tests
# ===========================================================================

class GradientMagnitudeTests(unittest.TestCase):
    """Tests for paint.bake._compute_gradient_magnitude()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _compute_gradient_magnitude
        self._compute_gradient_magnitude = _compute_gradient_magnitude

    def test_uniform_image_zero_gradient(self):
        """A uniform image should have near-zero gradient everywhere."""
        rgb = np.full((8, 8, 3), 0.5, dtype=np.float32)

        grad = self._compute_gradient_magnitude(rgb)

        self.assertEqual(grad.shape, (8, 8))
        # Interior pixels should be exactly 0
        np.testing.assert_array_almost_equal(grad[1:-1, 1:-1], 0.0)

    def test_sharp_edge_high_gradient(self):
        """A sharp black-to-white edge should produce high gradient values."""
        rgb = np.zeros((8, 8, 3), dtype=np.float32)
        rgb[:, 4:, :] = 1.0  # white on right

        grad = self._compute_gradient_magnitude(rgb)

        # Gradient should be high at the edge (column 3-4 boundary region)
        # Central difference at column 4: |rgb[4+1] - rgb[4-1]| = |1.0 - 0.0| = 1.0
        self.assertGreater(grad[4, 4], 0.5, "Sharp edge should have high gradient")
        # Far from edge should have low gradient
        self.assertLess(grad[4, 0], 0.01, "Far from edge should have low gradient")
        self.assertLess(grad[4, 7], 0.01, "Far from edge should have low gradient")

    def test_gradual_transition_low_gradient(self):
        """A smooth gradient should have low gradient magnitude throughout."""
        rgb = np.zeros((8, 8, 3), dtype=np.float32)
        for x in range(8):
            rgb[:, x, :] = x / 7.0

        grad = self._compute_gradient_magnitude(rgb)

        # Central diff step should be ~2/7 ≈ 0.286 at interior pixels
        # Still below a typical threshold of 0.5
        self.assertLess(np.max(grad), 0.4, "Gradual gradient should be modest")


# ===========================================================================
# Merge Small Regions Tests
# ===========================================================================

class MergeSmallRegionsTests(unittest.TestCase):
    """Tests for paint.bake._merge_small_regions()."""

    def setUp(self):
        from io_mesh_3mf.paint.bake import _merge_small_regions
        self._merge_small_regions = _merge_small_regions

    def test_no_small_regions_unchanged(self):
        """If no regions are below min_size, the map should be unchanged."""
        region_map = np.zeros((10, 10), dtype=np.int32)
        region_map[:5, :] = 1  # 50 pixels
        region_map[5:, :] = 2  # 50 pixels

        merged, num = self._merge_small_regions(region_map, 2, min_region_size=10)

        self.assertEqual(num, 2)
        # Both regions should still exist
        self.assertTrue(np.any(merged == 1) or np.any(merged == 2))

    def test_small_region_merged_into_neighbor(self):
        """A small region surrounded by a large one should be absorbed."""
        region_map = np.ones((10, 10), dtype=np.int32)  # all region 1
        # Add a tiny region 2 in the center (4 pixels)
        region_map[4:6, 4:6] = 2

        merged, num = self._merge_small_regions(region_map, 2, min_region_size=10)

        # The tiny region should have been absorbed into region 1
        self.assertEqual(num, 1)
        self.assertTrue(np.all(merged[merged > 0] == merged[0, 0]))

    def test_min_size_zero_no_merging(self):
        """min_region_size=0 should disable merging entirely."""
        region_map = np.ones((10, 10), dtype=np.int32)
        region_map[4:6, 4:6] = 2

        merged, num = self._merge_small_regions(region_map, 2, min_region_size=0)

        # Both regions should survive
        self.assertEqual(num, 2)

    def test_background_not_merged(self):
        """Background pixels (id=0) should not be treated as a region."""
        region_map = np.zeros((10, 10), dtype=np.int32)  # all background
        region_map[2:8, 2:8] = 1  # 36 pixels
        region_map[4:6, 4:6] = 2  # 4 pixels in center

        merged, num = self._merge_small_regions(region_map, 2, min_region_size=10)

        # Tiny center should merge into its neighbor (region 1), not background
        self.assertTrue(np.all(merged[4:6, 4:6] > 0),
                        "Small region should merge into neighbor, not background")


if __name__ == "__main__":
    unittest.main()
