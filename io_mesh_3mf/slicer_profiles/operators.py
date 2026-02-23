# Blender add-on to import and export 3MF files.
# Copyright (C) 2025 Jack
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# <pep8 compliant>

"""Blender operators for managing slicer profiles."""

from __future__ import annotations

import os

import bpy

from .storage import (
    extract_from_3mf,
    save_profile,
    delete_profile,
    rename_profile,
)


class THREEMF_OT_load_slicer_profile(bpy.types.Operator):
    """Extract slicer settings from a 3MF file and save as a named profile"""

    bl_idname = "threemf.load_slicer_profile"
    bl_label = "Load Profile from 3MF"
    bl_options = {'INTERNAL'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default="*.3mf", options={'HIDDEN'})

    profile_name: bpy.props.StringProperty(
        name="Profile Name",
        description=(
            "Display name for this slicer profile. "
            "Leave blank to derive from the filename"
        ),
        default="",
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        self.layout.prop(self, "profile_name")

    def execute(self, context):
        import zipfile as _zf

        filepath = self.filepath
        if not filepath or not os.path.isfile(filepath):
            self.report({'ERROR'}, "File not found")
            return {'CANCELLED'}

        try:
            vendor, machine, configs, labels = extract_from_3mf(filepath)
        except _zf.BadZipFile:
            self.report({'ERROR'}, "Not a valid 3MF archive")
            return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to read file: {e}")
            return {'CANCELLED'}

        if not configs:
            self.report(
                {'WARNING'}, "No slicer config files found in this 3MF",
            )
            return {'CANCELLED'}

        name = self.profile_name.strip()
        if not name:
            name = os.path.splitext(os.path.basename(filepath))[0]

        source_file = os.path.basename(filepath)
        save_profile(
            name, vendor or "Unknown", source_file, configs,
            machine=machine,
        )

        count = len(configs)
        self.report({'INFO'}, f"Saved profile \"{name}\" ({count} config(s))")

        # Reset for next use
        self.profile_name = ""
        return {'FINISHED'}


class THREEMF_OT_delete_slicer_profile(bpy.types.Operator):
    """Delete a saved slicer profile"""

    bl_idname = "threemf.delete_slicer_profile"
    bl_label = "Delete Profile"
    bl_options = {'INTERNAL'}

    profile_name: bpy.props.StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if delete_profile(self.profile_name):
            self.report({'INFO'}, f"Deleted profile \"{self.profile_name}\"")
        else:
            self.report(
                {'WARNING'},
                f"Profile \"{self.profile_name}\" not found",
            )
        return {'FINISHED'}


class THREEMF_OT_rename_slicer_profile(bpy.types.Operator):
    """Rename a saved slicer profile"""

    bl_idname = "threemf.rename_slicer_profile"
    bl_label = "Rename Profile"
    bl_options = {'INTERNAL'}

    old_name: bpy.props.StringProperty(options={'HIDDEN'})
    new_name: bpy.props.StringProperty(name="New Name")

    def invoke(self, context, event):
        self.new_name = self.old_name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        new = self.new_name.strip()
        if not new:
            self.report({'WARNING'}, "Name cannot be empty")
            return {'CANCELLED'}
        if new == self.old_name:
            return {'FINISHED'}
        if rename_profile(self.old_name, new):
            self.report({'INFO'}, f"Renamed to \"{new}\"")
        else:
            self.report({'WARNING'}, "Failed to rename profile")
        return {'FINISHED'}
