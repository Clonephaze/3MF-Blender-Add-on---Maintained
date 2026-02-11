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
PBR material import functionality for 3MF Materials Extension.

This module handles:
- Reading pbmetallicdisplayproperties (metallic workflow)
- Reading pbspeculardisplayproperties (specular workflow)
- Reading translucentdisplayproperties (translucent materials)
- Reading textured PBR display properties
- Applying PBR properties to Blender Principled BSDF
"""

from typing import Dict, List

import bpy
import bpy_extras.node_shader_utils

from ...common import debug, warn

from ...common.types import (
    ResourcePBRDisplayProps,
    ResourcePBRTextureDisplay,
    ResourceMaterial,
)


def read_pbr_metallic_properties(op, root, material_ns: Dict[str, str]) -> Dict[str, List[Dict]]:
    """
    Parse <m:pbmetallicdisplayproperties> elements from the 3MF document.

    The metallic workflow defines materials by:
    - metallicness: 0.0 (dielectric) to 1.0 (pure metal)
    - roughness: 0.0 (smooth/glossy) to 1.0 (rough/matte)

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    :return: Dict mapping displaypropertiesid -> list of property dicts.
    """
    from ...common.constants import MODEL_NAMESPACES

    props = {}
    for display_props in root.iterfind(
        "./3mf:resources/m:pbmetallicdisplayproperties",
        {**MODEL_NAMESPACES, **material_ns},
    ):
        try:
            props_id = display_props.attrib["id"]
        except KeyError:
            continue

        material_props = []
        raw_props = []
        for pbmetallic in display_props.iterfind("./m:pbmetallic", material_ns):
            prop_dict = {"type": "metallic"}
            raw_props.append(dict(pbmetallic.attrib))

            try:
                metallicness = float(pbmetallic.attrib.get("metallicness", "0"))
                prop_dict["metallic"] = max(0.0, min(1.0, metallicness))
            except ValueError:
                prop_dict["metallic"] = 0.0

            try:
                roughness = float(pbmetallic.attrib.get("roughness", "1"))
                prop_dict["roughness"] = max(0.0, min(1.0, roughness))
            except ValueError:
                prop_dict["roughness"] = 1.0

            prop_dict["name"] = pbmetallic.attrib.get("name", "")
            material_props.append(prop_dict)
            debug(f"Parsed metallic PBR: metallic={prop_dict['metallic']}, roughness={prop_dict['roughness']}")

        if material_props:
            props[props_id] = material_props
            debug(f"Imported {len(material_props)} metallic display properties (ID: {props_id})")
            op.resource_pbr_display_props[props_id] = ResourcePBRDisplayProps(type="metallic", properties=raw_props)

    return props


def read_pbr_specular_properties(op, root, material_ns: Dict[str, str]) -> Dict[str, List[Dict]]:
    """
    Parse <m:pbspeculardisplayproperties> elements from the 3MF document.

    The specular workflow defines materials by:
    - specularcolor: sRGB color for specular reflectance
    - glossiness: 0.0 (rough) to 1.0 (smooth) - inverse of roughness

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    :return: Dict mapping displaypropertiesid -> list of property dicts.
    """
    from ...common.constants import MODEL_NAMESPACES

    props = {}
    for display_props in root.iterfind(
        "./3mf:resources/m:pbspeculardisplayproperties",
        {**MODEL_NAMESPACES, **material_ns},
    ):
        try:
            props_id = display_props.attrib["id"]
        except KeyError:
            continue

        material_props = []
        raw_props = []
        for pbspecular in display_props.iterfind("./m:pbspecular", material_ns):
            prop_dict = {"type": "specular"}
            raw_props.append(dict(pbspecular.attrib))

            specular_color_hex = pbspecular.attrib.get("specularcolor", "#383838")
            specular_color_hex = specular_color_hex.lstrip("#")
            try:
                if len(specular_color_hex) >= 6:
                    sr = int(specular_color_hex[0:2], 16) / 255.0
                    sg = int(specular_color_hex[2:4], 16) / 255.0
                    sb = int(specular_color_hex[4:6], 16) / 255.0
                    prop_dict["specular_color"] = (sr, sg, sb)
                else:
                    prop_dict["specular_color"] = (0.22, 0.22, 0.22)
            except ValueError:
                prop_dict["specular_color"] = (0.22, 0.22, 0.22)

            try:
                glossiness = float(pbspecular.attrib.get("glossiness", "0"))
                prop_dict["glossiness"] = max(0.0, min(1.0, glossiness))
                prop_dict["roughness"] = 1.0 - prop_dict["glossiness"]
            except ValueError:
                prop_dict["glossiness"] = 0.0
                prop_dict["roughness"] = 1.0

            prop_dict["name"] = pbspecular.attrib.get("name", "")
            material_props.append(prop_dict)
            debug(f"Parsed specular PBR: glossiness={prop_dict['glossiness']}, specular={prop_dict['specular_color']}")

        if material_props:
            props[props_id] = material_props
            debug(f"Imported {len(material_props)} specular display properties (ID: {props_id})")
            op.resource_pbr_display_props[props_id] = ResourcePBRDisplayProps(type="specular", properties=raw_props)

    return props


def read_pbr_translucent_properties(op, root, material_ns: Dict[str, str]) -> Dict[str, List[Dict]]:
    """
    Parse <m:translucentdisplayproperties> elements from the 3MF document.

    Translucent materials are defined by:
    - attenuation: RGB coefficients for light absorption
    - refractiveindex: IOR per RGB channel
    - roughness: Surface roughness for blurry refractions

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    :return: Dict mapping displaypropertiesid -> list of property dicts.
    """
    from ...common.constants import MODEL_NAMESPACES

    props = {}
    for display_props in root.iterfind(
        "./3mf:resources/m:translucentdisplayproperties",
        {**MODEL_NAMESPACES, **material_ns},
    ):
        try:
            props_id = display_props.attrib["id"]
        except KeyError:
            continue

        material_props = []
        raw_props = []
        for translucent in display_props.iterfind("./m:translucent", material_ns):
            prop_dict = {"type": "translucent", "transmission": 1.0}
            raw_props.append(dict(translucent.attrib))

            blender_transmission = translucent.attrib.get("blender_transmission")
            if blender_transmission:
                try:
                    prop_dict["transmission"] = float(blender_transmission)
                except ValueError:
                    pass

            attenuation_str = translucent.attrib.get("attenuation", "0 0 0")
            try:
                attenuation_values = [float(x) for x in attenuation_str.split()]
                if len(attenuation_values) >= 3:
                    prop_dict["attenuation"] = tuple(attenuation_values[:3])
                else:
                    prop_dict["attenuation"] = (0.0, 0.0, 0.0)
            except ValueError:
                prop_dict["attenuation"] = (0.0, 0.0, 0.0)

            ior_str = translucent.attrib.get("refractiveindex", "1 1 1")
            try:
                ior_values = [float(x) for x in ior_str.split()]
                if len(ior_values) >= 3:
                    prop_dict["ior"] = sum(ior_values[:3]) / 3.0
                elif len(ior_values) == 1:
                    prop_dict["ior"] = ior_values[0]
                else:
                    prop_dict["ior"] = 1.45
            except ValueError:
                prop_dict["ior"] = 1.45

            try:
                roughness = float(translucent.attrib.get("roughness", "0"))
                prop_dict["roughness"] = max(0.0, min(1.0, roughness))
            except ValueError:
                prop_dict["roughness"] = 0.0

            prop_dict["name"] = translucent.attrib.get("name", "")
            material_props.append(prop_dict)
            debug(
                f"Parsed translucent PBR: ior={prop_dict['ior']}, "
                f"roughness={prop_dict['roughness']}, attenuation={prop_dict['attenuation']}"
            )

        if material_props:
            props[props_id] = material_props
            debug(f"Imported {len(material_props)} translucent display properties (ID: {props_id})")
            op.resource_pbr_display_props[props_id] = ResourcePBRDisplayProps(type="translucent", properties=raw_props)

    return props


def read_pbr_texture_display_properties(op, root, material_ns: Dict[str, str]) -> None:
    """
    Parse textured PBR display properties for round-trip support.

    Handles both pbspeculartexturedisplayproperties and pbmetallictexturedisplayproperties.

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    """
    from ...common.constants import MODEL_NAMESPACES

    # Parse pbspeculartexturedisplayproperties
    for prop_item in root.iterfind(
        "./3mf:resources/m:pbspeculartexturedisplayproperties",
        {**MODEL_NAMESPACES, **material_ns},
    ):
        try:
            prop_id = prop_item.attrib["id"]
        except KeyError:
            warn("Encountered pbspeculartexturedisplayproperties without ID")
            continue

        if prop_id in op.resource_pbr_texture_displays:
            continue

        name = prop_item.attrib.get("name", "")
        specular_texid = prop_item.attrib.get("speculartextureid")
        glossiness_texid = prop_item.attrib.get("glossinesstextureid")
        diffuse_texid = prop_item.attrib.get("diffusetextureid")

        factors = {
            "diffusefactor": prop_item.attrib.get("diffusefactor", "#FFFFFF"),
            "specularfactor": prop_item.attrib.get("specularfactor", "#FFFFFF"),
            "glossinessfactor": prop_item.attrib.get("glossinessfactor", "1"),
        }

        op.resource_pbr_texture_displays[prop_id] = ResourcePBRTextureDisplay(
            type="specular",
            name=name,
            primary_texid=specular_texid,
            secondary_texid=glossiness_texid,
            basecolor_texid=diffuse_texid,
            factors=factors,
        )
        debug(f"Parsed pbspeculartexturedisplayproperties {prop_id}")

    # Parse pbmetallictexturedisplayproperties
    for prop_item in root.iterfind(
        "./3mf:resources/m:pbmetallictexturedisplayproperties",
        {**MODEL_NAMESPACES, **material_ns},
    ):
        try:
            prop_id = prop_item.attrib["id"]
        except KeyError:
            warn("Encountered pbmetallictexturedisplayproperties without ID")
            continue

        if prop_id in op.resource_pbr_texture_displays:
            continue

        name = prop_item.attrib.get("name", "")
        metallic_texid = prop_item.attrib.get("metallictextureid")
        roughness_texid = prop_item.attrib.get("roughnesstextureid")
        basecolor_texid = prop_item.attrib.get("basecolortextureid")

        factors = {
            "basecolorfactor": prop_item.attrib.get("basecolorfactor", "#FFFFFF"),
            "metallicfactor": prop_item.attrib.get("metallicfactor", "1"),
            "roughnessfactor": prop_item.attrib.get("roughnessfactor", "1"),
        }

        op.resource_pbr_texture_displays[prop_id] = ResourcePBRTextureDisplay(
            type="metallic",
            name=name,
            primary_texid=metallic_texid,
            secondary_texid=roughness_texid,
            basecolor_texid=basecolor_texid,
            factors=factors,
        )
        debug(f"Parsed pbmetallictexturedisplayproperties {prop_id} (basecolor={basecolor_texid})")

    if op.resource_pbr_texture_displays:
        debug(f"Found {len(op.resource_pbr_texture_displays)} textured PBR display properties (passthrough)")


def apply_pbr_to_principled(
    op,
    principled: bpy_extras.node_shader_utils.PrincipledBSDFWrapper,
    material: bpy.types.Material,
    resource_material: ResourceMaterial,
) -> None:
    """
    Apply PBR properties from a 3MF ResourceMaterial to a Blender Principled BSDF material.

    Handles metallic, specular, and translucent workflows.

    :param op: The Import3MF operator instance.
    :param principled: PrincipledBSDFWrapper for the material.
    :param material: The Blender material being configured.
    :param resource_material: The ResourceMaterial with PBR data from 3MF.
    """
    has_pbr = False

    # Apply metallic workflow properties
    if resource_material.metallic is not None:
        principled.metallic = resource_material.metallic
        has_pbr = True
        debug(f"Applied metallic={resource_material.metallic} to material '{resource_material.name}'")

    if resource_material.roughness is not None:
        principled.roughness = resource_material.roughness
        has_pbr = True
        debug(f"Applied roughness={resource_material.roughness} to material '{resource_material.name}'")

    # Apply specular workflow properties
    if resource_material.specular_color is not None:
        material["3mf_specular_color"] = list(resource_material.specular_color)
        spec_r, spec_g, spec_b = resource_material.specular_color
        specular_intensity = (spec_r + spec_g + spec_b) / 3.0
        specular_level = specular_intensity / 0.44
        principled.specular = min(1.0, max(0.0, specular_level))
        has_pbr = True
        debug(
            f"Applied specular_level={principled.specular} (from color "
            f"{resource_material.specular_color}) to material '{resource_material.name}'"
        )

    # Apply translucent/glass properties
    if resource_material.transmission is not None and resource_material.transmission > 0:
        material["3mf_transmission"] = resource_material.transmission
        if material.node_tree:
            for node in material.node_tree.nodes:
                if node.type == "BSDF_PRINCIPLED":
                    if "Transmission Weight" in node.inputs:
                        node.inputs["Transmission Weight"].default_value = resource_material.transmission
                    elif "Transmission" in node.inputs:
                        node.inputs["Transmission"].default_value = resource_material.transmission
                    break
        has_pbr = True
        debug(f"Applied transmission={resource_material.transmission} to material '{resource_material.name}'")

    if resource_material.ior is not None:
        principled.ior = resource_material.ior
        has_pbr = True
        debug(f"Applied IOR={resource_material.ior} to material '{resource_material.name}'")

    # Apply attenuation as volume absorption
    if resource_material.attenuation is not None:
        att_r, att_g, att_b = resource_material.attenuation
        if att_r > 0 or att_g > 0 or att_b > 0:
            max_att = max(att_r, att_g, att_b, 0.001)
            abs_r = 1.0 - min(1.0, att_r / (max_att * 2))
            abs_g = 1.0 - min(1.0, att_g / (max_att * 2))
            abs_b = 1.0 - min(1.0, att_b / (max_att * 2))

            material["3mf_attenuation"] = list(resource_material.attenuation)

            if resource_material.transmission and resource_material.transmission > 0.5:
                current_color = list(principled.base_color)
                principled.base_color = (
                    current_color[0] * abs_r,
                    current_color[1] * abs_g,
                    current_color[2] * abs_b,
                )

            has_pbr = True
            debug(f"Applied attenuation={resource_material.attenuation} to material '{resource_material.name}'")

    if has_pbr:
        debug(f"Applied PBR properties to material '{resource_material.name}'")


def apply_pbr_textures_to_material(
    op, material: bpy.types.Material, resource_material: ResourceMaterial,
    has_uv_layer: bool = False,
) -> bool:
    """
    Apply PBR texture maps from a 3MF ResourceMaterial to a Blender material.

    Creates Image Texture nodes and connects them to Principled BSDF inputs.

    :param op: The Import3MF operator instance.
    :param material: The Blender material to configure (must have node tree).
    :param resource_material: The ResourceMaterial with PBR texture IDs from 3MF.
    :param has_uv_layer: True if the mesh has a UV layer (use UV coords),
        False to fall back to Generated projection.
    :return: True if any textures were applied, False otherwise.
    """
    if not material.node_tree:
        return False

    has_metallic_tex = resource_material.metallic_texid is not None
    has_roughness_tex = resource_material.roughness_texid is not None
    has_specular_tex = resource_material.specular_texid is not None
    has_glossiness_tex = resource_material.glossiness_texid is not None
    has_basecolor_tex = resource_material.basecolor_texid is not None

    if not (has_metallic_tex or has_roughness_tex or has_specular_tex or has_glossiness_tex or has_basecolor_tex):
        return False

    nodes = material.node_tree.nodes
    links = material.node_tree.links

    principled = None
    for node in nodes:
        if node.type == "BSDF_PRINCIPLED":
            principled = node
            break

    if principled is None:
        warn(f"No Principled BSDF found in material '{material.name}'")
        return False

    applied_any = False
    x_offset = -400

    # Texture Coordinate node — use UV output when a UV layer exists (from
    # texture2dgroup import), or Generated as fallback for PBR-only files.
    coord_output = "UV" if has_uv_layer else "Generated"
    tex_coord = None

    def _get_tex_coord():
        """Lazily get or create a shared Texture Coordinate node."""
        nonlocal tex_coord
        if tex_coord is None:
            # Reuse existing TexCoord node from setup_textured_material if present
            for node in nodes:
                if node.type == "TEX_COORD":
                    tex_coord = node
                    break
            if tex_coord is None:
                tex_coord = nodes.new("ShaderNodeTexCoord")
                tex_coord.location = (
                    principled.location.x + x_offset - 400,
                    principled.location.y,
                )
        return tex_coord

    # Apply base color texture — skip if already connected by setup_textured_material
    if has_basecolor_tex:
        base_color_input = principled.inputs.get("Base Color")
        if base_color_input and base_color_input.is_linked:
            debug(f"Base Color already connected in '{material.name}', skipping PBR base color")
        else:
            texture = op.resource_textures.get(resource_material.basecolor_texid)
            if texture and texture.blender_image:
                tex_node = nodes.new("ShaderNodeTexImage")
                tex_node.image = texture.blender_image
                tex_node.location = (
                    principled.location.x + x_offset,
                    principled.location.y + 400,
                )
                tex_node.label = "Base Color Map"
                links.new(_get_tex_coord().outputs[coord_output], tex_node.inputs["Vector"])
                links.new(tex_node.outputs["Color"], principled.inputs["Base Color"])
                applied_any = True
                debug(f"Applied base color texture '{texture.blender_image.name}' to '{material.name}'")

    # Apply metallic texture
    if has_metallic_tex:
        texture = op.resource_textures.get(resource_material.metallic_texid)
        if texture and texture.blender_image:
            tex_node = nodes.new("ShaderNodeTexImage")
            tex_node.image = texture.blender_image
            tex_node.location = (
                principled.location.x + x_offset,
                principled.location.y + 200,
            )
            tex_node.label = "Metallic Map"
            tex_node.image.colorspace_settings.name = "Non-Color"
            links.new(_get_tex_coord().outputs[coord_output], tex_node.inputs["Vector"])
            links.new(tex_node.outputs["Color"], principled.inputs["Metallic"])
            applied_any = True
            debug(f"Applied metallic texture '{texture.blender_image.name}' to '{material.name}'")

    # Apply roughness texture
    if has_roughness_tex:
        texture = op.resource_textures.get(resource_material.roughness_texid)
        if texture and texture.blender_image:
            tex_node = nodes.new("ShaderNodeTexImage")
            tex_node.image = texture.blender_image
            tex_node.location = (
                principled.location.x + x_offset,
                principled.location.y,
            )
            tex_node.label = "Roughness Map"
            tex_node.image.colorspace_settings.name = "Non-Color"
            links.new(_get_tex_coord().outputs[coord_output], tex_node.inputs["Vector"])
            links.new(tex_node.outputs["Color"], principled.inputs["Roughness"])
            applied_any = True
            debug(f"Applied roughness texture '{texture.blender_image.name}' to '{material.name}'")

    # Apply specular texture
    if has_specular_tex:
        texture = op.resource_textures.get(resource_material.specular_texid)
        if texture and texture.blender_image:
            tex_node = nodes.new("ShaderNodeTexImage")
            tex_node.image = texture.blender_image
            tex_node.location = (
                principled.location.x + x_offset,
                principled.location.y - 200,
            )
            tex_node.label = "Specular Map"
            links.new(_get_tex_coord().outputs[coord_output], tex_node.inputs["Vector"])
            if "Specular IOR Level" in principled.inputs:
                links.new(tex_node.outputs["Color"], principled.inputs["Specular IOR Level"])
            elif "Specular" in principled.inputs:
                links.new(tex_node.outputs["Color"], principled.inputs["Specular"])
            applied_any = True
            debug(f"Applied specular texture '{texture.blender_image.name}' to '{material.name}'")

    # Apply glossiness texture (invert to roughness)
    if has_glossiness_tex:
        texture = op.resource_textures.get(resource_material.glossiness_texid)
        if texture and texture.blender_image:
            tex_node = nodes.new("ShaderNodeTexImage")
            tex_node.image = texture.blender_image
            tex_node.location = (
                principled.location.x + x_offset - 200,
                principled.location.y - 400,
            )
            tex_node.label = "Glossiness Map"
            tex_node.image.colorspace_settings.name = "Non-Color"
            links.new(_get_tex_coord().outputs[coord_output], tex_node.inputs["Vector"])

            invert_node = nodes.new("ShaderNodeInvert")
            invert_node.location = (
                principled.location.x + x_offset + 100,
                principled.location.y - 400,
            )

            links.new(tex_node.outputs["Color"], invert_node.inputs["Color"])
            links.new(invert_node.outputs["Color"], principled.inputs["Roughness"])
            applied_any = True
            debug(f"Applied glossiness texture (inverted) '{texture.blender_image.name}' to '{material.name}'")

    if applied_any:
        debug(f"Applied PBR texture maps to material '{material.name}'")

    return applied_any
