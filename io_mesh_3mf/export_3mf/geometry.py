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
Geometry writing for 3MF export.

Functions for writing mesh geometry to the 3MF XML model:
- write_vertices: Serialize mesh vertices
- write_triangles: Serialize mesh triangles with material/texture/segmentation data
- write_passthrough_triangles: Write triangles with round-trip multiproperties indices
- write_metadata: Write metadata entries to XML
- check_non_manifold_geometry: Detect non-manifold issues using BMesh
"""

import bmesh
import xml.etree.ElementTree
from typing import Optional, Dict, List

import bpy

from ..common.constants import MODEL_NAMESPACE
from ..common.logging import debug, warn
from ..common.metadata import Metadata
from .materials import (
    ORCA_FILAMENT_CODES,
    get_triangle_color,
    get_or_create_tex2coord,
)


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

        bm = bmesh.new()
        bm.from_mesh(mesh)

        has_non_manifold = False

        for edge in bm.edges:
            if not edge.is_manifold:
                has_non_manifold = True
                break

        if not has_non_manifold:
            for vert in bm.verts:
                if not vert.is_manifold:
                    has_non_manifold = True
                    break

        bm.free()
        eval_object.to_mesh_clear()

        if has_non_manifold:
            return [blender_object.name]

    return []


def write_vertices(
    mesh_element: xml.etree.ElementTree.Element,
    vertices: List[bpy.types.MeshVertex],
    use_orca_format: str,
    coordinate_precision: int,
) -> None:
    """
    Writes a list of vertices into the specified mesh element.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param vertices: A list of Blender vertices to add.
    :param use_orca_format: Material export mode — affects namespace handling.
    :param coordinate_precision: Number of decimal places for coordinates.
    """
    vertices_element = xml.etree.ElementTree.SubElement(
        mesh_element, f"{{{MODEL_NAMESPACE}}}vertices"
    )

    vertex_name = f"{{{MODEL_NAMESPACE}}}vertex"
    if use_orca_format in ("PAINT", "BASEMATERIAL"):
        x_name = "x"
        y_name = "y"
        z_name = "z"
    else:
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
    use_orca_format: str,
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
    :param use_orca_format: Material export mode — 'PAINT', 'BASEMATERIAL', or 'STANDARD'.
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
        v1_name = "v1"
        v2_name = "v2"
        v3_name = "v3"
        p1_name = "p1"
        p2_name = "p2"
        p3_name = "p3"
        pid_name = "pid"
    else:
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

    seg_strings_written = 0

    for tri_idx, triangle in enumerate(triangles):
        triangle_element = xml.etree.ElementTree.SubElement(
            triangles_element, triangle_name
        )
        triangle_element.attrib[v1_name] = str(triangle.vertices[0])
        triangle_element.attrib[v2_name] = str(triangle.vertices[1])
        triangle_element.attrib[v3_name] = str(triangle.vertices[2])

        # Handle segmentation strings from UV texture (PAINT mode)
        if segmentation_strings and tri_idx in segmentation_strings:
            seg_string = segmentation_strings[tri_idx]
            if seg_string:
                if mmu_slicer_format == "PRUSA":
                    ns_attr = "{http://schemas.slic3r.org/3mf/2017/06}mmu_segmentation"
                    triangle_element.attrib[ns_attr] = seg_string
                    seg_strings_written += 1
                else:
                    triangle_element.attrib["paint_color"] = seg_string
                    seg_strings_written += 1
                continue

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
                    if colorgroup_id < len(ORCA_FILAMENT_CODES):
                        paint_code = ORCA_FILAMENT_CODES[colorgroup_id]
                        if paint_code:
                            ns_attr = "{http://schemas.slic3r.org/3mf/2017/06}mmu_segmentation"
                            triangle_element.attrib[ns_attr] = paint_code
                else:
                    triangle_element.attrib[pid_name] = str(colorgroup_id)
                    triangle_element.attrib[p1_name] = "0"

                    if colorgroup_id < len(ORCA_FILAMENT_CODES):
                        paint_code = ORCA_FILAMENT_CODES[colorgroup_id]
                        if paint_code:
                            triangle_element.attrib["paint_color"] = paint_code
        elif triangle.material_index < len(material_slots):
            triangle_material = material_slots[triangle.material_index].material
            if triangle_material is not None:
                triangle_material_name = str(triangle_material.name)

                # Textured material — use texture2dgroup with UV indices
                if (
                    texture_groups
                    and triangle_material_name in texture_groups
                    and uv_layer
                ):
                    group_data = texture_groups[triangle_material_name]
                    group_id = group_data["group_id"]
                    triangle_element.attrib[pid_name] = group_id

                    uv_data = uv_layer.data
                    loop_indices = triangle.loops

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
                    material_index = material_name_to_index[triangle_material_name]
                    if material_index != object_material_list_index:
                        if basematerials_resource_id:
                            triangle_element.attrib[pid_name] = str(
                                basematerials_resource_id
                            )
                        triangle_element.attrib[p1_name] = str(material_index)

    if segmentation_strings:
        debug(
            f"  [write_triangles] Wrote {seg_strings_written} segmentation strings",
            f"to triangles (had {len(segmentation_strings)} available)"
        )


def write_passthrough_triangles(
    mesh_element: xml.etree.ElementTree.Element,
    mesh: bpy.types.Mesh,
    original_pid: str,
    remapped_pid: str,
    use_orca_format: str,
    coordinate_precision: int,
) -> None:
    """
    Write triangles with passthrough multiproperties indices from UV map.

    When a mesh was imported with multiproperties (composites/mixed materials),
    the per-vertex material indices were stored in a UV map. This function writes
    them back using the remapped multiproperties ID.

    :param mesh_element: The <mesh> element to write triangles into.
    :param mesh: The Blender mesh with UV data.
    :param original_pid: The original multiproperties resource ID.
    :param remapped_pid: The remapped multiproperties resource ID.
    :param use_orca_format: Material export mode.
    :param coordinate_precision: Number of decimal places for coordinates.
    """
    import json

    scene = bpy.context.scene

    uv_layer = mesh.uv_layers.active
    if not uv_layer:
        warn("No active UV layer found for passthrough triangle export")
        return

    # Load multiproperties data to get the multi entries
    mp_data_str = scene.get("3mf_multiproperties", "{}")
    try:
        mp_data = json.loads(mp_data_str)
    except json.JSONDecodeError:
        warn("Failed to parse multiproperties for passthrough triangle export")
        return

    multiprop = mp_data.get(original_pid)
    if not multiprop:
        warn(f"Multiproperties {original_pid} not found in passthrough data")
        return

    # Get the first texture2dgroup pid to use for UV -> index mapping
    pids = multiprop.get("pids", "").split()
    if not pids:
        warn("Multiproperties has no pids")
        return

    # Load texture group data to get tex2coords
    tg_data_str = scene.get("3mf_texture_groups", "{}")
    try:
        tg_data = json.loads(tg_data_str)
    except json.JSONDecodeError:
        warn("Failed to parse texture groups for passthrough triangle export")
        return

    # Find the first texture group pid (skip basematerials pid)
    tex2coords = None
    for pid in pids:
        if pid in tg_data:
            tex2coords = tg_data[pid].get("tex2coords", [])
            break

    if not tex2coords:
        warn("No tex2coords found for passthrough triangle UV mapping")
        return

    # Build reverse lookup: tex2coord index -> multi entry index
    multis = multiprop.get("multis", [])
    tex_idx_to_multi = {}
    tex_group_position = None
    for i, pid in enumerate(pids):
        if pid in tg_data:
            tex_group_position = i
            break

    if tex_group_position is not None:
        for multi_idx, m in enumerate(multis):
            pindices_str = m.get("pindices", "")
            pindices = pindices_str.split()
            if tex_group_position < len(pindices):
                tex_idx = pindices[tex_group_position]
                if tex_idx not in tex_idx_to_multi:
                    tex_idx_to_multi[tex_idx] = multi_idx

    # Build UV -> tex2coord index lookup with tolerance
    def find_tex2coord_index(u: float, v: float) -> int:
        """Find the closest tex2coord index for a UV pair."""
        best_idx = 0
        best_dist = float("inf")
        for idx, coord in enumerate(tex2coords):
            if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                du = u - coord[0]
                dv = v - coord[1]
                dist = du * du + dv * dv
                if dist < best_dist:
                    best_dist = dist
                    best_idx = idx
        return best_idx

    triangles_element = xml.etree.ElementTree.SubElement(
        mesh_element, f"{{{MODEL_NAMESPACE}}}triangles"
    )

    triangle_name = f"{{{MODEL_NAMESPACE}}}triangle"
    if use_orca_format in ("PAINT", "BASEMATERIAL"):
        v1_name = "v1"
        v2_name = "v2"
        v3_name = "v3"
        p1_name = "p1"
        p2_name = "p2"
        p3_name = "p3"
    else:
        v1_name = f"{{{MODEL_NAMESPACE}}}v1"
        v2_name = f"{{{MODEL_NAMESPACE}}}v2"
        v3_name = f"{{{MODEL_NAMESPACE}}}v3"
        p1_name = f"{{{MODEL_NAMESPACE}}}p1"
        p2_name = f"{{{MODEL_NAMESPACE}}}p2"
        p3_name = f"{{{MODEL_NAMESPACE}}}p3"

    for triangle in mesh.loop_triangles:
        tri_elem = xml.etree.ElementTree.SubElement(triangles_element, triangle_name)
        tri_elem.attrib[v1_name] = str(triangle.vertices[0])
        tri_elem.attrib[v2_name] = str(triangle.vertices[1])
        tri_elem.attrib[v3_name] = str(triangle.vertices[2])

        # Set pid to multiproperties ID on each triangle
        if use_orca_format in ("PAINT", "BASEMATERIAL"):
            tri_elem.attrib["pid"] = str(remapped_pid)
        else:
            tri_elem.attrib[f"{{{MODEL_NAMESPACE}}}pid"] = str(remapped_pid)

        # Map UV coordinates to multi entry indices
        loop_indices = triangle.loops
        uv_data = uv_layer.data

        uv1 = uv_data[loop_indices[0]].uv
        uv2 = uv_data[loop_indices[1]].uv
        uv3 = uv_data[loop_indices[2]].uv

        tex_idx1 = find_tex2coord_index(uv1[0], uv1[1])
        tex_idx2 = find_tex2coord_index(uv2[0], uv2[1])
        tex_idx3 = find_tex2coord_index(uv3[0], uv3[1])

        # Map tex2coord index → multi entry index
        multi_idx1 = tex_idx_to_multi.get(str(tex_idx1), tex_idx1)
        multi_idx2 = tex_idx_to_multi.get(str(tex_idx2), tex_idx2)
        multi_idx3 = tex_idx_to_multi.get(str(tex_idx3), tex_idx3)

        tri_elem.attrib[p1_name] = str(multi_idx1)
        tri_elem.attrib[p2_name] = str(multi_idx2)
        tri_elem.attrib[p3_name] = str(multi_idx3)

    debug(
        f"Wrote {len(mesh.loop_triangles)} passthrough triangles "
        f"with multiproperties UV indices"
    )


def write_metadata(
    node: xml.etree.ElementTree.Element, metadata: Metadata, use_orca_format: str
) -> None:
    """
    Writes metadata from a metadata storage into an XML node.

    :param node: The node to add <metadata> tags to.
    :param metadata: The collection of metadata to write to that node.
    :param use_orca_format: Material export mode — affects namespace handling.
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
