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
Utility functions for 3MF export: geometry, materials, archive, and metadata handling.
"""

import base64
import collections
import datetime
import itertools
import logging
import os
import tempfile
import xml.etree.ElementTree
import zipfile
from typing import Optional, Dict, List, Tuple

import bpy
import bpy_extras.node_shader_utils
import mathutils

from .annotations import Annotations
from .constants import (
    MODEL_NAMESPACE,
    MODEL_DEFAULT_UNIT,
    MATERIAL_NAMESPACE,
    CORE_PROPERTIES_LOCATION,
    CORE_PROPERTIES_NAMESPACE,
    DC_NAMESPACE,
    DCTERMS_NAMESPACE,
    TRIANGLE_SETS_NAMESPACE,
    conflicting_mustpreserve_contents,
)
from .metadata import Metadata
from .unit_conversions import blender_to_metre, threemf_to_metre

# Orca Slicer paint_color encoding for filament IDs
# This matches CONST_FILAMENTS in OrcaSlicer's Model.cpp
# Index 0 = no color (base extruder), 1-32 = filament IDs
ORCA_FILAMENT_CODES = [
    "", "4", "8", "0C", "1C", "2C", "3C", "4C", "5C", "6C", "7C", "8C", "9C", "AC", "BC", "CC", "DC",
    "EC", "0FC", "1FC", "2FC", "3FC", "4FC", "5FC", "6FC", "7FC", "8FC", "9FC", "AFC", "BFC",
    "CFC", "DFC", "EFC",
]

log = logging.getLogger(__name__)


# =============================================================================
# Archive Management
# =============================================================================

def create_archive(filepath: str, safe_report) -> Optional[zipfile.ZipFile]:
    """
    Creates an empty 3MF archive.

    The archive is complete according to the 3MF specs except that the actual 3dmodel.model file is missing.
    :param filepath: The path to write the file to.
    :param safe_report: Callable for reporting errors/warnings.
    :return: A zip archive that other functions can add things to.
    """
    try:
        archive = zipfile.ZipFile(
            filepath, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        )

        # Store the file annotations we got from imported 3MF files, and store them in the archive.
        annotations = Annotations()
        annotations.retrieve()
        annotations.write_rels(archive)
        annotations.write_content_types(archive)
        must_preserve(archive)
    except EnvironmentError as e:
        log.error(f"Unable to write 3MF archive to {filepath}: {e}")
        safe_report({'ERROR'}, f"Unable to write 3MF archive to {filepath}: {e}")
        return None

    return archive


def must_preserve(archive: zipfile.ZipFile) -> None:
    """
    Write files that must be preserved to the archive.

    These files were stored in the Blender scene in a hidden location.
    :param archive: The archive to write files to.
    """
    for textfile in bpy.data.texts:
        # Cache filename to protect Unicode characters from garbage collection
        filename = str(textfile.name)
        if not filename.startswith(".3mf_preserved/"):
            continue  # Unrelated file. Not ours to read.
        contents = textfile.as_string()
        if contents == conflicting_mustpreserve_contents:
            continue  # This file was in conflict. Don't preserve any copy of it then.
        contents = base64.b85decode(contents.encode("UTF-8"))
        filename = filename[len(".3mf_preserved/"):]
        with archive.open(filename, "w") as f:
            f.write(contents)


def write_core_properties(archive: zipfile.ZipFile) -> None:
    """
    Write OPC Core Properties (Dublin Core metadata) to the archive.

    This adds standard document metadata like creator, creation date, and modification date
    as defined by the Open Packaging Conventions specification.
    :param archive: The 3MF archive to write Core Properties into.
    """
    # Register namespaces for cleaner output
    xml.etree.ElementTree.register_namespace("cp", CORE_PROPERTIES_NAMESPACE)
    xml.etree.ElementTree.register_namespace("dc", DC_NAMESPACE)
    xml.etree.ElementTree.register_namespace("dcterms", DCTERMS_NAMESPACE)

    # Create root element with proper namespaces
    root = xml.etree.ElementTree.Element(
        f"{{{CORE_PROPERTIES_NAMESPACE}}}coreProperties"
    )
    root.set("xmlns:dc", DC_NAMESPACE)
    root.set("xmlns:dcterms", DCTERMS_NAMESPACE)

    # dc:creator - who created this file
    creator = xml.etree.ElementTree.SubElement(root, f"{{{DC_NAMESPACE}}}creator")
    creator.text = "Blender 3MF Format Add-on"

    # dcterms:created - when the file was created (W3CDTF format)
    now = datetime.datetime.now(datetime.timezone.utc)
    created = xml.etree.ElementTree.SubElement(root, f"{{{DCTERMS_NAMESPACE}}}created")
    created.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # dcterms:modified - when the file was last modified
    modified = xml.etree.ElementTree.SubElement(root, f"{{{DCTERMS_NAMESPACE}}}modified")
    modified.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write the Core Properties file
    document = xml.etree.ElementTree.ElementTree(root)
    try:
        with archive.open(CORE_PROPERTIES_LOCATION, "w") as f:
            document.write(f, xml_declaration=True, encoding="UTF-8")
        log.info("Wrote OPC Core Properties to docProps/core.xml")
    except Exception as e:
        log.error(f"Failed to write Core Properties: {e}")


def write_thumbnail(archive: zipfile.ZipFile) -> None:
    """
    Generate a thumbnail and save it to the 3MF archive.

    Renders a small preview of the current viewport and saves it as
    Metadata/thumbnail.png in the 3MF archive.

    :param archive: The 3MF archive to write the thumbnail into.
    """
    try:
        # Thumbnail dimensions (3MF spec recommends these sizes)
        thumb_width = 256
        thumb_height = 256

        # Find a 3D viewport to render from
        view3d_area = None
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    view3d_area = area
                    break
            if view3d_area:
                break

        if not view3d_area:
            log.info("No 3D viewport found for thumbnail generation")
            return

        # Create a temporary file for the render
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
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
            scene.render.image_settings.file_format = 'PNG'
            scene.render.filepath = temp_path

            # Render viewport (much faster than full render)
            # Use OpenGL render which captures the viewport
            override = bpy.context.copy()
            override['area'] = view3d_area
            override['region'] = [r for r in view3d_area.regions if r.type == 'WINDOW'][0]

            with bpy.context.temp_override(**override):
                bpy.ops.render.opengl(write_still=True)

            # Read the rendered PNG
            with open(temp_path, 'rb') as png_file:
                png_data = png_file.read()

            # Write to 3MF archive
            with archive.open("Metadata/thumbnail.png", "w") as f:
                f.write(png_data)

            log.info(f"Wrote thumbnail.png ({thumb_width}x{thumb_height}) from viewport render")

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
        log.warning(f"Failed to write thumbnail: {e}")
        # Non-critical, don't fail the export


# =============================================================================
# Unit Conversion
# =============================================================================

def unit_scale(context: bpy.types.Context, global_scale: float) -> float:
    """
    Get the scaling factor we need to transform the document to millimetres.
    :param context: The Blender context to get the unit from.
    :param global_scale: User-specified global scale multiplier.
    :return: Floating point value that we need to scale this model by.
    """
    scale = global_scale

    blender_unit_to_metre = context.scene.unit_settings.scale_length
    if blender_unit_to_metre == 0:  # Fallback for special cases.
        blender_unit = context.scene.unit_settings.length_unit
        blender_unit_to_metre = blender_to_metre[blender_unit]

    threemf_unit = MODEL_DEFAULT_UNIT
    threemf_unit_to_metre = threemf_to_metre[threemf_unit]

    # Scale from Blender scene units to 3MF units.
    scale *= blender_unit_to_metre / threemf_unit_to_metre
    return scale


# =============================================================================
# Material Handling
# =============================================================================

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
        principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
            material, is_readonly=True
        )
        base_color = principled.base_color
        # Check if it's not the default gray (0.8, 0.8, 0.8)
        if (base_color and not (abs(base_color[0] - 0.8) < 0.01
                                and abs(base_color[1] - 0.8) < 0.01
                                and abs(base_color[2] - 0.8) < 0.01)):
            color = base_color

    # Fall back to diffuse_color for simple materials
    if color is None:
        color = material.diffuse_color[:3]

    # Blender's diffuse_color is already in sRGB display space - no conversion needed
    red = min(255, round(color[0] * 255))
    green = min(255, round(color[1] * 255))
    blue = min(255, round(color[2] * 255))
    return "#%0.2X%0.2X%0.2X" % (red, green, blue)


def get_triangle_color(mesh: bpy.types.Mesh, triangle: bpy.types.MeshLoopTriangle,
                       blender_object: bpy.types.Object) -> Optional[str]:
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


def collect_face_colors(blender_objects: List[bpy.types.Object],
                        use_mesh_modifiers: bool,
                        safe_report) -> Dict[str, int]:
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
        if blender_object.type != 'MESH':
            continue

        objects_processed += 1
        log.info(f"Processing object: {blender_object.name}")

        # Get evaluated mesh with modifiers applied
        if use_mesh_modifiers:
            dependency_graph = bpy.context.evaluated_depsgraph_get()
            eval_object = blender_object.evaluated_get(dependency_graph)
        else:
            eval_object = blender_object

        try:
            mesh = eval_object.to_mesh()
        except RuntimeError:
            log.warning(f"Could not get mesh for object: {blender_object.name}")
            continue

        if mesh is None:
            log.warning(f"Mesh is None for object: {blender_object.name}")
            continue

        # Extract colors from face material assignments
        log.info(f"Object {blender_object.name}: {len(mesh.vertices)} vertices, {len(mesh.polygons)} faces")

        # Get all materials used by faces
        materials_used = set()
        for poly in mesh.polygons:
            if poly.material_index < len(blender_object.material_slots):
                materials_used.add(poly.material_index)

        log.info(f"Object uses {len(materials_used)} different materials across its faces")

        # Extract color from each material
        for mat_idx in materials_used:
            if mat_idx < len(blender_object.material_slots):
                material = blender_object.material_slots[mat_idx].material
                if material is None:
                    log.warning(f"Material slot {mat_idx} is empty")
                    continue

                color_hex = material_to_hex_color(material)
                if color_hex:
                    unique_colors.add(color_hex)
                    log.info(f"Face color: {color_hex} from material '{material.name}'")

        # Clean up the temporary mesh
        eval_object.to_mesh_clear()

    # Sort colors for consistent ordering and create index mapping
    # IMPORTANT: Start at index 1 because Orca's paint_color codes:
    #   - "" (empty/no attribute) = no paint, use object base material
    #   - "4" = filament 1, "8" = filament 2, etc.
    # So all colored faces need paint_color attributes starting from index 1
    sorted_colors = sorted(unique_colors)
    color_to_index = {color: idx + 1 for idx, color in enumerate(sorted_colors)}

    log.info(f"Collected {len(unique_colors)} unique colors from {objects_processed} objects for Orca export")
    log.info(f"Colors: {sorted_colors}")

    # Report to user
    if objects_processed == 0:
        safe_report({'ERROR'}, "No mesh objects found to export!")
    else:
        safe_report({'INFO'}, f"Detected {len(unique_colors)} face colors for Orca export: {sorted_colors}")

    return color_to_index


def write_materials(resources_element: xml.etree.ElementTree.Element,
                    blender_objects: List[bpy.types.Object],
                    use_orca_format: bool,
                    vertex_colors: Dict[str, int],
                    next_resource_id: int,
                    export_pbr: bool = True) -> Tuple[Dict[str, int], int, str]:
    """
    Write the materials on the specified blender objects to a 3MF document.

    Supports:
    - Core spec <basematerials> with displaycolor
    - PBR display properties (metallic, specular, translucent workflows)
    - Orca Slicer colorgroup format

    :param resources_element: A <resources> node from a 3MF document.
    :param blender_objects: A list of Blender objects that may have materials.
    :param use_orca_format: Whether to use Orca Slicer format.
    :param vertex_colors: Dictionary of color hex to index for Orca mode.
    :param next_resource_id: Next available resource ID.
    :param export_pbr: Whether to export PBR display properties (default True).
    :return: Tuple of (name_to_index mapping, updated next_resource_id, material_resource_id).
    """
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

        log.info(f"Created {len(sorted_colors)} colorgroups for Orca: {name_to_index}")
        return name_to_index, next_resource_id, material_resource_id

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

            # Wrap this material into a principled render node, to convert its color to sRGB.
            principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
                material, is_readonly=True
            )
            color = principled.base_color
            red = min(255, round(color[0] * 255))
            green = min(255, round(color[1] * 255))
            blue = min(255, round(color[2] * 255))
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
                pbr_data = _extract_pbr_from_material(material, principled)
                pbr_materials.append((material_name, pbr_data))

            next_index += 1

    # Write PBR display properties if we have any meaningful PBR data
    if export_pbr and pbr_materials and basematerials_element is not None:
        next_resource_id = _write_pbr_display_properties(
            resources_element, basematerials_element, material_resource_id,
            pbr_materials, next_resource_id
        )

    return name_to_index, next_resource_id, material_resource_id


def _extract_pbr_from_material(material: bpy.types.Material,
                               principled: bpy_extras.node_shader_utils.PrincipledBSDFWrapper) -> Dict:
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
            if hasattr(value, '__iter__') and len(value) >= 3:
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
                if node.type == 'BSDF_PRINCIPLED':
                    if 'Specular Tint' in node.inputs:
                        tint_input = node.inputs['Specular Tint']
                        if hasattr(tint_input, 'default_value'):
                            val = tint_input.default_value
                            # Blender 4.x: It's an RGBA color
                            if hasattr(val, '__iter__') and len(val) >= 3:
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
                if node.type == 'BSDF_PRINCIPLED':
                    # Blender 4.0+ uses 'Transmission Weight' instead of 'Transmission'
                    if 'Transmission Weight' in node.inputs:
                        pbr_data["transmission"] = safe_float(
                            node.inputs['Transmission Weight'].default_value, 0.0)
                    elif 'Transmission' in node.inputs:
                        pbr_data["transmission"] = safe_float(
                            node.inputs['Transmission'].default_value, 0.0)
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


def _write_pbr_display_properties(resources_element: xml.etree.ElementTree.Element,
                                  basematerials_element: xml.etree.ElementTree.Element,
                                  basematerials_id: str,
                                  pbr_materials: List[Tuple[str, Dict]],
                                  next_resource_id: int) -> int:
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
        log.debug("No meaningful PBR data to export, skipping display properties")
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
        log.info(f"Exported {len(pbr_materials)} translucent display properties (ID: {display_props_id})")

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
        log.info(f"Exported {len(pbr_materials)} metallic display properties (ID: {display_props_id})")

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
        log.info(f"Exported {len(pbr_materials)} specular display properties (ID: {display_props_id})")

    return next_resource_id


def write_prusa_filament_colors(archive: zipfile.ZipFile, vertex_colors: Dict[str, int]) -> None:
    """
    Write filament color mapping for PrusaSlicer MMU export.

    Stores colors in Metadata/blender_filament_colors.txt for round-trip import.
    Format: paint_code=hex_color (one per line)
    """
    if not vertex_colors:
        return

    try:
        # Sort by colorgroup index to maintain order
        sorted_colors = sorted(vertex_colors.items(), key=lambda x: x[1])

        # Build color map: colorgroup_id -> hex_color
        color_lines = []
        for hex_color, colorgroup_id in sorted_colors:
            # Map colorgroup_id to paint code
            if colorgroup_id < len(ORCA_FILAMENT_CODES):
                paint_code = ORCA_FILAMENT_CODES[colorgroup_id]
                if paint_code:  # Skip empty (default)
                    color_lines.append(f"{paint_code}={hex_color}")

        if color_lines:
            color_data = "\n".join(color_lines)
            with archive.open("Metadata/blender_filament_colors.txt", "w") as f:
                f.write(color_data.encode('UTF-8'))
            log.info(f"Wrote {len(color_lines)} filament colors to metadata")
    except Exception as e:
        log.warning(f"Failed to write filament colors: {e}")


# =============================================================================
# Geometry Writing
# =============================================================================

def check_non_manifold_geometry(blender_objects: List[bpy.types.Object],
                                use_mesh_modifiers: bool) -> List[str]:
    """
    Check all mesh objects for non-manifold geometry.

    Non-manifold geometry can cause problems in slicers and is generally
    not suitable for 3D printing.
    :param blender_objects: List of Blender objects to check.
    :param use_mesh_modifiers: Whether to apply modifiers when getting mesh.
    :return: List of object names with non-manifold geometry.
    """
    non_manifold_objects = []

    for blender_object in blender_objects:
        if blender_object.type != 'MESH':
            continue

        # Get mesh data with modifiers applied if needed
        if use_mesh_modifiers:
            dependency_graph = bpy.context.evaluated_depsgraph_get()
            eval_object = blender_object.evaluated_get(dependency_graph)
        else:
            eval_object = blender_object

        try:
            mesh = eval_object.to_mesh()
        except RuntimeError:
            continue

        if mesh is None:
            continue

        # Check for non-manifold geometry using edge_keys for O(n) performance
        has_non_manifold = False

        # Count edge usage across all polygons
        edge_face_count = collections.Counter()
        for poly in mesh.polygons:
            for edge_key in poly.edge_keys:
                edge_face_count[edge_key] += 1

        # Check if any edge is used by != 2 faces
        for count in edge_face_count.values():
            if count != 2:
                has_non_manifold = True
                break

        # Check for loose vertices (not part of any face)
        if not has_non_manifold and len(mesh.vertices) > 0:
            vertices_in_faces = set()
            for poly in mesh.polygons:
                vertices_in_faces.update(poly.vertices)
            if len(vertices_in_faces) < len(mesh.vertices):
                has_non_manifold = True

        eval_object.to_mesh_clear()

        if has_non_manifold:
            non_manifold_objects.append(blender_object.name)

    return non_manifold_objects


def format_transformation(transformation: mathutils.Matrix) -> str:
    """
    Formats a transformation matrix in 3MF's formatting.

    :param transformation: The transformation matrix to format.
    :return: A serialisation of the transformation matrix.
    """
    pieces = (
        row[:3] for row in transformation.transposed()
    )
    formatted_cells = [
        f"{cell:.9f}" for cell in itertools.chain.from_iterable(pieces)
    ]
    return " ".join(formatted_cells)


def write_vertices(mesh_element: xml.etree.ElementTree.Element,
                   vertices: List[bpy.types.MeshVertex],
                   use_orca_format: bool,
                   coordinate_precision: int) -> None:
    """
    Writes a list of vertices into the specified mesh element.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param vertices: A list of Blender vertices to add.
    :param use_orca_format: Whether to use Orca format (affects namespace handling).
    :param coordinate_precision: Number of decimal places for coordinates.
    """
    vertices_element = xml.etree.ElementTree.SubElement(
        mesh_element, f"{{{MODEL_NAMESPACE}}}vertices"
    )

    vertex_name = f"{{{MODEL_NAMESPACE}}}vertex"
    if use_orca_format:
        x_name = "x"
        y_name = "y"
        z_name = "z"
    else:
        x_name = f"{{{MODEL_NAMESPACE}}}x"
        y_name = f"{{{MODEL_NAMESPACE}}}y"
        z_name = f"{{{MODEL_NAMESPACE}}}z"

    decimals = coordinate_precision
    for vertex in vertices:
        vertex_element = xml.etree.ElementTree.SubElement(
            vertices_element, vertex_name
        )
        vertex_element.attrib[x_name] = f"{vertex.co[0]:.{decimals}}"
        vertex_element.attrib[y_name] = f"{vertex.co[1]:.{decimals}}"
        vertex_element.attrib[z_name] = f"{vertex.co[2]:.{decimals}}"


def write_triangles(
    mesh_element: xml.etree.ElementTree.Element,
    triangles: List[bpy.types.MeshLoopTriangle],
    object_material_list_index: int,
    material_slots: List[bpy.types.MaterialSlot],
    material_name_to_index: Dict[str, int],
    use_orca_format: bool,
    mmu_slicer_format: str,
    vertex_colors: Dict[str, int],
    mesh: Optional[bpy.types.Mesh] = None,
    blender_object: Optional[bpy.types.Object] = None
) -> None:
    """
    Writes a list of triangles into the specified mesh element.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param triangles: A list of triangles.
    :param object_material_list_index: The index of the material that the object was written with.
    :param material_slots: List of materials belonging to the object.
    :param material_name_to_index: Mapping from material name to index.
    :param use_orca_format: Whether to use Orca format.
    :param mmu_slicer_format: The target slicer format ('ORCA' or 'PRUSA').
    :param vertex_colors: Dictionary of color hex to filament index.
    :param mesh: The mesh containing these triangles.
    :param blender_object: The Blender object.
    """
    triangles_element = xml.etree.ElementTree.SubElement(
        mesh_element, f"{{{MODEL_NAMESPACE}}}triangles"
    )

    triangle_name = f"{{{MODEL_NAMESPACE}}}triangle"
    if use_orca_format:
        v1_name = "v1"
        v2_name = "v2"
        v3_name = "v3"
        p1_name = "p1"
        pid_name = "pid"
    else:
        v1_name = f"{{{MODEL_NAMESPACE}}}v1"
        v2_name = f"{{{MODEL_NAMESPACE}}}v2"
        v3_name = f"{{{MODEL_NAMESPACE}}}v3"
        p1_name = f"{{{MODEL_NAMESPACE}}}p1"
        pid_name = f"{{{MODEL_NAMESPACE}}}pid"

    for triangle in triangles:
        triangle_element = xml.etree.ElementTree.SubElement(
            triangles_element, triangle_name
        )
        triangle_element.attrib[v1_name] = str(triangle.vertices[0])
        triangle_element.attrib[v2_name] = str(triangle.vertices[1])
        triangle_element.attrib[v3_name] = str(triangle.vertices[2])

        # Handle multi-material color zones based on format
        if use_orca_format and vertex_colors and mesh and blender_object:
            triangle_color = get_triangle_color(mesh, triangle, blender_object)
            if triangle_color and triangle_color in vertex_colors:
                colorgroup_id = vertex_colors[triangle_color]

                if mmu_slicer_format == 'PRUSA':
                    # PrusaSlicer format: use mmu_segmentation attribute
                    if colorgroup_id < len(ORCA_FILAMENT_CODES):
                        paint_code = ORCA_FILAMENT_CODES[colorgroup_id]
                        if paint_code:
                            ns_attr = "{http://schemas.slic3r.org/3mf/2017/06}mmu_segmentation"
                            triangle_element.attrib[ns_attr] = paint_code
                else:
                    # Orca format: use pid/p1 + paint_color
                    triangle_element.attrib[pid_name] = str(colorgroup_id)
                    triangle_element.attrib[p1_name] = "0"

                    if colorgroup_id < len(ORCA_FILAMENT_CODES):
                        paint_code = ORCA_FILAMENT_CODES[colorgroup_id]
                        if paint_code:
                            triangle_element.attrib["paint_color"] = paint_code
        elif triangle.material_index < len(material_slots):
            # Normal material handling
            triangle_material = material_slots[triangle.material_index].material
            if triangle_material is not None:
                triangle_material_name = str(triangle_material.name)
                if triangle_material_name in material_name_to_index:
                    material_index = material_name_to_index[triangle_material_name]
                    if material_index != object_material_list_index:
                        triangle_element.attrib[p1_name] = str(material_index)


def write_triangle_sets(
    mesh_element: xml.etree.ElementTree.Element,
    mesh: bpy.types.Mesh
) -> None:
    """
    Writes triangle sets from Blender mesh attributes into the mesh element.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param mesh: The Blender mesh containing triangle set attributes to export.
    """
    attr_name = "3mf_triangle_set"
    if attr_name not in mesh.attributes:
        return

    set_names = mesh.get("3mf_triangle_set_names", [])
    if not set_names:
        return

    # Build mapping of set_index -> list of triangle indices
    num_faces = len(mesh.polygons)
    set_values = [0] * num_faces
    mesh.attributes[attr_name].data.foreach_get("value", set_values)

    # Group triangles by set index
    set_to_triangles: Dict[int, List[int]] = {}
    for poly_idx, set_idx in enumerate(set_values):
        if set_idx > 0:
            if set_idx not in set_to_triangles:
                set_to_triangles[set_idx] = []
            set_to_triangles[set_idx].append(poly_idx)

    if not set_to_triangles:
        return

    trianglesets_element = xml.etree.ElementTree.SubElement(
        mesh_element, f"{{{TRIANGLE_SETS_NAMESPACE}}}trianglesets"
    )

    for set_idx, triangle_indices in sorted(set_to_triangles.items()):
        if set_idx <= len(set_names):
            set_name = str(set_names[set_idx - 1])
        else:
            set_name = f"TriangleSet_{set_idx}"

        triangleset_element = xml.etree.ElementTree.SubElement(
            trianglesets_element,
            f"{{{TRIANGLE_SETS_NAMESPACE}}}triangleset"
        )
        triangleset_element.attrib["name"] = set_name
        triangleset_element.attrib["identifier"] = set_name

        triangle_indices = sorted(triangle_indices)

        # Use refrange for consecutive sequences, ref for isolated indices
        i = 0
        while i < len(triangle_indices):
            start = triangle_indices[i]
            end = start
            while i + 1 < len(triangle_indices) and triangle_indices[i + 1] == end + 1:
                i += 1
                end = triangle_indices[i]

            if end - start >= 2:
                refrange_element = xml.etree.ElementTree.SubElement(
                    triangleset_element,
                    f"{{{TRIANGLE_SETS_NAMESPACE}}}refrange"
                )
                refrange_element.attrib["startindex"] = str(start)
                refrange_element.attrib["endindex"] = str(end)
            else:
                for idx in range(start, end + 1):
                    ref_element = xml.etree.ElementTree.SubElement(
                        triangleset_element,
                        f"{{{TRIANGLE_SETS_NAMESPACE}}}ref"
                    )
                    ref_element.attrib["index"] = str(idx)
            i += 1

        log.info(f"Exported triangle set '{set_name}' with {len(triangle_indices)} triangles")


# =============================================================================
# Metadata Writing
# =============================================================================

def write_metadata(node: xml.etree.ElementTree.Element, metadata: Metadata,
                   use_orca_format: bool) -> None:
    """
    Writes metadata from a metadata storage into an XML node.
    :param node: The node to add <metadata> tags to.
    :param metadata: The collection of metadata to write to that node.
    :param use_orca_format: Whether to use Orca format (affects namespace handling).
    """
    def attr(name: str) -> str:
        if use_orca_format:
            return name
        return f"{{{MODEL_NAMESPACE}}}{name}"

    for metadata_entry in metadata.values():
        metadata_node = xml.etree.ElementTree.SubElement(
            node, f"{{{MODEL_NAMESPACE}}}metadata"
        )
        metadata_name = str(metadata_entry.name)
        metadata_value = str(metadata_entry.value) if metadata_entry.value is not None else ""
        metadata_node.attrib[attr("name")] = metadata_name
        if metadata_entry.preserve:
            metadata_node.attrib[attr("preserve")] = "1"
        if metadata_entry.datatype:
            metadata_datatype = str(metadata_entry.datatype)
            metadata_node.attrib[attr("type")] = metadata_datatype
        metadata_node.text = metadata_value
