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
Filament / extruder color readers for slicer-specific config files.

Consolidates the five color-reading functions that each independently
opened the ZIP archive.  Now they all take a pre-opened archive or
the archive path and share the single open.
"""

import json
import xml.etree.ElementTree
import zipfile
from typing import TYPE_CHECKING

from ...common import debug, warn

if TYPE_CHECKING:
    from ..context import ImportContext

__all__ = [
    "read_orca_filament_colors",
    "read_prusa_slic3r_colors",
    "read_blender_addon_colors",
    "read_prusa_object_extruders",
    "read_prusa_filament_colors",
]


# ---------------------------------------------------------------------------
# Orca Slicer: project_settings.config
# ---------------------------------------------------------------------------

def read_orca_filament_colors(ctx: "ImportContext", archive_path: str) -> None:
    """Read filament colors from Orca Slicer's ``Metadata/project_settings.config``.

    :param ctx: Import context — populates ``ctx.orca_filament_colors``.
    :param archive_path: Filesystem path to the 3MF archive.
    """
    if ctx.options.import_materials == "NONE":
        return

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            config_path = "Metadata/project_settings.config"
            if config_path not in archive.namelist():
                debug(f"No {config_path} in archive, skipping Orca color import")
                return

            with archive.open(config_path) as config_file:
                try:
                    config = json.load(config_file)
                except json.JSONDecodeError as e:
                    warn(f"Failed to parse {config_path}: {e}")
                    return

                filament_colours = config.get("filament_colour", [])
                if filament_colours:
                    for idx, hex_color in enumerate(filament_colours):
                        ctx.orca_filament_colors[idx] = hex_color
                    debug(f"Loaded {len(filament_colours)} Orca filament colors: {filament_colours}")
                    ctx.safe_report(
                        {"INFO"},
                        f"Loaded {len(filament_colours)} Orca filament colors",
                    )

    except (zipfile.BadZipFile, IOError) as e:
        debug(f"Could not read Orca config from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# PrusaSlicer: Slic3r_PE.config
# ---------------------------------------------------------------------------

def read_prusa_slic3r_colors(ctx: "ImportContext", archive_path: str) -> None:
    """Read extruder colors from PrusaSlicer's ``Metadata/Slic3r_PE.config``.

    Skips if colors were already loaded from Orca config.

    :param ctx: Import context.
    :param archive_path: Filesystem path to the 3MF archive.
    """
    if ctx.options.import_materials == "NONE":
        return
    if ctx.orca_filament_colors:
        debug("Filament colors already loaded, skipping Slic3r_PE.config")
        return

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            config_path = "Metadata/Slic3r_PE.config"
            if config_path not in archive.namelist():
                debug(f"No {config_path} in archive, skipping PrusaSlicer color import")
                return

            with archive.open(config_path) as config_file:
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

    except (zipfile.BadZipFile, IOError) as e:
        debug(f"Could not read PrusaSlicer config from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# Blender addon fallback: blender_filament_colors.xml
# ---------------------------------------------------------------------------

def read_blender_addon_colors(ctx: "ImportContext", archive_path: str) -> None:
    """Read extruder colors from our addon's fallback metadata XML.

    Skips if colors were already loaded from Orca or PrusaSlicer config.

    :param ctx: Import context.
    :param archive_path: Filesystem path to the 3MF archive.
    """
    if ctx.options.import_materials == "NONE":
        return
    if ctx.orca_filament_colors:
        debug("Filament colors already loaded, skipping blender_filament_colors.xml")
        return

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            config_path = "Metadata/blender_filament_colors.xml"
            if config_path not in archive.namelist():
                debug(f"No {config_path} in archive, using default colors")
                return

            with archive.open(config_path) as config_file:
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

    except (zipfile.BadZipFile, IOError, xml.etree.ElementTree.ParseError) as e:
        debug(f"Could not read Blender addon colors from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# PrusaSlicer: Slic3r_PE_model.config (object extruder assignments)
# ---------------------------------------------------------------------------

def read_prusa_object_extruders(ctx: "ImportContext", archive_path: str) -> None:
    """Read per-object extruder assignments from PrusaSlicer's model config.

    :param ctx: Import context — populates ``ctx.object_default_extruders``.
    :param archive_path: Filesystem path to the 3MF archive.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            config_path = "Metadata/Slic3r_PE_model.config"
            if config_path not in archive.namelist():
                debug(f"No {config_path} in archive, skipping object extruder import")
                return

            with archive.open(config_path) as config_file:
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

    except (zipfile.BadZipFile, IOError) as e:
        debug(f"Could not read PrusaSlicer model config from {archive_path}: {e}")


# ---------------------------------------------------------------------------
# Legacy: blender_filament_colors.txt (paint code → hex)
# ---------------------------------------------------------------------------

def read_prusa_filament_colors(ctx: "ImportContext", archive_path: str) -> None:
    """Read filament colors from legacy ``blender_filament_colors.txt``.

    :param ctx: Import context.
    :param archive_path: Filesystem path to the 3MF archive.
    """
    from .paint import parse_paint_color_to_index

    if ctx.options.import_materials == "NONE":
        return

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            metadata_path = "Metadata/blender_filament_colors.txt"
            if metadata_path not in archive.namelist():
                debug(f"No {metadata_path} in archive, skipping Prusa color import")
                return

            with archive.open(metadata_path) as metadata_file:
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

    except (zipfile.BadZipFile, IOError) as e:
        debug(f"Could not read Prusa filament colors from {archive_path}: {e}")
