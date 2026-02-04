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
Texture import functionality for 3MF Materials Extension.

This module handles:
- Reading texture2d elements (image metadata)
- Reading texture2dgroup elements (UV coordinate sets)
- Extracting texture images from archives
- Setting up Blender materials with texture nodes
"""

import logging
import os
import tempfile
import zipfile
from typing import Dict, Optional, TYPE_CHECKING

import bpy

if TYPE_CHECKING:
    from ..import_3mf import Import3MF, ResourceTexture, ResourceMaterial, ResourceTextureGroup

log = logging.getLogger(__name__)


def read_textures(op: 'Import3MF', root, material_ns: Dict[str, str]) -> None:
    """
    Parse <m:texture2d> elements from the 3MF document.

    Texture2D elements define image resources within the archive.
    Per 3MF Materials Extension spec:
    - path: Required. Path to image file in archive
    - contenttype: Required. "image/png" or "image/jpeg"
    - tilestyleu, tilestylev: Optional. "wrap" (default), "mirror", "clamp", "none"
    - filter: Optional. "auto" (default), "linear", "nearest"

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    """
    from ..constants import MODEL_NAMESPACES
    from ..import_3mf import ResourceTexture

    for texture_item in root.iterfind(
        "./3mf:resources/m:texture2d",
        {**MODEL_NAMESPACES, **material_ns}
    ):
        try:
            texture_id = texture_item.attrib["id"]
        except KeyError:
            log.warning("Encountered a texture2d without resource ID.")
            op.safe_report({'WARNING'}, "Encountered a texture2d without resource ID")
            continue

        if texture_id in op.resource_textures:
            log.warning(f"Duplicate texture ID: {texture_id}")
            continue

        # Required attributes
        try:
            path = texture_item.attrib["path"]
            contenttype = texture_item.attrib["contenttype"]
        except KeyError as e:
            log.warning(f"Texture {texture_id} missing required attribute: {e}")
            continue

        # Validate content type
        if contenttype not in ("image/png", "image/jpeg"):
            log.warning(f"Texture {texture_id} has unsupported contenttype: {contenttype}")
            continue

        # Optional attributes with defaults
        tilestyleu = texture_item.attrib.get("tilestyleu", "wrap")
        tilestylev = texture_item.attrib.get("tilestylev", "wrap")
        filter_mode = texture_item.attrib.get("filter", "auto")

        op.resource_textures[texture_id] = ResourceTexture(
            path=path,
            contenttype=contenttype,
            tilestyleu=tilestyleu,
            tilestylev=tilestylev,
            filter=filter_mode,
            blender_image=None
        )
        log.debug(f"Parsed texture2d {texture_id}: {path} ({contenttype})")

    if op.resource_textures:
        log.info(f"Found {len(op.resource_textures)} texture2d resources")


def read_texture_groups(op: 'Import3MF', root, material_ns: Dict[str, str],
                        display_properties: Dict) -> None:
    """
    Parse <m:texture2dgroup> elements from the 3MF document.

    Texture2DGroup elements contain UV coordinate sets and reference a texture2d.

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    :param display_properties: Parsed PBR display properties lookup.
    """
    from ..constants import MODEL_NAMESPACES
    from ..import_3mf import ResourceTextureGroup

    for group_item in root.iterfind(
        "./3mf:resources/m:texture2dgroup",
        {**MODEL_NAMESPACES, **material_ns}
    ):
        try:
            group_id = group_item.attrib["id"]
        except KeyError:
            log.warning("Encountered a texture2dgroup without resource ID.")
            op.safe_report({'WARNING'}, "Encountered a texture2dgroup without resource ID")
            continue

        if group_id in op.resource_texture_groups:
            log.warning(f"Duplicate texture2dgroup ID: {group_id}")
            continue

        try:
            texid = group_item.attrib["texid"]
        except KeyError:
            log.warning(f"Texture2dgroup {group_id} missing required texid attribute")
            continue

        if texid not in op.resource_textures:
            log.warning(f"Texture2dgroup {group_id} references unknown texture: {texid}")
            continue

        display_props_id = group_item.attrib.get("displaypropertiesid")

        tex2coords = []
        for coord_item in group_item.iterfind("./m:tex2coord", material_ns):
            try:
                u = float(coord_item.attrib.get("u", "0"))
                v = float(coord_item.attrib.get("v", "0"))
                tex2coords.append((u, v))
            except (ValueError, KeyError) as e:
                log.warning(f"Invalid tex2coord in group {group_id}: {e}")
                tex2coords.append((0.0, 0.0))

        if not tex2coords:
            log.warning(f"Texture2dgroup {group_id} has no tex2coords")
            continue

        op.resource_texture_groups[group_id] = ResourceTextureGroup(
            texid=texid,
            tex2coords=tex2coords,
            displaypropertiesid=display_props_id
        )
        log.debug(f"Parsed texture2dgroup {group_id}: {len(tex2coords)} UVs referencing texture {texid}")

    if op.resource_texture_groups:
        log.info(f"Found {len(op.resource_texture_groups)} texture2dgroup resources")


def extract_textures_from_archive(op: 'Import3MF', archive_path: str) -> None:
    """
    Extract texture images from the 3MF archive and create Blender images.

    Textures are extracted from paths defined in texture2d elements and loaded
    as Blender images. The images are packed into the blend file for portability.

    :param op: The Import3MF operator instance.
    :param archive_path: Path to the 3MF archive file.
    """
    from ..import_3mf import ResourceTexture

    if not op.resource_textures:
        return

    if not op.import_materials:
        return

    try:
        with zipfile.ZipFile(archive_path, 'r') as archive:
            archive_files = archive.namelist()

            for texture_id, texture in list(op.resource_textures.items()):
                tex_path = texture.path.lstrip('/')

                if tex_path not in archive_files:
                    log.warning(f"Texture file not found in archive: {tex_path}")
                    continue

                try:
                    texture_data = archive.read(tex_path)

                    image_name = os.path.basename(tex_path)
                    base_name, ext = os.path.splitext(image_name)
                    counter = 1
                    while image_name in bpy.data.images:
                        image_name = f"{base_name}_{counter}{ext}"
                        counter += 1

                    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                        tmp.write(texture_data)
                        tmp_path = tmp.name

                    try:
                        blender_image = bpy.data.images.load(tmp_path)
                        blender_image.name = image_name
                        blender_image.pack()

                        blender_image["3mf_path"] = texture.path
                        blender_image["3mf_tilestyleu"] = texture.tilestyleu
                        blender_image["3mf_tilestylev"] = texture.tilestylev
                        blender_image["3mf_filter"] = texture.filter

                        op.resource_textures[texture_id] = ResourceTexture(
                            path=texture.path,
                            contenttype=texture.contenttype,
                            tilestyleu=texture.tilestyleu,
                            tilestylev=texture.tilestylev,
                            filter=texture.filter,
                            blender_image=blender_image
                        )

                        log.info(f"Loaded texture {texture_id}: {image_name}")

                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

                except Exception as e:
                    log.warning(f"Failed to extract texture {texture_id} ({tex_path}): {e}")
                    continue

    except (zipfile.BadZipFile, IOError) as e:
        log.error(f"Failed to read textures from archive: {e}")


def get_or_create_textured_material(op: 'Import3MF', texture_group_id: str,
                                    texture_group: 'ResourceTextureGroup') -> Optional['ResourceMaterial']:
    """
    Get or create a ResourceMaterial for a texture group.

    :param op: The Import3MF operator instance.
    :param texture_group_id: The ID of the texture2dgroup.
    :param texture_group: The ResourceTextureGroup data.
    :return: ResourceMaterial for this texture, or None if texture not available.
    """
    from ..import_3mf import ResourceMaterial

    # Get the texture this group references
    texture = op.resource_textures.get(texture_group.texid)
    if not texture:
        return None

    # Generate a material name based on the texture
    material_name = f"Texture_{texture_group_id}"

    return ResourceMaterial(
        name=material_name,
        color=(1.0, 1.0, 1.0, 1.0),
        metallic=None,
        roughness=None,
        specular_color=None,
        glossiness=None,
        ior=None,
        attenuation=None,
        transmission=None,
        texture_id=texture_group_id,
        metallic_texid=None,
        roughness_texid=None,
        specular_texid=None,
        glossiness_texid=None,
        basecolor_texid=None,
    )


def setup_textured_material(op: 'Import3MF', material: bpy.types.Material,
                            texture: 'ResourceTexture') -> None:
    """
    Set up a Blender material with an Image Texture node for 3MF texture support.

    Creates a node tree with Image Texture -> Principled BSDF connection.

    :param op: The Import3MF operator instance.
    :param material: The Blender material to configure.
    :param texture: The ResourceTexture containing the Blender image.
    """
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links

    nodes.clear()

    principled = nodes.new('ShaderNodeBsdfPrincipled')
    principled.location = (0, 0)

    output = nodes.new('ShaderNodeOutputMaterial')
    output.location = (300, 0)

    tex_node = nodes.new('ShaderNodeTexImage')
    tex_node.location = (-300, 0)
    tex_node.image = texture.blender_image

    # Set texture extension mode based on tilestyle
    if texture.tilestyleu == "clamp" or texture.tilestylev == "clamp":
        tex_node.extension = 'CLIP'
    elif texture.tilestyleu == "mirror" or texture.tilestylev == "mirror":
        tex_node.extension = 'EXTEND'
    else:
        tex_node.extension = 'REPEAT'

    # Set interpolation based on filter
    if texture.filter == "nearest":
        tex_node.interpolation = 'Closest'
    else:
        tex_node.interpolation = 'Linear'

    links.new(tex_node.outputs['Color'], principled.inputs['Base Color'])
    links.new(principled.outputs['BSDF'], output.inputs['Surface'])

    material["3mf_texture_tilestyleu"] = texture.tilestyleu or "wrap"
    material["3mf_texture_tilestylev"] = texture.tilestylev or "wrap"
    material["3mf_texture_filter"] = texture.filter or "auto"
    material["3mf_texture_path"] = texture.path

    log.info(f"Created textured material with image '{texture.blender_image.name}'")
