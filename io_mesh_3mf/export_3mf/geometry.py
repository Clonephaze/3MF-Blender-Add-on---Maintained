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
import numpy as np
import time
import xml.etree.ElementTree
from typing import Optional, Dict, List

import bpy

from ..common.constants import MODEL_NAMESPACE

# ---------------------------------------------------------------------------
# Raw geometry side-cache
# ---------------------------------------------------------------------------
# xml.etree.ElementTree.Element uses __slots__ and does not allow arbitrary
# attribute assignment.  We use a plain dict keyed by id(mesh_element) to
# pass pre-built raw XML strings to the streaming writer in standard.py.
# Entries MUST be cleared after each element is written to prevent unbounded
# growth across multiple exports in the same Blender session.
_raw_geometry_cache: Dict[int, Dict[str, str]] = {}


def get_raw_geometry(mesh_element: xml.etree.ElementTree.Element):
    """Return ``(raw_vertices_xml, raw_triangles_xml)`` for *mesh_element*.

    Either value may be ``None`` if the corresponding function did not run
    (e.g. empty mesh, or passthrough triangles path).
    """
    data = _raw_geometry_cache.get(id(mesh_element))
    if data:
        return data.get("vertices"), data.get("triangles")
    return None, None


def clear_raw_geometry(mesh_element: xml.etree.ElementTree.Element) -> None:
    """Remove the cached raw geometry for *mesh_element* after it has been written."""
    _raw_geometry_cache.pop(id(mesh_element), None)
