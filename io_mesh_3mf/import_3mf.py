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


import base64  # To encode MustPreserve files in the Blender scene.
import collections  # For namedtuple.
import json  # For reading Orca project_settings.config
import os.path  # To take file paths relative to the selected directory.
import re  # To find files in the archive based on the content types.
import xml.etree.ElementTree  # To parse the 3dmodel.model file.
import zipfile  # To read the 3MF files which are secretly zip archives.
from typing import Optional, Dict, Set, List, Tuple, Pattern, IO

import bpy  # The Blender API.
import bpy.ops  # To adjust the camera to fit models.
import bpy.props  # To define metadata properties for the operator.
import bpy.types  # This class is an operator in Blender.
import bpy_extras.io_utils  # Helper functions to import meshes more easily.
import bpy_extras.node_shader_utils  # Getting correct color spaces for materials.
import mathutils  # For the transformation matrices.

from .annotations import (  # To use annotations to decide on what to import.
    Annotations,
    ContentType,
    Relationship,
)
from .constants import (
    RELS_MIMETYPE,
    MODEL_MIMETYPE,
    MODEL_NAMESPACES,
    MODEL_DEFAULT_UNIT,
    SUPPORTED_EXTENSIONS,
    PRODUCTION_NAMESPACE,
    SLIC3RPE_NAMESPACE,
    CONTENT_TYPES_LOCATION,
    conflicting_mustpreserve_contents,
)
from .extensions import (
    ExtensionManager,
    get_extension_by_namespace,
)
from .metadata import Metadata, MetadataEntry  # To store and serialize metadata.
from .unit_conversions import (  # To convert to Blender's units.
    blender_to_metre,
    threemf_to_metre,
)

# Hash-based segmentation support (PrusaSlicer, Orca Slicer)
from .hash_segmentation import (
    TriangleSubdivider,
    decode_segmentation_string,
)

# Import materials package for Materials Extension support
from .import_materials import (
    read_materials as _read_materials_impl,
    find_existing_material as _find_existing_material_impl,
    parse_hex_color as _parse_hex_color_impl,
    srgb_to_linear as _srgb_to_linear_impl,
    read_textures as _read_textures_impl,
    read_texture_groups as _read_texture_groups_impl,
    extract_textures_from_archive as _extract_textures_impl,
    get_or_create_textured_material as _get_or_create_textured_material_impl,
    setup_textured_material as _setup_textured_material_impl,
    setup_multi_textured_material as _setup_multi_textured_material_impl,
    read_pbr_metallic_properties as _read_pbr_metallic_impl,
    read_pbr_specular_properties as _read_pbr_specular_impl,
    read_pbr_translucent_properties as _read_pbr_translucent_impl,
    read_pbr_texture_display_properties as _read_pbr_texture_display_impl,
    apply_pbr_to_principled as _apply_pbr_to_principled_impl,
    apply_pbr_textures_to_material as _apply_pbr_textures_impl,
    read_composite_materials as _read_composite_impl,
    read_multiproperties as _read_multiproperties_impl,
    store_passthrough_materials as _store_passthrough_impl,
)

# Import triangle sets module
from .import_trianglesets import read_triangle_sets as _read_triangle_sets_impl

# Debugging
from .utilities import debug, warn, error

# IDE and Documentation support.
__all__ = ["Import3MF"]

ResourceObject = collections.namedtuple(
    "ResourceObject",
    [
        "vertices",
        "triangles",
        "materials",
        "components",
        "metadata",
        "triangle_sets",
        "triangle_uvs",
        "segmentation_strings",
        "default_extruder",
    ],
    defaults=[
        None,
        None,
        None,
        None,
    ],  # triangle_sets, triangle_uvs, segmentation_strings, default_extruder are optional
)
# Component with optional path field for Production Extension support
# Using defaults parameter (Python 3.7+) to make path optional
Component = collections.namedtuple("Component", ["resource_object", "transformation", "path"], defaults=[None])

# PBR Material data structure for 3MF Materials Extension
# Stores all properties from pbmetallicdisplayproperties, pbspeculardisplayproperties, and translucentdisplayproperties
# All PBR fields are optional (defaults to None) for backward compatibility
ResourceMaterial = collections.namedtuple(
    "ResourceMaterial",
    [
        "name",  # Material name
        "color",  # RGBA tuple (0-1 range)
        # PBR Metallic workflow (pbmetallicdisplayproperties)
        "metallic",  # 0.0 (dielectric) to 1.0 (metal)
        "roughness",  # 0.0 (smooth) to 1.0 (rough)
        # PBR Specular workflow (pbspeculardisplayproperties)
        "specular_color",  # RGB tuple for specular reflectance at normal incidence
        "glossiness",  # 0.0 (rough) to 1.0 (smooth) - inverse of roughness
        # Translucent materials (translucentdisplayproperties)
        "ior",  # Index of refraction (typically ~1.45 for glass)
        "attenuation",  # RGB attenuation coefficients for volume absorption
        "transmission",  # 0.0 (opaque) to 1.0 (fully transparent)
        # Texture support (Materials Extension texture2dgroup)
        "texture_id",  # ID of texture2dgroup this material belongs to (for textured materials)
        # Textured PBR support (pbmetallictexturedisplayproperties / pbspeculartexturedisplayproperties)
        "metallic_texid",  # ID of texture2d for metallic map
        "roughness_texid",  # ID of texture2d for roughness map
        "specular_texid",  # ID of texture2d for specular map
        "glossiness_texid",  # ID of texture2d for glossiness map
        "basecolor_texid",  # ID of texture2d for base color map (from pbmetallictexturedisplayproperties)
        # Multiproperties multi-texture support
        "extra_texture_ids",  # List of additional texture2dgroup IDs (for multiproperties with multiple textures)
    ],
    defaults=[
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ],  # All optional
)

# Texture2D resource - stores texture image metadata from <m:texture2d> elements
# path: Path to texture file in archive (e.g., "/3D/Texture/wood.png")
# contenttype: MIME type ("image/png" or "image/jpeg")
# tilestyleu, tilestylev: Tiling mode ("wrap", "mirror", "clamp", "none")
# filter: Texture filter ("auto", "linear", "nearest")
ResourceTexture = collections.namedtuple(
    "ResourceTexture",
    ["path", "contenttype", "tilestyleu", "tilestylev", "filter", "blender_image"],
    defaults=[
        "wrap",
        "wrap",
        "auto",
        None,
    ],  # Default tile styles and filter per 3MF spec
)

# Texture2DGroup - container for texture coordinates that reference a texture
# texid: ID of the <texture2d> element this group references
# tex2coords: List of (u, v) tuples representing texture coordinates
# displaypropertiesid: Optional PBR display properties ID
ResourceTextureGroup = collections.namedtuple(
    "ResourceTextureGroup",
    ["texid", "tex2coords", "displaypropertiesid"],
    defaults=[None],  # displaypropertiesid is optional
)

# Composite Materials (3MF Materials Extension)
# Stores mixed material definitions for round-trip support
# matid: ID of referenced basematerials group
# matindices: Space-delimited list of material indices to mix
# displaypropertiesid: Optional PBR display properties reference
# composites: List of dicts with "values" attribute (mixing ratios)
ResourceComposite = collections.namedtuple(
    "ResourceComposite",
    ["matid", "matindices", "displaypropertiesid", "composites"],
    defaults=[None, []],  # displaypropertiesid optional, composites list
)

# Multiproperties (3MF Materials Extension)
# Stores layered property definitions for round-trip support
# pids: Space-delimited list of property group IDs (layering order)
# blendmethods: Optional blend methods ("mix" or "multiply") for each layer
# multis: List of dicts with "pindices" attribute (property indices per layer)
ResourceMultiproperties = collections.namedtuple(
    "ResourceMultiproperties",
    ["pids", "blendmethods", "multis"],
    defaults=[None, []],  # blendmethods optional, multis list
)

# Textured PBR Display Properties (3MF Materials Extension)
# For pbspeculartexturedisplayproperties and pbmetallictexturedisplayproperties
# These reference texture2d elements for PBR channel maps
ResourcePBRTextureDisplay = collections.namedtuple(
    "ResourcePBRTextureDisplay",
    ["type", "name", "primary_texid", "secondary_texid", "basecolor_texid", "factors"],
    defaults=[
        None,
        None,
        None,
        {},
    ],  # secondary_texid, basecolor_texid optional, factors is dict
)

# Passthrough storage for colorgroup elements (Materials Extension)
# colors: List of color strings in original format (e.g., "#FF0000FF")
ResourceColorgroup = collections.namedtuple(
    "ResourceColorgroup",
    ["colors", "displaypropertiesid"],
    defaults=[None],  # displaypropertiesid optional
)

# Passthrough storage for non-textured PBR display properties
# type: "metallic", "specular", or "translucent"
# properties: List of dicts containing the raw attribute values for each child element
ResourcePBRDisplayProps = collections.namedtuple("ResourcePBRDisplayProps", ["type", "properties"])

# Orca Slicer paint_color decoding - maps paint codes to filament indices
# This is the reverse of ORCA_FILAMENT_CODES in export_3mf.py
# Note: Paint codes can be uppercase or lowercase, so we'll normalize to uppercase
ORCA_PAINT_TO_INDEX = {
    "": 0,
    "4": 1,
    "8": 2,
    "0C": 3,
    "1C": 4,
    "2C": 5,
    "3C": 6,
    "4C": 7,
    "5C": 8,
    "6C": 9,
    "7C": 10,
    "8C": 11,
    "9C": 12,
    "AC": 13,
    "BC": 14,
    "CC": 15,
    "DC": 16,
    "EC": 17,
    "0FC": 18,
    "1FC": 19,
    "2FC": 20,
    "3FC": 21,
    "4FC": 22,
    "5FC": 23,
    "6FC": 24,
    "7FC": 25,
    "8FC": 26,
    "9FC": 27,
    "AFC": 28,
    "BFC": 29,
}


def parse_paint_color_to_index(paint_code: str) -> int:
    """
    Parse a paint_color code to a filament index.

    Returns 0 if not a known Orca paint code (caller should try segmentation decode).

    :param paint_code: The paint_color attribute value.
    :return: Filament index (1-based), or 0 if not a known paint code.
    """
    if not paint_code:
        return 0

    # Normalize to uppercase for lookup
    normalized = paint_code.upper()
    if normalized in ORCA_PAINT_TO_INDEX:
        return ORCA_PAINT_TO_INDEX[normalized]

    # Try without normalization
    if paint_code in ORCA_PAINT_TO_INDEX:
        return ORCA_PAINT_TO_INDEX[paint_code]

    # Not a known Orca paint code - return 0 so caller can try segmentation decode
    return 0


# Production Extension namespace for p:path attributes
PRODUCTION_NAMESPACES = {"p": "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"}


