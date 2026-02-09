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
Format-specific exporters: Standard 3MF, Orca Slicer, and PrusaSlicer.
"""

import collections
import datetime
import io
import json
import os
import re
import uuid
import xml.etree.ElementTree
import zipfile
from typing import Set, List, Tuple

import bpy
import mathutils

from .constants import (
    MODEL_LOCATION,
    MODEL_NAMESPACE,
    MODEL_REL,
    PRODUCTION_NAMESPACE,
    BAMBU_NAMESPACE,
    RELS_NAMESPACE,
)
from .extensions import (
    PRODUCTION_EXTENSION,
    ORCA_EXTENSION,
)
from .metadata import Metadata, MetadataEntry
from .export_components import (
    detect_linked_duplicates,
    should_use_components,
)
from .utilities import debug, warn, error, hex_to_rgb
from .export_utils import (
    ORCA_FILAMENT_CODES,
    write_core_properties,
    write_thumbnail,
    write_prusa_filament_colors,
    write_materials,
    write_metadata,
    write_vertices,
    write_triangles,
    write_triangle_sets,
    format_transformation,
    collect_face_colors,
    get_triangle_color,
    detect_textured_materials,
    write_textures_to_archive,
    write_texture_relationships,
    write_texture_resources,
    write_passthrough_materials,
    write_passthrough_textures_to_archive,
    detect_pbr_textured_materials,
    write_pbr_textures_to_archive,
    write_pbr_texture_display_properties,
)


def _write_passthrough_triangles(
    mesh_element: xml.etree.ElementTree.Element,
    mesh: bpy.types.Mesh,
    original_pid: str,
    remapped_pid: str,
    use_orca_format: str,
    coordinate_precision: int,
) -> None:
    """
    Write triangles with per-vertex multiproperties indices from the UV map.

    For round-trip export of multiproperties, maps each triangle vertex's UV
    coordinates back to tex2coord indices in the stored texture2dgroup, then
    finds the matching multi entry index.

    :param mesh_element: The <mesh> XML element.
    :param mesh: The Blender mesh with UV data.
    :param original_pid: The ORIGINAL multiproperties ID (for scene data lookup).
    :param remapped_pid: The REMAPPED multiproperties ID (for XML output).
    :param use_orca_format: Material export mode string.
    :param coordinate_precision: Decimal precision for coordinates.
    """
    scene = bpy.context.scene

    # Load multiproperties data to get the multi entries
    mp_data_str = scene.get("3mf_multiproperties", "{}")
    try:
        mp_data = json.loads(mp_data_str)
    except json.JSONDecodeError:
        warn("Failed to parse multiproperties for passthrough triangle export")
        return

    multiprop = mp_data.get(original_pid)
    if not multiprop:
        warn(f"Multiproperties {original_pid} not found in passthrough data")
        return

    # Get the first texture2dgroup pid to use for UV → index mapping
    pids = multiprop.get("pids", "").split()
    if not pids:
        warn("Multiproperties has no pids")
        return

    # Load texture group data to get tex2coords
    tg_data_str = scene.get("3mf_texture_groups", "{}")
    try:
        tg_data = json.loads(tg_data_str)
    except json.JSONDecodeError:
        warn("Failed to parse texture groups for passthrough triangle export")
        return

    # Find the first texture group pid (skip basematerials pid)
    tex2coords = None
    for pid in pids:
        if pid in tg_data:
            tex2coords = tg_data[pid].get("tex2coords", [])
            break

    if not tex2coords:
        warn("No tex2coords found for passthrough triangle UV mapping")
        return

    # Build the multi lookup: for each multi entry, store its pindices
    multis = multiprop.get("multis", [])

    # Build reverse lookup: tex2coord index → multi entry index
    # In the common case (constant basematerial, shared UV across groups),
    # multi entry index == tex2coord index for the first texture group
    # For the general case, build a lookup from first-texture-group-index to multi index
    tex_idx_to_multi = {}
    tex_group_position = None  # Which position in pids is the first texture group
    for i, pid in enumerate(pids):
        if pid in tg_data:
            tex_group_position = i
            break

    if tex_group_position is not None:
        for multi_idx, m in enumerate(multis):
            pindices_str = m.get("pindices", "")
            pindices = pindices_str.split()
            if tex_group_position < len(pindices):
                tex_idx = pindices[tex_group_position]
                # Only store first occurrence (in case of duplicates)
                if tex_idx not in tex_idx_to_multi:
                    tex_idx_to_multi[tex_idx] = multi_idx

    # Build UV → tex2coord index lookup with tolerance
    def find_tex2coord_index(u: float, v: float) -> int:
        """Find the closest tex2coord index for a UV pair."""
        best_idx = 0
        best_dist = float("inf")
        for idx, coord in enumerate(tex2coords):
            if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                du = u - coord[0]
                dv = v - coord[1]
                dist = du * du + dv * dv
                if dist < best_dist:
                    best_dist = dist
                    best_idx = idx
        return best_idx

    # Write triangles
    triangles_element = xml.etree.ElementTree.SubElement(
        mesh_element, f"{{{MODEL_NAMESPACE}}}triangles"
    )

    # Use short attribute names for BASEMATERIAL/PAINT modes
    if use_orca_format in ("PAINT", "BASEMATERIAL"):
        v1_name, v2_name, v3_name = "v1", "v2", "v3"
        p1_name, p2_name, p3_name = "p1", "p2", "p3"
    else:
        ns = MODEL_NAMESPACE
        v1_name = f"{{{ns}}}v1"
        v2_name = f"{{{ns}}}v2"
        v3_name = f"{{{ns}}}v3"
        p1_name = f"{{{ns}}}p1"
        p2_name = f"{{{ns}}}p2"
        p3_name = f"{{{ns}}}p3"

    triangle_name = f"{{{MODEL_NAMESPACE}}}triangle"
    uv_layer = mesh.uv_layers.active

    for triangle in mesh.loop_triangles:
        tri_elem = xml.etree.ElementTree.SubElement(triangles_element, triangle_name)
        tri_elem.attrib[v1_name] = str(triangle.vertices[0])
        tri_elem.attrib[v2_name] = str(triangle.vertices[1])
        tri_elem.attrib[v3_name] = str(triangle.vertices[2])

        # Set pid to multiproperties ID on each triangle so reimport
        # correctly enters the multiproperties resolution path
        if use_orca_format in ("PAINT", "BASEMATERIAL"):
            tri_elem.attrib["pid"] = str(remapped_pid)
        else:
            tri_elem.attrib[f"{{{MODEL_NAMESPACE}}}pid"] = str(remapped_pid)

        # Map UV coordinates to multi entry indices
        loop_indices = triangle.loops
        uv_data = uv_layer.data

        uv1 = uv_data[loop_indices[0]].uv
        uv2 = uv_data[loop_indices[1]].uv
        uv3 = uv_data[loop_indices[2]].uv

        tex_idx1 = find_tex2coord_index(uv1[0], uv1[1])
        tex_idx2 = find_tex2coord_index(uv2[0], uv2[1])
        tex_idx3 = find_tex2coord_index(uv3[0], uv3[1])

        # Map tex2coord index → multi entry index
        multi_idx1 = tex_idx_to_multi.get(str(tex_idx1), tex_idx1)
        multi_idx2 = tex_idx_to_multi.get(str(tex_idx2), tex_idx2)
        multi_idx3 = tex_idx_to_multi.get(str(tex_idx3), tex_idx3)

        tri_elem.attrib[p1_name] = str(multi_idx1)
        tri_elem.attrib[p2_name] = str(multi_idx2)
        tri_elem.attrib[p3_name] = str(multi_idx3)

    debug(
        f"Wrote {len(mesh.loop_triangles)} passthrough triangles "
        f"with multiproperties UV indices"
    )


class BaseExporter:
    """Base class for format-specific exporters."""

    def __init__(self, operator):
        """
        Initialize with reference to the Export3MF operator.

        :param operator: The Export3MF operator instance with settings and state.
        """
        self.op = operator

    def attr(self, name: str) -> str:
        """
        Get attribute name, optionally with namespace prefix.

        In Orca/PrusaSlicer mode, attributes should not have namespace prefixes.
        In standard 3MF mode with default_namespace, they need the prefix.
        """
        if (
            self.op.use_orca_format in ("PAINT", "BASEMATERIAL")
            or self.op.mmu_slicer_format == "PRUSA"
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
        # Activate Triangle Sets extension if enabled
        if self.op.export_triangle_sets:
            from .extensions import TRIANGLE_SETS_EXTENSION

            self.op.extension_manager.activate(TRIANGLE_SETS_EXTENSION.namespace)
            debug("Activated Triangle Sets extension")

        # Register all active extension namespaces with ElementTree
        self.op.extension_manager.register_namespaces(xml.etree.ElementTree)

        # Register MODEL_NAMESPACE as the default namespace (empty prefix)
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)

        # Create model root element
        root = xml.etree.ElementTree.Element(f"{{{MODEL_NAMESPACE}}}model")

        scene_metadata = Metadata()
        scene_metadata.retrieve(bpy.context.scene)
        write_metadata(root, scene_metadata, self.op.use_orca_format)

        resources_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}resources"
        )

        (
            self.op.material_name_to_index,
            self.op.next_resource_id,
            self.op.material_resource_id,
            basematerials_element,
        ) = write_materials(
            resources_element,
            blender_objects,
            self.op.use_orca_format,
            self.op.vertex_colors,
            self.op.next_resource_id,
        )

        # Detect PBR textured materials FIRST - these will use pbmetallictexturedisplayproperties
        # with basecolortextureid, NOT texture2dgroup
        pbr_textured_materials = detect_pbr_textured_materials(blender_objects)
        pbr_material_names = set()  # Track materials that have full PBR textures

        if pbr_textured_materials and basematerials_element is not None:
            # Check which materials have actual PBR textures (roughness or metallic)
            for mat_name, pbr_info in pbr_textured_materials.items():
                if pbr_info.get("roughness") or pbr_info.get("metallic"):
                    pbr_material_names.add(mat_name)

            if pbr_material_names:
                debug(f"Detected PBR textured materials: {list(pbr_material_names)}")
                # Activate Materials Extension
                from .extensions import MATERIALS_EXTENSION

                self.op.extension_manager.activate(MATERIALS_EXTENSION.namespace)

                # Write PBR texture files to archive (includes base_color for PBR materials)
                pbr_image_to_path = write_pbr_textures_to_archive(
                    archive, pbr_textured_materials
                )

                # Write texture relationships for PBR textures
                if pbr_image_to_path:
                    write_texture_relationships(archive, pbr_image_to_path)

                    # Write pbmetallictexturedisplayproperties elements and link to basematerials
                    # This includes basecolortextureid for the base color texture
                    material_to_display_props, self.op.next_resource_id = (
                        write_pbr_texture_display_properties(
                            resources_element,
                            pbr_textured_materials,
                            pbr_image_to_path,
                            self.op.next_resource_id,
                            basematerials_element,
                        )
                    )
                    debug(
                        f"Created PBR display properties for {len(material_to_display_props)} materials"
                    )

                    # Store which materials use PBR (triangles should reference basematerials, not texture2dgroup)
                    self.op.pbr_material_names = pbr_material_names

        # Detect and export textured materials that DON'T have PBR textures
        # These use texture2dgroup for UV-mapped base color only
        textured_materials = detect_textured_materials(blender_objects)
        self.op.texture_groups = {}

        # Filter out materials that have full PBR textures (they use pbmetallictexturedisplayproperties)
        textured_materials_filtered = {
            mat_name: tex_info
            for mat_name, tex_info in textured_materials.items()
            if mat_name not in pbr_material_names
        }

        if textured_materials_filtered:
            debug(
                f"Detected {len(textured_materials_filtered)} base-color-only textured materials"
            )
            # Activate Materials Extension
            from .extensions import MATERIALS_EXTENSION

            self.op.extension_manager.activate(MATERIALS_EXTENSION.namespace)

            # Write texture files to archive
            image_to_path = write_textures_to_archive(
                archive, textured_materials_filtered
            )

            # Write texture relationships (OPC requirement)
            write_texture_relationships(archive, image_to_path)

            # Write texture2d and texture2dgroup elements
            self.op.texture_groups, self.op.next_resource_id = write_texture_resources(
                resources_element,
                textured_materials_filtered,
                image_to_path,
                self.op.next_resource_id,
                self.op.coordinate_precision,
            )
            debug(f"Created {len(self.op.texture_groups)} texture groups")

        # Write passthrough texture images to the archive BEFORE writing XML references
        passthrough_image_paths = write_passthrough_textures_to_archive(archive)
        if passthrough_image_paths:
            # Write texture relationships for passthrough textures
            # Convert to {image_name: /archive/path} format for write_texture_relationships
            passthrough_rel_paths = {
                path: f"/{path}" for path in passthrough_image_paths
            }
            write_texture_relationships(archive, passthrough_rel_paths)

        # Write passthrough materials (compositematerials, multiproperties, etc.)
        # These are stored from import for round-trip support
        # IDs are remapped to avoid conflicts with newly created materials
        self.op.next_resource_id, passthrough_written, self.op.passthrough_id_remap = (
            write_passthrough_materials(resources_element, self.op.next_resource_id)
        )
        # Activate Materials Extension if passthrough data was written
        if passthrough_written:
            from .extensions import MATERIALS_EXTENSION

            self.op.extension_manager.activate(MATERIALS_EXTENSION.namespace)

        self.op._progress_update(15, "Writing objects...")
        # Set progress range for object writing
        self.op._progress_range = (15, 95)
        self.write_objects(root, resources_element, blender_objects, global_scale)
        self.op._progress_range = None

        document = xml.etree.ElementTree.ElementTree(root)
        with archive.open(MODEL_LOCATION, "w", force_zip64=True) as f:
            document.write(
                f,
                xml_declaration=True,
                encoding="UTF-8",
            )

        # Write OPC Core Properties (Dublin Core metadata)
        write_core_properties(archive)

        # Write thumbnail if available from .blend file
        write_thumbnail(archive)

        self.op._progress_update(100, "Finalizing export...")
        return self.op._finalize_export(archive)

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
        transformation = mathutils.Matrix.Scale(global_scale, 4)

        # Detect linked duplicates if component optimization is enabled
        component_groups = {}
        if self.op.use_components:
            component_groups = detect_linked_duplicates(blender_objects)

            if component_groups and should_use_components(
                component_groups, blender_objects
            ):
                debug(
                    f"Using component optimization: {len(component_groups)} component groups detected"
                )
                self.op.safe_report(
                    {"INFO"},
                    f"Using component optimization: {len(component_groups)} component groups detected",
                )

                # First, write component definitions (the shared mesh data)
                for mesh_data, group in component_groups.items():
                    # Use the first object as the representative for this component
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
                # Not enough benefit, export normally
                component_groups = {}

        build_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}build"
        )
        hidden_skipped = 0

        # Calculate total objects for progress tracking
        total_objects = sum(
            1
            for blender_object in blender_objects
            if not (blender_object.hide_get() and not self.op.export_hidden)
            and blender_object.parent is None
            and blender_object.type in {"MESH", "EMPTY"}
        )
        processed_objects = 0

        for blender_object in blender_objects:
            if blender_object.hide_get() and not self.op.export_hidden:
                hidden_skipped += 1
                continue
            if blender_object.parent is not None:
                continue
            if blender_object.type not in {"MESH", "EMPTY"}:
                continue

            processed_objects += 1
            if total_objects > 0:
                # Use progress range if set, otherwise 15-95%
                progress_range = getattr(self.op, '_progress_range', (15, 95))
                progress_min, progress_max = progress_range
                progress = progress_min + int((processed_objects / total_objects) * (progress_max - progress_min))
                self.op._progress_update(
                    progress, f"Writing {processed_objects}/{total_objects} objects..."
                )

            # Check if this object is a component instance
            if (
                component_groups
                and blender_object.type == "MESH"
                and blender_object.data in component_groups
            ):
                # Write as component instance (just a reference)
                objectid = self._write_component_instance(
                    resources_element,
                    blender_object,
                    component_groups[blender_object.data].component_id,
                )
            else:
                # Write as normal object with inline mesh
                objectid, mesh_transformation = self.write_object_resource(
                    resources_element, blender_object
                )

            item_element = xml.etree.ElementTree.SubElement(
                build_element, f"{{{MODEL_NAMESPACE}}}item"
            )
            self.op.num_written += 1
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
                write_metadata(metadatagroup_element, metadata, self.op.use_orca_format)

        if hidden_skipped > 0:
            self.op.safe_report(
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
        debug(
            f"write_object_resource called for: {blender_object.name}, type: {blender_object.type}"
        )

        new_resource_id = self.op.next_resource_id
        self.op.next_resource_id += 1
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
            components_element = xml.etree.ElementTree.SubElement(
                object_element, f"{{{MODEL_NAMESPACE}}}components"
            )
            for child in blender_object.children:
                if child.type != "MESH":
                    continue
                child_id, child_transformation = self.write_object_resource(
                    resources_element, child
                )
                child_transformation = (
                    mesh_transformation.inverted_safe() @ child_transformation
                )
                component_element = xml.etree.ElementTree.SubElement(
                    components_element, f"{{{MODEL_NAMESPACE}}}component"
                )
                self.op.num_written += 1
                component_element.attrib[self.attr("objectid")] = str(child_id)
                if child_transformation != mathutils.Matrix.Identity(4):
                    component_element.attrib[self.attr("transform")] = (
                        format_transformation(child_transformation)
                    )

        # Get vertex data (may need to apply modifiers)
        # Store reference to original object for accessing custom properties
        original_object = blender_object
        if self.op.use_mesh_modifiers:
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
                mesh_id = self.op.next_resource_id
                self.op.next_resource_id += 1
                mesh_object_element = xml.etree.ElementTree.SubElement(
                    resources_element, f"{{{MODEL_NAMESPACE}}}object"
                )
                mesh_object_element.attrib[self.attr("id")] = str(mesh_id)
                component_element = xml.etree.ElementTree.SubElement(
                    components_element, f"{{{MODEL_NAMESPACE}}}component"
                )
                self.op.num_written += 1
                component_element.attrib[self.attr("objectid")] = str(mesh_id)
            else:
                mesh_object_element = object_element

            mesh_element = xml.etree.ElementTree.SubElement(
                mesh_object_element, f"{{{MODEL_NAMESPACE}}}mesh"
            )

            # Find the most common material for this mesh
            most_common_material_list_index = 0

            debug(
                f"[export_formats] write_object_resource: {blender_object.name}, mode={self.op.use_orca_format}, "
                f"slicer={self.op.mmu_slicer_format}"
            )
            debug(
                f"  mesh has {len(mesh.loop_triangles)} triangles, {len(blender_object.material_slots)} material slots"
            )

            # Check for passthrough multiproperties pid (round-trip export)
            original_mesh_data = original_object.data
            passthrough_pid = original_mesh_data.get("3mf_passthrough_pid")
            use_passthrough = False

            if passthrough_pid and hasattr(self.op, "passthrough_id_remap"):
                id_remap = self.op.passthrough_id_remap
                remapped_pid = id_remap.get(passthrough_pid, passthrough_pid)
                object_element.attrib[self.attr("pid")] = str(remapped_pid)
                object_element.attrib[self.attr("pindex")] = "0"
                use_passthrough = True
                debug(f"  Using passthrough multiproperties pid={passthrough_pid} -> {remapped_pid}")

            if not use_passthrough:
                # Check if this object has any textured materials
                has_textured_material = False
                if hasattr(self.op, "texture_groups") and self.op.texture_groups:
                    for mat_slot in blender_object.material_slots:
                        if (
                            mat_slot.material
                            and mat_slot.material.name in self.op.texture_groups
                        ):
                            has_textured_material = True
                            break

                # In BASEMATERIAL mode, use face colors mapped to colorgroup IDs
                if (
                    self.op.use_orca_format == "BASEMATERIAL"
                    and self.op.vertex_colors
                    and self.op.mmu_slicer_format == "ORCA"
                ):
                    color_counts = {}
                    for triangle in mesh.loop_triangles:
                        triangle_color = get_triangle_color(mesh, triangle, blender_object)
                        debug(
                            f"  triangle {triangle.index}: material_index={triangle.material_index}, "
                            f"color={triangle_color}"
                        )
                        if triangle_color and triangle_color in self.op.vertex_colors:
                            color_counts[triangle_color] = (
                                color_counts.get(triangle_color, 0) + 1
                            )

                    debug(f"  color_counts: {color_counts}")
                    if color_counts:
                        most_common_color = max(color_counts, key=color_counts.get)
                        colorgroup_id = self.op.vertex_colors[most_common_color]
                        object_element.attrib[self.attr("pid")] = str(colorgroup_id)
                        object_element.attrib[self.attr("pindex")] = "0"
                        most_common_material_list_index = colorgroup_id
                elif not has_textured_material:
                    # Normal material handling - but only if NOT using textured materials
                    # (textured materials use per-triangle pid to reference texture2dgroup)
                    if self.op.material_name_to_index:
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
                                    self.op.material_name_to_index[
                                        most_common_material.name
                                    ]
                                )
                                object_element.attrib[self.attr("pid")] = str(
                                    self.op.material_resource_id
                                )
                                object_element.attrib[self.attr("pindex")] = str(
                                    most_common_material_list_index
                                )

            write_vertices(
                mesh_element,
                mesh.vertices,
                self.op.use_orca_format,
                self.op.coordinate_precision,
            )

            # Generate segmentation strings from UV texture if in PAINT mode
            segmentation_strings = {}
            debug(
                f"[export_formats] Checking PAINT export: mode={self.op.use_orca_format}",
                f", has_uv={bool(mesh.uv_layers.active)}"
            )
            if self.op.use_orca_format == "PAINT" and mesh.uv_layers.active:
                # Check if this mesh was imported with paint texture (has custom properties)
                # Read from original object's data, not the temporary evaluated mesh
                original_mesh_data = original_object.data
                debug(
                    f"  PAINT mode active - checking custom properties on '{original_mesh_data.name}'"
                )
                if (
                    "3mf_is_paint_texture" in original_mesh_data
                    and original_mesh_data["3mf_is_paint_texture"]
                ):
                    paint_texture = None
                    extruder_colors = {}
                    default_extruder = original_mesh_data.get(
                        "3mf_paint_default_extruder", 0
                    )
                    debug(
                        f"  Found paint texture flag, default_extruder={default_extruder}"
                    )

                    # Get the stored extruder colors
                    if "3mf_paint_extruder_colors" in original_mesh_data:
                        import ast

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

                        # Import here to avoid circular dependency
                        from .export_hash_segmentation import texture_to_segmentation

                        # Create progress callback for Standard PAINT mode
                        def std_seg_progress(current, total, message):
                            if total > 0:
                                seg_pct = current / total
                                overall = int(15 + (seg_pct * 80))  # 15-95% range
                                self.op._progress_update(overall, message)

                        try:
                            # Use evaluated object for mesh/UV data access (has calc_loop_triangles called)
                            # Original object's mesh may have empty UV layer data after addon reload
                            segmentation_strings = texture_to_segmentation(
                                blender_object,
                                paint_texture,
                                extruder_colors,
                                default_extruder,
                                progress_callback=std_seg_progress,
                            )
                            debug(
                                f"  Generated {len(segmentation_strings)} segmentation strings from texture"
                            )
                        except Exception as e:
                            debug(
                                f"  WARNING: Failed to generate segmentation from texture: {e}"
                            )
                            import traceback

                            traceback.print_exc()
                            segmentation_strings = {}
                    else:
                        debug(
                            "  WARNING: No paint texture or extruder colors found for export"
                        )

            debug(
                f"[export_formats] Calling write_triangles with {len(segmentation_strings)} segmentation strings"
            )

            if use_passthrough and mesh.uv_layers.active:
                # Write triangles with passthrough multiproperties indices from UV map
                _write_passthrough_triangles(
                    mesh_element, mesh, passthrough_pid, remapped_pid,
                    self.op.use_orca_format, self.op.coordinate_precision,
                )
            else:
                write_triangles(
                    mesh_element,
                    mesh.loop_triangles,
                    most_common_material_list_index,
                    blender_object.material_slots,
                    self.op.material_name_to_index,
                    self.op.use_orca_format,
                    self.op.mmu_slicer_format,
                    self.op.vertex_colors,
                    mesh,
                    blender_object,
                    getattr(self.op, "texture_groups", None),
                    str(self.op.material_resource_id)
                    if self.op.material_resource_id
                    else None,
                    segmentation_strings,  # Pass the generated segmentation strings
                )

            # Write triangle sets if enabled
            if self.op.export_triangle_sets and "3mf_triangle_set" in mesh.attributes:
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
                write_metadata(metadatagroup_element, metadata, self.op.use_orca_format)

        return new_resource_id, mesh_transformation

    def _write_component_definition(
        self,
        resources_element: xml.etree.ElementTree.Element,
        blender_object: bpy.types.Object,
    ) -> int:
        """
        Write a component definition - a reusable mesh resource.

        This writes just the mesh data without a transform.
        The mesh can then be referenced multiple times as component instances.

        :param resources_element: The <resources> element to write to
        :param blender_object: The Blender object (used as representative for this component)
        :return: The resource ID of the component definition
        """
        component_id = self.op.next_resource_id
        self.op.next_resource_id += 1

        object_element = xml.etree.ElementTree.SubElement(
            resources_element, f"{{{MODEL_NAMESPACE}}}object"
        )
        object_element.attrib[self.attr("id")] = str(component_id)
        # Use mesh name for the component definition
        mesh_name = str(blender_object.data.name)
        object_element.attrib[self.attr("name")] = mesh_name

        # Get evaluated mesh (with modifiers if enabled)
        if self.op.use_mesh_modifiers:
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

            # Determine most common material
            most_common_material_list_index = 0

            # Check for textured materials
            has_textured_material = False
            if hasattr(self.op, "texture_groups") and self.op.texture_groups:
                for mat_slot in blender_object.material_slots:
                    if (
                        mat_slot.material
                        and mat_slot.material.name in self.op.texture_groups
                    ):
                        has_textured_material = True
                        break

            # Handle material assignment (same logic as write_object_resource)
            if (
                self.op.use_orca_format == "BASEMATERIAL"
                and self.op.vertex_colors
                and self.op.mmu_slicer_format == "ORCA"
            ):
                color_counts = {}
                for triangle in mesh.loop_triangles:
                    triangle_color = get_triangle_color(mesh, triangle, blender_object)
                    if triangle_color and triangle_color in self.op.vertex_colors:
                        color_counts[triangle_color] = (
                            color_counts.get(triangle_color, 0) + 1
                        )

                if color_counts:
                    most_common_color = max(color_counts, key=color_counts.get)
                    colorgroup_id = self.op.vertex_colors[most_common_color]
                    object_element.attrib[self.attr("pid")] = str(colorgroup_id)
                    object_element.attrib[self.attr("pindex")] = "0"
                    most_common_material_list_index = colorgroup_id
            elif not has_textured_material and self.op.material_name_to_index:
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
                            self.op.material_name_to_index[most_common_material.name]
                        )
                        object_element.attrib[self.attr("pid")] = str(
                            self.op.material_resource_id
                        )
                        object_element.attrib[self.attr("pindex")] = str(
                            most_common_material_list_index
                        )

            # Write vertices
            write_vertices(
                mesh_element,
                mesh.vertices,
                self.op.use_orca_format,
                self.op.coordinate_precision,
            )

            # Write triangles
            write_triangles(
                mesh_element,
                mesh.loop_triangles,
                most_common_material_list_index,
                blender_object.material_slots,
                self.op.material_name_to_index,
                self.op.use_orca_format,
                self.op.mmu_slicer_format,
                self.op.vertex_colors,
                mesh,
                blender_object,
                getattr(self.op, "texture_groups", None),
                str(self.op.material_resource_id)
                if self.op.material_resource_id
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
        Write a component instance - an object that references a component definition.

        This creates a container object with a <component> child that references
        the shared mesh data. Only the transform is stored, not the mesh.

        :param resources_element: The <resources> element to write to
        :param blender_object: The Blender object instance
        :param component_id: The resource ID of the component definition to reference
        :return: The resource ID of this instance container
        """
        instance_id = self.op.next_resource_id
        self.op.next_resource_id += 1

        # Create container object (type="model" by default)
        object_element = xml.etree.ElementTree.SubElement(
            resources_element, f"{{{MODEL_NAMESPACE}}}object"
        )
        object_element.attrib[self.attr("id")] = str(instance_id)
        object_name = str(blender_object.name)
        object_element.attrib[self.attr("name")] = object_name

        # Add component reference
        components_element = xml.etree.ElementTree.SubElement(
            object_element, f"{{{MODEL_NAMESPACE}}}components"
        )
        component_element = xml.etree.ElementTree.SubElement(
            components_element, f"{{{MODEL_NAMESPACE}}}component"
        )
        component_element.attrib[self.attr("objectid")] = str(component_id)

        # Components don't need transforms here - the instance transform is applied
        # at the build item level

        return instance_id


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
        # Activate Production Extension for Orca compatibility
        self.op.extension_manager.activate(PRODUCTION_EXTENSION.namespace)
        self.op.extension_manager.activate(ORCA_EXTENSION.namespace)
        debug("Activated Orca Slicer extensions: Production + BambuStudio")

        # Register namespaces
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)
        xml.etree.ElementTree.register_namespace("p", PRODUCTION_NAMESPACE)
        xml.etree.ElementTree.register_namespace("BambuStudio", BAMBU_NAMESPACE)

        # Collect face colors for Orca export
        self.op.safe_report({"INFO"}, "Collecting face colors for Orca export...")

        # For PAINT mode, collect colors from paint texture metadata instead of face materials
        paint_colors_collected = False
        if self.op.use_orca_format == "PAINT":
            for blender_object in blender_objects:
                original_object = blender_object
                if hasattr(blender_object, "original"):
                    original_object = blender_object.original

                original_mesh_data = original_object.data
                if (
                    "3mf_is_paint_texture" in original_mesh_data
                    and original_mesh_data["3mf_is_paint_texture"]
                ):
                    if "3mf_paint_extruder_colors" in original_mesh_data:
                        import ast

                        try:
                            extruder_colors_hex = ast.literal_eval(
                                original_mesh_data["3mf_paint_extruder_colors"]
                            )
                            for idx, hex_color in extruder_colors_hex.items():
                                if hex_color not in self.op.vertex_colors:
                                    self.op.vertex_colors[hex_color] = idx
                            paint_colors_collected = True
                            debug(
                                f"Collected {len(extruder_colors_hex)} colors from paint texture metadata"
                            )
                        except Exception as e:
                            warn(f"Failed to parse extruder colors from metadata: {e}")

        # If no paint colors found, fall back to face material colors
        if not paint_colors_collected:
            self.op.vertex_colors = collect_face_colors(
                blender_objects, self.op.use_mesh_modifiers, self.op.safe_report
            )

        debug(f"Orca mode enabled with {len(self.op.vertex_colors)} color zones")

        if len(self.op.vertex_colors) == 0:
            warn("No face colors found! Assign materials to faces for color zones.")
            self.op.safe_report(
                {"WARNING"},
                "No face colors detected. Assign different materials to faces in Edit mode.",
            )
        else:
            self.op.safe_report(
                {"INFO"},
                f"Detected {len(self.op.vertex_colors)} color zones for Orca export",
            )

        # Generate build UUID
        build_uuid = str(uuid.uuid4())

        # Filter mesh objects and track their data
        mesh_objects = []
        for blender_object in blender_objects:
            if not self.op.export_hidden and not blender_object.visible_get():
                continue
            if blender_object.parent is not None:
                continue
            if blender_object.type != "MESH":
                continue
            mesh_objects.append(blender_object)

        if not mesh_objects:
            self.op.safe_report({"ERROR"}, "No mesh objects found to export!")
            archive.close()
            return {"CANCELLED"}

        # Write individual object model files
        object_data = []

        total_mesh_objects = len(mesh_objects)
        for idx, blender_object in enumerate(mesh_objects):
            # Don't update progress here in PAINT mode - let segmentation callback handle it
            if self.op.use_orca_format != "PAINT":
                progress = int(((idx + 1) / total_mesh_objects) * 95)
                self.op._progress_update(
                    progress,
                    f"Exporting {idx + 1}/{total_mesh_objects}: {blender_object.name}",
                )
            object_counter = idx + 1
            wrapper_id = object_counter * 2
            mesh_id = object_counter * 2 - 1

            # Generate UUIDs
            wrapper_uuid = f"0000000{object_counter}-61cb-4c03-9d28-80fed5dfa1dc"
            mesh_uuid = f"000{object_counter}0000-81cb-4c03-9d28-80fed5dfa1dc"
            component_uuid = f"000{object_counter}0000-b206-40ff-9872-83e8017abed1"

            # Create safe filename
            safe_name = re.sub(r"[^\w\-.]", "_", blender_object.name)
            object_path = f"/3D/Objects/{safe_name}_{object_counter}.model"

            # Get transformation
            transformation = blender_object.matrix_world.copy()
            transformation = mathutils.Matrix.Scale(global_scale, 4) @ transformation

            # Write the individual object model file
            self.write_object_model(
                archive, blender_object, object_path, mesh_id, mesh_uuid, idx, total_mesh_objects
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

            self.op.num_written += 1

        # Write main 3dmodel.model with wrapper objects and build items
        self.op._progress_update(90, "Writing main model...")
        self.write_main_model(archive, object_data, build_uuid)

        # Write 3D/_rels/3dmodel.model.rels
        self.op._progress_update(93, "Writing relationships...")
        self.write_model_relationships(archive, object_data)

        # Write Orca metadata files
        self.op._progress_update(96, "Writing configuration...")
        self.write_orca_metadata(archive, mesh_objects)

        # Write thumbnail if available from .blend file
        self.op._progress_update(99, "Writing thumbnail...")
        write_thumbnail(archive)

        self.op._progress_update(100, "Finalizing export...")
        return self.op._finalize_export(archive, "Orca-compatible ")

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
        if self.op.use_mesh_modifiers:
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
        for vertex in mesh.vertices:
            xml.etree.ElementTree.SubElement(
                vertices_elem,
                "vertex",
                attrib={
                    "x": str(vertex.co.x),
                    "y": str(vertex.co.y),
                    "z": str(vertex.co.z),
                },
            )

        # Generate segmentation strings from UV texture if in PAINT mode
        segmentation_strings = {}
        if self.op.use_orca_format == "PAINT" and mesh.uv_layers.active:
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
                    import ast

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
                    from .export_hash_segmentation import texture_to_segmentation

                    # Create progress callback for Orca segmentation
                    def orca_seg_progress(current, total_val, message):
                        if total_val > 0:
                            seg_pct = current / total_val
                            # Each object gets its share of the 15-90% range
                            obj_start = 15 + ((obj_index / total_objects) * 75)
                            obj_end = 15 + (((obj_index + 1) / total_objects) * 75)
                            overall = int(obj_start + (seg_pct * (obj_end - obj_start)))
                            self.op._progress_update(overall, f"{blender_object.name}: {message}")

                    try:
                        segmentation_strings = texture_to_segmentation(
                            blender_object,
                            paint_texture,
                            extruder_colors,
                            default_extruder,
                            progress_callback=orca_seg_progress,
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

        # Triangles with paint_color
        triangles_elem = xml.etree.ElementTree.SubElement(mesh_elem, "triangles")
        for tri_idx, triangle in enumerate(mesh.loop_triangles):
            tri_attribs = {
                "v1": str(triangle.vertices[0]),
                "v2": str(triangle.vertices[1]),
                "v3": str(triangle.vertices[2]),
            }

            # Check for segmentation string first (PAINT mode with UV texture)
            if segmentation_strings and triangle.polygon_index in segmentation_strings:
                seg_string = segmentation_strings[triangle.polygon_index]
                if seg_string:
                    tri_attribs["paint_color"] = seg_string
                    xml.etree.ElementTree.SubElement(
                        triangles_elem, "triangle", attrib=tri_attribs
                    )
                    continue

            # Fall back to simple paint_color from face material colors
            triangle_color = get_triangle_color(mesh, triangle, blender_object)
            if triangle_color and triangle_color in self.op.vertex_colors:
                filament_index = self.op.vertex_colors[triangle_color]
                if filament_index < len(ORCA_FILAMENT_CODES):
                    paint_code = ORCA_FILAMENT_CODES[filament_index]
                    if paint_code:
                        tri_attribs["paint_color"] = paint_code

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
        self, archive: zipfile.ZipFile, object_data: List[dict], build_uuid: str
    ) -> None:
        """Write the main 3dmodel.model file with wrapper objects."""
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
        meta_app = xml.etree.ElementTree.SubElement(
            root, "metadata", attrib={"name": "Application"}
        )
        meta_app.text = "Blender-3MF-OrcaExport"

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
        self, archive: zipfile.ZipFile, blender_objects: List[bpy.types.Object]
    ) -> None:
        """Write Orca Slicer compatible metadata files to the archive."""
        debug("Writing Orca metadata files...")

        try:
            # Write project_settings.config from template with updated colors
            project_settings = self.generate_project_settings()
            with archive.open("Metadata/project_settings.config", "w") as f:
                f.write(json.dumps(project_settings, indent=4).encode("utf-8"))
            debug("Wrote project_settings.config")

            # Write model_settings.config with object metadata
            model_settings_xml = self.generate_model_settings(blender_objects)
            with archive.open("Metadata/model_settings.config", "w") as f:
                f.write(model_settings_xml.encode("utf-8"))
            debug("Wrote model_settings.config")

            debug(f"Wrote Orca metadata with {len(self.op.vertex_colors)} color zones")
        except Exception as e:
            error(f"Failed to write Orca metadata: {e}")
            self.op.safe_report({"ERROR"}, f"Failed to write Orca metadata: {e}")
            raise

    def generate_project_settings(self) -> dict:
        """Generate project_settings.config by loading template and updating filament colors."""
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        template_path = os.path.join(addon_dir, "orca_project_template.json")

        with open(template_path, "r", encoding="utf-8") as f:
            settings = json.load(f)

        sorted_colors = sorted(self.op.vertex_colors.items(), key=lambda x: x[1])
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

    def generate_model_settings(self, blender_objects: List[bpy.types.Object]) -> str:
        """Generate the model_settings.config XML for Orca Slicer."""
        root = xml.etree.ElementTree.Element("config")

        object_id = 2  # Start from 2

        for blender_object in blender_objects:
            if blender_object.type != "MESH":
                continue

            object_elem = xml.etree.ElementTree.SubElement(
                root, "object", id=str(object_id)
            )
            xml.etree.ElementTree.SubElement(
                object_elem, "metadata", key="name", value=str(blender_object.name)
            )
            xml.etree.ElementTree.SubElement(
                object_elem, "metadata", key="extruder", value="1"
            )

            part_elem = xml.etree.ElementTree.SubElement(
                object_elem, "part", id="1", subtype="normal_part"
            )
            xml.etree.ElementTree.SubElement(
                part_elem, "metadata", key="name", value=str(blender_object.name)
            )
            matrix_value = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
            xml.etree.ElementTree.SubElement(
                part_elem, "metadata", key="matrix", value=matrix_value
            )

            object_id += 1

        # Add plate metadata
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

        # Add assemble section
        assemble_elem = xml.etree.ElementTree.SubElement(root, "assemble")
        xml.etree.ElementTree.SubElement(
            assemble_elem,
            "assemble_item",
            object_id="2",
            instance_id="0",
            transform="1 0 0 0 1 0 0 0 1 0 0 0",
            offset="0 0 0",
        )

        tree = xml.etree.ElementTree.ElementTree(root)

        output = io.BytesIO()
        tree.write(output, encoding="utf-8", xml_declaration=True)
        return output.getvalue().decode("utf-8")


