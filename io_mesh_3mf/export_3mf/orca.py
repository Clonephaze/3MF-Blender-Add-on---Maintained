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
Orca Slicer / BambuStudio 3MF exporter.

Uses the Production Extension to create multi-file 3MF archives with
individual object model files and paint_color attributes for per-triangle
multi-material data.
"""

from __future__ import annotations

import ast
import datetime
import io
import json
import math
import os
import re
import time
import uuid
import xml.etree.ElementTree
import zipfile
from typing import List, Optional, Set

import bpy
import mathutils
import numpy as np

from ..common.colors import hex_to_rgb
from ..common.constants import (
    MODEL_NAMESPACE,
    MODEL_LOCATION,
    MODEL_REL,
    PRODUCTION_NAMESPACE,
    BAMBU_NAMESPACE,
    RELS_NAMESPACE,
)
from ..common.extensions import PRODUCTION_EXTENSION, ORCA_EXTENSION
from ..common.logging import debug, timing_debug, warn, error
from ..common.plates import (
    ASSEMBLE_DELTA_PROPERTY,
    MAX_PLATES,
    PLATE_NAME_PROPERTY,
    PLATE_PROPERTY,
    printable_area_size,
)
from ..common.xml import format_transformation
from ..common.units import scale_transform_translation

from .geometry import _raw_geometry_cache
from .materials import (
    ORCA_FILAMENT_CODES,
    collect_face_colors,
    get_triangle_color,
    material_to_hex_color,
)
from .components import collect_mesh_objects
from .grouping import resolve_assembly_groups
from .plate_layout import plate_origin
from .material_plates import plan_material_plates
from .plate_model import explicit_unit_plate, layout_plates, plate_property
from .segmentation import texture_to_segmentation
from .standard import BaseExporter, _stream_model_to_file
from .thumbnail import write_thumbnail
from ..import_3mf.archive import get_stashed_config
from ..slicer_profiles import get_profile_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_stashed_settings(
    parent_elem: xml.etree.ElementTree.Element,
    blender_object: bpy.types.Object,
    prop_name: str = "3mf_orca_settings",
) -> None:
    """Write stashed slicer setting overrides as ``<metadata>`` children.

    Reads a JSON dict from *blender_object[prop_name]* and writes each
    key/value pair as a ``<metadata key="..." value="..."/>`` element.

    :param parent_elem: The ``<object>`` or ``<part>`` XML element.
    :param blender_object: Blender object carrying the stashed settings.
    :param prop_name: Custom property name (default ``"3mf_orca_settings"``).
    """
    raw = blender_object.get(prop_name)
    if not raw:
        return
    try:
        settings = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(settings, dict):
        return
    for key, value in settings.items():
        xml.etree.ElementTree.SubElement(
            parent_elem, "metadata",
            key=str(key), value=str(value),
        )
    debug(
        f"Wrote {len(settings)} stashed overrides from "
        f"'{blender_object.name}'.{prop_name}"
    )


class OrcaExporter(BaseExporter):
    """Exports Orca Slicer compatible 3MF files using Production Extension."""

    def execute(
        self,
        context: bpy.types.Context,
        archive: zipfile.ZipFile,
        blender_objects,
        global_scale: float,
    ) -> Set[str]:
        """
        Orca Slicer export using Production Extension structure.

        Creates separate model files for each object with paint_color attributes,
        and a main model file with component references.
        """
        ctx = self.ctx

        # Activate Production Extension for Orca compatibility
        ctx.extension_manager.activate(PRODUCTION_EXTENSION.namespace)
        ctx.extension_manager.activate(ORCA_EXTENSION.namespace)
        debug("Activated Orca Slicer extensions: Production + BambuStudio")

        from .segmentation import clear_export_state_cache
        clear_export_state_cache()

        # Register namespaces
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)
        xml.etree.ElementTree.register_namespace("p", PRODUCTION_NAMESPACE)
        xml.etree.ElementTree.register_namespace("BambuStudio", BAMBU_NAMESPACE)

        # Collect face colors for Orca export
        ctx.safe_report({"INFO"}, "Collecting face colors for Orca export...")

        # Sync mixed filament definitions from UI collection if user has edited them.
        settings = getattr(context.scene, "mmu_paint", None)
        if settings and settings.has_mixed_filaments and settings.mixed_filaments:
            from ..common.mixed_filaments import MixedFilament, serialize_mixed_filament_definitions
            from ..common.colors import rgb_to_hex
            entries = []
            for item in settings.mixed_filaments:
                # Copy the already-computed display color from the UI item so
                # filament_colour gets accurate swatches without needing to
                # re-derive physical colors at this point in the pipeline.
                disp = rgb_to_hex(*item.display_color[:]) if item.display_color else ""
                entries.append(MixedFilament(
                    component_a=item.component_a,
                    component_b=item.component_b,
                    enabled=item.enabled,
                    deleted=item.deleted,
                    custom=True,
                    mix_b_percent=item.mix_b_percent,
                    distribution_mode=int(item.distribution_mode),
                    manual_pattern=item.manual_pattern,
                    stable_id=item.stable_id,
                    display_color=disp,
                ))
            ctx.mixed_filament_definitions_raw = serialize_mixed_filament_definitions(entries)
            debug(f"Synced {len(entries)} mixed filament entries from UI to raw string")

        # Read mixed filament definitions from scene custom property (set on import).
        if not ctx.mixed_filament_definitions_raw:
            scene_defs = context.scene.get("3mf_mixed_filament_definitions", "")
            if scene_defs:
                ctx.mixed_filament_definitions_raw = str(scene_defs)
                debug(f"Loaded mixed filament definitions from scene property ({len(scene_defs)} chars)")

        # For PAINT mode, collect colors from paint texture metadata instead of face materials
        paint_colors_collected = False
        # FullSpectrum "parts mode": each part carries its own extruder assignment via
        # 3mf_paint_default_extruder.  Only physical filament colors belong in
        # ctx.vertex_colors / filament_colour; virtual slot display colors must NOT be
        # added here (the slicer derives them from mixed_filament_definitions at runtime).
        is_fullspectrum_parts = bool(ctx.mixed_filament_definitions_raw)
        if ctx.options.use_orca_format == "PAINT":
            mesh_objs_for_paint = collect_mesh_objects(
                blender_objects,
                export_hidden=ctx.options.export_hidden,
                include_disabled=ctx.options.include_disabled,
            )
            for blender_object in mesh_objs_for_paint:
                original_object = blender_object
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
                            # For FullSpectrum parts mode, only record the physical
                            # filament colors (indices 0..num_physical-1) with 1-based
                            # extruder numbers.  Virtual display colors are excluded so
                            # that filament_colour stays at the physical-filament count.
                            if is_fullspectrum_parts:
                                num_physical = int(
                                    original_mesh_data.get(
                                        "3mf_num_physical_filaments",
                                        len(extruder_colors_hex),
                                    )
                                )
                                for idx, hex_color in extruder_colors_hex.items():
                                    if int(idx) < num_physical and hex_color not in ctx.vertex_colors:
                                        ctx.vertex_colors[hex_color] = int(idx) + 1
                                debug(
                                    f"FullSpectrum parts mode: collected {min(num_physical, len(extruder_colors_hex))} "
                                    f"physical colors (skipping virtual slots)"
                                )
                            else:
                                for idx, hex_color in extruder_colors_hex.items():
                                    if hex_color not in ctx.vertex_colors:
                                        ctx.vertex_colors[hex_color] = idx
                                debug(
                                    f"Collected {len(extruder_colors_hex)} colors from paint texture metadata"
                                )
                            paint_colors_collected = True
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

        debug(f"Orca mode enabled with {len(ctx.vertex_colors)} color zones")

        if len(ctx.vertex_colors) == 0:
            warn("No face colors found! Assign materials to faces for color zones.")
            ctx.safe_report(
                {"WARNING"},
                "No face colors detected. Assign different materials to faces in Edit mode.",
            )
        else:
            ctx.safe_report(
                {"INFO"},
                f"Detected {len(ctx.vertex_colors)} color zones for Orca export",
            )

        # Generate build UUID
        build_uuid = str(uuid.uuid4())

        mesh_objects = collect_mesh_objects(
            blender_objects,
            export_hidden=ctx.options.export_hidden,
            include_disabled=ctx.options.include_disabled,
        )

        if not mesh_objects:
            ctx.safe_report({"ERROR"}, "No mesh objects found to export!")
            archive.close()
            return {"CANCELLED"}

        # Resolve assemblies: ``3mf_assembly`` property, else parent Empties
        resolved = resolve_assembly_groups(blender_objects, mesh_objects)
        mesh_to_group = resolved.mesh_to_key
        debug(f"Detected {len(resolved.groups)} group(s) (Empties + properties)")

        # Write individual object model files
        self._mesh_centers = {}
        object_data = []

        total_mesh_objects = len(mesh_objects)
        for idx, blender_object in enumerate(mesh_objects):
            # Don't update progress here in PAINT mode - let segmentation callback handle it
            if ctx.options.use_orca_format != "PAINT":
                # Scale to 5–44% so we stay within the Geometry phase (phase 1).
                # Using the full 0–95 range caused later objects to bleed into
                # Materials/Segmentation/Thumbnail phases on the browser card.
                progress = 5 + int(((idx + 1) / total_mesh_objects) * 39)
                ctx._progress_update(
                    progress,
                    f"Exporting {idx + 1}/{total_mesh_objects}: {blender_object.name}",
                    phase=1,  # Geometry
                )
            object_counter = idx + 1
            wrapper_id = object_counter * 2
            mesh_id = object_counter * 2 - 1

            # Generate UUIDs
            wrapper_uuid = f"{object_counter:08x}-61cb-4c03-9d28-80fed5dfa1dc"
            mesh_uuid = f"{object_counter:04x}0000-81cb-4c03-9d28-80fed5dfa1dc"
            component_uuid = f"{object_counter:04x}0000-b206-40ff-9872-83e8017abed1"

            # Create safe filename
            safe_name = re.sub(r"[^\w\-.]", "_", blender_object.name)
            object_path = f"/3D/Objects/{safe_name}_{object_counter}.model"

            # Unit conversion is baked into vertices; only translation changes units.
            transformation = scale_transform_translation(
                blender_object.matrix_world, global_scale
            )

            # Write the individual object model file
            self.write_object_model(
                archive, blender_object, object_path, mesh_id, mesh_uuid, global_scale,
                idx, total_mesh_objects,
            )

            # Track which group this mesh belongs to (if any)
            group_name = mesh_to_group.get(blender_object.name)  # None if ungrouped

            object_data.append(
                {
                    "wrapper_id": wrapper_id,
                    "mesh_id": mesh_id,
                    "object_path": object_path,
                    "wrapper_uuid": wrapper_uuid,
                    "mesh_uuid": mesh_uuid,
                    "component_uuid": component_uuid,
                    "transformation": transformation,
                    "assemble_delta": self._read_assemble_delta(blender_object),
                    "name": blender_object.name,
                    "mesh_center": self._mesh_centers.get(blender_object.name, (0.0, 0.0, 0.0)),
                    "group_name": group_name,  # None if ungrouped
                }
            )

            ctx.num_written += 1

        # Build groups list - each Empty becomes a separate assembly
        groups: list[dict] = []
        next_wrapper_id = len(object_data) * 2 + 1

        for spec in resolved.groups:
            group_members = [od for od in object_data if od["group_name"] == spec.key]

            if not group_members:
                continue

            groups.append({
                "wrapper_id": next_wrapper_id,
                "uuid": str(uuid.uuid4()),
                "name": spec.name,
                "members": group_members,
                "empty": spec.empty,
                "assemble_delta": self._group_assemble_delta(spec.empty, group_members),
            })
            next_wrapper_id += 1

        # Ungrouped objects (meshes not parented to any selected Empty)
        ungrouped = [od for od in object_data if od["group_name"] is None]

        debug(f"Export structure: {len(groups)} groups, {len(ungrouped)} ungrouped objects")

        # Center all objects: XY midpoint → origin, Z floor → 0
        scale_mat = mathutils.Matrix.Scale(global_scale, 4)
        all_corners = [
            scale_mat @ obj.matrix_world @ mathutils.Vector(corner)
            for obj in mesh_objects
            for corner in obj.bound_box
        ]
        cx = cy = cz = 0.0
        if all_corners:
            xs = [v.x for v in all_corners]
            ys = [v.y for v in all_corners]
            zs = [v.z for v in all_corners]
            cx = -(min(xs) + max(xs)) / 2
            cy = -(min(ys) + max(ys)) / 2
            cz = -min(zs)
            for od in object_data:
                od["transformation"][0][3] += cx
                od["transformation"][1][3] += cy
                od["transformation"][2][3] += cz
            debug(f"Applied centering offset ({cx:.2f}, {cy:.2f}, {cz:.2f}) mm")

        # Apply bed placement offset (printable_area + best_object_pos)
        bed_offset_x, bed_offset_y = self._get_bed_center_offset()

        if groups:
            # Multi-group mode: bed offset applied to each group's build item
            for grp in groups:
                grp["bed_offset"] = (bed_offset_x, bed_offset_y)

        # Ungrouped objects get offset applied directly
        if (bed_offset_x != 0.0 or bed_offset_y != 0.0) and ungrouped:
            for od in ungrouped:
                od["transformation"][0][3] += bed_offset_x
                od["transformation"][1][3] += bed_offset_y
            debug(
                f"Applied bed center offset ({bed_offset_x}, {bed_offset_y}) mm "
                f"to {len(ungrouped)} ungrouped objects"
            )

        center = mathutils.Matrix.Translation((cx, cy, cz))
        bed = mathutils.Matrix.Translation((bed_offset_x, bed_offset_y, 0.0))
        for grp in groups:
            if grp["assemble_delta"] is not None:
                delta = scale_transform_translation(grp["assemble_delta"], global_scale)
                grp["assemble_frame"] = bed @ center @ delta @ center.inverted()
        for od in ungrouped:
            if od["assemble_delta"] is not None:
                delta = scale_transform_translation(od["assemble_delta"], global_scale)
                od["assemble_frame"] = bed @ center @ delta @ center.inverted() @ bed.inverted()

        # Plate per Material: split mixed assemblies into per-material
        # sub-assemblies (their assembled positions are unchanged).
        if ctx.options.plate_per_material:
            groups, next_wrapper_id = self._split_groups_by_material(
                groups, ungrouped, mesh_objects, next_wrapper_id
            )

        # Plate assignment (``3mf_plate`` property) and per-plate placement
        self._assign_plates(
            mesh_objects, groups, ungrouped, scale_mat, (cx, cy, cz),
        )

        # Write main 3dmodel.model with wrapper objects and build items
        ctx._progress_update(90, "Writing main model...")
        self.write_main_model(archive, object_data, build_uuid, groups, ungrouped)

        # Write 3D/_rels/3dmodel.model.rels
        ctx._progress_update(93, "Writing relationships...")
        self.write_model_relationships(archive, object_data)

        # Write Orca metadata files
        ctx._progress_update(96, "Writing configuration...")
        self.write_orca_metadata(archive, mesh_objects, object_data, groups, ungrouped)

        # Write thumbnail
        ctx._progress_update(99, "Writing thumbnail...")
        write_thumbnail(archive, ctx, list(blender_objects))

        ctx._progress_update(100, "Finalizing export...")
        return ctx.finalize_export(archive, "Orca-compatible ")

    def write_object_model(
        self,
        archive: zipfile.ZipFile,
        blender_object: bpy.types.Object,
        object_path: str,
        mesh_id: int,
        mesh_uuid: str,
        global_scale: float,
        obj_index: int = 0,
        total_objects: int = 1,
    ) -> None:
        """Write an Orca object model with local vertices converted to millimeters."""
        ctx = self.ctx

        root = xml.etree.ElementTree.Element(
            "model",
            attrib={
                "unit": "millimeter",
                "xml:lang": "en-US",
                "xmlns": MODEL_NAMESPACE,
                "xmlns:BambuStudio": BAMBU_NAMESPACE,
                "xmlns:p": PRODUCTION_NAMESPACE,
                "requiredextensions": "p",
            },
        )

        # Add BambuStudio version metadata
        metadata = xml.etree.ElementTree.SubElement(
            root, "metadata", attrib={"name": "BambuStudio:3mfVersion"}
        )
        metadata.text = "1"

        # Resources
        resources = xml.etree.ElementTree.SubElement(root, "resources")

        # Get mesh data
        if ctx.options.use_mesh_modifiers:
            dependency_graph = bpy.context.evaluated_depsgraph_get()
            eval_object = blender_object.evaluated_get(dependency_graph)
        else:
            eval_object = blender_object

        try:
            mesh = eval_object.to_mesh()
        except RuntimeError:
            warn(f"Could not get mesh for object: {blender_object.name}")
            return

        if mesh is None:
            return

        mesh.calc_loop_triangles()

        if len(mesh.loop_triangles) == 0:
            warn(f"Skipping '{blender_object.name}': mesh has no triangles")
            eval_object.to_mesh_clear()
            return

        # Adaptive pre-subdivision for PAINT mode: split large faces so each
        # triangle can be encoded at full segmentation depth.
        # Skip for non-normal parts (modifiers, support enforcers/blockers, etc.)
        # Also skip for FullSpectrum parts mode (no per-triangle encoding needed).
        is_normal_part = blender_object.get("3mf_part_subtype", "normal_part") == "normal_part"
        _fs_mode = bool(
            hasattr(ctx, "mixed_filament_definitions_raw") and ctx.mixed_filament_definitions_raw
        )
        if is_normal_part and ctx.options.use_orca_format == "PAINT" and mesh.uv_layers.active and not _fs_mode:
            original_object = blender_object
            if hasattr(blender_object, "original"):
                original_object = blender_object.original
            paint_img = self._find_paint_texture(original_object)
            if paint_img:
                from .segmentation import subdivide_mesh_for_segmentation
                subdivide_mesh_for_segmentation(
                    mesh,
                    ctx.options.subdivision_depth,
                    paint_img.size[0],
                    paint_img.size[1],
                )

        # Create object element
        obj_elem = xml.etree.ElementTree.SubElement(
            resources,
            "object",
            attrib={
                "id": str(mesh_id),
                "p:UUID": mesh_uuid,
                "type": "model",
            },
        )

        # Mesh element
        mesh_elem = xml.etree.ElementTree.SubElement(obj_elem, "mesh")

        # Vertices — bulk extract via foreach_get + numpy char formatting.
        # No SubElement nodes needed; the streaming writer injects the raw XML string.
        decimals = ctx.options.coordinate_precision
        _t_vert0 = time.perf_counter()
        n_verts = len(mesh.vertices)
        co_flat = np.empty(n_verts * 3, dtype=np.float64)
        mesh.vertices.foreach_get("co", co_flat)
        co = co_flat.reshape(n_verts, 3)
        co *= global_scale
        # Orca centres every loaded mesh on its bounding box; remember where that is
        # so the assemble transform can compensate (see _assemble_translation).
        if n_verts:
            self._mesh_centers[blender_object.name] = tuple(
                float(v) for v in (co.min(axis=0) + co.max(axis=0)) / 2.0
            )
        _t_vert1 = time.perf_counter()
        timing_debug(f"orca write_vertices foreach_get ({n_verts} verts)", (_t_vert1 - _t_vert0) * 1000)
        fmt = f"%.{decimals}g"
        xs = np.char.mod(fmt, co[:, 0]).tolist()
        ys = np.char.mod(fmt, co[:, 1]).tolist()
        zs = np.char.mod(fmt, co[:, 2]).tolist()
        _t_vert2 = time.perf_counter()
        timing_debug(f"orca write_vertices str format ({n_verts} verts)", (_t_vert2 - _t_vert1) * 1000)
        vparts = ["<vertices>"]
        vappend = vparts.append
        for i in range(n_verts):
            vappend(f'<vertex x="{xs[i]}" y="{ys[i]}" z="{zs[i]}"/>')
        vparts.append("</vertices>")
        _raw_geometry_cache.setdefault(id(mesh_elem), {})["vertices"] = "".join(vparts)
        _t_vert3 = time.perf_counter()
        timing_debug(f"orca write_vertices build raw XML ({n_verts} verts)", (_t_vert3 - _t_vert2) * 1000)

        # Generate segmentation strings from UV texture if in PAINT mode.
        # Non-normal parts (modifiers, support, negative) get no paint data.
        # FullSpectrum "parts mode" files use extruder=N in model_settings.config
        # instead of per-triangle paint_color; skip the segmentation encoder entirely
        # for those objects so no paint_color attributes are written.
        segmentation_strings = {}
        seam_strings = {}
        support_strings = {}
        is_fullspectrum_parts_mode = bool(
            hasattr(ctx, "mixed_filament_definitions_raw") and ctx.mixed_filament_definitions_raw
        )
        if (
            is_normal_part
            and ctx.options.use_orca_format == "PAINT"
            and mesh.uv_layers.active
            and not is_fullspectrum_parts_mode
        ):
            # Read from original object's data, not the temporary evaluated mesh
            original_object = blender_object
            if hasattr(blender_object, "original"):
                original_object = blender_object.original
            original_mesh_data = original_object.data

            if (
                "3mf_is_paint_texture" in original_mesh_data
                and original_mesh_data["3mf_is_paint_texture"]
            ):
                paint_texture = None
                extruder_colors = {}
                default_extruder = original_mesh_data.get(
                    "3mf_paint_default_extruder", 0
                )

                # Get the stored extruder colors
                if "3mf_paint_extruder_colors" in original_mesh_data:
                    try:
                        extruder_colors_hex = ast.literal_eval(
                            original_mesh_data["3mf_paint_extruder_colors"]
                        )
                        for idx, hex_color in extruder_colors_hex.items():
                            extruder_colors[idx] = hex_to_rgb(hex_color)
                    except Exception as e:
                        debug(f"  WARNING: Failed to parse extruder colors: {e}")

                # Find the MMU paint texture
                for mat_slot in original_object.material_slots:
                    if mat_slot.material and mat_slot.material.use_nodes:
                        for node in mat_slot.material.node_tree.nodes:
                            if node.type == "TEX_IMAGE" and node.image:
                                paint_texture = node.image
                                break
                        if paint_texture:
                            break

                if paint_texture and extruder_colors:
                    debug(
                        f"  Exporting paint texture '{paint_texture.name}' as segmentation"
                    )

                    # Create progress callback for Orca segmentation
                    def orca_seg_progress(current, total_val, message):
                        if total_val > 0:
                            seg_pct = current / total_val
                            # Each object gets its share of the 65–89% range
                            # (Segmentation phase = phase 3, cumulative 65–90%).
                            obj_start = 65 + ((obj_index / total_objects) * 24)
                            obj_end = 65 + (((obj_index + 1) / total_objects) * 24)
                            overall = int(obj_start + (seg_pct * (obj_end - obj_start)))
                            ctx._progress_update(
                                overall, f"{blender_object.name}: {message}", phase=3
                            )

                    try:
                        segmentation_strings = texture_to_segmentation(
                            blender_object,
                            paint_texture,
                            extruder_colors,
                            default_extruder,
                            progress_callback=orca_seg_progress,
                            max_depth=ctx.options.subdivision_depth,
                            mesh=mesh,
                        )
                        debug(
                            f"  Generated {len(segmentation_strings)} segmentation strings"
                        )
                    except Exception as e:
                        debug(
                            f"  WARNING: Failed to generate segmentation from texture: {e}"
                        )
                        import traceback
                        traceback.print_exc()
                        segmentation_strings = {}

            # Seam / support paint (independent of color paint)
            seam_strings = self._extract_auxiliary_segmentation(
                original_object, blender_object, mesh, "SEAM",
                subdivided_mesh=mesh,
            )
            support_strings = self._extract_auxiliary_segmentation(
                original_object, blender_object, mesh, "SUPPORT",
                subdivided_mesh=mesh,
            )

        # Triangles with paint_color — bulk extract + pre-computed slot fragments.
        # foreach_get avoids the per-triangle Blender wrapper overhead.
        _t_tri0 = time.perf_counter()
        n_tris = len(mesh.loop_triangles)
        verts_flat = np.empty(n_tris * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", verts_flat)
        mat_flat = np.empty(n_tris, dtype=np.int32)
        mesh.loop_triangles.foreach_get("material_index", mat_flat)
        verts_list = verts_flat.reshape(n_tris, 3).tolist()
        mat_list = mat_flat.tolist()
        _t_tri1 = time.perf_counter()
        timing_debug(f"orca write_triangles foreach_get ({n_tris} tris)", (_t_tri1 - _t_tri0) * 1000)

        # Pre-compute per-slot paint code fragment — O(n_slots) instead of O(n_triangles).
        n_slots = len(eval_object.material_slots)
        slot_paint_frags = []
        if is_normal_part:
            for slot_idx in range(n_slots):
                frag = ""
                if slot_idx < len(mesh.materials):
                    mat = mesh.materials[slot_idx]
                    if mat is not None:
                        hex_color = material_to_hex_color(mat)
                        if hex_color and hex_color in ctx.vertex_colors:
                            filament_index = ctx.vertex_colors[hex_color]
                            if filament_index < len(ORCA_FILAMENT_CODES):
                                paint_code = ORCA_FILAMENT_CODES[filament_index]
                                if paint_code:
                                    frag = f' paint_color="{paint_code}"'
                slot_paint_frags.append(frag)
        else:
            slot_paint_frags = [""] * n_slots

        has_segmentation = bool(segmentation_strings)
        has_seam = bool(seam_strings)
        has_support = bool(support_strings)
        tparts = ["<triangles>"]
        tappend = tparts.append

        for tri_idx in range(n_tris):
            v = verts_list[tri_idx]
            base_tri = f'<triangle v1="{v[0]}" v2="{v[1]}" v3="{v[2]}"'

            # Check for segmentation string first (PAINT mode with UV texture)
            if has_segmentation:
                seg_str = segmentation_strings.get(tri_idx)
                if seg_str:
                    seam_part = ""
                    sup_part = ""
                    if has_seam:
                        seam = seam_strings.get(tri_idx)
                        if seam:
                            seam_part = f' paint_seam="{seam}"'
                    if has_support:
                        sup = support_strings.get(tri_idx)
                        if sup:
                            sup_part = f' paint_supports="{sup}"'
                    tappend(f'{base_tri} paint_color="{seg_str}"{seam_part}{sup_part}/>')
                    continue

            # Fallback: pre-computed slot paint fragment
            paint_frag = ""
            if is_normal_part and n_slots > 0:
                slot_idx = mat_list[tri_idx]
                if slot_idx < n_slots:
                    paint_frag = slot_paint_frags[slot_idx]

            seam_part = ""
            sup_part = ""
            if has_seam:
                seam = seam_strings.get(tri_idx)
                if seam:
                    seam_part = f' paint_seam="{seam}"'
            if has_support:
                sup = support_strings.get(tri_idx)
                if sup:
                    sup_part = f' paint_supports="{sup}"'

            tappend(f'{base_tri}{paint_frag}{seam_part}{sup_part}/>')

        tparts.append("</triangles>")
        _raw_geometry_cache.setdefault(id(mesh_elem), {})["triangles"] = "".join(tparts)
        _t_tri2 = time.perf_counter()
        timing_debug(f"orca write_triangles loop ({n_tris} tris)", (_t_tri2 - _t_tri1) * 1000)
        timing_debug(f"orca write_object_model geometry TOTAL", (_t_tri2 - _t_vert0) * 1000)

        # Empty build (geometry is in this file, build is in main model)
        xml.etree.ElementTree.SubElement(root, "build")

        # Clean up mesh
        eval_object.to_mesh_clear()

        # Write to archive using the streaming writer — bypasses ElementTree's
        # Python-level DOM walk and injects the pre-built geometry strings directly.
        archive_path = object_path.lstrip("/")
        with archive.open(archive_path, "w") as f:
            _stream_model_to_file(f, root)

        debug(f"Wrote object model: {archive_path}")

    def write_main_model(
        self,
        archive: zipfile.ZipFile,
        object_data: List[dict],
        build_uuid: str,
        groups: List[dict],
        ungrouped: List[dict],
    ) -> None:
        """Write the main 3dmodel.model file with wrapper objects.

        Supports multiple groups (each becomes a separate assembly/plate item)
        plus ungrouped objects as individual items.

        :param groups: List of group dicts with wrapper_id, uuid, name, members, bed_offset
        :param ungrouped: List of object_data dicts for objects not in any group
        """
        root = xml.etree.ElementTree.Element(
            "model",
            attrib={
                "unit": "millimeter",
                "xml:lang": "en-US",
                "xmlns": MODEL_NAMESPACE,
                "xmlns:BambuStudio": BAMBU_NAMESPACE,
                "xmlns:p": PRODUCTION_NAMESPACE,
                "requiredextensions": "p",
            },
        )

        # Metadata
        # Use BambuStudio application name so Bambu Studio recognizes the file
        # as a full project (not just geometry). Orca Slicer does the same.
        # All rights reserved to Bambu Lab for the application name
        meta_app = xml.etree.ElementTree.SubElement(
            root, "metadata", attrib={"name": "Application"}
        )
        meta_app.text = "BambuStudio-2.3.0"

        meta_version = xml.etree.ElementTree.SubElement(
            root, "metadata", attrib={"name": "BambuStudio:3mfVersion"}
        )
        meta_version.text = "1"

        # Standard metadata
        for name in [
            "Copyright",
            "Description",
            "Designer",
            "DesignerCover",
            "DesignerUserId",
            "License",
            "Origin",
        ]:
            meta = xml.etree.ElementTree.SubElement(
                root, "metadata", attrib={"name": name}
            )
            meta.text = ""

        # Creation/modification dates
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        meta_created = xml.etree.ElementTree.SubElement(
            root, "metadata", attrib={"name": "CreationDate"}
        )
        meta_created.text = today
        meta_modified = xml.etree.ElementTree.SubElement(
            root, "metadata", attrib={"name": "ModificationDate"}
        )
        meta_modified.text = today

        # Title from first object or scene
        title = object_data[0]["name"] if object_data else "Blender Export"
        meta_title = xml.etree.ElementTree.SubElement(
            root, "metadata", attrib={"name": "Title"}
        )
        meta_title.text = title

        # Resources - wrapper objects with component references
        resources = xml.etree.ElementTree.SubElement(root, "resources")

        # Write grouped assemblies - each group gets a wrapper object
        for grp in groups:
            wrapper = xml.etree.ElementTree.SubElement(
                resources,
                "object",
                attrib={
                    "id": str(grp["wrapper_id"]),
                    "p:UUID": grp["uuid"],
                    "type": "model",
                },
            )
            components = xml.etree.ElementTree.SubElement(wrapper, "components")
            for member in grp["members"]:
                comp_transform = format_transformation(member["transformation"])
                xml.etree.ElementTree.SubElement(
                    components,
                    "component",
                    attrib={
                        "p:path": member["object_path"],
                        "objectid": str(member["mesh_id"]),
                        "p:UUID": member["component_uuid"],
                        "transform": comp_transform,
                    },
                )

        # Write ungrouped objects - each gets its own wrapper
        for obj in ungrouped:
            wrapper = xml.etree.ElementTree.SubElement(
                resources,
                "object",
                attrib={
                    "id": str(obj["wrapper_id"]),
                    "p:UUID": obj["wrapper_uuid"],
                    "type": "model",
                },
            )

            components = xml.etree.ElementTree.SubElement(wrapper, "components")
            xml.etree.ElementTree.SubElement(
                components,
                "component",
                attrib={
                    "p:path": obj["object_path"],
                    "objectid": str(obj["mesh_id"]),
                    "p:UUID": obj["component_uuid"],
                    "transform": "1 0 0 0 1 0 0 0 1 0 0 0",
                },
            )

        # Build element
        build = xml.etree.ElementTree.SubElement(
            root, "build", attrib={"p:UUID": build_uuid}
        )

        item_idx = 0

        # Build items for groups
        for grp in groups:
            bed_x, bed_y = grp.get("bed_offset", (0.0, 0.0))
            plate_x, plate_y, plate_z = grp.get("plate_offset", (0.0, 0.0, 0.0))
            group_transform = (
                f"1.000000000 0.000000000 0.000000000 "
                f"0.000000000 1.000000000 0.000000000 "
                f"0.000000000 0.000000000 1.000000000 "
                f"{bed_x + plate_x:.9f} {bed_y + plate_y:.9f} {plate_z:.9f}"
            )
            item_uuid = f"0000000{item_idx + 2}-b1ec-4553-aec9-835e5b724bb4"
            xml.etree.ElementTree.SubElement(
                build,
                "item",
                attrib={
                    "objectid": str(grp["wrapper_id"]),
                    "p:UUID": item_uuid,
                    "transform": group_transform,
                    "printable": "1",
                },
            )
            item_idx += 1

        # Build items for ungrouped objects
        for obj in ungrouped:
            item_uuid = f"0000000{item_idx + 2}-b1ec-4553-aec9-835e5b724bb4"
            build_matrix = obj["transformation"]
            plate_offset = obj.get("plate_offset")
            if plate_offset:
                # Plate placement moves the build item only; the assemble
                # section keeps the original (assembled) position.
                build_matrix = build_matrix.copy()
                for axis in range(3):
                    build_matrix[axis][3] += plate_offset[axis]
            transform_str = format_transformation(build_matrix)

            xml.etree.ElementTree.SubElement(
                build,
                "item",
                attrib={
                    "objectid": str(obj["wrapper_id"]),
                    "p:UUID": item_uuid,
                    "transform": transform_str,
                    "printable": "1",
                },
            )
            item_idx += 1

        # Write to archive
        document = xml.etree.ElementTree.ElementTree(root)
        buffer = io.BytesIO()
        document.write(buffer, xml_declaration=True, encoding="UTF-8")
        xml_content = buffer.getvalue().decode("UTF-8")

        with archive.open(MODEL_LOCATION, "w") as f:
            f.write(xml_content.encode("UTF-8"))

        debug(f"Wrote main model: {MODEL_LOCATION}")

    def write_model_relationships(
        self, archive: zipfile.ZipFile, object_data: List[dict]
    ) -> None:
        """Write the 3D/_rels/3dmodel.model.rels file."""
        root = xml.etree.ElementTree.Element(
            "Relationships", attrib={"xmlns": RELS_NAMESPACE}
        )

        for idx, obj in enumerate(object_data):
            xml.etree.ElementTree.SubElement(
                root,
                "Relationship",
                attrib={
                    "Target": obj["object_path"],
                    "Id": f"rel-{idx + 1}",
                    "Type": MODEL_REL,
                },
            )

        document = xml.etree.ElementTree.ElementTree(root)
        buffer = io.BytesIO()
        document.write(buffer, xml_declaration=True, encoding="UTF-8")
        xml_content = buffer.getvalue().decode("UTF-8")

        with archive.open("3D/_rels/3dmodel.model.rels", "w") as f:
            f.write(xml_content.encode("UTF-8"))

        debug("Wrote 3D/_rels/3dmodel.model.rels")

    def write_orca_metadata(
        self,
        archive: zipfile.ZipFile,
        blender_objects: List[bpy.types.Object],
        object_data: List[dict],
        groups: List[dict],
        ungrouped: List[dict],
    ) -> None:
        """Write Orca Slicer compatible metadata files to the archive.

        :param archive: The ZIP archive to write into.
        :param blender_objects: The Blender objects being exported.
        :param object_data: Per-object export data dicts from ``execute()``.
        :param groups: List of group dicts for grouped assemblies.
        :param ungrouped: List of object_data for ungrouped objects.
        """
        ctx = self.ctx
        debug("Writing Orca metadata files...")

        try:
            # Write project_settings.config from template with updated colors
            project_settings = self._get_project_settings()
            with archive.open("Metadata/project_settings.config", "w") as f:
                f.write(json.dumps(project_settings, indent=4).encode("utf-8"))
            debug("Wrote project_settings.config")

            # Write model_settings.config with object metadata
            model_settings_xml = self.generate_model_settings(
                blender_objects, object_data, groups, ungrouped
            )
            with archive.open("Metadata/model_settings.config", "w") as f:
                f.write(model_settings_xml.encode("utf-8"))
            debug("Wrote model_settings.config")

            debug(f"Wrote Orca metadata with {len(ctx.vertex_colors)} color zones")
        except Exception as e:
            error(f"Failed to write Orca metadata: {e}")
            ctx.safe_report({"ERROR"}, f"Failed to write Orca metadata: {e}")
            raise

    def _get_bed_center_offset(self, *, force_for_plates: bool = False) -> tuple:
        """Return the XY point (mm) where the centered export should be placed.

        Derived from the effective ``project_settings.config`` that will be
        written: ``printable_area`` bounding box plus the ``best_object_pos``
        fraction (default 0.5,0.5).  Explicit ``project_template`` callers
        keep the old single-plate behaviour (no offset; the caller positions
        objects), unless multi-plate placement explicitly requests the bed
        centre. Falls back to ``(0, 0)`` with a warning if the bed is unknown.
        """
        if self.ctx.project_template_path and not force_for_plates:
            return (0.0, 0.0)
        settings = self._get_project_settings()
        try:
            pts = [tuple(float(v) for v in p.split("x")) for p in settings["printable_area"]]
            if len(pts) < 3:
                raise ValueError("printable_area needs at least 3 points")
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            warn(f"Cannot determine bed from printable_area ({e}); not offsetting objects.")
            return (0.0, 0.0)
        try:
            fx, fy = (float(v) for v in str(settings.get("best_object_pos", "0.5,0.5")).split(","))
        except ValueError:
            fx, fy = 0.5, 0.5
        target = (min(xs) + fx * (max(xs) - min(xs)), min(ys) + fy * (max(ys) - min(ys)))
        debug(f"Bed placement target {target} (best_object_pos={fx},{fy})")
        return target

    def _material_key(self, blender_object) -> tuple:
        """``(key, label)`` identifying the filament/material of an object.

        A custom material group takes precedence over the dominant colour or
        the stored extruder for FullSpectrum parts.
        """
        group = blender_object.get("3mf_material_group")
        if isinstance(group, str) and group.strip():
            label = group.strip()
            return (("group", label), label)
        if group is not None:
            self.ctx.safe_report(
                {"WARNING"},
                f"Invalid 3mf_material_group on '{blender_object.name}'; using its material.",
            )
        mesh_data = blender_object.data if blender_object else None
        if mesh_data and mesh_data.get("3mf_paint_default_extruder"):
            number = int(mesh_data["3mf_paint_default_extruder"])
            return (f"extruder:{number}", f"Filament {number}")
        hex_color, name = self._get_dominant_material_info(blender_object)
        if not hex_color:
            return (None, None)
        return (hex_color, name or hex_color)

    def _read_assemble_delta(self, obj) -> Optional[mathutils.Matrix]:
        values = obj.get(ASSEMBLE_DELTA_PROPERTY)
        if values is None:
            return None
        try:
            values = [float(value) for value in values]
            if (
                len(values) != 16 or not all(math.isfinite(value) for value in values)
                or any(abs(values[12 + i] - expected) > 1e-6
                       for i, expected in enumerate((0.0, 0.0, 0.0, 1.0)))
            ):
                raise ValueError("expected a finite affine 4x4 matrix")
            return mathutils.Matrix([values[index:index + 4] for index in range(0, 16, 4)])
        except (TypeError, ValueError, OverflowError) as exc:
            self.ctx.safe_report(
                {"WARNING"},
                f"Invalid {ASSEMBLE_DELTA_PROPERTY} on '{obj.name}': {exc}; "
                "using its current position.",
            )
            return None

    def _group_assemble_delta(self, empty, members) -> Optional[mathutils.Matrix]:
        if empty is not None:
            delta = self._read_assemble_delta(empty)
            if delta is not None:
                return delta

        identity = mathutils.Matrix.Identity(4)
        deltas = [member["assemble_delta"] for member in members]
        reference = deltas[0] if deltas and deltas[0] is not None else identity
        for delta in deltas[1:]:
            candidate = delta if delta is not None else identity
            if any(
                abs(reference[row][col] - candidate[row][col]) > 1e-6
                for row in range(4) for col in range(4)
            ):
                self.ctx.safe_report(
                    {"WARNING"},
                    "Assembly members have different saved assembled frames; "
                    "using their current positions.",
                )
                return None
        return reference if any(delta is not None for delta in deltas) else None

    def _split_groups_by_material(self, groups, ungrouped, mesh_objects, next_wrapper_id):
        """Tag every unit with its material and split mixed assemblies.

        Each assembly with several materials becomes one sub-assembly per
        material (when ``split_assemblies_by_material`` is on); members keep
        their transforms, so assembled positions are preserved.  With the
        option off, a mixed assembly stays whole and gets its own plate.
        """
        obj_by_name = {o.name: o for o in mesh_objects}
        info = {name: self._material_key(obj) for name, obj in obj_by_name.items()}
        split = self.ctx.options.split_assemblies_by_material

        result = []
        for grp in groups:
            by_key = {}
            for member in grp["members"]:
                key, label = info.get(member["name"], (None, None))
                by_key.setdefault(key, (label, []))[1].append(member)

            if len(by_key) <= 1:
                key, (label, _) = next(iter(by_key.items()))
                grp["material_key"], grp["material_label"] = key, label
                result.append(grp)
            elif not split:
                grp["material_key"] = ("mixed", grp["wrapper_id"])
                grp["material_label"] = grp["name"]
                result.append(grp)
            else:
                for index, (key, (label, members)) in enumerate(by_key.items()):
                    sub = dict(grp)
                    if index > 0:
                        sub["wrapper_id"] = next_wrapper_id
                        sub["uuid"] = str(uuid.uuid4())
                        next_wrapper_id += 1
                    sub["name"] = f"{grp['name']} ({label or 'No material'})"
                    sub["members"] = members
                    sub["material_key"], sub["material_label"] = key, label
                    sub["material_split"] = True
                    result.append(sub)
                debug(f"Split assembly '{grp['name']}' into {len(by_key)} material group(s)")

        for od in ungrouped:
            od["material_key"], od["material_label"] = info.get(od["name"], (None, None))
        return result, next_wrapper_id

    @staticmethod
    def _assemble_translation(matrix, mesh_center, base=(0.0, 0.0, 0.0)):
        """Assembled position of a single-volume object, as Orca computes it.

        On load Orca moves each mesh's origin to its bounding-box centre and, for
        single-volume objects, folds that shift into the instance (build) offset.
        It does not do the same for the assemble transform, so the exporter has to:
        ``translation = base + matrix.translation + matrix.linear @ mesh_center``.
        """
        shifted = matrix.to_3x3() @ mathutils.Vector(mesh_center)
        translation = matrix.translation
        return (
            base[0] + translation.x + shifted.x,
            base[1] + translation.y + shifted.y,
            base[2] + translation.z + shifted.z,
        )

    def _get_bed_size(self) -> tuple:
        """Return the bed ``(width, depth)`` in mm from ``printable_area``.

        Falls back to 256 x 256 (Bambu default) with a warning if unknown.
        """
        settings = self._get_project_settings()
        try:
            return printable_area_size(settings["printable_area"])
        except (KeyError, ValueError) as e:
            self.ctx.safe_report(
                {"WARNING"},
                f"Cannot determine bed size ({e}); assuming 256 x 256 mm for plate spacing.",
            )
            return (256.0, 256.0)

    def _assign_plates(self, mesh_objects, groups, ungrouped, scale_mat, centering) -> None:
        """Resolve each unit's plate (``3mf_plate``) and compute plate placement.

        Sets ``plate`` on every unit.  When more than one plate is used, also
        sets ``plate_offset`` (dx, dy, dz): recentre the plate's units on the
        bed, floor them, then move to the plate's grid position.  Orca decides
        plate membership from position, so this offset is required.  With a
        single plate nothing is changed, so default output is unchanged.
        """
        obj_by_name = {o.name: o for o in mesh_objects}
        units = []  # (unit dict, Blender member objects)
        for grp in groups:
            members = [obj_by_name[m["name"]] for m in grp["members"] if m["name"] in obj_by_name]
            units.append((grp, grp.get("empty"), members))
        for od in ungrouped:
            member = obj_by_name.get(od["name"])
            units.append((od, None, [member] if member is not None else []))

        self._plate_names = {}
        requests = []
        seen_sources = set()
        for unit, empty, members in units:
            for source in ([empty] if empty is not None else []) + members:
                if source.name in seen_sources:
                    continue
                seen_sources.add(source.name)
                value = source.get(PLATE_PROPERTY)
                if value is None:
                    continue
                normalized = plate_property(source)
                if normalized is None:
                    self.ctx.safe_report(
                        {"WARNING"},
                        f"Invalid {PLATE_PROPERTY} on '{source.name}'; ignoring the assignment.",
                    )
                elif value > MAX_PLATES:
                    self.ctx.safe_report(
                        {"WARNING"},
                        f"{PLATE_PROPERTY} on '{source.name}' exceeds {MAX_PLATES}; "
                        f"using plate {MAX_PLATES}.",
                    )
            number, conflict = explicit_unit_plate(empty, members)
            if conflict:
                self.ctx.safe_report(
                    {"WARNING"},
                    f"Objects in assembly '{unit.get('name', '?')}' request different "
                    f"plates; using plate {number}.",
                )
            elif empty is not None and plate_property(empty) is not None and any(
                plate_property(member) not in (None, number) for member in members
            ):
                self.ctx.safe_report(
                    {"WARNING"},
                    f"Assembly '{unit.get('name', '?')}' overrides conflicting member "
                    f"plate assignments with plate {number}.",
                )
            requests.append(number)

        if self.ctx.options.plate_per_material:
            explicit_keys = {
                unit.get("material_key")
                for (unit, _, _), request in zip(units, requests) if request is not None
            }
            auto_keys = {
                unit.get("material_key")
                for (unit, _, _), request in zip(units, requests)
                if request is None and unit.get("material_key") not in explicit_keys
            }
            reserved = {number for number in requests if number is not None}
            if len(auto_keys) > MAX_PLATES - len(reserved):
                self.ctx.safe_report(
                    {"WARNING"},
                    f"More material groups than available plates (maximum {MAX_PLATES}); "
                    f"remaining groups share plate {MAX_PLATES}.",
                )
            plates, self._plate_names = plan_material_plates(
                [(u.get("material_key"), u.get("material_label")) for u, _, _ in units],
                requests,
            )
        else:
            plates = [1 if r is None else r for r in requests]
        for (unit, _, _), number in zip(units, plates):
            unit["plate"] = number

        explicit_names = {}
        name_conflicts = set()
        for (_, empty, members), number in zip(units, plates):
            for source in ([empty] if empty is not None else []) + members:
                name = source.get(PLATE_NAME_PROPERTY)
                # A name without a plate number comes from a single-plate import.
                named_plate = plate_property(source) if PLATE_PROPERTY in source else 1
                if named_plate != number or not isinstance(name, str) or not name:
                    continue
                if number in explicit_names and explicit_names[number] != name:
                    conflict = (number, name)
                    if conflict not in name_conflicts:
                        self.ctx.safe_report(
                            {"WARNING"},
                            f"Conflicting names for plate {number}; "
                            f"using '{explicit_names[number]}'.",
                        )
                        name_conflicts.add(conflict)
                else:
                    explicit_names[number] = name
        self._plate_names.update(explicit_names)

        plate_numbers = sorted({unit["plate"] for unit, _, _ in units})
        has_split_units = any(unit.get("material_split") for unit, _, _ in units)
        if not plate_numbers or (plate_numbers == [1] and not has_split_units):
            return

        bed_w, bed_d = self._get_bed_size()
        template_x, template_y = (
            self._get_bed_center_offset(force_for_plates=True)
            if self.ctx.project_template_path else (0.0, 0.0)
        )
        plate_count = plate_numbers[-1]
        cx, cy, cz = centering

        for number in plate_numbers:
            members = [m for unit, _, ms in units if unit["plate"] == number for m in ms]
            corners = [
                scale_mat @ m.matrix_world @ mathutils.Vector(c)
                for m in members
                for c in m.bound_box
            ]
            if not corners:
                continue
            xs = [v.x + cx for v in corners]
            ys = [v.y + cy for v in corners]
            zs = [v.z + cz for v in corners]
            if max(xs) - min(xs) > bed_w or max(ys) - min(ys) > bed_d:
                self.ctx.safe_report(
                    {"WARNING"},
                    f"Objects on plate {number} are larger than the bed "
                    f"({bed_w:g} x {bed_d:g} mm); arrange them in the slicer.",
                )
            origin_x, origin_y = plate_origin(number - 1, plate_count, bed_w, bed_d)
            offset = (
                -(min(xs) + max(xs)) / 2 + origin_x + template_x,
                -(min(ys) + max(ys)) / 2 + origin_y + template_y,
                -min(zs),
            )
            for unit, _, unit_members in units:
                if unit["plate"] == number:
                    if unit.get("material_split") and unit_members:
                        unit_min_z = min(
                            (scale_mat @ member.matrix_world @ mathutils.Vector(corner)).z + cz
                            for member in unit_members
                            for corner in member.bound_box
                        )
                        unit["plate_offset"] = (offset[0], offset[1], -unit_min_z)
                    else:
                        unit["plate_offset"] = offset
        debug(f"Placed units on {len(plate_numbers)} plate(s): {plate_numbers}")

    def _get_project_settings(self) -> dict:
        """Generate project_settings once so placement and the written file agree."""
        if getattr(self, "_project_settings", None) is None:
            self._project_settings = self.generate_project_settings()
        return self._project_settings

    def _get_dominant_material_info(
        self, blender_object: bpy.types.Object
    ) -> tuple:
        """Return the hex colour string of the most-common face material on *blender_object*.

        Used to determine the per-object ``extruder`` value in
        ``model_settings.config`` so Orca assigns the right filament even
        when objects have only a single material (no ``paint_color``
        per-triangle overrides needed).

        Returns ``(hex_colour, material_name)``; both ``None`` when no material
        can be determined.
        """
        ctx = self.ctx

        if ctx.options.use_mesh_modifiers:
            depsgraph = bpy.context.evaluated_depsgraph_get()
            eval_obj = blender_object.evaluated_get(depsgraph)
        else:
            eval_obj = blender_object

        try:
            mesh = eval_obj.to_mesh()
        except RuntimeError:
            return (None, None)
        if mesh is None:
            return (None, None)

        mesh.calc_loop_triangles()
        n_tris = len(mesh.loop_triangles)
        if n_tris == 0:
            eval_obj.to_mesh_clear()
            return (None, None)

        n_slots = len(eval_obj.material_slots)
        if n_slots == 0:
            eval_obj.to_mesh_clear()
            return (None, None)

        # Bulk-extract material slot indices in one C call, then use bincount to
        # find the most-used slot without a Python loop over all triangles.
        mat_flat = np.empty(n_tris, dtype=np.int32)
        mesh.loop_triangles.foreach_get("material_index", mat_flat)
        counts = np.bincount(mat_flat, minlength=n_slots)
        dominant_slot = int(np.argmax(counts))

        dominant_color = None
        dominant_name = None
        if dominant_slot < len(mesh.materials):
            mat = mesh.materials[dominant_slot]
            if mat is not None:
                dominant_color = material_to_hex_color(mat)
                dominant_name = mat.name

        eval_obj.to_mesh_clear()

        return (dominant_color, dominant_name)

    def _get_dominant_color(self, blender_object: bpy.types.Object) -> Optional[str]:
        """Hex colour of the most-common face material (``None`` if unknown)."""
        return self._get_dominant_material_info(blender_object)[0]

    def generate_project_settings(self) -> dict:
        """Generate project_settings.config by loading template and updating filament colors.

        Priority order for the base template:
        1. ``ctx.project_template_path`` — explicit custom template from API
        2. Stashed config from a previous import (preserved in Blender text blocks)
        3. Built-in ``orca_project_template.json``

        Regardless of source, ``filament_colour`` is updated and all filament
        arrays are resized to match the current export colors.
        """
        ctx = self.ctx

        # Determine which template to load
        addon_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        builtin_path = os.path.join(addon_dir, "orca_project_template.json")
        template_path = builtin_path

        if ctx.project_template_path:
            if os.path.isfile(ctx.project_template_path):
                template_path = ctx.project_template_path
                debug(f"Using custom project template: {template_path}")
            else:
                warn(
                    f"Custom project template not found: {ctx.project_template_path}. "
                    f"Falling back to built-in template."
                )

        # Priority 2: stashed config from a previous import.
        stashed_config = None
        if template_path == builtin_path:
            stashed_raw = get_stashed_config("Metadata/project_settings.config")
            if stashed_raw is not None:
                try:
                    stashed_config = json.loads(stashed_raw.decode("utf-8"))
                    debug("Using stashed project_settings.config from previous import")
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    warn(f"Stashed project_settings.config is invalid: {e}. Using built-in template.")

        # Priority 3: user-selected slicer profile from addon settings.
        if stashed_config is None and template_path == builtin_path:
            profile_name = ctx.options.slicer_profile
            if profile_name != "NONE":
                profile_raw = get_profile_config(
                    profile_name,
                    "Metadata/project_settings.config",
                )
                if profile_raw is not None:
                    try:
                        stashed_config = json.loads(
                            profile_raw.decode("utf-8"),
                        )
                        debug(
                            f"Using slicer profile '{profile_name}' "
                            f"for project settings"
                        )
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        warn(
                            f"Profile config is invalid: {e}. "
                            f"Using built-in template."
                        )

        try:
            if stashed_config is not None:
                settings = stashed_config
            else:
                with open(template_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            if template_path != builtin_path:
                warn(
                    f"Invalid JSON in custom template: {e}. "
                    f"Falling back to built-in template."
                )
                with open(builtin_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            else:
                raise

        sorted_colors = sorted(ctx.vertex_colors.items(), key=lambda x: x[1])
        color_list = [color_hex for color_hex, _ in sorted_colors]

        if not color_list:
            color_list = ["#FFFFFF"]

        # For FullSpectrum files, the stashed filament_colour is the canonical list of
        # physical filament colors.  Re-exporting a FullSpectrum file must preserve all
        # physical filaments even when some are not applied to any visible face — the
        # mixed_filament_definitions reference slots by 1-based index, so losing a slot
        # would corrupt the virtual filament recipe.
        if stashed_config and "filament_colour" in stashed_config and ctx.mixed_filament_definitions_raw:
            stashed_physical = [c.upper() for c in stashed_config["filament_colour"]]
            stashed_set = set(stashed_physical)
            # Preserve original order; append any new colours that appeared in the mesh.
            for c in color_list:
                if c.upper() not in stashed_set:
                    stashed_physical.append(c.upper())
                    stashed_set.add(c.upper())
            color_list = stashed_physical

        # Append virtual (mixed) filament display colors after the physical ones.
        # This extends the filament_colour array so the slicer can display blended
        # swatches for each virtual filament slot.
        mixed_defs_to_write = ""
        if hasattr(ctx, "mixed_filament_definitions_raw") and ctx.mixed_filament_definitions_raw:
            mixed_defs_to_write = ctx.mixed_filament_definitions_raw
            from ..common.mixed_filaments import parse_mixed_filament_definitions, populate_display_colors
            entries = parse_mixed_filament_definitions(mixed_defs_to_write)
            # Compute display colors using the physical palette we just built.
            # (UI-synced entries already have display_color set; this fills in
            # any that are missing, e.g. stash-loaded entries.)
            missing = [mf for mf in entries if not mf.display_color]
            if missing:
                populate_display_colors(entries, color_list)
            # For FullSpectrum "parts mode" files the slicer computes virtual filament
            # display colours itself from mixed_filament_definitions at runtime.
            # Do NOT append them to filament_colour here — that would inflate the
            # physical-filament count and cause filament array length mismatches in
            # the slicer (it would try to create 44 extruder slots instead of 4).

        num_colors = len(color_list)
        settings["filament_colour"] = color_list

        # Resize all filament arrays to match the number of colors
        for key, value in list(settings.items()):
            if (
                isinstance(value, list)
                and key.startswith("filament_")
                and key != "filament_colour"
            ):
                if len(value) > 0:
                    if len(value) < num_colors:
                        settings[key] = value + [value[-1]] * (num_colors - len(value))
                    elif len(value) > num_colors:
                        settings[key] = value[:num_colors]

        # Also handle other arrays that need to match filament count
        array_keys_to_resize = [
            "activate_air_filtration",
            "activate_chamber_temp_control",
            "additional_cooling_fan_speed",
            "chamber_temperature",
            "close_fan_the_first_x_layers",
            "complete_print_exhaust_fan_speed",
            "cool_plate_temp",
            "cool_plate_temp_initial_layer",
            "default_filament_colour",
            "eng_plate_temp",
            "eng_plate_temp_initial_layer",
            "hot_plate_temp",
            "hot_plate_temp_initial_layer",
            "nozzle_temperature",
            "nozzle_temperature_initial_layer",
            "textured_plate_temp",
            "textured_plate_temp_initial_layer",
        ]

        for key in array_keys_to_resize:
            if key in settings and isinstance(settings[key], list):
                value = settings[key]
                if len(value) > 0:
                    if len(value) < num_colors:
                        settings[key] = value + [value[-1]] * (num_colors - len(value))
                    elif len(value) > num_colors:
                        settings[key] = value[:num_colors]

        # Write mixed filament definitions (FullSpectrum).  The stashed
        # project_settings already carries these, but if the user has re-exported
        # from scratch (no stash) we still write the definitions if we have them.
        if mixed_defs_to_write:
            settings["mixed_filament_definitions"] = mixed_defs_to_write

        return settings

    def generate_model_settings(
        self,
        blender_objects: List[bpy.types.Object],
        object_data: List[dict],
        groups: List[dict],
        ungrouped: List[dict],
    ) -> str:
        """Generate the model_settings.config XML for Orca Slicer.

        Supports multiple groups (each becomes a separate assembly) plus
        ungrouped objects as individual items.

        :param blender_objects: The Blender objects being exported.
        :param object_data: Per-object export data dicts from ``execute()``.
        :param groups: List of group dicts for grouped assemblies.
        :param ungrouped: List of object_data for ungrouped objects.
        """
        ctx = self.ctx
        root = xml.etree.ElementTree.Element("config")

        # Build a lookup from object name to Blender object
        blender_obj_by_name: dict[str, bpy.types.Object] = {}
        for blender_object in blender_objects:
            if blender_object.type == "MESH":
                blender_obj_by_name[blender_object.name] = blender_object

        # ----- Grouped assemblies: each group is an <object> with multiple <part> -----
        for grp in groups:
            object_elem = xml.etree.ElementTree.SubElement(
                root, "object", id=str(grp["wrapper_id"])
            )
            xml.etree.ElementTree.SubElement(
                object_elem, "metadata", key="name", value=grp["name"]
            )

            # Wrapper-level slicer setting overrides (round-trip passthrough)
            group_empty = grp.get("empty")
            if group_empty is not None:
                _write_stashed_settings(object_elem, group_empty)

            # Determine dominant extruder for entire group by aggregating part colors
            group_dominant_extruder = "1"
            group_color_counts: dict[str, int] = {}

            for member in grp["members"]:
                obj_name = member["name"]
                blender_object = blender_obj_by_name.get(obj_name)
                if blender_object is None:
                    continue

                # Determine per-part extruder.
                # FullSpectrum parts mode: use the stored 1-based extruder index directly
                # so virtual filament slots (5-44) are preserved in the output.
                extruder_value = "1"
                mesh_data = blender_object.data if blender_object else None
                if mesh_data and mesh_data.get("3mf_paint_default_extruder"):
                    extruder_value = str(int(mesh_data["3mf_paint_default_extruder"]))
                elif ctx.vertex_colors:
                    dominant_color = self._get_dominant_color(blender_object)
                    if dominant_color:
                        # Track for group-level dominant
                        group_color_counts[dominant_color] = (
                            group_color_counts.get(dominant_color, 0) + 1
                        )
                        if dominant_color in ctx.vertex_colors:
                            extruder_value = str(ctx.vertex_colors[dominant_color])

                part_subtype = "normal_part"
                if blender_object is not None:
                    part_subtype = blender_object.get("3mf_part_subtype", "normal_part")

                part_elem = xml.etree.ElementTree.SubElement(
                    object_elem, "part", id=str(member["mesh_id"]), subtype=part_subtype
                )
                xml.etree.ElementTree.SubElement(
                    part_elem, "metadata", key="name", value=obj_name
                )
                xml.etree.ElementTree.SubElement(
                    part_elem, "metadata", key="matrix",
                    value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1",
                )
                xml.etree.ElementTree.SubElement(
                    part_elem, "metadata", key="extruder", value=extruder_value
                )

                # Per-part slicer setting overrides (round-trip passthrough)
                if blender_object is not None:
                    _write_stashed_settings(part_elem, blender_object)

            # Set group-level extruder to most common color among its parts
            if group_color_counts and ctx.vertex_colors:
                most_common_color = max(group_color_counts, key=group_color_counts.get)
                if most_common_color in ctx.vertex_colors:
                    group_dominant_extruder = str(ctx.vertex_colors[most_common_color])

            xml.etree.ElementTree.SubElement(
                object_elem, "metadata", key="extruder", value=group_dominant_extruder
            )

        # ----- Ungrouped objects: each is an <object> with a single <part> -----
        for od in ungrouped:
            obj_name = od["name"]
            blender_object = blender_obj_by_name.get(obj_name)

            wrapper_id = od["wrapper_id"]
            mesh_id = od["mesh_id"]

            # Determine the dominant extruder for this object.
            # FullSpectrum parts mode: use the stored 1-based extruder index directly
            # so virtual filament slots (5-44) are preserved in the output.
            extruder_value = "1"
            _mesh_data = blender_object.data if blender_object else None
            if _mesh_data and _mesh_data.get("3mf_paint_default_extruder"):
                extruder_value = str(int(_mesh_data["3mf_paint_default_extruder"]))
            elif blender_object and ctx.vertex_colors:
                dominant_color = self._get_dominant_color(blender_object)
                if dominant_color and dominant_color in ctx.vertex_colors:
                    extruder_value = str(ctx.vertex_colors[dominant_color])

            object_elem = xml.etree.ElementTree.SubElement(
                root, "object", id=str(wrapper_id)
            )
            xml.etree.ElementTree.SubElement(
                object_elem, "metadata", key="name", value=obj_name
            )
            xml.etree.ElementTree.SubElement(
                object_elem, "metadata", key="extruder", value=extruder_value
            )

            # Per-object setting overrides (passthrough, no validation).
            # Priority: explicit API object_settings > stashed from import.
            if obj_name in ctx.object_settings:
                for setting_key, setting_value in ctx.object_settings[obj_name].items():
                    xml.etree.ElementTree.SubElement(
                        object_elem,
                        "metadata",
                        key=str(setting_key),
                        value=str(setting_value),
                    )
                debug(
                    f"Wrote {len(ctx.object_settings[obj_name])} per-object overrides "
                    f"for '{obj_name}'"
                )
            elif blender_object is not None:
                _write_stashed_settings(
                    object_elem, blender_object,
                    prop_name="3mf_orca_wrapper_settings",
                )

            part_subtype = "normal_part"
            if blender_object is not None:
                part_subtype = blender_object.get("3mf_part_subtype", "normal_part")

            part_elem = xml.etree.ElementTree.SubElement(
                object_elem, "part", id=str(mesh_id), subtype=part_subtype
            )
            xml.etree.ElementTree.SubElement(
                part_elem, "metadata", key="name", value=obj_name
            )
            matrix_value = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
            xml.etree.ElementTree.SubElement(
                part_elem, "metadata", key="matrix", value=matrix_value
            )

        # Plate entries with model_instance lists.  Default layout is a single
        # plate holding everything; other strategies only change this list.
        for plate in layout_plates(groups, ungrouped, getattr(self, "_plate_names", None)):
            plate_elem = xml.etree.ElementTree.SubElement(root, "plate")
            xml.etree.ElementTree.SubElement(
                plate_elem, "metadata", key="plater_id", value=str(plate.plater_id)
            )
            xml.etree.ElementTree.SubElement(
                plate_elem, "metadata", key="plater_name", value=plate.name
            )
            xml.etree.ElementTree.SubElement(
                plate_elem, "metadata", key="locked", value="false"
            )
            xml.etree.ElementTree.SubElement(
                plate_elem, "metadata", key="filament_map_mode", value="Auto For Flush"
            )
            for inst in plate.instances:
                instance_elem = xml.etree.ElementTree.SubElement(
                    plate_elem, "model_instance"
                )
                xml.etree.ElementTree.SubElement(
                    instance_elem, "metadata", key="object_id",
                    value=str(inst.object_id),
                )
                xml.etree.ElementTree.SubElement(
                    instance_elem, "metadata", key="instance_id",
                    value=str(inst.instance_id),
                )
                xml.etree.ElementTree.SubElement(
                    instance_elem, "metadata", key="identify_id",
                    value=str(inst.identify_id),
                )

        # Add assemble section with real world transforms
        assemble_elem = xml.etree.ElementTree.SubElement(root, "assemble")
        assemble_entries = []
        part_centers = []

        # Assemble items for groups
        for grp in groups:
            bed_x, bed_y = grp.get("bed_offset", (0.0, 0.0))
            group_matrix = grp.get("assemble_frame")
            if group_matrix is None:
                group_matrix = mathutils.Matrix.Translation((bed_x, bed_y, 0.0))
            else:
                group_matrix = group_matrix.copy()
            if len(grp["members"]) == 1:
                # One volume: Orca folds the mesh-centring shift into the build
                # offset only, so apply it to the assemble transform as well.
                member = grp["members"][0]
                group_matrix.translation = self._assemble_translation(
                    group_matrix @ member["transformation"], member["mesh_center"]
                )
                part_centers.append(group_matrix.translation.copy())
                linear = group_matrix.to_3x3() @ member["transformation"].to_3x3()
            else:
                for member in grp["members"]:
                    part_centers.append(
                        group_matrix @ member["transformation"] @ mathutils.Vector(member["mesh_center"])
                    )
                linear = None
            assemble_entries.append((grp["wrapper_id"], group_matrix, linear))

        # Assemble items for ungrouped objects
        for od in ungrouped:
            assemble_matrix = od["transformation"].copy()
            if od.get("assemble_frame") is not None:
                assemble_matrix = od["assemble_frame"] @ assemble_matrix
            assemble_matrix.translation = self._assemble_translation(
                assemble_matrix, od["mesh_center"]
            )
            part_centers.append(assemble_matrix.translation.copy())
            assemble_entries.append(
                (od["wrapper_id"], assemble_matrix, assemble_matrix.to_3x3())
            )

        pivot = mathutils.Vector((
            (min(point[axis] for point in part_centers)
             + max(point[axis] for point in part_centers)) / 2
            for axis in range(3)
        ))
        for object_id, matrix, linear in assemble_entries:
            try:
                if linear is None:
                    offset = -(matrix.inverted() @ pivot)
                else:
                    offset = linear.inverted() @ (matrix.translation - pivot)
            except ValueError:
                self.ctx.safe_report(
                    {"WARNING"},
                    f"Cannot calculate an explode offset for object {object_id} with zero scale.",
                )
                offset = mathutils.Vector((0.0, 0.0, 0.0))
            xml.etree.ElementTree.SubElement(
                assemble_elem,
                "assemble_item",
                object_id=str(object_id),
                instance_id="0",
                transform=format_transformation(matrix),
                offset=" ".join(f"{value:.9f}" for value in offset),
            )

        tree = xml.etree.ElementTree.ElementTree(root)

        output = io.BytesIO()
        tree.write(output, encoding="utf-8", xml_declaration=True)
        return output.getvalue().decode("utf-8")
