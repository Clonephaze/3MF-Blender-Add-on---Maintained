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

"""
Slicer profile management — file-based storage for reusable slicer configs.

Profiles are extracted from 3MF archives and stored as JSON files in
Blender's user config directory (``<config>/3mf_slicer_profiles/``).
Each profile holds Base85-encoded slicer config files that can be
embedded into exported 3MF archives as fallback settings.
"""

from .storage import (
    get_profiles_dir,
    list_profiles,
    load_profile,
    save_profile,
    delete_profile,
    rename_profile,
    get_profile_config,
    extract_from_3mf,
    ProfileInfo,
)

from .operators import (
    THREEMF_OT_load_slicer_profile,
    THREEMF_OT_delete_slicer_profile,
    THREEMF_OT_rename_slicer_profile,
)

__all__ = [
    "get_profiles_dir",
    "list_profiles",
    "load_profile",
    "save_profile",
    "delete_profile",
    "rename_profile",
    "get_profile_config",
    "extract_from_3mf",
    "ProfileInfo",
    "THREEMF_OT_load_slicer_profile",
    "THREEMF_OT_delete_slicer_profile",
    "THREEMF_OT_rename_slicer_profile",
]