class Import3MF(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """
    Operator that imports a 3MF file into Blender.
    """

    # Metadata.
    bl_idname = "import_mesh.threemf"
    bl_label = "Import 3MF"
    bl_description = "Load a 3MF scene"
    bl_options = {"UNDO"}
    filename_ext = ".3mf"

    # Options for the user.
    filter_glob: bpy.props.StringProperty(default="*.3mf", options={"HIDDEN"})
    files: bpy.props.CollectionProperty(name="File Path", type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    global_scale: bpy.props.FloatProperty(name="Scale", default=1.0, soft_min=0.001, soft_max=1000.0, min=1e-6, max=1e6)
    import_materials: bpy.props.EnumProperty(
        name="Material Mode",
        description="How to import material and color data",
        items=[
            (
                "MATERIALS",
                "Import Materials",
                "Import material colors and properties (standard 3MF)",
            ),
            (
                "PAINT",
                "Import MMU Paint Data",
                "Render multi-material segmentation to UV texture for painting (experimental, may be slow for large models)",
            ),
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
        default=0.1,
        min=0.0,
        soft_max=10.0,
    )
    origin_to_geometry: bpy.props.EnumProperty(
        name="Origin Placement",
        description="How to set the object origin after import",
        items=[
            ("KEEP", "Keep Original", "Keep origin from 3MF file (typically corner)"),
            ("CENTER", "Center of Geometry", "Move origin to center of bounding box"),
            (
                "BOTTOM",
                "Bottom Center",
                "Move origin to bottom center (useful for placing on surfaces)",
            ),
        ],
        default="KEEP",
    )

    def draw(self, context):
        """Draw the import options in the file browser."""
        layout = self.layout

        # Show file count if multiple files selected
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
        # Show grid spacing only when grid layout is selected
        if self.import_location == "GRID":
            placement_box.prop(self, "grid_spacing")
        placement_box.prop(self, "origin_to_geometry")

    def invoke(self, context, event):
        """
        Initialize properties from preferences when the import dialog is opened.

        If files are already provided (e.g., from drag-and-drop), shows a popup
        with just the import options instead of the full file browser.
        """
        prefs = context.preferences.addons.get(__package__)
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
        """
        Safely report a message, using Blender's report system if available, otherwise just logging.
        This allows the class to work both as a Blender operator and in unit tests.
        :param level: The report level (e.g., {'ERROR'}, {'WARNING'}, {'INFO'})
        :param message: The message to report
        """
        if hasattr(self, "report") and callable(getattr(self, "report", None)):
            self.report(level, message)
        # If report is not available, the message has already been logged via the log module

    def _progress_begin(self, context: bpy.types.Context, message: str) -> None:
        self._progress_context = context
        self._progress_value = 0
        window_manager = getattr(context, "window_manager", None)
        if window_manager:
            if hasattr(window_manager, "progress_begin"):
                window_manager.progress_begin(0, 100)
            if hasattr(window_manager, "status_text_set"):
                window_manager.status_text_set(message)

    def _progress_update(self, value: int, message: Optional[str] = None) -> None:
        context = getattr(self, "_progress_context", None)
        if not context:
            return
        current_value = getattr(self, "_progress_value", 0)
        new_value = max(current_value, value)
        self._progress_value = new_value
        window_manager = getattr(context, "window_manager", None)
        if window_manager and hasattr(window_manager, "progress_update"):
            window_manager.progress_update(new_value)
        if message and window_manager and hasattr(window_manager, "status_text_set"):
            window_manager.status_text_set(message)

    def _progress_end(self) -> None:
        context = getattr(self, "_progress_context", None)
        if not context:
            return
        window_manager = getattr(context, "window_manager", None)
        if window_manager:
            if hasattr(window_manager, "progress_end"):
                window_manager.progress_end()
            if hasattr(window_manager, "status_text_set"):
                window_manager.status_text_set(None)
        self._progress_context = None

    def _apply_grid_layout(self) -> None:
        """
        Arrange imported objects in a grid pattern.

        Objects are laid out in rows along the X axis, wrapping to new rows
        along the Y axis. Spacing is determined by object bounding boxes
        plus the grid_spacing gap.

        If only one object was imported, it falls back to world origin placement.
        """
        objects = getattr(self, "imported_objects", [])
        if not objects:
            return

        # Fallback: single object goes to origin (already placed there)
        if len(objects) == 1:
            debug("Grid layout: single object, placed at origin")
            return

        # Calculate bounding boxes for all objects
        object_bounds = []
        for obj in objects:
            # Get world-space bounding box
            bbox = [obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box]
            min_corner = mathutils.Vector((min(v.x for v in bbox), min(v.y for v in bbox), min(v.z for v in bbox)))
            max_corner = mathutils.Vector((max(v.x for v in bbox), max(v.y for v in bbox), max(v.z for v in bbox)))
            size = max_corner - min_corner
            object_bounds.append({"obj": obj, "size": size, "min": min_corner, "max": max_corner})

        # Calculate grid dimensions (roughly square layout)
        import math

        num_objects = len(objects)
        cols = math.ceil(math.sqrt(num_objects))
        rows = math.ceil(num_objects / cols)

        spacing = getattr(self, "grid_spacing", 0.1)

        # Calculate column widths and row heights (max size in each)
        col_widths = []
        row_heights = []

        for col in range(cols):
            col_objs = [object_bounds[i] for i in range(col, num_objects, cols)]
            if col_objs:
                col_widths.append(max(b["size"].x for b in col_objs))
            else:
                col_widths.append(0)

        for row in range(rows):
            start_idx = row * cols
            end_idx = min(start_idx + cols, num_objects)
            row_objs = object_bounds[start_idx:end_idx]
            if row_objs:
                row_heights.append(max(b["size"].y for b in row_objs))
            else:
                row_heights.append(0)

        # Position each object
        current_y = 0.0
        for row in range(rows):
            current_x = 0.0
            for col in range(cols):
                idx = row * cols + col
                if idx >= num_objects:
                    break

                bounds = object_bounds[idx]
                obj = bounds["obj"]

                # Calculate center position for this cell
                cell_center_x = current_x + col_widths[col] / 2
                cell_center_y = current_y + row_heights[row] / 2

                # Calculate object's current center offset from origin
                obj_center_x = (bounds["min"].x + bounds["max"].x) / 2
                obj_center_y = (bounds["min"].y + bounds["max"].y) / 2

                # Move object so its center aligns with cell center
                offset = mathutils.Vector(
                    (
                        cell_center_x - obj_center_x,
                        cell_center_y - obj_center_y,
                        -bounds["min"].z,  # Place on Z=0 plane
                    )
                )

                obj.location += offset

                current_x += col_widths[col] + spacing

            current_y += row_heights[row] + spacing

        debug(f"Grid layout: arranged {num_objects} objects in {rows}x{cols} grid")

    def detect_vendor(self, root: xml.etree.ElementTree.Element) -> Optional[str]:
        """
        Detect if this 3MF file was created by a specific vendor/slicer.

        This allows us to handle vendor-specific extensions appropriately.

        :param root: The root element of the 3MF model document
        :return: Vendor identifier string (e.g., 'orca', 'bambu') or None for standard 3MF
        """
        # Check for BambuStudio/Orca Slicer specific metadata
        for metadata_node in root.iterfind("./3mf:metadata", MODEL_NAMESPACES):
            name = metadata_node.attrib.get("name", "")
            if name == "BambuStudio:3mfVersion":
                debug("Detected BambuStudio/Orca Slicer format")
                return "orca"
            if name == "Application" and metadata_node.text:
                app_name = metadata_node.text.lower()
                if "orca" in app_name or "bambu" in app_name:
                    debug(f"Detected Orca/Bambu format from Application: {metadata_node.text}")
                    return "orca"

        # Check for BambuStudio namespace in root attributes
        for attr_name in root.attrib:
            if "bambu" in attr_name.lower():
                debug(f"Detected BambuStudio format from attribute: {attr_name}")
                return "orca"

        return None

    def execute(self, context: bpy.types.Context) -> Set[str]:
        """
        The main routine that reads out the 3MF file.

        This function serves as a high-level overview of the steps involved to read the 3MF file.
        :param context: The Blender context.
        :return: A set of status flags to indicate whether the operation succeeded or not.
        """
        self._progress_begin(context, "Importing 3MF...")
        try:
            # Reset state.
            self.resource_objects = {}
            self.resource_materials = {}
            self.resource_to_material = {}
            self.textured_to_basematerial_map = {}  # Map textured ResourceMaterial -> original basematerial (for deduplication)
            self.resource_textures = {}  # ID -> ResourceTexture
            self.resource_texture_groups = {}  # ID -> ResourceTextureGroup
            self.resource_composites = {}  # ID -> ResourceComposite (round-trip)
            self.resource_multiproperties = {}  # ID -> ResourceMultiproperties (round-trip)
            self.resource_pbr_texture_displays = {}  # ID -> ResourcePBRTextureDisplay (round-trip)
            self.resource_colorgroups = {}  # ID -> ResourceColorgroup (round-trip)
            self.resource_pbr_display_props = {}  # ID -> ResourcePBRDisplayProps (round-trip)
            self.object_passthrough_pids = {}  # objectid -> pid for objects whose pid references multiproperties
            self.component_instance_cache = {}  # Track component instances: objectid -> (mesh_data, instances_count)
            self.num_loaded = 0
            self.imported_objects = []  # Track all imported objects for grid layout
            self._paint_object_names = []  # Track objects with rendered paint textures
            self.vendor_format = None  # Will be set when we detect vendor-specific format
            self.extension_manager = ExtensionManager()  # Track active extensions
            scene_metadata = Metadata()
            # If there was already metadata in the scene, combine that with this file.
            scene_metadata.retrieve(bpy.context.scene)
            # Don't load the title from the old scene. If there is a title in the imported 3MF, use that.
            # Else, we'll not override the scene title and it gets retained.
            del scene_metadata["Title"]
            annotations = Annotations()
            annotations.retrieve()  # If there were already annotations in the scene, combine that with this file.

            # Preparation of the input parameters.
            paths = [os.path.join(self.directory, name.name) for name in self.files]
            if not paths:
                paths.append(self.filepath)

            if bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode="OBJECT")  # Switch to object mode to view the new file.
            if bpy.ops.object.select_all.poll():
                bpy.ops.object.select_all(action="DESELECT")  # Deselect other files.

            for path in paths:
                # Store current archive path for Production Extension support
                self.current_archive_path = path
                self._progress_update(5, f"Reading {os.path.basename(path)}...")

                files_by_content_type = self.read_archive(path)  # Get the files from the archive.

                # File metadata.
                for rels_file in files_by_content_type.get(RELS_MIMETYPE, []):
                    annotations.add_rels(rels_file)
                annotations.add_content_types(files_by_content_type)
                self.must_preserve(files_by_content_type, annotations)

                # Read the model data.
                for model_file in files_by_content_type.get(MODEL_MIMETYPE, []):
                    try:
                        document = xml.etree.ElementTree.ElementTree(file=model_file)
                    except xml.etree.ElementTree.ParseError as e:
                        error(f"3MF document in {path} is malformed: {str(e)}")
                        self.safe_report({"ERROR"}, f"3MF document in {path} is malformed: {str(e)}")
                        continue
                    if document is None:
                        # This file is corrupt or we can't read it.
                        # No error code to communicate this to Blender.
                        continue  # Leave the scene empty / skip this file.
                    root = document.getroot()

                    # Detect vendor-specific format (if materials are enabled)
                    if self.import_materials != "NONE":
                        self.vendor_format = self.detect_vendor(root)
                        if self.vendor_format:
                            self.safe_report(
                                {"INFO"},
                                f"Detected {self.vendor_format.upper()} Slicer format",
                            )
                            debug(f"Will import {self.vendor_format} specific color data")
                    else:
                        self.vendor_format = None
                        debug("Material import disabled: importing geometry only")

                    # Activate extensions based on what's declared in the file
                    required_ext = root.attrib.get("requiredextensions", "")
                    if required_ext:
                        resolved_namespaces = self.resolve_extension_prefixes(root, required_ext)
                        for ns in resolved_namespaces:
                            if ns in SUPPORTED_EXTENSIONS:
                                self.extension_manager.activate(ns)
                                debug(f"Activated required extension: {ns}")

                    # Validate required extensions
                    if not self.is_supported(root.attrib.get("requiredextensions", ""), root):
                        unsupported = root.attrib.get("requiredextensions", "")
                        resolved = self.resolve_extension_prefixes(root, unsupported)
                        # Only show warning for truly unsupported extensions
                        truly_unsupported = resolved - SUPPORTED_EXTENSIONS
                        if truly_unsupported:
                            # Try to get human-readable extension names
                            ext_names = []
                            for ns in truly_unsupported:
                                ext = get_extension_by_namespace(ns)
                                if ext:
                                    ext_names.append(f"{ext.name} ({ext.extension_type.value})")
                                else:
                                    ext_names.append(ns)

                            ext_list = ", ".join(ext_names) if ext_names else ", ".join(truly_unsupported)
                            warn(f"3MF document in {path} requires unsupported extensions: {ext_list}")
                            self.safe_report(
                                {"WARNING"},
                                f"3MF document requires unsupported extensions: {ext_list}",
                            )
                        # Still continue processing even though the spec says not to. Our aim is to retrieve whatever
                        # information we can.

                    # Check for recommended extensions (v1.3.0 spec addition)
                    recommended = root.attrib.get("recommendedextensions", "")
                    if recommended:
                        resolved_recommended = self.resolve_extension_prefixes(root, recommended)
                        for ns in resolved_recommended:
                            if ns in SUPPORTED_EXTENSIONS:
                                self.extension_manager.activate(ns)
                                debug(f"Activated recommended extension: {ns}")

                        if not self.is_supported(recommended, root):
                            truly_unsupported = resolved_recommended - SUPPORTED_EXTENSIONS
                            if truly_unsupported:
                                # Try to get human-readable extension names
                                rec_names = []
                                for ns in truly_unsupported:
                                    ext = get_extension_by_namespace(ns)
                                    if ext:
                                        rec_names.append(f"{ext.name} ({ext.extension_type.value})")
                                    else:
                                        rec_names.append(ns)

                                rec_list = ", ".join(rec_names) if rec_names else ", ".join(truly_unsupported)
                                debug(f"3MF document in {path} recommends extensions not fully supported: {rec_list}")
                                self.safe_report(
                                    {"INFO"},
                                    f"Document recommends extensions not fully supported: {rec_list}",
                                )

                    scale_unit = self.unit_scale(context, root)
                    self.resource_objects = {}
                    self.resource_materials = {}
                    self.resource_textures = {}  # ID -> ResourceTexture
                    self.resource_texture_groups = {}  # ID -> ResourceTextureGroup
                    self.orca_filament_colors = {}  # Maps filament index -> hex color
                    self.object_default_extruders = {}  # Maps object ID -> default extruder (1-based)

                    # Try to read filament colors from metadata (priority order)
                    self.read_orca_filament_colors(path)  # Orca project_settings.config
                    self.read_prusa_slic3r_colors(path)  # PrusaSlicer Slic3r_PE.config
                    self.read_blender_addon_colors(path)  # Blender addon fallback (direct extruder index)
                    # Note: read_prusa_filament_colors removed - replaced by read_blender_addon_colors
                    self.read_prusa_object_extruders(path)  # PrusaSlicer object extruder assignments

                    self._progress_update(25, "Reading materials and objects...")
                    scene_metadata = self.read_metadata(root, scene_metadata)
                    self.read_materials(root)
                    # Extract texture images from archive after materials are parsed
                    self._extract_textures_from_archive(path)
                    self.read_objects(root)
                    self._progress_update(60, "Building objects...")
                    self.build_items(root, scale_unit)

            scene_metadata.store(bpy.context.scene)
            annotations.store()
            self._store_passthrough_materials()  # Store round-trip material data

            # Apply grid layout if selected (works for multi-file or multi-object imports)
            if self.import_location == "GRID":
                self._apply_grid_layout()

            # Zoom the camera to view the imported objects.
            if not bpy.app.background and bpy.context.screen:
                for area in bpy.context.screen.areas:
                    if area.type == "VIEW_3D":
                        for region in area.regions:
                            if region.type == "WINDOW":
                                try:
                                    # Since Blender 3.2:
                                    context = bpy.context.copy()
                                    context["area"] = area
                                    context["region"] = region
                                    context["edit_object"] = bpy.context.edit_object
                                    with bpy.context.temp_override(**context):
                                        bpy.ops.view3d.view_selected()
                                except AttributeError:  # temp_override doesn't exist before Blender 3.2.
                                    # Before Blender 3.2:
                                    override = {
                                        "area": area,
                                        "region": region,
                                        "edit_object": bpy.context.edit_object,
                                    }
                                    bpy.ops.view3d.view_selected(override)

            self._progress_update(100, "Finalizing import...")
            debug(f"Imported {self.num_loaded} objects from 3MF files.")
            self.safe_report({"INFO"}, f"Imported {self.num_loaded} objects from 3MF files")

            # Show popup if any objects had MMU paint data
            if hasattr(self, "_paint_object_names") and self._paint_object_names:
                # Find the first paint object to offer switching to
                paint_obj_name = self._paint_object_names[0]
                # Find the Blender object by mesh name
                for obj in self.imported_objects:
                    if obj.data and obj.data.name == paint_obj_name:
                        paint_obj_name = obj.name
                        break
                try:
                    bpy.ops.mmu.import_paint_popup("INVOKE_DEFAULT", object_name=paint_obj_name)
                except Exception as e:
                    debug(f"Could not show paint popup: {e}")

            return {"FINISHED"}
        finally:
            self._progress_end()

    # The rest of the functions are in order of when they are called.

    def read_archive(self, path: str) -> Dict[str, List[IO[bytes]]]:
        """
        Creates file streams from all the files in the archive.

        The results are sorted by their content types. Consumers of this data can pick the content types that they know
        from the file and process those.
        :param path: The path to the archive to read.
        :return: A dictionary with all of the resources in the archive by content type. The keys in this dictionary are
        the different content types available in the file. The values in this dictionary are lists of input streams
        referring to files in the archive.
        """
        result = {}
        try:
            archive = zipfile.ZipFile(path)
            content_types = self.read_content_types(archive)
            mime_types = self.assign_content_types(archive, content_types)
            for path, mime_type in mime_types.items():
                if mime_type not in result:
                    result[mime_type] = []
                # Zipfile can open an infinite number of streams at the same time. Don't worry about it.
                result[mime_type].append(archive.open(path))
        except (zipfile.BadZipFile, EnvironmentError) as e:
            # File is corrupt, or the OS prevents us from reading it (doesn't exist, no permissions, etc.)
            error(f"Unable to read archive: {e}")
            self.safe_report({"ERROR"}, f"Unable to read archive: {e}")
            return result
        return result

    def read_content_types(self, archive: zipfile.ZipFile) -> List[Tuple[Pattern[str], str]]:
        """
        Read the content types from a 3MF archive.

        The output of this reading is a list of MIME types that are each mapped to a regular expression that matches on
        the file paths within the archive that could contain this content type. This encodes both types of descriptors
        for the content types that can occur in the content types document: Extensions and full paths.

        The output is ordered in priority. Matches that should be evaluated first will be put in the front of the output
        list.
        :param archive: The 3MF archive to read the contents from.
        :return: A list of tuples, in order of importance, where the first element describes a regex of paths that
        match, and the second element is the MIME type string of the content type.
        """
        namespaces = {"ct": "http://schemas.openxmlformats.org/package/2006/content-types"}
        result = []

        try:
            with archive.open(CONTENT_TYPES_LOCATION) as f:
                try:
                    root = xml.etree.ElementTree.ElementTree(file=f)
                except xml.etree.ElementTree.ParseError as e:
                    warn(f"{CONTENT_TYPES_LOCATION} has malformed XML(position {e.position[0]}:{e.position[1]}).")
                    self.safe_report(
                        {"WARNING"},
                        f"{CONTENT_TYPES_LOCATION} has malformed XML at position {e.position[0]}:{e.position[1]}",
                    )
                    root = None

                if root is not None:
                    # Overrides are more important than defaults, so put those in front.
                    for override_node in root.iterfind("ct:Override", namespaces):
                        if "PartName" not in override_node.attrib or "ContentType" not in override_node.attrib:
                            warn("[Content_Types].xml malformed: Override node without path or MIME type.")
                            self.safe_report(
                                {"WARNING"},
                                "[Content_Types].xml malformed: Override node without path or MIME type",
                            )
                            continue  # Ignore the broken one.
                        match_regex = re.compile(re.escape(override_node.attrib["PartName"]))
                        result.append((match_regex, override_node.attrib["ContentType"]))

                    for default_node in root.iterfind("ct:Default", namespaces):
                        if "Extension" not in default_node.attrib or "ContentType" not in default_node.attrib:
                            warn("[Content_Types].xml malformed: Default node without extension or MIME type.")
                            self.safe_report(
                                {"WARNING"},
                                "[Content_Types].xml malformed: Default node without extension or MIME type",
                            )
                            continue  # Ignore the broken one.
                        match_regex = re.compile(rf".*\.{re.escape(default_node.attrib['Extension'])}")
                        result.append((match_regex, default_node.attrib["ContentType"]))
        except KeyError:  # ZipFile reports that the content types file doesn't exist.
            warn(f"{CONTENT_TYPES_LOCATION} file missing!")
            self.safe_report({"WARNING"}, f"{CONTENT_TYPES_LOCATION} file missing")

        # This parser should be robust to slightly broken files and retrieve what we can.
        # In case the document is broken or missing, here we'll append the default ones for 3MF.
        # If the content types file was fine, this gets least priority so the actual data still wins.
        result.append((re.compile(r".*\.rels"), RELS_MIMETYPE))
        result.append((re.compile(r".*\.model"), MODEL_MIMETYPE))

        return result

    def assign_content_types(
        self, archive: zipfile.ZipFile, content_types: List[Tuple[Pattern[str], str]]
    ) -> Dict[str, str]:
        """
        Assign a MIME type to each file in the archive.

        The MIME types are obtained through the content types file from the archive. This content types file itself is
        not in the result though.
        :param archive: A 3MF archive with files to assign content types to.
        :param content_types: The content types for files in that archive, in order of priority.
        :return: A dictionary mapping all file paths in the archive to a content types. If the content type for a file
        is unknown, the content type will be an empty string.
        """
        result = {}
        for file_info in archive.filelist:
            file_path = file_info.filename
            if file_path == CONTENT_TYPES_LOCATION:  # Don't index this one.
                continue
            for pattern, content_type in content_types:  # Process in the correct order!
                if pattern.fullmatch(file_path):
                    result[file_path] = content_type
                    break
            else:  # None of the patterns matched.
                result[file_path] = ""

        return result

    def must_preserve(
        self,
        files_by_content_type: Dict[str, List[IO[bytes]]],
        annotations: Annotations,
    ) -> None:
        """
        Preserves files that are marked with the 'MustPreserve' relationship and PrintTickets.

        These files are saved in the Blender context as text files in a hidden folder. If the preserved files are in
        conflict with previously loaded 3MF archives (same file path, different content) then they will not be
        preserved.

        Archived files are stored in Base85 encoding to allow storing arbitrary files, even binary files. This sadly
        means that the file size will increase by about 25%, and that the files are not human-readable any more when
        opened in Blender, even if they were originally human-readable.
        :param files_by_content_type: The files in this 3MF archive, by content type. They must be provided by content
        type because that is how the ``read_archive`` function stores them, which is not ideal. But this function will
        sort that out.
        :param annotations: Collection of annotations gathered so far.
        """
        preserved_files = set()  # Find all files which must be preserved according to the annotations.
        for target, its_annotations in annotations.annotations.items():
            for annotation in its_annotations:
                if type(annotation) is Relationship:
                    if annotation.namespace in {
                        "http://schemas.openxmlformats.org/package/2006/relationships/mustpreserve",
                        "http://schemas.microsoft.com/3dmanufacturing/2013/01/printticket",
                    }:
                        preserved_files.add(target)
                elif type(annotation) is ContentType:
                    if annotation.mime_type == "application/vnd.ms-printing.printticket+xml":
                        preserved_files.add(target)

        for files in files_by_content_type.values():
            for file in files:
                # Cache file name to protect Unicode characters from garbage collection
                file_name = str(file.name)
                if file_name in preserved_files:
                    filename = f".3mf_preserved/{file_name}"
                    if filename in bpy.data.texts:
                        if bpy.data.texts[filename].as_string() == conflicting_mustpreserve_contents:
                            # This file was previously already in conflict. The new file will always be in conflict with
                            # one of the previous files.
                            continue
                    # Encode as Base85 so that the file can be saved in Blender's Text objects.
                    file_contents = base64.b85encode(file.read()).decode("UTF-8")
                    if filename in bpy.data.texts:
                        if bpy.data.texts[filename].as_string() == file_contents:
                            # File contents are EXACTLY the same, so the file is not in conflict.
                            continue  # But we also don't need to re-add the same file then.
                        else:  # Same file exists with different contents, so they are in conflict.
                            bpy.data.texts[filename].clear()
                            bpy.data.texts[filename].write(conflicting_mustpreserve_contents)
                            continue
                    else:  # File doesn't exist yet.
                        handle = bpy.data.texts.new(filename)
                        handle.write(file_contents)

    def _store_passthrough_materials(self) -> None:
        """
        Store passthrough material data in the scene for round-trip export.

        Delegates to import_materials.passthrough module.
        """
        _store_passthrough_impl(self)

    def resolve_extension_prefixes(self, root: xml.etree.ElementTree.Element, prefixes: str) -> Set[str]:
        """
        Resolve extension prefixes to their full namespace URIs.

        Per the 3MF spec, the `requiredextensions` attribute contains space-separated
        prefixes (like "p" for Production Extension), not full namespace URIs.
        This function maps those prefixes to the actual namespace URIs using the
        xmlns declarations on the root element.

        :param root: The XML root element containing namespace declarations.
        :param prefixes: Space-separated extension prefixes.
        :return: Set of full namespace URIs.
        """
        if not prefixes:
            return set()

        # Get namespace map from root element
        # Note: We need to iterate through attribs to find xmlns:prefix declarations
        # because ElementTree doesn't expose nsmap directly
        prefix_to_ns = {}
        for attr_name, attr_value in root.attrib.items():
            if attr_name.startswith("{"):
                # This is a namespaced attribute like {http://...}name
                continue
            if attr_name.startswith("xmlns:"):
                prefix = attr_name[6:]  # Remove "xmlns:" prefix
                prefix_to_ns[prefix] = attr_value

        # Also handle when the file was parsed with namespace awareness
        # In that case, we won't see xmlns: attributes, so use known mappings
        known_prefix_mappings = {
            "p": PRODUCTION_NAMESPACE,
            "m": "http://schemas.microsoft.com/3dmanufacturing/material/2015/02",
            "slic3rpe": "http://schemas.slic3r.org/3mf/2017/06",
        }
        prefix_to_ns.update({k: v for k, v in known_prefix_mappings.items() if k not in prefix_to_ns})

        # Resolve prefixes to namespace URIs
        resolved = set()
        for prefix in prefixes.split():
            prefix = prefix.strip()
            if not prefix:
                continue
            if prefix in prefix_to_ns:
                resolved.add(prefix_to_ns[prefix])
            else:
                # Unknown prefix - keep as-is for warning purposes
                resolved.add(prefix)
                debug(f"Unknown extension prefix: {prefix}")

        return resolved

    def is_supported(
        self,
        required_extensions: str,
        root: Optional[xml.etree.ElementTree.Element] = None,
    ) -> bool:
        """
        Determines if a document is supported by this add-on.
        :param required_extensions: The value of the `requiredextensions` attribute of the root node of the XML
        document.
        :param root: Optional root element to resolve prefixes to namespace URIs.
        :return: `True` if the document is supported, or `False` if it's not.
        """
        if root is not None:
            extensions = self.resolve_extension_prefixes(root, required_extensions)
        else:
            # Fallback: treat as prefixes/namespaces directly
            extensions = set(filter(lambda x: x != "", required_extensions.split(" ")))
        return extensions <= SUPPORTED_EXTENSIONS

    def unit_scale(self, context: bpy.types.Context, root: xml.etree.ElementTree.Element) -> float:
        """
        Get the scaling factor we need to use for this document, according to its unit.
        :param context: The Blender context.
        :param root: An ElementTree root element containing the entire 3MF file.
        :return: Floating point value that we need to scale this model by. A small number (<1) means that we need to
        make the coordinates in Blender smaller than the coordinates in the file. A large number (>1) means we need to
        make the coordinates in Blender larger than the coordinates in the file.
        """
        scale = self.global_scale

        blender_unit_to_metre = context.scene.unit_settings.scale_length
        if blender_unit_to_metre == 0:  # Fallback for special cases.
            blender_unit = context.scene.unit_settings.length_unit
            blender_unit_to_metre = blender_to_metre[blender_unit]

        threemf_unit = root.attrib.get("unit", MODEL_DEFAULT_UNIT)
        threemf_unit_to_metre = threemf_to_metre[threemf_unit]

        # Scale from 3MF units to Blender scene units
        scale *= threemf_unit_to_metre / blender_unit_to_metre
        return scale

    def read_metadata(
        self,
        node: xml.etree.ElementTree.Element,
        original_metadata: Optional[Metadata] = None,
    ) -> Metadata:
        """
        Reads the metadata tags from a metadata group.
        :param node: A node in the 3MF document that contains <metadata> tags. This can be either a root node, or a
        <metadatagroup> node.
        :param original_metadata: If there was already metadata for this context from other documents, you can provide
        that metadata here. The metadata of those documents will be combined then.
        :return: A `Metadata` object.
        """
        if original_metadata is not None:
            metadata = original_metadata
        else:
            metadata = Metadata()  # Create a new Metadata object.

        for metadata_node in node.iterfind("./3mf:metadata", MODEL_NAMESPACES):
            if "name" not in metadata_node.attrib:
                warn("Metadata entry without name is discarded.")
                self.safe_report({"WARNING"}, "Metadata entry without name is discarded")
                continue  # This attribute has no name, so there's no key by which I can save the metadata.
            name = metadata_node.attrib["name"]
            preserve_str = metadata_node.attrib.get("preserve", "0")
            # We don't use this ourselves since we always preserve, but the preserve attribute itself will also be
            # preserved.
            preserve = preserve_str != "0" and preserve_str.lower() != "false"
            datatype = metadata_node.attrib.get("type", "")
            value = metadata_node.text

            # Always store all metadata so that they are preserved.
            metadata[name] = MetadataEntry(name=name, preserve=preserve, datatype=datatype, value=value)

        return metadata

    def read_materials(self, root: xml.etree.ElementTree.Element) -> None:
        """
        Read out all of the material resources from the 3MF document.

        Supports:
        - Core spec <basematerials> (standard 3MF)
        - Materials extension <m:colorgroup> (Orca/BambuStudio vendor format)
        - PBR display properties (metallic, specular, translucent workflows)

        The materials will be stored in `self.resource_materials` until it gets used to build the items.
        :param root: The root of an XML document that may contain materials.
        """
        # Skip all material import if disabled
        if self.import_materials == "NONE":
            debug("Material import disabled, skipping all material data")
            return

        from .constants import MATERIAL_NAMESPACE

        material_ns = {"m": MATERIAL_NAMESPACE}

        # First, parse all PBR display properties into lookup dictionaries
        # These are referenced by basematerials via displaypropertiesid attribute
        pbr_metallic_props = _read_pbr_metallic_impl(self, root, material_ns)
        pbr_specular_props = _read_pbr_specular_impl(self, root, material_ns)
        pbr_translucent_props = _read_pbr_translucent_impl(self, root, material_ns)

        # Parse textured PBR display properties BEFORE basematerials
        # (basematerials lookup textured PBR by displaypropertiesid)
        _read_pbr_texture_display_impl(self, root, material_ns)

        # Merge all display properties by ID
        display_properties = {}
        display_properties.update(pbr_metallic_props)
        display_properties.update(pbr_specular_props)
        display_properties.update(pbr_translucent_props)

        if display_properties:
            debug(f"Parsed {len(display_properties)} PBR display property groups")

        # Import basematerials and colorgroups (delegates to import_materials.base module)
        _read_materials_impl(self, root, material_ns, display_properties)

        # Import Materials extension texture2d resources
        # These define the texture images and their properties
        _read_textures_impl(self, root, material_ns)

        # Import Materials extension texture2dgroup resources
        # These define UV coordinate sets that reference textures
        _read_texture_groups_impl(self, root, material_ns, display_properties)

        # Import passthrough material types for round-trip support
        # These are stored and re-exported without visual interpretation in Blender
        _read_composite_impl(self, root, material_ns)
        _read_multiproperties_impl(self, root, material_ns)
        # Note: _read_pbr_texture_display_properties is called earlier (before basematerials)
        # so basematerials can look up textured PBR by displaypropertiesid

    def _read_textures(self, root: xml.etree.ElementTree.Element, material_ns: Dict[str, str]) -> None:
        """
        Parse <m:texture2d> elements from the 3MF document.

        Delegates to import_materials.textures module.

        :param root: XML root element
        :param material_ns: Namespace dict for materials extension
        """
        _read_textures_impl(self, root, material_ns)

    def _read_texture_groups(
        self,
        root: xml.etree.ElementTree.Element,
        material_ns: Dict[str, str],
        display_properties: Dict[str, List[Dict]],
    ) -> None:
        """
        Parse <m:texture2dgroup> elements from the 3MF document.

        Delegates to import_materials.textures module.

        :param root: XML root element
        :param material_ns: Namespace dict for materials extension
        :param display_properties: Parsed PBR display properties lookup
        """
        _read_texture_groups_impl(self, root, material_ns, display_properties)

    def _read_composite_materials(self, root: xml.etree.ElementTree.Element, material_ns: Dict[str, str]) -> None:
        """
        Parse <m:compositematerials> elements for round-trip support.

        Delegates to import_materials.passthrough module.

        :param root: XML root element
        :param material_ns: Namespace dict for materials extension
        """
        _read_composite_impl(self, root, material_ns)

    def _read_multiproperties(self, root: xml.etree.ElementTree.Element, material_ns: Dict[str, str]) -> None:
        """
        Parse <m:multiproperties> elements for round-trip support.

        Delegates to import_materials.passthrough module.

        :param root: XML root element
        :param material_ns: Namespace dict for materials extension
        """
        _read_multiproperties_impl(self, root, material_ns)

    def _read_pbr_texture_display_properties(
        self, root: xml.etree.ElementTree.Element, material_ns: Dict[str, str]
    ) -> None:
        """
        Parse textured PBR display properties for round-trip support.

        Delegates to import_materials.pbr module.

        :param root: XML root element
        :param material_ns: Namespace dict for materials extension
        """
        _read_pbr_texture_display_impl(self, root, material_ns)

    def _extract_textures_from_archive(self, archive_path: str) -> None:
        """
        Extract texture images from the 3MF archive and create Blender images.

        Delegates to import_materials.textures module.

        :param archive_path: Path to the 3MF archive file.
        """
        _extract_textures_impl(self, archive_path)

    def _read_pbr_metallic_properties(
        self, root: xml.etree.ElementTree.Element, material_ns: Dict[str, str]
    ) -> Dict[str, List[Dict]]:
        """
        Parse <m:pbmetallicdisplayproperties> elements from the 3MF document.

        Delegates to import_materials.pbr module.

        :param root: XML root element
        :param material_ns: Namespace dict for materials extension
        :return: Dict mapping displaypropertiesid -> list of property dicts (one per material index)
        """
        return _read_pbr_metallic_impl(self, root, material_ns)

    def _read_pbr_specular_properties(
        self, root: xml.etree.ElementTree.Element, material_ns: Dict[str, str]
    ) -> Dict[str, List[Dict]]:
        """
        Parse <m:pbspeculardisplayproperties> elements from the 3MF document.

        Delegates to import_materials.pbr module.

        :param root: XML root element
        :param material_ns: Namespace dict for materials extension
        :return: Dict mapping displaypropertiesid -> list of property dicts
        """
        return _read_pbr_specular_impl(self, root, material_ns)

    def _read_pbr_translucent_properties(
        self, root: xml.etree.ElementTree.Element, material_ns: Dict[str, str]
    ) -> Dict[str, List[Dict]]:
        """
        Parse <m:translucentdisplayproperties> elements from the 3MF document.

        Delegates to import_materials.pbr module.

        :param root: XML root element
        :param material_ns: Namespace dict for materials extension
        :return: Dict mapping displaypropertiesid -> list of property dicts
        """
        return _read_pbr_translucent_impl(self, root, material_ns)

    def read_objects(self, root: xml.etree.ElementTree.Element) -> None:
        """
        Reads all repeatable build objects from the resources of an XML root node.

        This stores them in the resource_objects field.
        :param root: The root node of a 3dmodel.model XML file.
        """
        for object_node in root.iterfind("./3mf:resources/3mf:object", MODEL_NAMESPACES):
            try:
                objectid = object_node.attrib["id"]
            except KeyError:
                warn("Object resource without ID!")
                self.safe_report({"WARNING"}, "Object resource without ID")
                continue  # ID is required, otherwise the build can't refer to it.

            pid = object_node.attrib.get("pid")  # Material ID.
            pindex = object_node.attrib.get("pindex")  # Index within a collection of materials.
            material = None
            if pid is not None and pindex is not None:
                # Check if pid references multiproperties (passthrough for round-trip export)
                if pid in self.resource_multiproperties:
                    self.object_passthrough_pids[objectid] = pid
                    debug(f"Object {objectid} references multiproperties pid={pid}")
                    # Don't try to resolve as basematerial — multiproperties are
                    # resolved per-triangle in read_triangles via _resolve_multiproperties_material
                else:
                    try:
                        index = int(pindex)
                        material = self.resource_materials[pid][index]
                    except KeyError:
                        # Only warn if materials were supposed to be imported
                        if self.import_materials != "NONE":
                            warn(
                                f"Object with ID {objectid} refers to material collection {pid} with index {pindex}"
                                f" which doesn't exist."
                            )
                            self.safe_report(
                                {"WARNING"},
                                f"Object with ID {objectid} refers to material collection {pid} "
                                f"with index {pindex} which doesn't exist",
                            )
                        else:
                            debug(
                                f"Object with ID {objectid} refers to material {pid}:{pindex} "
                                f"(skipped due to import_materials=False)"
                            )
                    except ValueError:
                        warn(f"Object with ID {objectid} specifies material index {pindex}, which is not integer.")
                        self.safe_report(
                            {"WARNING"},
                            f"Object with ID {objectid} specifies material index {pindex}, which is not integer",
                        )

            vertices = self.read_vertices(object_node)
            # Pass vertex coordinates to allow PrusaSlicer segmentation subdivision
            # Also pass objectid for looking up default extruder
            (
                triangles,
                materials,
                triangle_uvs,
                vertices,
                segmentation_strings,
                default_extruder,
            ) = self.read_triangles(object_node, material, pid, vertices, objectid)

            # Also detect multiproperties references at the TRIANGLE level
            # (not just object level) for round-trip passthrough export.
            # The consortium test files reference multiproperties per-triangle, not per-object.
            if objectid not in self.object_passthrough_pids:
                for tri_node in object_node.iterfind(
                    "./3mf:mesh/3mf:triangles/3mf:triangle", MODEL_NAMESPACES
                ):
                    tri_pid = tri_node.attrib.get("pid")
                    if tri_pid and tri_pid in self.resource_multiproperties:
                        self.object_passthrough_pids[objectid] = tri_pid
                        debug(f"Object {objectid} has triangle-level multiproperties pid={tri_pid}")
                        break

            components = self.read_components(object_node)

            # Check if components have p:path references (Production Extension)
            # If so, load the external model files
            for component in components:
                if component.path:
                    self.load_external_model(component.path)

            metadata = Metadata()
            for metadata_node in object_node.iterfind("./3mf:metadatagroup", MODEL_NAMESPACES):
                metadata = self.read_metadata(metadata_node, metadata)
            if "partnumber" in object_node.attrib:
                # Blender has no way to ensure that custom properties get preserved if a mesh is split up, but for most
                # operations this is retained properly.
                metadata["3mf:partnumber"] = MetadataEntry(
                    name="3mf:partnumber",
                    preserve=True,
                    datatype="xs:string",
                    value=object_node.attrib["partnumber"],
                )
            if "name" in object_node.attrib and "Title" not in metadata:
                # Cache object name from XML to protect Unicode characters from garbage collection
                object_name = str(object_node.attrib.get("name"))
                metadata["Title"] = MetadataEntry(name="Title", preserve=True, datatype="xs:string", value=object_name)

            metadata["3mf:object_type"] = MetadataEntry(
                name="3mf:object_type",
                preserve=True,
                datatype="xs:string",
                value=object_node.attrib.get("type", "model"),
            )

            # Read triangle sets if present
            triangle_sets = self.read_triangle_sets(object_node)

            # Check if any triangles have UV data (textured)
            has_uvs = any(uv is not None for uv in triangle_uvs) if triangle_uvs else False

            self.resource_objects[objectid] = ResourceObject(
                vertices=vertices,
                triangles=triangles,
                materials=materials,
                components=components,
                metadata=metadata,
                triangle_sets=triangle_sets,
                triangle_uvs=triangle_uvs if has_uvs else None,
                segmentation_strings=segmentation_strings if segmentation_strings else None,
                default_extruder=default_extruder,
            )

    def read_vertices(self, object_node: xml.etree.ElementTree.Element) -> List[Tuple[float, float, float]]:
        """
        Reads out the vertices from an XML node of an object.

        If any vertex is corrupt, like with a coordinate missing or not proper floats, then the 0 coordinate will be
        used. This is to prevent messing up the list of indices.
        :param object_node: An <object> element from the 3dmodel.model file.
        :return: List of vertices in that object. Each vertex is a tuple of 3 floats for X, Y and Z.
        """
        result = []
        for vertex in object_node.iterfind("./3mf:mesh/3mf:vertices/3mf:vertex", MODEL_NAMESPACES):
            attrib = vertex.attrib
            try:
                x = float(attrib.get("x", 0))
            except ValueError:  # Not a float.
                warn("Vertex missing X coordinate.")
                self.safe_report({"WARNING"}, "Vertex missing X coordinate")
                x = 0
            try:
                y = float(attrib.get("y", 0))
            except ValueError:
                warn("Vertex missing Y coordinate.")
                self.safe_report({"WARNING"}, "Vertex missing Y coordinate")
                y = 0
            try:
                z = float(attrib.get("z", 0))
            except ValueError:
                warn("Vertex missing Z coordinate.")
                self.safe_report({"WARNING"}, "Vertex missing Z coordinate")
                z = 0
            result.append((x, y, z))
        return result

    def read_triangles(
        self,
        object_node: xml.etree.ElementTree.Element,
        default_material: Optional[int],
        material_pid: Optional[int],
        vertex_coords: Optional[List[Tuple[float, float, float]]] = None,
        object_id: Optional[str] = None,
    ) -> Tuple[
        List[Tuple[int, int, int]],
        List[Optional[int]],
        List[Optional[Tuple]],
        List[Tuple[float, float, float]],
        Dict[int, str],
        int,
    ]:
        """
        Reads out the triangles from an XML node of an object.

        These triangles always consist of 3 vertices each. Each vertex is an index to the list of vertices read
        previously. The triangle also contains an associated material, or None if the triangle gets no material.

        For textured triangles (pid references a texture2dgroup), UV coordinates are extracted from p1, p2, p3.

        For PrusaSlicer/Orca segmentation:
        - If import_materials == 'PAINT': Store hash strings for UV texture rendering
        - Otherwise: Subdivide geometry (legacy method)

        :param object_node: An <object> element from the 3dmodel.model file.
        :param default_material: If the triangle specifies no material, it should get this material. May be `None` if
        the model specifies no material.
        :param material_pid: Triangles that specify a material index will get their material from this material group.
        :param vertex_coords: Optional list of vertex coordinates for PrusaSlicer subdivision support.
        :param object_id: Object ID for looking up default extruder from PrusaSlicer metadata.
        :return: Six values:
            - vertices: 3-tuples of vertex indices
            - materials: material for each triangle (or None)
            - uvs: UV coordinates per triangle ((u1,v1), (u2,v2), (u3,v3)) or None if not textured
            - vertex_coords: Possibly expanded vertex coordinate list
            - segmentation_strings: Dict mapping face_index -> hash string
            - default_extruder: Default extruder index for this object
        """
        vertices = []
        materials = []
        triangle_uvs = []  # List of ((u1,v1), (u2,v2), (u3,v3)) or None per triangle
        segmentation_strings = {}  # Dict mapping face_index -> hash string for UV texture rendering

        # Make a mutable copy of vertex coordinates (or empty list if not provided)
        vertex_list = list(vertex_coords) if vertex_coords else []

        # Track state->material mapping for PrusaSlicer segmentation
        state_materials = {}

        # Get object's default extruder (1-based), default to 1 if not specified
        default_extruder = 1
        if object_id and hasattr(self, "object_default_extruders"):
            default_extruder = self.object_default_extruders.get(object_id, 1)

        # Threshold to distinguish simple Orca codes from PrusaSlicer segmentation
        PRUSA_SEGMENTATION_THRESHOLD = 10

        for tri_index, triangle in enumerate(
            object_node.iterfind("./3mf:mesh/3mf:triangles/3mf:triangle", MODEL_NAMESPACES)
        ):
            attrib = triangle.attrib
            try:
                v1 = int(attrib["v1"])
                v2 = int(attrib["v2"])
                v3 = int(attrib["v3"])
                if v1 < 0 or v2 < 0 or v3 < 0:  # Negative indices are not allowed.
                    warn("Triangle containing negative index to vertex list.")
                    self.safe_report({"WARNING"}, "Triangle containing negative index to vertex list")
                    continue

                pid = attrib.get("pid", material_pid)
                p1 = attrib.get("p1")
                p2 = attrib.get("p2")
                p3 = attrib.get("p3")
                material = None
                uvs = None  # Will be set if this is a textured triangle

                if self.import_materials != "NONE":
                    # Check for multi-material paint attributes first
                    # PrusaSlicer uses slic3rpe:mmu_segmentation, Orca uses paint_color
                    paint_code = attrib.get("paint_color")
                    if not paint_code:
                        # ElementTree returns namespaced attrs as {namespace}localname
                        paint_code = attrib.get(f"{{{SLIC3RPE_NAMESPACE}}}mmu_segmentation")
                    if not paint_code:
                        # Also check prefixed form (some parsers)
                        paint_code = attrib.get("slic3rpe:mmu_segmentation")

                    # Handle paint_code attribute (segmentation or Orca-style markers)
                    if paint_code and self.import_materials == "PAINT" and vertex_list:
                        # PAINT mode: All paint codes are segmentation strings for UV texture
                        current_face_index = len(vertices)
                        segmentation_strings[current_face_index] = paint_code
                        vertices.append((v1, v2, v3))
                        triangle_uvs.append(None)
                        materials.append(material or default_material)
                        continue  # Skip normal triangle addition below
                    elif paint_code and self.import_materials == "MATERIALS" and vertex_list:
                        # MATERIALS mode: Try different strategies based on length
                        if len(paint_code) >= PRUSA_SEGMENTATION_THRESHOLD:
                            # Long string (10+ chars): Full segmentation tree - subdivide geometry
                            try:
                                sub_tris, sub_mats = self._subdivide_prusa_segmentation(
                                    v1,
                                    v2,
                                    v3,
                                    paint_code,
                                    vertex_list,
                                    state_materials,
                                    tri_index,
                                    default_extruder,
                                )
                                for tri in sub_tris:
                                    vertices.append(tri)
                                    triangle_uvs.append(None)
                                materials.extend(sub_mats)
                                continue  # Skip normal triangle addition
                            except Exception as e:
                                warn(f"Failed to subdivide long segmentation: {e}")
                                # Fall through to default material
                        else:
                            # Short string (<10 chars): Could be Orca code or short segmentation
                            filament_index = parse_paint_color_to_index(paint_code)
                            if filament_index > 0:
                                # It's a known Orca paint code ("4", "8", "0C", etc.)
                                material = self.get_or_create_paint_material(filament_index, paint_code)
                            else:
                                # Unknown code - try as short segmentation string
                                try:
                                    sub_tris, sub_mats = self._subdivide_prusa_segmentation(
                                        v1,
                                        v2,
                                        v3,
                                        paint_code,
                                        vertex_list,
                                        state_materials,
                                        tri_index,
                                        default_extruder,
                                    )
                                    for tri in sub_tris:
                                        vertices.append(tri)
                                        triangle_uvs.append(None)
                                    materials.extend(sub_mats)
                                    continue
                                except Exception:
                                    debug(f"String '{paint_code}' not valid Orca code or segmentation, using default")
                                    # Fall through to default material
                    elif pid is not None and pid in self.resource_texture_groups:
                        # This is a texture group reference - extract UVs
                        texture_group = self.resource_texture_groups[pid]
                        tex2coords = texture_group.tex2coords

                        # Get UV indices from p1, p2, p3 (default to 0 if not specified per spec)
                        try:
                            idx1 = int(p1) if p1 is not None else 0
                            idx2 = int(p2) if p2 is not None else idx1  # p2 defaults to p1 per spec
                            idx3 = int(p3) if p3 is not None else idx1  # p3 defaults to p1 per spec

                            # Get actual UV coordinates from texture group
                            uv1 = tex2coords[idx1] if idx1 < len(tex2coords) else (0.0, 0.0)
                            uv2 = tex2coords[idx2] if idx2 < len(tex2coords) else (0.0, 0.0)
                            uv3 = tex2coords[idx3] if idx3 < len(tex2coords) else (0.0, 0.0)
                            uvs = (uv1, uv2, uv3)

                            # Create or get material for this texture group
                            material = self._get_or_create_textured_material(pid, texture_group)

                        except (ValueError, IndexError) as e:
                            warn(f"Invalid texture coordinate index: {e}")
                            uvs = None
                    elif pid is not None and pid in self.resource_multiproperties:
                        # Multiproperties reference - resolve to underlying basematerial
                        material, uvs = self._resolve_multiproperties_material(pid, p1, p2, p3, default_material)
                    elif p1 is not None:
                        # Standard 3MF material reference
                        try:
                            material = self.resource_materials[pid][int(p1)]
                        except KeyError as e:
                            warn(f"Material {e} is missing.")
                            self.safe_report({"WARNING"}, f"Material {e} is missing")
                            material = default_material
                        except ValueError as e:
                            warn(f"Material index is not an integer: {e}")
                            self.safe_report({"WARNING"}, f"Material index is not an integer: {e}")
                            material = default_material
                    else:
                        material = default_material
                else:
                    material = default_material

                vertices.append((v1, v2, v3))
                materials.append(material)
                triangle_uvs.append(uvs)
            except KeyError as e:
                warn(f"Vertex {e} is missing.")
                self.safe_report({"WARNING"}, f"Vertex {e} is missing")
                continue
            except ValueError as e:
                warn(f"Vertex reference is not an integer: {e}")
                self.safe_report({"WARNING"}, f"Vertex reference is not an integer: {e}")
                continue  # No fallback this time. Leave out the entire triangle.
        return (
            vertices,
            materials,
            triangle_uvs,
            vertex_list,
            segmentation_strings,
            default_extruder,
        )

    def _get_or_create_textured_material(
        self, texture_group_id: str, texture_group: "ResourceTextureGroup"
    ) -> Optional["ResourceMaterial"]:
        """
        Get or create a ResourceMaterial for a texture group.

        Delegates to import_materials.textures module.

        :param texture_group_id: The ID of the texture2dgroup
        :param texture_group: The ResourceTextureGroup data
        :return: ResourceMaterial for this texture, or None if texture not available
        """
        return _get_or_create_textured_material_impl(self, texture_group_id, texture_group)

    def _resolve_multiproperties_material(
        self,
        multiprop_id: str,
        p1: Optional[str],
        p2: Optional[str],
        p3: Optional[str],
        default_material: Optional[ResourceMaterial],
    ) -> Tuple[Optional[ResourceMaterial], Optional[Tuple]]:
        """
        Resolve a multiproperties reference to its underlying material and UVs.

        Multiproperties combine multiple property groups (basematerials, texture groups, etc.)
        with optional blend modes. For rendering, we extract the primary basematerial and
        any texture UVs.

        Per 3MF Materials Extension spec:
        - pids lists property group IDs in layer order (first = base layer)
        - Each <multi> has pindices listing indices into each layer's property group
        - p1/p2/p3 on triangle reference indices into the multiproperties' multi list

        :param multiprop_id: ID of the multiproperties resource
        :param p1: Property index for vertex 1 (into multi list)
        :param p2: Property index for vertex 2
        :param p3: Property index for vertex 3
        :param default_material: Fallback material if resolution fails
        :return: Tuple of (material, uvs) where uvs may be None
        """
        multiprop = self.resource_multiproperties.get(multiprop_id)
        if not multiprop:
            warn(f"Multiproperties {multiprop_id} not found")
            return default_material, None

        # Get the multi index from p1 (per 3MF spec, p1 is required for multiproperties)
        if p1 is None:
            warn(f"Multiproperties {multiprop_id} requires p1 index")
            return default_material, None

        try:
            multi_index = int(p1)
        except ValueError:
            warn(f"Invalid multi index: {p1}")
            return default_material, None

        # Get the multi element at this index
        if multi_index < 0 or multi_index >= len(multiprop.multis):
            warn(f"Multi index {multi_index} out of range for multiproperties {multiprop_id}")
            return default_material, None

        multi = multiprop.multis[multi_index]
        pindices_str = multi.get("pindices", "")
        pindices = pindices_str.split() if pindices_str else []

        # pids is the list of property group IDs (space-separated string)
        pids_str = multiprop.pids if multiprop.pids else ""
        pids = pids_str.split() if pids_str else []

        # Find the first basematerial reference (for the material)
        # and any texture group references (for UVs and visual representation)
        material = None
        uvs = None
        texture_group_ids = []  # Collect all texture group IDs

        for i, pid in enumerate(pids):
            if i >= len(pindices):
                break

            pindex = int(pindices[i]) if pindices[i] else 0

            # Check if this pid is a basematerial
            if pid in self.resource_materials:
                if material is None:  # Use first basematerial found
                    material_group = self.resource_materials[pid]
                    if pindex in material_group:
                        material = material_group[pindex]
                        debug(
                            f"Multiproperties {multiprop_id}: resolved to material "
                            f"'{material.name}' from basematerials {pid}[{pindex}]"
                        )

            # Check if this pid is a texture group (for UVs)
            elif pid in self.resource_texture_groups:
                texture_group = self.resource_texture_groups[pid]
                tex2coords = texture_group.tex2coords
                texture_group_ids.append(pid)  # Store texture group ID

                # For texture groups in multiproperties, p1/p2/p3 map to UV indices
                # The pindex from pindices is the base, but actual UV varies per vertex
                try:
                    # Get UV indices - for multiproperties, we need per-vertex UV indices
                    # p1, p2, p3 are multi indices, we need to get pindices for each vertex
                    # For simplicity, use the same multi index for all vertices (uniform texture)
                    uv_idx = pindex
                    if uv_idx < len(tex2coords):
                        uv = tex2coords[uv_idx]
                        # All three vertices get same UV (simplified - proper per-vertex requires p2/p3)
                        uvs = (uv, uv, uv)
                except (ValueError, IndexError):
                    pass

        if material is None:
            debug(f"Multiproperties {multiprop_id}: no basematerial found, using default")
            material = default_material
        elif texture_group_ids:
            # If we have both a basematerial and texture groups, create a new ResourceMaterial
            # that includes the texture information so the shader can be set up properly
            debug(f"Multiproperties {multiprop_id}: found {len(texture_group_ids)} texture groups")
            original_basematerial = material  # Keep reference for deduplication
            material = ResourceMaterial(
                name=original_basematerial.name,
                color=original_basematerial.color,
                metallic=original_basematerial.metallic,
                roughness=original_basematerial.roughness,
                specular_color=original_basematerial.specular_color,
                glossiness=original_basematerial.glossiness,
                ior=original_basematerial.ior,
                attenuation=original_basematerial.attenuation,
                transmission=original_basematerial.transmission,
                texture_id=texture_group_ids[0],
                metallic_texid=None,
                roughness_texid=None,
                specular_texid=None,
                glossiness_texid=None,
                basecolor_texid=None,
                extra_texture_ids=tuple(texture_group_ids[1:]) if len(texture_group_ids) > 1 else None,
            )
            # Store the mapping between textured and non-textured versions for deduplication
            self.textured_to_basematerial_map[material] = original_basematerial

        return material, uvs

    def read_triangle_sets(self, object_node: xml.etree.ElementTree.Element) -> Dict[str, List[int]]:
        """
        Reads triangle sets from an XML node of an object.

        Delegates to import_trianglesets module.

        :param object_node: An <object> element from the 3dmodel.model file.
        :return: Dictionary mapping triangle set names to lists of triangle indices.
        """
        return _read_triangle_sets_impl(self, object_node)

    def read_components(self, object_node: xml.etree.ElementTree.Element) -> List[Component]:
        """
        Reads out the components from an XML node of an object.

        These components refer to other resource objects, with a transformation applied. They will eventually appear in
        the scene as sub-objects.

        Supports Production Extension p:path attribute for external model file references.
        :param object_node: An <object> element from the 3dmodel.model file.
        :return: List of components in this object node.
        """
        result = []

        for component_node in object_node.iterfind("./3mf:components/3mf:component", MODEL_NAMESPACES):
            try:
                objectid = component_node.attrib["objectid"]
            except KeyError:  # ID is required.
                continue  # Ignore this invalid component.
            transform = self.parse_transformation(component_node.attrib.get("transform", ""))

            # Check for Production Extension p:path attribute
            # This references an external model file
            path = component_node.attrib.get(f"{{{PRODUCTION_NAMESPACE}}}path")
            if path:
                debug(f"Component references external model: {path}")

            result.append(Component(resource_object=objectid, transformation=transform, path=path))
        return result

    def load_external_model(self, model_path: str) -> None:
        """
        Load an external model file referenced by Production Extension p:path.

        This is used by Orca Slicer/BambuStudio which stores each object in a separate
        model file under 3D/Objects/.

        :param model_path: The path to the model file (e.g., "/3D/Objects/Cube_1.model")
        """
        if not hasattr(self, "current_archive_path") or not self.current_archive_path:
            warn(f"Cannot load external model {model_path}: no archive path set")
            return

        # Normalize path (remove leading slash for archive access)
        archive_path = model_path.lstrip("/")

        try:
            with zipfile.ZipFile(self.current_archive_path, "r") as archive:
                if archive_path not in archive.namelist():
                    warn(f"External model file not found in archive: {archive_path}")
                    return

                with archive.open(archive_path) as model_file:
                    try:
                        document = xml.etree.ElementTree.parse(model_file)
                    except xml.etree.ElementTree.ParseError as e:
                        error(f"External model {archive_path} is malformed: {e}")
                        self.safe_report({"ERROR"}, f"External model {archive_path} is malformed")
                        return

                    root = document.getroot()

                    # Read objects from this external model file
                    self.read_external_model_objects(root, model_path)

                    debug(f"Loaded external model: {archive_path}")

        except (zipfile.BadZipFile, IOError) as e:
            error(f"Failed to read external model {archive_path}: {e}")
            self.safe_report({"ERROR"}, f"Failed to read external model: {e}")

    def read_external_model_objects(self, root: xml.etree.ElementTree.Element, source_path: str) -> None:
        """
        Read objects from an external model file (Production Extension).

        This handles the paint_color attribute used by Orca Slicer for per-triangle colors.

        :param root: The root element of the external model XML file.
        :param source_path: The path of the source file (for logging).
        """
        for object_node in root.iterfind("./3mf:resources/3mf:object", MODEL_NAMESPACES):
            try:
                objectid = object_node.attrib["id"]
            except KeyError:
                warn(f"Object in {source_path} without ID!")
                continue

            # Skip if we already have this object (don't overwrite)
            if objectid in self.resource_objects:
                debug(f"Object {objectid} already loaded, skipping duplicate from {source_path}")
                continue

            vertices = self.read_vertices(object_node)
            # Pass vertices to allow PrusaSlicer segmentation subdivision to expand them
            triangles, materials, vertices, segmentation_strings, default_extruder = (
                self.read_triangles_with_paint_color(object_node, vertices, objectid)
            )
            components = self.read_components(object_node)

            metadata = Metadata()
            for metadata_node in object_node.iterfind("./3mf:metadatagroup", MODEL_NAMESPACES):
                metadata = self.read_metadata(metadata_node, metadata)

            if "name" in object_node.attrib and "Title" not in metadata:
                object_name = str(object_node.attrib.get("name"))
                metadata["Title"] = MetadataEntry(name="Title", preserve=True, datatype="xs:string", value=object_name)

            metadata["3mf:object_type"] = MetadataEntry(
                name="3mf:object_type",
                preserve=True,
                datatype="xs:string",
                value=object_node.attrib.get("type", "model"),
            )

            # Read triangle sets if present
            triangle_sets = self.read_triangle_sets(object_node)

            self.resource_objects[objectid] = ResourceObject(
                vertices=vertices,
                triangles=triangles,
                materials=materials,
                components=components,
                metadata=metadata,
                triangle_sets=triangle_sets,
                triangle_uvs=None,  # Orca objects don't have textured UVs
                segmentation_strings=segmentation_strings if segmentation_strings else None,
                default_extruder=default_extruder,
            )
            debug(
                f"Loaded object {objectid} from {source_path} with {len(vertices)} vertices, {len(triangles)} triangles"
            )

    def read_triangles_with_paint_color(
        self,
        object_node: xml.etree.ElementTree.Element,
        vertices: Optional[List[Tuple[float, float, float]]] = None,
        object_id: Optional[str] = None,
    ) -> Tuple[
        List[Tuple[int, int, int]],
        List[Optional[ResourceMaterial]],
        List[Tuple[float, float, float]],
        Dict[int, str],
        int,
    ]:
        """
        Read triangles from an object node, handling paint_color attributes.

        Supports both Orca Slicer format (simple paint codes like "4", "8") and
        PrusaSlicer format (hierarchical segmentation strings that get subdivided).

        :param object_node: An <object> element from a model file.
        :param vertices: Optional list of vertex coordinates for PrusaSlicer subdivision.
                        If None, vertices are read from object_node.
        :param object_id: Object ID for looking up default extruder from PrusaSlicer metadata.
        :return: Tuple of (triangle list, material list, vertex list, segmentation_strings dict, default_extruder).
                 Vertex list may be expanded if PrusaSlicer segmentation is present.
        """
        triangles = []
        materials = []
        segmentation_strings = {}  # For UV texture rendering in PAINT mode

        # Read vertices if not provided
        if vertices is None:
            vertices = self.read_vertices(object_node)

        # Make a mutable copy of vertices that we can extend
        vertex_list = list(vertices)

        # Track paint_color to material mapping for this object
        paint_color_materials = {}

        # Track state->material mapping for PrusaSlicer segmentation
        state_materials = {}

        # Get object's default extruder (1-based), default to 1 if not specified
        default_extruder = 1
        if object_id and hasattr(self, "object_default_extruders"):
            default_extruder = self.object_default_extruders.get(object_id, 1)

        # Threshold to distinguish simple Orca codes from PrusaSlicer segmentation
        # Simple codes are short: "", "4", "8", "0C", "1C" etc. (≤ 3 chars)
        # PrusaSlicer segmentation strings are long (hundreds of chars)
        PRUSA_SEGMENTATION_THRESHOLD = 10

        for tri_index, triangle in enumerate(
            object_node.iterfind("./3mf:mesh/3mf:triangles/3mf:triangle", MODEL_NAMESPACES)
        ):
            attrib = triangle.attrib
            try:
                v1 = int(attrib["v1"])
                v2 = int(attrib["v2"])
                v3 = int(attrib["v3"])
                if v1 < 0 or v2 < 0 or v3 < 0:
                    warn("Triangle with negative vertex index.")
                    continue

                # Handle multi-material attributes (Orca/PrusaSlicer)
                material = None
                if self.import_materials != "NONE":
                    # Try paint_color first (Orca), then mmu_segmentation (PrusaSlicer)
                    paint_code = attrib.get("paint_color")
                    if not paint_code:
                        # Check for PrusaSlicer's attribute with namespace
                        paint_code = attrib.get(f"{{{SLIC3RPE_NAMESPACE}}}mmu_segmentation")
                        if not paint_code:
                            # Also try without namespace (some files have it as plain attribute)
                            paint_code = attrib.get("slic3rpe:mmu_segmentation")

                    # Handle paint_code attribute (segmentation or Orca-style markers)
                    if paint_code and self.import_materials == "PAINT":
                        # PAINT mode: All paint codes are segmentation strings for UV texture
                        current_face_index = len(triangles)
                        segmentation_strings[current_face_index] = paint_code
                        triangles.append((v1, v2, v3))
                        materials.append(material)
                        continue  # Skip normal triangle addition below
                    elif paint_code and self.import_materials == "MATERIALS":
                        # MATERIALS mode: Try different strategies based on length
                        if len(paint_code) >= PRUSA_SEGMENTATION_THRESHOLD:
                            # Long string (10+ chars): Full segmentation tree - subdivide geometry
                            try:
                                sub_tris, sub_mats = self._subdivide_prusa_segmentation(
                                    v1,
                                    v2,
                                    v3,
                                    paint_code,
                                    vertex_list,
                                    state_materials,
                                    tri_index,
                                    default_extruder,
                                )
                                triangles.extend(sub_tris)
                                materials.extend(sub_mats)
                                continue  # Skip normal triangle addition
                            except Exception as e:
                                warn(f"Failed to subdivide long segmentation: {e}")
                                # Fall through to treat as unpainted triangle
                        else:
                            # Short string (<10 chars): Could be Orca code or short segmentation
                            if paint_code not in paint_color_materials:
                                filament_index = parse_paint_color_to_index(paint_code)
                                if filament_index > 0:
                                    # It's a known Orca paint code ("4", "8", "0C", etc.)
                                    material = self.get_or_create_paint_material(filament_index, paint_code)
                                    paint_color_materials[paint_code] = material
                                    debug(f"Multi-material code '{paint_code}' -> filament {filament_index}")
                                else:
                                    # Unknown code - try as short segmentation string
                                    try:
                                        sub_tris, sub_mats = self._subdivide_prusa_segmentation(
                                            v1,
                                            v2,
                                            v3,
                                            paint_code,
                                            vertex_list,
                                            state_materials,
                                            tri_index,
                                            default_extruder,
                                        )
                                        triangles.extend(sub_tris)
                                        materials.extend(sub_mats)
                                        continue
                                    except Exception:
                                        debug(
                                            f"String '{paint_code}' not valid Orca code or segmentation, using default"
                                        )
                                        # Fall through to default material
                            else:
                                material = paint_color_materials[paint_code]

                triangles.append((v1, v2, v3))
                materials.append(material)

            except KeyError as e:
                warn(f"Triangle missing vertex: {e}")
                continue
            except ValueError as e:
                warn(f"Invalid vertex reference: {e}")
                continue

        return triangles, materials, vertex_list, segmentation_strings, default_extruder

    def _subdivide_prusa_segmentation(
        self,
        v1: int,
        v2: int,
        v3: int,
        segmentation_string: str,
        vertex_list: List[Tuple[float, float, float]],
        state_materials: Dict[int, ResourceMaterial],
        source_triangle_index: int,
        default_extruder: int = 1,
    ) -> Tuple[List[Tuple[int, int, int]], List[Optional[ResourceMaterial]]]:
        """
        Subdivide a triangle according to PrusaSlicer segmentation.

        :param v1, v2, v3: Vertex indices of the original triangle
        :param segmentation_string: The slic3rpe:mmu_segmentation hex string
        :param vertex_list: Vertex coordinate list (will be extended with new vertices)
        :param state_materials: Dict mapping state -> material (will be extended)
        :param source_triangle_index: Index of source triangle (for tracking)
        :param default_extruder: Object's default extruder (1-based) for state 0
        :return: Tuple of (sub-triangles, sub-materials)
        """
        # Decode the segmentation tree
        tree = decode_segmentation_string(segmentation_string)
        if tree is None:
            # Failed to decode - return original triangle without material
            return [(v1, v2, v3)], [None]

        # Get vertex coordinates
        p1 = vertex_list[v1]
        p2 = vertex_list[v2]
        p3 = vertex_list[v3]

        # Subdivide using the tree
        subdivider = TriangleSubdivider()
        new_verts, sub_tris = subdivider.subdivide(p1, p2, p3, tree, source_triangle_index)

        # Add new vertices (indices 3+ in new_verts are new midpoints)
        base_vertex_idx = len(vertex_list)
        for i in range(3, len(new_verts)):
            vertex_list.append(new_verts[i])

        # Remap triangle vertex indices to global vertex list
        def remap_idx(local_idx: int) -> int:
            if local_idx == 0:
                return v1
            elif local_idx == 1:
                return v2
            elif local_idx == 2:
                return v3
            else:
                return base_vertex_idx + (local_idx - 3)

        result_triangles = []
        result_materials = []

        for tri in sub_tris:
            # Remap indices
            result_triangles.append((remap_idx(tri.v0), remap_idx(tri.v1), remap_idx(tri.v2)))

            # Get or create material for this state
            # State 0 = object's default extruder, State N = Extruder N directly
            state = int(tri.state)
            if state == 0:
                extruder_num = default_extruder  # Use object's assigned extruder
            else:
                extruder_num = state  # State value IS the extruder number (1-based)

            if state not in state_materials:
                # Create material for this extruder state
                material = self.get_or_create_paint_material(extruder_num, f"prusa_extruder_{extruder_num}")
                state_materials[state] = material
            result_materials.append(state_materials[state])

        debug(
            f"Subdivided triangle {source_triangle_index}: "
            f"{len(new_verts) - 3} new vertices, {len(result_triangles)} sub-triangles"
        )

        return result_triangles, result_materials

    def get_or_create_paint_material(self, filament_index: int, paint_code: str) -> ResourceMaterial:
        """
        Get or create a material for an Orca Slicer paint_color.

        Uses actual colors from project_settings.config if available, otherwise generates colors.

        :param filament_index: The filament index (1-based from paint_color codes: "4"=1, "8"=2, etc.).
        :param paint_code: The original paint code string.
        :return: A ResourceMaterial for this paint color.
        """
        # Generate a unique material ID for paint colors
        material_id = f"paint_{filament_index}_{paint_code}"

        if material_id not in self.resource_materials:
            # Try to get actual color from orca_filament_colors
            # filament_index is 1-based (from paint codes), but filament_colour array is 0-indexed
            # So filament 1 ("4") -> filament_colour[0], filament 2 ("8") -> filament_colour[1]
            color = None
            color_name = f"Filament {filament_index}"
            array_index = filament_index - 1  # Convert 1-based to 0-based

            if hasattr(self, "orca_filament_colors") and array_index >= 0 and array_index in self.orca_filament_colors:
                hex_color = self.orca_filament_colors[array_index]
                color = self.parse_hex_color(hex_color)
                color_name = f"Color {hex_color}"
                debug(f"Using Orca filament color {filament_index} (array index {array_index}): {hex_color}")

            if color is None:
                # Fallback: generate a color based on filament index
                import colorsys

                hue = (filament_index * 0.618033988749895) % 1.0  # Golden ratio for good distribution
                r, g, b = colorsys.hsv_to_rgb(hue, 0.8, 0.9)
                color = (r, g, b, 1.0)
                debug(f"Generated fallback color for filament {filament_index}")

            self.resource_materials[material_id] = {
                0: ResourceMaterial(
                    name=color_name,
                    color=color,
                    metallic=None,
                    roughness=None,
                    specular_color=None,
                    glossiness=None,
                    ior=None,
                    attenuation=None,
                    transmission=None,
                    metallic_texid=None,
                    roughness_texid=None,
                    specular_texid=None,
                    glossiness_texid=None,
                )
            }
            debug(f"Created paint material for filament {filament_index} (code: {paint_code})")

        return self.resource_materials[material_id][0]

    def read_orca_filament_colors(self, archive_path: str) -> None:
        """
        Read filament colors from Orca Slicer's project_settings.config.

        This file contains the filament_colour array with hex colors for each filament.

        :param archive_path: Path to the 3MF archive file.
        """
        if self.import_materials == "NONE":
            return

        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                config_path = "Metadata/project_settings.config"
                if config_path not in archive.namelist():
                    debug(f"No {config_path} in archive, skipping Orca color import")
                    return

                with archive.open(config_path) as config_file:
                    try:
                        config = json.load(config_file)
                    except json.JSONDecodeError as e:
                        warn(f"Failed to parse {config_path}: {e}")
                        return

                    # Extract filament_colour array
                    filament_colours = config.get("filament_colour", [])
                    if filament_colours:
                        # Use 0-indexed storage to match paint_color codes:
                        # - No paint_color / paint_color="" -> filament 0 -> first color
                        # - paint_color="4" -> filament 1 -> second color
                        # - paint_color="8" -> filament 2 -> third color
                        for idx, hex_color in enumerate(filament_colours):
                            self.orca_filament_colors[idx] = hex_color

                        debug(f"Loaded {len(filament_colours)} Orca filament colors: {filament_colours}")
                        self.safe_report(
                            {"INFO"},
                            f"Loaded {len(filament_colours)} Orca filament colors",
                        )

        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read Orca config from {archive_path}: {e}")

    def read_prusa_slic3r_colors(self, archive_path: str) -> None:
        """
        Read extruder colors from PrusaSlicer's Slic3r_PE.config.

        This file contains lines like:
        ; extruder_colour = #FF8000;#DB5182;#3EC0FF;#FF4F4F;#FBEB7D

        :param archive_path: Path to the 3MF archive file.
        """
        if self.import_materials == "NONE":
            return

        # Skip if colors already loaded from Orca config
        if self.orca_filament_colors:
            debug("Filament colors already loaded, skipping Slic3r_PE.config")
            return

        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                config_path = "Metadata/Slic3r_PE.config"
                if config_path not in archive.namelist():
                    debug(f"No {config_path} in archive, skipping PrusaSlicer color import")
                    return

                with archive.open(config_path) as config_file:
                    content = config_file.read().decode("UTF-8")

                    # Parse ; extruder_colour = ... line
                    for line in content.split("\n"):
                        line = line.strip()
                        if line.startswith("; extruder_colour = "):
                            colors_str = line[len("; extruder_colour = "):]
                            hex_colors = [c.strip() for c in colors_str.split(";")]

                            for idx, hex_color in enumerate(hex_colors):
                                if hex_color.startswith("#"):
                                    self.orca_filament_colors[idx] = hex_color

                            self.safe_report(
                                {"INFO"},
                                f"Loaded {len(hex_colors)} PrusaSlicer extruder colors",
                            )
                            break

        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read PrusaSlicer config from {archive_path}: {e}")

    def read_blender_addon_colors(self, archive_path: str) -> None:
        """
        Read extruder colors from our addon's fallback metadata.

        This XML file contains extruder elements like:
        <filament_colors>
          <extruder index="0" color="#FF8000"/>
          <extruder index="1" color="#DB5182"/>
        </filament_colors>

        Used as a fallback when no slicer config is present, allowing
        round-trip color preservation without forcing project file behavior.

        :param archive_path: Path to the 3MF archive file.
        """
        if self.import_materials == "NONE":
            return

        # Skip if colors already loaded from Orca or PrusaSlicer config
        if self.orca_filament_colors:
            debug("Filament colors already loaded, skipping blender_filament_colors.xml")
            return

        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                config_path = "Metadata/blender_filament_colors.xml"
                if config_path not in archive.namelist():
                    debug(f"No {config_path} in archive, using default colors")
                    return

                with archive.open(config_path) as config_file:
                    tree = xml.etree.ElementTree.parse(config_file)
                    root = tree.getroot()

                    # Parse <extruder index="0" color="#FF8000"/> elements
                    for extruder_elem in root.findall("extruder"):
                        try:
                            extruder_idx = int(extruder_elem.get("index", "-1"))
                            hex_color = extruder_elem.get("color", "")
                            if extruder_idx >= 0 and hex_color.startswith("#"):
                                self.orca_filament_colors[extruder_idx] = hex_color
                        except (ValueError, AttributeError):
                            continue

                    if self.orca_filament_colors:
                        debug(f"Loaded {len(self.orca_filament_colors)} colors from Blender addon metadata (fallback)")
                        self.safe_report(
                            {"INFO"},
                            f"Loaded {len(self.orca_filament_colors)} colors from addon metadata",
                        )

        except (zipfile.BadZipFile, IOError, xml.etree.ElementTree.ParseError) as e:
            debug(f"Could not read Blender addon colors from {archive_path}: {e}")

    def read_prusa_object_extruders(self, archive_path: str) -> None:
        """
        Read object extruder assignments from PrusaSlicer's Slic3r_PE_model.config.

        This file contains XML like:
        <object id="1" ...>
          <metadata type="object" key="extruder" value="3"/>
        </object>

        :param archive_path: Path to the 3MF archive file.
        """
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                config_path = "Metadata/Slic3r_PE_model.config"
                if config_path not in archive.namelist():
                    debug(f"No {config_path} in archive, skipping object extruder import")
                    return

                with archive.open(config_path) as config_file:
                    content = config_file.read().decode("UTF-8")

                    try:
                        root = xml.etree.ElementTree.fromstring(content)
                    except xml.etree.ElementTree.ParseError as e:
                        warn(f"Failed to parse {config_path}: {e}")
                        return

                    # Find all object elements and extract extruder metadata
                    for obj in root.findall(".//object"):
                        obj_id = obj.get("id")
                        if obj_id is None:
                            continue

                        # Look for extruder metadata at object level
                        for meta in obj.findall("metadata"):
                            if meta.get("type") == "object" and meta.get("key") == "extruder":
                                try:
                                    extruder = int(meta.get("value", "1"))
                                    # PrusaSlicer uses 1-based extruder numbers
                                    self.object_default_extruders[obj_id] = extruder
                                    debug(f"Object {obj_id} uses extruder {extruder}")
                                except ValueError:
                                    pass

                    if self.object_default_extruders:
                        debug(f"Loaded extruder assignments for {len(self.object_default_extruders)} objects")

        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read PrusaSlicer model config from {archive_path}: {e}")

    def read_prusa_filament_colors(self, archive_path: str) -> None:
        """
        Read filament colors from Blender's PrusaSlicer MMU export metadata.

        This reads from Metadata/blender_filament_colors.txt which maps paint codes to hex colors.
        Format: paint_code=hex_color (one per line)

        :param archive_path: Path to the 3MF archive file.
        """
        if self.import_materials == "NONE":
            return

        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                metadata_path = "Metadata/blender_filament_colors.txt"
                if metadata_path not in archive.namelist():
                    debug(f"No {metadata_path} in archive, skipping Prusa color import")
                    return

                with archive.open(metadata_path) as metadata_file:
                    content = metadata_file.read().decode("UTF-8")

                    # Parse paint_code=hex_color lines
                    for line in content.strip().split("\n"):
                        if "=" in line:
                            paint_code, hex_color = line.strip().split("=", 1)
                            # Convert paint code to filament index
                            filament_index = parse_paint_color_to_index(paint_code)
                            if filament_index > 0:
                                # Store as 0-indexed (filament 1 -> index 0)
                                array_index = filament_index - 1
                                self.orca_filament_colors[array_index] = hex_color

                    debug(f"Loaded {len(self.orca_filament_colors)} Prusa filament colors from metadata")
                    self.safe_report(
                        {"INFO"},
                        f"Loaded {len(self.orca_filament_colors)} PrusaSlicer filament colors",
                    )

        except (zipfile.BadZipFile, IOError) as e:
            debug(f"Could not read Prusa filament colors from {archive_path}: {e}")

    def srgb_to_linear(self, value: float) -> float:
        """
        Convert sRGB color component to linear color space.
        Delegates to import_materials.base module.

        :param value: sRGB value (0.0-1.0)
        :return: Linear value (0.0-1.0)
        """
        return _srgb_to_linear_impl(value)

    def parse_hex_color(self, hex_color: str) -> Tuple[float, float, float, float]:
        """
        Parse a hex color string to RGBA tuple.
        Delegates to import_materials.base module.

        :param hex_color: Hex color string like "#FF0000" or "FF0000"
        :return: RGBA tuple with values 0.0-1.0
        """
        return _parse_hex_color_impl(hex_color)

    def _apply_pbr_to_principled(
        self,
        principled: bpy_extras.node_shader_utils.PrincipledBSDFWrapper,
        material: bpy.types.Material,
        resource_material: ResourceMaterial,
    ) -> None:
        """
        Apply PBR properties from a 3MF ResourceMaterial to a Blender Principled BSDF material.

        Delegates to import_materials.pbr module.

        :param principled: PrincipledBSDFWrapper for the material
        :param material: The Blender material being configured
        :param resource_material: The ResourceMaterial with PBR data from 3MF
        """
        _apply_pbr_to_principled_impl(self, principled, material, resource_material)

    def _setup_textured_material(self, material: bpy.types.Material, texture: "ResourceTexture") -> None:
        """
        Set up a Blender material with an Image Texture node for 3MF texture support.

        Delegates to import_materials.textures module.

        :param material: The Blender material to configure
        :param texture: The ResourceTexture containing the Blender image
        """
        _setup_textured_material_impl(self, material, texture)

    def _apply_pbr_textures_to_material(
        self, material: bpy.types.Material, resource_material: ResourceMaterial
    ) -> bool:
        """
        Apply PBR texture maps from a 3MF ResourceMaterial to a Blender material.

        Delegates to import_materials.pbr module.

        :param material: The Blender material to configure (must have node tree)
        :param resource_material: The ResourceMaterial with PBR texture IDs from 3MF
        :return: True if any textures were applied, False otherwise
        """
        return _apply_pbr_textures_impl(self, material, resource_material)

    def parse_transformation(self, transformation_str: str) -> mathutils.Matrix:
        """
        Parses a transformation matrix as written in the 3MF files.

        Transformations in 3MF files are written in the form:
        `m00 m01 m01 m10 m11 m12 m20 m21 m22 m30 m31 m32`

        This would then result in a row-major matrix of the form:
        ```
        _                 _
        | m00 m01 m02 0.0 |
        | m10 m11 m12 0.0 |
        | m20 m21 m22 0.0 |
        | m30 m31 m32 1.0 |
        -                 -
        ```
        :param transformation_str: A transformation as represented in 3MF.
        :return: A `Matrix` object with the correct transformation.
        """
        components = transformation_str.split(" ")
        result = mathutils.Matrix.Identity(4)
        if transformation_str == "":  # Early-out if transformation is missing. This is not malformed.
            return result
        row = -1
        col = 0
        for component in components:
            row += 1
            if row > 2:
                col += 1
                row = 0
                if col > 3:
                    warn(f"Transformation matrix contains too many components: {transformation_str}")
                    break  # Too many components. Ignore the rest.
            try:
                component_float = float(component)
            except ValueError:  # Not a proper float. Skip this one.
                warn(f"Transformation matrix malformed: {transformation_str}")
                continue
            result[row][col] = component_float
        return result

    def find_existing_material(
        self, name: str, color: Tuple[float, float, float, float]
    ) -> Optional[bpy.types.Material]:
        """
        Find an existing Blender material that matches the given name and color.

        Delegates to import_materials.base module.

        :param name: The desired material name.
        :param color: The RGBA color tuple (values 0-1).
        :return: Matching material if found, None otherwise.
        """
        return _find_existing_material_impl(self, name, color)

    def build_items(self, root, scale_unit):
        """
        Builds the scene. This places objects with certain transformations in
        the scene.
        :param root: The root node of the 3dmodel.model XML document.
        :param scale_unit: The scale to apply for the units of the model to be
        transformed to Blender's units, as a float ratio.
        :return: A sequence of Blender Objects that need to be placed in the
        scene. Each mesh gets transformed appropriately.
        """
        build_items = list(root.iterfind("./3mf:build/3mf:item", MODEL_NAMESPACES))
        total_items = len(build_items)
        for idx, build_item in enumerate(build_items):
            if total_items > 0:
                progress = 60 + int(((idx + 1) / total_items) * 35)
                self._progress_update(progress, f"Building {idx + 1}/{total_items} objects...")
            try:
                objectid = build_item.attrib["objectid"]
                resource_object = self.resource_objects[objectid]
            except KeyError:  # ID is required, and it must be in the available resource_objects.
                warn("Encountered build item without object ID.")
                continue  # Ignore this invalid item.

            metadata = Metadata()
            for metadata_node in build_item.iterfind("./3mf:metadatagroup", MODEL_NAMESPACES):
                metadata = self.read_metadata(metadata_node, metadata)
            if "partnumber" in build_item.attrib:
                metadata["3mf:partnumber"] = MetadataEntry(
                    name="3mf:partnumber",
                    preserve=True,
                    datatype="xs:string",
                    value=build_item.attrib["partnumber"],
                )

            transform = mathutils.Matrix.Scale(scale_unit, 4)
            transform @= self.parse_transformation(build_item.attrib.get("transform", ""))

            self.build_object(resource_object, transform, metadata, [objectid])

    def build_object(
        self,
        resource_object: ResourceObject,
        transformation: mathutils.Matrix,
        metadata: Metadata,
        objectid_stack_trace: List[int],
        parent: Optional[bpy.types.Object] = None,
        is_temp_component_def: bool = False,
    ) -> Optional[bpy.types.Object]:
        """
        Converts a resource object into a Blender object.

        This resource object may refer to components that need to be built along. These components may again have
        subcomponents, and so on. These will be built recursively. A "stack trace" will be traced in order to prevent
        going into an infinite recursion.

        Component instances (objects with no mesh, only a single component reference) are created as
        linked duplicates in Blender, sharing the same mesh data.

        :param resource_object: The resource object that needs to be converted.
        :param transformation: A transformation matrix to apply to this resource object.
        :param metadata: A collection of metadata belonging to this build item.
        :param objectid_stack_trace: A list of all object IDs that have been processed so far, including the object ID
        we're processing now.
        :param parent: The resulting object must be marked as a child of this Blender object.
        :param is_temp_component_def: If True, this is a temporary component definition that will be deleted,
                                      so don't add it to imported_objects list.
        :return: A sequence of Blender objects. These objects may be "nested" in the sense that they sometimes refer to
        other objects as their parents.
        """
        # Detect component instance: object with no mesh data, only a single component reference
        # This is the pattern created by component-optimized export
        is_component_instance = (
            not resource_object.triangles and resource_object.components and len(resource_object.components) == 1
        )

        if is_component_instance:
            # This is a component instance - create linked duplicate
            component = resource_object.components[0]
            component_id = component.resource_object

            # Check if we already have a mesh for this component
            if component_id in self.component_instance_cache:
                # Reuse existing mesh data (linked duplicate)
                cached_mesh, instance_count = self.component_instance_cache[component_id]
                mesh = cached_mesh
                self.component_instance_cache[component_id] = (
                    cached_mesh,
                    instance_count + 1,
                )
                debug(f"Creating linked duplicate {instance_count + 1} for component {component_id}")
            else:
                # First instance - need to build the component and cache its mesh
                try:
                    component_resource = self.resource_objects[component_id]
                except KeyError:
                    warn(f"Component reference to unknown resource ID: {component_id}")
                    return None

                # Build the component to get its mesh
                # Use identity transform - we'll apply the instance transform later
                # Mark as temporary so it doesn't get added to imported_objects list
                temp_obj = self.build_object(
                    component_resource,
                    mathutils.Matrix.Identity(4),
                    Metadata(),
                    objectid_stack_trace + [component_id],
                    parent=None,
                    is_temp_component_def=True,
                )

                if temp_obj and temp_obj.data:
                    mesh = temp_obj.data
                    # Cache the mesh for future instances
                    self.component_instance_cache[component_id] = (mesh, 1)
                    debug(f"Cached component {component_id} mesh for linked duplicates")

                    # Remove the temporary object from the scene
                    # We only wanted its mesh data
                    bpy.data.objects.remove(temp_obj, do_unlink=True)
                else:
                    warn(f"Failed to build component {component_id}")
                    return None
        else:
            # Normal object or container with multiple components - create mesh as usual
            mesh = None
            if resource_object.triangles:
                mesh = bpy.data.meshes.new("3MF Mesh")
                mesh.from_pydata(resource_object.vertices, [], resource_object.triangles)
                mesh.update()
                resource_object.metadata.store(mesh)

                # Store passthrough multiproperties pid for round-trip export
                current_objectid = objectid_stack_trace[0] if objectid_stack_trace else None
                if current_objectid and current_objectid in self.object_passthrough_pids:
                    mesh["3mf_passthrough_pid"] = self.object_passthrough_pids[current_objectid]
                    debug(f"Stored passthrough pid={self.object_passthrough_pids[current_objectid]} on mesh")

                # Track if we rendered paint texture (to skip standard material assignment)
                paint_texture_rendered = False

                # Handle MMU segmentation with UV texture rendering (if in PAINT mode)
                if (
                    resource_object.segmentation_strings
                    and self.import_materials == "PAINT"
                    and resource_object.default_extruder is not None
                ):
                    # Need to create a temporary object for UV unwrapping
                    temp_obj_for_uv = bpy.data.objects.new("_temp_uv", mesh)
                    bpy.context.collection.objects.link(temp_obj_for_uv)

                    try:
                        # Get extruder colors from vendor config (both Orca and PrusaSlicer use orca_filament_colors)
                        extruder_colors_hex = {}
                        if hasattr(self, "orca_filament_colors") and self.orca_filament_colors:
                            extruder_colors_hex = self.orca_filament_colors.copy()

                        # If colors are available, render segmentation to texture
                        if extruder_colors_hex:
                            from .import_hash_segmentation import (
                                render_segmentation_to_texture,
                            )

                            # Convert hex colors to RGBA lists
                            extruder_colors = {}
                            for idx, hex_color in extruder_colors_hex.items():
                                if hex_color.startswith("#") and len(hex_color) == 7:
                                    r = int(hex_color[1:3], 16) / 255.0
                                    g = int(hex_color[3:5], 16) / 255.0
                                    b = int(hex_color[5:7], 16) / 255.0
                                    extruder_colors[idx] = [r, g, b, 1.0]
                                else:
                                    extruder_colors[idx] = [
                                        0.5,
                                        0.5,
                                        0.5,
                                        1.0,
                                    ]  # Fallback gray

                            # Determine texture size based on tri count (balance quality vs performance)
                            tri_count = len(resource_object.triangles)
                            if tri_count < 5000:
                                texture_size = 2048
                            elif tri_count < 20000:
                                texture_size = 4096
                            else:
                                texture_size = 8192

                            debug(
                                f"Rendering MMU segmentation to {texture_size}x{texture_size} "
                                f"UV texture for {tri_count} triangles"
                            )

                            # Render segmentation to texture
                            image = render_segmentation_to_texture(
                                temp_obj_for_uv,
                                resource_object.segmentation_strings,
                                extruder_colors,
                                texture_size=texture_size,
                                default_extruder=resource_object.default_extruder,
                                bpy=bpy,
                            )

                            # Create material with the segmentation texture
                            mat = bpy.data.materials.new(name=f"{mesh.name}_MMU_Paint")
                            mat.use_nodes = True
                            nodes = mat.node_tree.nodes
                            links = mat.node_tree.links

                            # Clear default nodes
                            nodes.clear()

                            # Create texture node
                            tex_node = nodes.new("ShaderNodeTexImage")
                            tex_node.image = image
                            tex_node.location = (-300, 0)

                            # Create BSDF
                            bsdf = nodes.new("ShaderNodeBsdfPrincipled")
                            bsdf.location = (100, 0)

                            # Create output
                            output = nodes.new("ShaderNodeOutputMaterial")
                            output.location = (400, 0)

                            # Link nodes
                            links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
                            links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

                            # Add material to mesh
                            mesh.materials.append(mat)

                            # Assign this material to ALL faces (index 0 since it's the only material)
                            num_faces = len(mesh.polygons)
                            material_indices = [0] * num_faces
                            mesh.polygons.foreach_set("material_index", material_indices)

                            # Store extruder color mapping as custom properties for export round-trip
                            mesh["3mf_paint_extruder_colors"] = str(extruder_colors_hex)  # Store as dict string
                            mesh["3mf_paint_default_extruder"] = resource_object.default_extruder
                            mesh["3mf_is_paint_texture"] = True

                            paint_texture_rendered = True
                            self._paint_object_names.append(mesh.name)
                            debug("Successfully rendered MMU paint data to UV texture")
                        else:
                            warn("No extruder colors found - cannot render MMU paint texture")

                    finally:
                        # Remove temporary object
                        bpy.data.objects.remove(temp_obj_for_uv, do_unlink=True)

                # Only do standard material assignment if we didn't render a paint texture
                if not paint_texture_rendered:
                    # Mapping resource materials to indices in the list of materials for this specific mesh.
                    # Build material index array for batch assignment (much faster than per-face assignment)
                    materials_to_index = {}
                    material_indices = [0] * len(resource_object.materials)  # Pre-allocate

                    for triangle_index, triangle_material in enumerate(resource_object.materials):
                        if triangle_material is None:
                            continue

                        # Add the material to Blender if it doesn't exist yet.
                        # Otherwise create a new material in Blender.
                        if triangle_material not in self.resource_to_material:
                            # Check if a textured version of this basematerial was already created
                            # from multiproperties - if so, reuse it to prevent duplicates
                            found_textured_version = False
                            if triangle_material.texture_id is None:  # This is a plain basematerial
                                # Search for a textured version in the cache
                                for textured_mat, original_mat in self.textured_to_basematerial_map.items():
                                    if original_mat == triangle_material and textured_mat in self.resource_to_material:
                                        # Found the textured version
                                        self.resource_to_material[triangle_material] = self.resource_to_material[textured_mat]
                                        found_textured_version = True
                                        debug(f"Reusing textured material for basematerial '{triangle_material.name}'")
                                        break
                            
                            if not found_textured_version:
                                # Cache material name to protect Unicode characters from garbage collection
                                material_name = str(triangle_material.name)

                                # Try to reuse existing material if enabled
                                # (not for textured materials or PBR textured materials)
                                material = None
                                has_pbr_textures = (
                                    triangle_material.basecolor_texid is not None
                                    or triangle_material.metallic_texid is not None
                                    or triangle_material.roughness_texid is not None
                                    or triangle_material.specular_texid is not None
                                    or triangle_material.glossiness_texid is not None
                                )

                                if self.reuse_materials and triangle_material.texture_id is None and not has_pbr_textures:
                                    material = self.find_existing_material(material_name, triangle_material.color)

                                # Create new material if not found or reuse disabled
                                if material is None:
                                    material = bpy.data.materials.new(material_name)
                                    material.use_nodes = True

                                    if triangle_material.texture_id is not None:
                                        # Collect all textures (primary + extra from multiproperties)
                                        all_textures = []
                                        all_tex_group_ids = [triangle_material.texture_id]
                                        extra_ids = getattr(triangle_material, 'extra_texture_ids', None)
                                        if extra_ids:
                                            all_tex_group_ids.extend(extra_ids)

                                        for tg_id in all_tex_group_ids:
                                            tg = self.resource_texture_groups.get(tg_id)
                                            if tg:
                                                tex = self.resource_textures.get(tg.texid)
                                                if tex and tex.blender_image:
                                                    all_textures.append(tex)

                                        if len(all_textures) > 1:
                                            # Multi-texture setup (multiproperties with multiple texture groups)
                                            _setup_multi_textured_material_impl(
                                                self, material, all_textures
                                            )
                                        elif len(all_textures) == 1:
                                            # Single texture
                                            self._setup_textured_material(material, all_textures[0])
                                        else:
                                            warn(f"No valid textures found for texture groups {all_tex_group_ids}")

                                        # Apply scalar PBR properties (metallic, roughness, etc.)
                                        principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
                                            material, is_readonly=False
                                        )
                                        self._apply_pbr_to_principled(principled, material, triangle_material)

                                        # Also apply PBR textures (roughness, metallic, etc.)
                                        self._apply_pbr_textures_to_material(material, triangle_material)
                                    else:
                                        # Standard color-based material
                                        principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
                                            material, is_readonly=False
                                        )
                                        principled.base_color = triangle_material.color[:3]
                                        principled.alpha = triangle_material.color[3]

                                        # Apply scalar PBR properties from 3MF Materials Extension
                                        self._apply_pbr_to_principled(principled, material, triangle_material)

                                        # Apply textured PBR properties (metallic/roughness/specular texture maps)
                                        self._apply_pbr_textures_to_material(material, triangle_material)

                                self.resource_to_material[triangle_material] = material
                                
                                # If this material was created from multiproperties with textures,
                                # also cache it under the original basematerial key to prevent duplicates
                                if triangle_material in self.textured_to_basematerial_map:
                                    original = self.textured_to_basematerial_map[triangle_material]
                                    if original not in self.resource_to_material:
                                        self.resource_to_material[original] = material
                                        debug("Cached textured material under original basematerial key")
                        else:
                            material = self.resource_to_material[triangle_material]

                        # Add the material to this mesh if it doesn't have it yet. Otherwise re-use previous index.
                        if triangle_material not in materials_to_index:
                            new_index = len(mesh.materials.items())
                            if new_index > 32767:
                                warn("Blender doesn't support more than 32768 different materials per mesh.")
                                continue
                            mesh.materials.append(material)
                            materials_to_index[triangle_material] = new_index

                        # Store material index for batch assignment
                        material_indices[triangle_index] = materials_to_index[triangle_material]

                    # Batch assign material indices using foreach_set (much faster than per-face loop)
                    if materials_to_index:  # Only if we have materials to assign
                        mesh.polygons.foreach_set("material_index", material_indices)

            # Apply triangle sets as integer face attributes
            # Face maps were removed in Blender 4.0, use custom attributes instead
            if resource_object.triangle_sets:
                # Create an integer attribute to store triangle set membership
                # Each face gets the index of its triangle set (0 = no set, 1+ = set index)
                # Also store set names as a custom property on the mesh
                set_names = list(resource_object.triangle_sets.keys())
                if set_names:
                    # Store triangle set names as mesh custom property
                    mesh["3mf_triangle_set_names"] = set_names

                    # Create integer attribute for face->set mapping
                    attr_name = "3mf_triangle_set"
                    if attr_name not in mesh.attributes:
                        mesh.attributes.new(name=attr_name, type="INT", domain="FACE")

                    # Build array of set indices for bulk assignment
                    # This is much faster than per-face loops for large meshes
                    num_faces = len(mesh.polygons)
                    set_values = [0] * num_faces  # Pre-allocate list, 0 = no set

                    # Assign faces to their triangle sets (1-indexed to reserve 0 for "no set")
                    triangle_set_items = resource_object.triangle_sets.items()
                    for set_idx, (set_name, triangle_indices) in enumerate(triangle_set_items, start=1):
                        for tri_idx in triangle_indices:
                            if 0 <= tri_idx < num_faces:
                                set_values[tri_idx] = set_idx

                    # Bulk assign using foreach_set (much faster than individual assignments)
                    mesh.attributes[attr_name].data.foreach_set("value", set_values)

                    debug(f"Applied {len(resource_object.triangle_sets)} triangle sets as face attributes")

            # Apply UV coordinates from texture mapping (3MF Materials Extension)
            if resource_object.triangle_uvs:
                # Create UV layer for texture coordinates
                uv_layer = mesh.uv_layers.new(name="UVMap")
                if uv_layer:
                    # Prepare UV data for bulk assignment
                    # Each triangle has 3 loops, each loop needs UV coordinates
                    uv_data = []
                    for tri_idx, tri_uvs in enumerate(resource_object.triangle_uvs):
                        if tri_uvs is not None:
                            # tri_uvs is ((u1, v1), (u2, v2), (u3, v3))
                            for uv in tri_uvs:
                                uv_data.append(uv[0])  # U
                                uv_data.append(uv[1])  # V
                        else:
                            # No UV for this triangle, use default (0, 0)
                            for _ in range(3):
                                uv_data.append(0.0)
                                uv_data.append(0.0)

                    # Bulk assign UV coordinates
                    uv_layer.data.foreach_set("uv", uv_data)
                    debug(f"Applied UV coordinates to mesh ({len(resource_object.triangle_uvs)} triangles)")

        # Only create a Blender object if there's actual mesh data.
        # Component-only objects (containers) don't need visible representation.
        if mesh is not None:
            blender_object = bpy.data.objects.new("3MF Object", mesh)
            self.num_loaded += 1
            if parent is not None:
                blender_object.parent = parent

            # Link to scene first so we can manipulate it
            bpy.context.collection.objects.link(blender_object)
            bpy.context.view_layer.objects.active = blender_object
            blender_object.select_set(True)

            # Set origin placement BEFORE applying transformation
            if self.origin_to_geometry in ("CENTER", "BOTTOM"):
                # Store current mode and switch to object mode
                previous_mode = bpy.context.object.mode if bpy.context.object else "OBJECT"
                if previous_mode != "OBJECT":
                    bpy.ops.object.mode_set(mode="OBJECT")

                if self.origin_to_geometry == "CENTER":
                    # Set origin to geometry center
                    bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
                elif self.origin_to_geometry == "BOTTOM":
                    # Set origin to bottom center of bounding box
                    # First get the bounding box in local space
                    bbox = blender_object.bound_box
                    min_z = min(v[2] for v in bbox)
                    center_x = (min(v[0] for v in bbox) + max(v[0] for v in bbox)) / 2
                    center_y = (min(v[1] for v in bbox) + max(v[1] for v in bbox)) / 2

                    # Calculate offset from current origin to bottom center
                    bottom_center = mathutils.Vector((center_x, center_y, min_z))

                    # Move mesh data in opposite direction to effectively move origin
                    mesh.transform(mathutils.Matrix.Translation(-bottom_center))
                    # Update the object's location to compensate
                    blender_object.location += bottom_center

                # Restore previous mode
                if previous_mode != "OBJECT":
                    bpy.ops.object.mode_set(mode=previous_mode)

            # Now apply transformation and placement options
            if self.import_location == "ORIGIN":
                # Place at world origin - strip translation from transformation
                transformation.translation = mathutils.Vector((0, 0, 0))
            elif self.import_location == "CURSOR":
                # Place at 3D cursor
                cursor_location = bpy.context.scene.cursor.location
                transformation.translation = cursor_location
            elif self.import_location == "GRID":
                # Grid layout - place at origin initially, will be arranged later
                transformation.translation = mathutils.Vector((0, 0, 0))
            # else 'KEEP' - use original transformation as-is

            blender_object.matrix_world = transformation

            # Track for grid layout (only root objects, not parented components, and not temp component defs)
            if parent is None and not is_temp_component_def and hasattr(self, "imported_objects"):
                self.imported_objects.append(blender_object)

            metadata.store(blender_object)
            # Higher precedence for per-resource metadata
            resource_object.metadata.store(blender_object)
            if "3mf:object_type" in resource_object.metadata and resource_object.metadata["3mf:object_type"].value in {
                "solidsupport",
                "support",
            }:
                # Don't render support meshes.
                blender_object.hide_render = True
        else:
            # No mesh data - this is a component-only container.
            # Don't create an Empty, just pass through to components.
            blender_object = parent

        # Recurse for all components (skip if this was a component instance - already handled)
        if not is_component_instance:
            for component in resource_object.components:
                if component.resource_object in objectid_stack_trace:
                    # These object IDs refer to each other in a loop. Don't go in there!
                    warn(f"Recursive components in object ID: {component.resource_object}")
                    continue
                try:
                    child_object = self.resource_objects[component.resource_object]
                except KeyError:  # Invalid resource ID. Doesn't exist!
                    warn(f"Build item with unknown resource ID: {component.resource_object}")
                    continue
                transform = (
                    transformation @ component.transformation
                )  # Apply the child's transformation and pass it on.
                objectid_stack_trace.append(component.resource_object)
                self.build_object(
                    child_object,
                    transform,
                    metadata,
                    objectid_stack_trace,
                    parent=blender_object,
                )
                objectid_stack_trace.pop()

        return blender_object
