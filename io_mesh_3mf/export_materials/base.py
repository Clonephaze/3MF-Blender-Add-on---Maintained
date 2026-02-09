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
Base material export functionality for 3MF Materials Extension.

Handles:
- Color extraction from Blender materials
- Basematerials element writing
- Colorgroup writing for Orca Slicer format
- Face color collection for multi-material export
"""

import xml.etree.ElementTree
from typing import Optional, Dict, List, Tuple

import bpy
import bpy_extras.node_shader_utils

from ..constants import (
    MODEL_NAMESPACE,
    MATERIAL_NAMESPACE,
)
from ..utilities import debug, warn, linear_to_srgb

# Orca Slicer paint_color encoding for filament IDs
# This matches CONST_FILAMENTS in OrcaSlicer's Model.cpp
# Index 0 = no color (base extruder), 1-32 = filament IDs
ORCA_FILAMENT_CODES = [
    "",
    "4",
    "8",
    "0C",
    "1C",
    "2C",
    "3C",
    "4C",
    "5C",
    "6C",
    "7C",
    "8C",
    "9C",
    "AC",
    "BC",
    "CC",
    "DC",
    "EC",
    "0FC",
    "1FC",
    "2FC",
    "3FC",
    "4FC",
    "5FC",
    "6FC",
    "7FC",
    "8FC",
    "9FC",
    "AFC",
    "BFC",
    "CFC",
    "DFC",
    "EFC",
]


def material_to_hex_color(material: bpy.types.Material) -> Optional[str]:
    """
    Extract hex color string from a Blender material.

    Tries Principled BSDF first (for node-based materials), falls back to diffuse_color.
    Skips default gray (0.8, 0.8, 0.8) from Principled BSDF.
    Converts from linear (Blender) to sRGB (3MF hex) color space.

    :param material: The Blender material to extract color from.
    :return: Hex color string like "#RRGGBB" or None if no material.
    """
    if material is None:
        return None

    color = None

    # Try Principled BSDF first for materials with node setup
    if material.use_nodes and material.node_tree:
        principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(material, is_readonly=True)
        base_color = principled.base_color
        # Check if it's not the default gray (0.8, 0.8, 0.8)
        if base_color and not (
            abs(base_color[0] - 0.8) < 0.01 and abs(base_color[1] - 0.8) < 0.01 and abs(base_color[2] - 0.8) < 0.01
        ):
            color = base_color

    # Fall back to diffuse_color for simple materials
    if color is None:
        color = material.diffuse_color[:3]

    # Blender stores colors in linear space; 3MF hex colors are sRGB.
    # Convert linear → sRGB before encoding.
    red = min(255, max(0, round(linear_to_srgb(color[0]) * 255)))
    green = min(255, max(0, round(linear_to_srgb(color[1]) * 255)))
    blue = min(255, max(0, round(linear_to_srgb(color[2]) * 255)))
    return "#%0.2X%0.2X%0.2X" % (red, green, blue)


def get_triangle_color(
    mesh: bpy.types.Mesh,
    triangle: bpy.types.MeshLoopTriangle,
    blender_object: bpy.types.Object,
) -> Optional[str]:
    """
    Get the color for a specific triangle from its face's material assignment.

    :param mesh: The mesh containing the triangle.
    :param triangle: The triangle to get the color for.
    :param blender_object: The object the mesh belongs to.
    :return: Hex color string like "#RRGGBB" or None if no color.
    """
    if triangle.material_index < len(blender_object.material_slots):
        material = blender_object.material_slots[triangle.material_index].material
        return material_to_hex_color(material)
    return None


def collect_face_colors(
    blender_objects: List[bpy.types.Object], use_mesh_modifiers: bool, safe_report
) -> Dict[str, int]:
    """
    Collect unique face colors from all objects for Orca color zone export.

    This extracts colors from material assignments per face. Each face can have its own
    material, allowing solid per-face coloring (perfect for cubes with different colored sides).

    :param blender_objects: List of Blender objects to extract colors from.
    :param use_mesh_modifiers: Whether to apply modifiers when getting mesh.
    :param safe_report: Callable for reporting errors/warnings.
    :return: Dictionary mapping color hex strings to filament indices (0-based).
    """
    unique_colors = set()
    objects_processed = 0

    for blender_object in blender_objects:
        if blender_object.type != "MESH":
            continue

        objects_processed += 1
        debug(f"Processing object: {blender_object.name}")

        # Get evaluated mesh with modifiers applied
        if use_mesh_modifiers:
            dependency_graph = bpy.context.evaluated_depsgraph_get()
            eval_object = blender_object.evaluated_get(dependency_graph)
        else:
            eval_object = blender_object

        try:
            mesh = eval_object.to_mesh()
        except RuntimeError:
            warn(f"Could not get mesh for object: {blender_object.name}")
            continue

        if mesh is None:
            warn(f"Mesh is None for object: {blender_object.name}")
            continue

        # Extract colors from face material assignments
        debug(f"Object {blender_object.name}: {len(mesh.vertices)} vertices, {len(mesh.polygons)} faces")

        # Get all materials used by faces
        for face in mesh.polygons:
            if face.material_index < len(blender_object.material_slots):
                material = blender_object.material_slots[face.material_index].material
                if material:
                    color = material_to_hex_color(material)
                    if color:
                        unique_colors.add(color)
                        debug(f"Face {face.index}: material={material.name}, color={color}")

        eval_object.to_mesh_clear()

    # Sort colors for consistent ordering and create index mapping
    # IMPORTANT: Start at index 1 because Orca's paint_color codes:
    #   - "" (empty/no attribute) = no paint, use object base material
    #   - "4" = filament 1, "8" = filament 2, etc.
    # So all colored faces need paint_color attributes starting from index 1
    sorted_colors = sorted(unique_colors)
    color_to_index = {color: idx + 1 for idx, color in enumerate(sorted_colors)}

    debug(f"Collected {len(unique_colors)} unique colors from {objects_processed} objects for Orca export")
    debug(f"Colors: {sorted_colors}")

    # Report to user
    if objects_processed == 0:
        safe_report({"WARNING"}, "No mesh objects found to export")
    else:
        safe_report(
            {"INFO"},
            f"Found {len(unique_colors)} unique colors across {objects_processed} objects",
        )

    return color_to_index


def write_materials(
    resources_element: xml.etree.ElementTree.Element,
    blender_objects: List[bpy.types.Object],
    use_orca_format: str,
    vertex_colors: Dict[str, int],
    next_resource_id: int,
    export_pbr: bool = True,
) -> Tuple[Dict[str, int], int, str, Optional[xml.etree.ElementTree.Element]]:
    """
    Write the materials on the specified blender objects to a 3MF document.

    Supports:
    - Core spec <basematerials> with displaycolor
    - PBR display properties (metallic, specular, translucent workflows)
    - Orca Slicer colorgroup format

    :param resources_element: A <resources> node from a 3MF document.
    :param blender_objects: A list of Blender objects that may have materials.
    :param use_orca_format: Material export mode - 'STANDARD', 'BASEMATERIAL', or 'PAINT'.
    :param vertex_colors: Dictionary of color hex to index for Orca mode.
    :param next_resource_id: Next available resource ID.
    :param export_pbr: Whether to export PBR display properties (default True).
    :return: Tuple of (name_to_index mapping, updated next_resource_id, material_resource_id, basematerials_element).
    """
    # Import here to avoid circular imports
    from .pbr import extract_pbr_from_material, write_pbr_display_properties

    name_to_index = {}
    next_index = 0
    material_resource_id = "-1"

    # Create an element lazily. We don't want to create an element if there are no materials to write.
    basematerials_element = None

    # Orca Slicer mode: Use Materials extension m:colorgroup (vendor-specific)
    if use_orca_format and vertex_colors:
        # Sort colors by their index to maintain consistent ordering
        sorted_colors = sorted(vertex_colors.items(), key=lambda x: x[1])

        # Create a colorgroup for each color (Orca expects one color per group)
        for color_hex, color_index in sorted_colors:
            colorgroup_id = next_resource_id
            next_resource_id += 1

            # Store the first colorgroup ID as our material resource ID
            if color_index == 0:
                material_resource_id = str(colorgroup_id)

            # Create m:colorgroup element
            colorgroup_element = xml.etree.ElementTree.SubElement(
                resources_element,
                f"{{{MATERIAL_NAMESPACE}}}colorgroup",
                attrib={"id": str(colorgroup_id)},
            )
            # Add m:color child with the color
            xml.etree.ElementTree.SubElement(
                colorgroup_element,
                f"{{{MATERIAL_NAMESPACE}}}color",
                attrib={"color": color_hex},
            )
            # Map color hex to colorgroup ID for object/triangle assignment
            name_to_index[color_hex] = colorgroup_id

        debug(f"Created {len(sorted_colors)} colorgroups for Orca: {name_to_index}")
        return name_to_index, next_resource_id, material_resource_id, None

    # Collect PBR data for all materials first
    pbr_materials = []  # List of (material_name, pbr_data_dict)

    # Normal material handling (when not in Orca mode)
    for blender_object in blender_objects:
        for material_slot in blender_object.material_slots:
            material = material_slot.material

            # Skip empty material slots
            if material is None:
                continue

            # Cache material name to protect Unicode characters from garbage collection
            material_name = str(material.name)
            if material_name in name_to_index:
                continue

            # Read linear color from Blender and convert to sRGB for 3MF hex.
            principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(material, is_readonly=True)
            color = principled.base_color
            red = min(255, max(0, round(linear_to_srgb(color[0]) * 255)))
            green = min(255, max(0, round(linear_to_srgb(color[1]) * 255)))
            blue = min(255, max(0, round(linear_to_srgb(color[2]) * 255)))
            alpha = principled.alpha
            if alpha >= 1.0:
                color_hex = "#%0.2X%0.2X%0.2X" % (red, green, blue)
            else:
                alpha = min(255, round(alpha * 255))
                color_hex = "#%0.2X%0.2X%0.2X%0.2X" % (red, green, blue, alpha)

            if basematerials_element is None:
                material_resource_id = str(next_resource_id)
                next_resource_id += 1
                basematerials_element = xml.etree.ElementTree.SubElement(
                    resources_element,
                    f"{{{MODEL_NAMESPACE}}}basematerials",
                    attrib={f"{{{MODEL_NAMESPACE}}}id": material_resource_id},
                )
            xml.etree.ElementTree.SubElement(
                basematerials_element,
                f"{{{MODEL_NAMESPACE}}}base",
                attrib={
                    f"{{{MODEL_NAMESPACE}}}name": material_name,
                    f"{{{MODEL_NAMESPACE}}}displaycolor": color_hex,
                },
            )
            name_to_index[material_name] = next_index

            # Extract PBR properties for this material
            if export_pbr:
                pbr_data = extract_pbr_from_material(material, principled)
                pbr_materials.append((material_name, pbr_data))

            next_index += 1

    # Write PBR display properties if we have any meaningful PBR data
    if export_pbr and pbr_materials and basematerials_element is not None:
        next_resource_id = write_pbr_display_properties(
            resources_element,
            basematerials_element,
            material_resource_id,
            pbr_materials,
            next_resource_id,
        )

    return name_to_index, next_resource_id, material_resource_id, basematerials_element


def write_prusa_filament_colors(archive, vertex_colors: Dict[str, int]) -> None:
    """
    Write filament color mapping for round-trip import.

    Stores color-to-extruder mapping in Metadata/blender_filament_colors.xml.
    This is used as a fallback when importing MMU segmentation if no slicer
    config file is present. Users can still override colors in their slicer.

    Format: XML with extruder elements
    Example:
        <?xml version="1.0" encoding="UTF-8"?>
        <filament_colors>
          <extruder index="0" color="#FF8000"/>
          <extruder index="1" color="#DB5182"/>
        </filament_colors>

    :param archive: The 3MF zip archive
    :param vertex_colors: Dictionary of color hex to colorgroup index
    """
    if not vertex_colors:
        return

    try:
        # Sort by colorgroup index (which maps to extruder index)
        sorted_colors = sorted(vertex_colors.items(), key=lambda x: x[1])

        # Build XML document
        root = xml.etree.ElementTree.Element("filament_colors")
        for hex_color, colorgroup_id in sorted_colors:
            extruder_elem = xml.etree.ElementTree.SubElement(root, "extruder")
            extruder_elem.set("index", str(colorgroup_id))
            extruder_elem.set("color", hex_color.upper())

        if len(sorted_colors) > 0:
            tree = xml.etree.ElementTree.ElementTree(root)
            with archive.open("Metadata/blender_filament_colors.xml", "w") as f:
                tree.write(f, xml_declaration=True, encoding="UTF-8")

            debug(f"Wrote {len(sorted_colors)} filament color mappings to metadata (fallback only)")
    except Exception as e:
        warn(f"Failed to write filament colors: {e}")
