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
Triangle Sets naming panel — VIEW3D_PT_3mf_triangle_sets.

Visible in Sculpt mode when the active mesh has ``.sculpt_face_set``
data.  Lets the user assign human-readable names to face-set IDs so
they can be round-tripped as named 3MF Triangle Sets on export.

Names are stored in the ``3mf_triangle_set_names`` custom property on
the mesh datablock (list of strings, index-aligned with set IDs
starting at 1).
"""

import bpy
import bpy.props
import bpy.types


# ===================================================================
#  Helpers
# ===================================================================


def _get_face_set_info(mesh):
    """Return ``{set_id: face_count}`` for all non-zero face-set IDs.

    Returns an empty dict when no sculpt face sets exist.
    """
    attr = mesh.attributes.get(".sculpt_face_set")
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


def _load_set_names(mesh):
    """Deserialise the JSON name list from the mesh property."""
    import json
    raw = mesh.get("3mf_triangle_set_names", "")
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    return list(raw) if raw else []


def _get_set_name(mesh, set_id):
    """Look up the name for *set_id* (1-based) from the mesh property."""
    names = _load_set_names(mesh)
    idx = set_id - 1
    if 0 <= idx < len(names):
        return str(names[idx])
    return ""


def _set_set_name(mesh, set_id, new_name):
    """Write *new_name* for *set_id* into the mesh property.

    Grows the list if needed, padding with empty strings.
    """
    import json
    names = _load_set_names(mesh)
    idx = set_id - 1
    while len(names) <= idx:
        names.append("")
    names[idx] = new_name
    mesh["3mf_triangle_set_names"] = json.dumps(names)


def _format_count(n):
    """Format an integer with thousands separators."""
    return f"{n:,}"


# ===================================================================
#  Operators
# ===================================================================


class THREEMF_OT_rename_face_set(bpy.types.Operator):
    """Assign a 3MF Triangle Set name to a sculpt face-set ID"""

    bl_idname = "threemf.rename_face_set"
    bl_label = "Name Face Set"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    set_id: bpy.props.IntProperty(
        name="Set ID",
        description="Face-set ID to name",
        min=1,
    )
    name: bpy.props.StringProperty(
        name="Name",
        description="Human-readable name for this face set",
        default="",
    )

    def invoke(self, context, event):
        mesh = context.active_object.data
        self.name = _get_set_name(mesh, self.set_id)
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"Face Set {self.set_id}")
        layout.prop(self, "name", text="Name")

    def execute(self, context):
        mesh = context.active_object.data
        _set_set_name(mesh, self.set_id, self.name.strip())
        self.report({"INFO"}, f"Face Set {self.set_id} → '{self.name.strip()}'")
        return {"FINISHED"}


class THREEMF_OT_clear_face_set_names(bpy.types.Operator):
    """Remove all 3MF Triangle Set names from this mesh"""

    bl_idname = "threemf.clear_face_set_names"
    bl_label = "Clear All Names"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    def execute(self, context):
        mesh = context.active_object.data
        if "3mf_triangle_set_names" in mesh:
            del mesh["3mf_triangle_set_names"]
        self.report({"INFO"}, "Cleared triangle set names")
        return {"FINISHED"}


# ===================================================================
#  Panel
# ===================================================================


class VIEW3D_PT_3mf_triangle_sets(bpy.types.Panel):
    """3MF Triangle Sets — name sculpt face sets for 3MF export."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "Triangle Sets"

    @classmethod
    def poll(cls, context):
        if context.mode != "SCULPT":
            return False
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            return False
        return ".sculpt_face_set" in obj.data.attributes

    def draw(self, context):
        layout = self.layout
        mesh = context.active_object.data
        info = _get_face_set_info(mesh)

        if not info:
            row = layout.row()
            row.enabled = False
            row.label(text="No face sets assigned", icon="INFO")
            return

        col = layout.column(align=True)
        for set_id in sorted(info.keys()):
            face_count = info[set_id]
            name = _get_set_name(mesh, set_id)

            row = col.row(align=True)
            split = row.split(factor=0.55, align=True)

            if name:
                split.label(text=f"{set_id}. {name}")
            else:
                lbl = split.row()
                lbl.alert = True
                lbl.label(text=f"{set_id}. (unnamed)")

            right = split.row(align=True)
            right.alignment = "RIGHT"
            right.label(text=f"{_format_count(face_count)} faces")

            op = right.operator(
                "threemf.rename_face_set", text="", icon="GREASEPENCIL",
            )
            op.set_id = set_id

        layout.separator()

        # Info note.
        tip = layout.column(align=True)
        tip.scale_y = 0.7
        tip.label(text="Named face sets export as", icon="INFO")
        tip.label(text="3MF Triangle Sets.")

        layout.separator()
        layout.operator("threemf.clear_face_set_names", icon="X")


# ===================================================================
#  Registration
# ===================================================================

_triangle_set_classes = (
    THREEMF_OT_rename_face_set,
    THREEMF_OT_clear_face_set_names,
    VIEW3D_PT_3mf_triangle_sets,
)


def register():
    for cls in _triangle_set_classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_triangle_set_classes):
        bpy.utils.unregister_class(cls)
