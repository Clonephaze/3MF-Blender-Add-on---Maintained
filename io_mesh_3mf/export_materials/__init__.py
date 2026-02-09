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
Materials Extension export functionality for 3MF files.

This package handles export of:
- Base materials (basematerials, colorgroups)
- Textures (texture2d, texture2dgroup)
- PBR display properties (metallic, specular, translucent)
- Passthrough/round-trip material data
"""

from .base import (
    ORCA_FILAMENT_CODES,
    material_to_hex_color,
    get_triangle_color,
    collect_face_colors,
    write_materials,
    write_prusa_filament_colors,
)

from .pbr import (
    extract_pbr_from_material,
    write_pbr_display_properties,
)

from .textures import (
    detect_textured_materials,
    detect_pbr_textured_materials,
    write_textures_to_archive,
    write_texture_relationships,
    write_texture_resources,
    get_or_create_tex2coord,
    write_pbr_textures_to_archive,
    write_pbr_texture_display_properties,
)

from .passthrough import (
    write_passthrough_materials,
    write_passthrough_textures_to_archive,
)

__all__ = [
    # Base materials
    "ORCA_FILAMENT_CODES",
    "material_to_hex_color",
    "get_triangle_color",
    "collect_face_colors",
    "write_materials",
    "write_prusa_filament_colors",
    # PBR
    "extract_pbr_from_material",
    "write_pbr_display_properties",
    # Textures
    "detect_textured_materials",
    "detect_pbr_textured_materials",
    "write_textures_to_archive",
    "write_texture_relationships",
    "write_texture_resources",
    "get_or_create_tex2coord",
    "write_pbr_textures_to_archive",
    "write_pbr_texture_display_properties",
    # Passthrough
    "write_passthrough_materials",
    "write_passthrough_textures_to_archive",
]
