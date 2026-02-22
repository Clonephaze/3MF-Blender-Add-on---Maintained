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

"""
Operators for the MMU Paint suite.

All ``MMU_OT_*`` operators that drive the painting workflow — initialization,
filament management, brush control, color reassignment, and the post-import
popup.
"""

import bmesh
import numpy as np
import bpy
import bpy.props
import bpy.types

from ..common.colors import rgb_to_hex as _hex_from_rgb
from ..common.logging import debug

from .helpers import (
    DEFAULT_PALETTE,
    _get_paint_image,
    _get_paint_mesh,
    _sync_filaments_from_mesh,
    _write_colors_to_mesh,
    _configure_paint_brush,
    _set_brush_color,
    _has_vertex_colors,
)
from .color_detection import (
    _collect_material_colors,
    _get_any_image_texture,
    _has_color_attribute_node,
    _extract_texture_colors,
    _extract_vertex_colors,
)


# ===================================================================
#  Initialization operators
# ===================================================================


class MMU_OT_initialize(bpy.types.Operator):
    """Initialize MMU painting on the active mesh object"""

    bl_idname = "mmu.initialize_painting"
    bl_label = "Initialize MMU Painting"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and not obj.data.get("3mf_is_paint_texture")
        )

    def execute(self, context):
        # Push a single undo step so Ctrl+Z restores the entire
        # pre-initialization state in one go (mode_set and UV ops
        # inside this method would otherwise fragment the undo stack).
        bpy.ops.ed.undo_push(message="Before MMU Initialize")

        obj = context.active_object
        mesh = obj.data
        settings = context.scene.mmu_paint

        # Use init_filaments for colors
        if len(settings.init_filaments) < 2:
            self.report({"ERROR"}, "At least 2 filaments required")
            return {"CANCELLED"}

        # --- Create dedicated MMU_Paint UV layer ---
        mmu_layer = mesh.uv_layers.get("MMU_Paint")
        if mmu_layer is None:
            mmu_layer = mesh.uv_layers.new(name="MMU_Paint")
        mesh.uv_layers.active = mmu_layer
        mmu_layer.active_render = True

        context.view_layer.objects.active = obj
        uv_method = settings.uv_method

        # Limited Dissolve merges coplanar triangles, giving each face
        # more UV space and reducing blurriness.  ~2 deg is conservative
        # enough to keep all intentional geometry detail.
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.dissolve_limit(
            bm, angle_limit=0.0349,
            verts=bm.verts, edges=bm.edges,
        )
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()

        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")

        if uv_method == "LIGHTMAP":
            bpy.ops.uv.lightmap_pack(
                PREF_CONTEXT="ALL_FACES",
                PREF_PACK_IN_ONE=True,
                PREF_NEW_UVLAYER=False,
                PREF_BOX_DIV=settings.lightmap_divisions,
                PREF_MARGIN_DIV=0.05,
            )
        else:
            bpy.ops.uv.smart_project(
                angle_limit=1.15192,
                margin_method="SCALED",
                rotate_method="AXIS_ALIGNED",
                island_margin=0.002,
                area_weight=0.6,
                correct_aspect=True,
                scale_to_bounds=False,
            )

        bpy.ops.object.mode_set(mode="OBJECT")

        # --- Texture size by triangle count ---
        tri_count = len(mesh.polygons)
        if tri_count < 5000:
            texture_size = 2048
        elif tri_count < 20000:
            texture_size = 4096
        else:
            texture_size = 8192

        # Get base color from first init filament
        base_color = tuple(settings.init_filaments[0].color[:])

        # --- Create image filled with base color ---
        image_name = f"{mesh.name}_MMU_Paint"
        image = bpy.data.images.new(
            image_name, width=texture_size, height=texture_size, alpha=True
        )
        # Fill entire image with base color
        fill = np.empty((texture_size, texture_size, 4), dtype=np.float32)
        fill[:, :, 0] = base_color[0]
        fill[:, :, 1] = base_color[1]
        fill[:, :, 2] = base_color[2]
        fill[:, :, 3] = 1.0
        image.pixels.foreach_set(fill.ravel())
        image.pack()

        # --- Material setup ---
        mat = bpy.data.materials.new(name=image_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        nodes.clear()

        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        tex_node.location = (-300, 0)

        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (100, 0)

        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)

        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        # Clear existing materials, assign ours
        mesh.materials.clear()
        mesh.materials.append(mat)
        num_faces = len(mesh.polygons)
        if num_faces > 0:
            material_indices = [0] * num_faces
            mesh.polygons.foreach_set("material_index", material_indices)

        # --- Build palette from init_filaments ---
        colors_dict = {}
        for i, item in enumerate(settings.init_filaments):
            colors_dict[i] = _hex_from_rgb(*item.color[:])

        # --- Store custom properties ---
        mesh["3mf_is_paint_texture"] = True
        mesh["3mf_paint_default_extruder"] = 1  # 1-based
        mesh["3mf_paint_extruder_colors"] = str(colors_dict)

        # --- Populate panel filaments ---
        settings.loaded_mesh_name = ""  # Force reload
        _sync_filaments_from_mesh(context)

        # Set active node so texture paint knows which image to paint on
        if mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE":
                    mat.node_tree.nodes.active = node
                    break

        # Switch to Texture Paint mode FIRST -- ts.image_paint / brush
        # are not reliably available until we're in paint mode.
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")

        # --- Setup brush and canvas (must be in TEXTURE_PAINT mode) ---
        _configure_paint_brush(context)

        ts = context.tool_settings
        if hasattr(ts.image_paint, "canvas"):
            ts.image_paint.canvas = image

        if len(settings.filaments) > 0:
            settings.active_filament_index = 0
            _set_brush_color(context, settings.filaments[0].color[:])

        count = len(settings.init_filaments)
        self.report(
            {"INFO"},
            f"Initialized MMU painting with {count} filaments at {texture_size}x{texture_size}",
        )
        return {"FINISHED"}