class PrusaExporter(BaseExporter):
    """Exports PrusaSlicer compatible 3MF files with mmu_segmentation."""

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
        # Register namespaces
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)
        xml.etree.ElementTree.register_namespace(
            "slic3rpe", "http://schemas.slic3r.org/3mf/2017/06"
        )

        # Collect face colors
        self.op.safe_report(
            {"INFO"}, "Collecting face colors for PrusaSlicer export..."
        )

        # For PAINT mode, collect colors from paint texture metadata instead of face materials
        paint_colors_collected = False
        for blender_object in blender_objects:
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
                    import ast

                    try:
                        extruder_colors_hex = ast.literal_eval(
                            original_mesh_data["3mf_paint_extruder_colors"]
                        )
                        # Add all colors from this paint texture to vertex_colors
                        for idx, hex_color in extruder_colors_hex.items():
                            if hex_color not in self.op.vertex_colors:
                                self.op.vertex_colors[hex_color] = idx
                        paint_colors_collected = True
                        debug(
                            f"Collected {len(extruder_colors_hex)} colors from paint texture metadata"
                        )
                    except Exception as e:
                        warn(f"Failed to parse extruder colors from metadata: {e}")

        # If no paint colors found, fall back to face material colors
        if not paint_colors_collected:
            self.op.vertex_colors = collect_face_colors(
                blender_objects, self.op.use_mesh_modifiers, self.op.safe_report
            )

        debug(f"PrusaSlicer mode enabled with {len(self.op.vertex_colors)} color zones")

        if len(self.op.vertex_colors) == 0:
            warn("No face colors found! Assign materials to faces for color zones.")
            self.op.safe_report(
                {"WARNING"},
                "No face colors detected. Assign different materials to faces in Edit mode.",
            )
        else:
            self.op.safe_report(
                {"INFO"},
                f"Detected {len(self.op.vertex_colors)} color zones for PrusaSlicer export",
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

        write_metadata(root, scene_metadata, self.op.use_orca_format)

        resources_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}resources"
        )

        # PrusaSlicer MMU painting doesn't use basematerials
        self.op.material_name_to_index = {}

        # Use StandardExporter's write_objects (reuse the logic)
        std_exporter = StandardExporter(self.op)
        std_exporter.write_objects(
            root, resources_element, blender_objects, global_scale
        )

        # Write filament colors to metadata for round-trip import
        write_prusa_filament_colors(archive, self.op.vertex_colors)

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
        write_thumbnail(archive)

        self.op._progress_update(100, "Finalizing export...")
        return self.op._finalize_export(archive, "PrusaSlicer-compatible ")
