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
Filament / extruder color readers for slicer-specific config files.

Consolidates the five color-reading functions that each independently
opened the ZIP archive.  Now they all take a pre-opened archive or
the archive path and share the single open.
"""

import json
import xml.etree.ElementTree
import zipfile
from typing import TYPE_CHECKING, Optional

from ...common import debug, warn

if TYPE_CHECKING:
    from ..context import ImportContext

__all__ = [
    "read_all_slicer_colors",
    "read_orca_filament_colors",
    "read_prusa_slic3r_colors",
    "read_blender_addon_colors",
    "read_prusa_object_extruders",
    "read_prusa_filament_colors",
    "read_orca_part_subtypes",
]


def read_all_slicer_colors(ctx: "ImportContext", archive_path: str) -> None:
    """Read all slicer filament / extruder colors with a single archive open.

    Opens the 3MF ZIP once and passes it to each reader in priority order.
    Individual readers still check ``ctx.options.import_materials`` and
    short-circuit when colours are already loaded.

    :param ctx: Import context.
    :param archive_path: Filesystem path to the 3MF archive.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            read_orca_filament_colors(ctx, archive_path, archive=archive)
            read_prusa_slic3r_colors(ctx, archive_path, archive=archive)
            read_blender_addon_colors(ctx, archive_path, archive=archive)
            read_prusa_object_extruders(ctx, archive_path, archive=archive)
            read_prusa_filament_colors(ctx, archive_path, archive=archive)
    except (zipfile.BadZipFile, IOError) as e:
        debug(f"Could not open archive {archive_path}: {e}")


# ---------------------------------------------------------------------------
# Orca Slicer: project_settings.config
# ---------------------------------------------------------------------------

def read_orca_filament_colors(
    ctx: "ImportContext",
    archive_path: str,
    archive: Optional[zipfile.ZipFile] = None,
) -> None:
    """Read filament colors from Orca Slicer's ``Metadata/project_settings.config``.

    :param ctx: Import context — populates ``ctx.orca_filament_colors``.
    :param archive_path: Filesystem path to the 3MF archive.
    :param archive: Optional pre-opened ZipFile to avoid redundant opens.
    """
    if ctx.options.import_materials == "NONE":
        return

    def _read(zf: zipfile.ZipFile) -> None:
        config_path = "Metadata/project_settings.config"
        if config_path not in zf.namelist():
            debug(f"No {config_path} in archive, skipping Orca color import")
            return

        with zf.open(config_path) as config_file:
            try:
                config = json.load(config_file)
            except json.JSONDecodeError as e:
                warn(f"Failed to parse {config_path}: {e}")
                return

            filament_colours = config.get("filament_colour", [])
            if filament_colours:
                for idx, hex_color in enumerate(filament_colours):
                    ctx.orca_filament_colors[idx] = hex_color
                # Record physical count so render_paint_texture can tag meshes
                ctx.num_physical_filaments = len(filament_colours)
                debug(f"Loaded {len(filament_colours)} Orca filament colors: {filament_colours}")
                ctx.safe_report(
                    {"INFO"},
                    f"Loaded {len(filament_colours)} Orca filament colors",
                )

            # --- OrcaSlicer-FullSpectrum: mixed filament definitions ---
            mixed_defs = config.get("mixed_filament_definitions", "")
            if mixed_defs:
                from ...common.mixed_filaments import (
                    parse_mixed_filament_definitions,
                    populate_display_colors,
                )
                from .detection import detect_fullspectrum
                ctx.mixed_filament_definitions_raw = mixed_defs
                ctx.has_mixed_filaments = True
                if detect_fullspectrum(config):
                    ctx.vendor_format = "orca_fullspectrum"
                    debug("Detected OrcaSlicer-FullSpectrum format")

                # Build a 0-indexed list of physical colors for display computation
                num_physical = len(filament_colours)
                physical_colors = [filament_colours[i] for i in range(num_physical)]

                entries = parse_mixed_filament_definitions(mixed_defs)
                populate_display_colors(entries, physical_colors)
                ctx.mixed_filament_entries = entries

                # Append virtual display colors into orca_filament_colors so
                # paint materials can look them up by index (num_physical onwards).
                #
                # PAINT files (e.g. Dragon): OrcaSlicer's paint_color codes count
                # only active (enabled, non-deleted) virtual slots sequentially, so
                # "1C"=first active mix, "2C"=second, etc.  We must filter here so
                # the code→index lookup matches the paint codes written in the file.
                #
                # PARTS files (e.g. PeggyPalette): extruder=N uses positional slot
                # numbers that include deleted entries.  That case is handled in
                # create_solid_paint_texture, which builds a separate positional
                # color table from ctx.mixed_filament_entries directly.
                for virt_idx, mf in enumerate(
                    e for e in entries if e.enabled and not e.deleted
                ):
                    ctx.orca_filament_colors[num_physical + virt_idx] = mf.display_color

                debug(
                    f"Loaded {len(entries)} mixed filament definitions "
                    f"({sum(1 for e in entries if e.enabled and not e.deleted)} enabled)"
                )
                ctx.safe_report(
                    {"INFO"},
                    f"Loaded {len(entries)} FullSpectrum mixed filament definitions",
                )

    if archive is not None:
        _read(archive)
    else:
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                _read(zf)
        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read Orca config from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# PrusaSlicer: Slic3r_PE.config
