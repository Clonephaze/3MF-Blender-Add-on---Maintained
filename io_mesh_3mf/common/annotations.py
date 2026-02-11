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

"""
OPC packaging annotations for 3MF archives.

Manages relationships (``.rels`` files) and content types
(``[Content_Types].xml``) that form the OPC (Open Packaging Conventions)
layer of a 3MF file.
"""

import collections
import json
import os.path
import urllib.parse
import xml.etree.ElementTree
from typing import Dict, Set, IO
import zipfile

import bpy  # To store annotations in Blender scene text blocks

from .logging import warn
from .constants import (
    RELS_FOLDER,
    RELS_RELATIONSHIP_FIND,
    RELS_NAMESPACES,
    MODEL_REL,
    THUMBNAIL_REL,
    TEXTURE_REL,
    RELS_MIMETYPE,
    MODEL_MIMETYPE,
    RELS_NAMESPACE,
    MODEL_LOCATION,
    CONTENT_TYPES_NAMESPACE,
    CONTENT_TYPES_LOCATION,
    CORE_PROPERTIES_LOCATION,
    CORE_PROPERTIES_REL,
    CORE_PROPERTIES_MIMETYPE,
)

# Annotation types
Relationship = collections.namedtuple("Relationship", ["namespace", "source"])
ContentType = collections.namedtuple("ContentType", ["mime_type"])

# Sentinel for conflicting content types from multiple archives
ConflictingContentType = object()

ANNOTATION_FILE = ".3mf_annotations"

__all__ = [
    "Annotations",
    "Relationship",
    "ContentType",
    "ConflictingContentType",
]


