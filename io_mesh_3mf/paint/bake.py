# Blender add-on to import and export 3MF files.
# Copyright (C) 2020 Ghostkeeper
# Copyright (C) 2026 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Bake to MMU — bake any material/shader to a quantized MMU paint texture.

Provides operators and panels that let users take procedural textures,
complex shader setups, or (in the future) geometry node color outputs and
convert them into discrete-color MMU paint textures for 3MF export.

Architecture:
- ``MMU_OT_bake_to_mmu`` — main operator: bake + quantize + setup properties
- ``MMU_OT_quantize_texture`` — standalone quantize (snap existing texture to filament colors)
- ``_draw_bake_panel()`` — shared draw function used by multiple space-type panels
- ``NODE_PT_mmu_bake`` — Shader Editor sidebar panel
- Future: ``NODE_PT_mmu_bake_gn`` — Geometry Nodes sidebar panel

The bake pipeline:
1. Ensure dedicated MMU_Paint UV layer exists (Smart UV or Lightmap Pack)
2. Create a target image at the chosen resolution
3. Bake the active material's diffuse output to the target image
4. Quantize: snap every pixel to the nearest filament color (numpy vectorized)
5. Set up 3mf_* custom properties so the export pipeline recognizes it
"""

import ast
import bmesh
import numpy as np
import bpy
import bpy.props
import bpy.types

from ..common.colors import hex_to_rgb as _rgb_from_hex
from ..common.colors import rgb_to_hex as _hex_from_rgb
from ..common.logging import debug, error


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _quantize_pixels(
    pixels: np.ndarray,
    filament_colors: list,
) -> int:
    """
    Snap every pixel in the image to the nearest filament color.

    Operates in-place on the (H, W, 4) float32 array.

    :param pixels: (H, W, 4) float32 pixel array, modified in-place.
    :param filament_colors: List of (r, g, b) tuples in [0, 1] range.
    :return: Number of pixels that changed color.
    """
    height, width = pixels.shape[:2]

    # Build color array for vectorized distance computation
    palette = np.array(filament_colors, dtype=np.float32)  # (N, 3)
    n_colors = len(palette)

    changed = 0
    chunk_size = 256  # Keep memory bounded for large textures

    for y_start in range(0, height, chunk_size):
        y_end = min(y_start + chunk_size, height)
        chunk = pixels[y_start:y_end]  # (chunk_h, W, 4)
        chunk_rgb = chunk[:, :, :3]    # (chunk_h, W, 3)
        chunk_h = chunk_rgb.shape[0]

        # Expand for broadcasting: (chunk_h, W, 1, 3) vs (1, 1, N, 3)
        expanded = chunk_rgb.reshape(chunk_h, width, 1, 3)
        palette_expanded = palette.reshape(1, 1, n_colors, 3)

        # Sum of squared differences (Euclidean-ish, no sqrt needed)
        dists = np.sum((expanded - palette_expanded) ** 2, axis=3)  # (chunk_h, W, N)
        nearest_idx = np.argmin(dists, axis=2)  # (chunk_h, W)

        # Build the new color values
        new_rgb = palette[nearest_idx]  # (chunk_h, W, 3)

        # Track changes (tolerance to avoid floating point noise)
        diff = np.any(np.abs(chunk_rgb - new_rgb) > 0.002, axis=2)
        changed += int(np.count_nonzero(diff))

        # Apply quantized colors
        pixels[y_start:y_end, :, :3] = new_rgb

    return changed


def _ensure_uv_unwrap(obj, context):
    """Ensure the object has a dedicated MMU_Paint UV layer.

    Uses the UV method selected in MMUPaintSettings (Smart UV Project by
    default, Lightmap Pack as an option).

    A Limited Dissolve pass (angle ~2°) is applied first to merge coplanar
    triangles — this gives each remaining face more UV space and reduces
    blurriness, especially with Lightmap Pack.

    Any existing UVs (e.g. hand-crafted unwraps) are left untouched.
    The ``MMU_Paint`` layer is set as the **active render** layer so the
    bake writes to it; the caller is responsible for restoring the
    original active layer afterward if desired.

    Returns the name of the previously active UV layer (or ``None``)
    so the caller can restore it.
    """
    mesh = obj.data
    settings = context.scene.mmu_paint
    uv_method = settings.uv_method

    # Remember which UV layer was active before (if any)
    prev_active_name = None
    if mesh.uv_layers.active:
        prev_active_name = str(mesh.uv_layers.active.name)

    # Create or reuse the dedicated MMU_Paint UV layer
    mmu_layer = mesh.uv_layers.get("MMU_Paint")
    if mmu_layer is None:
        mmu_layer = mesh.uv_layers.new(name="MMU_Paint")

    # Set it as both the active and active-render layer
    mesh.uv_layers.active = mmu_layer
    mmu_layer.active_render = True

    context.view_layer.objects.active = obj

    # Limited Dissolve merges coplanar triangles, giving each face more
    # UV space and reducing blurriness.  ~2° is conservative enough to
    # preserve all intentional geometry detail.
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.dissolve_limit(
        bm, angle_limit=0.0349,
        verts=bm.verts, edges=bm.edges,
    )
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    # Must be in edit mode for UV operators
    prev_mode = obj.mode
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

    bpy.ops.object.mode_set(mode=prev_mode)

    return prev_active_name


def _get_texture_size(mesh, override_size=0):
    """Determine texture size based on triangle count or user override."""
    if override_size > 0:
        return override_size
    tri_count = len(mesh.polygons)
    if tri_count < 5000:
        return 2048
    elif tri_count < 20000:
        return 4096
    else:
        return 8192


def _get_filament_colors_from_settings(context):
    """Read the init_filaments list from MMUPaintSettings, return list of (r,g,b) tuples."""
    settings = context.scene.mmu_paint
    colors = []
    for item in settings.init_filaments:
        colors.append(tuple(item.color[:3]))
    return colors


# ---------------------------------------------------------------------------
#  Operators
# ---------------------------------------------------------------------------

class MMU_OT_bake_to_mmu(bpy.types.Operator):
    """Bake the active material to a quantized MMU paint texture for 3MF export"""

    bl_idname = "mmu.bake_to_mmu"
    bl_label = "Bake to MMU Paint"
    bl_description = (
        "Bake the current material output to a texture, then quantize all pixels "
        "to the nearest filament color. The result is a discrete-color paint "
        "texture ready for multi-material 3MF export"
    )
    bl_options = {"REGISTER", "UNDO"}

    texture_size: bpy.props.EnumProperty(
        name="Texture Size",
        description="Resolution of the baked texture",
        items=[
            ("0", "Auto", "Automatic based on triangle count (2K/4K/8K)"),
            ("1024", "1024", "1024×1024 (fast bake, lower detail)"),
            ("2048", "2048", "2048×2048 (good for simple models)"),
            ("4096", "4096", "4096×4096 (recommended for most models)"),
            ("8192", "8192", "8192×8192 (high detail, slower bake)"),
        ],
        default="0",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            return False
        # Must have at least one material to bake from
        if not obj.data.materials or not obj.data.materials[0]:
            return False
        # Must NOT already be an MMU paint texture (use quantize for that)
        if obj.data.get("3mf_is_paint_texture"):
            return False
        # Must have filaments defined
        settings = context.scene.mmu_paint
        return len(settings.init_filaments) >= 2

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mmu_paint

        layout.label(text="Bake material to MMU paint texture", icon="BRUSH_DATA")
        layout.separator()

        layout.prop(self, "texture_size")
        layout.prop(settings, "uv_method")
        if settings.uv_method == "LIGHTMAP":
            layout.prop(settings, "lightmap_divisions")

        # Show the filament palette being used
        box = layout.box()
        box.label(text=f"Quantizing to {len(settings.init_filaments)} filament colors:")
        flow = box.grid_flow(row_major=True, columns=4, align=True)
        for i, item in enumerate(settings.init_filaments):
            row = flow.row(align=True)
            swatch = row.row()
            swatch.ui_units_x = 1.2
            swatch.enabled = False
            swatch.prop(item, "color", text="")
            row.label(text=f"{i + 1}")

        layout.separator()
        layout.label(text="This will replace the current material setup.", icon="INFO")

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        settings = context.scene.mmu_paint

        filament_colors = _get_filament_colors_from_settings(context)
        if len(filament_colors) < 2:
            self.report({"ERROR"}, "At least 2 filaments required")
            return {"CANCELLED"}

        # --- Step 1: Ensure UV unwrap ---
        self.report({"INFO"}, "Ensuring UV map...")
        prev_uv_name = _ensure_uv_unwrap(obj, context)

        # --- Step 2: Determine texture size ---
        tex_size = _get_texture_size(mesh, int(self.texture_size))
        debug(f"Bake to MMU: texture size {tex_size}x{tex_size}")

        # --- Step 3: Save reference to original material ---
        original_materials = [slot.material for slot in obj.material_slots]
        if not original_materials or not original_materials[0]:
            self.report({"ERROR"}, "No material found to bake")
            return {"CANCELLED"}

        # --- Step 4: Create the bake target image ---
        image_name = f"{mesh.name}_MMU_Paint"
        # Remove existing image with same name if present
        existing = bpy.data.images.get(image_name)
        if existing:
            bpy.data.images.remove(existing)

        image = bpy.data.images.new(
            image_name, width=tex_size, height=tex_size, alpha=True
        )

        # --- Step 5: Add an Image Texture node to the material for bake target ---
        # Blender's bake writes to the active Image Texture node
        mat = original_materials[0]
        if not mat.use_nodes:
            mat.use_nodes = True

        nodes = mat.node_tree.nodes
        bake_node = nodes.new("ShaderNodeTexImage")
        bake_node.image = image
        bake_node.name = "_MMU_Bake_Target"
        bake_node.label = "MMU Bake Target"
        bake_node.location = (-600, -300)
        # Must be the selected/active node for bake
        nodes.active = bake_node

        # --- Step 6: Switch to Cycles for baking ---
        original_engine = context.scene.render.engine
        context.scene.render.engine = "CYCLES"

        # --- Step 6b: Optimize Cycles settings for fast procedural bake ---
        cycles = context.scene.cycles
        original_samples = cycles.samples
        original_device = cycles.device

        # 1 sample is sufficient — we're baking flat procedural color, not lighting
        cycles.samples = 1

        # Try GPU compute if available (much faster for large textures)
        try:
            cycles_prefs = context.preferences.addons.get("cycles")
            if cycles_prefs and cycles_prefs.preferences:
                cprefs = cycles_prefs.preferences
                if hasattr(cprefs, "get_devices"):
                    cprefs.get_devices()
                # Check if any GPU device is enabled
                has_gpu = False
                if hasattr(cprefs, "devices"):
                    for dev in cprefs.devices:
                        if dev.type != "CPU" and dev.use:
                            has_gpu = True
                            break
                if has_gpu:
                    cycles.device = "GPU"
                    debug("Bake to MMU: using GPU compute")
        except Exception:
            pass  # Fall back to whatever was configured

        # --- Step 6c: Rewire to Emission for faster bake ---
        # EMIT bake evaluates only the shader color — skips all lighting,
        # bounces, and BSDF calculations.  Perfect for color-only bakes.
        emit_node = None
        original_surface_socket = None
        bake_type = "DIFFUSE"
        bake_pass_filter = {"COLOR"}

        links = mat.node_tree.links

        # Find the Principled BSDF and Material Output nodes
        principled = None
        output_node = None
        for node in nodes:
            if node.type == "BSDF_PRINCIPLED" and principled is None:
                principled = node
            if node.type == "OUTPUT_MATERIAL" and node.is_active_output:
                output_node = node

        if principled and output_node:
            # Find what drives Base Color
            base_color_source = None
            for link in links:
                if link.to_node == principled and link.to_socket.name == "Base Color":
                    base_color_source = link.from_socket
                    break

            if base_color_source:
                # Remember what was wired into Material Output → Surface
                for link in links:
                    if (
                        link.to_node == output_node
                        and link.to_socket.name == "Surface"
                    ):
                        original_surface_socket = link.from_socket
                        break

                # Create a temporary Emission shader wired from the same color source
                emit_node = nodes.new("ShaderNodeEmission")
                emit_node.name = "_MMU_Temp_Emission"
                emit_node.location = (
                    principled.location.x,
                    principled.location.y - 200,
                )

                links.new(base_color_source, emit_node.inputs["Color"])
                links.new(
                    emit_node.outputs["Emission"],
                    output_node.inputs["Surface"],
                )

                bake_type = "EMIT"
                bake_pass_filter = set()  # EMIT bake has no pass_filter
                debug("Bake to MMU: using EMIT bake (skipping lighting)")

        # Ensure we're in Object mode for baking
        prev_mode = obj.mode
        if prev_mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        # Ensure only this object is selected and active
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        context.view_layer.objects.active = obj

        # --- Step 7: Bake ---
        self.report({"INFO"}, "Baking texture...")
        try:
            bake_kwargs = {
                "type": bake_type,
                "use_clear": True,
                "margin": 2,
                "margin_type": "EXTEND",
            }
            if bake_pass_filter:
                bake_kwargs["pass_filter"] = bake_pass_filter
            bpy.ops.object.bake(**bake_kwargs)
        except RuntimeError as e:
            error(f"Bake failed: {e}")
            self.report({"ERROR"}, f"Bake failed: {e}")
            # Clean up temp nodes and settings
            if emit_node:
                if original_surface_socket:
                    links.new(
                        original_surface_socket,
                        output_node.inputs["Surface"],
                    )
                nodes.remove(emit_node)
            nodes.remove(bake_node)
            cycles.samples = original_samples
            cycles.device = original_device
            context.scene.render.engine = original_engine
            # Restore original active UV layer on failure
            if prev_uv_name and mesh.uv_layers.get(prev_uv_name):
                mesh.uv_layers.active = mesh.uv_layers[prev_uv_name]
            if prev_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=prev_mode)
            return {"CANCELLED"}

        # --- Step 8: Restore render engine and Cycles settings ---
        # Tear down the temporary Emission wiring
        if emit_node:
            if original_surface_socket:
                links.new(
                    original_surface_socket,
                    output_node.inputs["Surface"],
                )
            nodes.remove(emit_node)

        cycles.samples = original_samples
        cycles.device = original_device
        context.scene.render.engine = original_engine

        # --- Step 9: Quantize the baked texture ---
        self.report({"INFO"}, "Quantizing to filament colors...")
        pixel_count = tex_size * tex_size * 4
        pixels_flat = np.empty(pixel_count, dtype=np.float32)
        image.pixels.foreach_get(pixels_flat)
        pixels = pixels_flat.reshape(tex_size, tex_size, 4)

        changed = _quantize_pixels(pixels, filament_colors)
        debug(f"Bake to MMU: quantized {changed} pixels")

        image.pixels.foreach_set(pixels.ravel())
        image.update()
        image.pack()

        # --- Step 10: Replace material with MMU paint material ---
        # Remove the bake target node from the original material
        nodes.remove(bake_node)

        # Create new MMU paint material
        mmu_mat = bpy.data.materials.new(name=image_name)
        mmu_mat.use_nodes = True
        mmu_nodes = mmu_mat.node_tree.nodes
        mmu_links = mmu_mat.node_tree.links
        mmu_nodes.clear()

        tex_node = mmu_nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        tex_node.location = (-300, 0)

        bsdf = mmu_nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (100, 0)

        output = mmu_nodes.new("ShaderNodeOutputMaterial")
        output.location = (400, 0)

        mmu_links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        mmu_links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

        # Replace materials on the mesh
        mesh.materials.clear()
        mesh.materials.append(mmu_mat)
        num_faces = len(mesh.polygons)
        if num_faces > 0:
            material_indices = [0] * num_faces
            mesh.polygons.foreach_set("material_index", material_indices)

        # --- Step 11: Set up 3mf custom properties ---
        colors_dict = {}
        for i, color in enumerate(filament_colors):
            colors_dict[i] = _hex_from_rgb(*color)

        mesh["3mf_is_paint_texture"] = True
        mesh["3mf_paint_default_extruder"] = 1  # 1-based
        mesh["3mf_paint_extruder_colors"] = str(colors_dict)

        # --- Step 12: Sync the paint panel ---
        settings.loaded_mesh_name = ""  # Force reload
        from .panel import _sync_filaments_from_mesh
        _sync_filaments_from_mesh(context)

        # Set active node so texture paint can find the image
        mmu_mat.node_tree.nodes.active = tex_node

        # --- Step 13: Switch to Texture Paint mode ---
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
        from .panel import _configure_paint_brush
        _configure_paint_brush(context)

        ts = context.tool_settings
        if hasattr(ts.image_paint, "canvas"):
            ts.image_paint.canvas = image

        # Set brush to first filament color
        if len(settings.filaments) > 0:
            from .panel import _set_brush_color
            _set_brush_color(context, settings.filaments[0].color[:])

        self.report(
            {"INFO"},
            f"Baked and quantized to {len(filament_colors)} filament colors "
            f"at {tex_size}×{tex_size} ({changed} pixels adjusted)",
        )
        return {"FINISHED"}


class MMU_OT_quantize_texture(bpy.types.Operator):
    """Quantize an existing paint texture to snap all pixels to the nearest filament color"""

    bl_idname = "mmu.quantize_texture"
    bl_label = "Quantize to Filaments"
    bl_description = (
        "Snap every pixel in the current MMU paint texture to the nearest "
        "filament color. Useful for cleaning up anti-aliased edges or "
        "slightly off-color painted regions"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            return False
        # Must already have MMU paint data
        return bool(obj.data.get("3mf_is_paint_texture"))

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data

        # Get the paint image
        from .panel import _get_paint_image
        image = _get_paint_image(obj)
        if image is None:
            self.report({"ERROR"}, "No paint texture found")
            return {"CANCELLED"}

        # Get filament colors from mesh properties
        colors_str = mesh.get("3mf_paint_extruder_colors", "")
        if not colors_str:
            self.report({"ERROR"}, "No filament colors stored on mesh")
            return {"CANCELLED"}

        try:
            colors_dict = ast.literal_eval(colors_str)
        except (ValueError, SyntaxError):
            self.report({"ERROR"}, "Failed to parse filament colors")
            return {"CANCELLED"}

        filament_colors = []
        for idx in sorted(colors_dict.keys()):
            rgb = _rgb_from_hex(colors_dict[idx])
            filament_colors.append(rgb)

        if len(filament_colors) < 2:
            self.report({"ERROR"}, "Need at least 2 filament colors")
            return {"CANCELLED"}

        # Quantize
        w, h = image.size
        pixel_count = w * h * 4
        pixels_flat = np.empty(pixel_count, dtype=np.float32)
        image.pixels.foreach_get(pixels_flat)
        pixels = pixels_flat.reshape(h, w, 4)

        changed = _quantize_pixels(pixels, filament_colors)

        image.pixels.foreach_set(pixels.ravel())
        image.update()

        self.report(
            {"INFO"},
            f"Quantized {changed} pixels to {len(filament_colors)} filament colors",
        )
        return {"FINISHED"}


# ---------------------------------------------------------------------------
#  Shared panel draw function
# ---------------------------------------------------------------------------

def _draw_bake_panel(layout, context):
    """
    Shared draw logic for the Bake to MMU panel.

    Used by the Shader Editor panel and (in the future) the Geometry Nodes panel.
    Can also be called from the 3D Viewport paint panel's uninitialized state.

    :param layout: The Blender UI layout to draw into.
    :param context: The current Blender context.
    """
    obj = context.active_object
    if obj is None or obj.type != "MESH":
        layout.label(text="Select a mesh object", icon="INFO")
        return

    settings = context.scene.mmu_paint
    mesh = obj.data
    has_paint = bool(mesh.get("3mf_is_paint_texture"))

    if has_paint:
        # Already has MMU paint — show quantize option
        box = layout.box()
        box.label(text="MMU Paint Active", icon="CHECKMARK")
        box.label(text="Texture paint is set up on this object.")
        box.separator()
        box.operator("mmu.quantize_texture", icon="BRUSH_DATA")
        box.separator()
        info = box.column(align=True)
        info.scale_y = 0.7
        info.label(text="Tip: Use Quantize after painting to clean up", icon="INFO")
        info.label(text="anti-aliased edges or off-color pixels.")
    else:
        # Show bake setup
        box = layout.box()
        box.label(text="Bake Material to MMU", icon="RENDER_STILL")

        # Material status
        has_material = bool(obj.data.materials and obj.data.materials[0])
        if has_material:
            mat = obj.data.materials[0]
            mat_row = box.row()
            mat_row.label(text=f"Material: {mat.name}", icon="MATERIAL")
        else:
            box.label(text="No material assigned", icon="ERROR")
            return

        box.separator()

        # Filament palette setup — reuse the init_filaments from MMUPaintSettings
        if len(settings.init_filaments) == 0:
            box.operator(
                "mmu.reset_init_filaments",
                text="Create Default Palette",
                icon="ADD",
            )
        else:
            box.label(text="Filament Colors:")
            row = box.row()
            row.template_list(
                "MMU_UL_init_filaments",
                "bake_filaments",
                settings,
                "init_filaments",
                settings,
                "active_init_filament_index",
                rows=3,
                maxrows=8,
            )

            # Add/Remove buttons
            col = row.column(align=True)
            col.operator("mmu.add_init_filament", icon="ADD", text="")
            col.operator("mmu.remove_init_filament", icon="REMOVE", text="")

            box.separator()

            # Bake button
            bake_row = box.row(align=True)
            bake_row.scale_y = 1.4
            bake_row.operator("mmu.bake_to_mmu", icon="RENDER_STILL")

            # Detect + Reset row
            util_row = box.row(align=True)
            util_row.operator(
                "mmu.detect_material_colors", icon="MATERIAL",
            )
            util_row.operator("mmu.reset_init_filaments", icon="FILE_REFRESH")

        box.separator()
        info = box.column(align=True)
        info.scale_y = 0.7
        info.label(text="Bakes the material output to a texture,", icon="INFO")
        info.label(text="then snaps every pixel to the nearest")
        info.label(text="filament color for clean 3MF export.")


# ---------------------------------------------------------------------------
#  Panels
# ---------------------------------------------------------------------------

class NODE_PT_mmu_bake(bpy.types.Panel):
    """Bake to MMU Paint — Shader Editor sidebar panel."""

    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "Bake to MMU"

    @classmethod
    def poll(cls, context):
        # Only show in Shader Editor (not Geometry Nodes or Compositor)
        if not hasattr(context, "space_data") or context.space_data is None:
            return False
        space = context.space_data
        if space.type != "NODE_EDITOR":
            return False
        return space.tree_type == "ShaderNodeTree"

    def draw(self, context):
        _draw_bake_panel(self.layout, context)


# Future: Geometry Nodes panel
# class NODE_PT_mmu_bake_gn(bpy.types.Panel):
#     """Bake to MMU Paint — Geometry Nodes sidebar panel."""
#
#     bl_space_type = "NODE_EDITOR"
#     bl_region_type = "UI"
#     bl_category = "3MF"
#     bl_label = "Bake to MMU"
#
#     @classmethod
#     def poll(cls, context):
#         if not hasattr(context, "space_data") or context.space_data is None:
#             return False
#         space = context.space_data
#         if space.type != "NODE_EDITOR":
#             return False
#         return space.tree_type == "GeometryNodeTree"
#
#     def draw(self, context):
#         # GN-specific extraction would go here:
#         # - Detect color attributes from Store Named Attribute nodes
#         # - Detect material assignments
#         # - Offer appropriate bake/extract path
#         _draw_bake_panel(self.layout, context)


# ===================================================================
#  Registration
# ===================================================================

bake_classes = (
    MMU_OT_bake_to_mmu,
    MMU_OT_quantize_texture,
    NODE_PT_mmu_bake,
)


def register():
    for cls in bake_classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(bake_classes):
        bpy.utils.unregister_class(cls)
