# Blender add-on to import and export 3MF files.
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
FilamentMixer pigment-blending model.

Provides ``blend_two()`` and ``blend_multi()`` which approximate physical
pigment (subtractive / Mixbox-style) color mixing.  Blue + Yellow → Green,
not Teal.

The underlying model is a degree-4 polynomial regression with 330 features
over 7 inputs (R1, G1, B1, R2, G2, B2, t), trained to approximate Mixbox
behavior.  Mean Delta-E ≈ 2.07 vs the Mixbox reference.

**License:** The coefficient data (in ``_filament_mixer_data.py``) was
extracted from OrcaSlicer-FullSpectrum, which carries an MIT license:
"Copyright 2026 Justin Hayes".  Attribution is preserved here.

Source reference: OrcaSlicer-FullSpectrum ``src/libslic3r/filament_mixer_model.h``
and ``src/libslic3r/filament_mixer.cpp``.
"""

from __future__ import annotations

from typing import List, Tuple

__all__ = [
    "filament_mixer_lerp",
    "blend_two",
    "blend_multi",
]


# ---------------------------------------------------------------------------
# Lazy-load the coefficient tables (avoids import cost at addon startup)
# ---------------------------------------------------------------------------

_POWERS = None  # List[List[int]], shape (330, 7)
_COEF = None    # List[List[float]], shape (330, 3)
_INTERCEPT = None  # List[float], length 3


def _ensure_loaded() -> None:
    global _POWERS, _COEF, _INTERCEPT
    if _POWERS is not None:
        return
    from . import _filament_mixer_data as _d
    _POWERS = _d.POWERS
    _COEF = _d.COEF
    _INTERCEPT = _d.INTERCEPT


# ---------------------------------------------------------------------------
# Core blending
# ---------------------------------------------------------------------------

def filament_mixer_lerp(
    r1: int, g1: int, b1: int,
    r2: int, g2: int, b2: int,
    t: float,
) -> Tuple[int, int, int]:
    """Blend two sRGB colors using the FilamentMixer pigment model.

    Port of ``filament_mixer::lerp()`` from ``filament_mixer_model.h`` (L766-812).

    :param r1, g1, b1: First color, sRGB 0-255.
    :param r2, g2, b2: Second color, sRGB 0-255.
    :param t: Blend factor — 0.0 = 100% first color, 1.0 = 100% second color.
    :return: Blended color as ``(r, g, b)`` sRGB 0-255 tuple.
    """
    if t <= 0.0:
        return (r1, g1, b1)
    if t >= 1.0:
        return (r2, g2, b2)

    _ensure_loaded()

    # 7 inputs: r1, g1, b1, r2, g2, b2, t
    x = (float(r1), float(g1), float(b1), float(r2), float(g2), float(b2), float(t))

    # Compute 330 polynomial features (all monomials up to degree 4 in 7 vars)
    features = [0.0] * 330
    for i, exponents in enumerate(_POWERS):
        val = 1.0
        for j, exp in enumerate(exponents):
            if exp == 0:
                continue
            base = x[j]
            p = 1.0
            for _ in range(exp):
                p *= base
            val *= p
        features[i] = val

    # Dot product with coefficient matrix + intercept → output R, G, B
    coef = _COEF
    intercept = _INTERCEPT
    result = []
    for c in range(3):
        s = intercept[c]
        col = [coef[i][c] for i in range(330)]
        for i in range(330):
            s += features[i] * col[i]
        result.append(max(0, min(255, int(s))))

    return (result[0], result[1], result[2])


def _parse_hex(hex_color: str) -> Tuple[int, int, int]:
    """Parse a ``"#RRGGBB"`` string to sRGB (0-255) ints."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    if len(h) == 3:
        return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16))
    return (0, 0, 0)


def _to_hex(r: int, g: int, b: int) -> str:
    """Convert sRGB (0-255) ints to ``"#RRGGBB"``."""
    return "#%02X%02X%02X" % (r, g, b)


def blend_two(hex_a: str, hex_b: str, ratio_a: int, ratio_b: int) -> str:
    """Blend two hex colors using the FilamentMixer pigment model.

    Port of ``MixedFilamentManager::blend_color()`` from MixedFilament.cpp
    (L2176-2207).

    :param hex_a: First color ``"#RRGGBB"``.
    :param hex_b: Second color ``"#RRGGBB"``.
    :param ratio_a: Weight of the first color (any non-negative int).
    :param ratio_b: Weight of the second color (any non-negative int).
    :return: Blended color as ``"#RRGGBB"``.
    """
    safe_a = max(0, ratio_a)
    safe_b = max(0, ratio_b)
    total = safe_a + safe_b
    if total == 0:
        t = 0.5
    else:
        t = float(safe_b) / float(total)

    r1, g1, b1 = _parse_hex(hex_a)
    r2, g2, b2 = _parse_hex(hex_b)
    r, g, b = filament_mixer_lerp(r1, g1, b1, r2, g2, b2, t)
    return _to_hex(r, g, b)


def blend_multi(color_percents: List[Tuple[str, int]]) -> str:
    """Blend multiple hex colors using sequential pairwise FilamentMixer blending.

    Port of ``MixedFilamentManager::blend_color_multi()`` from MixedFilament.cpp
    (L2128-2175).

    Colors are accumulated left-to-right: at each step the running result is
    lerped toward the next color by ``t = next_weight / accumulated_weight``.

    :param color_percents: List of ``(hex_color, weight)`` pairs.  Zero or
        negative weights are skipped.
    :return: Blended color as ``"#RRGGBB"``, or ``"#000000"`` for empty input.
    """
    if not color_percents:
        return "#000000"
    if len(color_percents) == 1:
        return color_percents[0][0]

    # Filter zero-weight entries
    valid = [(h, w) for h, w in color_percents if w > 0]
    if not valid:
        return "#000000"
    if len(valid) == 1:
        return valid[0][0]

    r, g, b = _parse_hex(valid[0][0])
    accumulated = valid[0][1]

    for hex_next, weight in valid[1:]:
        new_total = accumulated + weight
        if new_total <= 0:
            continue
        t = float(weight) / float(new_total)
        r2, g2, b2 = _parse_hex(hex_next)
        r, g, b = filament_mixer_lerp(r, g, b, r2, g2, b2, t)
        accumulated = new_total

    return _to_hex(r, g, b)
