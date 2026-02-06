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

# Reload functionality - must check before importing bpy
_needs_reload = "bpy" in locals()

import bpy.types  # To (un)register the add-on as an import/export function.
import bpy.props  # For addon preferences properties.
import bpy.utils  # To (un)register the add-on.

from . import (
    import_3mf,
    export_3mf,
)

if _needs_reload:
    import importlib
    import_3mf = importlib.reload(import_3mf)
    export_3mf = importlib.reload(export_3mf)
    print("3MF Format Add-on Reloaded")

from .export_3mf import Export3MF  # Exports 3MF files.
from .import_3mf import Import3MF  # Imports 3MF files.

# IDE and Documentation support.
__all__ = [
    "Export3MF",
    "Import3MF",
    "ThreeMF_FH_import",
    "ThreeMFPreferences",
    "register",
    "unregister",
]

"""
Import and export 3MF files in Blender.
"""


class ThreeMF_FH_import(bpy.types.FileHandler):
    """
    FileHandler for drag-and-drop import of 3MF files.

    Enables users to drag .3mf files directly into Blender's 3D viewport
    to import them. Supports multiple files at once.

    Requires Blender 4.2+ (FileHandler API).
    """
    bl_idname = "IMPORT_FH_threemf"
    bl_label = "3MF File Handler"
    bl_import_operator = "import_mesh.threemf"
    bl_file_extensions = ".3mf"

    @classmethod
    def poll_drop(cls, context):
        """
        Allow drops in the 3D viewport and outliner.

        :param context: The current Blender context
        :return: True if the drop should be handled
        """
        return context.area and context.area.type in {'VIEW_3D', 'OUTLINER'}


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
    default_import_materials: bpy.props.EnumProperty(
        name="Material Import Mode",
        description="How to handle materials and multi-material paint data",
        items=[
            ('MATERIALS', 'Import Materials', 'Import material colors and properties (standard 3MF)'),
            ('PAINT', 'Import MMU Paint Data', 'Render multi-material segmentation to UV texture for painting (experimental, may be slow)'),
            ('NONE', 'Geometry Only', 'Skip all material and color data'),
        ],
        default='MATERIALS',
    )

    default_reuse_materials: bpy.props.BoolProperty(
        name="Reuse Existing Materials",
        description="Match and reuse existing Blender materials by name and color instead of always creating new ones. "
                    "Prevents material duplication when re-importing edited files",
        default=True,
    )

    default_import_location: bpy.props.EnumProperty(
        name="Import Location",
        description="Default location for imported objects",
        items=[
            ('ORIGIN', 'World Origin', 'Place at world origin'),
            ('CURSOR', '3D Cursor', 'Place at 3D cursor'),
            ('KEEP', 'Keep Original', 'Keep positions from file'),
            ('GRID', 'Grid Layout', 'Arrange files in a grid (for multi-file import)'),
        ],
        default='KEEP',
    )

    default_grid_spacing: bpy.props.FloatProperty(
        name="Grid Spacing",
        description="Spacing between objects when using Grid Layout placement (in scene units). "
                    "Objects are arranged in a grid pattern with this gap between them",
        default=0.1,
        min=0.0,
        soft_max=10.0,
    )

    default_origin_to_geometry: bpy.props.EnumProperty(
        name="Origin Placement",
        description="How to set the object origin after import",
        items=[
            ('KEEP', 'Keep Original', 'Keep origin from 3MF file (typically corner)'),
            ('CENTER', 'Center of Geometry', 'Move origin to center of bounding box'),
            ('BOTTOM', 'Bottom Center', 'Move origin to bottom center (useful for placing on surfaces)'),
        ],
        default='KEEP',
    )

    default_multi_material_export: bpy.props.EnumProperty(
        name="Material Export Mode",
        description="How to export material and color data to 3MF",
        items=[
            ('STANDARD', 'Standard 3MF', 'Export basic geometry without material data (maximum compatibility)'),
            ('BASEMATERIAL', 'Base Material', 'Export one solid color per object (simple multi-color prints)'),
            ('PAINT', 'Paint Segmentation', 'Export UV-painted regions as hash segmentation (experimental, may be slow)'),
        ],
        default='BASEMATERIAL',
    )

    default_export_triangle_sets: bpy.props.BoolProperty(
        name="Export Triangle Sets",
        description="Export Blender face maps as 3MF triangle sets by default. "
                    "Triangle sets group triangles for selection workflows and property assignment",
        default=False,
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
        col.prop(self, "default_multi_material_export", icon='COLORSET_01_VEC')
        col.prop(self, "default_export_triangle_sets", icon='OUTLINER_DATA_GP_LAYER')

        # Import behavior section
        import_box = layout.box()
        import_box.label(text="Import Behavior", icon='IMPORT')
        col = import_box.column(align=True)
        col.prop(self, "default_import_materials", icon='MATERIAL')
        col.prop(self, "default_reuse_materials", icon='LINKED')
        col.separator()
        col.label(text="Placement:", icon='OBJECT_ORIGIN')
        col.prop(self, "default_import_location")
        # Show grid spacing only when grid layout is selected
        if self.default_import_location == 'GRID':
            col.prop(self, "default_grid_spacing")
        col.prop(self, "default_origin_to_geometry")


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


classes = (ThreeMFPreferences, Import3MF, Export3MF, ThreeMF_FH_import)


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