class Annotations:
    """
    Collection of OPC annotations for a 3MF document.

    Tracks relationships and content types for files in the archive.
    Supports serialisation to/from Blender scene text blocks for persistence.
    """

    def __init__(self):
        self.annotations = {}

    def add_rels(self, rels_file: IO[bytes]) -> None:
        """Add relationships from a ``.rels`` file stream."""
        base_path = f"{os.path.dirname(rels_file.name)}/"
        if os.path.basename(os.path.dirname(base_path)) == RELS_FOLDER:
            base_path = f"{os.path.dirname(os.path.dirname(base_path))}/"

        try:
            root = xml.etree.ElementTree.ElementTree(file=rels_file)
        except xml.etree.ElementTree.ParseError as e:
            warn(
                f"Relationship file {rels_file.name} has malformed XML (position {e.position[0]}:{e.position[1]})."
            )
            return

        for relationship_node in root.iterfind(RELS_RELATIONSHIP_FIND, RELS_NAMESPACES):
            try:
                target = relationship_node.attrib["Target"]
                namespace = relationship_node.attrib["Type"]
            except KeyError as e:
                warn(f"Relationship missing attribute: {str(e)}")
                continue
            if namespace == MODEL_REL:
                continue
            if namespace == TEXTURE_REL:
                continue

            target = urllib.parse.urljoin(base_path, target)
            if target != "" and target[0] == "/":
                target = target[1:]

            if target not in self.annotations:
                self.annotations[target] = set()
            self.annotations[target].add(
                Relationship(namespace=namespace, source=base_path)
            )

    def add_content_types(
        self, files_by_content_type: Dict[str, Set[IO[bytes]]]
    ) -> None:
        """Add content type annotations from an archive's file classification."""
        for content_type, file_set in files_by_content_type.items():
            if content_type == "":
                continue
            if content_type in {RELS_MIMETYPE, MODEL_MIMETYPE}:
                continue
            for file in file_set:
                filename = file.name
                if filename not in self.annotations:
                    self.annotations[filename] = set()
                if ConflictingContentType in self.annotations[filename]:
                    continue
                content_type_annotations = list(
                    filter(
                        lambda annotation: type(annotation) is ContentType,
                        self.annotations[filename],
                    )
                )
                if (
                    any(content_type_annotations)
                    and content_type_annotations[0].mime_type != content_type
                ):
                    warn(f"Found conflicting content types for file: {filename}")
                    for annotation in content_type_annotations:
                        self.annotations[filename].remove(annotation)
                    self.annotations[filename].add(ConflictingContentType)
                else:
                    self.annotations[filename].add(ContentType(content_type))

    def write_rels(self, archive: zipfile.ZipFile) -> None:
        """Write relationship annotations to the archive as ``.rels`` files."""
        current_id = 0
        rels_by_source = {"/": set()}

        for target, annotations in self.annotations.items():
            for annotation in annotations:
                if type(annotation) is not Relationship:
                    continue
                if annotation.source not in rels_by_source:
                    rels_by_source[annotation.source] = set()
                rels_by_source[annotation.source].add((target, annotation.namespace))

        for source, annotations in rels_by_source.items():
            if source == "/":
                source = ""
            root = xml.etree.ElementTree.Element(f"{{{RELS_NAMESPACE}}}Relationships")
            for target, namespace in annotations:
                xml.etree.ElementTree.SubElement(
                    root,
                    f"{{{RELS_NAMESPACE}}}Relationship",
                    attrib={
                        f"{{{RELS_NAMESPACE}}}Id": f"rel{current_id}",
                        f"{{{RELS_NAMESPACE}}}Target": f"/{target}",
                        f"{{{RELS_NAMESPACE}}}Type": namespace,
                    },
                )
                current_id += 1

            if source == "":
                xml.etree.ElementTree.SubElement(
                    root,
                    f"{{{RELS_NAMESPACE}}}Relationship",
                    attrib={
                        f"{{{RELS_NAMESPACE}}}Id": f"rel{current_id}",
                        f"{{{RELS_NAMESPACE}}}Target": f"/{MODEL_LOCATION}",
                        f"{{{RELS_NAMESPACE}}}Type": MODEL_REL,
                    },
                )
                current_id += 1
                xml.etree.ElementTree.SubElement(
                    root,
                    f"{{{RELS_NAMESPACE}}}Relationship",
                    attrib={
                        f"{{{RELS_NAMESPACE}}}Id": f"rel{current_id}",
                        f"{{{RELS_NAMESPACE}}}Target": f"/{CORE_PROPERTIES_LOCATION}",
                        f"{{{RELS_NAMESPACE}}}Type": CORE_PROPERTIES_REL,
                    },
                )
                current_id += 1
                xml.etree.ElementTree.SubElement(
                    root,
                    f"{{{RELS_NAMESPACE}}}Relationship",
                    attrib={
                        f"{{{RELS_NAMESPACE}}}Id": f"rel{current_id}",
                        f"{{{RELS_NAMESPACE}}}Target": "/Metadata/thumbnail.png",
                        f"{{{RELS_NAMESPACE}}}Type": THUMBNAIL_REL,
                    },
                )
                current_id += 1

            document = xml.etree.ElementTree.ElementTree(root)
            rels_file = f"{source}{RELS_FOLDER}/.rels"
            with archive.open(rels_file, "w") as f:
                document.write(
                    f,
                    xml_declaration=True,
                    encoding="UTF-8",
                    default_namespace=RELS_NAMESPACE,
                )

    def write_content_types(self, archive: zipfile.ZipFile) -> None:
        """Write ``[Content_Types].xml`` to the archive."""
        content_types_by_extension = {}
        for target, annotations in self.annotations.items():
            for annotation in annotations:
                if type(annotation) is not ContentType:
                    continue
                extension = os.path.splitext(target)[1]
                if extension not in content_types_by_extension:
                    content_types_by_extension[extension] = []
                content_types_by_extension[extension].append(annotation.mime_type)

        most_common = {}
        for extension, mime_types in content_types_by_extension.items():
            counter = collections.Counter(mime_types)
            most_common[extension] = counter.most_common(1)[0][0]

        most_common[".rels"] = RELS_MIMETYPE
        most_common[".model"] = MODEL_MIMETYPE
        most_common[".config"] = "application/xml"
        most_common[".xml"] = CORE_PROPERTIES_MIMETYPE
        most_common[".png"] = "image/png"

        root = xml.etree.ElementTree.Element(f"{{{CONTENT_TYPES_NAMESPACE}}}Types")
        for extension, mime_type in most_common.items():
            if not extension:
                continue
            xml.etree.ElementTree.SubElement(
                root,
                f"{{{CONTENT_TYPES_NAMESPACE}}}Default",
                attrib={
                    f"{{{CONTENT_TYPES_NAMESPACE}}}Extension": extension[1:],
                    f"{{{CONTENT_TYPES_NAMESPACE}}}ContentType": mime_type,
                },
            )

        for target, annotations in self.annotations.items():
            for annotation in annotations:
                if type(annotation) is not ContentType:
                    continue
                extension = os.path.splitext(target)[1]
                if not extension or annotation.mime_type != most_common[extension]:
                    xml.etree.ElementTree.SubElement(
                        root,
                        f"{{{CONTENT_TYPES_NAMESPACE}}}Override",
                        attrib={
                            f"{{{CONTENT_TYPES_NAMESPACE}}}PartName": f"/{target}",
                            f"{{{CONTENT_TYPES_NAMESPACE}}}ContentType": annotation.mime_type,
                        },
                    )

        document = xml.etree.ElementTree.ElementTree(root)
        with archive.open(CONTENT_TYPES_LOCATION, "w") as f:
            document.write(
                f,
                xml_declaration=True,
                encoding="UTF-8",
                default_namespace=CONTENT_TYPES_NAMESPACE,
            )

    def store(self) -> None:
        """Serialize and store annotations in a Blender scene text block."""
        document = {}
        for target, annotations in self.annotations.items():
            serialized_annotations = []
            for annotation in annotations:
                if type(annotation) is Relationship:
                    serialized_annotations.append(
                        {
                            "annotation": "relationship",
                            "namespace": annotation.namespace,
                            "source": annotation.source,
                        }
                    )
                elif type(annotation) is ContentType:
                    serialized_annotations.append(
                        {
                            "annotation": "content_type",
                            "mime_type": annotation.mime_type,
                        }
                    )
                elif annotation == ConflictingContentType:
                    serialized_annotations.append(
                        {"annotation": "content_type_conflict"}
                    )
            document[target] = serialized_annotations

        if ANNOTATION_FILE in bpy.data.texts:
            bpy.data.texts.remove(bpy.data.texts[ANNOTATION_FILE])
        text_file = bpy.data.texts.new(ANNOTATION_FILE)
        text_file.write(json.dumps(document))

    def retrieve(self) -> None:
        """Restore annotations from a Blender scene text block."""
        self.annotations.clear()

        if ANNOTATION_FILE not in bpy.data.texts:
            return
        try:
            annotation_data = json.loads(bpy.data.texts[ANNOTATION_FILE].as_string())
        except json.JSONDecodeError:
            warn("Annotation file exists, but is not properly formatted.")
            return

        for target, annotations in annotation_data.items():
            self.annotations[target] = set()
            try:
                for annotation in annotations:
                    if annotation["annotation"] == "relationship":
                        self.annotations[target].add(
                            Relationship(
                                namespace=annotation["namespace"],
                                source=annotation["source"],
                            )
                        )
                    elif annotation["annotation"] == "content_type":
                        self.annotations[target].add(
                            ContentType(mime_type=annotation["mime_type"])
                        )
                    elif annotation["annotation"] == "content_type_conflict":
                        self.annotations[target].add(ConflictingContentType)
                    else:
                        warn(
                            f'Unknown annotation type "{annotation["annotation"]}" encountered.'
                        )
                        continue
            except TypeError:
                warn(f'Annotation for target "{target}" is not properly structured.')
            except KeyError as e:
                warn(f'Annotation for target "{target}" missing key: {str(e)}')
            if not self.annotations[target]:
                del self.annotations[target]
