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
Public API for programmatic 3MF import and export.

These entry points let other Blender addons, CLI scripts, and headless
automation workflows import or export 3MF files *without* going through
Blender operator invocation (``bpy.ops``).  They build the appropriate
context objects, run the same pipeline code as the operators, and return
lightweight result dataclasses.

Quick start::

    from io_mesh_3mf.api import import_3mf, export_3mf

    # Import
    result = import_3mf("/path/to/model.3mf", import_materials="PAINT")
    print(result.status, result.num_loaded, result.objects)

    # Export
    result = export_3mf(
        "/path/to/output.3mf",
        use_orca_format="STANDARD",
        use_selection=True,
    )
    print(result.status, result.num_written)

Inspect without importing::

    from io_mesh_3mf.api import inspect_3mf

    info = inspect_3mf("/path/to/model.3mf")
    print(info.unit, info.num_objects, info.num_triangles_total)
    for obj in info.objects:
        print(obj["name"], obj["num_vertices"], obj["num_triangles"])

Batch operations::

    from io_mesh_3mf.api import batch_import

    results = batch_import(["/a.3mf", "/b.3mf"], import_materials="PAINT")
    for r in results:
        print(r.status, r.num_loaded)

Building blocks for custom workflows::

    from io_mesh_3mf.api import colors, types, segmentation, units
