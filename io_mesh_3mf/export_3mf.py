import base64  # To decode files that must be preserved.
import collections  # Counter, to find the most common material of an object.
import datetime  # For Core Properties timestamps.
import io  # For string buffer to post-process XML
import itertools
import json  # For writing Orca project_settings.config
import logging  # To debug and log progress.
import os  # For path operations
import re  # For cleaning namespace prefixes in XML
import uuid  # For generating UUIDs for Production Extension
import xml.etree.ElementTree  # To write XML documents with the 3D model data.
import zipfile  # To write zip archives, the shell of the 3MF file.
from typing import Optional, Dict, Set, List, Tuple

import bpy  # The Blender API.
import bpy.props  # To define metadata properties for the operator.
import bpy.types  # This class is an operator in Blender, and to find meshes in the scene.
import bpy_extras.io_utils  # Helper functions to export meshes more easily.
import bpy_extras.node_shader_utils  # Converting material colors to sRGB.
import mathutils  # For the transformation matrices.

from .annotations import Annotations  # To store file annotations
from .constants import (
    MODEL_LOCATION,
    MODEL_NAMESPACE,
    MODEL_DEFAULT_UNIT,
    MODEL_REL,
    MATERIAL_NAMESPACE,
    PRODUCTION_NAMESPACE,
    BAMBU_NAMESPACE,
    RELS_NAMESPACE,
    CORE_PROPERTIES_LOCATION,
    CORE_PROPERTIES_NAMESPACE,
    DC_NAMESPACE,
    DCTERMS_NAMESPACE,
    conflicting_mustpreserve_contents,
)
from .extensions import (
    ExtensionManager,
    PRODUCTION_EXTENSION,
    ORCA_EXTENSION,
)
from .metadata import (
    Metadata,  # To store metadata from the Blender scene into the 3MF file.
)
from .unit_conversions import blender_to_metre, threemf_to_metre

# Orca Slicer paint_color encoding for filament IDs
# This matches CONST_FILAMENTS in OrcaSlicer's Model.cpp
# Index 0 = no color (base extruder), 1-32 = filament IDs
ORCA_FILAMENT_CODES = [
    "", "4", "8", "0C", "1C", "2C", "3C", "4C", "5C", "6C", "7C", "8C", "9C", "AC", "BC", "CC", "DC",
    "EC", "0FC", "1FC", "2FC", "3FC", "4FC", "5FC", "6FC", "7FC", "8FC", "9FC", "AFC", "BFC",
    "CFC", "DFC", "EFC",
]

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

# IDE and Documentation support.
__all__ = ["Export3MF"]

log = logging.getLogger(__name__)


