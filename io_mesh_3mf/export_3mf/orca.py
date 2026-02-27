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
import os
import re
import uuid
import xml.etree.ElementTree
import zipfile
from typing import List, Optional, Set

import bpy
import mathutils

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
from ..common.logging import debug, warn, error
from ..common.xml import format_transformation

from .materials import (
    ORCA_FILAMENT_CODES,
    collect_face_colors,
    get_triangle_color,
)
from .components import collect_mesh_objects
from .segmentation import texture_to_segmentation
from .standard import BaseExporter
from .thumbnail import write_thumbnail
from ..import_3mf.archive import get_stashed_config
from ..slicer_profiles import get_profile_config


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

        # Register namespaces
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)
        xml.etree.ElementTree.register_namespace("p", PRODUCTION_NAMESPACE)
        xml.etree.ElementTree.register_namespace("BambuStudio", BAMBU_NAMESPACE)

        # Collect face colors for Orca export
        ctx.safe_report({"INFO"}, "Collecting face colors for Orca export...")

        # For PAINT mode, collect colors from paint texture metadata instead of face materials
        paint_colors_collected = False
        if ctx.options.use_orca_format == "PAINT":
            mesh_objs_for_paint = collect_mesh_objects(
                blender_objects, export_hidden=ctx.options.export_hidden
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
                blender_objects, ctx.options.use_mesh_modifiers, ctx.safe_report
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

        # Collect mesh objects recursively (walks into nested empties)
        mesh_objects = collect_mesh_objects(
            blender_objects, export_hidden=ctx.options.export_hidden
        )

        if not mesh_objects:
            ctx.safe_report({"ERROR"}, "No mesh objects found to export!")
            archive.close()
            return {"CANCELLED"}

        # Write individual object model files
        object_data = []

        total_mesh_objects = len(mesh_objects)
        for idx, blender_object in enumerate(mesh_objects):
            # Don't update progress here in PAINT mode - let segmentation callback handle it
            if ctx.options.use_orca_format != "PAINT":
                progress = int(((idx + 1) / total_mesh_objects) * 95)
                ctx._progress_update(
                    progress,
                    f"Exporting {idx + 1}/{total_mesh_objects}: {blender_object.name}",
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

            # Get transformation
            transformation = blender_object.matrix_world.copy()
            transformation = mathutils.Matrix.Scale(global_scale, 4) @ transformation

            # Write the individual object model file
            self.write_object_model(
                archive, blender_object, object_path, mesh_id, mesh_uuid,
                idx, total_mesh_objects,
            )

            object_data.append(
                {
                    "wrapper_id": wrapper_id,
                    "mesh_id": mesh_id,
                    "object_path": object_path,
                    "wrapper_uuid": wrapper_uuid,
                    "mesh_uuid": mesh_uuid,
                    "component_uuid": component_uuid,
                    "transformation": transformation,
                    "name": blender_object.name,
                }
            )

            ctx.num_written += 1

        # Detect grouped mode: if any input object is an Empty with
        # children, the caller wants all meshes grouped as a single
        # assembly in the slicer (e.g. TrailPrint3D).
        group_info = None
        for obj in blender_objects:
            if obj.type == "EMPTY" and obj.children:
                group_info = {
                    "wrapper_id": len(object_data) * 2 + 1,
                    "uuid": str(uuid.uuid4()),
                    "name": str(obj.name),
                }
                break

        # Apply bed center offset to transformations (built-in template only)
        bed_offset_x, bed_offset_y = self._get_bed_center_offset()
        if group_info is not None:
            # Grouped mode: bed offset applied to the single group build
            # item; individual component transforms stay relative.
            group_info["bed_offset"] = (bed_offset_x, bed_offset_y)
        elif bed_offset_x != 0.0 or bed_offset_y != 0.0:
            for od in object_data:
                od["transformation"][0][3] += bed_offset_x
                od["transformation"][1][3] += bed_offset_y
            debug(
                f"Applied bed center offset ({bed_offset_x}, {bed_offset_y}) mm "
                f"to {len(object_data)} objects"
            )

        # Write main 3dmodel.model with wrapper objects and build items
        ctx._progress_update(90, "Writing main model...")
        self.write_main_model(archive, object_data, build_uuid, group_info)

        # Write 3D/_rels/3dmodel.model.rels
        ctx._progress_update(93, "Writing relationships...")
        self.write_model_relationships(archive, object_data)

        # Write Orca metadata files
        ctx._progress_update(96, "Writing configuration...")
        self.write_orca_metadata(archive, mesh_objects, object_data, group_info)

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
        obj_index: int = 0,
        total_objects: int = 1,
    ) -> None:
        """Write an individual object model file for Orca Slicer."""
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

        # Vertices
        vertices_elem = xml.etree.ElementTree.SubElement(mesh_elem, "vertices")
        decimals = ctx.options.coordinate_precision
        for vertex in mesh.vertices:
            xml.etree.ElementTree.SubElement(
                vertices_elem,
                "vertex",
                attrib={
                    "x": f"{vertex.co.x:.{decimals}}",
                    "y": f"{vertex.co.y:.{decimals}}",
                    "z": f"{vertex.co.z:.{decimals}}",
                },
            )

        # Generate segmentation strings from UV texture if in PAINT mode
        segmentation_strings = {}
        seam_strings = {}
        support_strings = {}
        if ctx.options.use_orca_format == "PAINT" and mesh.uv_layers.active:
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
                            # Each object gets its share of the 15-90% range
                            obj_start = 15 + ((obj_index / total_objects) * 75)
                            obj_end = 15 + (((obj_index + 1) / total_objects) * 75)
                            overall = int(obj_start + (seg_pct * (obj_end - obj_start)))
                            ctx._progress_update(
                                overall, f"{blender_object.name}: {message}"
                            )

                    try:
                        segmentation_strings = texture_to_segmentation(
                            blender_object,
                            paint_texture,
                            extruder_colors,
                            default_extruder,
                            progress_callback=orca_seg_progress,
                            max_depth=ctx.options.subdivision_depth,
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
                original_object, blender_object, mesh, "SEAM"
            )
            support_strings = self._extract_auxiliary_segmentation(
                original_object, blender_object, mesh, "SUPPORT"
            )

        # Triangles with paint_color
        triangles_elem = xml.etree.ElementTree.SubElement(mesh_elem, "triangles")
        for tri_idx, triangle in enumerate(mesh.loop_triangles):
            tri_attribs = {
                "v1": str(triangle.vertices[0]),
                "v2": str(triangle.vertices[1]),
                "v3": str(triangle.vertices[2]),
            }

            # Check for segmentation string first (PAINT mode with UV texture)
            if segmentation_strings and tri_idx in segmentation_strings:
                seg_string = segmentation_strings[tri_idx]
                if seg_string:
                    tri_attribs["paint_color"] = seg_string
                    # Seam / support are independent of color
                    if seam_strings and tri_idx in seam_strings and seam_strings[tri_idx]:
                        tri_attribs["paint_seam"] = seam_strings[tri_idx]
                    if support_strings and tri_idx in support_strings and support_strings[tri_idx]:
                        tri_attribs["paint_supports"] = support_strings[tri_idx]
                    xml.etree.ElementTree.SubElement(
                        triangles_elem, "triangle", attrib=tri_attribs
                    )
                    continue

            # Fall back to simple paint_color from face material colors
            triangle_color = get_triangle_color(mesh, triangle, blender_object, eval_object)
            if triangle_color and triangle_color in ctx.vertex_colors:
                filament_index = ctx.vertex_colors[triangle_color]
                if filament_index < len(ORCA_FILAMENT_CODES):
                    paint_code = ORCA_FILAMENT_CODES[filament_index]
                    if paint_code:
                        tri_attribs["paint_color"] = paint_code

            # Seam / support for non-segmentation triangles
            if seam_strings and tri_idx in seam_strings and seam_strings[tri_idx]:
                tri_attribs["paint_seam"] = seam_strings[tri_idx]
            if support_strings and tri_idx in support_strings and support_strings[tri_idx]:
                tri_attribs["paint_supports"] = support_strings[tri_idx]

            xml.etree.ElementTree.SubElement(
                triangles_elem, "triangle", attrib=tri_attribs
            )

        # Empty build (geometry is in this file, build is in main model)
        xml.etree.ElementTree.SubElement(root, "build")

        # Clean up mesh
        eval_object.to_mesh_clear()

        # Write to archive
        archive_path = object_path.lstrip("/")
        document = xml.etree.ElementTree.ElementTree(root)
        buffer = io.BytesIO()
        document.write(buffer, xml_declaration=True, encoding="UTF-8")
        xml_content = buffer.getvalue().decode("UTF-8")

        with archive.open(archive_path, "w") as f:
            f.write(xml_content.encode("UTF-8"))

        debug(f"Wrote object model: {archive_path}")

    def write_main_model(
        self,
        archive: zipfile.ZipFile,
        object_data: List[dict],
        build_uuid: str,
        group_info: Optional[dict] = None,
    ) -> None:
        """Write the main 3dmodel.model file with wrapper objects.

        When *group_info* is provided the meshes are written as components
        of a **single** wrapper object with one ``<item>`` in ``<build>``.
        Orca Slicer treats that structure as a grouped assembly that moves
        as one unit on the plate.
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

        if group_info is not None:
            # Grouped mode: single wrapper containing all meshes as components.
            wrapper = xml.etree.ElementTree.SubElement(
                resources,
                "object",
                attrib={
                    "id": str(group_info["wrapper_id"]),
                    "p:UUID": group_info["uuid"],
                    "type": "model",
                },
            )
            components = xml.etree.ElementTree.SubElement(wrapper, "components")
            for obj in object_data:
                comp_transform = format_transformation(obj["transformation"])
                xml.etree.ElementTree.SubElement(
                    components,
                    "component",
                    attrib={
                        "p:path": obj["object_path"],
                        "objectid": str(obj["mesh_id"]),
                        "p:UUID": obj["component_uuid"],
                        "transform": comp_transform,
                    },
                )
        else:
            # Non-grouped: per-object wrappers
            for obj in object_data:
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

        if group_info is not None:
            # Single build item for the grouped assembly
            bed_x, bed_y = group_info.get("bed_offset", (0.0, 0.0))
            group_transform = (
                f"1.000000000 0.000000000 0.000000000 "
                f"0.000000000 1.000000000 0.000000000 "
                f"0.000000000 0.000000000 1.000000000 "
                f"{bed_x:.9f} {bed_y:.9f} 0.000000000"
            )
            xml.etree.ElementTree.SubElement(
                build,
                "item",
                attrib={
                    "objectid": str(group_info["wrapper_id"]),
                    "p:UUID": "00000002-b1ec-4553-aec9-835e5b724bb4",
                    "transform": group_transform,
                    "printable": "1",
                },
            )
        else:
            for idx, obj in enumerate(object_data):
                item_uuid = f"0000000{idx + 2}-b1ec-4553-aec9-835e5b724bb4"
                transform_str = format_transformation(obj["transformation"])

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
        group_info: Optional[dict] = None,
    ) -> None:
        """Write Orca Slicer compatible metadata files to the archive.

        :param archive: The ZIP archive to write into.
        :param blender_objects: The Blender objects being exported.
        :param object_data: Per-object export data dicts from ``execute()``.
        :param group_info: Optional group metadata for grouped assembly export.
        """
        ctx = self.ctx
        debug("Writing Orca metadata files...")

        try:
            # Write project_settings.config from template with updated colors
            project_settings = self.generate_project_settings()
            with archive.open("Metadata/project_settings.config", "w") as f:
                f.write(json.dumps(project_settings, indent=4).encode("utf-8"))
            debug("Wrote project_settings.config")

            # Write model_settings.config with object metadata
            model_settings_xml = self.generate_model_settings(
                blender_objects, object_data, group_info
            )
            with archive.open("Metadata/model_settings.config", "w") as f:
                f.write(model_settings_xml.encode("utf-8"))
            debug("Wrote model_settings.config")

            debug(f"Wrote Orca metadata with {len(ctx.vertex_colors)} color zones")
        except Exception as e:
            error(f"Failed to write Orca metadata: {e}")
            ctx.safe_report({"ERROR"}, f"Failed to write Orca metadata: {e}")
            raise

    # Built-in template bed center (Bambu Lab A1: 256x256 mm, origin bottom-left)
    _BED_CENTER_X = 128.0
    _BED_CENTER_Y = 128.0

    def _get_bed_center_offset(self) -> tuple:
        """Return bed center offset for the built-in project template.

        The built-in template (Bambu Lab A1) uses a bottom-left origin.
        Custom templates and stashed configs get no offset — the caller
        handles positioning.

        :return: ``(offset_x, offset_y)`` in millimeters.
        """
        if self.ctx.project_template_path:
            return (0.0, 0.0)
        if get_stashed_config("Metadata/project_settings.config") is not None:
            return (0.0, 0.0)
        if (
            self.ctx.options.slicer_profile != "NONE"
            and get_profile_config(
                self.ctx.options.slicer_profile,
                "Metadata/project_settings.config",
            ) is not None
        ):
            return (0.0, 0.0)
        return (self._BED_CENTER_X, self._BED_CENTER_Y)

    def _get_dominant_color(self, blender_object: bpy.types.Object) -> Optional[str]:
        """Return the hex colour string of the most-common face material on *blender_object*.

        Used to determine the per-object ``extruder`` value in
        ``model_settings.config`` so Orca assigns the right filament even
        when objects have only a single material (no ``paint_color``
        per-triangle overrides needed).

        Returns ``None`` when no material can be determined.
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
            return None
        if mesh is None:
            return None

        mesh.calc_loop_triangles()

        # Count colours by triangle
        color_counts: dict[str, int] = {}
        for tri in mesh.loop_triangles:
            color = get_triangle_color(mesh, tri, blender_object, eval_obj)
            if color:
                color_counts[color] = color_counts.get(color, 0) + 1

        eval_obj.to_mesh_clear()

        if not color_counts:
            return None
        return max(color_counts, key=color_counts.get)

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

        return settings

    def generate_model_settings(
        self,
        blender_objects: List[bpy.types.Object],
        object_data: List[dict],
        group_info: Optional[dict] = None,
    ) -> str:
        """Generate the model_settings.config XML for Orca Slicer.

        :param blender_objects: The Blender objects being exported.
        :param object_data: Per-object export data dicts from ``execute()``,
            containing ``wrapper_id``, ``mesh_id``, ``transformation``, and ``name``.
        :param group_info: Optional group metadata for grouped assembly export.
        """
        ctx = self.ctx
        root = xml.etree.ElementTree.Element("config")

        # Build a lookup from object name to its object_data entry
        obj_data_by_name: dict = {}
        for od in object_data:
            obj_data_by_name[od["name"]] = od

        if group_info is not None:
            # ----- Grouped mode: single <object> with multiple <part> -----
            object_elem = xml.etree.ElementTree.SubElement(
                root, "object", id=str(group_info["wrapper_id"])
            )
            xml.etree.ElementTree.SubElement(
                object_elem, "metadata", key="name", value=group_info["name"]
            )
            xml.etree.ElementTree.SubElement(
                object_elem, "metadata", key="extruder", value="1"
            )

            for blender_object in blender_objects:
                if blender_object.type != "MESH":
                    continue

                obj_name = str(blender_object.name)
                od = obj_data_by_name.get(obj_name)
                if od is None:
                    continue

                # Determine per-part extruder from dominant face colour
                extruder_value = "1"
                if ctx.vertex_colors:
                    dominant_color = self._get_dominant_color(blender_object)
                    if dominant_color and dominant_color in ctx.vertex_colors:
                        extruder_value = str(ctx.vertex_colors[dominant_color])

                part_elem = xml.etree.ElementTree.SubElement(
                    object_elem, "part", id=str(od["mesh_id"]), subtype="normal_part"
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

        else:
            # ----- Non-grouped: per-object <object> entries -----
            for blender_object in blender_objects:
                if blender_object.type != "MESH":
                    continue

                obj_name = str(blender_object.name)
                od = obj_data_by_name.get(obj_name)
                if od is None:
                    continue

                wrapper_id = od["wrapper_id"]
                mesh_id = od["mesh_id"]

                # Determine the dominant extruder for this object from its
                # most-common face material colour mapped through vertex_colors.
                extruder_value = "1"
                if ctx.vertex_colors:
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

                # Per-object setting overrides (passthrough, no validation)
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

                part_elem = xml.etree.ElementTree.SubElement(
                    object_elem, "part", id=str(mesh_id), subtype="normal_part"
                )
                xml.etree.ElementTree.SubElement(
                    part_elem, "metadata", key="name", value=obj_name
                )
                matrix_value = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
                xml.etree.ElementTree.SubElement(
                    part_elem, "metadata", key="matrix", value=matrix_value
                )

        # Add plate metadata with model_instance entries
        plate_elem = xml.etree.ElementTree.SubElement(root, "plate")
        xml.etree.ElementTree.SubElement(
            plate_elem, "metadata", key="plater_id", value="1"
        )
        xml.etree.ElementTree.SubElement(
            plate_elem, "metadata", key="plater_name", value=""
        )
        xml.etree.ElementTree.SubElement(
            plate_elem, "metadata", key="locked", value="false"
        )
        xml.etree.ElementTree.SubElement(
            plate_elem, "metadata", key="filament_map_mode", value="Auto For Flush"
        )

        if group_info is not None:
            # Single model_instance for the grouped assembly
            instance_elem = xml.etree.ElementTree.SubElement(
                plate_elem, "model_instance"
            )
            xml.etree.ElementTree.SubElement(
                instance_elem, "metadata", key="object_id",
                value=str(group_info["wrapper_id"]),
            )
            xml.etree.ElementTree.SubElement(
                instance_elem, "metadata", key="instance_id", value="0"
            )
            xml.etree.ElementTree.SubElement(
                instance_elem, "metadata", key="identify_id",
                value=str(group_info["wrapper_id"]),
            )
        else:
            for od in object_data:
                instance_elem = xml.etree.ElementTree.SubElement(
                    plate_elem, "model_instance"
                )
                xml.etree.ElementTree.SubElement(
                    instance_elem,
                    "metadata",
                    key="object_id",
                    value=str(od["wrapper_id"]),
                )
                xml.etree.ElementTree.SubElement(
                    instance_elem, "metadata", key="instance_id", value="0"
                )
                xml.etree.ElementTree.SubElement(
                    instance_elem,
                    "metadata",
                    key="identify_id",
                    value=str(od["wrapper_id"]),
                )

        # Add assemble section with real world transforms
        assemble_elem = xml.etree.ElementTree.SubElement(root, "assemble")

        if group_info is not None:
            bed_x, bed_y = group_info.get("bed_offset", (0.0, 0.0))
            group_transform = (
                f"1.000000000 0.000000000 0.000000000 "
                f"0.000000000 1.000000000 0.000000000 "
                f"0.000000000 0.000000000 1.000000000 "
                f"{bed_x:.9f} {bed_y:.9f} 0.000000000"
            )
            xml.etree.ElementTree.SubElement(
                assemble_elem,
                "assemble_item",
                object_id=str(group_info["wrapper_id"]),
                instance_id="0",
                transform=group_transform,
                offset="0 0 0",
            )
        else:
            for od in object_data:
                transform_str = format_transformation(od["transformation"])
                xml.etree.ElementTree.SubElement(
                    assemble_elem,
                    "assemble_item",
                    object_id=str(od["wrapper_id"]),
                    instance_id="0",
                    transform=transform_str,
                    offset="0 0 0",
                )

        tree = xml.etree.ElementTree.ElementTree(root)

        output = io.BytesIO()
        tree.write(output, encoding="utf-8", xml_declaration=True)
        return output.getvalue().decode("utf-8")