# ---------------------------------------------------------------------------

def read_prusa_slic3r_colors(
    ctx: "ImportContext",
    archive_path: str,
    archive: Optional[zipfile.ZipFile] = None,
) -> None:
    """Read extruder colors from PrusaSlicer's ``Metadata/Slic3r_PE.config``.

    Skips if colors were already loaded from Orca config.

    :param ctx: Import context.
    :param archive_path: Filesystem path to the 3MF archive.
    :param archive: Optional pre-opened ZipFile to avoid redundant opens.
    """
    if ctx.options.import_materials == "NONE":
        return
    if ctx.orca_filament_colors:
        debug("Filament colors already loaded, skipping Slic3r_PE.config")
        return

    def _read(zf: zipfile.ZipFile) -> None:
        config_path = "Metadata/Slic3r_PE.config"
        if config_path not in zf.namelist():
            debug(f"No {config_path} in archive, skipping PrusaSlicer color import")
            return

        with zf.open(config_path) as config_file:
            content = config_file.read().decode("UTF-8")

            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("; extruder_colour = "):
                    colors_str = line[len("; extruder_colour = "):]
                    hex_colors = [c.strip() for c in colors_str.split(";")]

                    for idx, hex_color in enumerate(hex_colors):
                        if hex_color.startswith("#"):
                            ctx.orca_filament_colors[idx] = hex_color

                    ctx.safe_report(
                        {"INFO"},
                        f"Loaded {len(hex_colors)} PrusaSlicer extruder colors",
                    )
                    break

    if archive is not None:
        _read(archive)
    else:
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                _read(zf)
        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read PrusaSlicer config from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# Blender addon fallback: blender_filament_colors.xml
# ---------------------------------------------------------------------------

