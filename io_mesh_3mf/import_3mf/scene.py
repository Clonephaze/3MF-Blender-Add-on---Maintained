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
Scene helpers — Blender mesh creation, material assignment, UV setup, origin
placement, and grid layout.

These are the sub-functions extracted from the monolithic ``build_object()``
method.  Each one handles a single concern and operates on either a Blender
mesh or object.
"""

from typing import Dict, List, Optional, TYPE_CHECKING

import bpy
import bpy_extras.node_shader_utils
import mathutils

from ..common import debug, warn
from ..common.types import ResourceMaterial, ResourceObject

if TYPE_CHECKING:
    from .context import ImportContext

__all__ = [
    "create_mesh_from_data",
    "assign_materials_to_mesh",
    "render_paint_texture",
    "render_seam_support_texture",
    "create_solid_paint_texture",
    "finalize_shared_solid_texture",
    "apply_triangle_sets",
    "apply_uv_coordinates",
    "set_object_origin",
    "apply_import_location",
    "apply_grid_layout",
]


# ---------------------------------------------------------------------------
# create_mesh_from_data
# ---------------------------------------------------------------------------

def create_mesh_from_data(
    resource_object: ResourceObject,
) -> Optional[bpy.types.Mesh]:
    """Create a Blender mesh from parsed 3MF geometry data.

    :param resource_object: The resource object containing vertices and triangles.
    :return: A new :class:`bpy.types.Mesh`, or ``None`` if there are no triangles.
    """
    if not resource_object.triangles:
        return None

    mesh = bpy.data.meshes.new("3MF Mesh")
    mesh.from_pydata(resource_object.vertices, [], resource_object.triangles)
    mesh.update()
    resource_object.metadata.store(mesh)
    return mesh


# ---------------------------------------------------------------------------
# render_paint_texture
# ---------------------------------------------------------------------------

def render_paint_texture(
    ctx: "ImportContext",
    mesh: bpy.types.Mesh,
    resource_object: ResourceObject,
) -> bool:
    """Render MMU segmentation strings to a UV-based paint texture.

    :param ctx: Import context.
    :param mesh: The Blender mesh to apply the texture to.
    :param resource_object: Source data with segmentation strings.
    :return: ``True`` if a paint texture was successfully rendered.
    """
    if not resource_object.segmentation_strings:
        return False
    if ctx.options.import_materials != "PAINT":
        return False
    if resource_object.default_extruder is None:
        return False

    extruder_colors_hex = dict(ctx.orca_filament_colors) if ctx.orca_filament_colors else {}
    if not extruder_colors_hex:
        warn("No extruder colors found — cannot render MMU paint texture")
        return False

    from .segmentation import render_segmentation_to_texture

    # Create temporary object for UV unwrapping
    temp_obj = bpy.data.objects.new("_temp_uv", mesh)
    bpy.context.collection.objects.link(temp_obj)

    try:
        # Convert hex colors to RGBA lists
        extruder_colors: Dict[int, list] = {}
        for idx, hex_color in extruder_colors_hex.items():
            if hex_color.startswith("#") and len(hex_color) == 7:
                r = int(hex_color[1:3], 16) / 255.0
                g = int(hex_color[3:5], 16) / 255.0
                b = int(hex_color[5:7], 16) / 255.0
                extruder_colors[idx] = [r, g, b, 1.0]
            else:
                extruder_colors[idx] = [0.5, 0.5, 0.5, 1.0]

        # Texture size — user override or auto based on triangle count
        override_size = ctx.options.paint_texture_size
        tri_count = len(resource_object.triangles)
        if override_size > 0:
            texture_size = override_size
        elif tri_count < 5000:
            texture_size = 2048
        elif tri_count < 20000:
            texture_size = 4096
        else:
            texture_size = 8192

        debug(
            f"Rendering MMU segmentation to {texture_size}x{texture_size} "
            f"UV texture for {tri_count} triangles"
        )

        image = render_segmentation_to_texture(
            temp_obj,
            resource_object.segmentation_strings,
            extruder_colors,
            texture_size=texture_size,
            default_extruder=resource_object.default_extruder,
            uv_method=ctx.options.paint_uv_method,
        )

        # Build material with segmentation texture
        mat = bpy.data.materials.new(name=f"{mesh.name}_MMU_Paint")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        tex_node.location = (-300, 0)

        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (100, 0)

        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)

        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        mesh.materials.append(mat)

        num_faces = len(mesh.polygons)
        mesh.polygons.foreach_set("material_index", [0] * num_faces)

        # Store custom properties for round-trip export
        mesh["3mf_paint_extruder_colors"] = str(extruder_colors_hex)
        mesh["3mf_paint_default_extruder"] = resource_object.default_extruder
        mesh["3mf_is_paint_texture"] = True
        if ctx.num_physical_filaments > 0:
            mesh["3mf_num_physical_filaments"] = ctx.num_physical_filaments

        ctx._paint_object_names.append(mesh.name)
        debug("Successfully rendered MMU paint data to UV texture")
        return True

    finally:
        bpy.data.objects.remove(temp_obj, do_unlink=True)


# ---------------------------------------------------------------------------
# render_seam_support_texture
# ---------------------------------------------------------------------------

def create_solid_paint_texture(
    ctx: "ImportContext",
    mesh: bpy.types.Mesh,
    extruder_1based: int,
) -> bool:
    """Create a solid-color UV paint texture for a per-part extruder assignment.

    Used when importing in PAINT mode for files (e.g. PeggyPalette) where each
    part carries ``extruder=N`` metadata but no per-triangle segmentation data.
    UV-unwraps the mesh and fills every texel with the part's filament color so
    the result is indistinguishable from a normally-imported paint object and
    the user can begin painting immediately.

    :param ctx: Import context.
    :param mesh: The Blender mesh to apply the texture to.
    :param extruder_1based: 1-based extruder index for this part.
    :return: ``True`` if the texture was successfully created.
    """
    from .segmentation import render_segmentation_to_texture

    extruder_colors_hex = dict(ctx.orca_filament_colors) if ctx.orca_filament_colors else {0: "#808080"}

    # For PARTS-type files (e.g. PeggyPalette) all virtual filaments may be
    # marked deleted, so orca_filament_colors only contains physical entries.
    # Re-populate virtual slots positionally from ctx.mixed_filament_entries so
    # that extruder=N assignments resolve to the correct display color.
    num_physical = ctx.num_physical_filaments or len(extruder_colors_hex)
    if ctx.mixed_filament_entries:
        for pos_idx, mf in enumerate(ctx.mixed_filament_entries):
            slot_idx = num_physical + pos_idx
            if slot_idx not in extruder_colors_hex:
                extruder_colors_hex[slot_idx] = mf.display_color

    # Convert hex colors → RGBA float lists for the renderer.
    extruder_colors: Dict[int, list] = {}
    for idx, hex_color in extruder_colors_hex.items():
        if isinstance(hex_color, str) and hex_color.startswith("#") and len(hex_color) == 7:
            r = int(hex_color[1:3], 16) / 255.0
            g = int(hex_color[3:5], 16) / 255.0
            b = int(hex_color[5:7], 16) / 255.0
            extruder_colors[idx] = [r, g, b, 1.0]
        else:
            extruder_colors[idx] = [0.5, 0.5, 0.5, 1.0]

    temp_obj = bpy.data.objects.new("_temp_solid_uv", mesh)
    bpy.context.collection.objects.link(temp_obj)

    try:
        override_size = ctx.options.paint_texture_size
        tri_count = len(mesh.polygons)
        if override_size > 0:
            texture_size = override_size
        elif tri_count < 5000:
            texture_size = 2048
        elif tri_count < 20000:
            texture_size = 4096
        else:
            texture_size = 8192

        debug(
            f"Creating solid paint texture ({texture_size}px) for "
            f"extruder {extruder_1based} on {mesh.name!r}"
        )

        # Empty seg_strings → every face is filled with default_color
        # (the fallback loop in render_segmentation_to_texture covers all faces).
        image = render_segmentation_to_texture(
            temp_obj,
            {},
            extruder_colors,
            texture_size=texture_size,
            default_extruder=extruder_1based,
            uv_method=ctx.options.paint_uv_method,
        )

        mat = bpy.data.materials.new(name=f"{mesh.name}_MMU_Paint")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        tex_node.location = (-300, 0)

        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (100, 0)

        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)

        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        mesh.materials.append(mat)

        num_faces = len(mesh.polygons)
        mesh.polygons.foreach_set("material_index", [0] * num_faces)

        mesh["3mf_paint_extruder_colors"] = str(extruder_colors_hex)
        mesh["3mf_paint_default_extruder"] = extruder_1based
        mesh["3mf_is_paint_texture"] = True
        if ctx.num_physical_filaments > 0:
            mesh["3mf_num_physical_filaments"] = ctx.num_physical_filaments

        ctx._paint_object_names.append(mesh.name)
        return True

    finally:
        bpy.data.objects.remove(temp_obj, do_unlink=True)


# ---------------------------------------------------------------------------
# finalize_shared_solid_texture
# ---------------------------------------------------------------------------

def finalize_shared_solid_texture(ctx: "ImportContext") -> None:
    """Batch all deferred solid-paint parts into a single shared texture.

    Called once after all objects in an archive are built.  Instead of N
    individual textures (one per part), this creates ONE shared image:

    1. Create a temporary Blender object for every mesh.
    2. In **multi-object edit mode** (all objects selected at once):
       - Smart UV Project → every mesh gets non-overlapping islands in [0,1].
       - Pack Islands → all islands across all objects are repacked together
         into [0,1] without overlapping each other.
    3. Read back the final UV coordinates per mesh.
    4. Rasterize each mesh's triangles into a shared numpy buffer, filled
       with the solid filament color for that part.  Same triangle-fill
       and gap-close logic as ``render_segmentation_to_texture``.
    5. Create one :class:`bpy.types.Image` from the buffer.
    6. Assign one material per mesh, all pointing at the shared image.

    If there is only one part fall back to ``create_solid_paint_texture``
    (no sharing overhead).
    """
    import math
    import numpy as np
    from .segmentation import render_triangle_to_image, close_gaps_in_texture

    parts = ctx.pending_solid_paint_parts
    if not parts:
        return

    # --- Single-part fallback -----------------------------------------------
    if len(parts) == 1:
        mesh, extruder_1based = parts[0]
        create_solid_paint_texture(ctx, mesh, extruder_1based)
        ctx.pending_solid_paint_parts.clear()
        return

    # --- Build color lookup -------------------------------------------------
    extruder_colors_hex = dict(ctx.orca_filament_colors) if ctx.orca_filament_colors else {0: "#808080"}
    num_physical = ctx.num_physical_filaments or len(extruder_colors_hex)
    if ctx.mixed_filament_entries:
        for pos_idx, mf in enumerate(ctx.mixed_filament_entries):
            slot_idx = num_physical + pos_idx
            if slot_idx not in extruder_colors_hex:
                extruder_colors_hex[slot_idx] = mf.display_color

    def _hex_to_rgba(h: str):
        if isinstance(h, str) and h.startswith("#") and len(h) == 7:
            return np.array([int(h[1:3], 16) / 255.0,
                             int(h[3:5], 16) / 255.0,
                             int(h[5:7], 16) / 255.0, 1.0], dtype=np.float32)
        return np.array([0.5, 0.5, 0.5, 1.0], dtype=np.float32)

    # --- Texture size based on total triangle count -------------------------
    n = len(parts)
    total_tris = sum(len(mesh.polygons) for mesh, _ in parts)
    if total_tris < 5000:
        texture_size = 2048
    elif total_tris < 20000:
        texture_size = 4096
    else:
        texture_size = 8192
    override = ctx.options.paint_texture_size
    if override > 0:
        texture_size = override

    uv_layer_name = "MMU_Paint"

    # --- Step 1: create temp objects and ensure UV layer --------------------
    temp_objs = []
    for mesh, _ext in parts:
        uv_layer = mesh.uv_layers.get(uv_layer_name)
        if uv_layer is None:
            uv_layer = mesh.uv_layers.new(name=uv_layer_name)
        mesh.uv_layers.active = uv_layer
        uv_layer.active_render = True

        obj = bpy.data.objects.new("_temp_shared_uv", mesh)
        bpy.context.collection.objects.link(obj)
        temp_objs.append(obj)

    try:
        # --- Step 2: multi-object Smart UV Project + Pack Islands -----------
        # Deselect everything first, then select all temp objects.
        bpy.ops.object.select_all(action="DESELECT")
        for obj in temp_objs:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = temp_objs[0]

        # Enter multi-object edit mode (all selected objects participate).
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")

        # Smart UV Project across all selected objects simultaneously.
        # island_margin targets ~4px gap regardless of texture resolution.
        _island_margin = max(0.0002, 4.0 / texture_size)
        bpy.ops.uv.smart_project(
            angle_limit=1.15192,
            margin_method="SCALED",
            rotate_method="AXIS_ALIGNED",
            island_margin=_island_margin,
            area_weight=0.6,
            correct_aspect=True,
            scale_to_bounds=False,
        )

        # Pack all islands from all objects together into [0,1]×[0,1].
        bpy.ops.uv.pack_islands(margin=_island_margin)

        bpy.ops.object.mode_set(mode="OBJECT")

        # --- Step 3: rasterize each mesh into shared buffer -----------------
        buf = np.zeros((texture_size, texture_size, 4), dtype=np.float32)
        # Pre-fill transparent so gap-close can propagate island-edge colors.
        buf[:, :, 3] = 0.0

        for obj, (mesh, extruder_1based) in zip(temp_objs, parts):
            rgba = _hex_to_rgba(extruder_colors_hex.get(extruder_1based - 1, "#808080"))

            uv_data = mesh.uv_layers.active.data
            num_loops = len(uv_data)
            uv_flat = np.zeros(num_loops * 2, dtype=np.float64)
            uv_data.foreach_get("uv", uv_flat)
            all_uvs = uv_flat.reshape(-1, 2)

            for poly in mesh.polygons:
                if len(poly.loop_indices) != 3:
                    continue
                li = list(poly.loop_indices)
                uv0 = all_uvs[li[0]]
                uv1 = all_uvs[li[1]]
                uv2 = all_uvs[li[2]]
                render_triangle_to_image(buf, texture_size, texture_size, uv0, uv1, uv2, rgba)

        # Close seam gaps and fill any remaining transparent pixels.
        # island_margin = 4/texture_size → 4px gap; 2 rounds fills it exactly.
        buf = close_gaps_in_texture(buf, texture_size, texture_size, uv_method="SMART",
                                    dilation_rounds=2)
        still_transparent = buf[:, :, 3] < 0.5
        if np.any(still_transparent):
            buf[still_transparent] = np.array([0.5, 0.5, 0.5, 1.0], dtype=np.float32)

    finally:
        # Clean up temp objects regardless of errors.
        for obj in temp_objs:
            bpy.data.objects.remove(obj, do_unlink=True)

    # --- Step 4: create one shared image ------------------------------------
    image = bpy.data.images.new("Shared_MMU_Paint", width=texture_size, height=texture_size, alpha=True)
    image.pixels.foreach_set(buf.ravel())
    image.pack()

    debug(
        f"Shared solid texture: {n} parts, {total_tris} tris → "
        f"{texture_size}px image"
    )

    # --- Step 5: assign materials (one per mesh, all share the image) -------
    for mesh, extruder_1based in parts:
        mat = bpy.data.materials.new(name=f"{mesh.name}_MMU_Paint")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        tex_node.location = (-300, 0)

        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (100, 0)

        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)

        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        mesh.materials.append(mat)
        mesh.polygons.foreach_set("material_index", [0] * len(mesh.polygons))

        mesh["3mf_paint_extruder_colors"] = str(extruder_colors_hex)
        mesh["3mf_paint_default_extruder"] = extruder_1based
        mesh["3mf_is_paint_texture"] = True
        if ctx.num_physical_filaments > 0:
            mesh["3mf_num_physical_filaments"] = ctx.num_physical_filaments

        ctx._paint_object_names.append(mesh.name)

    ctx.safe_report(
        {"INFO"},
        f"Created shared MMU paint texture ({texture_size}px) for {n} parts",
    )
    ctx.pending_solid_paint_parts.clear()


def render_seam_support_texture(
    ctx: "ImportContext",
    mesh: bpy.types.Mesh,
    seg_strings: Dict[int, str],
    layer_type: str,
) -> bool:
    """Render seam or support segmentation strings to a dedicated UV texture.

    Creates a separate UV layer and image for the given layer type.  The
    segmentation states map to: 0 = auto (background), 1 = enforced,
    2 = blocked.

    The image is added as an unlinked ``ShaderNodeTexImage`` node in the
    mesh's first material so it can be selected for texture painting.

    :param ctx: Import context.
    :param mesh: The Blender mesh to apply the texture to.
    :param seg_strings: Dict mapping face_index → segmentation hex string.
    :param layer_type: ``"SEAM"`` or ``"SUPPORT"``.
    :return: ``True`` if the texture was successfully rendered.
    """
    if not seg_strings:
        return False
    if ctx.options.import_materials != "PAINT":
        return False

    from .segmentation import render_segmentation_to_texture
    from ..paint.helpers import (
        _layer_colors, _layer_uv_name, _layer_flag_key, _layer_colors_key,
    )
    from ..common.colors import rgb_to_hex as _hex_from_rgb

    bg, enforce, block = _layer_colors(layer_type)
    uv_name = _layer_uv_name(layer_type)
    flag_key = _layer_flag_key(layer_type)
    colors_key = _layer_colors_key(layer_type)

    # Color map: segmentation state 1 → enforce, state 2 → block.
    # render_segmentation_to_texture uses ci = int(state) - 1 to index
    # color_table, so color_table[0] → state 1, color_table[1] → state 2.
    extruder_colors: Dict[int, list] = {
        0: [enforce[0], enforce[1], enforce[2], 1.0],
        1: [block[0], block[1], block[2], 1.0],
    }
    default_color = [bg[0], bg[1], bg[2], 1.0]

    temp_obj = bpy.data.objects.new(f"_temp_{layer_type.lower()}", mesh)
    bpy.context.collection.objects.link(temp_obj)

    try:
        # Texture size — same logic as color paint
        override_size = ctx.options.paint_texture_size
        tri_count = len(mesh.polygons)
        if override_size > 0:
            texture_size = override_size
        elif tri_count < 5000:
            texture_size = 2048
        elif tri_count < 20000:
            texture_size = 4096
        else:
            texture_size = 8192

        image_name = f"{mesh.name}_{uv_name}"
        debug(
            f"Rendering {layer_type} segmentation to {texture_size}x{texture_size} "
            f"UV texture for {tri_count} triangles"
        )

        image = render_segmentation_to_texture(
            temp_obj,
            seg_strings,
            extruder_colors,
            texture_size=texture_size,
            default_extruder=1,
            uv_method=ctx.options.paint_uv_method,
            uv_layer_name=uv_name,
            default_color_override=default_color,
            image_name_override=image_name,
            set_active_render=False,
            source_uv_layer_name="MMU_Paint",
        )

        # Add the image as an unlinked TEX_IMAGE node in the paint material
        # so _get_layer_image() can find it and texture painting can use it.
        if mesh.materials:
            mat = mesh.materials[0]
            if mat and mat.use_nodes:
                tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                tex_node.image = image
                tex_node.label = uv_name
                tex_node.name = uv_name
                tex_node.location = (-300, -300 if layer_type == "SEAM" else -500)

        # Store custom properties for round-trip export
        mesh[flag_key] = True
        color_dict = {
            1: _hex_from_rgb(*enforce),
            2: _hex_from_rgb(*block),
        }
        mesh[colors_key] = str(color_dict)

        debug(f"Successfully rendered {layer_type} paint data to UV texture")
        return True

    finally:
        bpy.data.objects.remove(temp_obj, do_unlink=True)


# ---------------------------------------------------------------------------
# assign_materials_to_mesh
# ---------------------------------------------------------------------------

def assign_materials_to_mesh(
    ctx: "ImportContext",
    mesh: bpy.types.Mesh,
    resource_object: ResourceObject,
) -> None:
    """Assign materials to mesh faces based on parsed triangle materials.

    Creates Blender materials as needed (including textured / PBR variants),
    caches them in ``ctx.resource_to_material``, and batch-assigns material
    indices via ``foreach_set``.

    :param ctx: Import context.
    :param mesh: The Blender mesh.
    :param resource_object: Source data with per-triangle material assignments.
    """
    from .materials import (
        find_existing_material,
        apply_pbr_to_principled,
        apply_pbr_textures_to_material,
        setup_textured_material,
        setup_multi_textured_material,
    )

    materials_to_index: Dict[ResourceMaterial, int] = {}
    material_indices = [0] * len(resource_object.materials)

    for triangle_index, triangle_material in enumerate(resource_object.materials):
        if triangle_material is None:
            continue

        if triangle_material not in ctx.resource_to_material:
            # Check for textured version already created from multiproperties
            found_textured_version = False
            if triangle_material.texture_id is None:
                for textured_mat, original_mat in ctx.textured_to_basematerial_map.items():
                    if original_mat == triangle_material and textured_mat in ctx.resource_to_material:
                        ctx.resource_to_material[triangle_material] = ctx.resource_to_material[textured_mat]
                        found_textured_version = True
                        debug(f"Reusing textured material for basematerial '{triangle_material.name}'")
                        break

            if not found_textured_version:
                material_name = str(triangle_material.name)

                # Try to reuse existing material
                material = None
                has_pbr_textures = (
                    triangle_material.basecolor_texid is not None
                    or triangle_material.metallic_texid is not None
                    or triangle_material.roughness_texid is not None
                    or triangle_material.specular_texid is not None
                    or triangle_material.glossiness_texid is not None
                )

                if (
                    ctx.options.reuse_materials
                    and triangle_material.texture_id is None
                    and not has_pbr_textures
                ):
                    material = find_existing_material(ctx, material_name, triangle_material.color)

                # Create new material if not found
                if material is None:
                    material = bpy.data.materials.new(material_name)
                    material.use_nodes = True

                    if triangle_material.texture_id is not None:
                        _setup_textured_blender_material(
                            ctx, material, triangle_material,
                            setup_textured_material, setup_multi_textured_material,
                            apply_pbr_to_principled, apply_pbr_textures_to_material,
                        )
                    else:
                        # Standard color-based material
                        principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
                            material, is_readonly=False
                        )
                        principled.base_color = triangle_material.color[:3]
                        principled.alpha = triangle_material.color[3]
                        apply_pbr_to_principled(ctx, principled, material, triangle_material)
                        apply_pbr_textures_to_material(ctx, material, triangle_material)

                ctx.resource_to_material[triangle_material] = material

                # Cache textured material under original basematerial key
                if triangle_material in ctx.textured_to_basematerial_map:
                    original = ctx.textured_to_basematerial_map[triangle_material]
                    if original not in ctx.resource_to_material:
                        ctx.resource_to_material[original] = material
                        debug("Cached textured material under original basematerial key")
        else:
            material = ctx.resource_to_material[triangle_material]

        # Add material to mesh if not yet present
        if triangle_material not in materials_to_index:
            new_index = len(mesh.materials.items())
            if new_index > 32767:
                warn("Blender doesn't support more than 32768 different materials per mesh.")
                continue
            mesh.materials.append(ctx.resource_to_material[triangle_material])
            materials_to_index[triangle_material] = new_index

        material_indices[triangle_index] = materials_to_index[triangle_material]

    # Batch assign
    if materials_to_index:
        mesh.polygons.foreach_set("material_index", material_indices)


def _setup_textured_blender_material(
    ctx, material, triangle_material,
    setup_textured_material, setup_multi_textured_material,
    apply_pbr_to_principled, apply_pbr_textures_to_material,
):
    """Wire up texture nodes for a textured ResourceMaterial."""
    all_textures = []
    all_tex_group_ids = [triangle_material.texture_id]
    extra_ids = getattr(triangle_material, "extra_texture_ids", None)
    if extra_ids:
        all_tex_group_ids.extend(extra_ids)

    for tg_id in all_tex_group_ids:
        tg = ctx.resource_texture_groups.get(tg_id)
        if tg:
            tex = ctx.resource_textures.get(tg.texid)
            if tex and tex.blender_image:
                all_textures.append(tex)

    if len(all_textures) > 1:
        setup_multi_textured_material(ctx, material, all_textures)
    elif len(all_textures) == 1:
        setup_textured_material(ctx, material, all_textures[0])
    else:
        warn(f"No valid textures found for texture groups {all_tex_group_ids}")

    principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
        material, is_readonly=False
    )
    apply_pbr_to_principled(ctx, principled, material, triangle_material)
    apply_pbr_textures_to_material(ctx, material, triangle_material, has_uv_layer=True)


# ---------------------------------------------------------------------------
# apply_triangle_sets
# ---------------------------------------------------------------------------

def apply_triangle_sets(
    mesh: bpy.types.Mesh,
    resource_object: ResourceObject,
) -> None:
    """Store triangle set membership as integer face attributes.

    :param mesh: The Blender mesh.
    :param resource_object: Source data with triangle set definitions.
    """
    if not resource_object.triangle_sets:
        return

    set_names = list(resource_object.triangle_sets.keys())
    if not set_names:
        return

    import json
    mesh["3mf_triangle_set_names"] = json.dumps(set_names)
    # Store original face count to detect topology changes before export
    mesh["3mf_original_face_count"] = len(mesh.polygons)

    attr_name = "3mf_triangle_set"
    if attr_name not in mesh.attributes:
        mesh.attributes.new(name=attr_name, type="INT", domain="FACE")

    num_faces = len(mesh.polygons)
    set_values = [0] * num_faces

    for set_idx, (set_name, triangle_indices) in enumerate(
        resource_object.triangle_sets.items(), start=1
    ):
        for tri_idx in triangle_indices:
            if 0 <= tri_idx < num_faces:
                set_values[tri_idx] = set_idx

    mesh.attributes[attr_name].data.foreach_set("value", set_values)

    # Mirror into Blender's sculpt face sets so users can view/edit in
    # Sculpt mode with the built-in face-set tools.
    fs_name = ".sculpt_face_set"
    if fs_name not in mesh.attributes:
        mesh.attributes.new(name=fs_name, type="INT", domain="FACE")
    mesh.attributes[fs_name].data.foreach_set("value", set_values)

    debug(f"Applied {len(resource_object.triangle_sets)} triangle sets as face attributes + sculpt face sets")


# ---------------------------------------------------------------------------
# apply_uv_coordinates
# ---------------------------------------------------------------------------

def apply_uv_coordinates(
    mesh: bpy.types.Mesh,
    resource_object: ResourceObject,
) -> None:
    """Create a UV layer from parsed texture coordinates.

    :param mesh: The Blender mesh.
    :param resource_object: Source data with per-triangle UV tuples.
    """
    if not resource_object.triangle_uvs:
        return

    uv_layer = mesh.uv_layers.new(name="UVMap")
    if not uv_layer:
        return

    uv_data: List[float] = []
    for tri_uvs in resource_object.triangle_uvs:
        if tri_uvs is not None:
            for uv in tri_uvs:
                uv_data.append(uv[0])
                uv_data.append(uv[1])
        else:
            for _ in range(3):
                uv_data.append(0.0)
                uv_data.append(0.0)

    uv_layer.data.foreach_set("uv", uv_data)
    debug(f"Applied UV coordinates to mesh ({len(resource_object.triangle_uvs)} triangles)")


# ---------------------------------------------------------------------------
# set_object_origin
# ---------------------------------------------------------------------------

def set_object_origin(
    blender_object: bpy.types.Object,
    origin_mode: str,
) -> None:
    """Adjust the object origin placement.

    :param blender_object: The Blender object.
    :param origin_mode: ``"KEEP"``, ``"CENTER"``, or ``"BOTTOM"``.
    """
    if origin_mode not in ("CENTER", "BOTTOM"):
        return

    mesh = blender_object.data

    previous_mode = bpy.context.object.mode if bpy.context.object else "OBJECT"
    if previous_mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    if origin_mode == "CENTER":
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
    elif origin_mode == "BOTTOM":
        bbox = blender_object.bound_box
        min_z = min(v[2] for v in bbox)
        center_x = (min(v[0] for v in bbox) + max(v[0] for v in bbox)) / 2
        center_y = (min(v[1] for v in bbox) + max(v[1] for v in bbox)) / 2
        bottom_center = mathutils.Vector((center_x, center_y, min_z))

        mesh.transform(mathutils.Matrix.Translation(-bottom_center))
        blender_object.location += bottom_center

    if previous_mode != "OBJECT":
        bpy.ops.object.mode_set(mode=previous_mode)


# ---------------------------------------------------------------------------
# apply_import_location
# ---------------------------------------------------------------------------

def apply_import_location(
    transformation: mathutils.Matrix,
    import_location: str,
) -> mathutils.Matrix:
    """Modify the transformation matrix based on the import location setting.

    :param transformation: The original transformation matrix.
    :param import_location: ``"ORIGIN"``, ``"CURSOR"``, ``"GRID"``, or ``"KEEP"``.
    :return: Modified transformation matrix.
    """
    if import_location == "ORIGIN":
        transformation = transformation.copy()
        transformation.translation = mathutils.Vector((0, 0, 0))
    elif import_location == "CURSOR":
        transformation = transformation.copy()
        transformation.translation = bpy.context.scene.cursor.location
    elif import_location == "GRID":
        transformation = transformation.copy()
        transformation.translation = mathutils.Vector((0, 0, 0))
    # else "KEEP" — no change
    return transformation


# ---------------------------------------------------------------------------
# apply_grid_layout
# ---------------------------------------------------------------------------

def apply_grid_layout(
    objects: List[bpy.types.Object],
    grid_spacing: float = 0.1,
) -> None:
    """Arrange imported objects in a grid pattern.

    Lays objects out in rows along X, wrapping to new rows along Y.

    :param objects: List of Blender objects to arrange.
    :param grid_spacing: Gap between objects in scene units.
    """
    import math

    if not objects or len(objects) <= 1:
        if objects:
            debug("Grid layout: single object, placed at origin")
        return

    # Calculate bounding boxes
    object_bounds = []
    for obj in objects:
        bbox = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
        min_corner = mathutils.Vector(
            (min(v.x for v in bbox), min(v.y for v in bbox), min(v.z for v in bbox))
        )
        max_corner = mathutils.Vector(
            (max(v.x for v in bbox), max(v.y for v in bbox), max(v.z for v in bbox))
        )
        size = max_corner - min_corner
        object_bounds.append({"obj": obj, "size": size, "min": min_corner, "max": max_corner})

    num_objects = len(objects)
    cols = math.ceil(math.sqrt(num_objects))
    rows = math.ceil(num_objects / cols)

    col_widths = []
    row_heights = []

    for col in range(cols):
        col_objs = [object_bounds[i] for i in range(col, num_objects, cols)]
        col_widths.append(max(b["size"].x for b in col_objs) if col_objs else 0)

    for row in range(rows):
        start_idx = row * cols
        end_idx = min(start_idx + cols, num_objects)
        row_objs = object_bounds[start_idx:end_idx]
        row_heights.append(max(b["size"].y for b in row_objs) if row_objs else 0)

    current_y = 0.0
    for row in range(rows):
        current_x = 0.0
        for col in range(cols):
            idx = row * cols + col
            if idx >= num_objects:
                break

            bounds = object_bounds[idx]
            obj = bounds["obj"]

            cell_center_x = current_x + col_widths[col] / 2
            cell_center_y = current_y + row_heights[row] / 2

            obj_center_x = (bounds["min"].x + bounds["max"].x) / 2
            obj_center_y = (bounds["min"].y + bounds["max"].y) / 2

            offset = mathutils.Vector((
                cell_center_x - obj_center_x,
                cell_center_y - obj_center_y,
                -bounds["min"].z,
            ))
            obj.location += offset

            current_x += col_widths[col] + grid_spacing
        current_y += row_heights[row] + grid_spacing

    debug(f"Grid layout: arranged {num_objects} objects in {rows}x{cols} grid")