class MMU_OT_add_init_filament(bpy.types.Operator):
    """Add a filament to the initialization list"""

    bl_idname = "mmu.add_init_filament"
    bl_label = "Add Filament"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        settings = context.scene.mmu_paint

        if len(settings.init_filaments) >= 16:
            self.report({"ERROR"}, "Maximum 16 filaments supported")
            return {"CANCELLED"}

        idx = len(settings.init_filaments)
        item = settings.init_filaments.add()
        item.name = f"Filament {idx + 1}"

        # Pick color from palette
        if idx < len(DEFAULT_PALETTE):
            item.color = DEFAULT_PALETTE[idx]
        else:
            item.color = DEFAULT_PALETTE[idx % len(DEFAULT_PALETTE)]

        return {"FINISHED"}


class MMU_OT_remove_init_filament(bpy.types.Operator):
    """Remove the selected filament from the initialization list"""

    bl_idname = "mmu.remove_init_filament"
    bl_label = "Remove Filament"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        settings = context.scene.mmu_paint

        if len(settings.init_filaments) <= 2:
            self.report({"ERROR"}, "Minimum 2 filaments required")
            return {"CANCELLED"}

        idx = settings.active_init_filament_index
        if idx < 0 or idx >= len(settings.init_filaments):
            return {"CANCELLED"}

        settings.init_filaments.remove(idx)

        # Rename remaining filaments
        for i, item in enumerate(settings.init_filaments):
            item.name = f"Filament {i + 1}"

        # Clamp selection
        if settings.active_init_filament_index >= len(settings.init_filaments):
            settings.active_init_filament_index = len(settings.init_filaments) - 1

        return {"FINISHED"}


