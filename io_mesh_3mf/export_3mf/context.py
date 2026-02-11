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
Export context — the "bag" of mutable state threaded through all export functions.

Previously, the ``Export3MF`` operator accumulated many instance variables on ``self``
during :meth:`execute`, and the format-specific exporters accessed them via ``self.op``.
``ExportContext`` replaces that with an explicit, typed dataclass.  Every export helper
takes ``ctx`` as its first argument instead of ``op``.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from ..common.extensions import ExtensionManager
from ..common.logging import debug, warn, error


# ---------------------------------------------------------------------------
# Options sub-dataclass — mirrors the Blender operator properties
# ---------------------------------------------------------------------------

@dataclass
class ExportOptions:
    """User-facing export options (operator properties or API keyword args)."""

    use_selection: bool = False
    export_hidden: bool = False
    global_scale: float = 1.0
    use_mesh_modifiers: bool = True
    coordinate_precision: int = 9
    use_orca_format: str = "BASEMATERIAL"  # "STANDARD" | "BASEMATERIAL" | "PAINT"
    use_components: bool = True
    mmu_slicer_format: str = "ORCA"  # "ORCA" | "PRUSA"
    subdivision_depth: int = 7  # 4-10, controls paint segmentation detail


# ---------------------------------------------------------------------------
# ExportContext — the state bag
# ---------------------------------------------------------------------------

@dataclass
class ExportContext:
    """All mutable state accumulated during a single 3MF export operation.

    Create one in the operator's ``execute()`` (or in ``api.export_3mf()``),
    pass it to every helper function, and discard it when the export is done.
    """

    # --- User options -------------------------------------------------------
    options: ExportOptions = field(default_factory=ExportOptions)

    # --- Operator reference (for safe_report / progress) --------------------
    operator: object = None  # The Export3MF operator instance, or None for API usage.

    # --- Archive + filepath -------------------------------------------------
    filepath: str = ""

    # --- Resource tracking (populated during export) ------------------------
    next_resource_id: int = 1
    material_resource_id: str = "-1"
    num_written: int = 0

    # --- Color / material mappings ------------------------------------------
    vertex_colors: Dict[str, int] = field(default_factory=dict)
    material_name_to_index: Dict[str, int] = field(default_factory=dict)
    passthrough_id_remap: Dict[str, str] = field(default_factory=dict)

    # --- Texture and PBR tracking -------------------------------------------
    texture_groups: Dict = field(default_factory=dict)
    pbr_material_names: Set[str] = field(default_factory=set)

    # --- Orca-specific ------------------------------------------------------
    orca_object_files: List = field(default_factory=list)

    # --- Extension tracking -------------------------------------------------
    extension_manager: ExtensionManager = field(default_factory=ExtensionManager)

    # --- Progress state -----------------------------------------------------
    _progress_context: object = None
    _progress_value: int = 0
    _progress_range: Optional[Tuple[int, int]] = None

    # --- Helpers ------------------------------------------------------------

    def safe_report(self, level: Set[str], message: str) -> None:
        """Report a message through the operator if available, or log it."""
        from ..common.logging import safe_report as _safe_report

        if self.operator is not None:
            _safe_report(self.operator, level, message)
        else:
            if "ERROR" in level:
                error(message)
            elif "WARNING" in level:
                warn(message)
            else:
                debug(message)

    def _progress_begin(self, context, message: str) -> None:
        """Begin progress tracking via Blender's window manager."""
        self._progress_context = context
        self._progress_value = 0
        window_manager = getattr(context, "window_manager", None)
        if window_manager:
            if hasattr(window_manager, "progress_begin"):
                window_manager.progress_begin(0, 100)
            if hasattr(window_manager, "status_text_set"):
                window_manager.status_text_set(message)

    def _progress_update(self, value: int, message: Optional[str] = None) -> None:
        """Update progress bar (monotonically increasing)."""
        context = self._progress_context
        if not context:
            return
        new_value = max(self._progress_value, value)
        self._progress_value = new_value
        window_manager = getattr(context, "window_manager", None)
        if window_manager and hasattr(window_manager, "progress_update"):
            window_manager.progress_update(new_value)
        if message and window_manager and hasattr(window_manager, "status_text_set"):
            window_manager.status_text_set(message)

    def _progress_end(self) -> None:
        """End progress tracking."""
        context = self._progress_context
        if not context:
            return
        window_manager = getattr(context, "window_manager", None)
        if window_manager:
            if hasattr(window_manager, "progress_end"):
                window_manager.progress_end()
            if hasattr(window_manager, "status_text_set"):
                window_manager.status_text_set(None)
        self._progress_context = None

    def finalize_export(self, archive: zipfile.ZipFile, format_name: str = "") -> Set[str]:
        """
        Finalize an export by closing the archive and reporting results.

        :param archive: The 3MF archive to close.
        :param format_name: Optional format suffix for log message
                            (e.g., "Orca-compatible ", "PrusaSlicer-compatible ").
        :return: {"FINISHED"} on success, {"CANCELLED"} on failure.
        """
        try:
            archive.close()
        except EnvironmentError as e:
            error(f"Unable to complete writing to 3MF archive: {e}")
            self.safe_report({"ERROR"}, f"Unable to complete writing to 3MF archive: {e}")
            return {"CANCELLED"}

        debug(f"Exported {self.num_written} objects to {format_name}3MF archive {self.filepath}.")
        self.safe_report({"INFO"}, f"Exported {self.num_written} objects to {self.filepath}")
        return {"FINISHED"}
