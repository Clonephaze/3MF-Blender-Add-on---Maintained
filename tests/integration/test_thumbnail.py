"""
Integration tests for thumbnail generation in 3MF export.

Tests AUTO / CUSTOM / NONE thumbnail modes via both operator and API.
Runs inside real Blender (``--background --factory-startup``).
"""

import os
import struct
import tempfile
import unittest
import zipfile

import bpy

from test_base import Blender3mfTestCase, get_temp_test_dir

from io_mesh_3mf.api import export_3mf, ExportResult


def _has_thumbnail(archive_path: str) -> bool:
    """Return True if *Metadata/thumbnail.png* exists in the archive."""
    with zipfile.ZipFile(archive_path, "r") as zf:
        return "Metadata/thumbnail.png" in zf.namelist()


def _read_thumbnail_bytes(archive_path: str) -> bytes:
    """Read the raw thumbnail PNG bytes from the archive."""
    with zipfile.ZipFile(archive_path, "r") as zf:
        return zf.read("Metadata/thumbnail.png")


def _png_dimensions(data: bytes):
    """Parse width and height from a PNG header (first IHDR chunk)."""
    # PNG signature: 8 bytes, then first chunk is IHDR (4 len + 4 type + 4 w + 4 h)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None, None
    width = struct.unpack(">I", data[16:20])[0]
    height = struct.unpack(">I", data[20:24])[0]
    return width, height


# ============================================================================
# Thumbnail — Operator (bpy.ops)
# ============================================================================


class TestThumbnailOperator(Blender3mfTestCase):
    """Thumbnail via the export operator."""

    def test_auto_thumbnail_background_graceful(self):
        """In background mode, AUTO should succeed but skip the render."""
        bpy.ops.mesh.primitive_cube_add()
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            thumbnail_mode="AUTO",
        )
        self.assertIn("FINISHED", result)
        self.assertTrue(self.temp_file.exists())
        # Background mode has no OpenGL context, so thumbnail is skipped.
        # The export should still succeed.

    def test_none_mode_no_thumbnail(self):
        """NONE mode should produce a valid archive with no thumbnail."""
        bpy.ops.mesh.primitive_cube_add()
        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            thumbnail_mode="NONE",
        )
        self.assertIn("FINISHED", result)
        self.assertFalse(_has_thumbnail(str(self.temp_file)))

    def test_custom_mode_with_blend_image(self):
        """CUSTOM mode using a bpy.data.images entry writes a thumbnail."""
        bpy.ops.mesh.primitive_cube_add()

        # Create a small in-memory image.
        img = bpy.data.images.new("ThumbTest", width=32, height=32, alpha=True)
        pixels = [1.0, 0.0, 0.0, 1.0] * (32 * 32)  # solid red
        img.pixels[:] = pixels

        result = bpy.ops.export_mesh.threemf(
            filepath=str(self.temp_file),
            thumbnail_mode="CUSTOM",
            thumbnail_image=img.name,
        )
        self.assertIn("FINISHED", result)
        self.assertTrue(_has_thumbnail(str(self.temp_file)))

        # Verify it's a valid PNG.
        data = _read_thumbnail_bytes(str(self.temp_file))
        self.assertTrue(data[:4] == b"\x89PNG")


# ============================================================================
# Thumbnail — API (export_3mf)
# ============================================================================


class TestThumbnailAPI(Blender3mfTestCase):
    """Thumbnail via the public API."""

    def test_api_none_mode(self):
        """API: thumbnail_mode='NONE' produces no thumbnail."""
        bpy.ops.mesh.primitive_cube_add()
        result = export_3mf(str(self.temp_file), thumbnail_mode="NONE")
        self.assertEqual(result.status, "FINISHED")
        self.assertFalse(_has_thumbnail(str(self.temp_file)))

    def test_api_auto_background(self):
        """API: AUTO in background gracefully skips thumbnail render."""
        bpy.ops.mesh.primitive_cube_add()
        result = export_3mf(str(self.temp_file), thumbnail_mode="AUTO")
        self.assertEqual(result.status, "FINISHED")
        # In --background mode, the render is silently skipped.
        self.assertTrue(self.temp_file.exists())

    def test_api_custom_blend_image(self):
        """API: CUSTOM mode with a bpy.data.images name."""
        bpy.ops.mesh.primitive_cube_add()

        img = bpy.data.images.new("APIThumbTest", width=16, height=16)
        pixels = [0.0, 1.0, 0.0, 1.0] * (16 * 16)  # solid green
        img.pixels[:] = pixels

        result = export_3mf(
            str(self.temp_file),
            thumbnail_mode="CUSTOM",
            thumbnail_image=img.name,
        )
        self.assertEqual(result.status, "FINISHED")
        self.assertTrue(_has_thumbnail(str(self.temp_file)))

    def test_api_custom_file_path(self):
        """API: CUSTOM mode with an actual file path (backwards compat)."""
        bpy.ops.mesh.primitive_cube_add()

        # Create a tiny temporary PNG on disk.
        img = bpy.data.images.new("DiskThumbTest", width=8, height=8)
        pixels = [0.0, 0.0, 1.0, 1.0] * (8 * 8)  # solid blue
        img.pixels[:] = pixels

        tmp_png = os.path.join(tempfile.gettempdir(), "3mf_test_thumb.png")
        img.file_format = "PNG"
        img.save_render(tmp_png)
        bpy.data.images.remove(img)

        try:
            result = export_3mf(
                str(self.temp_file),
                thumbnail_mode="CUSTOM",
                thumbnail_image=tmp_png,
            )
            self.assertEqual(result.status, "FINISHED")
            self.assertTrue(_has_thumbnail(str(self.temp_file)))

            data = _read_thumbnail_bytes(str(self.temp_file))
            self.assertTrue(data[:4] == b"\x89PNG")
        finally:
            if os.path.exists(tmp_png):
                os.remove(tmp_png)

    def test_api_custom_missing_image_falls_back(self):
        """API: CUSTOM with a bad name/path should warn but not crash."""
        bpy.ops.mesh.primitive_cube_add()
        result = export_3mf(
            str(self.temp_file),
            thumbnail_mode="CUSTOM",
            thumbnail_image="/nonexistent/image.png",
        )
        self.assertEqual(result.status, "FINISHED")
        # Thumbnail missing, but export itself succeeds.
        self.assertFalse(_has_thumbnail(str(self.temp_file)))

    def test_api_default_mode_is_auto(self):
        """API: default thumbnail_mode should be AUTO."""
        bpy.ops.mesh.primitive_cube_add()
        # Just call with defaults — should not crash.
        result = export_3mf(str(self.temp_file))
        self.assertEqual(result.status, "FINISHED")

    def test_api_resolution_param_accepted(self):
        """API: thumbnail_resolution parameter is accepted without error."""
        bpy.ops.mesh.primitive_cube_add()
        result = export_3mf(
            str(self.temp_file),
            thumbnail_mode="AUTO",
            thumbnail_resolution=512,
        )
        self.assertEqual(result.status, "FINISHED")


if __name__ == "__main__":
    unittest.main()
