# Blender add-on to import and export 3MF files.
# Copyright (C) 2020 Ghostkeeper
# Copyright (C) 2026 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Paint package — MMU painting and bake-to-MMU functionality.

Submodules:
- ``panel`` — MMU Paint Suite sidebar panel (3D Viewport)
- ``bake`` — Bake-to-MMU operators and Shader/GN Editor panels
"""

from . import panel  # noqa: F401
from . import bake   # noqa: F401

# Re-export key helpers used by other packages (bake, import, export)
from .panel import (  # noqa: F401
    _get_paint_image,
    _get_paint_mesh,
    _sync_filaments_from_mesh,
    _configure_paint_brush,
    _set_brush_color,
    _write_colors_to_mesh,
    DEFAULT_PALETTE,
)


def register():
    panel.register()
    bake.register()


def unregister():
    bake.unregister()
    panel.unregister()
