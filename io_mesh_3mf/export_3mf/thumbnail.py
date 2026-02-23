# Blender add-on to import and export 3MF files.
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

Three modes:

- **AUTO** — Renders an off-screen preview of the exported objects from an
  elevated 3/4-view angle with grid, gizmos, and overlays disabled.
- **CUSTOM** — Reads a user-supplied image file and writes it as the thumbnail.
- **NONE** — Skips thumbnail generation entirely.

The result is stored as ``Metadata/thumbnail.png`` inside the 3MF archive.
"""

from __future__ import annotations

import math
import os
import tempfile
import zipfile
from typing import TYPE_CHECKING, List, Optional

import bpy
import mathutils

from ..common.logging import debug, warn

if TYPE_CHECKING:
    from .context import ExportContext


# ───────────────────────────────────────────────────────────────────────────
# Public entry point
# ───────────────────────────────────────────────────────────────────────────

def write_thumbnail(
    archive: zipfile.ZipFile,
    ctx: Optional["ExportContext"] = None,
    blender_objects: Optional[List[bpy.types.Object]] = None,
) -> None:
    """Generate a thumbnail and write it into the 3MF archive.

    :param archive: Open 3MF ZIP archive.
    :param ctx: Export context holding thumbnail options.  When *None*,
        falls back to ``AUTO`` mode at 256 × 256.
    :param blender_objects: Objects being exported — used by AUTO mode to
        frame the camera.  When *None*, all visible scene objects are used.
    """
    # Resolve options from context or defaults.
    if ctx is not None:
        mode = ctx.options.thumbnail_mode          # "AUTO" | "CUSTOM" | "NONE"
        resolution = ctx.options.thumbnail_resolution
        custom_path = ctx.options.thumbnail_image
    else:
        mode = "AUTO"
        resolution = 256
        custom_path = ""

    if mode == "NONE":
        debug("Thumbnail generation disabled by user")
        return

    try:
        if mode == "CUSTOM" and custom_path:
            _write_custom_thumbnail(archive, custom_path)
        else:
            _write_auto_thumbnail(archive, resolution, blender_objects)
    except Exception as e:
        warn(f"Failed to write thumbnail: {e}")


# ───────────────────────────────────────────────────────────────────────────
# CUSTOM mode — user-supplied image
# ───────────────────────────────────────────────────────────────────────────

def _write_custom_thumbnail(
    archive: zipfile.ZipFile,
    image_ref: str,
) -> None:
    """Write a user-supplied image as the thumbnail.

    *image_ref* is a ``bpy.data.images`` name.  If the name isn't found in
    the current .blend file it is treated as a file path for backwards
    compatibility (e.g. from the API).
    """
    # Try bpy.data.images first (the normal UI path).
    img = bpy.data.images.get(image_ref)
    loaded = False

    if img is None:
        # Fallback: treat as file path (API callers may pass a path).
        abs_path = bpy.path.abspath(image_ref)
        if not os.path.isfile(abs_path):
            warn(f"Custom thumbnail image not found: {image_ref}")
            return
        try:
            img = bpy.data.images.load(abs_path, check_existing=False)
            loaded = True
        except Exception as e:
            warn(f"Cannot load custom thumbnail image: {e}")
            return

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        img.file_format = "PNG"
        img.save_render(tmp_path)

        with open(tmp_path, "rb") as f:
            png_data = f.read()

        with archive.open("Metadata/thumbnail.png", "w") as f:
            f.write(png_data)

        debug(f"Wrote custom thumbnail from '{img.name}'")
    finally:
        # Only remove images we loaded ourselves (not user data).
        if loaded:
            bpy.data.images.remove(img)
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ───────────────────────────────────────────────────────────────────────────
# AUTO mode — off-screen render from computed camera
# ───────────────────────────────────────────────────────────────────────────

def _write_auto_thumbnail(
    archive: zipfile.ZipFile,
    resolution: int,
    blender_objects: Optional[List[bpy.types.Object]],
) -> None:
    """Render an automatic thumbnail from an elevated 3/4 view.

    Creates a temporary camera framing all exported objects, disables
    overlays (grid, gizmos, annotations), renders an OpenGL viewport
    capture, and writes the PNG into the archive.
    """
    if bpy.app.background:
        debug("Skipping thumbnail generation in background mode")
        return

    # Find a 3D viewport ---------------------------------------------------
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

    region = None
    for r in view3d_area.regions:
        if r.type == "WINDOW":
            region = r
            break
    if not region:
        return

    space = None
    for s in view3d_area.spaces:
        if s.type == "VIEW_3D":
            space = s
            break
    if not space:
        return

    # Compute bounding box of exported objects -----------------------------
    bbox_min, bbox_max = _compute_world_bbox(blender_objects)
    if bbox_min is None:
        debug("No objects to frame for thumbnail")
        return

    center = (bbox_min + bbox_max) * 0.5
    extent = bbox_max - bbox_min
    max_dim = max(extent.x, extent.y, extent.z, 0.001)

    # Camera position
    azimuth = math.radians(225.0)
    elevation = math.radians(25.0)
    # Distance proportional to bounding sphere radius — tight framing
    distance = max_dim * 1.2

    cam_x = center.x + distance * math.cos(elevation) * math.sin(azimuth)
    cam_y = center.y - distance * math.cos(elevation) * math.cos(azimuth)
    cam_z = center.z + distance * math.sin(elevation)
    camera_loc = mathutils.Vector((cam_x, cam_y, cam_z))

    # Build a rotation matrix that looks from camera_loc → center.
    direction = center - camera_loc
    rot_quat = direction.to_track_quat("-Z", "Y")

    # Save / override viewport state ----------------------------------------
    orig_view_type = space.region_3d.view_perspective
    orig_view_location = space.region_3d.view_location.copy()
    orig_view_rotation = space.region_3d.view_rotation.copy()
    orig_view_distance = space.region_3d.view_distance

    orig_show_overlays = space.overlay.show_overlays
    orig_show_gizmo = space.show_gizmo

    scene = bpy.context.scene
    orig_res_x = scene.render.resolution_x
    orig_res_y = scene.render.resolution_y
    orig_res_pct = scene.render.resolution_percentage
    orig_format = scene.render.image_settings.file_format
    orig_filepath = scene.render.filepath
    orig_alpha_mode = scene.render.image_settings.color_mode

    # Temporarily hide all objects NOT in the export set so only the
    # exported meshes appear in the thumbnail (no reference spheres,
    # lights, cameras, empties, etc.).
    export_set = set(id(o) for o in blender_objects) if blender_objects else set()
    hidden_objects = []  # list of (obj, original_hide_get) to restore
    if export_set:
        for obj in scene.objects:
            if id(obj) not in export_set and not obj.hide_get():
                obj.hide_set(True)
                hidden_objects.append(obj)

    tmp_path = ""
    try:
        # Set viewport to our computed camera angle -------------------------
        space.region_3d.view_perspective = "PERSP"
        space.region_3d.view_location = center
        space.region_3d.view_rotation = rot_quat
        space.region_3d.view_distance = distance

        # Disable all overlays (grid, floor, axes, cursor, origins,
        # reference spheres, extras, annotations — everything).
        space.overlay.show_overlays = False
        space.show_gizmo = False

        # Render settings ---------------------------------------------------
        scene.render.resolution_x = resolution
        scene.render.resolution_y = resolution
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"
        scene.render.image_settings.color_mode = "RGBA"

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        scene.render.filepath = tmp_path

        # Render OpenGL viewport capture ------------------------------------
        override = bpy.context.copy()
        override["area"] = view3d_area
        override["region"] = region

        with bpy.context.temp_override(**override):
            bpy.ops.render.opengl(write_still=True)

        # Write to archive --------------------------------------------------
        with open(tmp_path, "rb") as f:
            png_data = f.read()

        with archive.open("Metadata/thumbnail.png", "w") as f:
            f.write(png_data)

        debug(f"Wrote thumbnail.png ({resolution}x{resolution}) from auto render")

    finally:
        # Restore everything ------------------------------------------------
        space.region_3d.view_perspective = orig_view_type
        space.region_3d.view_location = orig_view_location
        space.region_3d.view_rotation = orig_view_rotation
        space.region_3d.view_distance = orig_view_distance

        space.overlay.show_overlays = orig_show_overlays
        space.show_gizmo = orig_show_gizmo

        scene.render.resolution_x = orig_res_x
        scene.render.resolution_y = orig_res_y
        scene.render.resolution_percentage = orig_res_pct
        scene.render.image_settings.file_format = orig_format
        scene.render.image_settings.color_mode = orig_alpha_mode
        scene.render.filepath = orig_filepath

        # Unhide objects that were temporarily hidden -------------------
        for obj in hidden_objects:
            obj.hide_set(False)

        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def _compute_world_bbox(
    blender_objects: Optional[List[bpy.types.Object]],
) -> tuple:
    """Return (min_vec, max_vec) world-space AABB of the given objects.

    Falls back to all visible scene objects when *blender_objects* is None.
    Returns (None, None) if nothing is found.
    """
    objects = blender_objects
    if objects is None:
        objects = [o for o in bpy.context.scene.objects if o.visible_get()]

    bb_min = mathutils.Vector((float("inf"), float("inf"), float("inf")))
    bb_max = mathutils.Vector((float("-inf"), float("-inf"), float("-inf")))
    found = False

    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            continue
        # bound_box is in local space — transform each corner to world.
        for corner in obj.bound_box:
            world_pt = obj.matrix_world @ mathutils.Vector(corner)
            bb_min.x = min(bb_min.x, world_pt.x)
            bb_min.y = min(bb_min.y, world_pt.y)
            bb_min.z = min(bb_min.z, world_pt.z)
            bb_max.x = max(bb_max.x, world_pt.x)
            bb_max.y = max(bb_max.y, world_pt.y)
            bb_max.z = max(bb_max.z, world_pt.z)
            found = True

    if not found:
        return None, None
    return bb_min, bb_max
