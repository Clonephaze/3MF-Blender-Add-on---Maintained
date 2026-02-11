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
Archive reading utilities for 3MF import.

Handles opening ZIP archives, parsing ``[Content_Types].xml``, assigning
MIME types to archive entries, and preserving ``MustPreserve`` files.
"""

import base64
import re
import xml.etree.ElementTree
import zipfile
from typing import Dict, IO, List, Pattern, Tuple, TYPE_CHECKING

import bpy

from ..common import (
    debug,
    warn,
    error,
    CONTENT_TYPES_LOCATION,
    RELS_MIMETYPE,
    MODEL_MIMETYPE,
    conflicting_mustpreserve_contents,
)
from ..common.annotations import Annotations, ContentType, Relationship

if TYPE_CHECKING:
    from .context import ImportContext

__all__ = [
    "read_archive",
    "read_content_types",
    "assign_content_types",
    "must_preserve",
    "load_external_model",
]


# ---------------------------------------------------------------------------
# read_archive
# ---------------------------------------------------------------------------

def read_archive(
    ctx: "ImportContext",
    path: str,
) -> Dict[str, List[IO[bytes]]]:
    """Create file streams from all the files in a 3MF ZIP archive.

    Results are grouped by MIME content type.  Consumers pick the types
    they understand and process those streams.

    :param ctx: The import context (for ``safe_report``).
    :param path: Filesystem path to the ``.3mf`` archive.
    :return: ``{content_type: [stream, ...]}``
    """
    result: Dict[str, List[IO[bytes]]] = {}
    try:
        archive = zipfile.ZipFile(path)
        content_types = read_content_types(ctx, archive)
        mime_types = assign_content_types(archive, content_types)
        for fpath, mime_type in mime_types.items():
            if mime_type not in result:
                result[mime_type] = []
            result[mime_type].append(archive.open(fpath))
    except (zipfile.BadZipFile, EnvironmentError) as e:
        error(f"Unable to read archive: {e}")
        ctx.safe_report({"ERROR"}, f"Unable to read archive: {e}")
        return result
    return result


# ---------------------------------------------------------------------------
# read_content_types
# ---------------------------------------------------------------------------

def read_content_types(
    ctx: "ImportContext",
    archive: zipfile.ZipFile,
) -> List[Tuple[Pattern[str], str]]:
    """Parse ``[Content_Types].xml`` from a 3MF archive.

    Returns a priority-ordered list of ``(regex, mime_type)`` pairs.

    :param ctx: The import context (for ``safe_report``).
    :param archive: An open ``ZipFile``.
    :return: Ordered list of ``(compiled_regex, mime_type)``.
    """
    namespaces = {"ct": "http://schemas.openxmlformats.org/package/2006/content-types"}
    result: List[Tuple[Pattern[str], str]] = []

    try:
        with archive.open(CONTENT_TYPES_LOCATION) as f:
            try:
                root = xml.etree.ElementTree.ElementTree(file=f)
            except xml.etree.ElementTree.ParseError as e:
                warn(
                    f"{CONTENT_TYPES_LOCATION} has malformed XML "
                    f"(position {e.position[0]}:{e.position[1]})."
                )
                ctx.safe_report(
                    {"WARNING"},
                    f"{CONTENT_TYPES_LOCATION} has malformed XML at position "
                    f"{e.position[0]}:{e.position[1]}",
                )
                root = None

            if root is not None:
                # Overrides have higher priority — put them first.
                for override_node in root.iterfind("ct:Override", namespaces):
                    if (
                        "PartName" not in override_node.attrib
                        or "ContentType" not in override_node.attrib
                    ):
                        warn(
                            "[Content_Types].xml malformed: Override node without path or MIME type."
                        )
                        ctx.safe_report(
                            {"WARNING"},
                            "[Content_Types].xml malformed: Override node without path or MIME type",
                        )
                        continue
                    match_regex = re.compile(re.escape(override_node.attrib["PartName"]))
                    result.append((match_regex, override_node.attrib["ContentType"]))

                for default_node in root.iterfind("ct:Default", namespaces):
                    if (
                        "Extension" not in default_node.attrib
                        or "ContentType" not in default_node.attrib
                    ):
                        warn(
                            "[Content_Types].xml malformed: Default node without extension or MIME type."
                        )
                        ctx.safe_report(
                            {"WARNING"},
                            "[Content_Types].xml malformed: Default node without extension or MIME type",
                        )
                        continue
                    match_regex = re.compile(
                        rf".*\.{re.escape(default_node.attrib['Extension'])}"
                    )
                    result.append((match_regex, default_node.attrib["ContentType"]))
    except KeyError:
        warn(f"{CONTENT_TYPES_LOCATION} file missing!")
        ctx.safe_report({"WARNING"}, f"{CONTENT_TYPES_LOCATION} file missing")

    # Robust fallback defaults (lowest priority).
    result.append((re.compile(r".*\.rels"), RELS_MIMETYPE))
    result.append((re.compile(r".*\.model"), MODEL_MIMETYPE))

    return result


# ---------------------------------------------------------------------------
# assign_content_types
# ---------------------------------------------------------------------------

def assign_content_types(
    archive: zipfile.ZipFile,
    content_types: List[Tuple[Pattern[str], str]],
) -> Dict[str, str]:
    """Assign a MIME type to every file in *archive*.

    :param archive: A 3MF archive.
    :param content_types: Priority-ordered ``(regex, mime)`` pairs.
    :return: ``{archive_path: mime_type}``
    """
    result: Dict[str, str] = {}
    for file_info in archive.filelist:
        file_path = file_info.filename
        if file_path == CONTENT_TYPES_LOCATION:
            continue
        for pattern, content_type in content_types:
            if pattern.fullmatch(file_path):
                result[file_path] = content_type
                break
        else:
            result[file_path] = ""
    return result


# ---------------------------------------------------------------------------
# must_preserve
# ---------------------------------------------------------------------------

def must_preserve(
    ctx: "ImportContext",
    files_by_content_type: Dict[str, List[IO[bytes]]],
    annotations: Annotations,
) -> None:
    """Preserve ``MustPreserve`` and ``PrintTicket`` files in Blender text blocks.

    Archived files are stored in Base85 encoding so that arbitrary binary data
    can round-trip through Blender's Text objects.

    :param ctx: The import context (unused currently, reserved for future reporting).
    :param files_by_content_type: Archive streams grouped by MIME type.
    :param annotations: OPC annotations gathered so far.
    """
    preserved_files: set = set()
    for target, its_annotations in annotations.annotations.items():
        for annotation in its_annotations:
            if type(annotation) is Relationship:
                if annotation.namespace in {
                    "http://schemas.openxmlformats.org/package/2006/relationships/mustpreserve",
                    "http://schemas.microsoft.com/3dmanufacturing/2013/01/printticket",
                }:
                    preserved_files.add(target)
            elif type(annotation) is ContentType:
                if annotation.mime_type == "application/vnd.ms-printing.printticket+xml":
                    preserved_files.add(target)

    for files in files_by_content_type.values():
        for file in files:
            file_name = str(file.name)
            if file_name in preserved_files:
                filename = f".3mf_preserved/{file_name}"
                if filename in bpy.data.texts:
                    if bpy.data.texts[filename].as_string() == conflicting_mustpreserve_contents:
                        continue
                file_contents = base64.b85encode(file.read()).decode("UTF-8")
                if filename in bpy.data.texts:
                    if bpy.data.texts[filename].as_string() == file_contents:
                        continue
                    else:
                        bpy.data.texts[filename].clear()
                        bpy.data.texts[filename].write(conflicting_mustpreserve_contents)
                        continue
                else:
                    handle = bpy.data.texts.new(filename)
                    handle.write(file_contents)


# ---------------------------------------------------------------------------
# load_external_model  (Production Extension)
# ---------------------------------------------------------------------------

def load_external_model(
    ctx: "ImportContext",
    model_path: str,
) -> None:
    """Load an external model file referenced by Production Extension ``p:path``.

    Used by Orca Slicer / BambuStudio which stores each object in a separate
    ``.model`` file under ``3D/Objects/``.

    :param ctx: The import context.
    :param model_path: Archive-relative path (e.g. ``/3D/Objects/Cube_1.model``).
    """
    from .geometry import read_external_model_objects

    if not ctx.current_archive_path:
        warn(f"Cannot load external model {model_path}: no archive path set")
        return

    archive_path = model_path.lstrip("/")

    try:
        with zipfile.ZipFile(ctx.current_archive_path, "r") as archive:
            if archive_path not in archive.namelist():
                warn(f"External model file not found in archive: {archive_path}")
                return

            with archive.open(archive_path) as model_file:
                try:
                    document = xml.etree.ElementTree.parse(model_file)
                except xml.etree.ElementTree.ParseError as e:
                    error(f"External model {archive_path} is malformed: {e}")
                    ctx.safe_report({"ERROR"}, f"External model {archive_path} is malformed")
                    return

                root = document.getroot()
                read_external_model_objects(ctx, root, model_path)
                debug(f"Loaded external model: {archive_path}")

    except (zipfile.BadZipFile, IOError) as e:
        error(f"Failed to read external model {archive_path}: {e}")
        ctx.safe_report({"ERROR"}, f"Failed to read external model: {e}")
