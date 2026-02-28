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
    common,
    import_3mf,
    export_3mf,
    paint,
    panels,
    slicer_profiles,
)

if _needs_reload:
    import importlib

    common = importlib.reload(common)
    import_3mf = importlib.reload(import_3mf)
    export_3mf = importlib.reload(export_3mf)
    paint = importlib.reload(paint)
    panels = importlib.reload(panels)
    slicer_profiles = importlib.reload(slicer_profiles)
    pass  # Reloaded

from .import_3mf import Import3MF
from .export_3mf import Export3MF, EXPORT_MT_threemf_presets, EXPORT_OT_threemf_preset
from .paint import (
    register as register_paint,
    unregister as unregister_paint,
)
from .panels import (
    register as register_panels,
    unregister as unregister_panels,
)
from .slicer_profiles import (
    THREEMF_OT_load_slicer_profile,
    THREEMF_OT_delete_slicer_profile,
    THREEMF_OT_rename_slicer_profile,
)

# IDE and Documentation support.
__all__ = [
    "Export3MF",
    "EXPORT_MT_threemf_presets",
    "EXPORT_OT_threemf_preset",
    "Import3MF",
    "ThreeMF_FH_import",
    "ThreeMFPreferences",
    "THREEMF_OT_load_slicer_profile",
    "THREEMF_OT_delete_slicer_profile",
    "THREEMF_OT_rename_slicer_profile",
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
        return context.area and context.area.type in {"VIEW_3D", "OUTLINER"}


class ThreeMFPreferences(bpy.types.AddonPreferences):
    """
    Preferences for the 3MF addon.
    """

    bl_idname = __package__

    # ---- Preferences tab selector ----
    pref_tab: bpy.props.EnumProperty(
        name="Category",
        items=[
            ("EXPORT", "Export", "Default export settings"),
            ("IMPORT", "Import", "Default import settings"),
            ("ADVANCED", "Advanced", "Advanced settings and slicer profiles"),
        ],
        default="EXPORT",
    )

    show_slicer_profiles: bpy.props.BoolProperty(
        name="Slicer Profiles",
        default=True,
    )

    # ---- Precision settings ----
    default_coordinate_precision: bpy.props.IntProperty(
        name="Coordinate Precision",
        description=(
            "Number of decimal digits for vertex coordinates. "
            "9 = lossless 32-bit float precision (recommended for 3D printing). "
            "Lower values reduce file size but may cause manifold issues"
        ),
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
            (
                "MATERIALS",
                "Import Materials",
                "Import material colors and properties (standard 3MF)",
            ),
            (
                "PAINT",
                "Import MMU Paint Data",
                "Render multi-material segmentation to UV texture for painting (experimental, may be slow)",
            ),
            ("NONE", "Geometry Only", "Skip all material and color data"),
        ],
        default="MATERIALS",
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
            ("ORIGIN", "World Origin", "Place at world origin"),
            ("CURSOR", "3D Cursor", "Place at 3D cursor"),
            ("KEEP", "Keep Original", "Keep positions from file"),
            ("GRID", "Grid Layout", "Arrange files in a grid (for multi-file import)"),
        ],
        default="KEEP",
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
            ("KEEP", "Keep Original", "Keep origin from 3MF file (typically corner)"),
            ("CENTER", "Center of Geometry", "Move origin to center of bounding box"),
            (
                "BOTTOM",
                "Bottom Center",
                "Move origin to bottom center (useful for placing on surfaces)",
            ),
        ],
        default="KEEP",
    )

    default_auto_smooth: bpy.props.BoolProperty(
        name="Smooth by Angle",
        description="Apply Smooth by Angle modifier to imported objects by default",
        default=False,
    )

    default_auto_smooth_angle: bpy.props.FloatProperty(
        name="Angle",
        description="Default maximum angle between face normals that will be considered smooth",
        default=0.5236,
        min=0.0,
        max=3.14159,
        subtype="ANGLE",
    )

    default_multi_material_export: bpy.props.EnumProperty(
        name="Material Export Mode",
        description="How to export material and color data to 3MF",
        items=[
            (
                "AUTO",
                "Auto",
                "Automatically detect materials and MMU paint data, "
                "choosing the best exporter",
            ),
            (
                "STANDARD",
                "Standard 3MF",
                "Always export spec-compliant 3MF with proper component instancing",
            ),
            (
                "PAINT",
                "Paint Segmentation",
                "Export UV-painted regions as hash segmentation (experimental, may be slow)",
            ),
        ],
        default="AUTO",
    )

    default_subdivision_depth: bpy.props.IntProperty(
        name="Subdivision Depth",
        description=(
            "Default subdivision depth for paint segmentation export. "
            "Higher values capture finer color boundaries but increase export time (4-10)"
        ),
        default=7,
        min=4,
        max=10,
    )

    default_compression_level: bpy.props.IntProperty(
        name="Compression Level",
        description=(
            "Default ZIP compression level for exported 3MF archives. "
            "0 = no compression (fastest), 9 = maximum compression (smallest). "
            "3 is a good balance between speed and file size"
        ),
        default=3,
        min=0,
        max=9,
    )

    default_thumbnail_mode: bpy.props.EnumProperty(
        name="Thumbnail",
        description="Default thumbnail mode for 3MF export",
        items=[
            ("AUTO", "Automatic", "Render a clean preview from an elevated 3/4 angle"),
            ("CUSTOM", "Custom Image", "Use a custom image file as the thumbnail"),
            ("NONE", "None", "Do not include a thumbnail"),
        ],
        default="AUTO",
    )

    default_thumbnail_resolution: bpy.props.IntProperty(
        name="Thumbnail Resolution",
        description="Default thumbnail size in pixels (square)",
        default=256,
        min=64,
        max=1024,
        step=64,
    )

    def draw(self, context):
        layout = self.layout

        # Tab selector
        row = layout.row()
        row.prop(self, "pref_tab", expand=True)
        layout.separator()

        if self.pref_tab == "EXPORT":
            self._draw_export(layout)
        elif self.pref_tab == "IMPORT":
            self._draw_import(layout)
        elif self.pref_tab == "ADVANCED":
            self._draw_advanced(layout)

    def _draw_export(self, layout):
        col = layout.column(align=True)
        col.label(text="Material Export Mode:", icon="COLORSET_01_VEC")
        col.prop(self, "default_multi_material_export", text="")
        if self.default_multi_material_export in ("PAINT", "AUTO"):
            col.prop(self, "default_subdivision_depth")
        col.separator()
        col.prop(self, "default_export_hidden", icon="HIDE_OFF")
        col.prop(self, "default_apply_modifiers", icon="MODIFIER")
        col.separator()
        col.prop(self, "default_coordinate_precision")
        col.prop(self, "default_global_scale")
        col.separator()
        col.prop(self, "default_compression_level")
        col.separator()
        col.label(text="Thumbnail:", icon="IMAGE_DATA")
        col.prop(self, "default_thumbnail_mode")
        if self.default_thumbnail_mode == "AUTO":
            col.prop(self, "default_thumbnail_resolution")

    def _draw_import(self, layout):
        col = layout.column(align=True)
        col.prop(self, "default_import_materials", icon="MATERIAL")
        col.prop(self, "default_reuse_materials", icon="LINKED")
        col.separator()
        col.label(text="Placement:", icon="OBJECT_ORIGIN")
        col.prop(self, "default_import_location")
        if self.default_import_location == "GRID":
            col.prop(self, "default_grid_spacing")
        col.prop(self, "default_origin_to_geometry")
        col.separator()
        col.label(text="Normals:", icon="MOD_SMOOTH")
        col.prop(self, "default_auto_smooth")
        if self.default_auto_smooth:
            col.prop(self, "default_auto_smooth_angle")

    def _draw_advanced(self, layout):
        from .slicer_profiles import list_profiles

        # ---- Slicer Profiles (collapsible) ----
        box = layout.box()
        row = box.row()
        row.prop(
            self, "show_slicer_profiles",
            icon="TRIA_DOWN" if self.show_slicer_profiles else "TRIA_RIGHT",
            emboss=False,
        )
        if self.show_slicer_profiles:
            profiles = list_profiles()
            if profiles:
                for p in profiles:
                    prow = box.row(align=True)
                    detail = p.machine or p.vendor
                    if p.vendor in ("PrusaSlicer", "SuperSlicer"):
                        tag = "Prusa"
                    elif p.vendor in ("Orca Slicer", "BambuStudio"):
                        tag = "Orca/Bambu"
                    else:
                        tag = p.vendor
                    prow.label(
                        text=f"[{tag}]  {p.name}  \u2014  {detail}",
                        icon="FILE_3D",
                    )
                    op = prow.operator(
                        "threemf.rename_slicer_profile",
                        text="", icon="GREASEPENCIL",
                    )
                    op.old_name = p.name
                    op = prow.operator(
                        "threemf.delete_slicer_profile",
                        text="", icon="X",
                    )
                    op.profile_name = p.name
            else:
                box.label(text="No profiles saved", icon="INFO")
            box.operator("threemf.load_slicer_profile", icon="FILEBROWSER")


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


