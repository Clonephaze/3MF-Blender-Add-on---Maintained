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
3MF Assemblies sidebar panel — VIEW3D_PT_3mf_assemblies.

Provides a **Create Assembly** button that parents the selected mesh objects
to a new Empty.  The Orca/Bambu exporter writes every Empty with children as
one slicer assembly (a single object made of several parts), so the parts keep
their relative positions.
"""

import bpy
import bpy.types
import mathutils

from ..common.logging import safe_report


def _nearest_empty(obj):
    parent = obj.parent
    while parent is not None:
        if parent.type == "EMPTY":
            return parent
        parent = parent.parent
    return None


def _branch_meshes(obj):
    if obj.type == "EMPTY":
        return []
    meshes = [obj] if obj.type == "MESH" else []
    for child in obj.children:
        meshes.extend(_branch_meshes(child))
    return meshes


def _empty_members(empty):
    from ..export_3mf.grouping import ASSEMBLY_PROPERTY

    return [
        mesh for child in empty.children for mesh in _branch_meshes(child)
        if not (isinstance(mesh.get(ASSEMBLY_PROPERTY), str)
                and mesh.get(ASSEMBLY_PROPERTY).strip())
    ]


def _world_bbox_center(objects) -> mathutils.Vector:
    """Centre of the combined world-space bounding box of *objects*."""
    corners = [
        obj.matrix_world @ mathutils.Vector(corner)
        for obj in objects
        for corner in obj.bound_box
    ]
    low = mathutils.Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    high = mathutils.Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return (low + high) / 2.0


class THREEMF_OT_create_assembly(bpy.types.Operator):
    """Parents the selected objects to a new Empty, so slicers treat them as one assembly"""

    bl_idname = "threemf.create_assembly"
    bl_label = "Create Assembly"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and any(
            obj.type == "MESH" for obj in context.selected_objects
        )

    def execute(self, context):
        from ..export_3mf.grouping import ASSEMBLY_PROPERTY

        selected = list(context.selected_objects)
        meshes = [obj for obj in selected if obj.type == "MESH"]
        skipped = len(selected) - len(meshes)
        if not meshes:
            safe_report(self, {"WARNING"}, "Select at least one mesh object")
            return {"CANCELLED"}

        center = _world_bbox_center(meshes)

        empty = bpy.data.objects.new("Assembly", None)
        empty.empty_display_type = "PLAIN_AXES"
        empty.location = center
        context.collection.objects.link(empty)
        context.view_layer.update()

        # Parent inverse cancels the Empty's translation, so every child keeps
        # exactly its current world transform (even if it had another parent).
        parent_inverse = mathutils.Matrix.Translation(-center)
        for obj in meshes:
            world = obj.matrix_world.copy()
            obj.parent = empty
            obj.matrix_parent_inverse = parent_inverse
            obj.matrix_basis = world
            # A property assembly would override the Empty on export.
            if ASSEMBLY_PROPERTY in obj:
                del obj[ASSEMBLY_PROPERTY]

        context.view_layer.update()

        # Select the Empty too so "selection only" exports include the group.
        empty.select_set(True)
        context.view_layer.objects.active = empty

        message = f"Created '{empty.name}' with {len(meshes)} object(s)"
        if skipped:
            message += f" ({skipped} non-mesh object(s) ignored)"
        safe_report(self, {"INFO"}, message)
        return {"FINISHED"}


class THREEMF_OT_clear_assembly(bpy.types.Operator):
    """Remove selected meshes from assemblies without moving them or deleting Empties"""

    bl_idname = "threemf.clear_assembly"
    bl_label = "Clear Assembly"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            return False
        return any(
            (obj.type == "MESH" and ("3mf_assembly" in obj or _nearest_empty(obj) is not None))
            or (obj.type == "EMPTY" and any(_branch_meshes(child) for child in obj.children))
            for obj in context.selected_objects
        )

    def execute(self, context):
        from ..common.plates import ASSEMBLE_DELTA_PROPERTY, PLATE_NAME_PROPERTY, PLATE_PROPERTY
        from ..export_3mf.grouping import ASSEMBLY_PROPERTY

        members = {}
        detach = {}
        for obj in context.selected_objects:
            if obj.type == "MESH":
                members[obj.name] = obj
                if obj.parent is not None and obj.parent.type == "EMPTY":
                    detach[obj.name] = (obj, obj.parent)
            elif obj.type == "EMPTY":
                for child in obj.children:
                    meshes = _branch_meshes(child)
                    if meshes:
                        detach[child.name] = (child, obj)
                        members.update({mesh.name: mesh for mesh in meshes})

        context.view_layer.update()
        worlds = {name: branch.matrix_world.copy() for name, (branch, _) in detach.items()}
        for branch, empty in detach.values():
            for mesh in _branch_meshes(branch):
                group = mesh.get(ASSEMBLY_PROPERTY)
                if isinstance(group, str) and group.strip():
                    continue
                if PLATE_PROPERTY in empty and PLATE_PROPERTY not in mesh:
                    mesh[PLATE_PROPERTY] = empty[PLATE_PROPERTY]
                    if PLATE_NAME_PROPERTY in empty:
                        mesh[PLATE_NAME_PROPERTY] = empty[PLATE_NAME_PROPERTY]
                elif (
                    PLATE_NAME_PROPERTY in empty
                    and PLATE_PROPERTY not in mesh
                    and PLATE_NAME_PROPERTY not in mesh
                ):
                    mesh[PLATE_NAME_PROPERTY] = empty[PLATE_NAME_PROPERTY]
                if ASSEMBLE_DELTA_PROPERTY in empty and ASSEMBLE_DELTA_PROPERTY not in mesh:
                    mesh[ASSEMBLE_DELTA_PROPERTY] = list(empty[ASSEMBLE_DELTA_PROPERTY])
        for name, (branch, _) in detach.items():
            branch.parent = None
            branch.matrix_world = worlds[name]

        cleared = 0
        for obj in members.values():
            if ASSEMBLY_PROPERTY in obj:
                del obj[ASSEMBLY_PROPERTY]
                cleared += 1
        context.view_layer.update()

        remaining = sum(_nearest_empty(obj) is not None for obj in members.values())
        if remaining:
            safe_report(
                self, {"WARNING"},
                f"{remaining} mesh object(s) remain in an Empty assembly through another parent.",
            )
        elif detach or cleared:
            safe_report(self, {"INFO"}, f"Cleared assembly for {len(members)} mesh object(s)")
        else:
            safe_report(self, {"WARNING"}, "No selected assembly could be cleared")
            return {"CANCELLED"}
        return {"FINISHED"}


def _plate_targets(context, obj):
    """
    Objects that should carry ``3mf_plate`` for the export unit *obj* belongs to.

    Mirrors the exporter's unit rules: an assembly is written as one unit, so
    the property goes on the assembly Empty (which wins over its members) or
    on every member of a ``3mf_assembly`` property group; any other object is
    its own unit.
    """
    from ..export_3mf.grouping import ASSEMBLY_PROPERTY

    if obj.type == "EMPTY":
        return [obj]
    name = obj.get(ASSEMBLY_PROPERTY) if obj.type == "MESH" else None
    if isinstance(name, str) and name.strip():
        value = name.strip()
        return [
            o for o in context.scene.objects
            if o.type == "MESH" and isinstance(o.get(ASSEMBLY_PROPERTY), str)
            and o.get(ASSEMBLY_PROPERTY).strip() == value
        ]
    parent = _nearest_empty(obj)
    if parent is not None:
        return [parent]
    return [obj]


class THREEMF_OT_assign_plate(bpy.types.Operator):
    """Sets the 3mf_plate property on the selected objects (Orca/Bambu plate)"""

    bl_idname = "threemf.assign_plate"
    bl_label = "Assign to Plate"
    bl_options = {"REGISTER", "UNDO"}

    plate: bpy.props.IntProperty(
        name="Plate",
        description="Plate number (1-based)",
        default=1,
        min=1,
        max=36,
    )
    each_on_own_plate: bpy.props.BoolProperty(
        name="Each on Its Own Plate",
        description=(
            "Give every selected object (or assembly) its own plate, "
            "counting up from the plate number"
        ),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and bool(context.selected_objects)

    def invoke(self, context, event):
        active = context.active_object
        current = active.get("3mf_plate") if active is not None else None
        if isinstance(current, int) and current >= 1:
            self.plate = min(current, 36)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        from ..common.plates import MAX_PLATES, PLATE_NAME_PROPERTY, PLATE_PROPERTY

        # Collect unique export units in selection order.
        units = []
        seen = set()
        for obj in context.selected_objects:
            targets = _plate_targets(context, obj)
            key = tuple(sorted(t.name for t in targets))
            if key in seen:
                continue
            seen.add(key)
            units.append(targets)

        clamped = False
        for index, targets in enumerate(units):
            number = self.plate + (index if self.each_on_own_plate else 0)
            if number > MAX_PLATES:
                number = MAX_PLATES
                clamped = True
            for target in targets:
                if target.get(PLATE_PROPERTY, 1) != number and PLATE_NAME_PROPERTY in target:
                    del target[PLATE_NAME_PROPERTY]
                target[PLATE_PROPERTY] = number
            # Members of an Empty assembly must not contradict the Empty.
            for target in targets:
                if target.type == "EMPTY":
                    for child in _empty_members(target):
                        if PLATE_PROPERTY in child:
                            del child[PLATE_PROPERTY]
                        if PLATE_NAME_PROPERTY in child:
                            del child[PLATE_NAME_PROPERTY]

        message = f"Assigned {len(units)} unit(s) to plate(s)"
        if clamped:
            message += f" (limited to plate {MAX_PLATES})"
        safe_report(self, {"WARNING" if clamped else "INFO"}, message)
        return {"FINISHED"}


class THREEMF_OT_clear_plate(bpy.types.Operator):
    """Removes the 3mf_plate property from the selected objects"""

    bl_idname = "threemf.clear_plate"
    bl_label = "Clear Plate"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and any(
            "3mf_plate" in source or "3mf_plate_name" in source
            for obj in context.selected_objects
            for target in _plate_targets(context, obj)
            for source in [target] + (_empty_members(target) if target.type == "EMPTY" else [])
        )

    def execute(self, context):
        cleared = 0
        seen = set()
        for obj in context.selected_objects:
            for target in _plate_targets(context, obj):
                sources = [target] + (_empty_members(target) if target.type == "EMPTY" else [])
                for source in sources:
                    if source.name in seen:
                        continue
                    seen.add(source.name)
                    changed = False
                    for key in ("3mf_plate", "3mf_plate_name"):
                        if key in source:
                            del source[key]
                            changed = True
                    if changed:
                        cleared += 1
        safe_report(self, {"INFO"}, f"Cleared plate on {cleared} object(s)")
        return {"FINISHED"}


class VIEW3D_PT_3mf_assemblies(bpy.types.Panel):
    """3MF Assemblies — group objects into slicer assemblies."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "Assemblies"

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def draw(self, context):
        layout = self.layout
        mesh_count = sum(1 for o in context.selected_objects if o.type == "MESH")

        row = layout.row(align=True)
        row.operator(THREEMF_OT_create_assembly.bl_idname, icon="OUTLINER_OB_EMPTY")
        row.operator(THREEMF_OT_clear_assembly.bl_idname, text="", icon="X")
        layout.label(text=f"{mesh_count} mesh object(s) selected")

        active = context.active_object
        if active is not None and active.parent is not None and active.parent.type == "EMPTY":
            layout.label(text=f"In assembly: {active.parent.name}", icon="GROUP")

        # --- Plates (Orca/Bambu) ---
        layout.separator()
        layout.label(text="Plates", icon="MESH_GRID")
        if active is not None:
            targets = _plate_targets(context, active)
            target = targets[0] if targets else active
            value = target.get("3mf_plate")
            shown = value if isinstance(value, int) and value >= 1 else "none (plate 1)"
            layout.label(text=f"Active: plate {shown}")
        row = layout.row(align=True)
        row.operator(THREEMF_OT_assign_plate.bl_idname, text="Assign Plate…", icon="ADD")
        row.operator(THREEMF_OT_clear_plate.bl_idname, text="", icon="X")

        used = sorted({
            o["3mf_plate"] for o in context.scene.objects
            if isinstance(o.get("3mf_plate"), int)
        })
        if used:
            layout.label(text="In use: " + ", ".join(str(n) for n in used))


_assembly_classes = (
    THREEMF_OT_create_assembly,
    THREEMF_OT_clear_assembly,
    THREEMF_OT_assign_plate,
    THREEMF_OT_clear_plate,
    VIEW3D_PT_3mf_assemblies,
)


def register():
    for cls in _assembly_classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_assembly_classes):
        bpy.utils.unregister_class(cls)