class MMU_OT_reset_init_filaments(bpy.types.Operator):
    """Reset initialization filaments to default 4-color palette"""

    bl_idname = "mmu.reset_init_filaments"
    bl_label = "Reset to Defaults"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        settings = context.scene.mmu_paint
        settings.init_filaments.clear()

        # Create default 4 filaments
        for i in range(4):
            item = settings.init_filaments.add()
            item.name = f"Filament {i + 1}"
            item.color = DEFAULT_PALETTE[i]

        settings.active_init_filament_index = 0
        return {"FINISHED"}


class MMU_OT_detect_material_colors(bpy.types.Operator):
    """Detect colors from the active object's material setup and populate the filament list"""

    bl_idname = "mmu.detect_material_colors"
    bl_label = "Detect from Materials"
    bl_description = (
        "Scan the active object's shader node trees for colors.\n"
        "Reads Color Ramp stops, Principled BSDF Base Color, RGB nodes,\n"
        "and viewport display colors, then populates the filament list.\n"
        "If an image texture or vertex colors are detected, prompts for\n"
        "the number of dominant colors to extract"
    )
    bl_options = {"INTERNAL"}

    num_colors: bpy.props.IntProperty(
        name="Number of Colors",
        description="How many dominant colors to extract from the texture",
        default=4,
        min=2,
        max=16,
    )

    # Internal: which source type was detected
    _source: str = "NODES"  # "NODES", "IMAGE", or "VERTEX"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            return False
        if obj.data.get("3mf_is_paint_texture"):
            return False
        # Allow if object has materials OR vertex colors
        has_materials = bool(obj.data.materials)
        has_vertex = (
            hasattr(obj.data, "color_attributes")
            and len(obj.data.color_attributes) > 0
        )
        return has_materials or has_vertex

    def invoke(self, context, event):
        obj = context.active_object

        # Check for image texture on active material
        image = _get_any_image_texture(obj)
        debug(f"[Detect] _get_any_image_texture -> {image}")
        if image is not None:
            self._source = "IMAGE"
            debug(f"[Detect] Source = IMAGE, image = '{image.name}' ({image.size[0]}x{image.size[1]})")
            return context.window_manager.invoke_props_dialog(
                self, title="Detect Colors from Image Texture",
            )

        # Check for vertex colors -- either via color attributes on the
        # mesh or a Color Attribute node feeding a Principled BSDF
        has_vc = _has_vertex_colors(obj)
        has_ca_node = _has_color_attribute_node(obj)
        debug(f"[Detect] _has_vertex_colors -> {has_vc}, _has_color_attribute_node -> {has_ca_node}")
        if has_vc or has_ca_node:
            self._source = "VERTEX"
            debug("[Detect] Source = VERTEX")
            return context.window_manager.invoke_props_dialog(
                self, title="Detect Colors from Vertex Colors",
            )

        # No texture sources -- run node detection immediately
        self._source = "NODES"
        debug("[Detect] Source = NODES (fallback to shader node detection)")
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        if self._source == "IMAGE":
            layout.label(text="Image texture detected on this object.")
        else:
            layout.label(text="Vertex color data detected on this object.")
        layout.label(text="How many dominant colors to extract?")
        layout.separator()
        layout.prop(self, "num_colors", slider=True)

    def execute(self, context):
        obj = context.active_object
        settings = context.scene.mmu_paint
        debug(f"[Detect] execute() _source={self._source}, num_colors={self.num_colors}")

        # --- Texture-based detection ---
        if self._source == "IMAGE":
            image = _get_any_image_texture(obj)
            if image is None:
                self.report({"WARNING"}, "No image texture found")
                return {"CANCELLED"}
            colors = _extract_texture_colors(image, self.num_colors)
            source_label = f"image texture '{image.name}'"

        elif self._source == "VERTEX":
            colors = _extract_vertex_colors(obj, self.num_colors)
            source_label = "vertex colors"

        else:
            # Node-tree detection (original behavior)
            colors = _collect_material_colors(obj)
            source_label = "materials"

        debug(f"[Detect] Got {len(colors)} colors from {self._source}:")
        for i, c in enumerate(colors):
            debug(f"  [{i}] sRGB ({c[0]:.3f}, {c[1]:.3f}, {c[2]:.3f})  ~  {_hex_from_rgb(c[0], c[1], c[2])}")

        if not colors:
            self.report({"WARNING"}, f"No colors detected from {source_label}")
            return {"CANCELLED"}

        # Clamp to 16 filaments max
        if len(colors) > 16:
            colors = colors[:16]

        # Clear existing init filaments and populate with detected colors
        settings.init_filaments.clear()
        for i, rgb in enumerate(colors):
            item = settings.init_filaments.add()
            item.name = f"Filament {i + 1}"
            item.color = rgb

        settings.active_init_filament_index = 0
        self.report({"INFO"}, f"Detected {len(colors)} colors from {source_label}")

        # Force panel redraw so the color swatches update immediately
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
            elif area.type == "PROPERTIES":
                area.tag_redraw()

        return {"FINISHED"}


