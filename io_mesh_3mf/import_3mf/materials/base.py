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
Base material import functionality for 3MF files.

This module handles:
- Reading basematerials and colorgroup elements
- Color parsing (hex to RGBA, sRGB to linear)
- Material reuse and finding existing materials
"""

from typing import Tuple, Optional, TYPE_CHECKING

import bpy
import bpy_extras.node_shader_utils

from ...common import debug, warn

if TYPE_CHECKING:
    pass  # Import3MF will be in import_3mf.operator once created


def srgb_to_linear(value: float) -> float:
    """
    Convert sRGB color component to linear color space.

    Blender materials use linear color space internally.

    :param value: sRGB value (0.0-1.0)
    :return: Linear value (0.0-1.0)
    """
    if value <= 0.04045:
        return value / 12.92
    else:
        return pow((value + 0.055) / 1.055, 2.4)


def parse_hex_color(hex_color: str) -> Tuple[float, float, float, float]:
    """
    Parse a hex color string to RGBA tuple in **linear** color space.

    Hex colors are sRGB.  The RGB components are converted to linear via
    ``srgb_to_linear()`` so the result can be assigned directly to Blender
    material properties (e.g. ``principled.base_color``).

    Alpha is NOT converted (it is linear in both spaces).

    :param hex_color: Hex color string like "#FF0000" or "FF0000"
    :return: RGBA tuple with values 0.0-1.0 in linear color space
    """
    hex_color = hex_color.lstrip("#")
    try:
        if len(hex_color) == 6:  # RGB
            r = srgb_to_linear(int(hex_color[0:2], 16) / 255.0)
            g = srgb_to_linear(int(hex_color[2:4], 16) / 255.0)
            b = srgb_to_linear(int(hex_color[4:6], 16) / 255.0)
            return (r, g, b, 1.0)
        elif len(hex_color) == 8:  # RGBA
            r = srgb_to_linear(int(hex_color[0:2], 16) / 255.0)
            g = srgb_to_linear(int(hex_color[2:4], 16) / 255.0)
            b = srgb_to_linear(int(hex_color[4:6], 16) / 255.0)
            a = int(hex_color[6:8], 16) / 255.0  # Alpha is linear, no conversion
            return (r, g, b, a)
    except ValueError:
        pass

    warn(f"Could not parse hex color: {hex_color}")
    return (0.8, 0.8, 0.8, 1.0)  # Default gray


def find_existing_material(
    op, name: str, color: Tuple[float, float, float, float]
) -> Optional[bpy.types.Material]:
    """
    Find an existing Blender material that matches the given name and color.

    :param op: The Import3MF operator instance.
    :param name: The desired material name.
    :param color: The RGBA color tuple (values 0-1).
    :return: Matching material if found, None otherwise.
    """
    # First try exact name match
    if name in bpy.data.materials:
        material = bpy.data.materials[name]
        if material.use_nodes:
            principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(material, is_readonly=True)
            # Check if colors match (within small tolerance for float comparison)
            existing_color = (*principled.base_color, principled.alpha)
            if all(abs(existing_color[i] - color[i]) < 0.001 for i in range(4)):
                debug(f"Reusing existing material: {name}")
                return material

    # Try to find any material with matching color (fuzzy name match)
    color_tolerance = 0.001
    for mat in bpy.data.materials:
        if mat.use_nodes:
            principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(mat, is_readonly=True)
            existing_color = (*principled.base_color, principled.alpha)
            if all(abs(existing_color[i] - color[i]) < color_tolerance for i in range(4)):
                # Found a material with matching color but different name
                debug(f"Reusing material '{mat.name}' for color match (requested name: '{name}')")
                return mat

    return None


def read_materials(op, root, material_ns: dict, display_properties: dict) -> None:
    """
    Read basematerials and colorgroup elements from the 3MF document.

    Populates op.resource_materials with ResourceMaterial entries.

    :param op: The Import3MF operator instance with state.
    :param root: The XML root element.
    :param material_ns: Namespace dict for materials extension.
    :param display_properties: Parsed PBR display properties lookup.
    """
    from ...common.constants import MODEL_NAMESPACES
    from ...common.types import ResourceMaterial

    # Import core spec basematerials
    for basematerials_item in root.iterfind("./3mf:resources/3mf:basematerials", MODEL_NAMESPACES):
        try:
            material_id = basematerials_item.attrib["id"]
        except KeyError:
            warn("Encountered a basematerials item without resource ID.")
            op.safe_report({"WARNING"}, "Encountered a basematerials item without resource ID")
            continue
        if material_id in op.resource_materials:
            warn(f"Duplicate material ID: {material_id}")
            op.safe_report({"WARNING"}, f"Duplicate material ID: {material_id}")
            continue

        # Check for PBR display properties reference at the group level
        group_display_props_id = basematerials_item.attrib.get("displaypropertiesid")
        group_pbr_props_list = display_properties.get(group_display_props_id, []) if group_display_props_id else []

        # Check group size before parsing — reject absurdly large groups
        # (e.g. 3D scans with per-vertex coloring)
        from ...common.constants import MAX_MATERIAL_GROUP_SIZE
        child_count = len(basematerials_item.findall("./3mf:base", MODEL_NAMESPACES))
        if child_count > MAX_MATERIAL_GROUP_SIZE:
            msg = (
                f"Basematerials group {material_id} has {child_count:,} entries — "
                f"exceeds the {MAX_MATERIAL_GROUP_SIZE:,} material limit. "
                f"This is likely a 3D scan with per-vertex coloring which "
                f"Blender cannot represent as individual materials. Skipping."
            )
            warn(msg)
            op.safe_report({"WARNING"}, msg)
            continue

        op.resource_materials[material_id] = {}
        index = 0

        for base_item in basematerials_item.iterfind("./3mf:base", MODEL_NAMESPACES):
            name = base_item.attrib.get("name", "3MF Material")
            color = base_item.attrib.get("displaycolor")

            # Check for per-material displaypropertiesid (overrides group-level)
            base_display_props_id = base_item.attrib.get("displaypropertiesid")
            display_props_id = base_display_props_id if base_display_props_id else group_display_props_id

            pbr_data = {}
            textured_pbr = None

            if display_props_id:
                # First check for scalar PBR properties
                if base_display_props_id:
                    base_pbr_props = display_properties.get(base_display_props_id, [])
                    pbr_data = base_pbr_props[0] if base_pbr_props else {}
                elif group_pbr_props_list:
                    pbr_data = group_pbr_props_list[index] if index < len(group_pbr_props_list) else {}

                # If no scalar data found, check for textured PBR properties
                if not pbr_data and display_props_id in op.resource_pbr_texture_displays:
                    textured_pbr = op.resource_pbr_texture_displays[display_props_id]
                    debug(f"Material '{name}' has textured PBR: {textured_pbr.type}")
            elif group_pbr_props_list:
                pbr_data = group_pbr_props_list[index] if index < len(group_pbr_props_list) else {}

            if color is not None:
                color = color.lstrip("#")
                try:
                    color_int = int(color, 16)
                    b1 = (color_int & 0x000000FF) / 255
                    b2 = ((color_int & 0x0000FF00) >> 8) / 255
                    b3 = ((color_int & 0x00FF0000) >> 16) / 255
                    b4 = ((color_int & 0xFF000000) >> 24) / 255
                    # 3MF hex colors are sRGB — convert RGB to linear for Blender.
                    # Alpha is linear in both spaces and is NOT converted.
                    if len(color) == 6:
                        color = (
                            srgb_to_linear(b3),
                            srgb_to_linear(b2),
                            srgb_to_linear(b1),
                            1.0,
                        )
                    else:
                        color = (
                            srgb_to_linear(b4),
                            srgb_to_linear(b3),
                            srgb_to_linear(b2),
                            b1,
                        )
                except ValueError:
                    warn(f"Invalid color for material {name} of resource {material_id}: {color}")
                    op.safe_report(
                        {"WARNING"},
                        f"Invalid color for material {name} of resource {material_id}: {color}",
                    )
                    color = None

            # Extract textured PBR texture IDs if present
            metallic_texid = None
            roughness_texid = None
            specular_texid = None
            glossiness_texid = None
            basecolor_texid = None

            if textured_pbr:
                if textured_pbr.type == "metallic":
                    metallic_texid = textured_pbr.primary_texid
                    roughness_texid = textured_pbr.secondary_texid
                    basecolor_texid = textured_pbr.basecolor_texid
                    if textured_pbr.factors.get("metallicfactor"):
                        try:
                            pbr_data["metallic"] = float(textured_pbr.factors["metallicfactor"])
                        except ValueError:
                            pass
                    if textured_pbr.factors.get("roughnessfactor"):
                        try:
                            pbr_data["roughness"] = float(textured_pbr.factors["roughnessfactor"])
                        except ValueError:
                            pass
                elif textured_pbr.type == "specular":
                    specular_texid = textured_pbr.primary_texid
                    glossiness_texid = textured_pbr.secondary_texid
                    basecolor_texid = textured_pbr.basecolor_texid
                    if textured_pbr.factors.get("glossinessfactor"):
                        try:
                            pbr_data["glossiness"] = float(textured_pbr.factors["glossinessfactor"])
                        except ValueError:
                            pass

            op.resource_materials[material_id][index] = ResourceMaterial(
                name=name,
                color=color,
                metallic=pbr_data.get("metallic"),
                roughness=pbr_data.get("roughness"),
                specular_color=pbr_data.get("specular_color"),
                glossiness=pbr_data.get("glossiness"),
                ior=pbr_data.get("ior"),
                attenuation=pbr_data.get("attenuation"),
                transmission=pbr_data.get("transmission"),
                metallic_texid=metallic_texid,
                roughness_texid=roughness_texid,
                specular_texid=specular_texid,
                glossiness_texid=glossiness_texid,
                basecolor_texid=basecolor_texid,
            )

            if pbr_data:
                debug(f"Material '{name}' has PBR properties: {pbr_data}")
            if textured_pbr:
                debug(
                    f"Material '{name}' has textured PBR: metallic_tex={metallic_texid}, "
                    f"roughness_tex={roughness_texid}, basecolor_tex={basecolor_texid}"
                )

            index += 1

        if len(op.resource_materials[material_id]) == 0:
            del op.resource_materials[material_id]

    # Import Materials extension colorgroups
    _read_colorgroups(op, root, material_ns, display_properties)


def _read_colorgroups(op, root, material_ns: dict, display_properties: dict) -> None:
    """
    Read colorgroup elements from the 3MF document.

    :param op: The Import3MF operator instance.
    :param root: The XML root element.
    :param material_ns: Namespace dict for materials extension.
    :param display_properties: Parsed PBR display properties lookup.
    """
    from ...common.constants import MODEL_NAMESPACES
    from ...common.types import ResourceMaterial, ResourceColorgroup

    for colorgroup_item in root.iterfind("./3mf:resources/m:colorgroup", {**MODEL_NAMESPACES, **material_ns}):
        try:
            colorgroup_id = colorgroup_item.attrib["id"]
        except KeyError:
            warn("Encountered a colorgroup without resource ID.")
            op.safe_report({"WARNING"}, "Encountered a colorgroup without resource ID")
            continue

        if colorgroup_id in op.resource_materials:
            warn(f"Duplicate material ID: {colorgroup_id}")
            op.safe_report({"WARNING"}, f"Duplicate material ID: {colorgroup_id}")
            continue

        display_props_id = colorgroup_item.attrib.get("displaypropertiesid")
        pbr_props_list = display_properties.get(display_props_id, []) if display_props_id else []

        # Check group size before parsing — reject absurdly large groups
        # (e.g. 3D scans with per-vertex coloring)
        from ...common.constants import MAX_MATERIAL_GROUP_SIZE
        child_count = len(colorgroup_item.findall("./m:color", material_ns))
        if child_count > MAX_MATERIAL_GROUP_SIZE:
            msg = (
                f"Colorgroup {colorgroup_id} has {child_count:,} entries — "
                f"exceeds the {MAX_MATERIAL_GROUP_SIZE:,} material limit. "
                f"This is likely a 3D scan with per-vertex coloring which "
                f"Blender cannot represent as individual materials. Skipping."
            )
            warn(msg)
            op.safe_report({"WARNING"}, msg)
            continue

        raw_colors = []
        op.resource_materials[colorgroup_id] = {}
        index = 0

        for color_item in colorgroup_item.iterfind("./m:color", material_ns):
            color = color_item.attrib.get("color")
            if color is not None:
                raw_color = color if color.startswith("#") else f"#{color}"
                raw_colors.append(raw_color)

                color = color.lstrip("#")
                try:
                    if len(color) == 6:
                        red = srgb_to_linear(int(color[0:2], 16) / 255)
                        green = srgb_to_linear(int(color[2:4], 16) / 255)
                        blue = srgb_to_linear(int(color[4:6], 16) / 255)
                        alpha = 1.0
                    elif len(color) == 8:
                        red = srgb_to_linear(int(color[0:2], 16) / 255)
                        green = srgb_to_linear(int(color[2:4], 16) / 255)
                        blue = srgb_to_linear(int(color[4:6], 16) / 255)
                        alpha = int(color[6:8], 16) / 255  # Alpha is linear
                    else:
                        warn(f"Invalid color for colorgroup {colorgroup_id}: #{color}")
                        op.safe_report({"WARNING"}, f"Invalid color: #{color}")
                        continue

                    pbr_data = pbr_props_list[index] if index < len(pbr_props_list) else {}

                    mat_color = (red, green, blue, alpha)
                    op.resource_materials[colorgroup_id][index] = ResourceMaterial(
                        name=f"Orca Color {index}",
                        color=mat_color,
                        metallic=pbr_data.get("metallic"),
                        roughness=pbr_data.get("roughness"),
                        specular_color=pbr_data.get("specular_color"),
                        glossiness=pbr_data.get("glossiness"),
                        ior=pbr_data.get("ior"),
                        attenuation=pbr_data.get("attenuation"),
                        transmission=pbr_data.get("transmission"),
                        metallic_texid=None,
                        roughness_texid=None,
                        specular_texid=None,
                        glossiness_texid=None,
                    )
                    index += 1

                except (ValueError, KeyError) as e:
                    warn(f"Invalid color for colorgroup {colorgroup_id}: {e}")
                    continue

        if raw_colors:
            op.resource_colorgroups[colorgroup_id] = ResourceColorgroup(
                colors=raw_colors, displaypropertiesid=display_props_id
            )
            debug(f"Stored colorgroup {colorgroup_id} for round-trip ({len(raw_colors)} colors)")

        if index > 0:
            debug(f"Imported colorgroup {colorgroup_id} with {index} colors")
            if op.vendor_format == "orca":
                op.safe_report({"INFO"}, f"Imported Orca color zone: {index} color(s)")
        elif colorgroup_id in op.resource_materials:
            del op.resource_materials[colorgroup_id]
