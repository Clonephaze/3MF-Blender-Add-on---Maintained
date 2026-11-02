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

"""Ordered filament slot table for Orca/BambuStudio export.

Slots are 1-based, matching ``extruder=`` and paint states.  When a previous
import stashed ``project_settings.config``, its ``filament_colour`` list is the
base table, so every slot (used or not, physical or mixed) survives export in
its original position.  New colours are appended after the base slots.
"""

import json
from typing import Dict, List, Optional, Sequence

import bpy

from ..common.constants import conflicting_mustpreserve_contents
from ..common.mixed_slots import mixed_slot_flags
from ..import_3mf.archive import get_stashed_config


_PROJECT_SETTINGS_TEXT = ".3mf_config/Metadata/project_settings.config"
_PROJECT_SETTINGS_PATH = "Metadata/project_settings.config"


def has_stashed_mixed_filaments() -> bool:
    """Return whether the current Blender file contains Orca mixed slots.

    A present but unreadable or conflicting project-settings block is an error,
    not evidence that the scene has no mixed slots.
    """
    text = bpy.data.texts.get(_PROJECT_SETTINGS_TEXT)
    if text is None:
        return False
    if text.as_string() == conflicting_mustpreserve_contents:
        raise ValueError(
            "Imported project settings conflict; resolve the conflict before export."
        )

    raw = get_stashed_config(_PROJECT_SETTINGS_PATH)
    if raw is None:
        raise ValueError(
            "The stashed Orca project settings are unreadable; export was stopped "
            "to avoid losing mixed-filament metadata."
        )
    try:
        settings = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "The stashed Orca project settings are invalid; export was stopped "
            "to avoid losing mixed-filament metadata."
        ) from exc
    if not isinstance(settings, dict):
        raise ValueError("The stashed Orca project settings must be a JSON object.")

    colors = settings.get("filament_colour")
    slot_count = len(colors) if isinstance(colors, list) else None
    return any(mixed_slot_flags(settings, slot_count))


def _norm(hex_color: str) -> str:
    return str(hex_color).strip().upper()


class FilamentSlotTable:
    """Maps colours and slot references onto a stable slot list."""

    def __init__(
        self,
        base_colors: Sequence[str] = (),
        mixed_flags: Sequence[bool] = (),
    ):
        self.colors: List[str] = [_norm(c) for c in base_colors]
        self.base_count = len(self.colors)
        # 0-based source slot in the base table, or None for appended slots.
        self.sources: List[Optional[int]] = list(range(self.base_count))
        flags = [bool(f) for f in mixed_flags][: self.base_count]
        self.mixed: List[bool] = flags + [False] * (self.base_count - len(flags))
        self._claimed: Dict[int, str] = {}

    def __len__(self) -> int:
        return len(self.colors)

    def is_mixed(self, slot: int) -> bool:
        return 1 <= slot <= len(self.mixed) and self.mixed[slot - 1]

    def _append(self, hex_color: str) -> int:
        self.colors.append(hex_color)
        self.sources.append(None)
        self.mixed.append(False)
        slot = len(self.colors)
        self._claimed[slot] = hex_color
        return slot

    def assign_slot(self, slot: int, hex_color: str, extend: bool = False) -> int:
        """Resolve an explicit slot reference carrying ``hex_color``.

        The first claim on a physical slot sets its colour (so palette or
        material recolours are exported).  Mixed slots keep their stored
        colour because it is derived from their components.  A conflicting
        second claim (e.g. a duplicated, recoloured material that copied the
        slot tag) falls back to colour matching.  With ``extend``, slots past
        the end of the table are created (paint palettes are slot-indexed).
        """
        hex_color = _norm(hex_color)
        slot = int(slot)
        if extend:
            while len(self.colors) < slot - 1:
                self._append(hex_color)
            if len(self.colors) == slot - 1:
                return self._append(hex_color)
        if not 1 <= slot <= len(self.colors):
            return self.assign_color(hex_color)
        if self.mixed[slot - 1]:
            return slot
        claimed = self._claimed.get(slot)
        if claimed is None:
            self._claimed[slot] = hex_color
            self.colors[slot - 1] = hex_color
            return slot
        if claimed == hex_color:
            return slot
        return self.assign_color(hex_color)

    def assign_color(self, hex_color: str) -> int:
        """Slot for an untagged colour: reuse a matching physical slot or append."""
        hex_color = _norm(hex_color)
        for slot in sorted(self._claimed):
            if self._claimed[slot] == hex_color and not self.mixed[slot - 1]:
                return slot
        for idx, color in enumerate(self.colors):
            slot = idx + 1
            if color == hex_color and not self.mixed[idx] and slot not in self._claimed:
                self._claimed[slot] = hex_color
                return slot
        return self._append(hex_color)

    def color_to_slot(self) -> Dict[str, int]:
        """``{hex: first slot}`` for colour-keyed lookups, preferring physical slots."""
        mapping: Dict[str, int] = {}
        for idx, color in enumerate(self.colors):
            if not self.mixed[idx]:
                mapping.setdefault(color, idx + 1)
        for idx, color in enumerate(self.colors):
            mapping.setdefault(color, idx + 1)
        return mapping