"""

from __future__ import annotations

import os
import xml.etree.ElementTree
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import bpy

from .common.constants import (
    RELS_MIMETYPE,
    MODEL_MIMETYPE,
    MODEL_NAMESPACES,
    SUPPORTED_EXTENSIONS,
    MATERIAL_NAMESPACE,
)
from .common.extensions import ExtensionManager
from .common.logging import debug, warn, error
from .common.metadata import Metadata, MetadataEntry
from .common.annotations import Annotations
from .common.units import (
    blender_to_metre,
    threemf_to_metre,
    export_unit_scale,
)

__all__ = [
    # --- Core functions ---
    "import_3mf",
    "export_3mf",
    "inspect_3mf",
    "batch_import",
    "batch_export",
    # --- Result types ---
    "ImportResult",
    "ExportResult",
    "InspectResult",
    # --- Building-block sub-namespaces ---
    "colors",
    "types",
    "segmentation",
    "units",
    "extensions",
    "xml_tools",
    "metadata",
    "components",
]


# ═══════════════════════════════════════════════════════════════════════════
# Result dataclasses
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ImportResult:
    """Return value from :func:`import_3mf`.

    Attributes:
        status: ``"FINISHED"`` on success, ``"CANCELLED"`` on failure.
        num_loaded: Number of objects successfully imported.
        objects: List of ``bpy.types.Object`` instances created during import.
        warnings: Accumulated warning messages (if any).
    """

    status: str = "FINISHED"
    num_loaded: int = 0
    objects: List = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class ExportResult:
    """Return value from :func:`export_3mf`.

    Attributes:
        status: ``"FINISHED"`` on success, ``"CANCELLED"`` on failure.
        num_written: Number of objects written to the archive.
        filepath: Absolute path of the written ``.3mf`` file.
        warnings: Accumulated warning messages (if any).
    """

    status: str = "FINISHED"
    num_written: int = 0
    filepath: str = ""
    warnings: List[str] = field(default_factory=list)


@dataclass
class InspectResult:
    """Return value from :func:`inspect_3mf`.

    A lightweight summary of a 3MF archive's contents, extracted *without*
    creating any Blender objects or materials.

    Attributes:
        status: ``"OK"`` on success, ``"ERROR"`` on failure.
        error_message: Human-readable error string when ``status == "ERROR"``.
        unit: The unit declared in the model file (``"millimeter"`` etc.).
        metadata: Top-level ``<metadata>`` key/value pairs from the model.
        objects: Per-object summary dicts with keys:

            - ``"id"`` — resource ID string
            - ``"name"`` — object name (or ``""`` if unnamed)
            - ``"type"`` — object type attribute (``"model"`` / ``"solidsupport"`` / …)
            - ``"num_vertices"`` — vertex count
            - ``"num_triangles"`` — triangle count
            - ``"num_components"`` — number of component references
            - ``"has_materials"`` — whether face materials are present
            - ``"has_segmentation"`` — whether MMU paint segmentation is present

        materials: Per-material-group summary dicts with keys:

            - ``"id"`` — resource ID string
            - ``"type"`` — ``"basematerials"`` | ``"colorgroup"`` | ``"texture2dgroup"``
            - ``"count"`` — number of entries in the group

        textures: Per-texture summary dicts with keys:

            - ``"id"`` — resource ID string
            - ``"path"`` — internal archive path
            - ``"contenttype"`` — MIME type string

        extensions_used: Set of namespace URIs for extensions referenced
            in the model's ``requiredextensions`` / ``recommendedextensions``.
        vendor_format: Detected slicer vendor format (``"orca"`` / ``None``).
        archive_files: List of all file paths inside the ZIP archive.
        num_objects: Total number of ``<object>`` resources.
        num_triangles_total: Sum of all triangle counts across objects.
        num_vertices_total: Sum of all vertex counts across objects.
        warnings: Accumulated warnings during inspection.
    """

    status: str = "OK"
    error_message: str = ""
    unit: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)
    objects: List[Dict] = field(default_factory=list)
    materials: List[Dict] = field(default_factory=list)
    textures: List[Dict] = field(default_factory=list)
    extensions_used: Set[str] = field(default_factory=set)
    vendor_format: Optional[str] = None
    archive_files: List[str] = field(default_factory=list)
    num_objects: int = 0
    num_triangles_total: int = 0
    num_vertices_total: int = 0
    warnings: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# Callback type aliases (for documentation clarity)
# ═══════════════════════════════════════════════════════════════════════════

# Called with (percentage: int 0-100, message: str)
ProgressCallback = Callable[[int, str], None]
# Called with (warning_message: str)
WarningCallback = Callable[[str], None]
# Called with (blender_object, resource_id: str) after each object is built
ObjectCreatedCallback = Callable[..., None]


# ═══════════════════════════════════════════════════════════════════════════
# inspect_3mf  — read-only archive inspection (no Blender objects created)
# ═══════════════════════════════════════════════════════════════════════════

def inspect_3mf(filepath: str) -> InspectResult:
    """Inspect a 3MF file without importing anything into Blender.

    Opens the archive, parses the XML model file(s), and returns a
    summary of objects, materials, textures, metadata, and extensions.
    No Blender objects, meshes, or materials are created.

    :param filepath: Path to the ``.3mf`` file.
    :return: :class:`InspectResult` with archive metadata and statistics.

    Example::

        info = inspect_3mf("model.3mf")
        if info.status == "OK":
            for obj in info.objects:
                print(f"{obj['name']}: {obj['num_triangles']} tris")
    """
    from .common.constants import MODEL_DEFAULT_UNIT

    filepath = os.path.abspath(filepath)
    result = InspectResult()

    # --- Open archive -------------------------------------------------------
    try:
        archive = zipfile.ZipFile(filepath, "r")
    except (zipfile.BadZipFile, EnvironmentError) as e:
        result.status = "ERROR"
        result.error_message = f"Unable to read archive: {e}"
        return result

    result.archive_files = archive.namelist()

    # --- Find model files ---------------------------------------------------
    # Look for [Content_Types].xml to resolve MIME types, but fall back to
    # scanning for *.model files if the content-types file is missing.
    model_paths: List[str] = []
    for name in result.archive_files:
        lower = name.lower()
        if lower.endswith(".model"):
            model_paths.append(name)

    if not model_paths:
        result.status = "ERROR"
        result.error_message = "No .model files found in archive"
        archive.close()
        return result

    # --- Parse each model file ----------------------------------------------
    for model_path in model_paths:
        try:
            with archive.open(model_path) as f:
                tree = xml.etree.ElementTree.ElementTree(file=f)
        except xml.etree.ElementTree.ParseError as e:
            result.warnings.append(f"Malformed XML in {model_path}: {e}")
            continue

        root = tree.getroot()

        # Unit.
        if not result.unit:
            result.unit = root.attrib.get("unit", MODEL_DEFAULT_UNIT)

        # Top-level metadata.
        for meta_node in root.iterfind("./3mf:metadata", MODEL_NAMESPACES):
            name = meta_node.attrib.get("name", "")
            if name:
                result.metadata[name] = meta_node.text or ""

        # Extensions referenced.
        for attr_key in ("requiredextensions", "recommendedextensions"):
            ext_str = root.attrib.get(attr_key, "")
            if ext_str:
                resolved = _resolve_prefixes(root, ext_str)
                result.extensions_used.update(resolved)

        # Vendor detection (lightweight — check namespace presence).
        from .import_3mf.slicer import detect_vendor
        detected = detect_vendor(root)
        if detected and result.vendor_format is None:
            result.vendor_format = detected

        # ---- Materials / textures ------------------------------------------
        _inspect_materials(root, result)
        _inspect_textures(root, result)

        # ---- Objects -------------------------------------------------------
        for obj_node in root.iterfind(
            "./3mf:resources/3mf:object", MODEL_NAMESPACES
        ):
            obj_id = obj_node.attrib.get("id", "")
            obj_name = obj_node.attrib.get("name", "")
            obj_type = obj_node.attrib.get("type", "model")

            # Count vertices.
            vert_nodes = obj_node.findall(
                "./3mf:mesh/3mf:vertices/3mf:vertex", MODEL_NAMESPACES
            )
            num_verts = len(vert_nodes)

            # Count triangles.
            tri_nodes = obj_node.findall(
                "./3mf:mesh/3mf:triangles/3mf:triangle", MODEL_NAMESPACES
            )
            num_tris = len(tri_nodes)

            # Count components.
            comp_nodes = obj_node.findall(
                "./3mf:components/3mf:component", MODEL_NAMESPACES
            )
            num_components = len(comp_nodes)

            # Check for materials (pid/pindex on object or any triangle).
            has_materials = "pid" in obj_node.attrib
            if not has_materials:
                for tri in tri_nodes[:1]:
                    if "pid" in tri.attrib:
                        has_materials = True
                        break

            # Check for segmentation (slic3rpe or Orca paint_color).
            has_seg = False
            for tri in tri_nodes[:1]:
                if tri.attrib.get(
                    "{http://schemas.slic3r.org/3mf/2017/06}mmu_segmentation"
                ):
                    has_seg = True
                    break
                if tri.attrib.get("paint_color"):
                    has_seg = True
                    break

            obj_summary = {
                "id": obj_id,
                "name": obj_name,
                "type": obj_type,
                "num_vertices": num_verts,
                "num_triangles": num_tris,
                "num_components": num_components,
                "has_materials": has_materials,
                "has_segmentation": has_seg,
            }
            result.objects.append(obj_summary)
            result.num_triangles_total += num_tris
            result.num_vertices_total += num_verts

    result.num_objects = len(result.objects)
    archive.close()
    return result


# ═══════════════════════════════════════════════════════════════════════════
# import_3mf
# ═══════════════════════════════════════════════════════════════════════════

def import_3mf(
    filepath: str,
    *,
    global_scale: float = 1.0,
    import_materials: str = "MATERIALS",
    reuse_materials: bool = True,
    import_location: str = "KEEP",
    origin_to_geometry: str = "KEEP",
    grid_spacing: float = 0.1,
    paint_uv_method: str = "SMART",
    paint_texture_size: int = 0,
    target_collection: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_warning: Optional[WarningCallback] = None,
    on_object_created: Optional[ObjectCreatedCallback] = None,
) -> ImportResult:
    """Import a 3MF file into the current Blender scene.

    This is the headless/programmatic counterpart to the ``Import3MF``
    operator.  It skips UI-specific behaviour (progress bars, camera zoom,
    paint popups) but runs the exact same import pipeline.

    :param filepath: Path to the ``.3mf`` file to import.
    :param global_scale: Scale multiplier (default 1.0).
    :param import_materials: ``"MATERIALS"`` | ``"PAINT"`` | ``"NONE"``.
    :param reuse_materials: Reuse existing Blender materials by name/color.
    :param import_location: ``"ORIGIN"`` | ``"CURSOR"`` | ``"KEEP"`` | ``"GRID"``.
    :param origin_to_geometry: ``"KEEP"`` | ``"CENTER"`` | ``"BOTTOM"``.
    :param grid_spacing: Spacing between objects in grid layout mode.
    :param paint_uv_method: ``"SMART"`` (default) or ``"LIGHTMAP"``.
        Smart UV groups adjacent faces; Lightmap gives each face unique space.
    :param paint_texture_size: Override texture resolution (0 = auto).
    :param target_collection: Name of an existing Blender collection to place
        imported objects into.  If *None*, objects are added to the active
        collection.  If the named collection does not exist it will be created
        and linked to the scene.
    :param on_progress: Optional ``(percentage: int, message: str)`` callback.
    :param on_warning: Optional ``(message: str)`` callback fired for each warning.
    :param on_object_created: Optional callback fired after each Blender
        object is built.  Receives ``(blender_object, resource_id)`` arguments.
    :return: :class:`ImportResult` with status, loaded count, and object list.
    """
    from .import_3mf.context import ImportContext, ImportOptions
    from .import_3mf import archive as archive_mod
    from .import_3mf import geometry as geometry_mod
    from .import_3mf import builder as builder_mod
    from .import_3mf.scene import apply_grid_layout
    from .import_3mf.slicer import (
        detect_vendor,
        read_orca_filament_colors,
        read_prusa_slic3r_colors,
        read_blender_addon_colors,
        read_prusa_object_extruders,
    )
    from .import_3mf.materials import (
        read_materials as _read_materials_impl,
        read_textures as _read_textures_impl,
        read_texture_groups as _read_texture_groups_impl,
        extract_textures_from_archive as _extract_textures_impl,
        read_pbr_metallic_properties as _read_pbr_metallic_impl,
        read_pbr_specular_properties as _read_pbr_specular_impl,
        read_pbr_translucent_properties as _read_pbr_translucent_impl,
        read_pbr_texture_display_properties as _read_pbr_texture_display_impl,
        read_composite_materials as _read_composite_impl,
        read_multiproperties as _read_multiproperties_impl,
        store_passthrough_materials as _store_passthrough_impl,
    )

    filepath = os.path.abspath(filepath)
    result = ImportResult()
    warnings_list = result.warnings

    if on_progress:
        on_progress(0, "Starting import…")

    # Build context (no operator).
    options = ImportOptions(
        global_scale=global_scale,
        import_materials=import_materials,
        reuse_materials=reuse_materials,
        import_location=import_location,
        origin_to_geometry=origin_to_geometry,
        grid_spacing=grid_spacing,
        paint_uv_method=paint_uv_method,
        paint_texture_size=paint_texture_size,
    )
    ctx = ImportContext(options=options, operator=None)

    # Wire up warning callback (intercept ctx.safe_report for WARNING level).
    if on_warning is not None:
        _original_safe_report = ctx.safe_report

        def _intercepted_safe_report(level, message):
            _original_safe_report(level, message)
            if "WARNING" in level:
                on_warning(message)
                warnings_list.append(message)

        ctx.safe_report = _intercepted_safe_report  # type: ignore[assignment]

    scene_metadata = Metadata()
    scene_metadata.retrieve(bpy.context.scene)
    del scene_metadata["Title"]
    annotations_obj = Annotations()
    annotations_obj.retrieve()

    # Switch to object mode, deselect everything.
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")
    if bpy.ops.object.select_all.poll():
        bpy.ops.object.select_all(action="DESELECT")

    # --- Collection targeting -----------------------------------------------
    original_collection = bpy.context.view_layer.active_layer_collection
    if target_collection is not None:
        col = bpy.data.collections.get(target_collection)
        if col is None:
            col = bpy.data.collections.new(target_collection)
            bpy.context.scene.collection.children.link(col)
        # Find the layer collection wrapper for this collection.
        layer_col = _find_layer_collection(
            bpy.context.view_layer.layer_collection, col,
        )
        if layer_col is not None:
            bpy.context.view_layer.active_layer_collection = layer_col

    ctx.current_archive_path = filepath

    if on_progress:
        on_progress(5, "Reading archive…")

    # --- Read archive -------------------------------------------------------
    try:
        files_by_content_type = archive_mod.read_archive(ctx, filepath)
    except Exception as e:
        error(f"Failed to read archive {filepath}: {e}")
        result.status = "CANCELLED"
        # Restore collection.
        bpy.context.view_layer.active_layer_collection = original_collection
        return result

    # If no model files were found, the archive is unreadable or invalid.
    if not files_by_content_type.get(MODEL_MIMETYPE):
        error(f"No model files found in archive: {filepath}")
        result.status = "CANCELLED"
        bpy.context.view_layer.active_layer_collection = original_collection
        return result

    # Relationships & content types.
    for rels_file in files_by_content_type.get(RELS_MIMETYPE, []):
        annotations_obj.add_rels(rels_file)
    annotations_obj.add_content_types(files_by_content_type)
    archive_mod.must_preserve(ctx, files_by_content_type, annotations_obj)

    if on_progress:
        on_progress(15, "Parsing model files…")

    # --- Parse model files --------------------------------------------------
    for model_file in files_by_content_type.get(MODEL_MIMETYPE, []):
        try:
            document = xml.etree.ElementTree.ElementTree(file=model_file)
        except xml.etree.ElementTree.ParseError as e:
            error(f"3MF document is malformed: {e}")
            warnings_list.append(f"Malformed XML: {e}")
            continue
        if document is None:
            continue
        root = document.getroot()

        # Vendor detection.
        if ctx.options.import_materials != "NONE":
            ctx.vendor_format = detect_vendor(root)
        else:
            ctx.vendor_format = None

        # Extension activation.
        _activate_extensions_api(ctx, root)

        # Unit scale.
        context = bpy.context
        scale_unit = _import_unit_scale(context, root, global_scale)

        # Reset per-model resource dictionaries.
        ctx.resource_objects = {}
        ctx.resource_materials = {}
        ctx.resource_textures = {}
        ctx.resource_texture_groups = {}
        ctx.orca_filament_colors = {}
        ctx.object_default_extruders = {}

        if on_progress:
            on_progress(20, "Reading filament colours…")

        # Read filament colours.
        read_orca_filament_colors(ctx, filepath)
        read_prusa_slic3r_colors(ctx, filepath)
        read_blender_addon_colors(ctx, filepath)
        read_prusa_object_extruders(ctx, filepath)

        # Metadata.
        for metadata_node in root.iterfind("./3mf:metadata", MODEL_NAMESPACES):
            if "name" not in metadata_node.attrib:
                continue
            name = metadata_node.attrib["name"]
            preserve_str = metadata_node.attrib.get("preserve", "0")
            preserve = preserve_str != "0" and preserve_str.lower() != "false"
            datatype = metadata_node.attrib.get("type", "")
            value = metadata_node.text
            scene_metadata[name] = MetadataEntry(
                name=name, preserve=preserve, datatype=datatype, value=value,
            )

        if on_progress:
            on_progress(30, "Reading materials…")

        # Materials.
        if ctx.options.import_materials != "NONE":
            material_ns = {"m": MATERIAL_NAMESPACE}
            pbr_metallic = _read_pbr_metallic_impl(ctx, root, material_ns)
            pbr_specular = _read_pbr_specular_impl(ctx, root, material_ns)
            pbr_translucent = _read_pbr_translucent_impl(ctx, root, material_ns)
            _read_pbr_texture_display_impl(ctx, root, material_ns)

            display_properties = {}
            display_properties.update(pbr_metallic)
            display_properties.update(pbr_specular)
            display_properties.update(pbr_translucent)

            _read_materials_impl(ctx, root, material_ns, display_properties)
            _read_textures_impl(ctx, root, material_ns)
            _read_texture_groups_impl(ctx, root, material_ns, display_properties)
            _read_composite_impl(ctx, root, material_ns)
            _read_multiproperties_impl(ctx, root, material_ns)

        # Extract textures.
        _extract_textures_impl(ctx, filepath)

        if on_progress:
            on_progress(45, "Reading geometry…")

        # Objects.
        geometry_mod.read_objects(ctx, root)

        if on_progress:
            on_progress(60, "Building Blender objects…")

        # Build items (pass progress_callback through if available).
        builder_mod.build_items(ctx, root, scale_unit, progress_callback=on_progress)

    # Fire on_object_created for each built object.
    if on_object_created is not None:
        for obj in ctx.imported_objects:
            on_object_created(obj, str(getattr(obj, "name", "")))

    # Store scene data.
    scene_metadata.store(bpy.context.scene)
    annotations_obj.store()
    _store_passthrough_impl(ctx)

    # Grid layout.
    if ctx.options.import_location == "GRID":
        apply_grid_layout(ctx.imported_objects, ctx.options.grid_spacing)

    # Restore original collection.
    bpy.context.view_layer.active_layer_collection = original_collection

    result.num_loaded = ctx.num_loaded
    result.objects = list(ctx.imported_objects)
    result.status = "FINISHED"

    if on_progress:
        on_progress(100, "Import complete")

    debug(f"API: Imported {ctx.num_loaded} objects from {filepath}")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# export_3mf
# ═══════════════════════════════════════════════════════════════════════════

def export_3mf(
    filepath: str,
    *,
    objects=None,
    use_selection: bool = False,
    export_hidden: bool = False,
    global_scale: float = 1.0,
    use_mesh_modifiers: bool = True,
    coordinate_precision: int = 9,
    use_orca_format: str = "STANDARD",
    use_components: bool = True,
    mmu_slicer_format: str = "ORCA",
    subdivision_depth: int = 7,
    on_progress: Optional[ProgressCallback] = None,
    on_warning: Optional[WarningCallback] = None,
) -> ExportResult:
    """Export Blender objects to a 3MF file.

    This is the headless/programmatic counterpart to the ``Export3MF``
    operator.  It skips UI-specific behaviour (progress bars, status text)
    but runs the exact same export pipeline.

    :param filepath: Destination path for the ``.3mf`` file.
    :param objects: Explicit list of ``bpy.types.Object`` to export.
        If *None*, falls back to ``use_selection`` logic or all scene objects.
    :param use_selection: Export selected objects only (ignored when *objects* is given).
    :param export_hidden: Include hidden objects.
    :param global_scale: Scale multiplier (default 1.0).
    :param use_mesh_modifiers: Apply modifiers before exporting.
    :param coordinate_precision: Decimal precision for vertex coordinates.
    :param use_orca_format: ``"STANDARD"`` | ``"PAINT"``.
    :param use_components: Use component instances for linked duplicates.
    :param mmu_slicer_format: ``"ORCA"`` | ``"PRUSA"`` (only relevant when
        *use_orca_format* is ``"PAINT"``).
    :param subdivision_depth: Maximum recursive subdivision depth for paint
        segmentation (4–10, default 7). Higher = finer detail but slower.
    :param on_progress: Optional ``(percentage: int, message: str)`` callback.
    :param on_warning: Optional ``(message: str)`` callback for warnings.
    :return: :class:`ExportResult` with status, written count, and filepath.
    """
    from .export_3mf.context import ExportContext, ExportOptions
    from .export_3mf.archive import create_archive
    from .export_3mf.geometry import check_non_manifold_geometry
    from .export_3mf.standard import StandardExporter
    from .export_3mf.orca import OrcaExporter
    from .export_3mf.prusa import PrusaExporter

    filepath = os.path.abspath(filepath)
    result = ExportResult(filepath=filepath)

    if on_progress:
        on_progress(0, "Starting export…")

    options = ExportOptions(
        use_selection=use_selection,
        export_hidden=export_hidden,
        global_scale=global_scale,
        use_mesh_modifiers=use_mesh_modifiers,
        coordinate_precision=coordinate_precision,
        use_orca_format=use_orca_format,
        use_components=use_components,
        mmu_slicer_format=mmu_slicer_format,
        subdivision_depth=subdivision_depth,
    )
    ctx = ExportContext(
        options=options,
        operator=None,
        filepath=filepath,
        extension_manager=ExtensionManager(),
    )

    # Wire up warning callback.
    if on_warning is not None:
        _original_safe_report = ctx.safe_report

        def _intercepted_safe_report(level, message):
            _original_safe_report(level, message)
            if "WARNING" in level:
                on_warning(message)
                result.warnings.append(message)

        ctx.safe_report = _intercepted_safe_report  # type: ignore[assignment]

    if on_progress:
        on_progress(10, "Creating archive…")

    # Create archive.
    archive = create_archive(filepath, ctx.safe_report)
    if archive is None:
        result.status = "CANCELLED"
        return result

    # Determine objects to export.
    context = bpy.context
    if objects is not None:
        blender_objects = objects
    elif use_selection:
        blender_objects = context.selected_objects
        mesh_objects = [obj for obj in blender_objects if obj.type == "MESH"]
        if not mesh_objects:
            error("Export cancelled: No mesh objects in selection")
            result.status = "CANCELLED"
            return result
    else:
        blender_objects = context.scene.objects

    if on_progress:
        on_progress(20, "Checking geometry…")

    # Non-manifold check.
    mesh_objects = [obj for obj in blender_objects if obj.type == "MESH"]
    if mesh_objects:
        non_manifold = check_non_manifold_geometry(mesh_objects, use_mesh_modifiers)
        if non_manifold:
            msg = f"Non-manifold geometry detected in: {non_manifold[0]}"
            warn(msg)
            result.warnings.append(
                "Exported geometry contains non-manifold issues."
            )
            if on_warning:
                on_warning(msg)

    if on_progress:
        on_progress(30, "Writing 3MF data…")

    scale = export_unit_scale(context, global_scale)

    # Dispatch to exporter.
    try:
        if use_orca_format == "PAINT":
            if mmu_slicer_format == "ORCA":
                exporter = OrcaExporter(ctx)
            else:
                exporter = PrusaExporter(ctx)
        else:
            exporter = StandardExporter(ctx)

        status_set = exporter.execute(context, archive, blender_objects, scale)
        result.status = next(iter(status_set)) if status_set else "FINISHED"
    except Exception as e:
        error(f"Export failed: {e}")
        result.status = "CANCELLED"
        result.warnings.append(str(e))
        return result

    result.num_written = ctx.num_written

    if on_progress:
        on_progress(100, "Export complete")

    debug(f"API: Exported {ctx.num_written} objects to {filepath}")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# batch_import / batch_export
# ═══════════════════════════════════════════════════════════════════════════

def batch_import(
    filepaths: Sequence[str],
    *,
    on_progress: Optional[ProgressCallback] = None,
    on_warning: Optional[WarningCallback] = None,
    on_object_created: Optional[ObjectCreatedCallback] = None,
    **import_kwargs,
) -> List[ImportResult]:
    """Import multiple 3MF files in sequence with per-file error isolation.

    Each file is imported independently — a failure in one file does not
    prevent the others from being processed.  All keyword arguments
    supported by :func:`import_3mf` can be passed via ``**import_kwargs``
    and will be applied to every file.

    :param filepaths: Sequence of ``.3mf`` file paths to import.
    :param on_progress: Optional global progress callback.  Receives
        ``(percentage, message)`` where percentage spans 0-100 across
        *all* files.
    :param on_warning: Warning callback forwarded to each :func:`import_3mf` call.
    :param on_object_created: Object-created callback forwarded to each call.
    :param import_kwargs: Keyword arguments forwarded to :func:`import_3mf`.
    :return: List of :class:`ImportResult`, one per input file (same order).

    Example::

        results = batch_import(
            ["a.3mf", "b.3mf", "c.3mf"],
            import_materials="PAINT",
            target_collection="Imports",
        )
        total = sum(r.num_loaded for r in results)
        print(f"Imported {total} objects total")
    """
    results: List[ImportResult] = []
    total = len(filepaths)

    for idx, fp in enumerate(filepaths):
        # Per-file progress wrapper.
        file_progress: Optional[ProgressCallback] = None
        if on_progress:
            base_pct = int((idx / total) * 100)
            span_pct = int(100 / total) if total else 100

            def _file_progress(pct: int, msg: str, _base=base_pct, _span=span_pct):
                overall = _base + int(pct * _span / 100)
                on_progress(min(overall, 100), f"[{idx + 1}/{total}] {msg}")

            file_progress = _file_progress

        try:
            r = import_3mf(
                fp,
                on_progress=file_progress,
                on_warning=on_warning,
                on_object_created=on_object_created,
                **import_kwargs,
            )
        except Exception as e:
            error(f"batch_import: Failed on {fp}: {e}")
            r = ImportResult(status="CANCELLED", warnings=[str(e)])
        results.append(r)

    if on_progress:
        on_progress(100, "Batch import complete")

    return results


def batch_export(
    items: Sequence[Tuple[str, Optional[List]]],
    *,
    on_progress: Optional[ProgressCallback] = None,
    on_warning: Optional[WarningCallback] = None,
    **export_kwargs,
) -> List[ExportResult]:
    """Export multiple 3MF files in sequence with per-file error isolation.

    Each item is a ``(filepath, objects)`` tuple.  If *objects* is ``None``,
    the export falls back to the ``use_selection`` / all-scene logic from
    :func:`export_3mf`.

    :param items: Sequence of ``(filepath, objects_or_None)`` tuples.
    :param on_progress: Optional global progress callback.
    :param on_warning: Warning callback forwarded to each :func:`export_3mf` call.
    :param export_kwargs: Keyword arguments forwarded to :func:`export_3mf`.
    :return: List of :class:`ExportResult`, one per item (same order).

    Example::

        cubes = [o for o in bpy.data.objects if "Cube" in o.name]
        spheres = [o for o in bpy.data.objects if "Sphere" in o.name]
        results = batch_export([
            ("cubes.3mf", cubes),
            ("spheres.3mf", spheres),
        ], use_orca_format="STANDARD")
    """
    results: List[ExportResult] = []
    total = len(items)

    for idx, (fp, objs) in enumerate(items):
        file_progress: Optional[ProgressCallback] = None
        if on_progress:
            base_pct = int((idx / total) * 100)
            span_pct = int(100 / total) if total else 100

            def _file_progress(pct: int, msg: str, _base=base_pct, _span=span_pct):
                overall = _base + int(pct * _span / 100)
                on_progress(min(overall, 100), f"[{idx + 1}/{total}] {msg}")

            file_progress = _file_progress

        try:
            r = export_3mf(
                fp,
                objects=objs,
                on_progress=file_progress,
                on_warning=on_warning,
                **export_kwargs,
            )
        except Exception as e:
            error(f"batch_export: Failed on {fp}: {e}")
            r = ExportResult(status="CANCELLED", filepath=fp, warnings=[str(e)])
        results.append(r)

    if on_progress:
        on_progress(100, "Batch export complete")

    return results


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

def _find_layer_collection(
    layer_collection,
    target_collection,
):
    """Recursively find the LayerCollection wrapping *target_collection*."""
    if layer_collection.collection == target_collection:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, target_collection)
        if found is not None:
            return found
    return None


def _resolve_prefixes(root, prefixes_str: str) -> Set[str]:
    """Resolve extension prefix strings to namespace URIs."""
    from .common.constants import PRODUCTION_NAMESPACE

    if not prefixes_str:
        return set()
    prefix_to_ns = {}
    for attr_name, attr_value in root.attrib.items():
        if attr_name.startswith("{"):
            continue
        if attr_name.startswith("xmlns:"):
            prefix_to_ns[attr_name[6:]] = attr_value
    known = {
        "p": PRODUCTION_NAMESPACE,
        "m": "http://schemas.microsoft.com/3dmanufacturing/material/2015/02",
        "slic3rpe": "http://schemas.slic3r.org/3mf/2017/06",
    }
    prefix_to_ns.update({k: v for k, v in known.items() if k not in prefix_to_ns})
    resolved: Set[str] = set()
    for prefix in prefixes_str.split():
        prefix = prefix.strip()
        if not prefix:
            continue
        if prefix in prefix_to_ns:
            resolved.add(prefix_to_ns[prefix])
        else:
            resolved.add(prefix)
    return resolved


def _inspect_materials(root, result: InspectResult) -> None:
    """Scan material resources for inspect_3mf without creating Blender data."""
    mat_ns = {"m": MATERIAL_NAMESPACE}

    # basematerials
    for bm_node in root.iterfind(
        "./3mf:resources/m:basematerials", {**MODEL_NAMESPACES, **mat_ns}
    ):
        mat_id = bm_node.attrib.get("id", "")
        bases = bm_node.findall("m:base", mat_ns)
        result.materials.append({
            "id": mat_id,
            "type": "basematerials",
            "count": len(bases),
        })

    # colorgroups
    for cg_node in root.iterfind(
        "./3mf:resources/m:colorgroup", {**MODEL_NAMESPACES, **mat_ns}
    ):
        cg_id = cg_node.attrib.get("id", "")
        colors = cg_node.findall("m:color", mat_ns)
        result.materials.append({
            "id": cg_id,
            "type": "colorgroup",
            "count": len(colors),
        })

    # texture2dgroups
    for tg_node in root.iterfind(
        "./3mf:resources/m:texture2dgroup", {**MODEL_NAMESPACES, **mat_ns}
    ):
        tg_id = tg_node.attrib.get("id", "")
        coords = tg_node.findall("m:tex2coord", mat_ns)
        result.materials.append({
            "id": tg_id,
            "type": "texture2dgroup",
            "count": len(coords),
        })


def _inspect_textures(root, result: InspectResult) -> None:
    """Scan texture resources for inspect_3mf."""
    mat_ns = {"m": MATERIAL_NAMESPACE}
    for tex_node in root.iterfind(
        "./3mf:resources/m:texture2d", {**MODEL_NAMESPACES, **mat_ns}
    ):
        tex_id = tex_node.attrib.get("id", "")
        result.textures.append({
            "id": tex_id,
            "path": tex_node.attrib.get("path", ""),
            "contenttype": tex_node.attrib.get("contenttype", ""),
        })


def _import_unit_scale(
    context: bpy.types.Context,
    root: xml.etree.ElementTree.Element,
    global_scale: float,
) -> float:
    """Calculate unit scale exactly like the Import3MF operator."""
    from .common.constants import MODEL_DEFAULT_UNIT

    scale = global_scale
    blender_unit_to_metre = context.scene.unit_settings.scale_length
    if blender_unit_to_metre == 0:
        blender_unit = context.scene.unit_settings.length_unit
        blender_unit_to_metre = blender_to_metre[blender_unit]

    threemf_unit = root.attrib.get("unit", MODEL_DEFAULT_UNIT)
    threemf_unit_to_metre = threemf_to_metre[threemf_unit]
    scale *= threemf_unit_to_metre / blender_unit_to_metre
    return scale


def _activate_extensions_api(
    ctx,
    root: xml.etree.ElementTree.Element,
) -> None:
    """Activate extensions on ctx.extension_manager (no operator needed)."""
    for attr_key in ("requiredextensions", "recommendedextensions"):
        ext_str = root.attrib.get(attr_key, "")
        if ext_str:
            resolved = _resolve_prefixes(root, ext_str)
            for ns in resolved:
                if ns in SUPPORTED_EXTENSIONS:
                    ctx.extension_manager.activate(ns)
                    debug(f"API: Activated extension: {ns}")


# ═══════════════════════════════════════════════════════════════════════════
# Building-block re-exports
# ═══════════════════════════════════════════════════════════════════════════
#
# These sub-namespace imports expose the common building blocks so addon
# developers and CLI scripts can access them through ``api.*`` without
# needing to know the internal package layout.
#
#   from io_mesh_3mf.api import colors
#   r, g, b = colors.hex_to_rgb("#CC3319")
#
#   from io_mesh_3mf.api import types
#   obj = types.ResourceObject(vertices=[], triangles=[], ...)
#
#   from io_mesh_3mf.api import segmentation
#   tree = segmentation.decode_segmentation_string("A3F0")

from .common import colors         # hex_to_rgb, rgb_to_hex, srgb_to_linear, ...  # noqa: E402
from .common import types          # ResourceObject, Component, ResourceMaterial, ... # noqa: E402
from .common import segmentation   # SegmentationDecoder, SegmentationEncoder, ... # noqa: E402
from .common import units          # blender_to_metre, threemf_to_metre, import_unit_scale, ... # noqa: E402
from .common import extensions     # ExtensionManager, Extension, MATERIALS_EXTENSION, ... # noqa: E402
from .common import xml as xml_tools  # parse_transformation, format_transformation, ... # noqa: E402
from .common import metadata       # Metadata, MetadataEntry # noqa: E402
from .export_3mf import components  # detect_linked_duplicates, ComponentGroup, ... # noqa: E402
