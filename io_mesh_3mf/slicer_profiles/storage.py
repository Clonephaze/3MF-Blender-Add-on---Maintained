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
File-based slicer profile storage.

Profiles are stored as JSON files in Blender's config directory under
``3mf_slicer_profiles/``.  Each file contains:

.. code-block:: json

    {
        "name": "My Printer Profile",
        "vendor": "Orca Slicer",
        "source_file": "benchy.3mf",
        "configs": {
            "Metadata/project_settings.config": "<base85-encoded>",
            ...
        }
    }
"""

from __future__ import annotations

import base64
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import NamedTuple

import bpy

from ..common.logging import debug, error


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROFILES_SUBDIR = "3mf_slicer_profiles"

_SLICER_CONFIG_PATHS = {
    "Metadata/project_settings.config": "Project Settings",
    "Metadata/model_settings.config": "Model Settings",
    "Metadata/Slic3r_PE.config": "Printer Config",
    "Metadata/Slic3r_PE_model.config": "Per-Object Config",
}


# ---------------------------------------------------------------------------
# ProfileInfo
# ---------------------------------------------------------------------------

class ProfileInfo(NamedTuple):
    """Summary of a stored slicer profile."""

    name: str
    vendor: str
    machine: str
    source_file: str
    filepath: str


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------

def get_profiles_dir() -> str:
    """Return the profiles directory path, creating it if necessary."""
    config_dir = bpy.utils.user_resource('CONFIG')
    profiles_dir = os.path.join(config_dir, _PROFILES_SUBDIR)
    os.makedirs(profiles_dir, exist_ok=True)
    return profiles_dir


def _sanitize_filename(name: str) -> str:
    """Convert a profile name to a safe filename component."""
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    sanitized = sanitized.strip('. ')
    return sanitized or "profile"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_profiles() -> list[ProfileInfo]:
    """List all saved slicer profiles sorted alphabetically by name."""
    profiles_dir = get_profiles_dir()
    result: list[ProfileInfo] = []
    for filename in os.listdir(profiles_dir):
        if not filename.endswith('.json'):
            continue
        filepath = os.path.join(profiles_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            result.append(ProfileInfo(
                name=data.get('name', os.path.splitext(filename)[0]),
                vendor=data.get('vendor', 'Unknown'),
                machine=data.get('machine', ''),
                source_file=data.get('source_file', ''),
                filepath=filepath,
            ))
        except (json.JSONDecodeError, OSError):
            continue
    result.sort(key=lambda p: p.name.lower())
    return result


def load_profile(name: str) -> dict | None:
    """Load a profile's full data dict by name.

    :return: The parsed JSON dict, or ``None`` if not found.
    """
    for info in list_profiles():
        if info.name == name:
            try:
                with open(info.filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return None
    return None


def save_profile(
    name: str,
    vendor: str,
    source_file: str,
    configs: dict[str, str],
    machine: str = "",
) -> str:
    """Save a new slicer profile to disk.

    If a profile with the same *name* already exists, a numeric suffix is
    appended to make the name unique.

    :param name: Display name for the profile.
    :param vendor: Detected slicer vendor string.
    :param source_file: Original ``.3mf`` filename.
    :param configs: ``{config_path: base85_encoded_str}`` dict.
    :param machine: Printer/machine model name extracted from configs.
    :return: Filepath of the saved JSON file.
    """
    existing_names = {p.name for p in list_profiles()}
    unique_name = name
    counter = 2
    while unique_name in existing_names:
        unique_name = f"{name} ({counter})"
        counter += 1

    profiles_dir = get_profiles_dir()
    filename = _sanitize_filename(unique_name) + '.json'
    filepath = os.path.join(profiles_dir, filename)

    data = {
        'name': unique_name,
        'vendor': vendor,
        'machine': machine,
        'source_file': source_file,
        'configs': configs,
    }

    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    debug(f"Saved slicer profile '{unique_name}' to {filepath}")
    return filepath


def delete_profile(name: str) -> bool:
    """Delete a profile by name.

    :return: ``True`` if deleted, ``False`` if not found or failed.
    """
    for info in list_profiles():
        if info.name == name:
            try:
                os.remove(info.filepath)
                debug(f"Deleted slicer profile '{name}'")
                return True
            except OSError as e:
                error(f"Failed to delete profile '{name}': {e}")
                return False
    return False


def rename_profile(old_name: str, new_name: str) -> bool:
    """Rename a profile on disk.

    Updates the ``name`` key inside the JSON and renames the file.

    :return: ``True`` on success.
    """
    for info in list_profiles():
        if info.name == old_name:
            try:
                with open(info.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data['name'] = new_name

                new_filename = _sanitize_filename(new_name) + '.json'
                new_path = os.path.join(
                    os.path.dirname(info.filepath), new_filename,
                )

                with open(new_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)

                if new_path != info.filepath:
                    os.remove(info.filepath)

                debug(f"Renamed slicer profile '{old_name}' -> '{new_name}'")
                return True
            except (json.JSONDecodeError, OSError) as e:
                error(f"Failed to rename profile: {e}")
                return False
    return False


# ---------------------------------------------------------------------------
# Config retrieval
# ---------------------------------------------------------------------------

def get_profile_config(profile_name: str, config_path: str) -> bytes | None:
    """Retrieve a specific config file's raw bytes from a saved profile.

    :param profile_name: Profile display name.
    :param config_path: Archive path,
        e.g. ``"Metadata/project_settings.config"``.
    :return: Raw file bytes, or ``None`` if unavailable.
    """
    profile = load_profile(profile_name)
    if profile is None:
        return None
    encoded = profile.get('configs', {}).get(config_path)
    if not encoded:
        return None
    try:
        return base64.b85decode(encoded.encode('UTF-8'))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3MF extraction
# ---------------------------------------------------------------------------

def _extract_machine_name(
    configs: dict[str, str],
) -> str:
    """Try to extract the printer/machine model name from config data.

    Checks Orca/Bambu JSON ``printer_model`` first, then PrusaSlicer
    INI-style ``printer_model``.
    """
    # Orca / BambuStudio JSON configs
    orca_key = "Metadata/project_settings.config"
    if orca_key in configs:
        try:
            raw = base64.b85decode(configs[orca_key].encode("UTF-8"))
            data = json.loads(raw.decode("utf-8"))
            model = data.get("printer_model", "")
            if model:
                return str(model)
        except Exception:
            pass

    # PrusaSlicer INI-style configs
    prusa_key = "Metadata/Slic3r_PE.config"
    if prusa_key in configs:
        try:
            raw = base64.b85decode(configs[prusa_key].encode("UTF-8"))
            for line in raw.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("printer_model"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        val = parts[1].strip()
                        if val:
                            return val
        except Exception:
            pass

    return ""


def extract_from_3mf(
    filepath: str,
) -> tuple[str, str, dict[str, str], list[str]]:
    """Extract slicer config files and detect vendor from a 3MF archive.

    :param filepath: Path to a ``.3mf`` file.
    :return: ``(vendor, machine, configs, labels)`` where *configs* maps
        archive paths to Base85-encoded content, *labels* are
        human-readable names, *machine* is the printer model name.
    :raises zipfile.BadZipFile: If the file is not a valid ZIP.
    """
    configs: dict[str, str] = {}
    labels: list[str] = []
    vendor = ""

    with zipfile.ZipFile(filepath, "r") as archive:
        namelist = set(archive.namelist())
        for config_path, label in _SLICER_CONFIG_PATHS.items():
            if config_path in namelist:
                raw = archive.read(config_path)
                configs[config_path] = base64.b85encode(raw).decode("UTF-8")
                labels.append(label)

        # Initial vendor guess from config file presence
        if "Metadata/project_settings.config" in configs:
            vendor = "Orca Slicer"
        elif "Metadata/Slic3r_PE.config" in configs:
            vendor = "PrusaSlicer"

        # Refine from model XML Application metadata
        model_path = "3D/3dmodel.model"
        if model_path in namelist:
            try:
                ns = (
                    "http://schemas.microsoft.com/"
                    "3dmanufacturing/core/2015/02"
                )
                root = ET.fromstring(archive.read(model_path))
                for meta in root.iter(f"{{{ns}}}metadata"):
                    if meta.get("name") != "Application":
                        continue
                    app = (meta.text or "").lower()
                    if "orca" in app:
                        vendor = "Orca Slicer"
                    elif "bambu" in app:
                        vendor = "BambuStudio"
                    elif "prusa" in app or "slic3r" in app:
                        vendor = "PrusaSlicer"
                    elif "superslicer" in app:
                        vendor = "SuperSlicer"
                    elif "cura" in app or "ultimaker" in app:
                        vendor = "Cura"
                    break
            except ET.ParseError:
                pass

    machine = _extract_machine_name(configs)
    return vendor, machine, configs, labels
