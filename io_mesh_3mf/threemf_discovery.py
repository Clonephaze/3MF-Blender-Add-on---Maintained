# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2025 Jack
"""
3MF API Discovery Helper — Copy this into your addon

This module provides utility functions to discover and use the 3MF Import/Export
addon's public API from another Blender addon. Copy this file into your addon
or inline the functions you need.

The 3MF addon registers itself in bpy.app.driver_namespace["io_mesh_3mf"] when
enabled, making it discoverable without parsing addon directories.

Example usage::

    from . import threemf_discovery  # or inline the functions

    def my_operator_execute(self, context):
        api = threemf_discovery.get_threemf_api()
        if api is None:
            self.report({'ERROR'}, "3MF Format addon not installed/enabled")
            return {'CANCELLED'}

        # Import a 3MF file
        result = api.import_3mf("/path/to/model.3mf")
        if result.status == "FINISHED":
            self.report({'INFO'}, f"Imported {result.num_loaded} objects")

        # Export selected objects
        result = api.export_3mf(
            "/path/to/output.3mf",
            use_selection=True,
            use_orca_format="PAINT",
        )

        # Inspect without importing
        info = api.inspect_3mf("/path/to/model.3mf")
        print(info.unit, info.num_objects)

        return {'FINISHED'}
"""

from typing import TYPE_CHECKING, Optional, Tuple

import bpy

if TYPE_CHECKING:
    # Type hints for IDE support — these are only used for static analysis,
    # not at runtime, so no ImportError if 3MF addon isn't installed.
    from io_mesh_3mf import api as ThreeMFAPI
else:
    ThreeMFAPI = None

# Registry key used by the 3MF addon
_REGISTRY_KEY = "io_mesh_3mf"


def is_threemf_available() -> bool:
    """Check if the 3MF addon is installed, enabled, and its API is registered.

    :return: True if the 3MF API is available for use.
    """
    return _REGISTRY_KEY in bpy.app.driver_namespace


def get_threemf_api() -> Optional["ThreeMFAPI"]:
    """Get the 3MF API module if available.

    :return: The io_mesh_3mf.api module, or None if not available.

    Example::

        api = get_threemf_api()
        if api:
            result = api.import_3mf("/model.3mf")
    """
    return bpy.app.driver_namespace.get(_REGISTRY_KEY)


def get_threemf_version() -> Optional[Tuple[int, int, int]]:
    """Get the 3MF API version tuple (major, minor, patch).

    :return: Version tuple like (1, 0, 0), or None if not available.
    """
    api = get_threemf_api()
    if api is not None:
        return getattr(api, "API_VERSION", None)
    return None


def check_threemf_version(minimum: Tuple[int, int, int]) -> bool:
    """Check if the installed 3MF API meets a minimum version requirement.

    :param minimum: Tuple of (major, minor, patch) minimum version.
    :return: True if the API version >= minimum, False otherwise.

    Example::

        if check_threemf_version((1, 2, 0)):
            # Safe to use features added in v1.2.0
            ...
    """
    version = get_threemf_version()
    if version is None:
        return False
    return version >= minimum


def has_threemf_capability(capability: str) -> bool:
    """Check if a specific 3MF API capability is available.

    Use this for forward-compatible feature detection. Capabilities include:
    - "import", "export", "inspect", "batch"
    - "callbacks" (on_progress, on_warning, on_object_created)
    - "target_collection", "orca_format", "prusa_format"
    - "paint_mode", "project_template", "object_settings"
    - "building_blocks" (colors, types, segmentation sub-namespaces)

    :param capability: Capability name string.
    :return: True if the capability is supported.
    """
    api = get_threemf_api()
    if api is None:
        return False
    capabilities = getattr(api, "API_CAPABILITIES", frozenset())
    return capability in capabilities


# ═══════════════════════════════════════════════════════════════════════════
# Convenience wrappers (optional — you can call api.* directly instead)
# ═══════════════════════════════════════════════════════════════════════════

def import_3mf(filepath: str, **kwargs):
    """Import a 3MF file. Returns ImportResult or None if API unavailable.

    See io_mesh_3mf.api.import_3mf for full parameter documentation.
    """
    api = get_threemf_api()
    if api is None:
        return None
    return api.import_3mf(filepath, **kwargs)


def export_3mf(filepath: str, **kwargs):
    """Export to 3MF file. Returns ExportResult or None if API unavailable.

    See io_mesh_3mf.api.export_3mf for full parameter documentation.
    """
    api = get_threemf_api()
    if api is None:
        return None
    return api.export_3mf(filepath, **kwargs)


def inspect_3mf(filepath: str):
    """Inspect a 3MF file without importing. Returns InspectResult or None.

    See io_mesh_3mf.api.inspect_3mf for full parameter documentation.
    """
    api = get_threemf_api()
    if api is None:
        return None
    return api.inspect_3mf(filepath)
