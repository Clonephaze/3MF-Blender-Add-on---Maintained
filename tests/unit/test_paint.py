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


if __name__ == "__main__":
    unittest.main()
