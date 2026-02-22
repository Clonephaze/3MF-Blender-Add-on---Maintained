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
3MF Metadata sidebar panel — VIEW3D_PT_3mf_metadata.

Displays and allows editing of 3MF metadata stored on the active scene
and active object.  Visible in the 3D Viewport sidebar ("3MF" tab) in
Object mode — no ``bl_context`` restriction so it also appears in other
non-paint modes.

Scene-level metadata (Title, Designer, Description, Copyright, etc.) is
editable.  Slicer-injected fields (Application, CreationDate) are shown
read-only.  Per-object metadata (partnumber) is shown when an object is
selected.

Stashed slicer configs (from import round-trip) are indicated with an
info icon.
"""

import bpy
import bpy.props
import bpy.types
import idprop.types


# ===================================================================
#  Constants
# ===================================================================

# Standard 3MF metadata keys in display order.
# Editable keys come first, followed by read-only slicer/system keys.
_EDITABLE_KEYS = ("Title", "Designer", "Description", "Copyright", "LicenseTerms")

_READONLY_KEYS = ("Application", "CreationDate", "ModificationDate")

# Config stash indicator prefix (matches import_3mf/archive.py)
_CONFIG_STASH_PREFIX = ".3mf_config/"


# ===================================================================
#  Operators
# ===================================================================


class THREEMF_OT_edit_metadata(bpy.types.Operator):
    """Edit a 3MF metadata field on the active scene"""

    bl_idname = "threemf.edit_metadata"
    bl_label = "Edit 3MF Metadata"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    key: bpy.props.StringProperty(
        name="Key",
        description="Metadata key to edit",
    )
    value: bpy.props.StringProperty(
        name="Value",
        description="New value for the metadata field",
    )

    def invoke(self, context, event):
        # Pre-fill value from existing metadata
        scene = context.scene
        entry = scene.get(self.key)
        if self.key == "Title":
            self.value = scene.name
        elif isinstance(entry, idprop.types.IDPropertyGroup):
            self.value = str(entry.get("value", ""))
        elif isinstance(entry, str):
            self.value = entry
        else:
            self.value = ""
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Editing: {self.key}")
        if self.key == "Description":
            layout.prop(self, "value", text="")
        else:
            layout.prop(self, "value", text="")

    def execute(self, context):
        scene = context.scene
        value = self.value.strip()

        if self.key == "Title":
            scene.name = value
            self.report({"INFO"}, f"Set scene name to '{value}'")
            return {"FINISHED"}

        # Store as a proper metadata entry
        scene[self.key] = {
            "datatype": "xs:string",
            "preserve": True,
            "value": value,
        }
        self.report({"INFO"}, f"Set {self.key} = '{value}'")
        return {"FINISHED"}


class THREEMF_OT_add_metadata(bpy.types.Operator):
    """Add a new custom 3MF metadata field"""

    bl_idname = "threemf.add_metadata"
    bl_label = "Add Metadata Field"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    key: bpy.props.StringProperty(
        name="Key",
        description="Metadata key name",
        default="",
    )
    value: bpy.props.StringProperty(
        name="Value",
        description="Metadata value",
        default="",
    )

    def invoke(self, context, event):
        self.key = ""
        self.value = ""
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "key")
        layout.prop(self, "value")

    def execute(self, context):
        key = self.key.strip()
        value = self.value.strip()

        if not key:
            self.report({"ERROR"}, "Key cannot be empty")
            return {"CANCELLED"}

        if key in _READONLY_KEYS:
            self.report({"ERROR"}, f"'{key}' is a read-only system field")
            return {"CANCELLED"}

        scene = context.scene
        scene[key] = {
            "datatype": "xs:string",
            "preserve": True,
            "value": value,
        }
        self.report({"INFO"}, f"Added metadata: {key} = '{value}'")
        return {"FINISHED"}


class THREEMF_OT_remove_metadata(bpy.types.Operator):
    """Remove a custom 3MF metadata field from the scene"""

    bl_idname = "threemf.remove_metadata"
    bl_label = "Remove Metadata"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    key: bpy.props.StringProperty(
        name="Key",
        description="Metadata key to remove",
    )

    def execute(self, context):
        scene = context.scene
        if self.key in scene.keys():
            del scene[self.key]
            self.report({"INFO"}, f"Removed metadata: {self.key}")
        return {"FINISHED"}


# ===================================================================
#  Panel
# ===================================================================


def _get_metadata_value(owner, key):
    """Read a metadata value from a Blender ID (scene/object).

    Returns ``(value_str, is_metadata_entry)`` or ``(None, False)`` if the
    key doesn't hold 3MF metadata.
    """
    if key == "Title":
        return owner.name, True

    entry = owner.get(key)
    if isinstance(entry, idprop.types.IDPropertyGroup):
        if "value" in entry.keys() and "datatype" in entry.keys():
            return str(entry.get("value", "")), True
    return None, False


def _has_stashed_configs():
    """Return True if any slicer config text blocks are stashed."""
    for text in bpy.data.texts:
        if text.name.startswith(_CONFIG_STASH_PREFIX):
            return True
    return False


class VIEW3D_PT_3mf_metadata(bpy.types.Panel):
    """3MF Metadata — view and edit scene/object metadata for 3MF export."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "Metadata"

    @classmethod
    def poll(cls, context):
        # Show in all modes except Texture Paint (where MMU Paint panel takes over)
        if context.mode == "PAINT_TEXTURE":
            return False
        return True

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.active_object

        # ============================
        #  Scene Metadata
        # ============================
        scene_box = layout.box()
        header = scene_box.row()
        header.label(text="Scene", icon="SCENE_DATA")

        # Editable fields
        for key in _EDITABLE_KEYS:
            value, is_meta = _get_metadata_value(scene, key)
            row = scene_box.row(align=True)
            row.alignment = "EXPAND"

            if value is not None:
                # Key label + value
                split = row.split(factor=0.35, align=True)
                split.label(text=f"{key}:")
                val_row = split.row(align=True)
                val_row.label(text=value if value else "(empty)")
                # Edit button
                op = val_row.operator("threemf.edit_metadata", text="", icon="GREASEPENCIL")
                op.key = key
            else:
                # Key not set — show as addable
                split = row.split(factor=0.35, align=True)
                lbl = split.row()
                lbl.enabled = False
                lbl.label(text=f"{key}:")
                val_row = split.row(align=True)
                val_row.label(text="—")
                op = val_row.operator("threemf.edit_metadata", text="", icon="ADD")
                op.key = key

        # Read-only system fields
        has_readonly = False
        for key in _READONLY_KEYS:
            value, is_meta = _get_metadata_value(scene, key)
            if value is not None:
                if not has_readonly:
                    scene_box.separator()
                    has_readonly = True
                row = scene_box.row(align=True)
                split = row.split(factor=0.35, align=True)
                split.label(text=f"{key}:")
                val_row = split.row()
                val_row.enabled = False
                val_row.label(text=value)

        # Custom metadata entries (anything not in standard keys)
        standard_keys = set(_EDITABLE_KEYS) | set(_READONLY_KEYS) | {"Title"}
        custom_keys = []
        for key in scene.keys():
            if key in standard_keys:
                continue
            if key.startswith("_") or key.startswith("3mf_"):
                continue  # Internal properties
            value, is_meta = _get_metadata_value(scene, key)
            if is_meta:
                custom_keys.append((key, value))

        if custom_keys:
            scene_box.separator()
            for key, value in custom_keys:
                row = scene_box.row(align=True)
                split = row.split(factor=0.35, align=True)
                split.label(text=f"{key}:")
                val_row = split.row(align=True)
                val_row.label(text=value if value else "(empty)")
                op = val_row.operator("threemf.edit_metadata", text="", icon="GREASEPENCIL")
                op.key = key
                op = val_row.operator("threemf.remove_metadata", text="", icon="X")
                op.key = key

        # Add custom field button
        scene_box.separator()
        scene_box.operator("threemf.add_metadata", icon="ADD")

        # ============================
        #  Object Metadata
        # ============================
        if obj is not None:
            obj_box = layout.box()
            obj_header = obj_box.row()
            obj_header.label(text="Object", icon="OBJECT_DATA")
            obj_header.label(text=obj.name)

            # Part number (common per-object 3MF metadata)
            partnumber = obj.get("3mf:partnumber")
            if partnumber is not None:
                row = obj_box.row(align=True)
                split = row.split(factor=0.35, align=True)
                split.label(text="Part Number:")
                split.label(text=str(partnumber))

            # Any other metadata entries on the object
            obj_meta_count = 0
            for key in obj.keys():
                if key == "3mf:partnumber":
                    continue
                if key.startswith("_"):
                    continue
                value, is_meta = _get_metadata_value(obj, key)
                if is_meta:
                    obj_meta_count += 1
                    row = obj_box.row(align=True)
                    split = row.split(factor=0.35, align=True)
                    split.label(text=f"{key}:")
                    val_row = split.row()
                    val_row.enabled = False
                    val_row.label(text=value if value else "(empty)")

            if partnumber is None and obj_meta_count == 0:
                row = obj_box.row()
                row.enabled = False
                row.label(text="No object metadata", icon="INFO")

        # ============================
        #  Stashed Config Indicator
        # ============================
        if _has_stashed_configs():
            config_box = layout.box()
            row = config_box.row()
            row.label(text="Slicer Config", icon="FILE_TICK")
            info = config_box.column(align=True)
            info.scale_y = 0.7
            info.label(text="Slicer project settings are stashed")
            info.label(text="and will be included on export.")


# ===================================================================
#  Registration
# ===================================================================

_metadata_classes = (
    THREEMF_OT_edit_metadata,
    THREEMF_OT_add_metadata,
    THREEMF_OT_remove_metadata,
    VIEW3D_PT_3mf_metadata,
)


def register():
    for cls in _metadata_classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_metadata_classes):
        bpy.utils.unregister_class(cls)