class Export3MF(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    """
    Operator that exports a 3MF file from Blender.
    """

    # Metadata.
    bl_idname = "export_mesh.threemf"
    bl_label = "Export 3MF"
    bl_description = "Save the current scene to 3MF"
    filename_ext = ".3mf"

    # Options for the user.
    filter_glob: bpy.props.StringProperty(default="*.3mf", options={"HIDDEN"})
    use_selection: bpy.props.BoolProperty(
        name="Selection Only",
        description="Export selected objects only.",
        default=False,
    )
    export_hidden: bpy.props.BoolProperty(
        name="Export hidden objects",
        description="Export objects hidden in the viewport",
        default=False,
    )
    global_scale: bpy.props.FloatProperty(
        name="Scale", default=1.0, soft_min=0.001, soft_max=1000.0, min=1e-6, max=1e6
    )
    use_mesh_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers",
        description="Apply the modifiers before saving.",
        default=True,
    )
    coordinate_precision: bpy.props.IntProperty(
        name="Precision",
        description="The number of decimal digits to use in coordinates in the file.",
        default=9,
        min=0,
        max=12,
    )
    use_orca_format: bpy.props.BoolProperty(
        name="Orca Slicer Color Zones",
        description="Export face colors as Orca Slicer color zones. Assign different materials to faces "
                    "in Blender, and each color will become a separate filament in Orca",
        default=False,
    )

    def invoke(self, context, event):
        """Initialize properties from preferences when the export dialog is opened."""
        prefs = context.preferences.addons.get(__package__)
        if prefs and prefs.preferences:
            self.coordinate_precision = prefs.preferences.default_coordinate_precision
            self.export_hidden = prefs.preferences.default_export_hidden
            self.use_mesh_modifiers = prefs.preferences.default_apply_modifiers
            self.global_scale = prefs.preferences.default_global_scale
        return super().invoke(context, event)

    def draw(self, context):
        """Custom draw method for the export dialog."""
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        # Orca Slicer section at the top (most important for multi-color printing)
        orca_box = layout.box()
        orca_header = orca_box.row()
        orca_header.label(text="Multi-Color Printing", icon='COLORSET_01_VEC')
        orca_box.prop(self, "use_orca_format")
        if self.use_orca_format:
            info_col = orca_box.column(align=True)
            info_col.scale_y = 0.7
            info_col.label(text="Tip: Assign different materials to faces in Edit Mode", icon='INFO')
            info_col.label(text="Each unique color becomes a filament in Orca Slicer")

        layout.separator()

        # Standard options
        layout.prop(self, "use_selection")
        layout.prop(self, "export_hidden")
        layout.prop(self, "use_mesh_modifiers")
        layout.prop(self, "global_scale")
        layout.prop(self, "coordinate_precision")

    def safe_report(self, level: Set[str], message: str) -> None:
        """
        Safely report a message, using Blender's report system if available, otherwise just logging.
        This allows the class to work both as a Blender operator and in unit tests.
        :param level: The report level (e.g., {'ERROR'}, {'WARNING'}, {'INFO'})
        :param message: The message to report
        """
        if hasattr(self, 'report') and callable(getattr(self, 'report', None)):
            self.report(level, message)
        # If report is not available, the message has already been logged via the log module

    def attr(self, name: str) -> str:
        """
        Get attribute name, optionally with namespace prefix.

        In Orca mode, attributes should not have namespace prefixes.
        In standard 3MF mode with default_namespace, they need the prefix.
        """
        if self.use_orca_format:
            return name
        return f"{{{MODEL_NAMESPACE}}}{name}"

    def execute(self, context: bpy.types.Context) -> Set[str]:
        """
        The main routine that writes the 3MF archive.

        This function serves as a high-level overview of the steps involved to write a 3MF file.
        :param context: The Blender context.
        :return: A set of status flags to indicate whether the write succeeded or not.
        """
        # Reset state.
        self.next_resource_id = 1  # Starts counting at 1 for some inscrutable reason.
        self.material_resource_id = -1
        self.num_written = 0
        self.vertex_colors = {}  # Maps color hex values to filament indices for Orca export
        self.orca_object_files = []  # List of (path, uuid) for each object model file

        # Initialize extension manager
        self.extension_manager = ExtensionManager()

        archive = self.create_archive(self.filepath)
        if archive is None:
            return {"CANCELLED"}

        if self.use_selection:
            blender_objects = context.selected_objects
        else:
            blender_objects = context.scene.objects

        global_scale = self.unit_scale(context)

        # Orca Slicer mode uses Production Extension with separate model files
        if self.use_orca_format:
            return self.execute_orca_export(context, archive, blender_objects, global_scale)

        # Standard 3MF export (original behavior)
        return self.execute_standard_export(context, archive, blender_objects, global_scale)

    def execute_standard_export(self, context: bpy.types.Context, archive: zipfile.ZipFile,
                                blender_objects, global_scale: float) -> Set[str]:
        """
        Standard 3MF export (non-Orca mode).

        Uses core 3MF spec with optional basematerials.
        """
        # Register all active extension namespaces with ElementTree
        self.extension_manager.register_namespaces(xml.etree.ElementTree)

        # Create model root element
        root = xml.etree.ElementTree.Element(
            f"{{{MODEL_NAMESPACE}}}model"
        )

        scene_metadata = Metadata()
        scene_metadata.retrieve(bpy.context.scene)
        self.write_metadata(root, scene_metadata)

        resources_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}resources"
        )

        self.material_name_to_index = self.write_materials(
            resources_element, blender_objects
        )

        self.write_objects(root, resources_element, blender_objects, global_scale)

        document = xml.etree.ElementTree.ElementTree(root)
        with archive.open(MODEL_LOCATION, "w", force_zip64=True) as f:
            document.write(
                f,
                xml_declaration=True,
                encoding="UTF-8",
                default_namespace=MODEL_NAMESPACE,
            )

        # Write OPC Core Properties (Dublin Core metadata)
        self.write_core_properties(archive)

        try:
            archive.close()
        except EnvironmentError as e:
            log.error(f"Unable to complete writing to 3MF archive: {e}")
            self.safe_report({'ERROR'}, f"Unable to complete writing to 3MF archive: {e}")
            return {"CANCELLED"}

        log.info(f"Exported {self.num_written} objects to 3MF archive {self.filepath}.")
        self.safe_report({'INFO'}, f"Exported {self.num_written} objects to {self.filepath}")
        return {"FINISHED"}

    def execute_orca_export(self, context: bpy.types.Context, archive: zipfile.ZipFile,
                            blender_objects, global_scale: float) -> Set[str]:
        """
        Orca Slicer export using Production Extension structure.

        Creates separate model files for each object with paint_color attributes,
        and a main model file with component references.
        """
        # Activate Production Extension for Orca compatibility
        self.extension_manager.activate(PRODUCTION_EXTENSION.namespace)
        self.extension_manager.activate(ORCA_EXTENSION.namespace)
        log.info("Activated Orca Slicer extensions: Production + BambuStudio")

        # Register namespaces
        xml.etree.ElementTree.register_namespace("", MODEL_NAMESPACE)
        xml.etree.ElementTree.register_namespace("p", PRODUCTION_NAMESPACE)
        xml.etree.ElementTree.register_namespace("BambuStudio", BAMBU_NAMESPACE)

        # Collect face colors for Orca export
        self.safe_report({'INFO'}, "Collecting face colors for Orca export...")
        self.vertex_colors = self.collect_face_colors(blender_objects)
        log.info(f"Orca mode enabled with {len(self.vertex_colors)} color zones")

        if len(self.vertex_colors) == 0:
            log.warning("No face colors found! Assign materials to faces for color zones.")
            self.safe_report({'WARNING'},
                             "No face colors detected. Assign different materials to faces in Edit mode.")
        else:
            self.safe_report({'INFO'}, f"Detected {len(self.vertex_colors)} color zones for Orca export")

        # Generate build UUID
        build_uuid = str(uuid.uuid4())

        # Filter mesh objects and track their data
        mesh_objects = []
        object_counter = 1
        for blender_object in blender_objects:
            if not self.export_hidden and not blender_object.visible_get():
                continue
            if blender_object.parent is not None:
                continue
            if blender_object.type != 'MESH':
                continue
            mesh_objects.append(blender_object)

        if not mesh_objects:
            self.safe_report({'ERROR'}, "No mesh objects found to export!")
            archive.close()
            return {"CANCELLED"}

        # Write individual object model files
        object_data = []  # List of (wrapper_id, mesh_id, object_path, wrapper_uuid, mesh_uuid, transformation)

        for idx, blender_object in enumerate(mesh_objects):
            object_counter = idx + 1
            wrapper_id = object_counter * 2  # Even IDs for wrappers
            mesh_id = object_counter * 2 - 1  # Odd IDs for mesh objects (inside sub-files)

            # Generate UUIDs
            wrapper_uuid = f"0000000{object_counter}-61cb-4c03-9d28-80fed5dfa1dc"
            mesh_uuid = f"000{object_counter}0000-81cb-4c03-9d28-80fed5dfa1dc"
            component_uuid = f"000{object_counter}0000-b206-40ff-9872-83e8017abed1"

            # Create safe filename
            safe_name = re.sub(r'[^\w\-.]', '_', blender_object.name)
            object_path = f"/3D/Objects/{safe_name}_{object_counter}.model"

            # Get transformation
            transformation = blender_object.matrix_world.copy()
            transformation = mathutils.Matrix.Scale(global_scale, 4) @ transformation

            # Write the individual object model file
            self.write_orca_object_model(
                archive, blender_object, object_path, mesh_id, mesh_uuid
            )

            object_data.append({
                'wrapper_id': wrapper_id,
                'mesh_id': mesh_id,
                'object_path': object_path,
                'wrapper_uuid': wrapper_uuid,
                'mesh_uuid': mesh_uuid,
                'component_uuid': component_uuid,
                'transformation': transformation,
                'name': blender_object.name,
            })

            self.num_written += 1

        # Write main 3dmodel.model with wrapper objects and build items
        self.write_orca_main_model(archive, object_data, build_uuid)

        # Write 3D/_rels/3dmodel.model.rels
        self.write_orca_model_relationships(archive, object_data)

        # Write Orca metadata files
        self.write_orca_metadata(archive, mesh_objects)

        try:
            archive.close()
        except EnvironmentError as e:
            log.error(f"Unable to complete writing to 3MF archive: {e}")
            self.safe_report({'ERROR'}, f"Unable to complete writing to 3MF archive: {e}")
            return {"CANCELLED"}

        log.info(f"Exported {self.num_written} objects to Orca-compatible 3MF archive {self.filepath}.")
        self.safe_report({'INFO'}, f"Exported {self.num_written} objects to {self.filepath}")
        return {"FINISHED"}

    def write_orca_object_model(self, archive: zipfile.ZipFile, blender_object: bpy.types.Object,
                                object_path: str, mesh_id: int, mesh_uuid: str) -> None:
        """
        Write an individual object model file for Orca Slicer.

        Each object gets its own .model file in 3D/Objects/ with the actual geometry
        and paint_color attributes on triangles.
        """
        # Create root element with Production Extension
        root = xml.etree.ElementTree.Element(
            "model",
            attrib={
                "unit": "millimeter",
                "xml:lang": "en-US",
                "xmlns": MODEL_NAMESPACE,
                "xmlns:BambuStudio": BAMBU_NAMESPACE,
                "xmlns:p": PRODUCTION_NAMESPACE,
                "requiredextensions": "p",
            }
        )

        # Add BambuStudio version metadata
        metadata = xml.etree.ElementTree.SubElement(root, "metadata", attrib={"name": "BambuStudio:3mfVersion"})
        metadata.text = "1"

        # Resources
        resources = xml.etree.ElementTree.SubElement(root, "resources")

        # Get mesh data
        if self.use_mesh_modifiers:
            dependency_graph = bpy.context.evaluated_depsgraph_get()
            eval_object = blender_object.evaluated_get(dependency_graph)
        else:
            eval_object = blender_object

        try:
            mesh = eval_object.to_mesh()
        except RuntimeError:
            log.warning(f"Could not get mesh for object: {blender_object.name}")
            return

        if mesh is None:
            return

        mesh.calc_loop_triangles()

        # Create object element
        obj_elem = xml.etree.ElementTree.SubElement(
            resources, "object",
            attrib={
                "id": str(mesh_id),
                "p:UUID": mesh_uuid,
                "type": "model",
            }
        )

        # Mesh element
        mesh_elem = xml.etree.ElementTree.SubElement(obj_elem, "mesh")

        # Vertices
        vertices_elem = xml.etree.ElementTree.SubElement(mesh_elem, "vertices")
        for vertex in mesh.vertices:
            xml.etree.ElementTree.SubElement(
                vertices_elem, "vertex",
                attrib={
                    "x": str(vertex.co.x),
                    "y": str(vertex.co.y),
                    "z": str(vertex.co.z),
                }
            )

        # Triangles with paint_color
        triangles_elem = xml.etree.ElementTree.SubElement(mesh_elem, "triangles")
        for triangle in mesh.loop_triangles:
            tri_attribs = {
                "v1": str(triangle.vertices[0]),
                "v2": str(triangle.vertices[1]),
                "v3": str(triangle.vertices[2]),
            }

            # Get paint_color from material
            triangle_color = self.get_triangle_color(mesh, triangle, blender_object)
            if triangle_color and triangle_color in self.vertex_colors:
                filament_index = self.vertex_colors[triangle_color]
                if filament_index < len(ORCA_FILAMENT_CODES):
                    paint_code = ORCA_FILAMENT_CODES[filament_index]
                    if paint_code:
                        tri_attribs["paint_color"] = paint_code

            xml.etree.ElementTree.SubElement(triangles_elem, "triangle", attrib=tri_attribs)

        # Empty build (geometry is in this file, build is in main model)
        xml.etree.ElementTree.SubElement(root, "build")

        # Clean up mesh
        eval_object.to_mesh_clear()

        # Write to archive
        # Remove leading slash for archive path
        archive_path = object_path.lstrip('/')

        document = xml.etree.ElementTree.ElementTree(root)
        buffer = io.BytesIO()
        document.write(buffer, xml_declaration=True, encoding="UTF-8")
        xml_content = buffer.getvalue().decode('UTF-8')

        with archive.open(archive_path, "w") as f:
            f.write(xml_content.encode('UTF-8'))

        log.info(f"Wrote object model: {archive_path}")

    def write_orca_main_model(self, archive: zipfile.ZipFile, object_data: List[dict],
                               build_uuid: str) -> None:
        """
        Write the main 3dmodel.model file with wrapper objects pointing to sub-models.
        """
        # Create root element
        root = xml.etree.ElementTree.Element(
            "model",
            attrib={
                "unit": "millimeter",
                "xml:lang": "en-US",
                "xmlns": MODEL_NAMESPACE,
                "xmlns:BambuStudio": BAMBU_NAMESPACE,
                "xmlns:p": PRODUCTION_NAMESPACE,
                "requiredextensions": "p",
            }
        )

        # Metadata
        meta_app = xml.etree.ElementTree.SubElement(root, "metadata", attrib={"name": "Application"})
        meta_app.text = "Blender-3MF-OrcaExport"

        meta_version = xml.etree.ElementTree.SubElement(root, "metadata", attrib={"name": "BambuStudio:3mfVersion"})
        meta_version.text = "1"

        # Standard metadata
        for name in ["Copyright", "Description", "Designer", "DesignerCover", "DesignerUserId", "License", "Origin"]:
            meta = xml.etree.ElementTree.SubElement(root, "metadata", attrib={"name": name})
            meta.text = ""

        # Creation/modification dates
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        meta_created = xml.etree.ElementTree.SubElement(root, "metadata", attrib={"name": "CreationDate"})
        meta_created.text = today
        meta_modified = xml.etree.ElementTree.SubElement(root, "metadata", attrib={"name": "ModificationDate"})
        meta_modified.text = today

        # Title from first object or scene
        title = object_data[0]['name'] if object_data else "Blender Export"
        meta_title = xml.etree.ElementTree.SubElement(root, "metadata", attrib={"name": "Title"})
        meta_title.text = title

        # Resources - wrapper objects with component references
        resources = xml.etree.ElementTree.SubElement(root, "resources")

        for obj in object_data:
            # Wrapper object
            wrapper = xml.etree.ElementTree.SubElement(
                resources, "object",
                attrib={
                    "id": str(obj['wrapper_id']),
                    "p:UUID": obj['wrapper_uuid'],
                    "type": "model",
                }
            )

            # Components element with path reference
            components = xml.etree.ElementTree.SubElement(wrapper, "components")
            xml.etree.ElementTree.SubElement(
                components, "component",
                attrib={
                    "p:path": obj['object_path'],
                    "objectid": str(obj['mesh_id']),
                    "p:UUID": obj['component_uuid'],
                    "transform": "1 0 0 0 1 0 0 0 1 0 0 0",  # Identity transform
                }
            )

        # Build element
        build = xml.etree.ElementTree.SubElement(root, "build", attrib={"p:UUID": build_uuid})

        for idx, obj in enumerate(object_data):
            item_uuid = f"0000000{idx + 2}-b1ec-4553-aec9-835e5b724bb4"
            transform_str = self.format_transformation(obj['transformation'])

            xml.etree.ElementTree.SubElement(
                build, "item",
                attrib={
                    "objectid": str(obj['wrapper_id']),
                    "p:UUID": item_uuid,
                    "transform": transform_str,
                    "printable": "1",
                }
            )

        # Write to archive
        document = xml.etree.ElementTree.ElementTree(root)
        buffer = io.BytesIO()
        document.write(buffer, xml_declaration=True, encoding="UTF-8")
        xml_content = buffer.getvalue().decode('UTF-8')

        with archive.open(MODEL_LOCATION, "w") as f:
            f.write(xml_content.encode('UTF-8'))

        log.info(f"Wrote main model: {MODEL_LOCATION}")

    def write_orca_model_relationships(self, archive: zipfile.ZipFile, object_data: List[dict]) -> None:
        """
        Write the 3D/_rels/3dmodel.model.rels file linking to sub-models.
        """
        root = xml.etree.ElementTree.Element(
            "Relationships",
            attrib={"xmlns": RELS_NAMESPACE}
        )

        for idx, obj in enumerate(object_data):
            xml.etree.ElementTree.SubElement(
                root, "Relationship",
                attrib={
                    "Target": obj['object_path'],
                    "Id": f"rel-{idx + 1}",
                    "Type": MODEL_REL,
                }
            )

        # Write to archive
        document = xml.etree.ElementTree.ElementTree(root)
        buffer = io.BytesIO()
        document.write(buffer, xml_declaration=True, encoding="UTF-8")
        xml_content = buffer.getvalue().decode('UTF-8')

        with archive.open("3D/_rels/3dmodel.model.rels", "w") as f:
            f.write(xml_content.encode('UTF-8'))

        log.info("Wrote 3D/_rels/3dmodel.model.rels")

    # The rest of the functions are in order of when they are called.

    def create_archive(self, filepath: str) -> Optional[zipfile.ZipFile]:
        """
        Creates an empty 3MF archive.

        The archive is complete according to the 3MF specs except that the actual 3dmodel.model file is missing.
        :param filepath: The path to write the file to.
        :return: A zip archive that other functions can add things to.
        """
        try:
            archive = zipfile.ZipFile(
                filepath, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            )

            # Store the file annotations we got from imported 3MF files, and store them in the archive.
            annotations = Annotations()
            annotations.retrieve()
            annotations.write_rels(archive)
            annotations.write_content_types(archive)
            self.must_preserve(archive)
        except EnvironmentError as e:
            log.error(f"Unable to write 3MF archive to {filepath}: {e}")
            self.safe_report({'ERROR'}, f"Unable to write 3MF archive to {filepath}: {e}")
            return None

        return archive

    def must_preserve(self, archive: zipfile.ZipFile) -> None:
        """
        Write files that must be preserved to the archive.

        These files were stored in the Blender scene in a hidden location.
        :param archive: The archive to write files to.
        """
        for textfile in bpy.data.texts:
            # Cache filename to protect Unicode characters from garbage collection
            filename = str(textfile.name)
            if not filename.startswith(".3mf_preserved/"):
                continue  # Unrelated file. Not ours to read.
            contents = textfile.as_string()
            if contents == conflicting_mustpreserve_contents:
                continue  # This file was in conflict. Don't preserve any copy of it then.
            contents = base64.b85decode(contents.encode("UTF-8"))
            filename = filename[len(".3mf_preserved/"):]
            with archive.open(filename, "w") as f:
                f.write(contents)

    def write_core_properties(self, archive: zipfile.ZipFile) -> None:
        """
        Write OPC Core Properties (Dublin Core metadata) to the archive.

        This adds standard document metadata like creator, creation date, and modification date
        as defined by the Open Packaging Conventions specification.
        :param archive: The 3MF archive to write Core Properties into.
        """
        # Register namespaces for cleaner output
        xml.etree.ElementTree.register_namespace("cp", CORE_PROPERTIES_NAMESPACE)
        xml.etree.ElementTree.register_namespace("dc", DC_NAMESPACE)
        xml.etree.ElementTree.register_namespace("dcterms", DCTERMS_NAMESPACE)

        # Create root element with proper namespaces
        root = xml.etree.ElementTree.Element(
            f"{{{CORE_PROPERTIES_NAMESPACE}}}coreProperties"
        )
        root.set("xmlns:dc", DC_NAMESPACE)
        root.set("xmlns:dcterms", DCTERMS_NAMESPACE)

        # dc:creator - who created this file
        creator = xml.etree.ElementTree.SubElement(root, f"{{{DC_NAMESPACE}}}creator")
        creator.text = "Blender 3MF Format Add-on"

        # dcterms:created - when the file was created (W3CDTF format)
        now = datetime.datetime.now(datetime.timezone.utc)
        created = xml.etree.ElementTree.SubElement(root, f"{{{DCTERMS_NAMESPACE}}}created")
        created.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # dcterms:modified - when the file was last modified
        modified = xml.etree.ElementTree.SubElement(root, f"{{{DCTERMS_NAMESPACE}}}modified")
        modified.text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Write the Core Properties file
        document = xml.etree.ElementTree.ElementTree(root)
        try:
            with archive.open(CORE_PROPERTIES_LOCATION, "w") as f:
                document.write(f, xml_declaration=True, encoding="UTF-8")
            log.info("Wrote OPC Core Properties to docProps/core.xml")
        except Exception as e:
            log.error(f"Failed to write Core Properties: {e}")

    def write_orca_metadata(self, archive: zipfile.ZipFile, blender_objects: List[bpy.types.Object]) -> None:
        """
        Write Orca Slicer compatible metadata files to the archive.

        This includes:
        - Metadata/project_settings.config: JSON with filament colors and printer settings
        - Metadata/model_settings.config: XML with object/extruder assignments

        :param archive: The 3MF archive to write metadata files into.
        :param blender_objects: List of Blender objects being exported.
        """
        log.info("Writing Orca metadata files...")

        try:
            # Write project_settings.config from template with updated colors
            project_settings = self.generate_project_settings()
            with archive.open("Metadata/project_settings.config", "w") as f:
                f.write(json.dumps(project_settings, indent=4).encode('utf-8'))
            log.info("Wrote project_settings.config")

            # Write model_settings.config with object metadata
            model_settings_xml = self.generate_model_settings(blender_objects)
            with archive.open("Metadata/model_settings.config", "w") as f:
                f.write(model_settings_xml.encode('utf-8'))
            log.info("Wrote model_settings.config")

            log.info(f"Wrote Orca metadata with {len(self.vertex_colors)} color zones")
        except Exception as e:
            log.error(f"Failed to write Orca metadata: {e}")
            self.safe_report({'ERROR'}, f"Failed to write Orca metadata: {e}")
            raise

    def generate_project_settings(self) -> dict:
        """
        Generate project_settings.config by loading the template and updating filament colors.

        :return: Dictionary to be serialized as JSON.
        """
        # Load the template from the addon directory
        addon_dir = os.path.dirname(os.path.realpath(__file__))
        template_path = os.path.join(addon_dir, "orca_project_template.json")

        with open(template_path, 'r', encoding='utf-8') as f:
            settings = json.load(f)

        # Get sorted colors by their index
        sorted_colors = sorted(self.vertex_colors.items(), key=lambda x: x[1])
        color_list = [color_hex for color_hex, _ in sorted_colors]

        if not color_list:
            color_list = ["#FFFFFF"]  # Default white if no colors

        num_colors = len(color_list)

        # Update filament_colour to match our export colors
        settings["filament_colour"] = color_list

        # Resize all filament arrays to match the number of colors
        # Find all keys that are filament arrays and resize them
        for key, value in list(settings.items()):
            if isinstance(value, list) and key.startswith("filament_") and key != "filament_colour":
                if len(value) > 0:
                    # Extend or truncate the array to match num_colors
                    if len(value) < num_colors:
                        # Repeat the last value to fill
                        settings[key] = value + [value[-1]] * (num_colors - len(value))
                    elif len(value) > num_colors:
                        settings[key] = value[:num_colors]

        # Also handle other arrays that need to match filament count
        array_keys_to_resize = [
            "activate_air_filtration", "activate_chamber_temp_control",
            "additional_cooling_fan_speed", "chamber_temperature",
            "close_fan_the_first_x_layers", "complete_print_exhaust_fan_speed",
            "cool_plate_temp", "cool_plate_temp_initial_layer",
            "default_filament_colour", "eng_plate_temp", "eng_plate_temp_initial_layer",
            "hot_plate_temp", "hot_plate_temp_initial_layer",
            "nozzle_temperature", "nozzle_temperature_initial_layer",
            "textured_plate_temp", "textured_plate_temp_initial_layer",
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
        """
        Generate the model_settings.config XML for Orca Slicer.

        Assigns default extruder (1) to all objects.
        :param blender_objects: List of Blender objects being exported.
        :return: XML string for model_settings.config.
        """
        root = xml.etree.ElementTree.Element("config")

        # For now, just create a basic structure with extruder assignment
        # In a full implementation, would map each object to appropriate extruders based on colors
        object_id = 2  # Start from 2 (1 is typically the mesh resource)

        for blender_object in blender_objects:
            if blender_object.type != 'MESH':
                continue

            object_elem = xml.etree.ElementTree.SubElement(root, "object", id=str(object_id))
            xml.etree.ElementTree.SubElement(object_elem, "metadata", key="name", value=str(blender_object.name))
            xml.etree.ElementTree.SubElement(object_elem, "metadata", key="extruder", value="1")

            part_elem = xml.etree.ElementTree.SubElement(object_elem, "part", id="1", subtype="normal_part")
            xml.etree.ElementTree.SubElement(part_elem, "metadata", key="name", value=str(blender_object.name))
            matrix_value = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"
            xml.etree.ElementTree.SubElement(part_elem, "metadata", key="matrix", value=matrix_value)

            object_id += 1

        # Add plate metadata
        plate_elem = xml.etree.ElementTree.SubElement(root, "plate")
        xml.etree.ElementTree.SubElement(plate_elem, "metadata", key="plater_id", value="1")
        xml.etree.ElementTree.SubElement(plate_elem, "metadata", key="plater_name", value="")
        xml.etree.ElementTree.SubElement(plate_elem, "metadata", key="locked", value="false")
        xml.etree.ElementTree.SubElement(plate_elem, "metadata", key="filament_map_mode", value="Auto For Flush")

        # Add assemble section
        assemble_elem = xml.etree.ElementTree.SubElement(root, "assemble")
        xml.etree.ElementTree.SubElement(assemble_elem, "assemble_item",
                                         object_id="2", instance_id="0",
                                         transform="1 0 0 0 1 0 0 0 1 0 0 0",
                                         offset="0 0 0")

        tree = xml.etree.ElementTree.ElementTree(root)

        # Convert to string
        output = io.BytesIO()
        tree.write(output, encoding='utf-8', xml_declaration=True)
        return output.getvalue().decode('utf-8')

    def clean_orca_namespaces(self, xml_content: str) -> str:
        """
        Clean up namespace prefixes for Orca Slicer compatibility.

        Python's ElementTree has a bug where it can't properly handle default namespaces,
        resulting in ns0:, ns1: etc. prefixes. Orca Slicer expects clean XML without these.

        :param xml_content: The raw XML string from ElementTree.
        :return: Cleaned XML with proper namespace declarations.
        """
        BAMBU_NAMESPACE = "http://schemas.bambulab.com/package/2021"

        # Remove ns0: prefix from core 3MF elements and attributes
        xml_content = re.sub(r'<ns0:', '<', xml_content)
        xml_content = re.sub(r'</ns0:', '</', xml_content)
        xml_content = re.sub(r' ns0:(\w+)=', r' \1=', xml_content)

        # Fix the xmlns:ns0 declaration to be default xmlns
        xml_content = re.sub(
            r'xmlns:ns0="' + re.escape(MODEL_NAMESPACE) + '"',
            f'xmlns="{MODEL_NAMESPACE}"',
            xml_content
        )

        # Fix BambuStudio namespace - change ns1:BambuStudio to xmlns:BambuStudio
        xml_content = re.sub(
            r'xmlns:ns1="http://www\.w3\.org/2000/xmlns/"',
            '',
            xml_content
        )
        xml_content = re.sub(
            r' ns1:BambuStudio="' + re.escape(BAMBU_NAMESPACE) + '"',
            f' xmlns:BambuStudio="{BAMBU_NAMESPACE}"',
            xml_content
        )

        # Remove duplicate material namespace declaration
        xml_content = re.sub(
            r' ns1:m="' + re.escape(MATERIAL_NAMESPACE) + '"',
            '',
            xml_content
        )

        # Add unit and xml:lang attributes to model element for full Orca compatibility
        xml_content = re.sub(
            r'<model xmlns=',
            '<model unit="millimeter" xml:lang="en-US" xmlns=',
            xml_content
        )

        # Clean up any double spaces from removals
        xml_content = re.sub(r'  +', ' ', xml_content)
        xml_content = re.sub(r' >', '>', xml_content)

        return xml_content

    def unit_scale(self, context: bpy.types.Context) -> float:
        """
        Get the scaling factor we need to transform the document to millimetres.
        :param context: The Blender context to get the unit from.
        :return: Floating point value that we need to scale this model by. A small number (<1) means that we need to
        make the coordinates in the 3MF file smaller than the coordinates in Blender. A large number (>1) means we need
        to make the coordinates in the file larger than the coordinates in Blender.
        """
        scale = self.global_scale

        blender_unit_to_metre = context.scene.unit_settings.scale_length
        if blender_unit_to_metre == 0:  # Fallback for special cases.
            blender_unit = context.scene.unit_settings.length_unit
            blender_unit_to_metre = blender_to_metre[blender_unit]

        threemf_unit = MODEL_DEFAULT_UNIT
        threemf_unit_to_metre = threemf_to_metre[threemf_unit]

        # Scale from Blender scene units to 3MF units.
        scale *= blender_unit_to_metre / threemf_unit_to_metre
        return scale

    def write_materials(self, resources_element: xml.etree.ElementTree.Element,
                        blender_objects: List[bpy.types.Object]) -> Dict[str, int]:
        """
        Write the materials on the specified blender objects to a 3MF document.

        Depending on active extensions, this will write either:
        - Core spec <basematerials> (standard mode): 3MF Core Specification v1.3.0 compliant
        - Materials extension <m:colorgroup> (Orca mode): Vendor-specific for Orca Slicer/BambuStudio

        We'll write all materials to one single <basematerials> tag in the resources (standard mode).
        In Orca mode, each color becomes a separate <m:colorgroup> resource.

        Aside from writing the materials to the document, this function also returns a mapping from the names of the
        materials in Blender (which must be unique) to the index in the <basematerials> material group. Using that
        mapping, the objects and triangles can write down an index referring to the list of <base> tags.

        Since the <base> material can only hold a color, we'll write the diffuse color of the material to the file.
        :param resources_element: A <resources> node from a 3MF document.
        :param blender_objects: A list of Blender objects that may have materials which we need to write to the
        document.
        :return: A mapping from material name to the index of that material in the <basematerials> tag
                 (or colorgroup ID in Orca mode).
        """
        name_to_index = {}  # The output list, mapping from material name to indexes in the <basematerials> tag.
        next_index = 0

        # Create an element lazily. We don't want to create an element if there are no materials to write.
        basematerials_element = None

        # Orca Slicer mode: Use Materials extension m:colorgroup (vendor-specific)
        # Orca/BambuStudio parses <m:colorgroup> with <m:color>, NOT <basematerials>
        # This is a vendor-specific interpretation of the Materials extension
        if self.use_orca_format and self.vertex_colors:
            # Sort colors by their index to maintain consistent ordering
            sorted_colors = sorted(self.vertex_colors.items(), key=lambda x: x[1])

            # Create a colorgroup for each color (Orca expects one color per group)
            for color_hex, color_index in sorted_colors:
                colorgroup_id = self.next_resource_id
                self.next_resource_id += 1

                # Store the first colorgroup ID as our material resource ID
                if color_index == 0:
                    self.material_resource_id = str(colorgroup_id)

                # Create m:colorgroup element
                colorgroup_element = xml.etree.ElementTree.SubElement(
                    resources_element,
                    f"{{{MATERIAL_NAMESPACE}}}colorgroup",
                    attrib={"id": str(colorgroup_id)},
                )
                # Add m:color child with the color
                xml.etree.ElementTree.SubElement(
                    colorgroup_element,
                    f"{{{MATERIAL_NAMESPACE}}}color",
                    attrib={"color": color_hex},
                )
                # Map color hex to colorgroup ID for object/triangle assignment
                name_to_index[color_hex] = colorgroup_id

            log.info(f"Created {len(sorted_colors)} colorgroups for Orca: {name_to_index}")
            return name_to_index

        # Normal material handling (when not in Orca mode)
        for blender_object in blender_objects:
            for material_slot in blender_object.material_slots:
                material = material_slot.material

                # Skip empty material slots
                if material is None:
                    continue

                # Cache material name to protect Unicode characters from garbage collection
                material_name = str(material.name)
                if (
                    material_name in name_to_index
                ):  # Already have this material through another object.
                    continue

                # Wrap this material into a principled render node, to convert its color to sRGB.
                principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
                    material, is_readonly=True
                )
                color = principled.base_color
                red = min(255, round(color[0] * 255))
                green = min(255, round(color[1] * 255))
                blue = min(255, round(color[2] * 255))
                alpha = principled.alpha
                if alpha >= 1.0:  # Completely opaque. Leave out the alpha component.
                    color_hex = "#%0.2X%0.2X%0.2X" % (red, green, blue)
                else:
                    alpha = min(255, round(alpha * 255))
                    color_hex = "#%0.2X%0.2X%0.2X%0.2X" % (red, green, blue, alpha)

                if basematerials_element is None:
                    self.material_resource_id = str(self.next_resource_id)
                    self.next_resource_id += 1
                    basematerials_element = xml.etree.ElementTree.SubElement(
                        resources_element,
                        f"{{{MODEL_NAMESPACE}}}basematerials",
                        attrib={f"{{{MODEL_NAMESPACE}}}id": self.material_resource_id},
                    )
                xml.etree.ElementTree.SubElement(
                    basematerials_element,
                    f"{{{MODEL_NAMESPACE}}}base",
                    attrib={
                        f"{{{MODEL_NAMESPACE}}}name": material_name,
                        f"{{{MODEL_NAMESPACE}}}displaycolor": color_hex,
                    },
                )
                name_to_index[material_name] = next_index
                next_index += 1

        return name_to_index

    def collect_face_colors(self, blender_objects: List[bpy.types.Object]) -> Dict[str, int]:
        """
        Collect unique face colors from all objects for Orca color zone export.

        This extracts colors from material assignments per face. Each face can have its own
        material, allowing solid per-face coloring (perfect for cubes with different colored sides).
        :param blender_objects: List of Blender objects to extract colors from.
        :return: Dictionary mapping color hex strings to filament indices (0-based).
        """
        unique_colors = set()
        objects_processed = 0

        for blender_object in blender_objects:
            if blender_object.type != 'MESH':
                continue

            objects_processed += 1
            log.info(f"Processing object: {blender_object.name}")

            # Get evaluated mesh with modifiers applied
            if self.use_mesh_modifiers:
                dependency_graph = bpy.context.evaluated_depsgraph_get()
                eval_object = blender_object.evaluated_get(dependency_graph)
            else:
                eval_object = blender_object

            try:
                mesh = eval_object.to_mesh()
            except RuntimeError:
                log.warning(f"Could not get mesh for object: {blender_object.name}")
                continue

            if mesh is None:
                log.warning(f"Mesh is None for object: {blender_object.name}")
                continue

            # Extract colors from face material assignments
            log.info(f"Object {blender_object.name}: {len(mesh.vertices)} vertices, {len(mesh.polygons)} faces")

            # Get all materials used by faces
            materials_used = set()
            for poly in mesh.polygons:
                if poly.material_index < len(blender_object.material_slots):
                    materials_used.add(poly.material_index)

            log.info(f"Object uses {len(materials_used)} different materials across its faces")

            # Extract color from each material
            for mat_idx in materials_used:
                if mat_idx < len(blender_object.material_slots):
                    material = blender_object.material_slots[mat_idx].material
                    if material is None:
                        log.warning(f"Material slot {mat_idx} is empty")
                        continue

                    # Try Principled BSDF first for materials with node setup
                    color = None
                    if material.use_nodes and material.node_tree:
                        principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
                            material, is_readonly=True
                        )
                        base_color = principled.base_color
                        # Check if it's not the default gray (0.8, 0.8, 0.8)
                        if (base_color and not (abs(base_color[0] - 0.8) < 0.01
                                                and abs(base_color[1] - 0.8) < 0.01
                                                and abs(base_color[2] - 0.8) < 0.01)):
                            color = base_color

                    # Fall back to diffuse_color for simple materials
                    if color is None:
                        color = material.diffuse_color[:3]
                        log.info(f"Using diffuse_color for material '{material.name}': {color}")

                    red = min(255, round(color[0] * 255))
                    green = min(255, round(color[1] * 255))
                    blue = min(255, round(color[2] * 255))
                    color_hex = "#%0.2X%0.2X%0.2X" % (red, green, blue)
                    unique_colors.add(color_hex)
                    log.info(f"Face color: {color_hex} from material '{material.name}'")

            # Clean up the temporary mesh
            eval_object.to_mesh_clear()

        # Sort colors for consistent ordering and create index mapping
        # IMPORTANT: Start at index 1 because Orca's paint_color codes:
        #   - "" (empty/no attribute) = no paint, use object base material
        #   - "4" = filament 1, "8" = filament 2, etc.
        # So all colored faces need paint_color attributes starting from index 1
        sorted_colors = sorted(unique_colors)
        color_to_index = {color: idx + 1 for idx, color in enumerate(sorted_colors)}

        log.info(f"Collected {len(unique_colors)} unique colors from {objects_processed} objects for Orca export")
        log.info(f"Colors: {sorted_colors}")

        # Report to user
        if objects_processed == 0:
            self.safe_report({'ERROR'}, "No mesh objects found to export!")
        else:
            self.safe_report({'INFO'}, f"Detected {len(unique_colors)} face colors for Orca export: {sorted_colors}")

        return color_to_index

    def get_triangle_color(self, mesh: bpy.types.Mesh, triangle: bpy.types.MeshLoopTriangle,
                           blender_object: bpy.types.Object) -> Optional[str]:
        """
        Get the color for a specific triangle from its face's material assignment.

        :param mesh: The mesh containing the triangle.
        :param triangle: The triangle to get the color for.
        :param blender_object: The object the mesh belongs to.
        :return: Hex color string like "#RRGGBB" or None if no color.
        """
        # Get color from the face's assigned material
        if triangle.material_index < len(blender_object.material_slots):
            material = blender_object.material_slots[triangle.material_index].material
            if material is not None:
                # Try Principled BSDF first for materials with node setup
                color = None
                if material.use_nodes and material.node_tree:
                    principled = bpy_extras.node_shader_utils.PrincipledBSDFWrapper(
                        material, is_readonly=True
                    )
                    base_color = principled.base_color
                    # Check if it's not the default gray (0.8, 0.8, 0.8)
                    if (base_color and not (abs(base_color[0] - 0.8) < 0.01
                                            and abs(base_color[1] - 0.8) < 0.01
                                            and abs(base_color[2] - 0.8) < 0.01)):
                        color = base_color

                # Fall back to diffuse_color for simple materials
                if color is None:
                    color = material.diffuse_color[:3]

                red = min(255, round(color[0] * 255))
                green = min(255, round(color[1] * 255))
                blue = min(255, round(color[2] * 255))
                return "#%0.2X%0.2X%0.2X" % (red, green, blue)

        return None

    def write_objects(self, root: xml.etree.ElementTree.Element,
                      resources_element: xml.etree.ElementTree.Element,
                      blender_objects: List[bpy.types.Object],
                      global_scale: float) -> None:
        """
        Writes a group of objects into the 3MF archive.
        :param root: An XML root element to write the objects into.
        :param resources_element: An XML element to write resources into.
        :param blender_objects: A list of Blender objects that need to be written to that XML element.
        :param global_scale: A scaling factor to apply to all objects to convert the units.
        """
        transformation = mathutils.Matrix.Scale(global_scale, 4)

        build_element = xml.etree.ElementTree.SubElement(
            root, f"{{{MODEL_NAMESPACE}}}build"
        )
        hidden_skipped = 0
        for blender_object in blender_objects:
            if blender_object.hide_get() and not self.export_hidden:
                # Do not export hidden objects
                hidden_skipped += 1
                continue
            if blender_object.parent is not None:
                continue  # Only write objects that have no parent, since we'll get the child objects recursively.
            if blender_object.type not in {"MESH", "EMPTY"}:
                continue

            objectid, mesh_transformation = self.write_object_resource(
                resources_element, blender_object
            )

            item_element = xml.etree.ElementTree.SubElement(
                build_element, f"{{{MODEL_NAMESPACE}}}item"
            )
            self.num_written += 1
            item_element.attrib[self.attr("objectid")] = str(objectid)
            mesh_transformation = transformation @ mesh_transformation
            if mesh_transformation != mathutils.Matrix.Identity(4):
                item_element.attrib[self.attr("transform")] = (
                    self.format_transformation(mesh_transformation)
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
                self.write_metadata(metadatagroup_element, metadata)

        # Notify user if hidden objects were skipped
        if hidden_skipped > 0:
            self.safe_report(
                {'INFO'},
                f"Skipped {hidden_skipped} hidden object(s). "
                "Enable 'Include Hidden' to export them."
            )

    def write_object_resource(self, resources_element: xml.etree.ElementTree.Element,
                              blender_object: bpy.types.Object) -> Tuple[int, mathutils.Matrix]:
        """
        Write a single Blender object and all of its children to the resources of a 3MF document.

        If the object contains a mesh it'll get written to the document as an object with a mesh resource. If the object
        contains children it'll get written to the document as an object with components. If the object contains both,
        two objects will be written; one with the mesh and another with the components. The mesh then gets added as a
        component of the object with components.
        :param resources_element: The <resources> element of the 3MF document to write into.
        :param blender_object: A Blender object to write to that XML element.
        :return: A tuple, containing the object ID of the newly written resource and a transformation matrix that this
        resource must be saved with.
        """
        log.info(f"write_object_resource called for: {blender_object.name}, type: {blender_object.type}")

        new_resource_id = self.next_resource_id
        self.next_resource_id += 1
        object_element = xml.etree.ElementTree.SubElement(
            resources_element, f"{{{MODEL_NAMESPACE}}}object"
        )
        object_element.attrib[self.attr("id")] = str(new_resource_id)
        # Cache object name to protect Unicode characters from garbage collection
        object_name = str(blender_object.name)
        object_element.attrib[self.attr("name")] = object_name

        metadata = Metadata()
        metadata.retrieve(blender_object)
        if "3mf:object_type" in metadata:
            object_type = metadata["3mf:object_type"].value
            if object_type != "model":  # Only write if not the default.
                object_element.attrib[self.attr("type")] = object_type
            del metadata["3mf:object_type"]

        if blender_object.mode == "EDIT":
            blender_object.update_from_editmode()  # Apply recent changes made to the model.
        mesh_transformation = blender_object.matrix_world

        child_objects = blender_object.children
        if (
            child_objects
        ):  # Only write the <components> tag if there are actually components.
            components_element = xml.etree.ElementTree.SubElement(
                object_element, f"{{{MODEL_NAMESPACE}}}components"
            )
            for child in blender_object.children:
                if child.type != "MESH":
                    continue
                # Recursively write children to the resources.
                child_id, child_transformation = self.write_object_resource(
                    resources_element, child
                )
                # Use pseudo-inverse for safety, but the epsilon then doesn't matter since it'll get multiplied by 0
                # later anyway then.
                child_transformation = (
                    mesh_transformation.inverted_safe() @ child_transformation
                )
                component_element = xml.etree.ElementTree.SubElement(
                    components_element, f"{{{MODEL_NAMESPACE}}}component"
                )
                self.num_written += 1
                component_element.attrib[self.attr("objectid")] = str(
                    child_id
                )
                if child_transformation != mathutils.Matrix.Identity(4):
                    component_element.attrib[self.attr("transform")] = (
                        self.format_transformation(child_transformation)
                    )

        # In the tail recursion, get the vertex data.
        # This is necessary because we may need to apply the mesh modifiers, which causes these objects to lose their
        # children.
        if self.use_mesh_modifiers:
            dependency_graph = bpy.context.evaluated_depsgraph_get()
            blender_object = blender_object.evaluated_get(dependency_graph)

        try:
            mesh = blender_object.to_mesh()
        except (
            RuntimeError
        ):  # Object.to_mesh() is not guaranteed to return Optional[Mesh], apparently.
            return new_resource_id, mesh_transformation
        if mesh is None:
            return new_resource_id, mesh_transformation

        # Need to convert this to triangles-only, because 3MF doesn't support faces with more than 3 vertices.
        mesh.calc_loop_triangles()

        log.info(f"  Got mesh: {len(mesh.vertices)} vertices, {len(mesh.loop_triangles)} triangles")

        if len(mesh.vertices) > 0:  # Only write a <mesh> tag if there is mesh data.
            # If this object already contains components, we can't also store a mesh. So create a new object and use
            # that object as another component.
            if child_objects:
                mesh_id = self.next_resource_id
                self.next_resource_id += 1
                mesh_object_element = xml.etree.ElementTree.SubElement(
                    resources_element, f"{{{MODEL_NAMESPACE}}}object"
                )
                mesh_object_element.attrib[self.attr("id")] = str(mesh_id)
                component_element = xml.etree.ElementTree.SubElement(
                    components_element, f"{{{MODEL_NAMESPACE}}}component"
                )
                self.num_written += 1
                component_element.attrib[self.attr("objectid")] = str(
                    mesh_id
                )
            else:  # No components, then we can write directly into this object resource.
                mesh_object_element = object_element
            mesh_element = xml.etree.ElementTree.SubElement(
                mesh_object_element, f"{{{MODEL_NAMESPACE}}}mesh"
            )

            # Find the most common material for this mesh, for maximum compression.
            most_common_material_list_index = 0

            log.info(f"write_object_resource: {blender_object.name}, Orca={self.use_orca_format}, "
                     f"vertex_colors={self.vertex_colors}")
            log.info(f"  mesh has {len(mesh.loop_triangles)} triangles, "
                     f"{len(blender_object.material_slots)} material slots")

            # In Orca mode, use face colors mapped to colorgroup IDs
            if self.use_orca_format and self.vertex_colors:
                # Find the most common color for this object
                color_counts = {}
                for triangle in mesh.loop_triangles:
                    triangle_color = self.get_triangle_color(mesh, triangle, blender_object)
                    log.info(f"  triangle {triangle.index}: material_index={triangle.material_index}, "
                             f"color={triangle_color}")
                    if triangle_color and triangle_color in self.vertex_colors:
                        color_counts[triangle_color] = color_counts.get(triangle_color, 0) + 1

                log.info(f"  color_counts: {color_counts}")
                if color_counts:
                    most_common_color = max(color_counts, key=color_counts.get)
                    # In Orca mode, vertex_colors maps color_hex -> colorgroup_id
                    colorgroup_id = self.vertex_colors[most_common_color]
                    # pid references the colorgroup directly in Orca format
                    object_element.attrib[self.attr("pid")] = str(colorgroup_id)
                    # pindex is 0 since each colorgroup has only one color
                    object_element.attrib[self.attr("pindex")] = "0"
                    most_common_material_list_index = colorgroup_id  # For triangle overrides
            else:
                # Normal material handling
                material_indices = [
                    triangle.material_index for triangle in mesh.loop_triangles
                ]

                if material_indices and blender_object.material_slots:
                    counter = collections.Counter(material_indices)
                    # most_common_material_object_index is an index from the MeshLoopTriangle, referring to the list of
                    # materials attached to the Blender object.
                    most_common_material_object_index = counter.most_common(1)[0][0]
                    most_common_material = blender_object.material_slots[
                        most_common_material_object_index
                    ].material

                    # Only proceed if the most common material slot is not empty
                    if most_common_material is not None:
                        # most_common_material_list_index is an index referring to our
                        # own list of materials that we put in the resources.
                        most_common_material_list_index = self.material_name_to_index[
                            most_common_material.name
                        ]
                        # We always only write one group of materials.
                        # The resource ID was determined when it was written.
                        object_element.attrib[self.attr("pid")] = str(
                            self.material_resource_id
                        )
                        object_element.attrib[self.attr("pindex")] = str(
                            most_common_material_list_index
                        )

            self.write_vertices(mesh_element, mesh.vertices)
            self.write_triangles(
                mesh_element,
                mesh.loop_triangles,
                most_common_material_list_index,
                blender_object.material_slots,
                mesh,
                blender_object,
            )

            # If the object has metadata, write that to a metadata object.
            if "3mf:partnumber" in metadata:
                mesh_object_element.attrib[self.attr("partnumber")] = (
                    metadata["3mf:partnumber"].value
                )
                del metadata["3mf:partnumber"]
            if "3mf:object_type" in metadata:
                object_type = metadata["3mf:object_type"].value
                if object_type != "model" and object_type != "other":
                    # Only write if not the default.
                    # Don't write "other" object types since we're not allowed to refer to them. Pretend they are normal
                    # models.
                    mesh_object_element.attrib[self.attr("type")] = (
                        object_type
                    )
                del metadata["3mf:object_type"]
            if metadata:
                metadatagroup_element = xml.etree.ElementTree.SubElement(
                    object_element, f"{{{MODEL_NAMESPACE}}}metadatagroup"
                )
                self.write_metadata(metadatagroup_element, metadata)

        return new_resource_id, mesh_transformation

    def write_metadata(self, node: xml.etree.ElementTree.Element, metadata: Metadata) -> None:
        """
        Writes metadata from a metadata storage into an XML node.
        :param node: The node to add <metadata> tags to.
        :param metadata: The collection of metadata to write to that node.
        """
        for metadata_entry in metadata.values():
            metadata_node = xml.etree.ElementTree.SubElement(
                node, f"{{{MODEL_NAMESPACE}}}metadata"
            )
            # Cache metadata name and value to protect Unicode characters from garbage collection
            metadata_name = str(metadata_entry.name)
            metadata_value = str(metadata_entry.value) if metadata_entry.value is not None else ""
            metadata_node.attrib[self.attr("name")] = metadata_name
            if metadata_entry.preserve:
                metadata_node.attrib[self.attr("preserve")] = "1"
            if metadata_entry.datatype:
                # Cache datatype as well
                metadata_datatype = str(metadata_entry.datatype)
                metadata_node.attrib[self.attr("type")] = metadata_datatype
            metadata_node.text = metadata_value

    def format_transformation(self, transformation: mathutils.Matrix) -> str:
        """
        Formats a transformation matrix in 3MF's formatting.

        This transformation matrix can then be written to an attribute.
        :param transformation: The transformation matrix to format.
        :return: A serialisation of the transformation matrix.
        """
        pieces = (
            row[:3] for row in transformation.transposed()
        )  # Don't convert the 4th column.
        formatted_cells = [
            f"{cell:.9f}" for cell in itertools.chain.from_iterable(pieces)
        ]
        return " ".join(formatted_cells)

    def write_vertices(self, mesh_element: xml.etree.ElementTree.Element,
                       vertices: List[bpy.types.MeshVertex]) -> None:
        """
        Writes a list of vertices into the specified mesh element.

        This then becomes a resource that can be used in a build.
        :param mesh_element: The <mesh> element of the 3MF document.
        :param vertices: A list of Blender vertices to add.
        """
        vertices_element = xml.etree.ElementTree.SubElement(
            mesh_element, f"{{{MODEL_NAMESPACE}}}vertices"
        )

        # Precompute some names for better performance.
        # Note: In Orca mode, use plain attribute names (no namespace prefix)
        # because XML attributes don't inherit default namespace
        vertex_name = f"{{{MODEL_NAMESPACE}}}vertex"
        if self.use_orca_format:
            x_name = "x"
            y_name = "y"
            z_name = "z"
        else:
            x_name = f"{{{MODEL_NAMESPACE}}}x"
            y_name = f"{{{MODEL_NAMESPACE}}}y"
            z_name = f"{{{MODEL_NAMESPACE}}}z"

        decimals = self.coordinate_precision
        for vertex in vertices:  # Create the <vertex> elements.
            vertex_element = xml.etree.ElementTree.SubElement(
                vertices_element, vertex_name
            )
            vertex_element.attrib[x_name] = f"{vertex.co[0]:.{decimals}}"
            vertex_element.attrib[y_name] = f"{vertex.co[1]:.{decimals}}"
            vertex_element.attrib[z_name] = f"{vertex.co[2]:.{decimals}}"

    def write_triangles(
        self, mesh_element: xml.etree.ElementTree.Element,
        triangles: List[bpy.types.MeshLoopTriangle],
        object_material_list_index: int,
        material_slots: List[bpy.types.MaterialSlot],
        mesh: Optional[bpy.types.Mesh] = None,
        blender_object: Optional[bpy.types.Object] = None
    ) -> None:
        """
        Writes a list of triangles into the specified mesh element.

        This then becomes a resource that can be used in a build.
        :param mesh_element: The <mesh> element of the 3MF document.
        :param triangles: A list of triangles. Each list is a list of indices to the list of vertices.
        :param object_material_list_index: The index of the material that the object was written with to which these
        triangles belong. If the triangle has a different index, we need to write the index with the triangle.
        :param material_slots: List of materials belonging to the object for which we write triangles. These are
        necessary to interpret the material indices stored in the MeshLoopTriangles.
        :param mesh: The mesh containing these triangles (for vertex color extraction).
        :param blender_object: The Blender object (for color extraction).
        """
        triangles_element = xml.etree.ElementTree.SubElement(
            mesh_element, f"{{{MODEL_NAMESPACE}}}triangles"
        )

        # Precompute some names for better performance.
        # Note: In Orca mode, use plain attribute names (no namespace prefix)
        triangle_name = f"{{{MODEL_NAMESPACE}}}triangle"
        if self.use_orca_format:
            v1_name = "v1"
            v2_name = "v2"
            v3_name = "v3"
            p1_name = "p1"
            pid_name = "pid"
        else:
            v1_name = f"{{{MODEL_NAMESPACE}}}v1"
            v2_name = f"{{{MODEL_NAMESPACE}}}v2"
            v3_name = f"{{{MODEL_NAMESPACE}}}v3"
            p1_name = f"{{{MODEL_NAMESPACE}}}p1"
            pid_name = f"{{{MODEL_NAMESPACE}}}pid"

        for triangle in triangles:
            triangle_element = xml.etree.ElementTree.SubElement(
                triangles_element, triangle_name
            )
            triangle_element.attrib[v1_name] = str(triangle.vertices[0])
            triangle_element.attrib[v2_name] = str(triangle.vertices[1])
            triangle_element.attrib[v3_name] = str(triangle.vertices[2])

            # Handle Orca color zones if enabled
            if self.use_orca_format and self.vertex_colors and mesh and blender_object:
                triangle_color = self.get_triangle_color(mesh, triangle, blender_object)
                if triangle_color and triangle_color in self.vertex_colors:
                    colorgroup_id = self.vertex_colors[triangle_color]
                    # pid references the colorgroup ID, p1 is 0 (first color in group)
                    triangle_element.attrib[pid_name] = str(colorgroup_id)
                    triangle_element.attrib[p1_name] = "0"

                    # Add paint_color attribute for Orca's per-triangle coloring
                    # This is the MMU segmentation data that Orca uses for visual display
                    # The colorgroup_id is 1-based, matching ORCA_FILAMENT_CODES indices
                    if colorgroup_id < len(ORCA_FILAMENT_CODES):
                        paint_code = ORCA_FILAMENT_CODES[colorgroup_id]
                        if paint_code:  # Don't add empty paint_color
                            triangle_element.attrib["paint_color"] = paint_code
            elif triangle.material_index < len(material_slots):
                # Normal material handling (only if not in Orca mode)
                # Check if the material slot is not empty
                triangle_material = material_slots[triangle.material_index].material
                if triangle_material is not None:
                    # Convert to index in our global list.
                    # Cache material name to protect Unicode characters from garbage collection
                    triangle_material_name = str(triangle_material.name)
                    material_index = self.material_name_to_index[
                        triangle_material_name
                    ]
                    if material_index != object_material_list_index:
                        # Not equal to the index that our parent object was written with, so we must override it here.
                        triangle_element.attrib[p1_name] = str(material_index)
