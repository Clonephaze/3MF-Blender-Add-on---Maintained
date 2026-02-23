# Blender add-on to import and export 3MF files.
# Copyright (C) 2025 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
MMU Paint sidebar panel — VIEW3D_PT_mmu_paint.

The main 3D Viewport panel for multi-filament texture painting, visible in
Texture Paint mode (``bl_context = "imagepaint"``).  Contains two UIList
subclasses and the panel's draw logic.

Also registers the depsgraph handler that auto-syncs the palette when the
active object changes.
"""

import bpy
import bpy.types

from .helpers import (
    _get_paint_mesh,
    _sync_filaments_from_mesh,
    _has_vertex_colors,
)


# ===================================================================
#  UILists
# ===================================================================


class MMU_UL_init_filaments(bpy.types.UIList):
    """Two-column initialization filament list: color picker + name."""

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_property, index
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            # Editable color swatch column
            swatch = row.row()
            swatch.ui_units_x = 1.5
            swatch.prop(item, "color", text="")
            # Wider name column
            row.label(text=item.name)
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.prop(item, "color", text="")


class MMU_UL_filaments(bpy.types.UIList):
    """Two-column filament list: color swatch + name label."""

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_property, index
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            # Skinny color swatch column (read-only display)
            swatch = row.row()
            swatch.ui_units_x = 1.5
            swatch.enabled = False  # Make read-only
            swatch.prop(item, "color", text="")
            # Wider name column
            row.label(text=item.name)
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.prop(item, "color", text="")


# ===================================================================
#  Panel
# ===================================================================


class VIEW3D_PT_mmu_paint(bpy.types.Panel):
    """MMU Paint Suite — multi-filament texture painting for 3MF export."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "MMU Paint"
    bl_context = "imagepaint"

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None and context.active_object.type == "MESH"
        )

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mmu_paint
        mesh = _get_paint_mesh(context)

        if mesh is None:
            # ============================
            #  STATE A: Uninitialized
            # ============================
            box = layout.box()
            box.label(text="Setup MMU Painting", icon="BRUSH_DATA")

            # Initialize list if empty
            if len(settings.init_filaments) == 0:
                box.operator(
                    "mmu.reset_init_filaments",
                    text="Create Default Palette",
                    icon="ADD",
                )
            else:
                # Show filament list
                row = box.row()
                row.template_list(
                    "MMU_UL_init_filaments",
                    "",
                    settings,
                    "init_filaments",
                    settings,
                    "active_init_filament_index",
                    rows=3,
                    maxrows=8,
                )

                # Add/Remove buttons
                col = row.column(align=True)
                col.operator("mmu.add_init_filament", icon="ADD", text="")
                col.operator("mmu.remove_init_filament", icon="REMOVE", text="")

                # Reset and Initialize buttons
                row = box.row(align=True)
                row.operator("mmu.reset_init_filaments", icon="FILE_REFRESH")
                row.operator("mmu.initialize_painting", icon="PLAY", text="Initialize")

                # UV method setting
                uv_box = box.box()
                uv_box.prop(settings, "uv_method")
                if settings.uv_method == "LIGHTMAP":
                    uv_box.prop(settings, "lightmap_divisions")

                # Bake to MMU — for procedural/complex materials
                obj = context.active_object
                has_mats = obj and obj.data.materials and obj.data.materials[0]
                has_vcol = obj and _has_vertex_colors(obj)
                if has_mats or has_vcol:
                    layout.separator()
                    bake_box = layout.box()
                    bake_box.label(text="From Existing Material", icon="RENDER_STILL")
                    if has_mats:
                        bake_row = bake_box.row()
                        bake_row.scale_y = 1.2
                        bake_row.operator("mmu.bake_to_mmu", icon="RENDER_STILL")
                    detect_row = bake_box.row()
                    detect_row.operator(
                        "mmu.detect_material_colors", icon="MATERIAL",
                    )
                    info = bake_box.column(align=True)
                    info.scale_y = 0.7
                    info.label(text="Bake a procedural material to")
                    info.label(text="discrete filament colors for export")

        else:
            # ============================
            #  STATE B: Active palette
            # ============================

            active_layer = settings.active_paint_layer

            # --- Paint layer selector ---
            layer_box = layout.box()
            layer_box.label(text="Paint Layer", icon="OUTLINER_DATA_GP_LAYER")
            layer_row = layer_box.row(align=True)

            # Color layer button
            op = layer_row.operator(
                "mmu.switch_paint_layer", text="Color",
                icon="BRUSHES_ALL",
                depress=(active_layer == "COLOR"),
            )
            op.layer_type = "COLOR"

            # Seam layer button
            has_seam = mesh.get("3mf_has_seam_paint", False)
            if has_seam:
                op = layer_row.operator(
                    "mmu.switch_paint_layer", text="Seam",
                    icon="MOD_EDGESPLIT",
                    depress=(active_layer == "SEAM"),
                )
                op.layer_type = "SEAM"
            else:
                op = layer_row.operator(
                    "mmu.init_auxiliary_paint", text="Init Seam",
                    icon="MOD_EDGESPLIT",
                )
                op.layer_type = "SEAM"

            # Support layer button
            has_support = mesh.get("3mf_has_support_paint", False)
            if has_support:
                op = layer_row.operator(
                    "mmu.switch_paint_layer", text="Support",
                    icon="MOD_LATTICE",
                    depress=(active_layer == "SUPPORT"),
                )
                op.layer_type = "SUPPORT"
            else:
                op = layer_row.operator(
                    "mmu.init_auxiliary_paint", text="Init Support",
                    icon="MOD_LATTICE",
                )
                op.layer_type = "SUPPORT"

            if active_layer == "COLOR":
                # --- Filament list (color layer only) ---
                box = layout.box()
                box.label(text="Filament Palette", icon="COLOR")

                row = box.row()
                row.template_list(
                    "MMU_UL_filaments",
                    "",
                    settings,
                    "filaments",
                    settings,
                    "active_filament_index",
                    rows=3,
                    maxrows=6,
                )

                # Add/Remove buttons
                col = row.column(align=True)
                col.operator("mmu.add_filament", icon="ADD", text="")
                col.operator("mmu.remove_filament", icon="REMOVE", text="")

                # Reassign color button below list
                box.operator("mmu.reassign_filament_color", icon="COLORSET_01_VEC")

            else:
                # --- Seam / Support layer palette ---
                from .helpers import _layer_colors
                bg, enforce, block = _layer_colors(active_layer)
                label = active_layer.title()

                box = layout.box()
                box.label(text=f"{label} Paint", icon="BRUSH_DATA")

                # Enforce / Block color swatches
                row = box.row(align=True)
                enforce_btn = row.operator(
                    "mmu.switch_aux_brush", text="Enforce",
                    depress=True,
                )
                enforce_btn.layer_type = active_layer
                enforce_btn.mode = "ENFORCE"

                block_btn = row.operator(
                    "mmu.switch_aux_brush", text="Block",
                    depress=False,
                )
                block_btn.layer_type = active_layer
                block_btn.mode = "BLOCK"

                info = box.column(align=True)
                info.scale_y = 0.7
                info.label(text="Enforce: painted areas are forced")
                info.label(text="Block: painted areas are prevented")

            # --- Brush falloff warning ---
            brush = context.tool_settings.image_paint.brush
            if brush:
                is_constant = False
                try:
                    is_constant = brush.curve_distance_falloff_preset == "CONSTANT"
                except AttributeError:
                    pass

                if not is_constant:
                    warn_box = layout.box()
                    warn_box.alert = True
                    warn_box.label(text="Soft edges will cause banding", icon="ERROR")
                    warn_box.label(text="issues on export")
                    warn_box.operator("mmu.fix_falloff", icon="CHECKMARK")

            # --- Quantize button ---
            layout.separator()
            quant_box = layout.box()
            quant_box.label(text="Cleanup", icon="BRUSH_DATA")
            quant_box.operator("mmu.quantize_texture", icon="SNAP_ON")
            info = quant_box.column(align=True)
            info.scale_y = 0.7
            info.label(text="Snap all pixels to the nearest")
            info.label(text="filament color to clean up edges")


# ===================================================================
#  Object-switch handler
# ===================================================================

_last_active_object_name = ""


def _on_depsgraph_update(scene, depsgraph=None):
    """Re-sync the panel palette when the active object changes."""
    global _last_active_object_name

    try:
        ctx = bpy.context
        obj = ctx.active_object
        current_name = obj.name if obj else ""

        if current_name != _last_active_object_name:
            _last_active_object_name = current_name
            if obj and obj.type == "MESH":
                settings = scene.mmu_paint
                settings.loaded_mesh_name = ""  # Force resync
                _sync_filaments_from_mesh(ctx)
    except Exception:
        pass  # Silently ignore context errors during undo/redo/render
