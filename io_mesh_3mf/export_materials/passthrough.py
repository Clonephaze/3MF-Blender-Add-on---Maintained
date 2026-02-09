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
Passthrough material export for 3MF round-trip support.

Handles writing back material data that was imported but not visually interpreted:
- compositematerials
- multiproperties
- colorgroups
- texture2d and texture2dgroup (passthrough)
- PBR display properties (passthrough)
- PBR textured display properties (passthrough)

IDs are remapped to avoid conflicts with newly created materials.
"""

import json
import os
import tempfile
import xml.etree.ElementTree
import zipfile
from typing import Dict, Tuple

import bpy

from ..constants import MATERIAL_NAMESPACE, MODEL_NAMESPACE
from ..utilities import debug, warn, error


def write_passthrough_textures_to_archive(archive: zipfile.ZipFile) -> Dict[str, str]:
    """
    Write passthrough texture images from Blender data to the 3MF archive.

    This writes the actual image data for texture2d elements that were imported
    and stored in the scene's passthrough data. The images are retrieved from
    bpy.data.images using the stored blender_image name.

    Must be called BEFORE write_passthrough_materials() so that image files
    exist in the archive before their XML references are written.

    :param archive: The 3MF zip archive to write to.
    :return: Dict mapping archive path -> content type for relationship writing.
    """
    scene = bpy.context.scene
    stored_data = scene.get("3mf_textures")
    if not stored_data:
        return {}

    try:
        texture_data = json.loads(stored_data)
    except json.JSONDecodeError:
        warn("Failed to parse stored textures data for image writing")
        return {}

    image_paths = {}

    for res_id, tex in texture_data.items():
        path = tex.get("path", "")
        blender_image_name = tex.get("blender_image")
        if not path or not blender_image_name:
            continue

        # Normalize the archive path (remove leading /)
        archive_path = path.lstrip("/")

        # Skip if already written (e.g., by the standard texture pipeline)
        existing_names = {info.filename for info in archive.infolist()}
        if archive_path in existing_names:
            debug(f"Passthrough texture '{archive_path}' already in archive, skipping")
            continue

        # Find the Blender image
        image = bpy.data.images.get(blender_image_name)
        if image is None:
            warn(f"Passthrough texture image '{blender_image_name}' not found in bpy.data.images")
            continue

        # Determine format from path
        ext = os.path.splitext(archive_path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            file_format = "JPEG"
        else:
            file_format = "PNG"

        try:
            # Try saving via Blender's image save API
            with tempfile.NamedTemporaryFile(suffix=ext or ".png", delete=False) as tmp:
                tmp_path = tmp.name

            original_filepath = image.filepath_raw
            image.filepath_raw = tmp_path
            image.file_format = file_format
            image.save()
            image.filepath_raw = original_filepath

            archive.write(tmp_path, archive_path)
            os.unlink(tmp_path)

            image_paths[archive_path] = tex.get("contenttype", "image/png")
            debug(f"Wrote passthrough texture image '{blender_image_name}' to {archive_path}")

        except Exception as e:
            warn(f"Failed to write passthrough texture '{blender_image_name}': {e}")
            # Fallback: try packed file data
            if image.packed_file:
                try:
                    archive.writestr(archive_path, image.packed_file.data)
                    image_paths[archive_path] = tex.get("contenttype", "image/png")
                    debug(f"Wrote packed passthrough texture '{blender_image_name}' to {archive_path}")
                except Exception as e2:
                    error(f"Failed to write packed passthrough texture '{blender_image_name}': {e2}")

    if image_paths:
        debug(f"Wrote {len(image_paths)} passthrough texture images to archive")

    return image_paths


def write_passthrough_materials(
    resources_element: xml.etree.ElementTree.Element, next_resource_id: int
) -> Tuple[int, bool, Dict[str, str]]:
    """
    Write stored passthrough material data from scene custom properties.

    This writes back compositematerials, multiproperties, textured PBR
    display properties, colorgroups, and non-textured PBR display properties
    that were imported but not visually interpreted.

    IDs are remapped to avoid conflicts with newly created materials.

    :param resources_element: The <resources> element
    :param next_resource_id: Next available resource ID
    :return: Tuple of (updated next_resource_id, whether any passthrough data was written,
             id_remap dict mapping original IDs to new IDs)
    """
    scene = bpy.context.scene
    any_written = False

    # Check if any passthrough data exists
    has_composites = bool(scene.get("3mf_compositematerials"))
    has_multiprops = bool(scene.get("3mf_multiproperties"))
    has_pbr_tex = bool(scene.get("3mf_pbr_texture_displays"))
    has_colorgroups = bool(scene.get("3mf_colorgroups"))
    has_pbr_display = bool(scene.get("3mf_pbr_display_props"))
    has_textures = bool(scene.get("3mf_textures"))
    has_tex_groups = bool(scene.get("3mf_texture_groups"))

    if (
        has_composites
        or has_multiprops
        or has_pbr_tex
        or has_colorgroups
        or has_pbr_display
        or has_textures
        or has_tex_groups
    ):
        any_written = True
    else:
        return next_resource_id, False, {}

    # Build ID remap table: original_id -> new_id
    # This prevents conflicts with newly created materials
    # Only remap IDs that would conflict with IDs < next_resource_id
    id_remap = {}

    # Collect all original IDs that need remapping
    original_ids = set()

    if has_textures:
        try:
            tex_data = json.loads(scene.get("3mf_textures", "{}"))
            original_ids.update(tex_data.keys())
        except json.JSONDecodeError:
            pass

    if has_tex_groups:
        try:
            group_data = json.loads(scene.get("3mf_texture_groups", "{}"))
            original_ids.update(group_data.keys())
        except json.JSONDecodeError:
            pass

    if has_colorgroups:
        try:
            cg_data = json.loads(scene.get("3mf_colorgroups", "{}"))
            original_ids.update(cg_data.keys())
        except json.JSONDecodeError:
            pass

    if has_pbr_display:
        try:
            pbr_data = json.loads(scene.get("3mf_pbr_display_props", "{}"))
            original_ids.update(pbr_data.keys())
        except json.JSONDecodeError:
            pass

    if has_composites:
        try:
            comp_data = json.loads(scene.get("3mf_compositematerials", "{}"))
            original_ids.update(comp_data.keys())
        except json.JSONDecodeError:
            pass

    if has_multiprops:
        try:
            mp_data = json.loads(scene.get("3mf_multiproperties", "{}"))
            original_ids.update(mp_data.keys())
        except json.JSONDecodeError:
            pass

    if has_pbr_tex:
        try:
            pbr_tex_data = json.loads(scene.get("3mf_pbr_texture_displays", "{}"))
            original_ids.update(pbr_tex_data.keys())
        except json.JSONDecodeError:
            pass

    # Find IDs that would conflict with newly created materials (IDs 1 to next_resource_id-1)
    conflicting_ids = set()
    non_conflicting_int_ids = set()
    for orig_id in original_ids:
        try:
            id_int = int(orig_id)
            if id_int < next_resource_id:
                conflicting_ids.add(orig_id)
            else:
                non_conflicting_int_ids.add(id_int)
        except ValueError:
            pass

    # Only remap conflicting IDs, assign them new unique IDs starting from next_resource_id
    # Skip over IDs that are already used by non-conflicting original IDs
    if conflicting_ids:
        for orig_id in sorted(conflicting_ids, key=lambda x: int(x) if x.isdigit() else 0):
            while next_resource_id in non_conflicting_int_ids:
                next_resource_id += 1
            id_remap[orig_id] = str(next_resource_id)
            next_resource_id += 1
        debug(f"Remapped {len(conflicting_ids)} conflicting passthrough IDs: {id_remap}")

    # Update next_resource_id to account for non-conflicting original IDs
    # This ensures objects don't use IDs that overlap with passthrough
    max_original_id = max((int(x) for x in original_ids if x.isdigit()), default=0)
    if max_original_id >= next_resource_id:
        next_resource_id = max_original_id + 1

    # Write textures first (they may be referenced by other elements)
    _write_passthrough_textures(resources_element, scene, id_remap)

    # Write texture groups (referenced by multiproperties)
    _write_passthrough_texture_groups(resources_element, scene, id_remap)

    # Write colorgroups
    _write_passthrough_colorgroups(resources_element, scene, id_remap)

    # Write non-textured PBR display properties
    _write_passthrough_pbr_display(resources_element, scene, id_remap)

    # Write compositematerials
    _write_passthrough_composites(resources_element, scene, id_remap)

    # Write multiproperties
    _write_passthrough_multiproperties(resources_element, scene, id_remap)

    # Write textured PBR display properties
    _write_passthrough_pbr_textures(resources_element, scene, id_remap)

    return next_resource_id, any_written, id_remap


def _write_passthrough_composites(
    resources_element: xml.etree.ElementTree.Element,
    scene: bpy.types.Scene,
    id_remap: Dict[str, str],
) -> None:
    """
    Write stored compositematerials to XML.

    :param resources_element: The <resources> element
    :param scene: Blender scene with stored data
    :param id_remap: Mapping from original IDs to new IDs
    """
    stored_data = scene.get("3mf_compositematerials")
    if not stored_data:
        return

    try:
        composite_data = json.loads(stored_data)
    except json.JSONDecodeError:
        warn("Failed to parse stored compositematerials data")
        return

    for res_id, comp in composite_data.items():
        new_id = id_remap.get(res_id, res_id)
        attrib = {
            "id": new_id,
            "matid": id_remap.get(comp["matid"], comp["matid"]),
            "matindices": comp["matindices"],
        }
        if comp.get("displaypropertiesid"):
            attrib["displaypropertiesid"] = id_remap.get(comp["displaypropertiesid"], comp["displaypropertiesid"])

        comp_element = xml.etree.ElementTree.SubElement(
            resources_element,
            f"{{{MATERIAL_NAMESPACE}}}compositematerials",
            attrib=attrib,
        )

        # Write composite children
        for c in comp.get("composites", []):
            xml.etree.ElementTree.SubElement(
                comp_element,
                f"{{{MATERIAL_NAMESPACE}}}composite",
                attrib={"values": c.get("values", "")},
            )

        debug(f"Wrote passthrough compositematerials {res_id} -> {new_id}")

    debug(f"Wrote {len(composite_data)} passthrough compositematerials")


def _write_passthrough_textures(
    resources_element: xml.etree.ElementTree.Element,
    scene: bpy.types.Scene,
    id_remap: Dict[str, str],
) -> None:
    """
    Write stored texture2d elements to XML.

    :param resources_element: The <resources> element
    :param scene: Blender scene with stored data
    :param id_remap: Mapping from original IDs to new IDs
    """
    stored_data = scene.get("3mf_textures")
    if not stored_data:
        return

    try:
        texture_data = json.loads(stored_data)
    except json.JSONDecodeError:
        warn("Failed to parse stored textures data")
        return

    for res_id, tex in texture_data.items():
        new_id = id_remap.get(res_id, res_id)
        attrib = {
            "id": new_id,
            "path": tex.get("path", ""),
            "contenttype": tex.get("contenttype", "image/png"),
        }
        # Add optional attributes if not default
        if tex.get("tilestyleu") and tex.get("tilestyleu") != "wrap":
            attrib["tilestyleu"] = tex["tilestyleu"]
        if tex.get("tilestylev") and tex.get("tilestylev") != "wrap":
            attrib["tilestylev"] = tex["tilestylev"]
        if tex.get("filter") and tex.get("filter") != "auto":
            attrib["filter"] = tex["filter"]

        xml.etree.ElementTree.SubElement(
            resources_element,
            f"{{{MATERIAL_NAMESPACE}}}texture2d",
            attrib=attrib,
        )

        debug(f"Wrote passthrough texture2d {res_id} -> {new_id}")

    debug(f"Wrote {len(texture_data)} passthrough textures")


def _write_passthrough_texture_groups(
    resources_element: xml.etree.ElementTree.Element,
    scene: bpy.types.Scene,
    id_remap: Dict[str, str],
) -> None:
    """
    Write stored texture2dgroup elements to XML.

    :param resources_element: The <resources> element
    :param scene: Blender scene with stored data
    :param id_remap: Mapping from original IDs to new IDs
    """
    stored_data = scene.get("3mf_texture_groups")
    if not stored_data:
        return

    try:
        texgroup_data = json.loads(stored_data)
    except json.JSONDecodeError:
        warn("Failed to parse stored texture groups data")
        return

    for res_id, tg in texgroup_data.items():
        new_id = id_remap.get(res_id, res_id)
        texid = tg.get("texid", "")
        attrib = {
            "id": new_id,
            "texid": id_remap.get(texid, texid),
        }
        if tg.get("displaypropertiesid"):
            dp_id = tg["displaypropertiesid"]
            attrib["displaypropertiesid"] = id_remap.get(dp_id, dp_id)

        group_element = xml.etree.ElementTree.SubElement(
            resources_element,
            f"{{{MATERIAL_NAMESPACE}}}texture2dgroup",
            attrib=attrib,
        )

        # Write tex2coord children
        for coord in tg.get("tex2coords", []):
            if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                xml.etree.ElementTree.SubElement(
                    group_element,
                    f"{{{MATERIAL_NAMESPACE}}}tex2coord",
                    attrib={"u": str(coord[0]), "v": str(coord[1])},
                )

        debug(f"Wrote passthrough texture2dgroup {res_id} -> {new_id}")

    debug(f"Wrote {len(texgroup_data)} passthrough texture groups")


def _write_passthrough_colorgroups(
    resources_element: xml.etree.ElementTree.Element,
    scene: bpy.types.Scene,
    id_remap: Dict[str, str],
) -> None:
    """
    Write stored colorgroup elements to XML.

    :param resources_element: The <resources> element
    :param scene: Blender scene with stored data
    :param id_remap: Mapping from original IDs to new IDs
    """
    stored_data = scene.get("3mf_colorgroups")
    if not stored_data:
        return

    try:
        colorgroup_data = json.loads(stored_data)
    except json.JSONDecodeError:
        warn("Failed to parse stored colorgroups data")
        return

    for res_id, cg in colorgroup_data.items():
        new_id = id_remap.get(res_id, res_id)
        attrib = {"id": new_id}
        if cg.get("displaypropertiesid"):
            dp_id = cg["displaypropertiesid"]
            attrib["displaypropertiesid"] = id_remap.get(dp_id, dp_id)

        group_element = xml.etree.ElementTree.SubElement(
            resources_element,
            f"{{{MATERIAL_NAMESPACE}}}colorgroup",
            attrib=attrib,
        )

        # Write color children
        for color in cg.get("colors", []):
            xml.etree.ElementTree.SubElement(
                group_element,
                f"{{{MATERIAL_NAMESPACE}}}color",
                attrib={"color": color},
            )

        debug(f"Wrote passthrough colorgroup {res_id} -> {new_id}")

    debug(f"Wrote {len(colorgroup_data)} passthrough colorgroups")


def _write_passthrough_pbr_display(
    resources_element: xml.etree.ElementTree.Element,
    scene: bpy.types.Scene,
    id_remap: Dict[str, str],
) -> None:
    """
    Write stored non-textured PBR display properties to XML.

    :param resources_element: The <resources> element
    :param scene: Blender scene with stored data
    :param id_remap: Mapping from original IDs to new IDs
    """
    stored_data = scene.get("3mf_pbr_display_props")
    if not stored_data:
        return

    try:
        pbr_data = json.loads(stored_data)
    except json.JSONDecodeError:
        warn("Failed to parse stored PBR display props data")
        return

    for res_id, prop in pbr_data.items():
        new_id = id_remap.get(res_id, res_id)
        prop_type = prop.get("type", "metallic")
        properties = prop.get("properties", [])

        if prop_type == "metallic":
            element_name = f"{{{MATERIAL_NAMESPACE}}}pbmetallicdisplayproperties"
            child_name = f"{{{MATERIAL_NAMESPACE}}}pbmetallic"
        elif prop_type == "specular":
            element_name = f"{{{MATERIAL_NAMESPACE}}}pbspeculardisplayproperties"
            child_name = f"{{{MATERIAL_NAMESPACE}}}pbspecular"
        elif prop_type == "translucent":
            element_name = f"{{{MATERIAL_NAMESPACE}}}translucentdisplayproperties"
            child_name = f"{{{MATERIAL_NAMESPACE}}}translucent"
        else:
            warn(f"Unknown PBR display property type: {prop_type}")
            continue

        display_element = xml.etree.ElementTree.SubElement(
            resources_element,
            element_name,
            attrib={"id": new_id},
        )

        # Write child elements with their raw attributes
        for prop_dict in properties:
            xml.etree.ElementTree.SubElement(
                display_element,
                child_name,
                attrib=prop_dict,
            )

        debug(f"Wrote passthrough {prop_type} PBR display properties {res_id} -> {new_id}")

    debug(f"Wrote {len(pbr_data)} passthrough PBR display properties")


def _write_passthrough_multiproperties(
    resources_element: xml.etree.ElementTree.Element,
    scene: bpy.types.Scene,
    id_remap: Dict[str, str],
) -> None:
    """
    Write stored multiproperties to XML.

    :param resources_element: The <resources> element
    :param scene: Blender scene with stored data
    :param id_remap: Mapping from original IDs to new IDs
    """
    stored_data = scene.get("3mf_multiproperties")
    if not stored_data:
        return

    try:
        multi_data = json.loads(stored_data)
    except json.JSONDecodeError:
        warn("Failed to parse stored multiproperties data")
        return

    for res_id, multi in multi_data.items():
        new_id = id_remap.get(res_id, res_id)
        # Remap pids - space-separated list of resource IDs
        orig_pids = multi["pids"].split()
        remapped_pids = " ".join(id_remap.get(pid, pid) for pid in orig_pids)

        attrib = {
            "id": new_id,
            "pids": remapped_pids,
        }
        if multi.get("blendmethods"):
            attrib["blendmethods"] = multi["blendmethods"]

        multi_element = xml.etree.ElementTree.SubElement(
            resources_element,
            f"{{{MATERIAL_NAMESPACE}}}multiproperties",
            attrib=attrib,
        )

        # Write multi children
        # Note: <multi> elements are in the core namespace, not materials namespace
        for m in multi.get("multis", []):
            xml.etree.ElementTree.SubElement(
                multi_element,
                f"{{{MODEL_NAMESPACE}}}multi",
                attrib={"pindices": m.get("pindices", "")},
            )

        debug(f"Wrote passthrough multiproperties {res_id} -> {new_id}")

    debug(f"Wrote {len(multi_data)} passthrough multiproperties")


def _write_passthrough_pbr_textures(
    resources_element: xml.etree.ElementTree.Element,
    scene: bpy.types.Scene,
    id_remap: Dict[str, str],
) -> None:
    """
    Write stored textured PBR display properties to XML.

    :param resources_element: The <resources> element
    :param scene: Blender scene with stored data
    :param id_remap: Mapping from original IDs to new IDs
    """
    stored_data = scene.get("3mf_pbr_texture_displays")
    if not stored_data:
        return

    try:
        pbr_data = json.loads(stored_data)
    except json.JSONDecodeError:
        warn("Failed to parse stored PBR texture displays data")
        return

    for res_id, prop in pbr_data.items():
        new_id = id_remap.get(res_id, res_id)
        prop_type = prop.get("type", "specular")
        factors = prop.get("factors", {})

        if prop_type == "specular":
            primary_tex = prop.get("primary_texid", "")
            secondary_tex = prop.get("secondary_texid", "")
            diffuse_tex = prop.get("basecolor_texid", "")  # diffusetextureid in specular workflow
            attrib = {
                "id": new_id,
                "name": prop.get("name", ""),
            }
            # Only include texture IDs if they have values
            if primary_tex:
                attrib["speculartextureid"] = id_remap.get(primary_tex, primary_tex)
            if secondary_tex:
                attrib["glossinesstextureid"] = id_remap.get(secondary_tex, secondary_tex)
            if diffuse_tex:
                attrib["diffusetextureid"] = id_remap.get(diffuse_tex, diffuse_tex)
            # Add factor attributes
            for factor_name, factor_value in factors.items():
                attrib[factor_name] = factor_value

            xml.etree.ElementTree.SubElement(
                resources_element,
                f"{{{MATERIAL_NAMESPACE}}}pbspeculartexturedisplayproperties",
                attrib=attrib,
            )
        elif prop_type == "metallic":
            primary_tex = prop.get("primary_texid", "")
            secondary_tex = prop.get("secondary_texid", "")
            basecolor_tex = prop.get("basecolor_texid", "")
            attrib = {
                "id": new_id,
                "name": prop.get("name", ""),
            }
            # Only include texture IDs if they have values
            if primary_tex:
                attrib["metallictextureid"] = id_remap.get(primary_tex, primary_tex)
            if secondary_tex:
                attrib["roughnesstextureid"] = id_remap.get(secondary_tex, secondary_tex)
            if basecolor_tex:
                attrib["basecolortextureid"] = id_remap.get(basecolor_tex, basecolor_tex)
            # Add factor attributes
            for factor_name, factor_value in factors.items():
                attrib[factor_name] = factor_value

            xml.etree.ElementTree.SubElement(
                resources_element,
                f"{{{MATERIAL_NAMESPACE}}}pbmetallictexturedisplayproperties",
                attrib=attrib,
            )

        debug(f"Wrote passthrough {prop_type} PBR texture display {res_id} -> {new_id}")

    debug(f"Wrote {len(pbr_data)} passthrough PBR texture displays")
