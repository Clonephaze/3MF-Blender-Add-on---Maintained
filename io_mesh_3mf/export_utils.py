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
Core utility functions for 3MF export.

This module contains:
- Archive management (create_archive, must_preserve, write_core_properties, write_thumbnail)
- Unit conversion
- Geometry writing (check_non_manifold, format_transformation, write_vertices, write_triangles)
- Metadata writing

Materials Extension functionality has been moved to the export_materials package.
Triangle Sets Extension functionality has been moved to export_trianglesets module.

For backward compatibility, this module re-exports functions from:
- export_materials.base
- export_materials.textures
- export_materials.pbr
- export_materials.passthrough
- export_trianglesets
"""

import base64
import bmesh
import datetime
import itertools
import os
import tempfile
import xml.etree.ElementTree
import zipfile
from typing import Optional, Dict, List

import bpy
import mathutils

from .annotations import Annotations
from .utilities import debug, warn, error
from .constants import (
    MODEL_NAMESPACE,
    MODEL_DEFAULT_UNIT,
    CORE_PROPERTIES_LOCATION,
    CORE_PROPERTIES_NAMESPACE,
    DC_NAMESPACE,
    DCTERMS_NAMESPACE,
    conflicting_mustpreserve_contents,
)
from .metadata import Metadata
from .unit_conversions import blender_to_metre, threemf_to_metre

# Re-export from export_materials for backward compatibility
from .export_materials import (  # noqa: F401
    ORCA_FILAMENT_CODES,
    material_to_hex_color,
    get_triangle_color,
    collect_face_colors,
    write_materials,
    write_prusa_filament_colors,
    extract_pbr_from_material as _extract_pbr_from_material,
    write_pbr_display_properties as _write_pbr_display_properties,
    detect_textured_materials,
    detect_pbr_textured_materials,
    write_textures_to_archive,
    write_texture_relationships,
    write_texture_resources,
    get_or_create_tex2coord,
    write_pbr_textures_to_archive,
    write_pbr_texture_display_properties,
    write_passthrough_materials,
    write_passthrough_textures_to_archive,
)

# Re-export from export_trianglesets for backward compatibility
from .export_trianglesets import write_triangle_sets  # noqa: F401


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
        error(f"Unable to write 3MF archive to {filepath}: {e}")
        safe_report({"ERROR"}, f"Unable to write 3MF archive to {filepath}: {e}")
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
    modified = xml.etree.ElementTree.SubElement(
        root, f"{{{DCTERMS_NAMESPACE}}}modified"
    )
    modified.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write the Core Properties file
    document = xml.etree.ElementTree.ElementTree(root)
    try:
        with archive.open(CORE_PROPERTIES_LOCATION, "w") as f:
            document.write(f, xml_declaration=True, encoding="UTF-8")
        debug("Wrote OPC Core Properties to docProps/core.xml")
    except Exception as e:
        error(f"Failed to write Core Properties: {e}")


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
            # Use OpenGL render which captures the viewport
            override = bpy.context.copy()
            override["area"] = view3d_area
            override["region"] = [r for r in view3d_area.regions if r.type == "WINDOW"][
                0
            ]

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
# Geometry Writing
# =============================================================================


def check_non_manifold_geometry(
    blender_objects: List[bpy.types.Object], use_mesh_modifiers: bool
) -> List[str]:
    """
    Check mesh objects for non-manifold geometry using BMesh.

    Non-manifold geometry can cause problems in slicers and is generally
    not suitable for 3D printing. Uses BMesh's C-optimized is_manifold
    property for fast detection.

    Stops checking after finding the first non-manifold object for performance.

    :param blender_objects: List of Blender objects to check.
    :param use_mesh_modifiers: Whether to apply modifiers when getting mesh.
    :return: List with first object name that has non-manifold geometry, or empty list.
    """
    for blender_object in blender_objects:
        if blender_object.type != "MESH":
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

        # Use BMesh for fast C-optimized non-manifold detection
        bm = bmesh.new()
        bm.from_mesh(mesh)

        has_non_manifold = False

        # Check edges - BMesh provides is_manifold property at C level
        for edge in bm.edges:
            if not edge.is_manifold:
                has_non_manifold = True
                break

        # Check vertices for non-manifold (wire verts, etc.)
        if not has_non_manifold:
            for vert in bm.verts:
                if not vert.is_manifold:
                    has_non_manifold = True
                    break

        bm.free()
        eval_object.to_mesh_clear()

        if has_non_manifold:
            # Return immediately after finding first non-manifold object
            return [blender_object.name]

    return []


def format_transformation(transformation: mathutils.Matrix) -> str:
    """
    Formats a transformation matrix in 3MF's formatting.

    :param transformation: The transformation matrix to format.
    :return: A serialisation of the transformation matrix.
    """
    pieces = (row[:3] for row in transformation.transposed())
    formatted_cells = [f"{cell:.9f}" for cell in itertools.chain.from_iterable(pieces)]
    return " ".join(formatted_cells)


def write_vertices(
    mesh_element: xml.etree.ElementTree.Element,
    vertices: List[bpy.types.MeshVertex],
    use_orca_format: str,  # 'PAINT', 'BASEMATERIAL', or 'STANDARD'
    coordinate_precision: int,
) -> None:
    """
    Writes a list of vertices into the specified mesh element.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param vertices: A list of Blender vertices to add.
    :param use_orca_format: Material export mode - affects namespace handling.
    :param coordinate_precision: Number of decimal places for coordinates.
    """
    vertices_element = xml.etree.ElementTree.SubElement(
        mesh_element, f"{{{MODEL_NAMESPACE}}}vertices"
    )

    vertex_name = f"{{{MODEL_NAMESPACE}}}vertex"
    if use_orca_format in ("PAINT", "BASEMATERIAL"):
        # PAINT and BASEMATERIAL modes use short attribute names
        x_name = "x"
        y_name = "y"
        z_name = "z"
    else:
        # STANDARD mode uses fully namespaced attribute names
        x_name = f"{{{MODEL_NAMESPACE}}}x"
        y_name = f"{{{MODEL_NAMESPACE}}}y"
        z_name = f"{{{MODEL_NAMESPACE}}}z"

    decimals = coordinate_precision
    for vertex in vertices:
        vertex_element = xml.etree.ElementTree.SubElement(vertices_element, vertex_name)
        vertex_element.attrib[x_name] = f"{vertex.co[0]:.{decimals}}"
        vertex_element.attrib[y_name] = f"{vertex.co[1]:.{decimals}}"
        vertex_element.attrib[z_name] = f"{vertex.co[2]:.{decimals}}"


def write_triangles(
    mesh_element: xml.etree.ElementTree.Element,
    triangles: List[bpy.types.MeshLoopTriangle],
    object_material_list_index: int,
    material_slots: List[bpy.types.MaterialSlot],
    material_name_to_index: Dict[str, int],
    use_orca_format: str,  # 'PAINT', 'BASEMATERIAL', or 'STANDARD'
    mmu_slicer_format: str,
    vertex_colors: Dict[str, int],
    mesh: Optional[bpy.types.Mesh] = None,
    blender_object: Optional[bpy.types.Object] = None,
    texture_groups: Optional[Dict[str, Dict]] = None,
    basematerials_resource_id: Optional[str] = None,
    segmentation_strings: Optional[Dict[int, str]] = None,
) -> None:
    """
    Writes a list of triangles into the specified mesh element.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param triangles: A list of triangles.
    :param object_material_list_index: The index of the material that the object was written with.
    :param material_slots: List of materials belonging to the object.
    :param material_name_to_index: Mapping from material name to index.
    :param use_orca_format: Material export mode - 'PAINT', 'BASEMATERIAL', or 'STANDARD'.
    :param mmu_slicer_format: The target slicer format ('ORCA' or 'PRUSA').
    :param vertex_colors: Dictionary of color hex to filament index.
    :param mesh: The mesh containing these triangles.
    :param blender_object: The Blender object.
    :param texture_groups: Dict of material_name -> texture group data for UV mapping.
    :param basematerials_resource_id: The ID of the basematerials resource for per-face material refs.
    :param segmentation_strings: Dict of face_index -> segmentation hash string (for PAINT mode).
    """
    debug(
        f"[write_triangles] mode={use_orca_format}, slicer={mmu_slicer_format},",
        f" seg_strings={len(segmentation_strings) if segmentation_strings else 0}"
    )

    triangles_element = xml.etree.ElementTree.SubElement(
        mesh_element, f"{{{MODEL_NAMESPACE}}}triangles"
    )

    triangle_name = f"{{{MODEL_NAMESPACE}}}triangle"
    if use_orca_format in ("PAINT", "BASEMATERIAL"):
        # PAINT and BASEMATERIAL modes use short attribute names
        v1_name = "v1"
        v2_name = "v2"
        v3_name = "v3"
        p1_name = "p1"
        p2_name = "p2"
        p3_name = "p3"
        pid_name = "pid"
    else:
        # STANDARD mode uses fully namespaced attribute names
        v1_name = f"{{{MODEL_NAMESPACE}}}v1"
        v2_name = f"{{{MODEL_NAMESPACE}}}v2"
        v3_name = f"{{{MODEL_NAMESPACE}}}v3"
        p1_name = f"{{{MODEL_NAMESPACE}}}p1"
        p2_name = f"{{{MODEL_NAMESPACE}}}p2"
        p3_name = f"{{{MODEL_NAMESPACE}}}p3"
        pid_name = f"{{{MODEL_NAMESPACE}}}pid"

    # Get active UV layer for texture coordinate export
    uv_layer = None
    if mesh and texture_groups and mesh.uv_layers.active:
        uv_layer = mesh.uv_layers.active

    # Track how many segmentation strings we actually write
    seg_strings_written = 0

    for tri_idx, triangle in enumerate(triangles):
        triangle_element = xml.etree.ElementTree.SubElement(
            triangles_element, triangle_name
        )
        triangle_element.attrib[v1_name] = str(triangle.vertices[0])
        triangle_element.attrib[v2_name] = str(triangle.vertices[1])
        triangle_element.attrib[v3_name] = str(triangle.vertices[2])

        # Handle segmentation strings from UV texture (PAINT mode)
        # Note: segmentation_strings is keyed by polygon_index, not loop_triangle index
        if segmentation_strings and triangle.polygon_index in segmentation_strings:
            seg_string = segmentation_strings[triangle.polygon_index]
            if seg_string:
                if mmu_slicer_format == "PRUSA":
                    # PrusaSlicer format: use mmu_segmentation attribute
                    ns_attr = "{http://schemas.slic3r.org/3mf/2017/06}mmu_segmentation"
                    triangle_element.attrib[ns_attr] = seg_string
                    seg_strings_written += 1
                else:
                    # Orca format: use paint_color attribute
                    triangle_element.attrib["paint_color"] = seg_string
                    seg_strings_written += 1
                continue  # Skip other material handling for this triangle

        # Handle multi-material color zones (BASEMATERIAL mode only)
        if (
            use_orca_format == "BASEMATERIAL"
            and vertex_colors
            and mesh
            and blender_object
        ):
            triangle_color = get_triangle_color(mesh, triangle, blender_object)
            if triangle_color and triangle_color in vertex_colors:
                colorgroup_id = vertex_colors[triangle_color]

                if mmu_slicer_format == "PRUSA":
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
            # Normal material handling (or textured material)
            triangle_material = material_slots[triangle.material_index].material
            if triangle_material is not None:
                triangle_material_name = str(triangle_material.name)

                # Check if this is a textured material
                if (
                    texture_groups
                    and triangle_material_name in texture_groups
                    and uv_layer
                ):
                    # Textured material - use texture2dgroup with UV indices
                    group_data = texture_groups[triangle_material_name]
                    group_id = group_data["group_id"]
                    triangle_element.attrib[pid_name] = group_id

                    # Get UV coordinates for this triangle's loops
                    # triangle.loops gives the 3 loop indices for this triangle
                    uv_data = uv_layer.data
                    loop_indices = triangle.loops

                    # Get or create tex2coord indices for each UV
                    uv1 = uv_data[loop_indices[0]].uv
                    uv2 = uv_data[loop_indices[1]].uv
                    uv3 = uv_data[loop_indices[2]].uv

                    idx1 = get_or_create_tex2coord(group_data, uv1[0], uv1[1])
                    idx2 = get_or_create_tex2coord(group_data, uv2[0], uv2[1])
                    idx3 = get_or_create_tex2coord(group_data, uv3[0], uv3[1])

                    triangle_element.attrib[p1_name] = str(idx1)
                    triangle_element.attrib[p2_name] = str(idx2)
                    triangle_element.attrib[p3_name] = str(idx3)

                elif triangle_material_name in material_name_to_index:
                    # Standard color material
                    material_index = material_name_to_index[triangle_material_name]
                    if material_index != object_material_list_index:
                        # Must write pid along with p1 per 3MF spec, so import knows
                        # which basematerials group to look up the index in
                        if basematerials_resource_id:
                            triangle_element.attrib[pid_name] = str(
                                basematerials_resource_id
                            )
                        triangle_element.attrib[p1_name] = str(material_index)

    # Log summary of segmentation string writing
    if segmentation_strings:
        debug(
            f"  [write_triangles] Wrote {seg_strings_written} segmentation strings",
            f"to triangles (had {len(segmentation_strings)} available)"
        )


# =============================================================================
# Metadata Writing
# =============================================================================


def write_metadata(
    node: xml.etree.ElementTree.Element, metadata: Metadata, use_orca_format: str
) -> None:  # 'PAINT', 'BASEMATERIAL', or 'STANDARD'
    """
    Writes metadata from a metadata storage into an XML node.
    :param node: The node to add <metadata> tags to.
    :param metadata: The collection of metadata to write to that node.
    :param use_orca_format: Material export mode - affects namespace handling.
    """

    def attr(name: str) -> str:
        if use_orca_format in ("PAINT", "BASEMATERIAL"):
            return name
        return f"{{{MODEL_NAMESPACE}}}{name}"

    for metadata_entry in metadata.values():
        metadata_node = xml.etree.ElementTree.SubElement(
            node, f"{{{MODEL_NAMESPACE}}}metadata"
        )
        metadata_name = str(metadata_entry.name)
        metadata_value = (
            str(metadata_entry.value) if metadata_entry.value is not None else ""
        )
        metadata_node.attrib[attr("name")] = metadata_name
        if metadata_entry.preserve:
            metadata_node.attrib[attr("preserve")] = "1"
        if metadata_entry.datatype:
            metadata_datatype = str(metadata_entry.datatype)
            metadata_node.attrib[attr("type")] = metadata_datatype
        metadata_node.text = metadata_value
