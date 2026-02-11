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

# <pep8 compliant>

"""
Import context — the "bag" of mutable state threaded through all import functions.

Previously, the monolithic ``Import3MF`` operator accumulated 23+ instance
variables on ``self`` during :meth:`execute`.  ``ImportContext`` replaces that
with an explicit, typed dataclass.  Every import helper takes ``ctx`` as its
first argument instead of ``op``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..common.extensions import ExtensionManager
from ..common.types import (
    ResourceObject,
    ResourceMaterial,
    ResourceTexture,
    ResourceTextureGroup,
    ResourceComposite,
    ResourceMultiproperties,
    ResourcePBRTextureDisplay,
    ResourceColorgroup,
    ResourcePBRDisplayProps,
)


# ---------------------------------------------------------------------------
# Options sub-dataclass — mirrors the Blender operator properties
# ---------------------------------------------------------------------------

@dataclass
class ImportOptions:
    """User-facing import options (operator properties or API keyword args)."""

    global_scale: float = 1.0
    import_materials: str = "MATERIALS"  # "MATERIALS" | "PAINT" | "NONE"
    reuse_materials: bool = True
    import_location: str = "KEEP"  # "ORIGIN" | "CURSOR" | "KEEP" | "GRID"
    origin_to_geometry: str = "KEEP"  # "KEEP" | "CENTER" | "BOTTOM"
    grid_spacing: float = 0.1
    paint_uv_method: str = "SMART"  # "SMART" | "LIGHTMAP"
    paint_texture_size: int = 0  # 0 = auto


# ---------------------------------------------------------------------------
# ImportContext — the state bag
# ---------------------------------------------------------------------------

@dataclass
class ImportContext:
    """All mutable state accumulated during a single 3MF import operation.

    Create one in the operator's ``execute()`` (or in ``api.import_3mf()``),
    pass it to every helper function, and discard it when the import is done.
    """

    # --- User options -------------------------------------------------------
    options: ImportOptions = field(default_factory=ImportOptions)

    # --- Operator reference (for safe_report / progress) --------------------
    operator: object = None  # The Import3MF operator instance, or None for API usage.

    # --- Resource dictionaries (populated while reading XML) ----------------
    resource_objects: Dict[str, ResourceObject] = field(default_factory=dict)
    resource_materials: Dict[str, dict] = field(default_factory=dict)
    resource_to_material: Dict[ResourceMaterial, object] = field(default_factory=dict)
    textured_to_basematerial_map: Dict[ResourceMaterial, ResourceMaterial] = field(default_factory=dict)
    resource_textures: Dict[str, ResourceTexture] = field(default_factory=dict)
    resource_texture_groups: Dict[str, ResourceTextureGroup] = field(default_factory=dict)

    # Round-trip passthrough resources
    resource_composites: Dict[str, ResourceComposite] = field(default_factory=dict)
    resource_multiproperties: Dict[str, ResourceMultiproperties] = field(default_factory=dict)
    resource_pbr_texture_displays: Dict[str, ResourcePBRTextureDisplay] = field(default_factory=dict)
    resource_colorgroups: Dict[str, ResourceColorgroup] = field(default_factory=dict)
    resource_pbr_display_props: Dict[str, ResourcePBRDisplayProps] = field(default_factory=dict)
    object_passthrough_pids: Dict[str, str] = field(default_factory=dict)

    # --- Component instance cache (for linked-duplicate detection) ----------
    component_instance_cache: Dict[str, Tuple[object, int]] = field(default_factory=dict)

    # --- Slicer-specific data -----------------------------------------------
    vendor_format: Optional[str] = None  # "orca" | None
    orca_filament_colors: Dict[int, str] = field(default_factory=dict)
    object_default_extruders: Dict[str, int] = field(default_factory=dict)

    # --- Extension tracking -------------------------------------------------
    extension_manager: ExtensionManager = field(default_factory=ExtensionManager)

    # --- Import progress / result tracking ----------------------------------
    num_loaded: int = 0
    imported_objects: List[object] = field(default_factory=list)  # bpy.types.Object list
    _paint_object_names: List[str] = field(default_factory=list)
    current_archive_path: Optional[str] = None

    # --- Helpers ------------------------------------------------------------

    def safe_report(self, level: Set[str], message: str) -> None:
        """Report a message through the operator if available, or log it."""
        from ..common.logging import safe_report as _safe_report, warn, error, debug

        if self.operator is not None:
            _safe_report(self.operator, level, message)
        else:
            # No operator — log directly
            if "ERROR" in level:
                error(message)
            elif "WARNING" in level:
                warn(message)
            else:
                debug(message)
