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
Import Materials Package for 3MF Materials Extension support.

This package handles importing material-related elements from 3MF files:
- base.py: Core basematerials and colorgroup parsing
- textures.py: texture2d and texture2dgroup parsing, texture extraction
- pbr.py: PBR display properties (metallic, specular, translucent workflows)
- passthrough.py: Round-trip support for composite materials, multiproperties
"""

from .base import (
    read_materials,
    find_existing_material,
    parse_hex_color,
    srgb_to_linear,
)
from .textures import (
    read_textures,
    read_texture_groups,
    extract_textures_from_archive,
    get_or_create_textured_material,
    setup_textured_material,
    setup_multi_textured_material,
)
from .pbr import (
    read_pbr_metallic_properties,
    read_pbr_specular_properties,
    read_pbr_translucent_properties,
    read_pbr_texture_display_properties,
    apply_pbr_to_principled,
    apply_pbr_textures_to_material,
)
from .passthrough import (
    read_composite_materials,
    read_multiproperties,
    store_passthrough_materials,
)

__all__ = [
    # base
    "read_materials",
    "find_existing_material",
    "parse_hex_color",
    "srgb_to_linear",
    # textures
    "read_textures",
    "read_texture_groups",
    "extract_textures_from_archive",
    "get_or_create_textured_material",
    "setup_textured_material",
    "setup_multi_textured_material",
    # pbr
    "read_pbr_metallic_properties",
    "read_pbr_specular_properties",
    "read_pbr_translucent_properties",
    "read_pbr_texture_display_properties",
    "apply_pbr_to_principled",
    "apply_pbr_textures_to_material",
    # passthrough
    "read_composite_materials",
    "read_multiproperties",
    "store_passthrough_materials",
]