classes = (
    ThreeMFPreferences,
    EXPORT_MT_threemf_presets,
    EXPORT_OT_threemf_preset,
    THREEMF_OT_load_slicer_profile,
    THREEMF_OT_delete_slicer_profile,
    THREEMF_OT_rename_slicer_profile,
    Import3MF,
    Export3MF,
    ThreeMF_FH_import,
)


def register() -> None:
    for cls in classes:
        bpy.utils.register_class(cls)

    # Guard against duplicate menu entries on reinstall / reload.
    _remove_menu_entries()
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)

    register_paint()
    register_panels()

    # Register API in bpy.app.driver_namespace for addon discovery.
    # The api module self-registers on import, but we explicitly call it here
    # to ensure it's available after addon enable (not just first import).
    try:
        from . import api
        api._register_api()
    except Exception:
        pass  # Non-critical — API still works via direct import


def _remove_menu_entries() -> None:
    """Remove our import/export menu entries, tolerating stale references.

    On reinstall (drag-and-drop zip), Blender may call unregister() with
    new function objects that don't match the old ones that were append()ed.
    We walk the draw funcs and remove ANY entry whose qualified name matches
    ours, regardless of object identity.
    """
    for menu, func_name in (
        (bpy.types.TOPBAR_MT_file_import, menu_import.__qualname__),
        (bpy.types.TOPBAR_MT_file_export, menu_export.__qualname__),
    ):
        draw_funcs = getattr(menu, "_dyn_ui_initialize", lambda: menu.draw._draw_funcs)()
        to_remove = [f for f in draw_funcs if getattr(f, "__qualname__", None) == func_name]
        for f in to_remove:
            try:
                menu.remove(f)
            except ValueError:
                pass


def unregister() -> None:
    # Unregister API from bpy.app.driver_namespace.
    try:
        from . import api
        api._unregister_api()
    except Exception:
        pass

    unregister_panels()
    unregister_paint()

    _remove_menu_entries()

    for cls in classes:
        bpy.utils.unregister_class(cls)


# Allow the add-on to be ran directly without installation.
if __name__ == "__main__":
    register()
