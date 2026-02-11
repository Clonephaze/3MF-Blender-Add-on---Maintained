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
Standard 3MF exporter.

Exports spec-compliant 3MF files with optional basematerials, texture2dgroup,
PBR display properties, and triangle sets.  This is the default exporter used
when the user does not select Orca or PrusaSlicer paint-segmentation output.
"""

from __future__ import annotations

import collections
import xml.etree.ElementTree
import zipfile
from typing import List, Set, Tuple, TYPE_CHECKING

import bpy
import mathutils

from ..common.constants import MODEL_NAMESPACE, MODEL_LOCATION
from ..common.extensions import TRIANGLE_SETS_EXTENSION, MATERIALS_EXTENSION
from ..common.logging import debug
from ..common.metadata import Metadata
from ..common.xml import format_transformation

from .archive import write_core_properties
from .components import collect_mesh_objects, detect_linked_duplicates, should_use_components
from .geometry import write_vertices, write_triangles, write_passthrough_triangles, write_metadata
from .materials import (
    write_materials,
    get_triangle_color,
    detect_textured_materials,
    detect_pbr_textured_materials,
    write_textures_to_archive,
    write_texture_relationships,
    write_texture_resources,
    write_pbr_textures_to_archive,
    write_pbr_texture_display_properties,
    write_passthrough_materials,
    write_passthrough_textures_to_archive,
)
from .thumbnail import write_thumbnail
from .triangle_sets import write_triangle_sets

if TYPE_CHECKING:
    from .context import ExportContext


class BaseExporter:
    """Base class for format-specific exporters."""

    def __init__(self, ctx: ExportContext):
        """
        Initialize with reference to the export context.

        :param ctx: The ExportContext with settings and state.
        """
        self.ctx = ctx

    def attr(self, name: str) -> str:
        """
        Get attribute name, optionally with namespace prefix.

        In Orca/PrusaSlicer mode, attributes should not have namespace prefixes.
        In standard 3MF mode with default_namespace, they need the prefix.
        """
        if (
            self.ctx.options.use_orca_format in ("PAINT", "STANDARD")
            or self.ctx.options.mmu_slicer_format == "PRUSA"
        ):
            return name
        return f"{{{MODEL_NAMESPACE}}}{name}"


class StandardExporter(BaseExporter):
    """Exports standard 3MF files (core spec with optional basematerials and triangle sets)."""

    def execute(
        self,
        context: bpy.types.Context,
        archive: zipfile.ZipFile,
        blender_objects,
        global_scale: float,
    ) -> Set[str]:
        """
        Standard 3MF export (non-Orca mode).

        Uses core 3MF spec with optional basematerials and triangle sets.
        """
        ctx = self.ctx

        # Register all active extension namespaces with ElementTree
        ctx.extension_manager.register_namespaces(xml.etree.ElementTree)

        # Register MODEL_NAMESPACE as the default namespace (empty prefix)
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)

        # Create model root element
        root = xml.etree.ElementTree.Element(f"{{{MODEL_NAMESPACE}}}model")

        scene_metadata = Metadata()
        scene_metadata.retrieve(bpy.context.scene)
        write_metadata(root, scene_metadata, ctx.options.use_orca_format)

        resources_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}resources"
        )

        # Resolve all mesh objects recursively (descends into nested empties)
        # Used for material scanning; write_objects handles hierarchy itself.
        all_mesh_objects = collect_mesh_objects(
            blender_objects, export_hidden=ctx.options.export_hidden
        )

        (
            ctx.material_name_to_index,
            ctx.next_resource_id,
            ctx.material_resource_id,
            basematerials_element,
        ) = write_materials(
            resources_element,
            all_mesh_objects,
            ctx.options.use_orca_format,
            ctx.vertex_colors,
            ctx.next_resource_id,
        )

        # Detect PBR textured materials FIRST — these use pbmetallictexturedisplayproperties
        pbr_textured_materials = detect_pbr_textured_materials(all_mesh_objects)

        if pbr_textured_materials and basematerials_element is not None:
            for mat_name, pbr_info in pbr_textured_materials.items():
                if pbr_info.get("roughness") or pbr_info.get("metallic"):
                    ctx.pbr_material_names.add(mat_name)

            if ctx.pbr_material_names:
                debug(f"Detected PBR textured materials: {list(ctx.pbr_material_names)}")
                ctx.extension_manager.activate(MATERIALS_EXTENSION.namespace)

                pbr_image_to_path = write_pbr_textures_to_archive(
                    archive, pbr_textured_materials
                )

                if pbr_image_to_path:
                    write_texture_relationships(archive, pbr_image_to_path)

                    material_to_display_props, ctx.next_resource_id = (
                        write_pbr_texture_display_properties(
                            resources_element,
                            pbr_textured_materials,
                            pbr_image_to_path,
                            ctx.next_resource_id,
                            basematerials_element,
                        )
                    )
                    debug(
                        f"Created PBR display properties for {len(material_to_display_props)} materials"
                    )

        # Detect and export textured materials — including PBR materials.
        # PBR materials need a texture2dgroup for UV coordinate data;
        # pbmetallictexturedisplayproperties are display hints only.
        textured_materials = detect_textured_materials(all_mesh_objects)
        ctx.texture_groups = {}

        if textured_materials:
            debug(
                f"Detected {len(textured_materials)} textured materials"
            )
            ctx.extension_manager.activate(MATERIALS_EXTENSION.namespace)

            image_to_path = write_textures_to_archive(
                archive, textured_materials
            )
            write_texture_relationships(archive, image_to_path)

            ctx.texture_groups, ctx.next_resource_id = write_texture_resources(
                resources_element,
                textured_materials,
                image_to_path,
                ctx.next_resource_id,
                ctx.options.coordinate_precision,
            )
            debug(f"Created {len(ctx.texture_groups)} texture groups")

        # Write passthrough texture images to the archive BEFORE writing XML references
        passthrough_image_paths = write_passthrough_textures_to_archive(archive)
        if passthrough_image_paths:
            passthrough_rel_paths = {
                path: f"/{path}" for path in passthrough_image_paths
            }
            write_texture_relationships(archive, passthrough_rel_paths)

        # Write passthrough materials (compositematerials, multiproperties, etc.)
        ctx.next_resource_id, passthrough_written, ctx.passthrough_id_remap = (
            write_passthrough_materials(resources_element, ctx.next_resource_id)
        )
        if passthrough_written:
            ctx.extension_manager.activate(MATERIALS_EXTENSION.namespace)

        ctx._progress_update(15, "Writing objects...")
        ctx._progress_range = (15, 95)
        self.write_objects(root, resources_element, blender_objects, global_scale)
        ctx._progress_range = None

        document = xml.etree.ElementTree.ElementTree(root)
        with archive.open(MODEL_LOCATION, "w", force_zip64=True) as f:
            document.write(f, xml_declaration=True, encoding="UTF-8")

        write_core_properties(archive)
        write_thumbnail(archive)

        ctx._progress_update(100, "Finalizing export...")
        return ctx.finalize_export(archive)

    def write_objects(
        self,
        root: xml.etree.ElementTree.Element,
        resources_element: xml.etree.ElementTree.Element,
        blender_objects: List[bpy.types.Object],
        global_scale: float,
    ) -> None:
        """
        Writes a group of objects into the 3MF archive.

        If use_components is enabled, detects linked duplicates and exports them
        as component instances for smaller file sizes.
        """
        ctx = self.ctx
        transformation = mathutils.Matrix.Scale(global_scale, 4)

        # Detect linked duplicates if component optimization is enabled
        component_groups = {}
        if ctx.options.use_components:
            component_groups = detect_linked_duplicates(blender_objects)

            if component_groups and should_use_components(
                component_groups, blender_objects
            ):
                debug(
                    f"Using component optimization: {len(component_groups)} component groups detected"
                )
                ctx.safe_report(
                    {"INFO"},
                    f"Using component optimization: {len(component_groups)} component groups detected",
                )

                for mesh_data, group in component_groups.items():
                    representative_obj = group.objects[0]
                    component_id = self._write_component_definition(
                        resources_element, representative_obj
                    )
                    group.component_id = component_id
                    debug(
                        f"Component definition {component_id}: '{mesh_data.name}' "
                        f"({len(group.objects)} instances)"
                    )
            else:
                component_groups = {}

        build_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}build"
        )
        hidden_skipped = 0

        total_objects = sum(
            1
            for obj in blender_objects
            if not (obj.hide_get() and not ctx.options.export_hidden)
            and obj.parent is None
            and obj.type in {"MESH", "EMPTY"}
        )
        processed_objects = 0

        for blender_object in blender_objects:
            if blender_object.hide_get() and not ctx.options.export_hidden:
                hidden_skipped += 1
                continue
            if blender_object.parent is not None:
                continue
            if blender_object.type not in {"MESH", "EMPTY"}:
                continue

            processed_objects += 1
            if total_objects > 0:
                progress_range = ctx._progress_range or (15, 95)
                progress_min, progress_max = progress_range
                progress = progress_min + int(
                    (processed_objects / total_objects) * (progress_max - progress_min)
                )
                ctx._progress_update(
                    progress, f"Writing {processed_objects}/{total_objects} objects..."
                )

            # Check if this object is a component instance
            if (
                component_groups
                and blender_object.type == "MESH"
                and blender_object.data in component_groups
            ):
                objectid = self._write_component_instance(
                    resources_element,
                    blender_object,
                    component_groups[blender_object.data].component_id,
                )
            else:
                objectid, mesh_transformation = self.write_object_resource(
                    resources_element, blender_object
                )

            item_element = xml.etree.ElementTree.SubElement(
                build_element, f"{{{MODEL_NAMESPACE}}}item"
            )
            ctx.num_written += 1
            item_element.attrib[self.attr("objectid")] = str(objectid)

            mesh_transformation = transformation @ blender_object.matrix_world
            if mesh_transformation != mathutils.Matrix.Identity(4):
                item_element.attrib[self.attr("transform")] = format_transformation(
                    mesh_transformation
                )

            metadata = Metadata()
            metadata.retrieve(blender_object)
            if "3mf:partnumber" in metadata:
                item_element.attrib[self.attr("partnumber")] = metadata[
                    "3mf:partnumber"
                ].value
                del metadata["3mf:partnumber"]
            if metadata:
                metadatagroup_element = xml.etree.ElementTree.SubElement(
                    item_element, f"{{{MODEL_NAMESPACE}}}metadatagroup"
                )
                write_metadata(metadatagroup_element, metadata, ctx.options.use_orca_format)

        if hidden_skipped > 0:
            ctx.safe_report(
                {"INFO"},
                f"Skipped {hidden_skipped} hidden object(s). "
                "Enable 'Include Hidden' to export them.",
            )

    def write_object_resource(
        self,
        resources_element: xml.etree.ElementTree.Element,
        blender_object: bpy.types.Object,
    ) -> Tuple[int, mathutils.Matrix]:
        """
        Write a single Blender object and all of its children to the resources of a 3MF document.
        """
        ctx = self.ctx
        debug(
            f"write_object_resource called for: {blender_object.name}, type: {blender_object.type}"
        )

        new_resource_id = ctx.next_resource_id
        ctx.next_resource_id += 1
        object_element = xml.etree.ElementTree.SubElement(
            resources_element, f"{{{MODEL_NAMESPACE}}}object"
        )
        object_element.attrib[self.attr("id")] = str(new_resource_id)
        object_name = str(blender_object.name)
        object_element.attrib[self.attr("name")] = object_name

        metadata = Metadata()
        metadata.retrieve(blender_object)
        if "3mf:object_type" in metadata:
            object_type = metadata["3mf:object_type"].value
            if object_type != "model":
                object_element.attrib[self.attr("type")] = object_type
            del metadata["3mf:object_type"]

        if blender_object.mode == "EDIT":
            blender_object.update_from_editmode()
        mesh_transformation = blender_object.matrix_world

        child_objects = blender_object.children
        components_element = None
        if child_objects:
            # Filter to MESH and EMPTY children (recurse into nested empties)
            exportable_children = [
                child for child in blender_object.children
                if child.type in {"MESH", "EMPTY"}
            ]
            if exportable_children:
                components_element = xml.etree.ElementTree.SubElement(
                    object_element, f"{{{MODEL_NAMESPACE}}}components"
                )
                for child in exportable_children:
                    child_id, child_transformation = self.write_object_resource(
                        resources_element, child
                    )
                    child_transformation = (
                        mesh_transformation.inverted_safe() @ child_transformation
                    )
                    component_element = xml.etree.ElementTree.SubElement(
                        components_element, f"{{{MODEL_NAMESPACE}}}component"
                    )
                    ctx.num_written += 1
                    component_element.attrib[self.attr("objectid")] = str(child_id)
                    if child_transformation != mathutils.Matrix.Identity(4):
                        component_element.attrib[self.attr("transform")] = (
                            format_transformation(child_transformation)
                        )

        # Get vertex data (may need to apply modifiers)
        original_object = blender_object
        if ctx.options.use_mesh_modifiers:
            dependency_graph = bpy.context.evaluated_depsgraph_get()
            blender_object = blender_object.evaluated_get(dependency_graph)

        try:
            mesh = blender_object.to_mesh()
        except RuntimeError:
            return new_resource_id, mesh_transformation
        if mesh is None:
            return new_resource_id, mesh_transformation

        mesh.calc_loop_triangles()
        debug(
            f"  Got mesh: {len(mesh.vertices)} vertices, {len(mesh.loop_triangles)} triangles"
        )

        if len(mesh.vertices) > 0:
            if child_objects:
                mesh_id = ctx.next_resource_id
                ctx.next_resource_id += 1
                mesh_object_element = xml.etree.ElementTree.SubElement(
                    resources_element, f"{{{MODEL_NAMESPACE}}}object"
                )
                mesh_object_element.attrib[self.attr("id")] = str(mesh_id)
                component_element = xml.etree.ElementTree.SubElement(
                    components_element, f"{{{MODEL_NAMESPACE}}}component"
                )
                ctx.num_written += 1
                component_element.attrib[self.attr("objectid")] = str(mesh_id)
            else:
                mesh_object_element = object_element

            mesh_element = xml.etree.ElementTree.SubElement(
                mesh_object_element, f"{{{MODEL_NAMESPACE}}}mesh"
            )

            most_common_material_list_index = 0

            debug(
                f"[standard] write_object_resource: {blender_object.name}, mode={ctx.options.use_orca_format}, "
                f"slicer={ctx.options.mmu_slicer_format}"
            )
            debug(
                f"  mesh has {len(mesh.loop_triangles)} triangles, {len(blender_object.material_slots)} material slots"
            )

            # Check for passthrough multiproperties pid (round-trip export)
            original_mesh_data = original_object.data
            passthrough_pid = original_mesh_data.get("3mf_passthrough_pid")
            use_passthrough = False

            if passthrough_pid and ctx.passthrough_id_remap:
                id_remap = ctx.passthrough_id_remap
                remapped_pid = id_remap.get(passthrough_pid, passthrough_pid)
                object_element.attrib[self.attr("pid")] = str(remapped_pid)
                object_element.attrib[self.attr("pindex")] = "0"
                use_passthrough = True
                debug(f"  Using passthrough multiproperties pid={passthrough_pid} -> {remapped_pid}")

            if not use_passthrough:
                # Check if this object has any textured materials
                has_textured_material = False
                if ctx.texture_groups:
                    for mat_slot in blender_object.material_slots:
                        if (
                            mat_slot.material
                            and mat_slot.material.name in ctx.texture_groups
                        ):
                            has_textured_material = True
                            break

                # In STANDARD mode, use face colors mapped to colorgroup IDs
                if (
                    ctx.options.use_orca_format == "STANDARD"
                    and ctx.vertex_colors
                    and ctx.options.mmu_slicer_format == "ORCA"
                ):
                    color_counts = {}
                    for triangle in mesh.loop_triangles:
                        triangle_color = get_triangle_color(mesh, triangle, blender_object)
                        debug(
                            f"  triangle {triangle.index}: material_index={triangle.material_index}, "
                            f"color={triangle_color}"
                        )
                        if triangle_color and triangle_color in ctx.vertex_colors:
                            color_counts[triangle_color] = (
                                color_counts.get(triangle_color, 0) + 1
                            )

                    debug(f"  color_counts: {color_counts}")
                    if color_counts:
                        most_common_color = max(color_counts, key=color_counts.get)
                        colorgroup_id = ctx.vertex_colors[most_common_color]
                        object_element.attrib[self.attr("pid")] = str(colorgroup_id)
                        object_element.attrib[self.attr("pindex")] = "0"
                        most_common_material_list_index = colorgroup_id
                elif not has_textured_material:
                    if ctx.material_name_to_index:
                        material_indices = [
                            triangle.material_index for triangle in mesh.loop_triangles
                        ]

                        if material_indices and blender_object.material_slots:
                            counter = collections.Counter(material_indices)
                            most_common_material_object_index = counter.most_common(1)[0][0]
                            most_common_material = blender_object.material_slots[
                                most_common_material_object_index
                            ].material

                            if most_common_material is not None:
                                most_common_material_list_index = (
                                    ctx.material_name_to_index[
                                        most_common_material.name
                                    ]
                                )
                                object_element.attrib[self.attr("pid")] = str(
                                    ctx.material_resource_id
                                )
                                object_element.attrib[self.attr("pindex")] = str(
                                    most_common_material_list_index
                                )

            write_vertices(
                mesh_element,
                mesh.vertices,
                ctx.options.use_orca_format,
                ctx.options.coordinate_precision,
            )

            # Generate segmentation strings from UV texture if in PAINT mode
            segmentation_strings = {}
            debug(
                f"[standard] Checking PAINT export: mode={ctx.options.use_orca_format}",
                f", has_uv={bool(mesh.uv_layers.active)}"
            )
            if ctx.options.use_orca_format == "PAINT" and mesh.uv_layers.active:
                segmentation_strings = self._extract_segmentation(
                    original_object, blender_object, mesh
                )

            debug(
                f"[standard] Calling write_triangles with {len(segmentation_strings)} segmentation strings"
            )

            if use_passthrough and mesh.uv_layers.active:
                write_passthrough_triangles(
                    mesh_element, mesh, passthrough_pid, remapped_pid,
                    ctx.options.use_orca_format, ctx.options.coordinate_precision,
                )
            else:
                write_triangles(
                    mesh_element,
                    mesh.loop_triangles,
                    most_common_material_list_index,
                    blender_object.material_slots,
                    ctx.material_name_to_index,
                    ctx.options.use_orca_format,
                    ctx.options.mmu_slicer_format,
                    ctx.vertex_colors,
                    mesh,
                    blender_object,
                    ctx.texture_groups or None,
                    str(ctx.material_resource_id)
                    if ctx.material_resource_id
                    else None,
                    segmentation_strings,
                )

            # Write triangle sets if present (auto-export utility metadata)
            # Skipped when PAINT mode is active since segmentation data replaces it
            if ctx.options.use_orca_format != "PAINT" and "3mf_triangle_set" in mesh.attributes:
                # Activate extension on first use
                if not ctx.extension_manager.is_active(TRIANGLE_SETS_EXTENSION.namespace):
                    ctx.extension_manager.activate(TRIANGLE_SETS_EXTENSION.namespace)
                    debug("Activated Triangle Sets extension")
                write_triangle_sets(mesh_element, mesh)

            # Write metadata
            if "3mf:partnumber" in metadata:
                mesh_object_element.attrib[self.attr("partnumber")] = metadata[
                    "3mf:partnumber"
                ].value
                del metadata["3mf:partnumber"]
            if "3mf:object_type" in metadata:
                object_type = metadata["3mf:object_type"].value
                if object_type != "model" and object_type != "other":
                    mesh_object_element.attrib[self.attr("type")] = object_type
                del metadata["3mf:object_type"]
            if metadata:
                metadatagroup_element = xml.etree.ElementTree.SubElement(
                    object_element, f"{{{MODEL_NAMESPACE}}}metadatagroup"
                )
                write_metadata(metadatagroup_element, metadata, ctx.options.use_orca_format)

        return new_resource_id, mesh_transformation

    def _extract_segmentation(
        self,
        original_object: bpy.types.Object,
        eval_object: bpy.types.Object,
        mesh: bpy.types.Mesh,
    ) -> dict:
        """
        Extract segmentation strings from a paint texture on the given object.

        Shared logic used by multiple exporters.

        :param original_object: The original (non-evaluated) Blender object.
        :param eval_object: The evaluated Blender object (with modifiers applied).
        :param mesh: The mesh with loop_triangles already calculated.
        :return: Dict mapping loop_triangle index -> hex segmentation string.
        """
        import ast
        from ..common.colors import hex_to_rgb
        from .segmentation import texture_to_segmentation

        ctx = self.ctx
        original_mesh_data = original_object.data
        debug(
            f"  PAINT mode active — checking custom properties on '{original_mesh_data.name}'"
        )

        if not (
            "3mf_is_paint_texture" in original_mesh_data
            and original_mesh_data["3mf_is_paint_texture"]
        ):
            debug("  WARNING: No paint texture flag found for export")
            return {}

        paint_texture = None
        extruder_colors = {}
        default_extruder = original_mesh_data.get("3mf_paint_default_extruder", 0)
        debug(f"  Found paint texture flag, default_extruder={default_extruder}")

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

        if not paint_texture or not extruder_colors:
            debug("  WARNING: No paint texture or extruder colors found for export")
            return {}

        debug(f"  Exporting paint texture '{paint_texture.name}' as segmentation")

        # Create progress callback
        def seg_progress(current, total, message):
            if total > 0:
                seg_pct = current / total
                overall = int(15 + (seg_pct * 80))  # 15-95% range
                ctx._progress_update(overall, message)

        try:
            segmentation_strings = texture_to_segmentation(
                eval_object,
                paint_texture,
                extruder_colors,
                default_extruder,
                progress_callback=seg_progress,
                max_depth=self.ctx.options.subdivision_depth,
            )
            debug(
                f"  Generated {len(segmentation_strings)} segmentation strings from texture"
            )
            return segmentation_strings
        except Exception as e:
            debug(f"  WARNING: Failed to generate segmentation from texture: {e}")
            import traceback
            traceback.print_exc()
            return {}

    def _write_component_definition(
        self,
        resources_element: xml.etree.ElementTree.Element,
        blender_object: bpy.types.Object,
    ) -> int:
        """
        Write a component definition — a reusable mesh resource.

        :param resources_element: The <resources> element to write to.
        :param blender_object: The Blender object (used as representative for this component).
        :return: The resource ID of the component definition.
        """
        ctx = self.ctx
        component_id = ctx.next_resource_id
        ctx.next_resource_id += 1

        object_element = xml.etree.ElementTree.SubElement(
            resources_element, f"{{{MODEL_NAMESPACE}}}object"
        )
        object_element.attrib[self.attr("id")] = str(component_id)
        mesh_name = str(blender_object.data.name)
        object_element.attrib[self.attr("name")] = mesh_name

        if ctx.options.use_mesh_modifiers:
            dependency_graph = bpy.context.evaluated_depsgraph_get()
            eval_object = blender_object.evaluated_get(dependency_graph)
        else:
            eval_object = blender_object

        try:
            mesh = eval_object.to_mesh()
        except RuntimeError:
            return component_id

        if mesh is None:
            return component_id

        mesh.calc_loop_triangles()

        if len(mesh.vertices) > 0:
            mesh_element = xml.etree.ElementTree.SubElement(
                object_element, f"{{{MODEL_NAMESPACE}}}mesh"
            )

            most_common_material_list_index = 0

            has_textured_material = False
            if ctx.texture_groups:
                for mat_slot in blender_object.material_slots:
                    if (
                        mat_slot.material
                        and mat_slot.material.name in ctx.texture_groups
                    ):
                        has_textured_material = True
                        break

            if (
                ctx.options.use_orca_format == "STANDARD"
                and ctx.vertex_colors
                and ctx.options.mmu_slicer_format == "ORCA"
            ):
                color_counts = {}
                for triangle in mesh.loop_triangles:
                    triangle_color = get_triangle_color(mesh, triangle, blender_object)
                    if triangle_color and triangle_color in ctx.vertex_colors:
                        color_counts[triangle_color] = (
                            color_counts.get(triangle_color, 0) + 1
                        )

                if color_counts:
                    most_common_color = max(color_counts, key=color_counts.get)
                    colorgroup_id = ctx.vertex_colors[most_common_color]
                    object_element.attrib[self.attr("pid")] = str(colorgroup_id)
                    object_element.attrib[self.attr("pindex")] = "0"
                    most_common_material_list_index = colorgroup_id
            elif not has_textured_material and ctx.material_name_to_index:
                material_indices = [
                    triangle.material_index for triangle in mesh.loop_triangles
                ]

                if material_indices and blender_object.material_slots:
                    counter = collections.Counter(material_indices)
                    most_common_material_object_index = counter.most_common(1)[0][0]
                    most_common_material = blender_object.material_slots[
                        most_common_material_object_index
                    ].material

                    if most_common_material is not None:
                        most_common_material_list_index = (
                            ctx.material_name_to_index[most_common_material.name]
                        )
                        object_element.attrib[self.attr("pid")] = str(
                            ctx.material_resource_id
                        )
                        object_element.attrib[self.attr("pindex")] = str(
                            most_common_material_list_index
                        )

            write_vertices(
                mesh_element,
                mesh.vertices,
                ctx.options.use_orca_format,
                ctx.options.coordinate_precision,
            )

            write_triangles(
                mesh_element,
                mesh.loop_triangles,
                most_common_material_list_index,
                blender_object.material_slots,
                ctx.material_name_to_index,
                ctx.options.use_orca_format,
                ctx.options.mmu_slicer_format,
                ctx.vertex_colors,
                mesh,
                blender_object,
                ctx.texture_groups or None,
                str(ctx.material_resource_id)
                if ctx.material_resource_id
                else None,
            )

        eval_object.to_mesh_clear()
        return component_id

    def _write_component_instance(
        self,
        resources_element: xml.etree.ElementTree.Element,
        blender_object: bpy.types.Object,
        component_id: int,
    ) -> int:
        """
        Write a component instance — an object that references a component definition.

        :param resources_element: The <resources> element to write to.
        :param blender_object: The Blender object instance.
        :param component_id: The resource ID of the component definition to reference.
        :return: The resource ID of this instance container.
        """
        ctx = self.ctx
        instance_id = ctx.next_resource_id
        ctx.next_resource_id += 1

        object_element = xml.etree.ElementTree.SubElement(
            resources_element, f"{{{MODEL_NAMESPACE}}}object"
        )
        object_element.attrib[self.attr("id")] = str(instance_id)
        object_name = str(blender_object.name)
        object_element.attrib[self.attr("name")] = object_name

        components_element = xml.etree.ElementTree.SubElement(
            object_element, f"{{{MODEL_NAMESPACE}}}components"
        )
        component_element = xml.etree.ElementTree.SubElement(
            components_element, f"{{{MODEL_NAMESPACE}}}component"
        )
        component_element.attrib[self.attr("objectid")] = str(component_id)

        return instance_id
