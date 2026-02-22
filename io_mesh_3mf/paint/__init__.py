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
- ``helpers``           — Shared utilities (palette, brush config, mesh queries)
- ``properties``        — Blender PropertyGroups (filament items, paint settings)
- ``color_detection``   — Color extraction from materials/textures/vertex colors
- ``operators``         — All MMU_OT_* operators
- ``mmu_panel``         — VIEW3D_PT_mmu_paint panel + UILists + depsgraph handler
- ``bake``              — Bake-to-MMU operators and Shader Editor panel
"""

from . import helpers       # noqa: F401
from . import properties    # noqa: F401
from . import operators     # noqa: F401
from . import mmu_panel     # noqa: F401
from . import bake          # noqa: F401

# Re-export key helpers used by other packages (bake, import, export).
# These maintain backward-compatible import paths for external consumers.
from .helpers import (  # noqa: F401
    DEFAULT_PALETTE,
    _get_paint_image,
    _get_paint_mesh,
    _sync_filaments_from_mesh,
    _configure_paint_brush,
    _set_brush_color,
    _write_colors_to_mesh,
)

import bpy

from .properties import MMUFilamentItem, MMUInitFilamentItem, MMUPaintSettings
from .operators import (
    MMU_OT_initialize,
    MMU_OT_add_init_filament,
    MMU_OT_remove_init_filament,
    MMU_OT_reset_init_filaments,
    MMU_OT_detect_material_colors,
    MMU_OT_select_filament,
    MMU_OT_reassign_filament_color,
    MMU_OT_add_filament,
    MMU_OT_remove_filament,
    MMU_OT_fix_falloff,
    MMU_OT_switch_to_paint,
    MMU_OT_import_paint_popup,
)
from .mmu_panel import (
    MMU_UL_init_filaments,
    MMU_UL_filaments,
    VIEW3D_PT_mmu_paint,
    _on_depsgraph_update,
)


# All classes to register, in dependency order (PropertyGroups first).
_paint_classes = (
    MMUFilamentItem,
    MMUInitFilamentItem,
    MMUPaintSettings,
    MMU_OT_initialize,
    MMU_OT_add_init_filament,
    MMU_OT_remove_init_filament,
    MMU_OT_reset_init_filaments,
    MMU_OT_detect_material_colors,
    MMU_OT_select_filament,
    MMU_OT_reassign_filament_color,
    MMU_OT_add_filament,
    MMU_OT_remove_filament,
    MMU_OT_fix_falloff,
    MMU_OT_switch_to_paint,
    MMU_OT_import_paint_popup,
    MMU_UL_init_filaments,
    MMU_UL_filaments,
    VIEW3D_PT_mmu_paint,
)


def register():
    for cls in _paint_classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mmu_paint = bpy.props.PointerProperty(type=MMUPaintSettings)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    bake.register()


def unregister():
    bake.unregister()
    bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    del bpy.types.Scene.mmu_paint
    for cls in reversed(_paint_classes):
        bpy.utils.unregister_class(cls)
