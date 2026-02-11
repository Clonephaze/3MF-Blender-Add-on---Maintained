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
Vendor / slicer format detection for 3MF files.

Detects whether a 3MF file was created by BambuStudio, Orca Slicer,
PrusaSlicer, or is a generic / standard 3MF.
"""

import xml.etree.ElementTree
from typing import Optional

from ...common import debug, MODEL_NAMESPACES

__all__ = ["detect_vendor"]


def detect_vendor(
    root: xml.etree.ElementTree.Element,
) -> Optional[str]:
    """Detect if a 3MF file was created by a specific vendor slicer.

    :param root: The root element of the 3MF model document.
    :return: Vendor identifier (e.g. ``"orca"``) or ``None`` for standard 3MF.
    """
    # Check for BambuStudio / Orca Slicer specific metadata
    for metadata_node in root.iterfind("./3mf:metadata", MODEL_NAMESPACES):
        name = metadata_node.attrib.get("name", "")
        if name == "BambuStudio:3mfVersion":
            debug("Detected BambuStudio/Orca Slicer format")
            return "orca"
        if name == "Application" and metadata_node.text:
            app_name = metadata_node.text.lower()
            if "orca" in app_name or "bambu" in app_name:
                debug(f"Detected Orca/Bambu format from Application: {metadata_node.text}")
                return "orca"

    # Check for BambuStudio namespace in root attributes
    for attr_name in root.attrib:
        if "bambu" in attr_name.lower():
            debug(f"Detected BambuStudio format from attribute: {attr_name}")
            return "orca"

    return None
