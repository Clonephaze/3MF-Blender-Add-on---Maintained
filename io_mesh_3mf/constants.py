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
This module defines some constants for 3MF's file structure.

These are the constants that are inherent to the 3MF file format.
"""

from typing import Set, Dict

# IDE and Documentation support.
__all__ = [
    "SPEC_VERSION",
    "SUPPORTED_EXTENSIONS",
    "conflicting_mustpreserve_contents",
    "MODEL_LOCATION",
    "CONTENT_TYPES_LOCATION",
    "CORE_PROPERTIES_LOCATION",
    "RELS_FOLDER",
    "MODEL_REL",
    "THUMBNAIL_REL",
    "CORE_PROPERTIES_REL",
    "RELS_MIMETYPE",
    "MODEL_MIMETYPE",
    "CORE_PROPERTIES_MIMETYPE",
    "MODEL_NAMESPACE",
    "SLIC3RPE_NAMESPACE",
    "TRIANGLE_SETS_NAMESPACE",
    "MODEL_NAMESPACES",
    "MODEL_DEFAULT_UNIT",
    "MATERIAL_NAMESPACE",
    "PRODUCTION_NAMESPACE",
    "BAMBU_NAMESPACE",
    "CONTENT_TYPES_NAMESPACE",
    "CONTENT_TYPES_NAMESPACES",
    "CORE_PROPERTIES_NAMESPACE",
    "DC_NAMESPACE",
    "DCTERMS_NAMESPACE",
    "RELS_NAMESPACE",
    "RELS_NAMESPACES",
    "RELS_RELATIONSHIP_FIND",
]

# 3MF Core Specification version this addon targets.
# v1.4.0 (February 6, 2025): Removed deprecated mirror, clarified shapes/composition, document naming conventions.
SPEC_VERSION: str = "1.4.0"

# Set of namespaces for 3MF extensions that we support.
# Materials extension is used for Orca Slicer color export.
MATERIAL_NAMESPACE: str = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
# Production extension for multi-file structure (used by Orca/BambuStudio)
PRODUCTION_NAMESPACE: str = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
# Triangle Sets extension (introduced in Core Spec v1.3.0)
# Used for grouping triangles for selection workflows and property assignment.
TRIANGLE_SETS_NAMESPACE: str = "http://schemas.microsoft.com/3dmanufacturing/trianglesets/2021/07"
# BambuStudio/Orca vendor namespace
BAMBU_NAMESPACE: str = "http://schemas.bambulab.com/package/2021"

SUPPORTED_EXTENSIONS: Set[str] = {
    MATERIAL_NAMESPACE,  # Materials and colors extension
    PRODUCTION_NAMESPACE,  # Production extension (multi-file)
    TRIANGLE_SETS_NAMESPACE,  # Triangle sets (groups of triangles) - read support
}

# File contents to use when files must be preserved but there's a file with different content in a previous archive.
# Only for flagging. This will not be in the final 3MF archives.
conflicting_mustpreserve_contents: str = "<Conflicting MustPreserve file!>"

# Default storage locations.
MODEL_LOCATION: str = "3D/3dmodel.model"  # Conventional location for the 3D model data.
CONTENT_TYPES_LOCATION: str = "[Content_Types].xml"  # Location of the content types definition.
CORE_PROPERTIES_LOCATION: str = "docProps/core.xml"  # OPC Core Properties location.
RELS_FOLDER: str = "_rels"  # Folder name to store relationships files in.

# Relationship types.
MODEL_REL: str = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"  # Relationship type of 3D models.
THUMBNAIL_REL: str = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"
CORE_PROPERTIES_REL: str = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"

# MIME types of files in the archive.
RELS_MIMETYPE: str = "application/vnd.openxmlformats-package.relationships+xml"  # MIME type of .rels files.
MODEL_MIMETYPE: str = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"  # MIME type of .model files.
CORE_PROPERTIES_MIMETYPE: str = "application/vnd.openxmlformats-package.core-properties+xml"

# Constants in the 3D model file.
MODEL_NAMESPACE: str = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
SLIC3RPE_NAMESPACE: str = "http://schemas.slic3r.org/3mf/2017/06"  # PrusaSlicer/Slic3r vendor extension
MODEL_NAMESPACES: Dict[str, str] = {
    "3mf": MODEL_NAMESPACE,
    "slic3rpe": SLIC3RPE_NAMESPACE,
    "t": TRIANGLE_SETS_NAMESPACE,  # Triangle sets extension (Core Spec v1.3+)
    "m": MATERIAL_NAMESPACE,  # Materials and Properties extension (PBR support)
}
MODEL_DEFAULT_UNIT: str = "millimeter"  # If the unit is missing, it will be this.

# Constants in the ContentTypes file.
CONTENT_TYPES_NAMESPACE: str = "http://schemas.openxmlformats.org/package/2006/content-types"
CONTENT_TYPES_NAMESPACES: Dict[str, str] = {
    "ct": CONTENT_TYPES_NAMESPACE
}

# OPC Core Properties namespaces (Dublin Core).
CORE_PROPERTIES_NAMESPACE: str = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NAMESPACE: str = "http://purl.org/dc/elements/1.1/"  # Dublin Core elements
DCTERMS_NAMESPACE: str = "http://purl.org/dc/terms/"  # Dublin Core terms

# Constants in the .rels files.
RELS_NAMESPACE: str = "http://schemas.openxmlformats.org/package/2006/relationships"
RELS_NAMESPACES: Dict[str, str] = {  # Namespaces used for the rels files.
    "rel": RELS_NAMESPACE
}
RELS_RELATIONSHIP_FIND: str = "rel:Relationship"
