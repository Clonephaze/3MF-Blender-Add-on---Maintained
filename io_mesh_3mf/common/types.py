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
Data types for 3MF import/export.

All structured data types (previously ``namedtuple`` definitions scattered in
``import_3mf.py``) live here as ``@dataclass`` classes.  Both the import and
export sides import from this single module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple

__all__ = [
    "ResourceObject",
    "Component",
    "ResourceMaterial",
    "ResourceTexture",
    "ResourceTextureGroup",
    "ResourceComposite",
    "ResourceMultiproperties",
    "ResourcePBRTextureDisplay",
    "ResourceColorgroup",
    "ResourcePBRDisplayProps",
]


@dataclass
class ResourceObject:
    """A parsed ``<object>`` element from a 3MF model file."""

    vertices: List[Tuple[float, float, float]]
    triangles: list  # List of (v1, v2, v3, material, ...) tuples
    materials: dict  # Dict mapping face index → ResourceMaterial
    components: list  # List of Component instances
    metadata: object = None  # Metadata instance
    triangle_sets: Optional[dict] = None
    triangle_uvs: Optional[dict] = None
    segmentation_strings: Optional[dict] = None
    default_extruder: Optional[int] = None


@dataclass
class Component:
    """A ``<component>`` reference inside an ``<object>``."""

    resource_object: str  # Object ID string
    transformation: object = None  # mathutils.Matrix
    path: Optional[str] = None  # Production Extension p:path


@dataclass
class ResourceMaterial:
    """Material properties parsed from 3MF basematerials, colorgroups, or PBR extensions."""

    name: Optional[str] = None
    color: Optional[Tuple[float, ...]] = None  # RGBA tuple (0-1 range)

    # PBR Metallic workflow
    metallic: Optional[float] = None
    roughness: Optional[float] = None

    # PBR Specular workflow
    specular_color: Optional[Tuple[float, float, float]] = None
    glossiness: Optional[float] = None

    # Translucent materials
    ior: Optional[float] = None
    attenuation: Optional[Tuple[float, float, float]] = None
    transmission: Optional[float] = None

    # Texture support
    texture_id: Optional[str] = None

    # Textured PBR support
    metallic_texid: Optional[str] = None
    roughness_texid: Optional[str] = None
    specular_texid: Optional[str] = None
    glossiness_texid: Optional[str] = None
    basecolor_texid: Optional[str] = None

    # Multiproperties multi-texture support
    extra_texture_ids: Optional[List[str]] = None

    def __hash__(self):
        """Hash based on name and color for use as dict keys / set members."""
        return hash((self.name, self.color))

    def __eq__(self, other):
        if not isinstance(other, ResourceMaterial):
            return NotImplemented
        return (self.name, self.color) == (other.name, other.color)


@dataclass
class ResourceTexture:
    """A ``<texture2d>`` element — texture image metadata."""

    path: str  # Path to texture file in archive
    contenttype: str  # MIME type
    tilestyleu: str = "wrap"
    tilestylev: str = "wrap"
    filter: str = "auto"
    blender_image: object = None  # bpy.types.Image (set after extraction)


@dataclass
class ResourceTextureGroup:
    """A ``<texture2dgroup>`` container for texture coordinates."""

    texid: str  # ID of referenced <texture2d>
    tex2coords: List[Tuple[float, float]] = field(default_factory=list)
    displaypropertiesid: Optional[str] = None


@dataclass
class ResourceComposite:
    """Composite materials (Materials Extension) for round-trip support."""

    matid: str
    matindices: str = ""  # Space-delimited material indices
    displaypropertiesid: Optional[str] = None
    composites: List[dict] = field(default_factory=list)


@dataclass
class ResourceMultiproperties:
    """Multiproperties (Materials Extension) for round-trip support."""

    pids: str  # Space-delimited property group IDs
    blendmethods: Optional[str] = None
    multis: List[dict] = field(default_factory=list)


@dataclass
class ResourcePBRTextureDisplay:
    """Textured PBR display properties (metallic/specular texture maps)."""

    type: str  # "metallic" or "specular"
    name: Optional[str] = None
    primary_texid: Optional[str] = None
    secondary_texid: Optional[str] = None
    basecolor_texid: Optional[str] = None
    factors: Dict[str, str] = field(default_factory=dict)


@dataclass
class ResourceColorgroup:
    """Passthrough storage for ``<colorgroup>`` elements."""

    colors: List[str]  # Color strings in original format
    displaypropertiesid: Optional[str] = None


@dataclass
class ResourcePBRDisplayProps:
    """Passthrough storage for non-textured PBR display properties."""

    type: str  # "metallic", "specular", or "translucent"
    properties: List[dict] = field(default_factory=list)