# ===================================================================
#  Runtime painting operators
# ===================================================================


class MMU_OT_select_filament(bpy.types.Operator):
    """Select a filament and set it as the active brush color"""

    bl_idname = "mmu.select_filament"
    bl_label = "Select Filament"
    bl_options = {"INTERNAL"}

    index: bpy.props.IntProperty()

    def execute(self, context):
        settings = context.scene.mmu_paint
        if 0 <= self.index < len(settings.filaments):
            settings.active_filament_index = self.index
            _set_brush_color(context, settings.filaments[self.index].color[:])
        return {"FINISHED"}


class MMU_OT_add_filament(bpy.types.Operator):
    """Add a new filament to the palette"""

    bl_idname = "mmu.add_filament"
    bl_label = "Add Filament"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        mesh = _get_paint_mesh(context)
        if mesh is None:
            return False
        settings = context.scene.mmu_paint
        return len(settings.filaments) < 16

    def execute(self, context):
        settings = context.scene.mmu_paint
        count = len(settings.filaments)

        if count >= 16:
            self.report({"ERROR"}, "Maximum 16 filaments supported")
            return {"CANCELLED"}

        # Pick a default color from the palette
        new_index = count
        if new_index < len(DEFAULT_PALETTE):
            new_color = DEFAULT_PALETTE[new_index]
        else:
            new_color = DEFAULT_PALETTE[new_index % len(DEFAULT_PALETTE)]

        item = settings.filaments.add()
        item.index = new_index
        item.name = f"Filament {new_index + 1}"
        item.color = new_color

        _write_colors_to_mesh(context)

        self.report(
            {"WARNING"},
            f"Added filament {new_index + 1}. "
            f"Ensure your printer profile supports {count + 1} filaments.",
        )
        return {"FINISHED"}


