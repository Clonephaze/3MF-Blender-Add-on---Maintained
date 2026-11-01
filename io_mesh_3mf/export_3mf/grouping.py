"""
Assembly resolution for Orca/Bambu export.

Decides which mesh objects form a slicer assembly (one ``<object>`` with
several ``<part>`` entries).  Pure Python and duck-typed (objects only need
``name``, ``type``, ``children`` and ``get``), so it is unit-testable without
Blender.

Precedence, highest first:

1. The ``3mf_assembly`` custom property on a mesh object.  All objects that
   share the same non-empty string value form one assembly, named by that
   value.  This lets scripts and other addons define assemblies without
   creating Empties.
2. A parent Empty with children (the original behavior).  Each such Empty is
   one assembly, named by the Empty.  The Empty must be part of the export
   (in the input objects or under one of them); selecting only an assembly's
   children exports them as standalone objects.
3. Otherwise the object is standalone.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

#: Custom property that assigns an object to a named assembly.
ASSEMBLY_PROPERTY = "3mf_assembly"

_PROP_KEY_PREFIX = "prop:"
_EMPTY_KEY_PREFIX = "empty:"


@dataclass
class AssemblyGroup:
    """One resolved assembly.  *key* is unique across Empty and property groups."""

    key: str
    name: str
    empty: Optional[object] = None  # source Empty, if the group came from one


@dataclass
class ResolvedAssemblies:
    groups: List[AssemblyGroup] = field(default_factory=list)
    mesh_to_key: Dict[str, str] = field(default_factory=dict)


def _property_value(obj) -> Optional[str]:
    """Return the assembly name from the custom property, or None."""
    value = obj.get(ASSEMBLY_PROPERTY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def resolve_assembly_groups(
    blender_objects: Iterable, mesh_objects: Iterable
) -> ResolvedAssemblies:
    """
    Resolve assemblies for an export.

    :param blender_objects: Objects being exported (Empties are looked up here).
    :param mesh_objects: The mesh objects that will actually be written.
    :return: Groups in stable order (Empty groups first, in input order, then
        property groups in first-seen order) and a mesh-name -> group-key map.
    """
    mesh_objects = list(mesh_objects)
    result = ResolvedAssemblies()
    empty_groups: Dict[str, AssemblyGroup] = {}

    def add_empty(obj) -> AssemblyGroup:
        name = str(obj.name)
        key = _EMPTY_KEY_PREFIX + name
        if key not in empty_groups:
            empty_groups[key] = AssemblyGroup(key=key, name=name, empty=obj)
            result.groups.append(empty_groups[key])
        return empty_groups[key]

    # 1. Parent Empties (original behavior).  Only Empties that are themselves
    # exported count: the input objects plus their descendants, since a
    # selected parent exports its whole subtree.
    blender_objects = list(blender_objects)
    exported = set()
    pending = list(blender_objects)
    while pending:
        obj = pending.pop()
        if obj.name in exported:
            continue
        exported.add(obj.name)
        pending.extend(obj.children)

    for obj in blender_objects:
        if obj.type == "EMPTY" and obj.children:
            key = add_empty(obj).key
            for child in obj.children:
                if child.type == "MESH":
                    result.mesh_to_key[child.name] = key

    # Nested Empties under an exported parent are not in the input list.
    for obj in mesh_objects:
        parent = getattr(obj, "parent", None)
        while parent is not None:
            if parent.type == "EMPTY":
                if parent.name in exported:
                    result.mesh_to_key[obj.name] = add_empty(parent).key
                break
            parent = parent.parent

    # 2. Property-defined assemblies override Empty parenting
    prop_groups: Dict[str, AssemblyGroup] = {}
    for obj in mesh_objects:
        value = _property_value(obj)
        if value is None:
            continue
        key = _PROP_KEY_PREFIX + value
        if key not in prop_groups:
            prop_groups[key] = AssemblyGroup(key=key, name=value)
        result.mesh_to_key[obj.name] = key
    result.groups.extend(prop_groups.values())

    return result
