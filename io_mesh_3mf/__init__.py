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

# Reload functionality.
if "bpy" in locals():
    import importlib
    from . import import_3mf, export_3mf

    importlib.reload(import_3mf)
    importlib.reload(export_3mf)
else:
    from . import import_3mf, export_3mf

import bpy.types  # To (un)register the add-on as an import/export function.
import bpy.props  # For addon preferences properties.
import bpy.utils  # To (un)register the add-on.

from .export_3mf import Export3MF  # Exports 3MF files.
from .import_3mf import Import3MF  # Imports 3MF files.

# IDE and Documentation support.
__all__ = [
    "Export3MF",
    "Import3MF",
    "ThreeMFPreferences",
    "register",
    "unregister",
]

"""
Import and export 3MF files in Blender.
"""


class ThreeMFPreferences(bpy.types.AddonPreferences):
    """
    Preferences for the 3MF addon.
    """
    bl_idname = __package__

    # Precision settings
    default_coordinate_precision: bpy.props.IntProperty(
        name="Coordinate Precision",
        description=("Number of decimal digits for vertex coordinates. "
                     "9 = lossless 32-bit float precision (recommended for 3D printing). "
                     "Lower values reduce file size but may cause manifold issues"),
        default=9,
        min=0,
        max=12,
    )

    # Export behavior settings
    default_export_hidden: bpy.props.BoolProperty(
        name="Include Hidden Objects",
        description="Include viewport-hidden objects in exports. When off, hidden objects are skipped",
        default=False,
    )

    default_apply_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers",
        description="Bake modifiers into mesh before export. Disable to export base mesh only",
        default=True,
    )

    # Scale settings
    default_global_scale: bpy.props.FloatProperty(
        name="Global Scale",
        description="Scale factor applied during import and export. Use 0.001 to convert mm to m",
        default=1.0,
        soft_min=0.001,
        soft_max=1000.0,
        min=1e-6,
        max=1e6,
    )

    # Import behavior settings
    default_import_materials: bpy.props.BoolProperty(
        name="Import Materials",
        description="Import material colors from 3MF files by default. "
                    "Disable to import geometry only",
        default=True,
    )

    def draw(self, context):
        layout = self.layout

        # Precision section
        precision_box = layout.box()
        precision_box.label(text="Precision", icon='PREFERENCES')
        row = precision_box.row()
        row.prop(self, "default_coordinate_precision")
        precision_box.label(text="Tip: 9 decimals preserves full 32-bit float precision", icon='INFO')

        # Scale section
        scale_box = layout.box()
        scale_box.label(text="Scale (Import & Export)", icon='ORIENTATION_GLOBAL')
        scale_box.prop(self, "default_global_scale")

        # Export behavior section
        export_box = layout.box()
        export_box.label(text="Export Behavior", icon='EXPORT')
        col = export_box.column(align=True)
        col.prop(self, "default_export_hidden", icon='HIDE_OFF')
        col.prop(self, "default_apply_modifiers", icon='MODIFIER')

        # Import behavior section
        import_box = layout.box()
        import_box.label(text="Import Behavior", icon='IMPORT')
        import_box.prop(self, "default_import_materials", icon='MATERIAL')


def menu_import(self, _) -> None:
    """
    Calls the 3MF import operator from the menu item.
    """
    self.layout.operator(Import3MF.bl_idname, text="3D Manufacturing Format (.3mf)")


def menu_export(self, _) -> None:
    """
    Calls the 3MF export operator from the menu item.
    """
    self.layout.operator(Export3MF.bl_idname, text="3D Manufacturing Format (.3mf)")


classes = (ThreeMFPreferences, Import3MF, Export3MF)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister() -> None:
    for cls in classes:
        bpy.utils.unregister_class(cls)

    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)


# Allow the add-on to be ran directly without installation.
if __name__ == "__main__":
    register()