class MMU_OT_remove_filament(bpy.types.Operator):
    """Remove the selected filament from the palette"""

    bl_idname = "mmu.remove_filament"
    bl_label = "Remove Filament"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        mesh = _get_paint_mesh(context)
        if mesh is None:
            return False
        settings = context.scene.mmu_paint
        return len(settings.filaments) > 2

    def execute(self, context):
        settings = context.scene.mmu_paint
        idx = settings.active_filament_index
        if idx < 0 or idx >= len(settings.filaments):
            return {"CANCELLED"}

        if len(settings.filaments) <= 2:
            self.report({"ERROR"}, "Minimum 2 filaments required")
            return {"CANCELLED"}

        removed = settings.filaments[idx]
        removed_color = tuple(removed.color[:])

        # Determine the new base color (what will be filament 0 after removal).
        # If removing filament 0, the new base is current filament 1.
        # Otherwise, the base stays filament 0.
        if idx == 0:
            new_base_color = tuple(settings.filaments[1].color[:])
        else:
            new_base_color = tuple(settings.filaments[0].color[:])

        # Replace all pixels of the removed color with the new base color
        obj = context.active_object
        image = _get_paint_image(obj)
        replaced_count = 0

        if image is not None:
            w, h = image.size
            pixels_flat = np.empty(w * h * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels_flat)
            pixels = pixels_flat.reshape(h, w, 4)

            old_arr = np.array(removed_color, dtype=np.float32)
            new_arr = np.array(new_base_color, dtype=np.float32)

            tolerance = 3.0 / 255.0
            mask = np.all(np.abs(pixels[:, :, :3] - old_arr) < tolerance, axis=2)
            replaced_count = int(np.count_nonzero(mask))

            if replaced_count > 0:
                pixels[mask, 0] = new_arr[0]
                pixels[mask, 1] = new_arr[1]
                pixels[mask, 2] = new_arr[2]
                image.pixels.foreach_set(pixels.ravel())
                image.update()

        settings.filaments.remove(idx)

        # Re-index remaining filaments
        for i, item in enumerate(settings.filaments):
            item.index = i
            item.name = f"Filament {i + 1}"

        # Clamp selection
        if settings.active_filament_index >= len(settings.filaments):
            settings.active_filament_index = len(settings.filaments) - 1

        _write_colors_to_mesh(context)

        if replaced_count > 0:
            self.report(
                {"INFO"}, f"Removed filament and replaced {replaced_count} pixels"
            )
        else:
            self.report({"INFO"}, "Removed filament")

        msg = f"Removed filament. {len(settings.filaments)} remaining."
        if replaced_count > 0:
            msg += f" Replaced {replaced_count} painted pixels with base color."
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class MMU_OT_fix_falloff(bpy.types.Operator):
    """Set brush falloff to Constant to prevent banding on export"""

    bl_idname = "mmu.fix_falloff"
    bl_label = "Fix Brush Falloff"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        brush = context.tool_settings.image_paint.brush
        if brush:
            brush.curve_distance_falloff_preset = "CONSTANT"
            self.report({"INFO"}, "Brush falloff set to Constant")
        return {"FINISHED"}


class MMU_OT_switch_to_paint(bpy.types.Operator):
    """Switch to Texture Paint mode and open the MMU Paint panel"""

    bl_idname = "mmu.switch_to_paint"
    bl_label = "Open MMU Paint Mode"
    bl_description = "Switch to Texture Paint mode to paint multi-material regions"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"WARNING"}, "Select a mesh object first")
            return {"CANCELLED"}

        # Switch to texture paint
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")

        # Setup brush
        _configure_paint_brush(context)
        ts = context.tool_settings

        # Select the paint image
        image = _get_paint_image(obj)
        if image and hasattr(ts.image_paint, "canvas"):
            ts.image_paint.canvas = image

        # Set active node
        if obj.data.materials:
            mat = obj.data.materials[0]
            if mat and mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type == "TEX_IMAGE":
                        mat.node_tree.nodes.active = node
                        break

        # Sync filament palette
        _sync_filaments_from_mesh(context)

        # Set brush to first filament color
        settings = context.scene.mmu_paint
        if len(settings.filaments) > 0:
            _set_brush_color(context, settings.filaments[0].color[:])

        # Try to open the sidebar panel
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                for space in area.spaces:
                    if space.type == "VIEW_3D":
                        space.show_region_ui = True
                break

        return {"FINISHED"}


