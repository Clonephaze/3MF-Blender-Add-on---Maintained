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
Triangle Sets Extension import functionality for 3MF files.

This module handles reading triangle set definitions from 3MF Core Spec v1.3.0+.
Triangle sets are groups of triangles with a name and identifier for selection
and property assignment workflows.
"""

import xml.etree.ElementTree
from typing import Dict, List, TYPE_CHECKING

from ..common import debug, warn
from ..common.constants import MODEL_NAMESPACES

if TYPE_CHECKING:
    from .context import ImportContext


def read_triangle_sets(
    ctx: "ImportContext",
    object_node: xml.etree.ElementTree.Element,
) -> Dict[str, List[int]]:
    """
    Reads triangle sets from an XML node of an object.

    Triangle sets are groups of triangles with a name and unique identifier.
    They are used for selection workflows and property assignment.
    Introduced in 3MF Core Spec v1.3.0.

    Supports both ``<ref index="N"/>`` and ``<refrange startindex="N" endindex="M"/>``
    elements.

    :param ctx: Import context.
    :param object_node: An ``<object>`` element from the 3dmodel.model file.
    :return: Dictionary mapping triangle set names to lists of triangle indices.
    """
    triangle_sets: Dict[str, List[int]] = {}

    # Look for triangle sets under <mesh><trianglesets>
    for triangleset in object_node.iterfind(
        "./3mf:mesh/t:trianglesets/t:triangleset", MODEL_NAMESPACES
    ):
        attrib = triangleset.attrib

        # Per spec: both name and identifier are required, but we gracefully handle missing
        set_name = attrib.get("name")
        if not set_name:
            # Fall back to identifier if name missing
            set_name = attrib.get("identifier")
        if not set_name:
            warn("Triangle set missing name attribute, skipping")
            ctx.safe_report({"WARNING"}, "Triangle set missing name attribute")
            continue

        # Cache set name to protect Unicode characters
        set_name = str(set_name)

        # Parse triangle indices from child <ref> and <refrange> elements
        triangle_indices: List[int] = []

        # Handle <ref index="N"/> elements
        for ref in triangleset.iterfind("t:ref", MODEL_NAMESPACES):
            try:
                index = int(ref.attrib.get("index", "-1"))
                if index < 0:
                    warn(f"Triangle set '{set_name}' contains negative triangle index")
                    continue
                triangle_indices.append(index)
            except (KeyError, ValueError) as e:
                warn(f"Triangle set '{set_name}' contains invalid ref: {e}")
                continue

        # Handle <refrange startindex="N" endindex="M"/> elements (inclusive range)
        for refrange in triangleset.iterfind("t:refrange", MODEL_NAMESPACES):
            try:
                start_index = int(refrange.attrib.get("startindex", "-1"))
                end_index = int(refrange.attrib.get("endindex", "-1"))
                if start_index < 0 or end_index < 0:
                    warn(f"Triangle set '{set_name}' contains invalid refrange indices")
                    continue
                if end_index < start_index:
                    warn(f"Triangle set '{set_name}' has refrange with end < start")
                    continue
                # Per spec: range is inclusive on both ends
                triangle_indices.extend(range(start_index, end_index + 1))
            except (KeyError, ValueError) as e:
                warn(f"Triangle set '{set_name}' contains invalid refrange: {e}")
                continue

        if triangle_indices:
            # Remove duplicates per spec: "A consumer MUST ignore duplicate references"
            triangle_indices = list(dict.fromkeys(triangle_indices))
            triangle_sets[set_name] = triangle_indices
            debug(
                f"Loaded triangle set '{set_name}' with {len(triangle_indices)} triangles"
            )

    return triangle_sets
