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
Metadata storage for 3MF objects and scenes.

Tracks metadata entries with conflict resolution — if the same key appears
with different values across multiple 3MF files, that entry is marked
conflicting and excluded from output.
"""

import collections
from typing import Iterator, Union

import bpy.types
import idprop.types

MetadataEntry = collections.namedtuple(
    "MetadataEntry", ["name", "preserve", "datatype", "value"]
)

__all__ = [
    "Metadata",
    "MetadataEntry",
]


class Metadata:
    """
    Tracks metadata of a Blender object for 3MF round-trip.

    Behaves like a dictionary keyed by metadata name.  Storing the same key
    with a different value marks that entry as conflicting (``None``), so only
    the intersection of consistent metadata survives a multi-file import.
    """

    def __init__(self):
        self.metadata = {}

    def __setitem__(self, key: str, value: MetadataEntry) -> None:
        if key not in self.metadata:
            self.metadata[key] = value
            return

        if self.metadata[key] is None:
            return

        competing = self.metadata[key]
        if value.value != competing.value or value.datatype != competing.datatype:
            self.metadata[key] = None
            return

        if not competing.preserve and value.preserve:
            self.metadata[key] = MetadataEntry(
                name=key,
                preserve=True,
                datatype=competing.datatype,
                value=competing.value,
            )

    def __getitem__(self, key: str) -> MetadataEntry:
        if key not in self.metadata or self.metadata[key] is None:
            raise KeyError(key)
        return self.metadata[key]

    def __contains__(self, item: str) -> bool:
        return item in self.metadata and self.metadata[item] is not None

    def __bool__(self) -> bool:
        return any(self.values())

    def __len__(self) -> int:
        return sum(1 for _ in self.values())

    def __delitem__(self, key: str) -> None:
        if key in self.metadata:
            del self.metadata[key]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Metadata):
            return NotImplemented
        return self.metadata == other.metadata

    def store(self, blender_object: Union[bpy.types.Object, bpy.types.Scene]) -> None:
        """Store this metadata in a Blender object as custom properties."""
        for metadata_entry in self.values():
            name = str(metadata_entry.name)
            value = (
                str(metadata_entry.value) if metadata_entry.value is not None else ""
            )
            if name == "Title":
                blender_object.name = value
            elif name == "3mf:partnumber":
                blender_object[name] = value
            else:
                datatype = (
                    str(metadata_entry.datatype)
                    if metadata_entry.datatype is not None
                    else ""
                )
                blender_object[name] = {
                    "datatype": datatype,
                    "preserve": metadata_entry.preserve,
                    "value": value,
                }

    def retrieve(
        self, blender_object: Union[bpy.types.Object, bpy.types.Scene]
    ) -> None:
        """Retrieve metadata from a Blender object's custom properties."""
        for key in blender_object.keys():
            cached_key = str(key)
            entry = blender_object[key]
            if cached_key == "3mf:partnumber":
                cached_entry = str(entry)
                self[cached_key] = MetadataEntry(
                    name=cached_key,
                    preserve=True,
                    datatype="xs:string",
                    value=cached_entry,
                )
                continue
            if (
                isinstance(entry, idprop.types.IDPropertyGroup)
                and "datatype" in entry.keys()
                and "preserve" in entry.keys()
                and "value" in entry.keys()
            ):
                self[key] = MetadataEntry(
                    name=key,
                    preserve=entry.get("preserve"),
                    datatype=entry.get("datatype"),
                    value=entry.get("value"),
                )

        self["Title"] = MetadataEntry(
            name="Title",
            preserve=True,
            datatype="xs:string",
            value=blender_object.name,
        )

    def values(self) -> Iterator[MetadataEntry]:
        """Yield all non-conflicting metadata entries."""
        yield from filter(lambda entry: entry is not None, self.metadata.values())
