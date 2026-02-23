# Blender add-on to import and export 3MF files.
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
Triangle Sets Extension export functionality for 3MF files.

Handles export of triangle sets (groups of triangles) from Blender mesh attributes
to 3MF Triangle Sets Extension format.
"""

import xml.etree.ElementTree
from typing import Dict, List

import bpy

from ..common.constants import TRIANGLE_SETS_NAMESPACE
from ..common.logging import debug, warn


def write_triangle_sets(
    mesh_element: xml.etree.ElementTree.Element,
    mesh: bpy.types.Mesh,
    original_mesh: bpy.types.Mesh = None,
) -> None:
    """
    Writes triangle sets from Blender mesh attributes into the mesh element.

    Triangle sets group triangles together for selection workflows and property
    assignment in 3MF-compatible applications.

    The source data is read in priority order:

    1. ``3mf_triangle_set`` custom attribute — written on import, mirrors
       the original 3MF data verbatim.
    2. ``.sculpt_face_set`` — Blender's native sculpt face sets.  Used as
       a fallback when no ``3mf_triangle_set`` attribute exists, so users
       can create triangle sets purely through Sculpt-mode tools.

    Names are looked up from the ``3mf_triangle_set_names`` custom property.
    Because ``to_mesh()`` copies do not carry custom properties, an
    *original_mesh* reference is accepted so names can be read from the
    real mesh datablock.

    :param mesh_element: The <mesh> element of the 3MF document.
    :param mesh: The (possibly evaluated) Blender mesh with face attributes.
    :param original_mesh: The original mesh datablock for custom-property
        lookups.  Falls back to *mesh* when ``None``.
    """
    if original_mesh is None:
        original_mesh = mesh
    # Determine source attribute.
    attr_name = "3mf_triangle_set"
    from_face_sets = False

    if attr_name not in mesh.attributes:
        # Fall back to sculpt face sets.
        if ".sculpt_face_set" in mesh.attributes:
            attr_name = ".sculpt_face_set"
            from_face_sets = True
        else:
            return

    import json
    raw_names = original_mesh.get("3mf_triangle_set_names", "")
    if isinstance(raw_names, str) and raw_names:
        try:
            set_names = json.loads(raw_names)
        except (json.JSONDecodeError, ValueError):
            set_names = []
    else:
        set_names = list(raw_names) if raw_names else []

    # When sourcing from the dedicated attribute, require names.
    if not from_face_sets and not set_names:
        return

    # CRITICAL: Check if topology has changed since import.
    # Only relevant for imported 3mf_triangle_set data — sculpt face sets
    # are maintained by Blender and always match current topology.
    if not from_face_sets:
        original_face_count = original_mesh.get("3mf_original_face_count")
        current_face_count = len(mesh.polygons)
        if original_face_count is not None and original_face_count != current_face_count:
            warn(
                f"Mesh '{mesh.name}' topology changed ({original_face_count} → {current_face_count} faces). "
                f"Triangle sets are invalid and will not be exported. Original triangle indices are lost."
            )
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
            trianglesets_element, f"{{{TRIANGLE_SETS_NAMESPACE}}}triangleset"
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
                    triangleset_element, f"{{{TRIANGLE_SETS_NAMESPACE}}}refrange"
                )
                refrange_element.attrib["startindex"] = str(start)
                refrange_element.attrib["endindex"] = str(end)
            else:
                for idx in range(start, end + 1):
                    ref_element = xml.etree.ElementTree.SubElement(
                        triangleset_element, f"{{{TRIANGLE_SETS_NAMESPACE}}}ref"
                    )
                    ref_element.attrib["index"] = str(idx)
            i += 1

        debug(
            f"Exported triangle set '{set_name}' with {len(triangle_indices)} triangles"
        )
