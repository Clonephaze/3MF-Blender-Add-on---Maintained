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
3MF Import Package.

Provides the :class:`Import3MF` operator and all supporting modules for reading
3MF files into Blender.

Modules:
    - ``operator`` — Blender operator (Import3MF)
    - ``context`` — ImportContext / ImportOptions dataclasses
    - ``archive`` — ZIP archive reading, content types, must-preserve
    - ``geometry`` — Vertex / triangle / component parsing
    - ``builder`` — Orchestrator: parsed resources → Blender objects
    - ``scene`` — Mesh creation, materials, UVs, origin, grid layout
    - ``segmentation`` — Hash segmentation → UV texture rendering
    - ``triangle_sets`` — Triangle Sets Extension import

Sub-packages:
    - ``materials/`` — Materials Extension import (basematerials, textures, PBR, passthrough)
    - ``slicer/`` — Slicer-specific detection, filament colors, paint codes
"""

from .operator import Import3MF

__all__ = ["Import3MF"]
