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
Extension registry and management for 3MF extensions.

This module provides a framework for registering and using 3MF extensions,
both official extensions from the 3MF Consortium and vendor-specific extensions.
"""

from typing import Dict, Set, Optional, List
from dataclasses import dataclass
from enum import Enum


class ExtensionType(Enum):
    """Type of 3MF extension."""

    OFFICIAL = "official"  # Official 3MF Consortium extension
    VENDOR = "vendor"  # Vendor-specific extension


@dataclass
class Extension:
    """
    Represents a 3MF extension with its metadata and capabilities.

    Attributes:
        namespace: XML namespace URI for this extension
        prefix: Preferred XML namespace prefix
        name: Human-readable name
        extension_type: Whether this is an official or vendor-specific extension
        description: Brief description of what this extension provides
        required: Whether this extension must be declared in requiredextensions
        vendor_attribute: Optional vendor-specific attribute name (e.g., "BambuStudio:3mfVersion")
    """

    namespace: str
    prefix: str
    name: str
    extension_type: ExtensionType
    description: str
    required: bool = False
    vendor_attribute: Optional[str] = None


# Official 3MF Consortium Extensions
# From: http://schemas.microsoft.com/3dmanufacturing/

MATERIALS_EXTENSION = Extension(
    namespace="http://schemas.microsoft.com/3dmanufacturing/material/2015/02",
    prefix="m",
    name="Materials and Properties",
    extension_type=ExtensionType.OFFICIAL,
    description="Defines material properties, colors, and textures for objects",
    required=False,
)

PRODUCTION_EXTENSION = Extension(
    namespace="http://schemas.microsoft.com/3dmanufacturing/production/2015/06",
    prefix="p",
    name="Production",
    extension_type=ExtensionType.OFFICIAL,
    description="Manufacturing metadata like UUID, path, and production instructions",
    required=True,
)

SLICE_EXTENSION = Extension(
    namespace="http://schemas.microsoft.com/3dmanufacturing/slice/2015/07",
    prefix="s",
    name="Slice",
    extension_type=ExtensionType.OFFICIAL,
    description="Pre-sliced geometry for specific printers",
    required=True,
)

BEAM_LATTICE_EXTENSION = Extension(
    namespace="http://schemas.microsoft.com/3dmanufacturing/beamlattice/2017/02",
    prefix="b",
    name="Beam Lattice",
    extension_type=ExtensionType.OFFICIAL,
    description="Efficient representation of lattice structures",
    required=True,
)

VOLUMETRIC_EXTENSION = Extension(
    namespace="http://schemas.microsoft.com/3dmanufacturing/volumetric/2017/07",
    prefix="v",
    name="Volumetric",
    extension_type=ExtensionType.OFFICIAL,
    description="Voxel-based 3D models",
    required=True,
)

TRIANGLE_SETS_EXTENSION = Extension(
    namespace="http://schemas.microsoft.com/3dmanufacturing/trianglesets/2021/07",
    prefix="t",
    name="Triangle Sets",
    extension_type=ExtensionType.OFFICIAL,
    description="Grouping of triangles for selection workflows and property assignment",
    required=False,
)

# Vendor-Specific Extensions

ORCA_EXTENSION = Extension(
    namespace="http://schemas.bambulab.com/package/2021",
    prefix="BambuStudio",
    name="Orca Slicer / BambuStudio",
    extension_type=ExtensionType.VENDOR,
    description="BambuLab/Orca Slicer specific features including color zones and project settings",
    required=False,
    vendor_attribute="BambuStudio:3mfVersion",
)

# Extension Registry
# Maps namespace URI to Extension object
EXTENSION_REGISTRY: Dict[str, Extension] = {
    MATERIALS_EXTENSION.namespace: MATERIALS_EXTENSION,
    PRODUCTION_EXTENSION.namespace: PRODUCTION_EXTENSION,
    SLICE_EXTENSION.namespace: SLICE_EXTENSION,
    BEAM_LATTICE_EXTENSION.namespace: BEAM_LATTICE_EXTENSION,
    VOLUMETRIC_EXTENSION.namespace: VOLUMETRIC_EXTENSION,
    TRIANGLE_SETS_EXTENSION.namespace: TRIANGLE_SETS_EXTENSION,
    ORCA_EXTENSION.namespace: ORCA_EXTENSION,
}


class ExtensionManager:
    """Manages active extensions for import/export operations."""

    def __init__(self):
        """Initialize with no active extensions."""
        self._active_extensions: Set[str] = set()

    def activate(self, namespace: str) -> None:
        """Activate an extension by its namespace URI.

        :raises ValueError: If the namespace is not registered.
        """
        if namespace not in EXTENSION_REGISTRY:
            raise ValueError(f"Unknown extension namespace: {namespace}")
        self._active_extensions.add(namespace)

    def deactivate(self, namespace: str) -> None:
        """Deactivate an extension."""
        self._active_extensions.discard(namespace)

    def is_active(self, namespace: str) -> bool:
        """Check if an extension is currently active."""
        return namespace in self._active_extensions

    def clear(self) -> None:
        """Deactivate all extensions."""
        self._active_extensions.clear()

    def get_active_extensions(self) -> List[Extension]:
        """Get list of all active Extension objects."""
        return [EXTENSION_REGISTRY[ns] for ns in self._active_extensions]

    def get_required_extensions_string(self) -> str:
        """Build the ``requiredextensions`` attribute value for the model element.

        :return: Space-separated string of namespace URIs that require declaration.
        """
        required = [
            ext.namespace for ext in self.get_active_extensions() if ext.required
        ]
        return " ".join(required)

    def get_vendor_attributes(self) -> Dict[str, str]:
        """Get vendor-specific attributes to add to the model element."""
        attrs = {}
        for ext in self.get_active_extensions():
            if ext.extension_type == ExtensionType.VENDOR and ext.vendor_attribute:
                attrs[ext.vendor_attribute] = "1"
        return attrs

    def register_namespaces(self, xml_module) -> None:
        """Register all active extension namespaces with ElementTree.

        :param xml_module: The ``xml.etree.ElementTree`` module.
        """
        for ext in self.get_active_extensions():
            xml_module.register_namespace(ext.prefix, ext.namespace)


# Convenience functions

def get_extension_by_namespace(namespace: str) -> Optional[Extension]:
    """Get Extension object by namespace URI."""
    return EXTENSION_REGISTRY.get(namespace)


def get_extension_by_prefix(prefix: str) -> Optional[Extension]:
    """Get Extension object by XML prefix."""
    for ext in EXTENSION_REGISTRY.values():
        if ext.prefix == prefix:
            return ext
    return None


def list_official_extensions() -> List[Extension]:
    """Get all registered official 3MF Consortium extensions."""
    return [
        ext
        for ext in EXTENSION_REGISTRY.values()
        if ext.extension_type == ExtensionType.OFFICIAL
    ]


def list_vendor_extensions() -> List[Extension]:
    """Get all registered vendor-specific extensions."""
    return [
        ext
        for ext in EXTENSION_REGISTRY.values()
        if ext.extension_type == ExtensionType.VENDOR
    ]


__all__ = [
    "Extension",
    "ExtensionType",
    "ExtensionManager",
    "EXTENSION_REGISTRY",
    "MATERIALS_EXTENSION",
    "PRODUCTION_EXTENSION",
    "SLICE_EXTENSION",
    "BEAM_LATTICE_EXTENSION",
    "VOLUMETRIC_EXTENSION",
    "TRIANGLE_SETS_EXTENSION",
    "ORCA_EXTENSION",
    "get_extension_by_namespace",
    "get_extension_by_prefix",
    "list_official_extensions",
    "list_vendor_extensions",
]
