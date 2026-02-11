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

"""
XML utility functions shared across import and export.

These are pure XML/transformation helpers that don't depend on Blender's scene
state (though ``parse_transformation`` returns a ``mathutils.Matrix``).
"""

import xml.etree.ElementTree
from typing import Optional, Set

import mathutils  # For Matrix

from .logging import debug, warn
from .constants import (
    MODEL_NAMESPACES,
    PRODUCTION_NAMESPACE,
    SUPPORTED_EXTENSIONS,
)
from .metadata import Metadata, MetadataEntry

__all__ = [
    "parse_transformation",
    "format_transformation",
    "resolve_extension_prefixes",
    "is_supported",
    "read_metadata",
]


def parse_transformation(transformation_str: str) -> mathutils.Matrix:
    """Parse a 3MF affine transformation string into a 4×4 Matrix.

    The 3MF spec stores transformations as 12 space-separated floats in
    column-major order (without the implicit last row ``[0 0 0 1]``).

    :param transformation_str: A transformation as represented in 3MF.
    :return: A ``Matrix`` object with the correct transformation.
    """
    components = transformation_str.split(" ")
    result = mathutils.Matrix.Identity(4)
    if transformation_str == "":  # Early-out if transformation is missing.
        return result
    row = -1
    col = 0
    for component in components:
        row += 1
        if row > 2:
            col += 1
            row = 0
            if col > 3:
                warn(f"Transformation matrix contains too many components: {transformation_str}")
                break
        try:
            component_float = float(component)
        except ValueError:
            warn(f"Transformation matrix malformed: {transformation_str}")
            continue
        result[row][col] = component_float
    return result


def format_transformation(transformation: mathutils.Matrix) -> str:
    """Format a 4×4 Matrix as a 3MF transformation string.

    :param transformation: The transformation matrix to format.
    :return: Space-separated string of 12 floats.
    """
    import itertools
    pieces = (row[:3] for row in transformation.transposed())
    formatted_cells = [f"{cell:.9f}" for cell in itertools.chain.from_iterable(pieces)]
    return " ".join(formatted_cells)


def resolve_extension_prefixes(
    root: xml.etree.ElementTree.Element,
    prefixes: str,
) -> Set[str]:
    """Resolve extension prefixes to their full namespace URIs.

    Per the 3MF spec, the ``requiredextensions`` attribute contains
    space-separated prefixes (like ``"p"`` for Production Extension),
    not full namespace URIs.  This function maps those prefixes to the
    actual namespace URIs using the ``xmlns`` declarations on the root
    element.

    :param root: The XML root element containing namespace declarations.
    :param prefixes: Space-separated extension prefixes.
    :return: Set of full namespace URIs.
    """
    if not prefixes:
        return set()

    # Build prefix → namespace map from root element attributes
    prefix_to_ns = {}
    for attr_name, attr_value in root.attrib.items():
        if attr_name.startswith("{"):
            continue
        if attr_name.startswith("xmlns:"):
            prefix = attr_name[6:]
            prefix_to_ns[prefix] = attr_value

    # Fallback: known prefix mappings for when ElementTree strips xmlns attrs
    known_prefix_mappings = {
        "p": PRODUCTION_NAMESPACE,
        "m": "http://schemas.microsoft.com/3dmanufacturing/material/2015/02",
        "slic3rpe": "http://schemas.slic3r.org/3mf/2017/06",
    }
    prefix_to_ns.update(
        {k: v for k, v in known_prefix_mappings.items() if k not in prefix_to_ns}
    )

    # Resolve
    resolved = set()
    for prefix in prefixes.split():
        prefix = prefix.strip()
        if not prefix:
            continue
        if prefix in prefix_to_ns:
            resolved.add(prefix_to_ns[prefix])
        else:
            resolved.add(prefix)
            debug(f"Unknown extension prefix: {prefix}")

    return resolved


def is_supported(
    required_extensions: str,
    root: Optional[xml.etree.ElementTree.Element] = None,
) -> bool:
    """Check whether all required extensions are supported by this addon.

    :param required_extensions: The ``requiredextensions`` attribute value.
    :param root: Optional root element to resolve prefixes to namespace URIs.
    :return: ``True`` if all required extensions are in :data:`SUPPORTED_EXTENSIONS`.
    """
    if root is not None:
        extensions = resolve_extension_prefixes(root, required_extensions)
    else:
        extensions = set(filter(lambda x: x != "", required_extensions.split(" ")))
    return extensions <= SUPPORTED_EXTENSIONS


def read_metadata(
    node: xml.etree.ElementTree.Element,
    original_metadata: Optional[Metadata] = None,
    reporter=None,
) -> Metadata:
    """Read ``<metadata>`` tags from an XML node.

    :param node: A node containing ``<metadata>`` children (root or metadatagroup).
    :param original_metadata: Existing metadata to merge with (optional).
    :param reporter: Optional operator for ``safe_report()`` calls (or None).
    :return: A :class:`Metadata` object.
    """
    from .logging import safe_report

    if original_metadata is not None:
        metadata = original_metadata
    else:
        metadata = Metadata()

    for metadata_node in node.iterfind("./3mf:metadata", MODEL_NAMESPACES):
        if "name" not in metadata_node.attrib:
            warn("Metadata entry without name is discarded.")
            if reporter is not None:
                safe_report(reporter, {"WARNING"}, "Metadata entry without name is discarded")
            continue
        name = metadata_node.attrib["name"]
        preserve_str = metadata_node.attrib.get("preserve", "0")
        preserve = preserve_str != "0" and preserve_str.lower() != "false"
        datatype = metadata_node.attrib.get("type", "")
        value = metadata_node.text

        metadata[name] = MetadataEntry(
            name=name, preserve=preserve, datatype=datatype, value=value
        )

    return metadata
