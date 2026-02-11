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
Color space conversion helpers.

3MF hex colors are sRGB.  Blender materials work in linear.  These helpers
bridge the two representations.
"""

from typing import Tuple

__all__ = [
    "srgb_to_linear",
    "linear_to_srgb",
    "hex_to_rgb",
    "hex_to_linear_rgb",
    "rgb_to_hex",
    "linear_rgb_to_hex",
]


# ---------------------------------------------------------------------------
#  Color space conversion
# ---------------------------------------------------------------------------


def srgb_to_linear(c: float) -> float:
    """Convert a single sRGB gamma component to linear.

    3MF hex colors are sRGB.  Blender materials work in linear.
    Apply this when **importing** colors from 3MF into Blender materials.
    """
    if c <= 0.04045:
        return c / 12.92
    return pow((c + 0.055) / 1.055, 2.4)


def linear_to_srgb(c: float) -> float:
    """Convert a single linear component to sRGB gamma.

    Blender materials are linear.  3MF hex colors are sRGB.
    Apply this when **exporting** colors from Blender materials to 3MF hex.
    """
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * pow(c, 1.0 / 2.4) - 0.055


# ---------------------------------------------------------------------------
#  Hex / RGB helpers
# ---------------------------------------------------------------------------


def hex_to_rgb(hex_str: str) -> Tuple[float, float, float]:
    """Convert ``#RRGGBB`` hex string to an ``(r, g, b)`` tuple of 0-1 floats.

    Returns **raw sRGB** values — no gamma conversion.  Use this for the paint
    texture pipeline where sRGB pixel values must round-trip exactly.
    For Blender material colors, use :func:`hex_to_linear_rgb` instead.

    Leading ``#`` is optional.
    """
    hex_str = hex_str.lstrip("#")
    return (
        int(hex_str[0:2], 16) / 255.0,
        int(hex_str[2:4], 16) / 255.0,
        int(hex_str[4:6], 16) / 255.0,
    )


def hex_to_linear_rgb(hex_str: str) -> Tuple[float, float, float]:
    """Convert ``#RRGGBB`` hex string to an ``(r, g, b)`` tuple in **linear** space.

    Parses the sRGB hex value and applies sRGB-to-linear conversion.
    Use this when the result will be assigned to Blender material properties
    (e.g. ``principled.base_color``).

    Leading ``#`` is optional.
    """
    r, g, b = hex_to_rgb(hex_str)
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))


def rgb_to_hex(r: float, g: float, b: float) -> str:
    """Convert 0-1 float RGB values to a ``#RRGGBB`` hex string.

    Expects **raw sRGB** values.  For Blender linear colors, use
    :func:`linear_rgb_to_hex` instead.
    """
    return "#%02X%02X%02X" % (
        min(255, max(0, int(r * 255 + 0.5))),
        min(255, max(0, int(g * 255 + 0.5))),
        min(255, max(0, int(b * 255 + 0.5))),
    )


def linear_rgb_to_hex(r: float, g: float, b: float) -> str:
    """Convert linear RGB (0-1) to a ``#RRGGBB`` sRGB hex string.

    Applies linear-to-sRGB conversion before encoding.
    Use this when reading colors from Blender materials for 3MF export.
    """
    return rgb_to_hex(linear_to_srgb(r), linear_to_srgb(g), linear_to_srgb(b))
