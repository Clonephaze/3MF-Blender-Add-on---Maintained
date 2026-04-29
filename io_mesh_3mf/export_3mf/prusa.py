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
        combined_id,
        part_info_list: list,
    ) -> bytes:
        """Generate Slic3r_PE_model.config with a single object and one volume per part.

        PrusaSlicer reads per-volume extruder assignments from this config file.
        All volumes are grouped under one <object> matching the single build item,
        which avoids the "Multi-part object detected" dialog.

        :param combined_id: Resource ID of the combined mesh object, or None.
        :param part_info_list: List of dicts with keys: name, extruder, firstid, lastid.
        :return: UTF-8 encoded XML bytes ready to write into the archive.
        """
        config_root = xml.etree.ElementTree.Element("config")

        if combined_id is None or not part_info_list:
            return xml.etree.ElementTree.tostring(
                config_root, encoding="UTF-8", xml_declaration=True
            )

        obj_elem = xml.etree.ElementTree.SubElement(config_root, "object")
        obj_elem.set("id", str(combined_id))
        obj_elem.set("instances_count", "1")

        xml.etree.ElementTree.SubElement(
            obj_elem, "metadata",
            {"type": "object", "key": "name", "value": part_info_list[0]["name"]},
        )
        xml.etree.ElementTree.SubElement(
            obj_elem, "metadata",
            {"type": "object", "key": "extruder", "value": "0"},
        )

        for part in part_info_list:
            vol_elem = xml.etree.ElementTree.SubElement(obj_elem, "volume")
            vol_elem.set("firstid", str(part["firstid"]))
            vol_elem.set("lastid", str(part["lastid"]))

            for key, val in (
                ("name", part["name"]),
                ("volume_type", "ModelPart"),
                ("extruder", str(part["extruder"])),
            ):
                xml.etree.ElementTree.SubElement(
                    vol_elem, "metadata",
                    {"type": "volume", "key": key, "value": val},
                )

            mesh_elem = xml.etree.ElementTree.SubElement(vol_elem, "mesh")
            for attr in (
                "edges_fixed", "degenerate_facets", "facets_removed",
                "facets_reversed", "backwards_edges",
            ):
                mesh_elem.set(attr, "0")

            debug(
                f"  [model_config] volume '{part['name']}': "
                f"firstid={part['firstid']}, lastid={part['lastid']}, "
                f"extruder={part['extruder']}"
            )

        return xml.etree.ElementTree.tostring(
            config_root, encoding="UTF-8", xml_declaration=True
        )

    def _write_prusa_combined_objects(
        self,
        std_exporter,
        root: xml.etree.ElementTree.Element,
        resources_element: xml.etree.ElementTree.Element,
        mesh_objects: list,
        global_scale: float,
    ) -> tuple:
        """Write all mesh objects as a single combined mesh for PrusaSlicer.

        Creates one <object> resource with all vertices and triangles merged
        (transformed to world space), and one <build><item> referencing it.
        PrusaSlicer requires a single build item to avoid the "Multi-part object
        detected" dialog and to preserve the Z-position of each part.

        :param std_exporter: A StandardExporter instance used for PAINT
            segmentation extraction.
        :param root: The root <model> element.
        :param resources_element: The <resources> element.
        :param mesh_objects: Flat list of Blender MESH objects to export.
        :param global_scale: Uniform scale factor applied to all coordinates.
        :return: (combined_id, part_info_list) where combined_id is the resource
            ID of the combined object (or None if nothing was written), and
            part_info_list is a list of dicts with keys:
            name, extruder, firstid, lastid.
        """
        import mathutils

        ctx = self.ctx
        prec = ctx.options.coordinate_precision

        combined_vertices = []   # (x, y, z) tuples in world space
        combined_triangles = []  # (v1, v2, v3, seg_string_or_None)
        part_info_list = []

        vertex_offset = 0
        triangle_offset = 0

        for blender_object in mesh_objects:
            obj_name = str(blender_object.name)
            original_object = blender_object

            if ctx.options.use_mesh_modifiers:
                dep_graph = bpy.context.evaluated_depsgraph_get()
                eval_object = blender_object.evaluated_get(dep_graph)
            else:
                eval_object = blender_object

            try:
                mesh = eval_object.to_mesh()
            except RuntimeError as e:
                debug(f"  [prusa_combined] '{obj_name}': to_mesh() failed: {e}")
                continue

            if mesh is None:
                debug(f"  [prusa_combined] '{obj_name}': to_mesh() returned None, skipping")
                continue

            mesh.calc_loop_triangles()
            loop_tris = mesh.loop_triangles
            num_tris = len(loop_tris)

            if num_tris == 0:
                eval_object.to_mesh_clear()
                debug(f"  [prusa_combined] '{obj_name}': no triangles, skipping")
                continue

            # Extract segmentation strings for PAINT mode
            seg_strings = {}
            if ctx.options.use_orca_format == "PAINT" and mesh.uv_layers.active:
                try:
                    seg_strings = std_exporter._extract_segmentation(
                        original_object, eval_object, mesh
                    )
                except Exception as e:
                    debug(
                        f"  [prusa_combined] Segmentation extraction failed "
                        f"for '{obj_name}': {e}"
                    )

            # Apply world transform + global_scale to get world-space vertices.
            world_matrix = blender_object.matrix_world
            scale_matrix = mathutils.Matrix.Scale(global_scale, 4)
            transform = scale_matrix @ world_matrix

            for v in mesh.vertices:
                co = transform @ v.co
                combined_vertices.append((
                    round(co.x, prec),
                    round(co.y, prec),
                    round(co.z, prec),
                ))

            # Add triangles with cumulative vertex offset; carry segmentation.
            for tri_idx, tri in enumerate(loop_tris):
                v1 = tri.vertices[0] + vertex_offset
                v2 = tri.vertices[1] + vertex_offset
                v3 = tri.vertices[2] + vertex_offset
                combined_triangles.append((v1, v2, v3, seg_strings.get(tri_idx)))

            # Determine extruder from the object's primary material.
            extruder = 1
            for slot in blender_object.material_slots:
                if slot.material:
                    hex_color = material_to_hex_color(slot.material)
                    if hex_color and hex_color in ctx.vertex_colors:
                        extruder = ctx.vertex_colors[hex_color]
                        break

            part_info_list.append({
                "name": obj_name,
                "extruder": extruder,
                "firstid": triangle_offset,
                "lastid": triangle_offset + num_tris - 1,
            })

            debug(
                f"  [prusa_combined] '{obj_name}': {len(mesh.vertices)} verts, "
                f"{num_tris} tris, extruder={extruder}"
            )

            vertex_offset += len(mesh.vertices)
            triangle_offset += num_tris
            eval_object.to_mesh_clear()

        # Always write the build element.
        build_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}build"
        )

        if not combined_vertices:
            warn("No mesh data found for Prusa combined export")
            return (None, [])

        # Assign a new resource ID for the combined object.
        combined_id = ctx.next_resource_id
        ctx.next_resource_id += 1

        # Write combined <object> with merged mesh.
        obj_elem = xml.etree.ElementTree.SubElement(
            resources_element, f"{{{MODEL_NAMESPACE}}}object"
        )
        obj_elem.set("id", str(combined_id))
        obj_elem.set("type", "model")

        mesh_elem = xml.etree.ElementTree.SubElement(
            obj_elem, f"{{{MODEL_NAMESPACE}}}mesh"
        )

        vertices_elem = xml.etree.ElementTree.SubElement(
            mesh_elem, f"{{{MODEL_NAMESPACE}}}vertices"
        )
        for (x, y, z) in combined_vertices:
            v_elem = xml.etree.ElementTree.SubElement(
                vertices_elem, f"{{{MODEL_NAMESPACE}}}vertex"
            )
            v_elem.set("x", str(x))
            v_elem.set("y", str(y))
            v_elem.set("z", str(z))

        SLIC3R_NS = "http://schemas.slic3r.org/3mf/2017/06"
        triangles_elem = xml.etree.ElementTree.SubElement(
            mesh_elem, f"{{{MODEL_NAMESPACE}}}triangles"
        )
        for (v1, v2, v3, seg) in combined_triangles:
            t_elem = xml.etree.ElementTree.SubElement(
                triangles_elem, f"{{{MODEL_NAMESPACE}}}triangle"
            )
            t_elem.set("v1", str(v1))
            t_elem.set("v2", str(v2))
            t_elem.set("v3", str(v3))
            if seg:
                t_elem.set(f"{{{SLIC3R_NS}}}mmu_segmentation", seg)

        # Single build item — no transform since vertices are already world-space.
        item_elem = xml.etree.ElementTree.SubElement(
            build_element, f"{{{MODEL_NAMESPACE}}}item"
        )
        item_elem.set("objectid", str(combined_id))

        ctx.num_written = len(part_info_list)

        debug(
            f"  [prusa_combined] Combined object id={combined_id}: "
            f"{len(combined_vertices)} vertices, {len(combined_triangles)} triangles, "
            f"{len(part_info_list)} parts"
        )

        return (combined_id, part_info_list)

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

        # Write all mesh objects as a single combined mesh object with one build
        # item, so PrusaSlicer does not show the "Multi-part object detected" dialog.
        std_exporter = StandardExporter(ctx)
        combined_id, part_info_list = self._write_prusa_combined_objects(
            std_exporter, root, resources_element, mesh_objects, global_scale
        )

        # Write filament colors to metadata for round-trip import
        write_prusa_filament_colors(archive, ctx.vertex_colors)

        # Write back stashed or profile PrusaSlicer config files.
        # Slic3r_PE_model.config is always regenerated so object IDs and
        # extruder assignments match the current combined-mesh export.
        for config_path in ("Metadata/Slic3r_PE.config", "Metadata/Slic3r_PE_model.config"):
            if config_path == "Metadata/Slic3r_PE_model.config":
                generated = self._generate_model_config(combined_id, part_info_list)
                with archive.open(config_path, "w") as f:
                    f.write(generated)
                debug("Generated Slic3r_PE_model.config from exported parts")
                continue

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
