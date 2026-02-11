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
Slicer-specific import helpers.

- ``detection.py`` — Detect vendor format (Orca/BambuStudio, PrusaSlicer)
- ``colors.py`` — Read filament/extruder colors from slicer config files
- ``paint.py`` — Orca paint codes, PrusaSlicer segmentation subdivision
"""

from .detection import detect_vendor
from .colors import (
    read_orca_filament_colors,
    read_prusa_slic3r_colors,
    read_blender_addon_colors,
    read_prusa_object_extruders,
    read_prusa_filament_colors,
)
from .paint import (
    ORCA_PAINT_TO_INDEX,
    parse_paint_color_to_index,
    get_or_create_paint_material,
    subdivide_prusa_segmentation,
)

__all__ = [
    "detect_vendor",
    "read_orca_filament_colors",
    "read_prusa_slic3r_colors",
    "read_blender_addon_colors",
    "read_prusa_object_extruders",
    "read_prusa_filament_colors",
    "ORCA_PAINT_TO_INDEX",
    "parse_paint_color_to_index",
    "get_or_create_paint_material",
    "subdivide_prusa_segmentation",
]
