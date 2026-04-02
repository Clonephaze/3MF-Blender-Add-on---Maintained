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
PrusaSlicer 3MF exporter.

Uses slic3rpe:mmu_segmentation attributes for per-triangle multi-material
data, compatible with PrusaSlicer and SuperSlicer.
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree
import zipfile
from typing import Set

import bpy

from ..common.constants import MODEL_NAMESPACE, MODEL_LOCATION
from ..common.logging import debug, warn
from ..common.metadata import Metadata, MetadataEntry

from .archive import write_core_properties
from .geometry import write_metadata
from .materials import collect_face_colors, write_prusa_filament_colors
from .materials.base import material_to_hex_color
from .components import collect_mesh_objects
from .standard import BaseExporter, StandardExporter
from .thumbnail import write_thumbnail
from ..import_3mf.archive import get_stashed_config
from ..slicer_profiles import get_profile_config


class PrusaExporter(BaseExporter):
    """Exports PrusaSlicer compatible 3MF files with mmu_segmentation."""

    def _generate_model_config(
        self,
        resources_element: xml.etree.ElementTree.Element,
        mesh_objects: list,
    ) -> bytes:
        """Generate Slic3r_PE_model.config XML assigning extruders per object.

        PrusaSlicer reads per-object extruder assignments from this config file.
        Without it, every object defaults to extruder 1 regardless of material.

        :param resources_element: The written <resources> element, scanned for
            object IDs by name.
        :param mesh_objects: Flat list of Blender MESH objects that were exported.
        :return: UTF-8 encoded XML bytes ready to write into the archive.
        """
        ctx = self.ctx

        # Build name -> resource_id from the written <object> elements.
        name_to_id: dict = {}
        for child in resources_element:
            tag = child.tag.split("}")[1] if "}" in child.tag else child.tag
            if tag == "object":
                obj_id = child.get("id")
                obj_name = child.get("name")
                if obj_id and obj_name:
                    name_to_id[obj_name] = obj_id

        config_root = xml.etree.ElementTree.Element("config")

        for blender_object in mesh_objects:
            obj_name = str(blender_object.name)
            obj_id = name_to_id.get(obj_name)
            if obj_id is None:
                debug(f"  [model_config] No resource ID found for '{obj_name}', skipping")
                continue

            # Count triangles on the (optionally evaluated) mesh.
            try:
                if ctx.options.use_mesh_modifiers:
                    dep_graph = bpy.context.evaluated_depsgraph_get()
                    eval_obj = blender_object.evaluated_get(dep_graph)
                else:
                    eval_obj = blender_object
                mesh = eval_obj.to_mesh()
                if mesh is None:
                    continue
                mesh.calc_loop_triangles()
                num_triangles = len(mesh.loop_triangles)
                eval_obj.to_mesh_clear()
            except Exception as e:
                debug(f"  [model_config] Failed to get mesh for '{obj_name}': {e}")
                continue

            if num_triangles == 0:
                continue

            # Determine the extruder number from the object's primary material.
            extruder = 1
            for slot in blender_object.material_slots:
                if slot.material:
                    hex_color = material_to_hex_color(slot.material)
                    if hex_color and hex_color in ctx.vertex_colors:
                        extruder = ctx.vertex_colors[hex_color]
                        break

            obj_elem = xml.etree.ElementTree.SubElement(config_root, "object")
            obj_elem.set("id", obj_id)
            obj_elem.set("instances_count", "1")

            for key, val in (("name", obj_name), ("extruder", str(extruder))):
                xml.etree.ElementTree.SubElement(
                    obj_elem, "metadata",
                    {"type": "object", "key": key, "value": val},
                )

            vol_elem = xml.etree.ElementTree.SubElement(obj_elem, "volume")
            vol_elem.set("firstid", "0")
            vol_elem.set("lastid", str(num_triangles - 1))

            for key, val in (
                ("name", obj_name),
                ("volume_type", "ModelPart"),
                ("extruder", str(extruder)),
            ):
                xml.etree.ElementTree.SubElement(
                    vol_elem, "metadata",
                    {"type": "volume", "key": key, "value": val},
                )

            mesh_elem = xml.etree.ElementTree.SubElement(vol_elem, "mesh")
            for attr, val in (
                ("edges_fixed", "0"),
                ("degenerate_facets", "0"),
                ("facets_removed", "0"),
                ("facets_reversed", "0"),
                ("backwards_edges", "0"),
            ):
                mesh_elem.set(attr, val)

            debug(f"  [model_config] {obj_name} → object id={obj_id}, extruder={extruder}, tris={num_triangles}")

        return xml.etree.ElementTree.tostring(
            config_root, encoding="UTF-8", xml_declaration=True
        )

    def execute(
        self,
        context: bpy.types.Context,
        archive: zipfile.ZipFile,
        blender_objects,
        global_scale: float,
    ) -> Set[str]:
        """
        PrusaSlicer export with mmu_segmentation attributes.

        Uses single model file with slic3rpe:mmu_segmentation on painted triangles.
        """
        ctx = self.ctx

        # Register namespaces
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)
        xml.etree.ElementTree.register_namespace(
            "slic3rpe", "http://schemas.slic3r.org/3mf/2017/06"
        )

        # Collect face colors
        ctx.safe_report(
            {"INFO"}, "Collecting face colors for PrusaSlicer export..."
        )

        # For PAINT mode, collect colors from paint texture metadata instead of face materials
        paint_colors_collected = False
        mesh_objects = collect_mesh_objects(
            blender_objects,
            export_hidden=ctx.options.export_hidden,
            include_disabled=ctx.options.include_disabled,
        )
        for blender_object in mesh_objects:
            original_object = blender_object
            # Handle evaluated objects
            if hasattr(blender_object, "original"):
                original_object = blender_object.original

            original_mesh_data = original_object.data
            if (
                "3mf_is_paint_texture" in original_mesh_data
                and original_mesh_data["3mf_is_paint_texture"]
            ):
                if "3mf_paint_extruder_colors" in original_mesh_data:
                    try:
                        extruder_colors_hex = ast.literal_eval(
                            original_mesh_data["3mf_paint_extruder_colors"]
                        )
                        # Add all colors from this paint texture to vertex_colors
                        for idx, hex_color in extruder_colors_hex.items():
                            if hex_color not in ctx.vertex_colors:
                                ctx.vertex_colors[hex_color] = idx
                        paint_colors_collected = True
                        debug(
                            f"Collected {len(extruder_colors_hex)} colors from paint texture metadata"
                        )
                    except Exception as e:
                        warn(f"Failed to parse extruder colors from metadata: {e}")

        # If no paint colors found, fall back to face material colors
        if not paint_colors_collected:
            ctx.vertex_colors = collect_face_colors(
                blender_objects,
                ctx.options.use_mesh_modifiers,
                ctx.safe_report,
                export_hidden=ctx.options.export_hidden,
                include_disabled=ctx.options.include_disabled,
            )

        debug(f"PrusaSlicer mode enabled with {len(ctx.vertex_colors)} color zones")

        if len(ctx.vertex_colors) == 0:
            warn("No face colors found! Assign materials to faces for color zones.")
            ctx.safe_report(
                {"WARNING"},
                "No face colors detected. Assign different materials to faces in Edit mode.",
            )
        else:
            ctx.safe_report(
                {"INFO"},
                f"Detected {len(ctx.vertex_colors)} color zones for PrusaSlicer export",
            )

        # Create model root element
        root = xml.etree.ElementTree.Element(f"{{{MODEL_NAMESPACE}}}model")

        root.set("unit", "millimeter")
        root.set("{http://www.w3.org/XML/1998/namespace}lang", "en-US")

        # Add scene metadata first
        scene_metadata = Metadata()
        scene_metadata.retrieve(bpy.context.scene)

        # Add PrusaSlicer metadata if not already present in scene
        if "slic3rpe:Version3mf" not in scene_metadata:
            scene_metadata["slic3rpe:Version3mf"] = MetadataEntry(
                name="slic3rpe:Version3mf", preserve=False, datatype=None, value="1"
            )
        if "slic3rpe:MmPaintingVersion" not in scene_metadata:
            scene_metadata["slic3rpe:MmPaintingVersion"] = MetadataEntry(
                name="slic3rpe:MmPaintingVersion",
                preserve=False,
                datatype=None,
                value="1",
            )

        write_metadata(root, scene_metadata, ctx.options.use_orca_format)

        resources_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}resources"
        )

        # PrusaSlicer MMU painting doesn't use basematerials
        ctx.material_name_to_index = {}

        # Use StandardExporter's write_objects (reuse the logic)
        std_exporter = StandardExporter(ctx)
        std_exporter.write_objects(
            root, resources_element, blender_objects, global_scale
        )

        # Write filament colors to metadata for round-trip import
        write_prusa_filament_colors(archive, ctx.vertex_colors)

        # Write back stashed or profile PrusaSlicer config files.
        # For Slic3r_PE_model.config specifically, fall back to generating it
        # from the exported objects so per-object extruder assignments are
        # always written (not just when a prior Prusa import was round-tripped).
        for config_path in ("Metadata/Slic3r_PE.config", "Metadata/Slic3r_PE_model.config"):
            stashed = get_stashed_config(config_path)
            if stashed is None and ctx.options.slicer_profile != "NONE":
                stashed = get_profile_config(
                    ctx.options.slicer_profile, config_path,
                )
                if stashed is not None:
                    debug(
                        f"Using slicer profile "
                        f"'{ctx.options.slicer_profile}' "
                        f"for {config_path}"
                    )
            if stashed is not None:
                with archive.open(config_path, "w") as f:
                    f.write(stashed)
                debug(f"Wrote {config_path} to archive")
            elif config_path == "Metadata/Slic3r_PE_model.config":
                generated = self._generate_model_config(resources_element, mesh_objects)
                with archive.open(config_path, "w") as f:
                    f.write(generated)
                debug("Generated Slic3r_PE_model.config from object materials")

        document = xml.etree.ElementTree.ElementTree(root)
        with archive.open(MODEL_LOCATION, "w", force_zip64=True) as f:
            document.write(
                f,
                xml_declaration=True,
                encoding="UTF-8",
            )

        # Write OPC Core Properties
        write_core_properties(archive)

        # Write thumbnail
        write_thumbnail(archive, ctx, list(blender_objects))

        ctx._progress_update(100, "Finalizing export...")
        return ctx.finalize_export(archive, "PrusaSlicer-compatible ")