from ..common.logging import debug, timing_debug, warn
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
    Writes vertex geometry into the specified mesh element.

    Instead of building ElementTree sub-elements, the geometry is pre-built as a
    raw XML string and stored on ``mesh_element._raw_vertices_xml``.  The streaming
    writer in ``standard.py`` injects it directly, bypassing ElementTree's
    Python-level DOM serialiser.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param vertices: A list of Blender vertices to add.
    :param use_orca_format: Material export mode (unused, kept for API compat).
    :param coordinate_precision: Number of significant figures for coordinates.
    """
    n = len(vertices)
    if n == 0:
        return

    _t0 = time.perf_counter()

    # Bulk-extract all vertex coordinates in one C call, avoiding the per-vertex
    # Blender wrapper overhead that dominates for large meshes.
    coords_flat = np.empty(n * 3, dtype=np.float64)
    vertices.foreach_get("co", coords_flat)
    coords = coords_flat.reshape(n, 3)

    _t1 = time.perf_counter()
    timing_debug(f"write_vertices foreach_get ({n} verts)", (_t1 - _t0) * 1000)

    # Vectorized float→string using numpy's C-level char operations.
    # np.char.mod is ~3–5× faster than a Python list-comprehension for large arrays.
    decimals = coordinate_precision
    fmt = f"%.{decimals}g"
    x_strs = np.char.mod(fmt, coords[:, 0]).tolist()
    y_strs = np.char.mod(fmt, coords[:, 1]).tolist()
    z_strs = np.char.mod(fmt, coords[:, 2]).tolist()

    _t2 = time.perf_counter()
    timing_debug(f"write_vertices str format ({n} verts)", (_t2 - _t1) * 1000)

    # Build the raw <vertices> XML string directly, bypassing ElementTree's
    # Python-level DOM so the final document.write() doesn't need to walk N nodes.
    # Attribute names are always unqualified — they inherit the default namespace
    # from the enclosing <model xmlns="..."> declaration, which is semantically
    # equivalent to the namespaced form ElementTree would have generated.
    parts = ["<vertices>"]
    append = parts.append
    for i in range(n):
        append(f'<vertex x="{x_strs[i]}" y="{y_strs[i]}" z="{z_strs[i]}"/>')
    parts.append("</vertices>")

    # Store in the side-cache so the streaming writer in standard.py can
    # inject it without DOM overhead.  (Element objects have __slots__ and
    # do not allow arbitrary attribute assignment.)
    _raw_geometry_cache.setdefault(id(mesh_element), {})["vertices"] = "".join(parts)

    _t3 = time.perf_counter()
    timing_debug(f"write_vertices build raw XML ({n} verts)", (_t3 - _t2) * 1000)
    timing_debug(f"write_vertices TOTAL ({n} verts)", (_t3 - _t0) * 1000)


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
    seam_strings: Optional[Dict[int, str]] = None,
    support_strings: Optional[Dict[int, str]] = None,
) -> None:
    """
    Writes triangle geometry into the specified mesh element.

    Instead of building ElementTree sub-elements, the geometry is pre-built as a
    raw XML string and stored on ``mesh_element._raw_triangles_xml``.  The
    streaming writer in ``standard.py`` injects it directly.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param triangles: A list of triangles.
    :param object_material_list_index: Index of the material the object was written with.
    :param material_slots: List of materials belonging to the object.
    :param material_name_to_index: Mapping from material name to index in the basematerials resource.
    :param use_orca_format: Material export mode — 'PAINT', 'BASEMATERIAL', or 'STANDARD'.
    :param mmu_slicer_format: The target slicer format ('ORCA' or 'PRUSA').
    :param vertex_colors: Dictionary of color hex to filament index.
    :param mesh: The mesh containing these triangles.
    :param blender_object: The Blender object.
    :param texture_groups: Dict of material_name -> texture group data for UV mapping.
    :param basematerials_resource_id: The ID of the basematerials resource for per-face material refs.
    :param segmentation_strings: Dict of face_index -> segmentation hash string (for PAINT mode).
    :param seam_strings: Dict of face_index -> seam segmentation hash string.
    :param support_strings: Dict of face_index -> support segmentation hash string.
    """
    debug(
        f"[write_triangles] mode={use_orca_format}, slicer={mmu_slicer_format},",
        f" seg_strings={len(segmentation_strings) if segmentation_strings else 0}"
    )

    # Always use plain (unqualified) attribute names in the raw XML string.
    # Attribute names are not in a namespace — they inherit the element's default
    # namespace which the streaming writer declares on the root <model> element.
    p1_name = "p1"
    p2_name = "p2"
    p3_name = "p3"
    pid_name = "pid"

    # Get active UV layer for texture coordinate export
    uv_layer = None
    if mesh and texture_groups and mesh.uv_layers.active:
        uv_layer = mesh.uv_layers.active

    n_tris = len(triangles)
    seg_strings_written = 0

    if n_tris == 0:
        return

    _t0 = time.perf_counter()

    # --- Pre-computation phase: bulk-extract mesh data to avoid per-triangle
    # Blender wrapper overhead in the hot loop. ---

    # Bulk-extract all triangle vertex indices (flat: n_tris * 3)
    verts_flat = np.empty(n_tris * 3, dtype=np.int32)
    triangles.foreach_get("vertices", verts_flat)
    # tolist() gives Python ints; map(str, ...) is fastest for batch int→str
    verts_strs = list(map(str, verts_flat.tolist()))

    # Bulk-extract per-triangle material slot indices
    mat_flat = np.empty(n_tris, dtype=np.int32)
    triangles.foreach_get("material_index", mat_flat)
    mat_indices_list = mat_flat.tolist()

    _t1 = time.perf_counter()
    timing_debug(f"write_triangles foreach_get verts+mat ({n_tris} tris)", (_t1 - _t0) * 1000)

    # Pre-compute per-slot attribute dicts so the hot loop avoids repeated
    # material_slots[i].material.name lookups and dict rebuilds per triangle.
    # slot_attribs[i]: None | group_data_dict (texture, has "group_id") | {attr: val}
    n_slots = len(material_slots)
    slot_attribs: List = [None] * n_slots
    for slot_idx in range(n_slots):
        slot_mat = material_slots[slot_idx].material
        if slot_mat is None:
            continue
        name = str(slot_mat.name)
        if uv_layer and texture_groups and name in texture_groups:
            # Texture path — store group_data dict directly.
            # Identified in the loop by presence of "group_id" key.
            slot_attribs[slot_idx] = texture_groups[name]
        elif name in material_name_to_index:
            mat_idx_val = material_name_to_index[name]
            attrs: Dict[str, str] = {p1_name: str(mat_idx_val)}
            if basematerials_resource_id:
                attrs[pid_name] = str(basematerials_resource_id)
            slot_attribs[slot_idx] = attrs

    # Pre-fetch all UV loop data when texture groups are present, replacing
    # per-triangle uv_layer.data[loop_idx].uv wrapper access.
    uv_data_arr = None
    uv_loops_list = None
    if uv_layer:
        n_loops = len(uv_layer.data)
        uv_flat = np.empty(n_loops * 2, dtype=np.float32)
        uv_layer.data.foreach_get("uv", uv_flat)
        uv_data_arr = uv_flat.reshape(n_loops, 2)
        loops_flat = np.empty(n_tris * 3, dtype=np.int32)
        triangles.foreach_get("loops", loops_flat)
        uv_loops_list = loops_flat.tolist()

    _t2 = time.perf_counter()
    timing_debug(f"write_triangles pre-compute (slots + UV fetch)", (_t2 - _t1) * 1000)

    # Hoist per-iteration conditionals that don't change between triangles
    has_segmentation = bool(segmentation_strings)
    is_basematerial_vc = (
        use_orca_format == "BASEMATERIAL"
        and bool(vertex_colors)
        and mesh is not None
        and blender_object is not None
    )
    is_prusa = mmu_slicer_format == "PRUSA"
    prusa_seg_attr = "slic3rpe:mmu_segmentation"

    # Pre-compute per-slot attribute string fragments so the hot loop emits
    # complete XML strings rather than building dicts and calling attrib.update().
    # Each entry is: None | group_data dict (texture) | str (plain frag like ' pid="2" p1="3"')
    slot_frags: List = [None] * n_slots
    for slot_idx in range(n_slots):
        cached = slot_attribs[slot_idx]
        if cached is None or "group_id" in cached:
            slot_frags[slot_idx] = cached  # None or texture group_data dict
        else:
            # Render the attribute fragment.  Attribute names are always
            # unqualified in raw XML output — they inherit the default namespace.
            pid_val = cached.get(pid_name) or cached.get("pid")
            p1_val = cached.get(p1_name) or cached.get("p1")
            frag = ""
            if pid_val is not None:
                frag += f' pid="{pid_val}"'
            if p1_val is not None:
                frag += f' p1="{p1_val}"'
            slot_frags[slot_idx] = frag if frag else None

    # --- Build raw <triangles> XML string directly, bypassing ElementTree DOM ---
    # This avoids creating 2M+ Python Element objects and lets the streaming
    # writer inject the text block without a per-node serialisation walk.
    parts = ["<triangles>"]
    append = parts.append
    v = verts_strs  # local alias

    for tri_idx in range(n_tris):
        base = tri_idx * 3
        v0, v1_, v2_ = v[base], v[base + 1], v[base + 2]

        # --- PAINT mode: segmentation strings ---
        if has_segmentation:
            seg_str = segmentation_strings.get(tri_idx)
            if seg_str:
                seg_strings_written += 1
                seg_part = f' {prusa_seg_attr}="{seg_str}"' if is_prusa else f' paint_color="{seg_str}"'
                seam = seam_strings.get(tri_idx) if seam_strings else None
                sup = support_strings.get(tri_idx) if support_strings else None
                seam_part = f' paint_seam="{seam}"' if seam else ""
                sup_part = f' paint_supports="{sup}"' if sup else ""
                append(f'<triangle v1="{v0}" v2="{v1_}" v3="{v2_}"{seg_part}{seam_part}{sup_part}/>')
                continue

        # --- BASEMATERIAL mode with vertex colors ---
        if is_basematerial_vc:
            triangle = triangles[tri_idx]
            triangle_color = get_triangle_color(mesh, triangle, blender_object)
            if triangle_color and triangle_color in vertex_colors:
                colorgroup_id = vertex_colors[triangle_color]
                if is_prusa:
                    if colorgroup_id < len(ORCA_FILAMENT_CODES):
                        paint_code = ORCA_FILAMENT_CODES[colorgroup_id]
                        if paint_code:
                            append(f'<triangle v1="{v0}" v2="{v1_}" v3="{v2_}" {prusa_seg_attr}="{paint_code}"/>')
                            continue
                else:
                    paint_extra = ""
                    if colorgroup_id < len(ORCA_FILAMENT_CODES):
                        paint_code = ORCA_FILAMENT_CODES[colorgroup_id]
                        if paint_code:
                            paint_extra = f' paint_color="{paint_code}"'
                    append(f'<triangle v1="{v0}" v2="{v1_}" v3="{v2_}" pid="{colorgroup_id}" p1="0"{paint_extra}/>')
                    continue
            append(f'<triangle v1="{v0}" v2="{v1_}" v3="{v2_}"/>')
        else:
            # --- Standard material path (pre-computed slot fragments) ---
            mat_slot_idx = mat_indices_list[tri_idx]
            extra = ""
            if mat_slot_idx < n_slots:
                frag = slot_frags[mat_slot_idx]
                if frag is not None:
                    if isinstance(frag, str):
                        extra = frag
                    else:
                        # Texture UV path — frag is the group_data dict
                        loop0 = uv_loops_list[base]
                        loop1 = uv_loops_list[base + 1]
                        loop2 = uv_loops_list[base + 2]
                        uv1 = uv_data_arr[loop0]
                        uv2 = uv_data_arr[loop1]
                        uv3 = uv_data_arr[loop2]
                        gid = frag["group_id"]
                        i1 = get_or_create_tex2coord(frag, float(uv1[0]), float(uv1[1]))
                        i2 = get_or_create_tex2coord(frag, float(uv2[0]), float(uv2[1]))
                        i3 = get_or_create_tex2coord(frag, float(uv3[0]), float(uv3[1]))
                        extra = f' pid="{gid}" p1="{i1}" p2="{i2}" p3="{i3}"'

            seam = seam_strings.get(tri_idx) if seam_strings else None
            sup = support_strings.get(tri_idx) if support_strings else None
            seam_part = f' paint_seam="{seam}"' if seam else ""
            sup_part = f' paint_supports="{sup}"' if sup else ""
            append(f'<triangle v1="{v0}" v2="{v1_}" v3="{v2_}"{extra}{seam_part}{sup_part}/>')

    parts.append("</triangles>")
    _raw_geometry_cache.setdefault(id(mesh_element), {})["triangles"] = "".join(parts)

    _t3 = time.perf_counter()
    timing_debug(f"write_triangles main loop ({n_tris} tris)", (_t3 - _t2) * 1000)
    timing_debug(f"write_triangles TOTAL ({n_tris} tris)", (_t3 - _t0) * 1000)

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
