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
3MF Metadata sidebar panel — VIEW3D_PT_3mf_metadata.

Displays and allows editing of 3MF metadata stored on the active scene
and active object.  Organised into collapsible sub-panels using
``UILayout.panel()`` (Blender 4.1+):

- **Scene Metadata** — editable Title/Designer/etc., read-only dates,
  custom entries.
- **Object Info** — vertex/face counts, bounding-box dimensions (mesh
  objects only).
- **Object Metadata** — 3MF partnumber and custom per-object entries.
- **MMU Paint** — paint texture status, filament colours (when imported
  as PAINT or initialised via MMU Paint Suite).
- **Slicer Info** — detected source slicer, stashed project configs.
- **Materials** — material-slot summary.
- **Triangle Sets** — named triangle-set membership counts.

Visible in the 3D Viewport sidebar ("3MF" tab) — hidden during Texture
Paint mode where the MMU Paint panel takes over.
"""

import ast

import bpy
import bpy.props
import bpy.types
import idprop.types


# ===================================================================
#  Constants
# ===================================================================

# Editable 3MF metadata keys shown in Scene Metadata section.
_EDITABLE_KEYS = ("Title", "Designer", "Description", "Copyright", "LicenseTerms")

# Read-only date metadata shown in Scene Metadata section.
_SCENE_READONLY_KEYS = ("CreationDate", "ModificationDate")

# Metadata keys displayed in the Slicer Info section instead.
_SLICER_META_KEYS = {"Application", "BambuStudio:3mfVersion"}

# Union of all standard/special keys — used to filter custom entries.
_STANDARD_KEYS = (
    set(_EDITABLE_KEYS) | set(_SCENE_READONLY_KEYS) | _SLICER_META_KEYS | {"Title"}
)

# Read-only keys checked by the add-metadata operator.
_READONLY_KEYS = set(_SCENE_READONLY_KEYS) | _SLICER_META_KEYS

# Config stash indicator prefix (matches import_3mf/archive.py).
_CONFIG_STASH_PREFIX = ".3mf_config/"


# ===================================================================
#  Helpers
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


def _get_stashed_configs():
    """Return a list of stashed slicer config text-block names."""
    return [t.name for t in bpy.data.texts if t.name.startswith(_CONFIG_STASH_PREFIX)]


def _detect_vendor_from_scene(scene):
    """Infer slicer vendor from stored scene metadata.

    Returns a human-readable slicer name or ``None``.
    """
    app_value, _ = _get_metadata_value(scene, "Application")
    if app_value:
        app_lower = app_value.lower()
        if "bambu" in app_lower or "orca" in app_lower:
            return "Orca / BambuStudio"
        if "prusa" in app_lower or "slic3r" in app_lower:
            return "PrusaSlicer"
        if "cura" in app_lower:
            return "Cura"
        return app_value  # Unknown slicer — show raw value.

    bambu_ver, _ = _get_metadata_value(scene, "BambuStudio:3mfVersion")
    if bambu_ver is not None:
        return "Orca / BambuStudio"
    return None


def _parse_paint_colors(mesh):
    """Parse ``3mf_paint_extruder_colors`` from *mesh*.

    Returns ``{index: "#RRGGBB"}`` or empty dict.
    """
    raw = mesh.get("3mf_paint_extruder_colors", "")
    if not raw:
        return {}
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return {}


def _get_triangle_set_counts(mesh):
    """Return ``{set_index: face_count}`` from the triangle-set attribute."""
    attr = mesh.attributes.get("3mf_triangle_set")
    if attr is None:
        return {}
    num_faces = len(mesh.polygons)
    if num_faces == 0:
        return {}
    values = [0] * num_faces
    attr.data.foreach_get("value", values)
    counts = {}
    for v in values:
        if v > 0:
            counts[v] = counts.get(v, 0) + 1
    return counts


def _has_object_metadata(obj):
    """Return ``True`` if *obj* has any 3MF metadata worth displaying."""
    if obj.get("3mf:partnumber") is not None:
        return True
    for key in obj.keys():
        if key.startswith("_"):
            continue
        _, is_meta = _get_metadata_value(obj, key)
        if is_meta:
            return True
    return False


def _format_count(n):
    """Format an integer with thousands separators."""
    return f"{n:,}"


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


class VIEW3D_PT_3mf_metadata(bpy.types.Panel):
    """3MF Metadata — view and edit scene/object info for 3MF workflows."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "Metadata"

    @classmethod
    def poll(cls, context):
        # Hidden in Texture Paint — the MMU Paint panel takes over there.
        return context.mode != "PAINT_TEXTURE"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.active_object
        mesh = obj.data if (obj and obj.type == "MESH") else None

        # 1) Scene Metadata — always visible.
        self._draw_scene_metadata(layout, scene)

        # 2) Object Info — mesh objects only.
        if mesh is not None:
            self._draw_object_info(layout, obj, mesh)

        # 3) Object Metadata — when the object carries 3MF metadata.
        if obj is not None and _has_object_metadata(obj):
            self._draw_object_metadata(layout, obj)

        # 4) MMU Paint — when paint-texture data exists on the mesh.
        if mesh is not None and mesh.get("3mf_is_paint_texture"):
            self._draw_mmu_paint(layout, mesh)

        # 5) Slicer Info — when slicer data or stashed configs detected.
        vendor = _detect_vendor_from_scene(scene)
        stashed = _get_stashed_configs()
        if vendor or stashed:
            self._draw_slicer_info(layout, scene, vendor, stashed)

        # 6) Materials — when the object has material slots.
        if obj is not None and len(obj.material_slots) > 0:
            self._draw_materials(layout, obj)

        # 7) Triangle Sets — when set data exists on the mesh.
        if mesh is not None and mesh.get("3mf_triangle_set_names"):
            self._draw_triangle_sets(layout, mesh)

    # ---------------------------------------------------------------
    #  Section: Scene Metadata
    # ---------------------------------------------------------------

    def _draw_scene_metadata(self, layout, scene):
        header, body = layout.panel(
            "THREEMF_PT_meta_scene", default_closed=False,
        )
        header.label(text="Scene Metadata", icon="SCENE_DATA")
        if body is None:
            return

        # Editable fields.
        for key in _EDITABLE_KEYS:
            value, _ = _get_metadata_value(scene, key)
            row = body.row(align=True)
            split = row.split(factor=0.35, align=True)

            if value is not None:
                split.label(text=f"{key}:")
                val_row = split.row(align=True)
                val_row.label(text=value if value else "(empty)")
                op = val_row.operator(
                    "threemf.edit_metadata", text="", icon="GREASEPENCIL",
                )
                op.key = key
            else:
                lbl = split.row()
                lbl.enabled = False
                lbl.label(text=f"{key}:")
                val_row = split.row(align=True)
                val_row.label(text="—")
                op = val_row.operator(
                    "threemf.edit_metadata", text="", icon="ADD",
                )
                op.key = key

        # Read-only date fields.
        has_readonly = False
        for key in _SCENE_READONLY_KEYS:
            value, _ = _get_metadata_value(scene, key)
            if value is not None:
                if not has_readonly:
                    body.separator()
                    has_readonly = True
                row = body.row(align=True)
                split = row.split(factor=0.35, align=True)
                split.label(text=f"{key}:")
                val_row = split.row()
                val_row.enabled = False
                val_row.label(text=value)

        # Custom metadata entries (not standard, not slicer, not internal).
        custom_keys = []
        for key in scene.keys():
            if key in _STANDARD_KEYS or key.startswith("_") or key.startswith("3mf_"):
                continue
            value, is_meta = _get_metadata_value(scene, key)
            if is_meta:
                custom_keys.append((key, value))

        if custom_keys:
            body.separator()
            for key, value in custom_keys:
                row = body.row(align=True)
                split = row.split(factor=0.35, align=True)
                split.label(text=f"{key}:")
                val_row = split.row(align=True)
                val_row.label(text=value if value else "(empty)")
                op = val_row.operator(
                    "threemf.edit_metadata", text="", icon="GREASEPENCIL",
                )
                op.key = key
                op = val_row.operator(
                    "threemf.remove_metadata", text="", icon="X",
                )
                op.key = key

        body.separator()
        body.operator("threemf.add_metadata", icon="ADD")

    # ---------------------------------------------------------------
    #  Section: Object Info
    # ---------------------------------------------------------------

    def _draw_object_info(self, layout, obj, mesh):
        header, body = layout.panel(
            "THREEMF_PT_meta_obj_info", default_closed=False,
        )
        header.label(text="Object Info", icon="MESH_DATA")
        if body is None:
            return

        col = body.column(align=True)

        row = col.row(align=True)
        split = row.split(factor=0.35, align=True)
        split.label(text="Name:")
        split.label(text=obj.name)

        row = col.row(align=True)
        split = row.split(factor=0.35, align=True)
        split.label(text="Vertices:")
        split.label(text=_format_count(len(mesh.vertices)))

        row = col.row(align=True)
        split = row.split(factor=0.35, align=True)
        split.label(text="Faces:")
        split.label(text=_format_count(len(mesh.polygons)))

        dims = obj.dimensions
        row = col.row(align=True)
        split = row.split(factor=0.35, align=True)
        split.label(text="Dimensions:")
        split.label(text=f"{dims.x:.4g} \u00d7 {dims.y:.4g} \u00d7 {dims.z:.4g}")

    # ---------------------------------------------------------------
    #  Section: Object Metadata
    # ---------------------------------------------------------------

    def _draw_object_metadata(self, layout, obj):
        header, body = layout.panel(
            "THREEMF_PT_meta_obj_meta", default_closed=False,
        )
        header.label(text="Object Metadata", icon="OBJECT_DATA")
        if body is None:
            return

        col = body.column(align=True)

        partnumber = obj.get("3mf:partnumber")
        if partnumber is not None:
            row = col.row(align=True)
            split = row.split(factor=0.35, align=True)
            split.label(text="Part Number:")
            split.label(text=str(partnumber))

        for key in obj.keys():
            if key == "3mf:partnumber" or key.startswith("_"):
                continue
            value, is_meta = _get_metadata_value(obj, key)
            if is_meta:
                row = col.row(align=True)
                split = row.split(factor=0.35, align=True)
                split.label(text=f"{key}:")
                val_row = split.row()
                val_row.enabled = False
                val_row.label(text=value if value else "(empty)")

    # ---------------------------------------------------------------
    #  Section: MMU Paint
    # ---------------------------------------------------------------

    def _draw_mmu_paint(self, layout, mesh):
        header, body = layout.panel(
            "THREEMF_PT_meta_mmu_paint", default_closed=True,
        )
        header.label(text="MMU Paint", icon="BRUSH_DATA")
        if body is None:
            return

        col = body.column(align=True)

        row = col.row(align=True)
        split = row.split(factor=0.35, align=True)
        split.label(text="Status:")
        split.label(text="Active", icon="CHECKMARK")

        default_ext = mesh.get("3mf_paint_default_extruder", 0)
        if default_ext:
            row = col.row(align=True)
            split = row.split(factor=0.35, align=True)
            split.label(text="Default Extruder:")
            split.label(text=str(default_ext))

        colors = _parse_paint_colors(mesh)
        if colors:
            row = col.row(align=True)
            split = row.split(factor=0.35, align=True)
            split.label(text="Filaments:")
            split.label(text=str(len(colors)))

            col.separator()
            for idx in sorted(colors.keys()):
                row = col.row(align=True)
                split = row.split(factor=0.35, align=True)
                split.label(text=f"  Filament {idx}:")
                split.label(text=colors[idx])

    # ---------------------------------------------------------------
    #  Section: Slicer Info
    # ---------------------------------------------------------------

    def _draw_slicer_info(self, layout, scene, vendor, stashed):
        header, body = layout.panel(
            "THREEMF_PT_meta_slicer", default_closed=True,
        )
        header.label(text="Slicer Info", icon="FILE_TICK")
        if body is None:
            return

        col = body.column(align=True)

        if vendor:
            row = col.row(align=True)
            split = row.split(factor=0.35, align=True)
            split.label(text="Source:")
            split.label(text=vendor)

        app_value, _ = _get_metadata_value(scene, "Application")
        if app_value:
            row = col.row(align=True)
            split = row.split(factor=0.35, align=True)
            split.label(text="Application:")
            val_row = split.row()
            val_row.enabled = False
            val_row.label(text=app_value)

        bambu_ver, _ = _get_metadata_value(scene, "BambuStudio:3mfVersion")
        if bambu_ver is not None:
            row = col.row(align=True)
            split = row.split(factor=0.35, align=True)
            split.label(text="3MF Version:")
            val_row = split.row()
            val_row.enabled = False
            val_row.label(text=bambu_ver)

        if stashed:
            col.separator()
            row = col.row(align=True)
            split = row.split(factor=0.35, align=True)
            split.label(text="Stashed Configs:")
            count = len(stashed)
            split.label(text=f"{count} file{'s' if count != 1 else ''}")

            info = col.column(align=True)
            info.scale_y = 0.7
            for name in stashed:
                display = name[len(_CONFIG_STASH_PREFIX):]
                info.label(text=f"  {display}", icon="DOT")

    # ---------------------------------------------------------------
    #  Section: Materials
    # ---------------------------------------------------------------

    def _draw_materials(self, layout, obj):
        header, body = layout.panel(
            "THREEMF_PT_meta_materials", default_closed=True,
        )
        count = len(obj.material_slots)
        header.label(text=f"Materials ({count})", icon="MATERIAL")
        if body is None:
            return

        col = body.column(align=True)
        for i, slot in enumerate(obj.material_slots):
            row = col.row(align=True)
            if slot.material:
                row.label(text=f"{i + 1}. {slot.material.name}")
            else:
                row.label(text=f"{i + 1}. (empty slot)")

    # ---------------------------------------------------------------
    #  Section: Triangle Sets
    # ---------------------------------------------------------------

    def _draw_triangle_sets(self, layout, mesh):
        import json
        raw_names = mesh.get("3mf_triangle_set_names", "")
        if isinstance(raw_names, str) and raw_names:
            try:
                set_names = json.loads(raw_names)
            except (json.JSONDecodeError, ValueError):
                set_names = []
        else:
            set_names = list(raw_names) if raw_names else []
        if not set_names:
            return

        header, body = layout.panel(
            "THREEMF_PT_meta_trisets", default_closed=True,
        )
        header.label(text=f"Triangle Sets ({len(set_names)})", icon="GROUP_VERTEX")
        if body is None:
            return

        counts = _get_triangle_set_counts(mesh)
        col = body.column(align=True)
        for i, name in enumerate(set_names, start=1):
            face_count = counts.get(i, 0)
            row = col.row(align=True)
            split = row.split(factor=0.6, align=True)
            split.label(text=f"{i}. {name}")
            sub = split.row()
            sub.alignment = "RIGHT"
            sub.label(text=f"{_format_count(face_count)} faces")


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
