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
Archive management for 3MF export.

Functions for creating and managing the 3MF ZIP archive:
- create_archive: Create an empty 3MF archive with OPC structure
- must_preserve: Write must-preserve files from previous imports
- write_core_properties: Write Dublin Core metadata
"""

import base64
import datetime
import xml.etree.ElementTree
import zipfile
from typing import Optional, Callable

import bpy

from ..common.annotations import Annotations
from ..common.logging import debug, error
from ..common.constants import (
    CORE_PROPERTIES_LOCATION,
    CORE_PROPERTIES_NAMESPACE,
    DC_NAMESPACE,
    DCTERMS_NAMESPACE,
    conflicting_mustpreserve_contents,
)


def create_archive(filepath: str, safe_report: Callable) -> Optional[zipfile.ZipFile]:
    """
    Creates an empty 3MF archive.

    The archive is complete according to the 3MF specs except that the actual
    3dmodel.model file is missing.

    :param filepath: The path to write the file to.
    :param safe_report: Callable for reporting errors/warnings.
    :return: A zip archive that other functions can add things to.
    """
    try:
        archive = zipfile.ZipFile(
            filepath, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        )

        # Store the file annotations we got from imported 3MF files.
        annotations = Annotations()
        annotations.retrieve()
        annotations.write_rels(archive)
        annotations.write_content_types(archive)
        must_preserve(archive)
    except EnvironmentError as e:
        error(f"Unable to write 3MF archive to {filepath}: {e}")
        safe_report({"ERROR"}, f"Unable to write 3MF archive to {filepath}: {e}")
        return None

    return archive


def must_preserve(archive: zipfile.ZipFile) -> None:
    """
    Write files that must be preserved to the archive.

    These files were stored in the Blender scene in a hidden location.

    :param archive: The archive to write files to.
    """
    for textfile in bpy.data.texts:
        # Cache filename to protect Unicode characters from garbage collection
        filename = str(textfile.name)
        if not filename.startswith(".3mf_preserved/"):
            continue  # Unrelated file. Not ours to read.
        contents = textfile.as_string()
        if contents == conflicting_mustpreserve_contents:
            continue  # This file was in conflict. Don't preserve any copy of it then.
        contents = base64.b85decode(contents.encode("UTF-8"))
        filename = filename[len(".3mf_preserved/"):]
        with archive.open(filename, "w") as f:
            f.write(contents)


def write_core_properties(archive: zipfile.ZipFile) -> None:
    """
    Write OPC Core Properties (Dublin Core metadata) to the archive.

    This adds standard document metadata like creator, creation date, and modification
    date as defined by the Open Packaging Conventions specification.

    :param archive: The 3MF archive to write Core Properties into.
    """
    # Register namespaces for cleaner output
    xml.etree.ElementTree.register_namespace("cp", CORE_PROPERTIES_NAMESPACE)
    xml.etree.ElementTree.register_namespace("dc", DC_NAMESPACE)
    xml.etree.ElementTree.register_namespace("dcterms", DCTERMS_NAMESPACE)

    # Create root element with proper namespaces
    root = xml.etree.ElementTree.Element(
        f"{{{CORE_PROPERTIES_NAMESPACE}}}coreProperties"
    )
    root.set("xmlns:dc", DC_NAMESPACE)
    root.set("xmlns:dcterms", DCTERMS_NAMESPACE)

    # dc:creator - who created this file
    creator = xml.etree.ElementTree.SubElement(root, f"{{{DC_NAMESPACE}}}creator")
    creator.text = "Blender 3MF Format Add-on"

    # dcterms:created - when the file was created (W3CDTF format)
    now = datetime.datetime.now(datetime.timezone.utc)
    created = xml.etree.ElementTree.SubElement(root, f"{{{DCTERMS_NAMESPACE}}}created")
    created.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # dcterms:modified - when the file was last modified
    modified = xml.etree.ElementTree.SubElement(
        root, f"{{{DCTERMS_NAMESPACE}}}modified"
    )
    modified.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Write the Core Properties file
    document = xml.etree.ElementTree.ElementTree(root)
    try:
        with archive.open(CORE_PROPERTIES_LOCATION, "w") as f:
            document.write(f, xml_declaration=True, encoding="UTF-8")
        debug("Wrote OPC Core Properties to docProps/core.xml")
    except Exception as e:
        error(f"Failed to write Core Properties: {e}")
