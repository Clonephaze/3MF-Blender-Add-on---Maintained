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
PBR (Physically Based Rendering) export functionality for 3MF Materials Extension.

Handles:
- PBR property extraction from Blender materials
- Writing pbmetallicdisplayproperties
- Writing pbspeculardisplayproperties
- Writing translucentdisplayproperties
"""

import xml.etree.ElementTree
from typing import Dict, List, Tuple

import bpy
import bpy_extras.node_shader_utils

from ...common.constants import MATERIAL_NAMESPACE
from ...common import debug


def extract_pbr_from_material(
    material: bpy.types.Material,
    principled: bpy_extras.node_shader_utils.PrincipledBSDFWrapper,
) -> Dict:
    """
    Extract PBR properties from a Blender material's Principled BSDF.

    Supports all three 3MF Materials Extension workflows:
    - Metallic: metallicness, roughness
    - Specular: specularcolor (RGB), glossiness
    - Translucent: refractiveindex (RGB), roughness, attenuation (RGB)

    :param material: The Blender material
    :param principled: PrincipledBSDFWrapper for the material
    :return: Dictionary with PBR properties for export
    """

    # Helper to safely convert values to float (handles MagicMock in unit tests)
    def safe_float(value, default):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def safe_color(value, default):
        """Safely extract RGB tuple from color value."""
        try:
            if hasattr(value, "__iter__") and len(value) >= 3:
                return (float(value[0]), float(value[1]), float(value[2]))
            return default
        except (TypeError, ValueError, IndexError):
            return default

    # Core PBR properties
    metallic = safe_float(principled.metallic, 0.0)
    roughness = safe_float(principled.roughness, 0.5)
    specular_ior_level = safe_float(principled.specular, 0.5)
    ior = safe_float(principled.ior, 1.45)
    base_color = safe_color(principled.base_color, (0.8, 0.8, 0.8))

    # Calculate specular color for specular workflow
    # In Blender 4.0+, Specular IOR Level controls Fresnel reflectance
    # Default 0.5 = 4% reflectance at normal incidence (#383838 in sRGB)
    # Scale specular color by the specular IOR level
    default_specular_gray = 0.22  # ~4% reflectance in linear space (sRGB #383838)
    specular_intensity = specular_ior_level * 2 * default_specular_gray  # Scale from 0-1 to color

    # Check for specular tint (tints specular with base color)
    # In Blender 4.x, Specular Tint is an RGBA color where WHITE (1,1,1) = no tint
    # We need to detect if it's NOT white to know if tinting is applied
    specular_tint_color = None
    try:
        if material.node_tree:
            for node in material.node_tree.nodes:
                if node.type == "BSDF_PRINCIPLED":
                    if "Specular Tint" in node.inputs:
                        tint_input = node.inputs["Specular Tint"]
                        if hasattr(tint_input, "default_value"):
                            val = tint_input.default_value
                            # Blender 4.x: It's an RGBA color
                            if hasattr(val, "__iter__") and len(val) >= 3:
                                # Check if it's NOT white (default = no tint)
                                r, g, b = float(val[0]), float(val[1]), float(val[2])
                                if abs(r - 1.0) > 0.01 or abs(g - 1.0) > 0.01 or abs(b - 1.0) > 0.01:
                                    specular_tint_color = (r, g, b)
                            else:
                                # Old Blender: It's a float 0-1
                                tint_amount = safe_float(val, 0.0)
                                if tint_amount > 0.01:
                                    specular_tint_color = base_color[:3]
                    break
    except (TypeError, AttributeError):
        pass

    # Calculate final specular color
    if specular_tint_color is not None:
        # Use the tint color directly (it already incorporates the tint)
        specular_color = specular_tint_color
    else:
        # Neutral gray specular based on specular IOR level
        specular_color = (specular_intensity, specular_intensity, specular_intensity)

    pbr_data = {
        "metallic": metallic,
        "roughness": roughness,
        "glossiness": 1.0 - roughness,  # Specular workflow uses glossiness (inverse)
        "specular_ior_level": specular_ior_level,
        "specular_color": specular_color,
        "ior": ior,
        "transmission": 0.0,
        "attenuation": None,
    }

    # Get transmission from node tree (PrincipledBSDFWrapper may not expose it directly)
    try:
        if material.node_tree:
            for node in material.node_tree.nodes:
                if node.type == "BSDF_PRINCIPLED":
                    # Blender 4.0+ uses 'Transmission Weight' instead of 'Transmission'
                    if "Transmission Weight" in node.inputs:
                        pbr_data["transmission"] = safe_float(node.inputs["Transmission Weight"].default_value, 0.0)
                    elif "Transmission" in node.inputs:
                        pbr_data["transmission"] = safe_float(node.inputs["Transmission"].default_value, 0.0)
                    break
    except (TypeError, AttributeError):
        # Handle mocked objects in unit tests
        pass

    # Check for stored 3MF attenuation from round-trip
    try:
        if "3mf_attenuation" in material:
            pbr_data["attenuation"] = tuple(material["3mf_attenuation"])
    except TypeError:
        # Handle mocked objects in unit tests
        pass

    # Check for stored 3MF specular_color from round-trip (preserves exact color)
    try:
        if "3mf_specular_color" in material:
            pbr_data["specular_color"] = tuple(material["3mf_specular_color"])
    except TypeError:
        # Handle mocked objects in unit tests
        pass

    # Check for stored 3MF transmission from round-trip
    try:
        if "3mf_transmission" in material:
            pbr_data["transmission"] = float(material["3mf_transmission"])
    except TypeError:
        # Handle mocked objects in unit tests
        pass

    return pbr_data


def write_pbr_display_properties(
    resources_element: xml.etree.ElementTree.Element,
    basematerials_element: xml.etree.ElementTree.Element,
    basematerials_id: str,
    pbr_materials: List[Tuple[str, Dict]],
    next_resource_id: int,
) -> int:
    """
    Write PBR display properties for materials.

    Supports all three 3MF Materials Extension workflows:
    - Translucent (transmission > 0.01): translucentdisplayproperties
    - Metallic (metallic > 0.5): pbmetallicdisplayproperties
    - Specular (default for dielectrics): pbspeculardisplayproperties

    Per-material workflow selection based on material characteristics.

    :param resources_element: The <resources> element to add display properties to
    :param basematerials_element: The <basematerials> element to link
    :param basematerials_id: The ID of the basematerials element
    :param pbr_materials: List of (name, pbr_data) tuples
    :param next_resource_id: Next available resource ID
    :return: Updated next_resource_id
    """
    if not pbr_materials:
        return next_resource_id

    # Check if any material has meaningful PBR data (not just defaults)
    def has_meaningful_pbr(pbr):
        metallic_check = pbr.get("metallic", 0) > 0.01
        roughness_check = pbr.get("roughness", 1) < 0.99
        transmission_check = pbr.get("transmission", 0) > 0.01
        specular_check = pbr.get("specular_ior_level", 0.5) != 0.5
        return metallic_check or roughness_check or transmission_check or specular_check

    has_pbr_data = any(has_meaningful_pbr(pbr) for _, pbr in pbr_materials)

    if not has_pbr_data:
        debug("No meaningful PBR data to export, skipping display properties")
        return next_resource_id

    # Categorize materials by workflow
    # Any material with metallic > 0.01 uses metallic workflow (not just > 0.5!)
    # Any material with transmission > 0.01 uses translucent workflow
    translucent_materials = []
    metallic_materials = []
    specular_materials = []

    for material_name, pbr in pbr_materials:
        transmission = pbr.get("transmission", 0)
        metallic = pbr.get("metallic", 0)

        if transmission > 0.01:
            translucent_materials.append((material_name, pbr))
        elif metallic > 0.01:  # Fixed: was > 0.5, which lost partial metallic values!
            metallic_materials.append((material_name, pbr))
        else:
            specular_materials.append((material_name, pbr))

    # Write each workflow type as needed
    # Note: 3MF allows only ONE displaypropertiesid per basematerials,
    # so we choose the dominant workflow for all materials
    # Priority: translucent > metallic > specular (most to least specialized)

    if translucent_materials:
        # Write translucentdisplayproperties for ALL materials
        display_props_id = str(next_resource_id)
        next_resource_id += 1

        translucent_props = xml.etree.ElementTree.SubElement(
            resources_element,
            f"{{{MATERIAL_NAMESPACE}}}translucentdisplayproperties",
            attrib={"id": display_props_id},
        )

        for material_name, pbr in pbr_materials:
            ior = pbr.get("ior", 1.45)
            roughness = pbr.get("roughness", 0.0)
            attenuation = pbr.get("attenuation")
            transmission = pbr.get("transmission", 1.0)

            attrib = {
                "name": material_name,
                "refractiveindex": f"{ior:.6g} {ior:.6g} {ior:.6g}",
                "roughness": f"{roughness:.6g}",
            }

            # Store transmission as custom attribute for round-trip
            # (3MF spec doesn't have transmission in translucent - it's assumed 1.0)
            if transmission < 0.99:
                attrib["blender_transmission"] = f"{transmission:.6g}"

            if attenuation:
                attrib["attenuation"] = f"{attenuation[0]:.6g} {attenuation[1]:.6g} {attenuation[2]:.6g}"
            else:
                attrib["attenuation"] = "0 0 0"

            xml.etree.ElementTree.SubElement(
                translucent_props,
                f"{{{MATERIAL_NAMESPACE}}}translucent",
                attrib=attrib,
            )

        basematerials_element.set("displaypropertiesid", display_props_id)
        debug(f"Exported {len(pbr_materials)} translucent display properties (ID: {display_props_id})")

    elif metallic_materials:
        # Write pbmetallicdisplayproperties for ALL materials
        display_props_id = str(next_resource_id)
        next_resource_id += 1

        metallic_props = xml.etree.ElementTree.SubElement(
            resources_element,
            f"{{{MATERIAL_NAMESPACE}}}pbmetallicdisplayproperties",
            attrib={"id": display_props_id},
        )

        for material_name, pbr in pbr_materials:
            metallic = pbr.get("metallic", 0.0)
            roughness = pbr.get("roughness", 1.0)

            xml.etree.ElementTree.SubElement(
                metallic_props,
                f"{{{MATERIAL_NAMESPACE}}}pbmetallic",
                attrib={
                    "name": material_name,
                    "metallicness": f"{metallic:.6g}",
                    "roughness": f"{roughness:.6g}",
                },
            )

        basematerials_element.set("displaypropertiesid", display_props_id)
        debug(f"Exported {len(pbr_materials)} metallic display properties (ID: {display_props_id})")

    else:
        # Write pbspeculardisplayproperties (dielectric materials)
        display_props_id = str(next_resource_id)
        next_resource_id += 1

        specular_props = xml.etree.ElementTree.SubElement(
            resources_element,
            f"{{{MATERIAL_NAMESPACE}}}pbspeculardisplayproperties",
            attrib={"id": display_props_id},
        )

        for material_name, pbr in pbr_materials:
            specular_color = pbr.get("specular_color", (0.22, 0.22, 0.22))
            glossiness = pbr.get("glossiness", 0.5)

            # Convert linear specular color to sRGB hex
            sr = min(255, max(0, int(specular_color[0] * 255)))
            sg = min(255, max(0, int(specular_color[1] * 255)))
            sb = min(255, max(0, int(specular_color[2] * 255)))
            specular_hex = "#%02X%02X%02X" % (sr, sg, sb)

            xml.etree.ElementTree.SubElement(
                specular_props,
                f"{{{MATERIAL_NAMESPACE}}}pbspecular",
                attrib={
                    "name": material_name,
                    "specularcolor": specular_hex,
                    "glossiness": f"{glossiness:.6g}",
                },
            )

        basematerials_element.set("displaypropertiesid", display_props_id)
        debug(f"Exported {len(pbr_materials)} specular display properties (ID: {display_props_id})")

    return next_resource_id
