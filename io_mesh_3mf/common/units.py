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
Unit conversions between Blender's units and 3MF's units.
"""

from typing import Dict

import bpy.types  # For type hints in unit_scale functions

from .constants import MODEL_DEFAULT_UNIT

__all__ = [
    "blender_to_metre",
    "threemf_to_metre",
    "import_unit_scale",
    "export_unit_scale",
]

blender_to_metre: Dict[str, float] = {
    # Scale of each of Blender's length units to a metre.
    "THOU": 0.0000254,
    "INCHES": 0.0254,
    "FEET": 0.3048,
    "YARDS": 0.9144,
    "CHAINS": 20.1168,
    "FURLONGS": 201.168,
    "MILES": 1609.344,
    "MICROMETERS": 0.000001,
    "MILLIMETERS": 0.001,
    "CENTIMETERS": 0.01,
    "DECIMETERS": 0.1,
    "METERS": 1,
    "ADAPTIVE": 1,
    "DEKAMETERS": 10,
    "HECTOMETERS": 100,
    "KILOMETERS": 1000,
}

threemf_to_metre: Dict[str, float] = {
    # Scale of each of 3MF's length units to a metre.
    "micron": 0.000001,
    "millimeter": 0.001,
    "centimeter": 0.01,
    "inch": 0.0254,
    "foot": 0.3048,
    "meter": 1,
}


def import_unit_scale(
    context: bpy.types.Context,
    root,
    global_scale: float = 1.0,
) -> float:
    """Compute the import scale factor from 3MF document units to Blender scene units.

    :param context: The Blender context (for scene unit settings).
    :param root: The XML root element of the 3MF model (reads ``unit`` attribute).
    :param global_scale: Additional user-specified scale multiplier.
    :return: Combined scale factor to apply to coordinates.
    """
    scale = global_scale

    blender_unit_to_metre = context.scene.unit_settings.scale_length
    if blender_unit_to_metre == 0:  # Fallback for special cases.
        blender_unit = context.scene.unit_settings.length_unit
        blender_unit_to_metre = blender_to_metre[blender_unit]

    threemf_unit = root.attrib.get("unit", MODEL_DEFAULT_UNIT)
    threemf_unit_to_metre = threemf_to_metre[threemf_unit]

    scale *= threemf_unit_to_metre / blender_unit_to_metre
    return scale


def export_unit_scale(context: bpy.types.Context, global_scale: float = 1.0) -> float:
    """Compute the export scale factor from Blender scene units to 3MF millimeters.

    :param context: The Blender context (for scene unit settings).
    :param global_scale: Additional user-specified scale multiplier.
    :return: Scale factor to apply to coordinates during export.
    """
    scale = global_scale

    blender_unit_to_metre = context.scene.unit_settings.scale_length
    if blender_unit_to_metre == 0:
        blender_unit = context.scene.unit_settings.length_unit
        blender_unit_to_metre = blender_to_metre[blender_unit]

    threemf_unit_to_metre = threemf_to_metre[MODEL_DEFAULT_UNIT]  # Always export as mm

    scale *= blender_unit_to_metre / threemf_unit_to_metre
    return scale
