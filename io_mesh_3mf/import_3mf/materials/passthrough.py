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
Passthrough material import functionality for 3MF Materials Extension.

This module handles round-trip support for:
- Composite materials (material mixtures)
- Multiproperties (layered property combinations)
- Storing passthrough data in Blender scene for export
"""

import json
from typing import Dict

import bpy

from ...common import debug, warn

from ...common.types import (
    ResourceComposite,
    ResourceMultiproperties,
)


def read_composite_materials(op, root, material_ns: Dict[str, str]) -> None:
    """
    Parse <m:compositematerials> elements for round-trip support.

    Composite materials define mixtures of base materials with specified ratios.

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    """
    from ...common.constants import MODEL_NAMESPACES

    for composite_item in root.iterfind("./3mf:resources/m:compositematerials", {**MODEL_NAMESPACES, **material_ns}):
        try:
            composite_id = composite_item.attrib["id"]
        except KeyError:
            warn("Encountered a compositematerials without resource ID.")
            continue

        if composite_id in op.resource_composites:
            warn(f"Duplicate compositematerials ID: {composite_id}")
            continue

        try:
            matid = composite_item.attrib["matid"]
            matindices = composite_item.attrib["matindices"]
        except KeyError as e:
            warn(f"Compositematerials {composite_id} missing required attribute: {e}")
            continue

        display_props_id = composite_item.attrib.get("displaypropertiesid")

        composites = []
        for comp_item in composite_item.iterfind("./m:composite", material_ns):
            values = comp_item.attrib.get("values", "")
            composites.append({"values": values})

        op.resource_composites[composite_id] = ResourceComposite(
            matid=matid,
            matindices=matindices,
            displaypropertiesid=display_props_id,
            composites=composites,
        )
        debug(f"Parsed compositematerials {composite_id}: {len(composites)} composites")

    if op.resource_composites:
        debug(f"Found {len(op.resource_composites)} compositematerials resources (passthrough)")


def read_multiproperties(op, root, material_ns: Dict[str, str]) -> None:
    """
    Parse <m:multiproperties> elements for round-trip support.

    Multiproperties define layered property combinations with blend modes.

    :param op: The Import3MF operator instance.
    :param root: XML root element.
    :param material_ns: Namespace dict for materials extension.
    """
    from ...common.constants import MODEL_NAMESPACES

    for multi_item in root.iterfind("./3mf:resources/m:multiproperties", {**MODEL_NAMESPACES, **material_ns}):
        try:
            multi_id = multi_item.attrib["id"]
        except KeyError:
            warn("Encountered a multiproperties without resource ID.")
            continue

        if multi_id in op.resource_multiproperties:
            warn(f"Duplicate multiproperties ID: {multi_id}")
            continue

        try:
            pids = multi_item.attrib["pids"]
        except KeyError:
            warn(f"Multiproperties {multi_id} missing required pids attribute")
            continue

        blendmethods = multi_item.attrib.get("blendmethods")

        multis = []
        # Note: <multi> elements are in the core namespace, not materials namespace
        for m_item in multi_item.iterfind("./3mf:multi", {**MODEL_NAMESPACES, **material_ns}):
            pindices = m_item.attrib.get("pindices", "")
            multis.append({"pindices": pindices})

        op.resource_multiproperties[multi_id] = ResourceMultiproperties(
            pids=pids, blendmethods=blendmethods, multis=multis
        )
        debug(f"Parsed multiproperties {multi_id}: {len(multis)} multi entries")

    if op.resource_multiproperties:
        debug(f"Found {len(op.resource_multiproperties)} multiproperties resources (passthrough)")


def store_passthrough_materials(op) -> None:
    """
    Store imported passthrough material data in the Blender scene.

    This preserves imported 3MF Materials Extension data that Blender can't
    natively represent, allowing it to be re-exported correctly.

    Stored as JSON in scene custom properties:
    - 3mf_colorgroups: Colorgroup definitions
    - 3mf_composites: Composite material definitions
    - 3mf_multiproperties: Multiproperties definitions
    - 3mf_textures: Texture2d metadata
    - 3mf_texture_groups: Texture2dgroup definitions
    - 3mf_pbr_display_props: Non-textured PBR display properties
    - 3mf_pbr_texture_displays: Textured PBR display properties

    :param op: The Import3MF operator instance with parsed material data.
    """
    scene = bpy.context.scene

    # Store colorgroups
    if op.resource_colorgroups:
        colorgroups_data = {}
        for cg_id, cg in op.resource_colorgroups.items():
            colorgroups_data[cg_id] = {
                "colors": cg.colors,
                "displaypropertiesid": cg.displaypropertiesid,
            }
        scene["3mf_colorgroups"] = json.dumps(colorgroups_data)
        debug(f"Stored {len(colorgroups_data)} colorgroups for round-trip export")

    # Store composite materials
    if op.resource_composites:
        composites_data = {}
        for comp_id, comp in op.resource_composites.items():
            composites_data[comp_id] = {
                "matid": comp.matid,
                "matindices": comp.matindices,
                "displaypropertiesid": comp.displaypropertiesid,
                "composites": comp.composites,
            }
        scene["3mf_compositematerials"] = json.dumps(composites_data)
        debug(f"Stored {len(composites_data)} compositematerials for round-trip export")

    # Store multiproperties
    if op.resource_multiproperties:
        multiprops_data = {}
        for mp_id, mp in op.resource_multiproperties.items():
            multiprops_data[mp_id] = {
                "pids": mp.pids,
                "blendmethods": mp.blendmethods,
                "multis": mp.multis,
            }
        scene["3mf_multiproperties"] = json.dumps(multiprops_data)
        debug(f"Stored {len(multiprops_data)} multiproperties for round-trip export")

    # Store texture metadata
    if op.resource_textures:
        textures_data = {}
        for tex_id, tex in op.resource_textures.items():
            textures_data[tex_id] = {
                "path": tex.path,
                "contenttype": tex.contenttype,
                "tilestyleu": tex.tilestyleu,
                "tilestylev": tex.tilestylev,
                "filter": tex.filter,
                "blender_image": tex.blender_image.name if tex.blender_image else None,
            }
        scene["3mf_textures"] = json.dumps(textures_data)
        debug(f"Stored {len(textures_data)} texture2d resources for round-trip export")

    # Store texture groups
    if op.resource_texture_groups:
        groups_data = {}
        for group_id, group in op.resource_texture_groups.items():
            groups_data[group_id] = {
                "texid": group.texid,
                "tex2coords": group.tex2coords,
                "displaypropertiesid": group.displaypropertiesid,
            }
        scene["3mf_texture_groups"] = json.dumps(groups_data)
        debug(f"Stored {len(groups_data)} texture2dgroup resources for round-trip export")

    # Store non-textured PBR display properties
    if op.resource_pbr_display_props:
        pbr_data = {}
        for props_id, props in op.resource_pbr_display_props.items():
            pbr_data[props_id] = {"type": props.type, "properties": props.properties}
        scene["3mf_pbr_display_props"] = json.dumps(pbr_data)
        debug(f"Stored {len(pbr_data)} PBR display properties for round-trip export")

    # Store textured PBR display properties
    if op.resource_pbr_texture_displays:
        tex_pbr_data = {}
        for props_id, props in op.resource_pbr_texture_displays.items():
            tex_pbr_data[props_id] = {
                "type": props.type,
                "name": props.name,
                "primary_texid": props.primary_texid,
                "secondary_texid": props.secondary_texid,
                "basecolor_texid": props.basecolor_texid,
                "factors": props.factors,
            }
        scene["3mf_pbr_texture_displays"] = json.dumps(tex_pbr_data)
        debug(f"Stored {len(tex_pbr_data)} textured PBR display properties for round-trip export")