class MMU_OT_reassign_filament_color(bpy.types.Operator):
    """Reassign a filament color -- replaces all pixels of old color with new color"""

    bl_idname = "mmu.reassign_filament_color"
    bl_label = "Reassign Filament Color"
    bl_options = {"REGISTER", "UNDO"}

    new_color: bpy.props.FloatVectorProperty(
        name="New Color",
        subtype="COLOR_GAMMA",
        size=3,
        min=0.0,
        max=1.0,
        default=(1.0, 1.0, 1.0),
        description="New color to replace the current filament color",
    )

    @classmethod
    def poll(cls, context):
        mesh = _get_paint_mesh(context)
        if mesh is None:
            return False
        settings = context.scene.mmu_paint
        return len(settings.filaments) > 0 and settings.active_filament_index < len(
            settings.filaments
        )

    def invoke(self, context, event):
        settings = context.scene.mmu_paint
        idx = settings.active_filament_index
        if idx < len(settings.filaments):
            # Initialize color picker with current color
            self.new_color = settings.filaments[idx].color[:]
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mmu_paint
        idx = settings.active_filament_index

        if idx < len(settings.filaments):
            item = settings.filaments[idx]
            layout.label(text=f"Reassigning {item.name}")
            layout.label(
                text="This will replace all pixels of the current color", icon="INFO"
            )
            layout.label(text="with the new color you choose.")
            layout.separator()
            layout.prop(self, "new_color", text="New Color")

    def execute(self, context):
        settings = context.scene.mmu_paint
        idx = settings.active_filament_index

        if idx >= len(settings.filaments):
            return {"CANCELLED"}

        item = settings.filaments[idx]
        obj = context.active_object
        image = _get_paint_image(obj)
        if image is None:
            self.report({"WARNING"}, "No paint texture found")
            return {"CANCELLED"}

        old_rgb = tuple(item.color[:])
        new_rgb = tuple(self.new_color[:])

        # Skip if colors are identical
        if all(abs(o - n) < 0.002 for o, n in zip(old_rgb, new_rgb)):
            return {"CANCELLED"}

        # Bulk pixel replacement
        w, h = image.size
        pixel_count = w * h * 4
        pixels_flat = np.empty(pixel_count, dtype=np.float32)
        image.pixels.foreach_get(pixels_flat)
        pixels = pixels_flat.reshape(h, w, 4)

        old_arr = np.array(old_rgb, dtype=np.float32)
        new_arr = np.array(new_rgb, dtype=np.float32)

        tolerance = 3.0 / 255.0
        mask = np.all(np.abs(pixels[:, :, :3] - old_arr) < tolerance, axis=2)

        num_changed = np.count_nonzero(mask)
        if num_changed == 0:
            self.report({"INFO"}, "No pixels found with the current color")
            return {"CANCELLED"}

        pixels[mask, 0] = new_arr[0]
        pixels[mask, 1] = new_arr[1]
        pixels[mask, 2] = new_arr[2]

        image.pixels.foreach_set(pixels.ravel())
        image.update()

        # Update stored color
        item.color = new_rgb
        _write_colors_to_mesh(context)

        # Update brush if this is the active filament
        _set_brush_color(context, new_rgb)

        self.report({"INFO"}, f"Reassigned {num_changed} pixels to new color")
        return {"FINISHED"}


class MMU_OT_import_paint_popup(bpy.types.Operator):
    """Post-import popup asking to switch to Texture Paint mode"""

    bl_idname = "mmu.import_paint_popup"
    bl_label = "MMU Paint Data Detected"
    bl_options = {"INTERNAL", "UNDO"}

    object_name: bpy.props.StringProperty()

    def execute(self, context):
        """User clicked 'Switch to Texture Paint'."""
        # Select the imported object
        obj = bpy.data.objects.get(self.object_name)
        if obj:
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            context.view_layer.objects.active = obj

        bpy.ops.mmu.switch_to_paint()
        return {"FINISHED"}

    def cancel(self, context):
        """User dismissed the popup -- stay in Object mode."""
        pass

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        layout.label(text="This 3MF file contains multi-material paint data.")
        layout.label(text="Would you like to switch to Texture Paint mode")
        layout.label(text="to view and edit the paint regions?")
        layout.separator()
        box = layout.box()
        box.label(text="After switching, open the sidebar (N key) and", icon="INFO")
        box.label(text="click the '3MF' tab to access the paint tools.")
