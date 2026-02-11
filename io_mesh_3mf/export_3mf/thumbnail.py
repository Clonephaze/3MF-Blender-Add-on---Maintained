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

# <pep8 compliant>

"""
Thumbnail generation for 3MF export.

Renders a viewport preview and saves it as ``Metadata/thumbnail.png`` in the
3MF archive.
"""

import os
import tempfile
import zipfile

import bpy

from ..common.logging import debug, warn


def write_thumbnail(archive: zipfile.ZipFile) -> None:
    """
    Generate a thumbnail and save it to the 3MF archive.

    Renders a small preview of the current viewport and saves it as
    Metadata/thumbnail.png in the 3MF archive.

    :param archive: The 3MF archive to write the thumbnail into.
    """
    try:
        # Skip thumbnail generation in background mode (no OpenGL context)
        if bpy.app.background:
            debug("Skipping thumbnail generation in background mode")
            return

        # Thumbnail dimensions (3MF spec recommends these sizes)
        thumb_width = 256
        thumb_height = 256

        # Find a 3D viewport to render from
        view3d_area = None
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    view3d_area = area
                    break
            if view3d_area:
                break

        if not view3d_area:
            debug("No 3D viewport found for thumbnail generation")
            return

        # Create a temporary file for the render
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            temp_path = tmp.name

        # Store original render settings
        scene = bpy.context.scene
        original_res_x = scene.render.resolution_x
        original_res_y = scene.render.resolution_y
        original_res_percent = scene.render.resolution_percentage
        original_file_format = scene.render.image_settings.file_format
        original_filepath = scene.render.filepath

        try:
            # Set up for thumbnail render
            scene.render.resolution_x = thumb_width
            scene.render.resolution_y = thumb_height
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = "PNG"
            scene.render.filepath = temp_path

            # Render viewport (much faster than full render)
            override = bpy.context.copy()
            override["area"] = view3d_area
            override["region"] = [
                r for r in view3d_area.regions if r.type == "WINDOW"
            ][0]

            with bpy.context.temp_override(**override):
                bpy.ops.render.opengl(write_still=True)

            # Read the rendered PNG
            with open(temp_path, "rb") as png_file:
                png_data = png_file.read()

            # Write to 3MF archive
            with archive.open("Metadata/thumbnail.png", "w") as f:
                f.write(png_data)

            debug(
                f"Wrote thumbnail.png ({thumb_width}x{thumb_height}) from viewport render"
            )

        finally:
            # Restore original settings
            scene.render.resolution_x = original_res_x
            scene.render.resolution_y = original_res_y
            scene.render.resolution_percentage = original_res_percent
            scene.render.image_settings.file_format = original_file_format
            scene.render.filepath = original_filepath

            # Clean up temp file
            try:
                os.remove(temp_path)
            except OSError:
                pass

    except Exception as e:
        warn(f"Failed to write thumbnail: {e}")
        # Non-critical, don't fail the export