def read_blender_addon_colors(
    ctx: "ImportContext",
    archive_path: str,
    archive: Optional[zipfile.ZipFile] = None,
) -> None:
    """Read extruder colors from our addon's fallback metadata XML.

    Skips if colors were already loaded from Orca or PrusaSlicer config.

    :param ctx: Import context.
    :param archive_path: Filesystem path to the 3MF archive.
    :param archive: Optional pre-opened ZipFile to avoid redundant opens.
    """
    if ctx.options.import_materials == "NONE":
        return
    if ctx.orca_filament_colors:
        debug("Filament colors already loaded, skipping blender_filament_colors.xml")
        return

    def _read(zf: zipfile.ZipFile) -> None:
        config_path = "Metadata/blender_filament_colors.xml"
        if config_path not in zf.namelist():
            debug(f"No {config_path} in archive, using default colors")
            return

        with zf.open(config_path) as config_file:
            tree = xml.etree.ElementTree.parse(config_file)
            root = tree.getroot()

            for extruder_elem in root.findall("extruder"):
                try:
                    extruder_idx = int(extruder_elem.get("index", "-1"))
                    hex_color = extruder_elem.get("color", "")
                    if extruder_idx >= 0 and hex_color.startswith("#"):
                        ctx.orca_filament_colors[extruder_idx] = hex_color
                except (ValueError, AttributeError):
                    continue

            if ctx.orca_filament_colors:
                debug(
                    f"Loaded {len(ctx.orca_filament_colors)} colors from "
                    f"Blender addon metadata (fallback)"
                )
                ctx.safe_report(
                    {"INFO"},
                    f"Loaded {len(ctx.orca_filament_colors)} colors from addon metadata",
                )

    if archive is not None:
        _read(archive)
    else:
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                _read(zf)
        except (zipfile.BadZipFile, IOError, xml.etree.ElementTree.ParseError) as e:
            debug(f"Could not read Blender addon colors from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# PrusaSlicer: Slic3r_PE_model.config (object extruder assignments)
# ---------------------------------------------------------------------------

def read_prusa_object_extruders(
    ctx: "ImportContext",
    archive_path: str,
    archive: Optional[zipfile.ZipFile] = None,
) -> None:
    """Read per-object extruder assignments from PrusaSlicer's model config.

    :param ctx: Import context — populates ``ctx.object_default_extruders``.
    :param archive_path: Filesystem path to the 3MF archive.
    :param archive: Optional pre-opened ZipFile to avoid redundant opens.
    """
    def _read(zf: zipfile.ZipFile) -> None:
        config_path = "Metadata/Slic3r_PE_model.config"
        if config_path not in zf.namelist():
            debug(f"No {config_path} in archive, skipping object extruder import")
            return

        with zf.open(config_path) as config_file:
            content = config_file.read().decode("UTF-8")

            try:
                root = xml.etree.ElementTree.fromstring(content)
            except xml.etree.ElementTree.ParseError as e:
                warn(f"Failed to parse {config_path}: {e}")
                return

            for obj in root.findall(".//object"):
                obj_id = obj.get("id")
                if obj_id is None:
                    continue
                for meta in obj.findall("metadata"):
                    if meta.get("type") == "object" and meta.get("key") == "extruder":
                        try:
                            extruder = int(meta.get("value", "1"))
                            ctx.object_default_extruders[obj_id] = extruder
                            debug(f"Object {obj_id} uses extruder {extruder}")
                        except ValueError:
                            pass

            if ctx.object_default_extruders:
                debug(
                    f"Loaded extruder assignments for "
                    f"{len(ctx.object_default_extruders)} objects"
                )

    if archive is not None:
        _read(archive)
    else:
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                _read(zf)
        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read PrusaSlicer model config from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# Legacy: blender_filament_colors.txt (paint code → hex)
# ---------------------------------------------------------------------------

def read_prusa_filament_colors(
    ctx: "ImportContext",
    archive_path: str,
    archive: Optional[zipfile.ZipFile] = None,
) -> None:
    """Read filament colors from legacy ``blender_filament_colors.txt``.

    :param ctx: Import context.
    :param archive_path: Filesystem path to the 3MF archive.
    :param archive: Optional pre-opened ZipFile to avoid redundant opens.
    """
    from .paint import parse_paint_color_to_index

    if ctx.options.import_materials == "NONE":
        return

    def _read(zf: zipfile.ZipFile) -> None:
        metadata_path = "Metadata/blender_filament_colors.txt"
        if metadata_path not in zf.namelist():
            debug(f"No {metadata_path} in archive, skipping Prusa color import")
            return

        with zf.open(metadata_path) as metadata_file:
            content = metadata_file.read().decode("UTF-8")

            for line in content.strip().split("\n"):
                if "=" in line:
                    paint_code, hex_color = line.strip().split("=", 1)
                    filament_index = parse_paint_color_to_index(paint_code)
                    if filament_index > 0:
                        array_index = filament_index - 1
                        ctx.orca_filament_colors[array_index] = hex_color

            debug(
                f"Loaded {len(ctx.orca_filament_colors)} Prusa filament "
                f"colors from metadata"
            )
            ctx.safe_report(
                {"INFO"},
                f"Loaded {len(ctx.orca_filament_colors)} PrusaSlicer filament colors",
            )

    if archive is not None:
        _read(archive)
    else:
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                _read(zf)
        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read Prusa filament colors from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# Orca / BambuStudio: model_settings.config (part subtypes)
# ---------------------------------------------------------------------------

def read_orca_part_subtypes(
    ctx: "ImportContext",
    archive_path: str,
    archive: Optional[zipfile.ZipFile] = None,
) -> None:
    """Read part subtype assignments from Orca/BambuStudio model_settings.config.

    Populates ``ctx.part_subtypes`` with ``{(wrapper_id, part_id): subtype}``
    mappings for parts that are not ``normal_part`` (e.g. ``modifier_part``,
    ``support_enforcer``, ``support_blocker``, ``negative_part``).

    Part IDs in model_settings.config are scoped to their model file, so
    different wrapper objects can reuse the same part IDs with different
    subtypes.  The composite ``(wrapper_id, part_id)`` key avoids collisions.

    Also populates ``ctx.part_groups`` with wrapper object groupings so the
    importer can recreate the parent-child hierarchy (Empty parent with mesh
    children) for multi-part assemblies.

    :param ctx: Import context — populates ``ctx.part_subtypes`` and
        ``ctx.part_groups``.
    :param archive_path: Filesystem path to the 3MF archive.
    :param archive: Optional pre-opened ZipFile to avoid redundant opens.
    """
    def _read(zf: zipfile.ZipFile) -> None:
        config_path = "Metadata/model_settings.config"
        if config_path not in zf.namelist():
            debug(f"No {config_path} in archive, skipping part subtype import")
            return

        with zf.open(config_path) as config_file:
            content = config_file.read().decode("UTF-8")

            try:
                root = xml.etree.ElementTree.fromstring(content)
            except xml.etree.ElementTree.ParseError as e:
                warn(f"Failed to parse {config_path}: {e}")
                return

            for obj_elem in root.findall(".//object"):
                wrapper_id = obj_elem.get("id")
                part_ids = []

                # Read group name and collect object-level setting overrides
                # from <metadata key="..." value="..."> children.
                group_name = None
                _OBJ_STANDARD_KEYS = {"name", "extruder"}
                wrapper_overrides: dict[str, str] = {}
                for meta in obj_elem.findall("metadata"):
                    key = meta.get("key")
                    value = meta.get("value")
                    if key is None or value is None:
                        continue
                    if key == "name":
                        group_name = value
                    elif key == "extruder" and wrapper_id:
                        try:
                            ctx.object_default_extruders[wrapper_id] = int(value)
                        except (ValueError, TypeError):
                            pass
                    elif key not in _OBJ_STANDARD_KEYS:
                        wrapper_overrides[key] = value

                if wrapper_id and wrapper_overrides:
                    ctx.wrapper_metadata[wrapper_id] = wrapper_overrides
                    debug(
                        f"Wrapper {wrapper_id}: {len(wrapper_overrides)} "
                        f"setting overrides"
                    )

                _PART_STANDARD_KEYS = {"name", "matrix", "extruder"}
                for part_elem in obj_elem.findall("part"):
                    part_id = part_elem.get("id")
                    subtype = part_elem.get("subtype", "normal_part")
                    if part_id is None:
                        continue
                    part_ids.append(part_id)
                    if subtype != "normal_part" and wrapper_id:
                        ctx.part_subtypes[(wrapper_id, part_id)] = subtype
                        debug(f"Part ({wrapper_id}, {part_id}) subtype: {subtype}")

                    # Collect part name, extruder, and setting overrides.
                    part_name = None
                    part_overrides: dict[str, str] = {}
                    for meta in part_elem.findall("metadata"):
                        key = meta.get("key")
                        value = meta.get("value")
                        if key is None or value is None:
                            continue
                        if key == "name":
                            part_name = value
                        elif key == "extruder" and wrapper_id:
                            try:
                                ctx.part_extruders[(wrapper_id, part_id)] = int(value)
                            except (ValueError, TypeError):
                                pass
                        elif key not in _PART_STANDARD_KEYS:
                            part_overrides[key] = value

                    if part_name and wrapper_id:
                        ctx.part_names[(wrapper_id, part_id)] = part_name

                    if part_overrides and wrapper_id:
                        ctx.part_metadata[(wrapper_id, part_id)] = part_overrides
                        debug(
                            f"Part ({wrapper_id}, {part_id}): "
                            f"{len(part_overrides)} setting overrides"
                        )

                # Store group info for multi-part assemblies
                if wrapper_id and len(part_ids) > 1:
                    ctx.part_groups[wrapper_id] = {
                        "name": group_name or "3MF Group",
                        "part_ids": part_ids,
                    }
                    debug(
                        f"Group '{group_name}' (wrapper {wrapper_id}): "
                        f"{len(part_ids)} parts"
                    )

            if ctx.part_subtypes:
                debug(
                    f"Loaded part subtypes for "
                    f"{len(ctx.part_subtypes)} parts"
                )

    if archive is not None:
        _read(archive)
    else:
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                _read(zf)
        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read Orca model settings from {archive_path}: {e}")
