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
Slim Import3MF operator — Blender UI shell that delegates all work to submodules.

Responsibilities:
- Define Blender operator properties (UI panel)
- Create :class:`ImportContext` and populate from operator properties
- Orchestrate the import pipeline via module calls
- Progress reporting and camera zoom
"""

import os.path
import xml.etree.ElementTree
from typing import Optional, Set

import bpy
import bpy.ops
import bpy.props
import bpy.types
import bpy_extras.io_utils

from ..common import debug, warn, error
from ..common.constants import (
    RELS_MIMETYPE,
    MODEL_MIMETYPE,
    MODEL_NAMESPACES,
    SUPPORTED_EXTENSIONS,
    MATERIAL_NAMESPACE,
)
from ..common.extensions import get_extension_by_namespace
from ..common.metadata import Metadata, MetadataEntry
from ..common.annotations import Annotations
from ..common.units import blender_to_metre, threemf_to_metre
from .context import ImportContext, ImportOptions
from . import archive as archive_mod
from . import geometry as geometry_mod
from . import builder as builder_mod
from .scene import apply_grid_layout
from .slicer import (
    detect_vendor,
    read_orca_filament_colors,
    read_prusa_slic3r_colors,
    read_blender_addon_colors,
    read_prusa_object_extruders,
)
from .materials import (
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

__all__ = ["Import3MF"]


class Import3MF(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Operator that imports a 3MF file into Blender."""

    # Metadata.
    bl_idname = "import_mesh.threemf"
    bl_label = "Import 3MF"
    bl_description = "Load a 3MF scene"
    bl_options = {"UNDO"}
    filename_ext = ".3mf"

    # ----- Operator properties (user-facing) --------------------------------

    filter_glob: bpy.props.StringProperty(default="*.3mf", options={"HIDDEN"})
    files: bpy.props.CollectionProperty(name="File Path", type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    global_scale: bpy.props.FloatProperty(
        name="Scale", default=1.0, soft_min=0.001, soft_max=1000.0, min=1e-6, max=1e6,
    )
    import_materials: bpy.props.EnumProperty(
        name="Material Mode",
        description="How to import material and color data",
        items=[
            ("MATERIALS", "Import Materials",
             "Import material colors and properties (standard 3MF)"),
            ("PAINT", "Import MMU Paint Data",
             "Render multi-material segmentation to UV texture for painting "
             "(experimental, may be slow for large models)"),
            ("NONE", "Geometry Only", "Skip all material and color data"),
        ],
        default="MATERIALS",
    )
    reuse_materials: bpy.props.BoolProperty(
        name="Reuse Existing Materials",
        description="Match and reuse existing Blender materials by name and color. "
                    "Prevents material duplication when re-importing edited files",
        default=True,
    )
    import_location: bpy.props.EnumProperty(
        name="Location",
        description="Where to place imported objects in the scene",
        items=[
            ("ORIGIN", "World Origin", "Place objects at world origin (0,0,0)"),
            ("CURSOR", "3D Cursor", "Place objects at 3D cursor position"),
            ("KEEP", "Keep Original", "Keep object positions from 3MF file"),
            ("GRID", "Grid Layout", "Arrange multiple files in a grid pattern"),
        ],
        default="KEEP",
    )
    grid_spacing: bpy.props.FloatProperty(
        name="Grid Spacing",
        description="Gap between objects when using Grid Layout (in scene units)",
        default=0.1, min=0.0, soft_max=10.0,
    )
    origin_to_geometry: bpy.props.EnumProperty(
        name="Origin Placement",
        description="How to set the object origin after import",
        items=[
            ("KEEP", "Keep Original", "Keep origin from 3MF file (typically corner)"),
            ("CENTER", "Center of Geometry", "Move origin to center of bounding box"),
            ("BOTTOM", "Bottom Center",
             "Move origin to bottom center (useful for placing on surfaces)"),
        ],
        default="KEEP",
    )

    # ----- Advanced MMU Paint settings (collapsed by default) ---------------

    show_advanced: bpy.props.BoolProperty(
        name="Advanced Settings",
        description="Show advanced MMU paint import settings",
        default=False,
    )
    paint_uv_method: bpy.props.EnumProperty(
        name="UV Method",
        description=(
            "UV unwrap method for MMU paint texture rendering.\n"
            "Smart UV Project groups adjacent faces into contiguous islands — "
            "best for seamless pixel coverage with the built-in rasterizer.\n"
            "Lightmap Pack gives every face its own rectangle — higher fidelity "
            "but may show gaps at triangle edges"
        ),
        items=[
            ("SMART", "Smart UV Project",
             "Groups coplanar faces into islands (best for most models)"),
            ("LIGHTMAP", "Lightmap Pack",
             "Every face gets unique UV space (higher fidelity, may show edge gaps)"),
        ],
        default="SMART",
    )
    paint_texture_size: bpy.props.EnumProperty(
        name="Texture Size",
        description="Resolution of the MMU paint texture",
        items=[
            ("0", "Auto", "Automatic based on triangle count (2K / 4K / 8K)"),
            ("1024", "1024", "1024\u00d71024 (fast, lower detail)"),
            ("2048", "2048", "2048\u00d72048 (good for simple models)"),
            ("4096", "4096", "4096\u00d74096 (recommended for most models)"),
            ("8192", "8192", "8192\u00d78192 (high detail, uses more memory)"),
            ("16384", "16384", "16384\u00d716384 (very high detail, slow)"),
        ],
        default="0",
    )

    # ----- UI ---------------------------------------------------------------

    def draw(self, context):
        """Draw the import options in the file browser."""
        layout = self.layout

        file_count = len(self.files) if self.files else 1
        if file_count > 1:
            info_box = layout.box()
            info_box.label(text=f"Importing {file_count} files", icon="FILE_FOLDER")

        layout.prop(self, "global_scale")
        layout.separator()

        box = layout.box()
        box.label(text="Import Options:", icon="IMPORT")
        box.prop(self, "import_materials")
        box.prop(self, "reuse_materials")

        layout.separator()
        placement_box = layout.box()
        placement_box.label(text="Placement:", icon="OBJECT_ORIGIN")
        placement_box.prop(self, "import_location")
        if self.import_location == "GRID":
            placement_box.prop(self, "grid_spacing")
        placement_box.prop(self, "origin_to_geometry")

        # --- Advanced settings (collapsible, only relevant for MMU Paint) ---
        if self.import_materials == "PAINT":
            layout.separator()
            adv_box = layout.box()
            adv_header = adv_box.row()
            adv_header.prop(
                self, "show_advanced",
                icon="TRIA_DOWN" if self.show_advanced else "TRIA_RIGHT",
                emboss=False,
            )
            if self.show_advanced:
                adv_box.prop(self, "paint_uv_method")
                adv_box.prop(self, "paint_texture_size")

    def invoke(self, context, event):
        """Initialize properties from preferences when the import dialog opens."""
        prefs = context.preferences.addons.get(__package__.rsplit(".", 1)[0])
        if prefs and prefs.preferences:
            self.global_scale = prefs.preferences.default_global_scale
            self.import_materials = prefs.preferences.default_import_materials
            self.reuse_materials = prefs.preferences.default_reuse_materials
            self.import_location = prefs.preferences.default_import_location
            self.origin_to_geometry = prefs.preferences.default_origin_to_geometry
            if hasattr(prefs.preferences, "default_grid_spacing"):
                self.grid_spacing = prefs.preferences.default_grid_spacing

        # If files are already provided (drag-drop), show popup instead of file browser
        if getattr(self, "directory", "") and getattr(self, "files", None):
            return self.invoke_popup(context)

        self.report({"INFO"}, "Importing, please wait...")
        return super().invoke(context, event)

    def safe_report(self, level: Set[str], message: str) -> None:
        """Safely report a message — works in both operator and unit-test contexts."""
        if hasattr(self, "report") and callable(getattr(self, "report", None)):
            self.report(level, message)

    # ----- Progress helpers -------------------------------------------------

    def _progress_begin(self, context: bpy.types.Context, message: str) -> None:
        self._progress_context = context
        self._progress_value = 0
        wm = getattr(context, "window_manager", None)
        if wm:
            if hasattr(wm, "progress_begin"):
                wm.progress_begin(0, 100)
            if hasattr(wm, "status_text_set"):
                wm.status_text_set(message)

    def _progress_update(self, value: int, message: Optional[str] = None) -> None:
        ctx_bl = getattr(self, "_progress_context", None)
        if not ctx_bl:
            return
        current = getattr(self, "_progress_value", 0)
        new_value = max(current, value)
        self._progress_value = new_value
        wm = getattr(ctx_bl, "window_manager", None)
        if wm and hasattr(wm, "progress_update"):
            wm.progress_update(new_value)
        if message and wm and hasattr(wm, "status_text_set"):
            wm.status_text_set(message)

    def _progress_end(self) -> None:
        ctx_bl = getattr(self, "_progress_context", None)
        if not ctx_bl:
            return
        wm = getattr(ctx_bl, "window_manager", None)
        if wm:
            if hasattr(wm, "progress_end"):
                wm.progress_end()
            if hasattr(wm, "status_text_set"):
                wm.status_text_set(None)
        self._progress_context = None

    # ----- Main entry point -------------------------------------------------

    def execute(self, context: bpy.types.Context) -> Set[str]:
        """Import one or more 3MF files."""
        self._progress_begin(context, "Importing 3MF...")
        try:
            return self._execute_inner(context)
        finally:
            self._progress_end()

    def _execute_inner(self, context: bpy.types.Context) -> Set[str]:
        """Core import logic — separated for clean progress begin/end."""
        # Build ImportContext from operator properties.
        options = ImportOptions(
            global_scale=self.global_scale,
            import_materials=self.import_materials,
            reuse_materials=self.reuse_materials,
            import_location=self.import_location,
            origin_to_geometry=self.origin_to_geometry,
            grid_spacing=self.grid_spacing,
            paint_uv_method=self.paint_uv_method,
            paint_texture_size=int(self.paint_texture_size),
        )
        ctx = ImportContext(options=options, operator=self)

        # Scene-level metadata (combine with existing scene metadata).
        scene_metadata = Metadata()
        scene_metadata.retrieve(bpy.context.scene)
        del scene_metadata["Title"]
        annotations = Annotations()
        annotations.retrieve()

        # Prepare input paths.
        paths = [os.path.join(self.directory, name.name) for name in self.files]
        if not paths:
            paths.append(self.filepath)

        # Switch to object mode, deselect everything.
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")
        if bpy.ops.object.select_all.poll():
            bpy.ops.object.select_all(action="DESELECT")

        for path in paths:
            ctx.current_archive_path = path
            self._progress_update(5, f"Reading {os.path.basename(path)}...")
            self._import_single_archive(ctx, path, context, scene_metadata, annotations)

        # Store scene-level data.
        scene_metadata.store(bpy.context.scene)
        annotations.store()
        _store_passthrough_impl(ctx)

        # Grid layout (multi-file or multi-object).
        if ctx.options.import_location == "GRID":
            apply_grid_layout(ctx.imported_objects, ctx.options.grid_spacing)

        # Zoom camera to imported objects.
        self._zoom_to_imported()

        self._progress_update(100, "Finalizing import...")
        debug(f"Imported {ctx.num_loaded} objects from 3MF files.")
        self.safe_report({"INFO"}, f"Imported {ctx.num_loaded} objects from 3MF files")

        # Show paint popup if needed.
        self._show_paint_popup(ctx)

        return {"FINISHED"}

    # ----- Per-archive pipeline ---------------------------------------------

    def _import_single_archive(
        self,
        ctx: ImportContext,
        path: str,
        context: bpy.types.Context,
        scene_metadata: Metadata,
        annotations: Annotations,
    ) -> None:
        """Import a single 3MF archive into the running context."""
        files_by_content_type = archive_mod.read_archive(ctx, path)

        # File metadata.
        for rels_file in files_by_content_type.get(RELS_MIMETYPE, []):
            annotations.add_rels(rels_file)
        annotations.add_content_types(files_by_content_type)
        archive_mod.must_preserve(ctx, files_by_content_type, annotations)

        # Parse each model file.
        for model_file in files_by_content_type.get(MODEL_MIMETYPE, []):
            try:
                document = xml.etree.ElementTree.ElementTree(file=model_file)
            except xml.etree.ElementTree.ParseError as e:
                error(f"3MF document in {path} is malformed: {str(e)}")
                ctx.safe_report({"ERROR"}, f"3MF document in {path} is malformed: {str(e)}")
                continue
            if document is None:
                continue
            root = document.getroot()

            self._process_model_root(ctx, root, path, context, scene_metadata)

    def _process_model_root(
        self,
        ctx: ImportContext,
        root: xml.etree.ElementTree.Element,
        path: str,
        context: bpy.types.Context,
        scene_metadata: Metadata,
    ) -> None:
        """Process a single <model> root element."""
        # Vendor detection.
        if ctx.options.import_materials != "NONE":
            ctx.vendor_format = detect_vendor(root)
            if ctx.vendor_format:
                ctx.safe_report({"INFO"}, f"Detected {ctx.vendor_format.upper()} Slicer format")
                debug(f"Will import {ctx.vendor_format} specific color data")
        else:
            ctx.vendor_format = None
            debug("Material import disabled: importing geometry only")

        # Extension activation.
        self._activate_extensions(ctx, root, path)

        # Unit scale.
        scale_unit = self._unit_scale(context, root)

        # Reset per-model resource dictionaries.
        ctx.resource_objects = {}
        ctx.resource_materials = {}
        ctx.resource_textures = {}
        ctx.resource_texture_groups = {}
        ctx.orca_filament_colors = {}
        ctx.object_default_extruders = {}

        # Read filament colours (priority order).
        read_orca_filament_colors(ctx, path)
        read_prusa_slic3r_colors(ctx, path)
        read_blender_addon_colors(ctx, path)
        read_prusa_object_extruders(ctx, path)

        self._progress_update(25, "Reading materials and objects...")

        # Metadata.
        self._read_metadata(ctx, root, scene_metadata)

        # Materials.
        self._read_all_materials(ctx, root)

        # Extract texture images.
        _extract_textures_impl(ctx, path)

        # Objects.
        geometry_mod.read_objects(ctx, root)

        # Build items.
        self._progress_update(60, "Building objects...")
        builder_mod.build_items(
            ctx, root, scale_unit,
            progress_callback=lambda v, m: self._progress_update(v, m),
        )

    # ----- Extension handling -----------------------------------------------

    def _activate_extensions(
        self, ctx: ImportContext, root: xml.etree.ElementTree.Element, path: str,
    ) -> None:
        """Activate required and recommended extensions from the model root."""
        required_ext = root.attrib.get("requiredextensions", "")
        if required_ext:
            resolved = self._resolve_extension_prefixes(root, required_ext)
            for ns in resolved:
                if ns in SUPPORTED_EXTENSIONS:
                    ctx.extension_manager.activate(ns)
                    debug(f"Activated required extension: {ns}")

        # Validate required extensions.
        if not self._is_supported(required_ext, root):
            resolved = self._resolve_extension_prefixes(root, required_ext)
            truly_unsupported = resolved - SUPPORTED_EXTENSIONS
            if truly_unsupported:
                ext_names = []
                for ns in truly_unsupported:
                    ext = get_extension_by_namespace(ns)
                    if ext:
                        ext_names.append(f"{ext.name} ({ext.extension_type.value})")
                    else:
                        ext_names.append(ns)
                ext_list = ", ".join(ext_names) if ext_names else ", ".join(truly_unsupported)
                warn(f"3MF document in {path} requires unsupported extensions: {ext_list}")
                ctx.safe_report(
                    {"WARNING"},
                    f"3MF document requires unsupported extensions: {ext_list}",
                )

        # Recommended extensions (v1.3.0 spec).
        recommended = root.attrib.get("recommendedextensions", "")
        if recommended:
            resolved_rec = self._resolve_extension_prefixes(root, recommended)
            for ns in resolved_rec:
                if ns in SUPPORTED_EXTENSIONS:
                    ctx.extension_manager.activate(ns)
                    debug(f"Activated recommended extension: {ns}")
            if not self._is_supported(recommended, root):
                truly_unsupported = resolved_rec - SUPPORTED_EXTENSIONS
                if truly_unsupported:
                    rec_names = []
                    for ns in truly_unsupported:
                        ext = get_extension_by_namespace(ns)
                        if ext:
                            rec_names.append(f"{ext.name} ({ext.extension_type.value})")
                        else:
                            rec_names.append(ns)
                    rec_list = ", ".join(rec_names) if rec_names else ", ".join(truly_unsupported)
                    debug(f"3MF document recommends extensions not fully supported: {rec_list}")
                    ctx.safe_report(
                        {"INFO"},
                        f"Document recommends extensions not fully supported: {rec_list}",
                    )

    @staticmethod
    def _resolve_extension_prefixes(
        root: xml.etree.ElementTree.Element, prefixes: str,
    ) -> Set[str]:
        """Resolve space-separated extension prefixes to full namespace URIs."""
        if not prefixes:
            return set()

        prefix_to_ns = {}
        for attr_name, attr_value in root.attrib.items():
            if attr_name.startswith("{"):
                continue
            if attr_name.startswith("xmlns:"):
                prefix_to_ns[attr_name[6:]] = attr_value

        # Known fallback mappings.
        from ..common.constants import PRODUCTION_NAMESPACE
        known = {
            "p": PRODUCTION_NAMESPACE,
            "m": "http://schemas.microsoft.com/3dmanufacturing/material/2015/02",
            "slic3rpe": "http://schemas.slic3r.org/3mf/2017/06",
        }
        prefix_to_ns.update({k: v for k, v in known.items() if k not in prefix_to_ns})

        resolved: Set[str] = set()
        for prefix in prefixes.split():
            prefix = prefix.strip()
            if not prefix:
                continue
            if prefix in prefix_to_ns:
                resolved.add(prefix_to_ns[prefix])
            else:
                resolved.add(prefix)
                debug(f"Unknown extension prefix: {prefix}")
        return resolved

    @staticmethod
    def _is_supported(
        required_extensions: str,
        root: Optional[xml.etree.ElementTree.Element] = None,
    ) -> bool:
        """Return whether all required extensions are supported."""
        if root is not None:
            extensions = Import3MF._resolve_extension_prefixes(root, required_extensions)
        else:
            extensions = set(filter(lambda x: x != "", required_extensions.split(" ")))
        return extensions <= SUPPORTED_EXTENSIONS

    # ----- Unit scale -------------------------------------------------------

    def _unit_scale(self, context: bpy.types.Context, root: xml.etree.ElementTree.Element) -> float:
        """Calculate the scale factor for the 3MF document's units (including global_scale)."""
        from ..common.constants import MODEL_DEFAULT_UNIT

        scale = self.global_scale

        blender_unit_to_metre = context.scene.unit_settings.scale_length
        if blender_unit_to_metre == 0:
            blender_unit = context.scene.unit_settings.length_unit
            blender_unit_to_metre = blender_to_metre[blender_unit]

        threemf_unit = root.attrib.get("unit", MODEL_DEFAULT_UNIT)
        threemf_unit_to_metre = threemf_to_metre[threemf_unit]
        scale *= threemf_unit_to_metre / blender_unit_to_metre
        return scale

    # ----- Metadata ---------------------------------------------------------

    def _read_metadata(
        self,
        ctx: ImportContext,
        root: xml.etree.ElementTree.Element,
        scene_metadata: Metadata,
    ) -> None:
        """Read metadata tags from the model root."""
        for metadata_node in root.iterfind("./3mf:metadata", MODEL_NAMESPACES):
            if "name" not in metadata_node.attrib:
                warn("Metadata entry without name is discarded.")
                ctx.safe_report({"WARNING"}, "Metadata entry without name is discarded")
                continue
            name = metadata_node.attrib["name"]
            preserve_str = metadata_node.attrib.get("preserve", "0")
            preserve = preserve_str != "0" and preserve_str.lower() != "false"
            datatype = metadata_node.attrib.get("type", "")
            value = metadata_node.text
            scene_metadata[name] = MetadataEntry(
                name=name, preserve=preserve, datatype=datatype, value=value,
            )

    # ----- Materials pipeline -----------------------------------------------

    def _read_all_materials(
        self, ctx: ImportContext, root: xml.etree.ElementTree.Element,
    ) -> None:
        """Read all material resources from the 3MF document.

        Delegates to the materials sub-package functions, passing *ctx* as the
        ``op`` parameter (duck-typed — :class:`ImportContext` exposes the same
        attributes that the material functions access).
        """
        if ctx.options.import_materials == "NONE":
            debug("Material import disabled, skipping all material data")
            return

        material_ns = {"m": MATERIAL_NAMESPACE}

        # PBR display properties first (basematerials reference them).
        pbr_metallic = _read_pbr_metallic_impl(ctx, root, material_ns)
        pbr_specular = _read_pbr_specular_impl(ctx, root, material_ns)
        pbr_translucent = _read_pbr_translucent_impl(ctx, root, material_ns)
        _read_pbr_texture_display_impl(ctx, root, material_ns)

        display_properties = {}
        display_properties.update(pbr_metallic)
        display_properties.update(pbr_specular)
        display_properties.update(pbr_translucent)
        if display_properties:
            debug(f"Parsed {len(display_properties)} PBR display property groups")

        # Base materials and colour groups.
        _read_materials_impl(ctx, root, material_ns, display_properties)

        # Textures.
        _read_textures_impl(ctx, root, material_ns)
        _read_texture_groups_impl(ctx, root, material_ns, display_properties)

        # Passthrough types (round-trip).
        _read_composite_impl(ctx, root, material_ns)
        _read_multiproperties_impl(ctx, root, material_ns)

    # ----- Post-import helpers ----------------------------------------------

    def _zoom_to_imported(self) -> None:
        """Zoom the 3D viewport to fit imported objects."""
        if bpy.app.background or not bpy.context.screen:
            return
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        try:
                            override = bpy.context.copy()
                            override["area"] = area
                            override["region"] = region
                            override["edit_object"] = bpy.context.edit_object
                            with bpy.context.temp_override(**override):
                                bpy.ops.view3d.view_selected()
                        except AttributeError:
                            override = {
                                "area": area,
                                "region": region,
                                "edit_object": bpy.context.edit_object,
                            }
                            bpy.ops.view3d.view_selected(override)

    @staticmethod
    def _show_paint_popup(ctx: ImportContext) -> None:
        """Show the MMU paint popup if any objects had paint data."""
        if not ctx._paint_object_names:
            return
        paint_obj_name = ctx._paint_object_names[0]
        for obj in ctx.imported_objects:
            if obj.data and obj.data.name == paint_obj_name:
                paint_obj_name = obj.name
                break
        try:
            bpy.ops.mmu.import_paint_popup("INVOKE_DEFAULT", object_name=paint_obj_name)
        except Exception as e:
            debug(f"Could not show paint popup: {e}")
