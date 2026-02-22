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
Panels package — non-paint sidebar panels for the 3MF tab.

Each module provides one or more ``bpy.types.Panel`` subclasses that appear
in the 3D Viewport sidebar under the **3MF** tab.  These are independent of
the MMU paint subsystem and show in different Blender modes:

- ``metadata`` — Object-mode panel for viewing/editing 3MF metadata
- (future) ``triangle_sets`` — Edit-mode panel for triangle set assignment/viewing
"""

from . import metadata  # noqa: F401


def register():
    metadata.register()


def unregister():
    metadata.unregister()
