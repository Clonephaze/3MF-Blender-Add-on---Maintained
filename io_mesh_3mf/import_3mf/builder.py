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
Object builder — converts parsed 3MF resources into Blender objects.

:func:`build_items` iterates over ``<build>/<item>`` elements and calls
:func:`build_object` for each one.  ``build_object`` orchestrates the
scene-level helpers in :mod:`scene` for mesh creation, material assignment,
UV setup, origin placement, etc.
"""

from typing import List, Optional, TYPE_CHECKING

import bpy
import mathutils

from ..common import debug, warn, MODEL_NAMESPACES
from ..common.metadata import Metadata, MetadataEntry
from ..common.xml import parse_transformation, read_metadata as _read_metadata
from ..common.types import ResourceObject

from .scene import (
    create_mesh_from_data,
    render_paint_texture,
    assign_materials_to_mesh,
    apply_triangle_sets,
    apply_uv_coordinates,
    set_object_origin,
    apply_import_location,
)

if TYPE_CHECKING:
    from .context import ImportContext

__all__ = [
    "build_items",
    "build_object",
]


# ---------------------------------------------------------------------------
# build_items
# ---------------------------------------------------------------------------

def build_items(
    ctx: "ImportContext",
    root,
    scale_unit: float,
    progress_callback=None,
) -> None:
    """Build all ``<build>/<item>`` entries into the Blender scene.

    :param ctx: Import context.
    :param root: XML root of the model file.
    :param scale_unit: Scale factor converting 3MF units to Blender units.
    :param progress_callback: Optional ``(value, message)`` callable for progress.
    """
    build_items_list = list(root.iterfind("./3mf:build/3mf:item", MODEL_NAMESPACES))
    total_items = len(build_items_list)

    for idx, build_item in enumerate(build_items_list):
        if progress_callback and total_items > 0:
            progress = 60 + int(((idx + 1) / total_items) * 35)
            progress_callback(progress, f"Building {idx + 1}/{total_items} objects...")

        try:
            objectid = build_item.attrib["objectid"]
            resource_object = ctx.resource_objects[objectid]
        except KeyError:
            warn("Encountered build item without object ID.")
            continue

        metadata = Metadata()
        for metadata_node in build_item.iterfind("./3mf:metadatagroup", MODEL_NAMESPACES):
            metadata = _read_metadata(metadata_node, metadata, ctx.operator)
        if "partnumber" in build_item.attrib:
            metadata["3mf:partnumber"] = MetadataEntry(
                name="3mf:partnumber",
                preserve=True,
                datatype="xs:string",
                value=build_item.attrib["partnumber"],
            )

        transform = mathutils.Matrix.Scale(scale_unit, 4)
        transform @= parse_transformation(build_item.attrib.get("transform", ""))

        build_object(ctx, resource_object, transform, metadata, [objectid])


# ---------------------------------------------------------------------------
# build_object
# ---------------------------------------------------------------------------

def build_object(
    ctx: "ImportContext",
    resource_object: ResourceObject,
    transformation: mathutils.Matrix,
    metadata: Metadata,
    objectid_stack_trace: List[str],
    parent: Optional[bpy.types.Object] = None,
    is_temp_component_def: bool = False,
) -> Optional[bpy.types.Object]:
    """Convert a resource object into a Blender object (recursive for components).

    Component instances (objects with no mesh, only a single component reference)
    are created as linked duplicates sharing the same mesh data.

    :param ctx: Import context.
    :param resource_object: The resource object to convert.
    :param transformation: World-space transformation matrix.
    :param metadata: Metadata for this build item.
    :param objectid_stack_trace: Stack of object IDs (for cycle detection).
    :param parent: Parent Blender object (for component hierarchy).
    :param is_temp_component_def: If ``True``, this is a temporary object for
        caching component meshes — skip tracking in ``imported_objects``.
    :return: The created Blender Object, or ``None``.
    """
    # --- Component instance detection ---
    is_component_instance = (
        not resource_object.triangles
        and resource_object.components
        and len(resource_object.components) == 1
    )

    if is_component_instance:
        mesh = _build_component_instance(
            ctx, resource_object, transformation, metadata,
            objectid_stack_trace, parent,
        )
        if mesh is None:
            return None
    else:
        mesh = _build_mesh(ctx, resource_object, objectid_stack_trace)

    # --- Create Blender object ---
    if mesh is not None:
        blender_object = bpy.data.objects.new("3MF Object", mesh)
        ctx.num_loaded += 1
        if parent is not None:
            blender_object.parent = parent

        bpy.context.collection.objects.link(blender_object)
        bpy.context.view_layer.objects.active = blender_object
        blender_object.select_set(True)

        # Origin placement (before transformation)
        set_object_origin(blender_object, ctx.options.origin_to_geometry)

        # Adjust transformation for import_location
        transformation = apply_import_location(
            transformation, ctx.options.import_location
        )

        blender_object.matrix_world = transformation

        # Track for grid layout
        if parent is None and not is_temp_component_def:
            ctx.imported_objects.append(blender_object)

        metadata.store(blender_object)
        resource_object.metadata.store(blender_object)

        if (
            "3mf:object_type" in resource_object.metadata
            and resource_object.metadata["3mf:object_type"].value in {"solidsupport", "support"}
        ):
            blender_object.hide_render = True
    else:
        blender_object = parent

    # --- Recurse for components ---
    if not is_component_instance:
        for component in resource_object.components:
            if component.resource_object in objectid_stack_trace:
                warn(f"Recursive components in object ID: {component.resource_object}")
                continue
            try:
                child_object = ctx.resource_objects[component.resource_object]
            except KeyError:
                warn(f"Build item with unknown resource ID: {component.resource_object}")
                continue
            transform = transformation @ component.transformation
            objectid_stack_trace.append(component.resource_object)
            build_object(
                ctx, child_object, transform, metadata,
                objectid_stack_trace, parent=blender_object,
            )
            objectid_stack_trace.pop()

    return blender_object


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_component_instance(
    ctx: "ImportContext",
    resource_object: ResourceObject,
    transformation: mathutils.Matrix,
    metadata: Metadata,
    objectid_stack_trace: List[str],
    parent,
) -> Optional[bpy.types.Mesh]:
    """Handle component-instance pattern: reuse or build + cache mesh."""
    component = resource_object.components[0]
    component_id = component.resource_object

    if component_id in ctx.component_instance_cache:
        cached_mesh, instance_count = ctx.component_instance_cache[component_id]
        ctx.component_instance_cache[component_id] = (cached_mesh, instance_count + 1)
        debug(f"Creating linked duplicate {instance_count + 1} for component {component_id}")
        return cached_mesh

    # First instance — build the component and cache its mesh
    try:
        component_resource = ctx.resource_objects[component_id]
    except KeyError:
        warn(f"Component reference to unknown resource ID: {component_id}")
        return None

    temp_obj = build_object(
        ctx,
        component_resource,
        mathutils.Matrix.Identity(4),
        Metadata(),
        objectid_stack_trace + [component_id],
        parent=None,
        is_temp_component_def=True,
    )

    if temp_obj and temp_obj.data:
        mesh = temp_obj.data
        ctx.component_instance_cache[component_id] = (mesh, 1)
        debug(f"Cached component {component_id} mesh for linked duplicates")
        bpy.data.objects.remove(temp_obj, do_unlink=True)
        return mesh

    warn(f"Failed to build component {component_id}")
    return None


def _build_mesh(
    ctx: "ImportContext",
    resource_object: ResourceObject,
    objectid_stack_trace: List[str],
) -> Optional[bpy.types.Mesh]:
    """Create mesh with all attributes (materials, UVs, triangle sets, paint)."""
    mesh = create_mesh_from_data(resource_object)
    if mesh is None:
        return None

    # Store passthrough multiproperties pid
    current_objectid = objectid_stack_trace[0] if objectid_stack_trace else None
    if current_objectid and current_objectid in ctx.object_passthrough_pids:
        mesh["3mf_passthrough_pid"] = ctx.object_passthrough_pids[current_objectid]
        debug(f"Stored passthrough pid={ctx.object_passthrough_pids[current_objectid]} on mesh")

    # Paint texture (if PAINT mode) — skip standard material assignment if rendered
    paint_rendered = render_paint_texture(ctx, mesh, resource_object)

    if not paint_rendered:
        assign_materials_to_mesh(ctx, mesh, resource_object)

    apply_triangle_sets(mesh, resource_object)
    apply_uv_coordinates(mesh, resource_object)

    return mesh
