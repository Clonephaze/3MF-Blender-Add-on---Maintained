# Blender add-on to import and export 3MF files.
# Copyright (C) 2026 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Bake to MMU â€” operators, panels, and small helpers.

Provides the Blender operators and UI panels that let users take
procedural textures, complex shader setups, or vertex color data and
convert them into discrete-color MMU paint textures for 3MF export.

Heavy-lifting functions live in sibling modules:
- ``quantize`` â€” pixel-level and region-based quantization pipeline
- ``vertex_colors`` â€” vertex color detection, rasterization, face assignment

This module keeps:
- ``_ensure_uv_unwrap``, ``_get_texture_size``, ``_get_filament_colors_from_settings``
- ``_cleanup_per_mat_state``
- ``MMU_OT_bake_to_mmu`` / ``MMU_OT_quantize_texture`` operators
- ``_draw_bake_panel`` / ``NODE_PT_mmu_bake`` panel
- Registration
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

from .quantize import (  # noqa: F401 â€” re-exported for backward compat
    _rgb_to_hsv,
    _compute_neighborhood_brightness,
    _hue_aware_distance,
    _quantize_pixels,
    _compute_palette_index_map,
    _rasterize_island_map,
    _uv_edge_shared,
    _flood_fill_segmentation,
    _compute_gradient_magnitude,
    _merge_small_regions,
    _compute_region_representative_colors,
    _match_colors_to_palette,
    _build_palette_regions,
    _rebuild_region_palette,
    _quantize_by_regions,
    _apply_majority_filter,
)
from .vertex_colors import (  # noqa: F401 â€” re-exported for backward compat
    _detect_vertex_color_source,
    _rasterize_vertex_colors,
    _compute_face_filaments,
    _rasterize_face_colors,
)


# ---------------------------------------------------------------------------
#  UV / Texture Helpers
# ---------------------------------------------------------------------------

def _ensure_uv_unwrap(obj, context):
    """Ensure the object has a dedicated MMU_Paint UV layer.

    Uses the UV method selected in MMUPaintSettings (Smart UV Project by
    default, Lightmap Pack as an option).

    A Limited Dissolve pass (angle ~0.5Â°) is applied first to merge coplanar
    triangles â€” this gives each remaining face more UV space and reduces
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
    # UV space and reducing blurriness.  ~0.5Â° is tight enough to only
    # merge truly flat faces while leaving curved surfaces intact.
    if not settings.skip_dissolve:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.dissolve_limit(
            bm, angle_limit=0.00873,
            verts=bm.verts, edges=bm.edges,
        )
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
    else:
        debug("_ensure_uv_unwrap: skipping Limited Dissolve (skip_dissolve=True)")

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
    """Read the init_filaments list from MMUPaintSettings.

    Returns list of (r, g, b) tuples in **sRGB**, matching the colour
    space of ``image.pixels`` for sRGB-tagged images (Blender's default
    for newly-created images).

    When OrcaSlicer-FullSpectrum mixed filaments are present
    (``settings.has_mixed_filaments``), the computed display colors for
    all enabled, non-deleted virtual filament entries are appended after
    the physical filament colors.  This extends the quantization palette
    so that pixels painted with a virtual mixed color are assigned the
    correct virtual filament index on bake/quantize.
    """
    settings = context.scene.mmu_paint
    colors = []
    for item in settings.init_filaments:
        colors.append(tuple(item.color[:3]))

    # Append virtual (mixed) filament display colors when present.
    # Only enabled, non-deleted entries contribute virtual slots —
    # the same logic used by virtual_filament_id_to_index().
    if getattr(settings, "has_mixed_filaments", False):
        for mf_item in settings.mixed_filaments:
            if not getattr(mf_item, "enabled", True):
                continue
            if getattr(mf_item, "deleted", False):
                continue
            dc = getattr(mf_item, "display_color", None)
            if dc is None:
                continue
            # display_color is stored as a FloatVectorProperty (r,g,b)
            colors.append(tuple(dc[:3]))

    return colors


# ---------------------------------------------------------------------------
#  Internal helpers
# ---------------------------------------------------------------------------

def _cleanup_per_mat_state(per_mat_state):
    """Remove all temporary bake nodes and restore original wiring for every material.

    Called after a successful bake (Step 8) or on error (Step 7) to
    ensure each material's node tree is returned to its pre-bake state.
    """
    for state in per_mat_state:
        if state is None:
            continue
        mat = state["mat"]
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Remove temp UV Map nodes
        for uv_node in state.get("temp_uv_nodes", []):
            try:
                nodes.remove(uv_node)
            except Exception:
                pass

        # Restore original Material Output â†’ Surface wiring
        if state.get("emit_node"):
            if state.get("original_surface_socket"):
                output_node = None
                for n in nodes:
                    if n.type == "OUTPUT_MATERIAL" and n.is_active_output:
                        output_node = n
                        break
                if output_node:
                    links.new(
                        state["original_surface_socket"],
                        output_node.inputs["Surface"],
                    )
            try:
                nodes.remove(state["emit_node"])
            except Exception:
                pass

        # Remove temp RGB node (if created for solid-color materials)
        if state.get("rgb_node"):
            try:
                nodes.remove(state["rgb_node"])
            except Exception:
                pass

        # Remove the bake target Image Texture node
        if state.get("bake_node"):
            try:
                nodes.remove(state["bake_node"])
            except Exception:
                pass


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
            ("1024", "1024", "1024Ã—1024 (fast bake, lower detail)"),
            ("2048", "2048", "2048Ã—2048 (good for simple models)"),
            ("4096", "4096", "4096Ã—4096 (recommended for most models)"),
            ("8192", "8192", "8192Ã—8192 (high detail, slower bake)"),
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
        return wm.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mmu_paint
        obj = context.active_object

        # --- Source Material -----------------------------------------------
        mat = obj.data.materials[0] if obj.data.materials else None
        is_vc = obj and _detect_vertex_color_source(obj) is not None

        if is_vc:
            src_box = layout.box()
            header = src_box.row()
            header.label(text="Source: Vertex Colors", icon="VPAINT_HLT")
            sub = src_box.column(align=True)
            sub.scale_y = 0.75
            sub.label(text="Fast path — per-face colour assignment,")
            sub.label(text="no Cycles bake needed.")
        elif mat:
            src_box = layout.box()
            src_box.label(text=f"Source: {mat.name}", icon="MATERIAL")

        # --- Filament Palette Preview --------------------------------------
        if len(settings.init_filaments) >= 2:
            pal_box = layout.box()
            pal_box.label(text="Filament Palette", icon="COLOR")
            flow = pal_box.grid_flow(
                row_major=True, columns=0, even_columns=True,
                even_rows=True, align=True,
            )
            for item in settings.init_filaments:
                swatch = flow.row(align=True)
                swatch.ui_units_x = 1.2
                swatch.enabled = False
                swatch.prop(item, "color", text="")

        # --- Bake Settings -------------------------------------------------
        settings_box = layout.box()
        settings_box.label(text="Bake Settings", icon="PREFERENCES")
        col = settings_box.column(align=True)
        col.prop(self, "texture_size")
        col.prop(settings, "uv_method")
        if settings.uv_method == "LIGHTMAP":
            col.prop(settings, "lightmap_divisions")
        if not is_vc:
            col.prop(settings, "skip_dissolve")

        # --- Quantization -------------------------------------------------
        quant_box = layout.box()
        quant_box.label(text="Quantization", icon="BRUSH_DATA")
        quant_box.prop(settings, "quantize_method", text="")
        if settings.quantize_method == "REGION":
            col = quant_box.column(align=True)
            col.prop(settings, "region_similarity")
            col.prop(settings, "min_region_size")

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        settings = context.scene.mmu_paint

        filament_colors = _get_filament_colors_from_settings(context)
        if len(filament_colors) < 2:
            self.report({"ERROR"}, "At least 2 filaments required")
            return {"CANCELLED"}

        # --- Vertex-color fast path (before UV unwrap) ---
        # Detect FIRST â€” Limited Dissolve in _ensure_uv_unwrap would
        # destroy vertex color data and is extremely slow on high-poly
        # meshes.  The fast path skips dissolve entirely.
        vc_attr_name = _detect_vertex_color_source(obj)
        if vc_attr_name:
            debug(f"Bake to MMU: vertex color fast path, attr='{vc_attr_name}'")
            self.report({"INFO"}, "Converting vertex colors (fast path)...")

            wm = context.window_manager
            wm.progress_begin(0, 100)

            # A â€” Per-face filament assignment (vectorised, instant)
            self.report({"INFO"}, "Assigning filament colours per face...")
            face_filaments = _compute_face_filaments(
                mesh, vc_attr_name, filament_colors,
            )
            if face_filaments is None:
                self.report({"ERROR"}, "Failed to read vertex color data")
                wm.progress_end()
                return {"CANCELLED"}
            wm.progress_update(10)

            # B â€” Lightweight UV unwrap (no Limited Dissolve)
            self.report({"INFO"}, "Creating UV map...")
            mmu_layer = mesh.uv_layers.get("MMU_Paint")
            if mmu_layer is None:
                mmu_layer = mesh.uv_layers.new(name="MMU_Paint")
            mesh.uv_layers.active = mmu_layer
            mmu_layer.active_render = True
            context.view_layer.objects.active = obj

            prev_mode = obj.mode
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            uv_method = settings.uv_method
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
            wm.progress_update(40)

            # C â€” Texture size
            tex_size = _get_texture_size(mesh, int(self.texture_size))
            debug(f"Bake to MMU (VC): texture size {tex_size}x{tex_size}")

            # D â€” Fill texture with flat per-face filament colours
            self.report({"INFO"}, "Filling texture...")
            palette = np.array(filament_colors, dtype=np.float32)
            face_rgb = palette[face_filaments]
            pixels = _rasterize_face_colors(
                mesh, "MMU_Paint", face_rgb, tex_size, tex_size,
            )
            if pixels is None:
                self.report({"ERROR"}, "Failed to rasterize face colours")
                wm.progress_end()
                return {"CANCELLED"}
            wm.progress_update(60)

            # E â€” Per-pixel quantize (always â€” region method is overkill
            #     here since the texture already contains near-exact
            #     filament colours from the face assignment)
            self.report({"INFO"}, "Snapping pixels to filament colours...")
            changed = _quantize_pixels(pixels, filament_colors)
            debug(f"Bake to MMU (VC): pixel quantize changed {changed} px")
            wm.progress_update(85)

            # F â€” Create image + finalize
            image_name = f"{mesh.name}_MMU_Paint"
            existing = bpy.data.images.get(image_name)
            if existing:
                bpy.data.images.remove(existing)
            image = bpy.data.images.new(
                image_name, width=tex_size, height=tex_size, alpha=True,
            )
            image.pixels.foreach_set(pixels.ravel())
            image.update()
            image.pack()
            wm.progress_update(90)

            return self._finalize_mmu_paint(
                context, obj, mesh, settings, image, image_name,
                filament_colors, tex_size, changed,
            )

        # --- Step 1: Ensure UV unwrap ---
        self.report({"INFO"}, "Ensuring UV map...")
        prev_uv_name = _ensure_uv_unwrap(obj, context)

        # Keep active_render on the original UV during the bake so that
        # Texture Coordinate â†’ UV and unlinked Image Texture nodes sample
        # from the correct coordinates.  MMU_Paint is still the *active*
        # layer (which tells the bake where to write output pixels).
        if prev_uv_name and mesh.uv_layers.get(prev_uv_name):
            mesh.uv_layers[prev_uv_name].active_render = True

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

        # --- Step 5: Prepare ALL materials for baking ---
        # Blender's bake uses per-face material assignments. Each material
        # needs a bake-target Image Texture node (set as the active node)
        # so pixels from *all* material slots are captured.
        #
        # We also rewire each material's Base Color â†’ Emission â†’ Material
        # Output for an EMIT bake, which is much faster than DIFFUSE because
        # it skips all lighting calculations.
        #
        # Track per-material state so we can clean up afterwards.
        _per_mat_state = []  # list of dicts, one per material slot
        bake_type = "EMIT"
        bake_pass_filter = set()
        all_emit_ok = True  # Will any material need DIFFUSE fallback?

        for slot_idx, mat in enumerate(original_materials):
            if mat is None:
                _per_mat_state.append(None)
                continue

            if not mat.use_nodes:
                mat.use_nodes = True

            state = {
                "mat": mat,
                "bake_node": None,
                "emit_node": None,
                "rgb_node": None,
                "original_surface_socket": None,
                "temp_uv_nodes": [],
            }

            nodes = mat.node_tree.nodes
            links = mat.node_tree.links

            # Add bake target image node (must be active for bake to write to it)
            bake_node = nodes.new("ShaderNodeTexImage")
            bake_node.image = image
            bake_node.name = "_MMU_Bake_Target"
            bake_node.label = "MMU Bake Target"
            bake_node.location = (-600, -300)
            nodes.active = bake_node
            state["bake_node"] = bake_node

            # Find Principled BSDF and Material Output
            principled = None
            output_node = None
            for node in nodes:
                if node.type == "BSDF_PRINCIPLED" and principled is None:
                    principled = node
                if node.type == "OUTPUT_MATERIAL" and node.is_active_output:
                    output_node = node

            if principled and output_node:
                # Remember what was wired into Material Output â†’ Surface
                for link in links:
                    if (
                        link.to_node == output_node
                        and link.to_socket.name == "Surface"
                    ):
                        state["original_surface_socket"] = link.from_socket
                        break

                # Find what drives Base Color
                base_color_source = None
                for link in links:
                    if link.to_node == principled and link.to_socket.name == "Base Color":
                        base_color_source = link.from_socket
                        break

                # Create Emission node
                emit_node = nodes.new("ShaderNodeEmission")
                emit_node.name = "_MMU_Temp_Emission"
                emit_node.location = (
                    principled.location.x,
                    principled.location.y - 200,
                )
                state["emit_node"] = emit_node

                if base_color_source:
                    # Base Color has an input link (texture, noise, etc.)
                    links.new(base_color_source, emit_node.inputs["Color"])
                else:
                    # Solid color only â€” extract the default value and feed it
                    # through an RGB node so Emission still captures it.
                    default_color = principled.inputs["Base Color"].default_value
                    rgb_node = nodes.new("ShaderNodeRGB")
                    rgb_node.name = "_MMU_Temp_RGB"
                    rgb_node.outputs[0].default_value = (
                        default_color[0], default_color[1],
                        default_color[2], 1.0,
                    )
                    rgb_node.location = (
                        emit_node.location.x - 200,
                        emit_node.location.y,
                    )
                    state["rgb_node"] = rgb_node
                    links.new(rgb_node.outputs[0], emit_node.inputs["Color"])

                links.new(
                    emit_node.outputs["Emission"],
                    output_node.inputs["Surface"],
                )
                debug(f"Bake to MMU: slot {slot_idx} '{mat.name}' wired for EMIT bake")
            else:
                # No Principled BSDF â€” fall back to DIFFUSE for this material.
                all_emit_ok = False
                debug(
                    f"Bake to MMU: slot {slot_idx} '{mat.name}' has no Principled BSDF, "
                    "falling back to DIFFUSE bake"
                )

            # Pin Image Texture UVs to the original UV layer so textures
            # sample with correct coordinates after the MMU_Paint UV was
            # set as active.
            if prev_uv_name and mesh.uv_layers.get(prev_uv_name):
                for node in list(nodes):
                    if node.type != "TEX_IMAGE" or node == bake_node:
                        continue
                    uv_input = node.inputs.get("Vector")
                    if uv_input and not uv_input.is_linked:
                        uv_node = nodes.new("ShaderNodeUVMap")
                        uv_node.uv_map = prev_uv_name
                        uv_node.name = "_MMU_Temp_UV"
                        uv_node.location = (
                            node.location.x - 200,
                            node.location.y - 100,
                        )
                        links.new(uv_node.outputs["UV"], uv_input)
                        state["temp_uv_nodes"].append(uv_node)
                        debug(f"Bake to MMU: pinned '{node.name}' UV to '{prev_uv_name}'")

            _per_mat_state.append(state)

        # If any material couldn't use EMIT, fall back to DIFFUSE COLOR
        if not all_emit_ok:
            bake_type = "DIFFUSE"
            bake_pass_filter = {"COLOR"}
            # Undo Emission rewiring for materials that had it
            for state in _per_mat_state:
                if state is None:
                    continue
                mat = state["mat"]
                nodes = mat.node_tree.nodes
                links = mat.node_tree.links
                if state["emit_node"]:
                    if state["original_surface_socket"]:
                        output_node = [
                            n for n in nodes
                            if n.type == "OUTPUT_MATERIAL" and n.is_active_output
                        ][0]
                        links.new(
                            state["original_surface_socket"],
                            output_node.inputs["Surface"],
                        )
                    nodes.remove(state["emit_node"])
                    state["emit_node"] = None
                if state["rgb_node"]:
                    nodes.remove(state["rgb_node"])
                    state["rgb_node"] = None
        else:
            debug(f"Bake to MMU: all {len(original_materials)} materials use EMIT bake")

        # --- Step 6: Switch to Cycles for baking ---
        original_engine = context.scene.render.engine
        context.scene.render.engine = "CYCLES"

        # --- Step 6b: Optimize Cycles settings for fast procedural bake ---
        cycles = context.scene.cycles
        original_samples = cycles.samples
        original_device = cycles.device

        # 1 sample is sufficient â€” we're baking flat procedural color, not lighting
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
            # Clean up temp nodes and settings from ALL materials
            _cleanup_per_mat_state(_per_mat_state)
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
        # Tear down all temporary nodes from ALL materials
        _cleanup_per_mat_state(_per_mat_state)

        cycles.samples = original_samples
        cycles.device = original_device
        context.scene.render.engine = original_engine

        # Now that bake is done, set MMU_Paint as active_render for the
        # final paint material.
        mmu_layer = mesh.uv_layers.get("MMU_Paint")
        if mmu_layer:
            mmu_layer.active_render = True

        # --- Step 9: Quantize the baked texture ---
        self.report({"INFO"}, "Quantizing to filament colors...")
        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.progress_update(5)

        pixel_count = tex_size * tex_size * 4
        pixels_flat = np.empty(pixel_count, dtype=np.float32)
        image.pixels.foreach_get(pixels_flat)
        pixels = pixels_flat.reshape(tex_size, tex_size, 4)

        wm.progress_update(10)

        # Use the selected quantization method
        if settings.quantize_method == "REGION":
            # Region-based quantization: segment â†’ merge â†’ characterize â†’ quantize
            self.report({"INFO"}, "Segmenting into color regions...")
            # Map quantization sub-steps to progress 10-85

            def _bake_progress(pct):
                wm.progress_update(10 + int(pct * 0.75))
            changed = _quantize_by_regions(
                pixels, filament_colors,
                similarity_threshold=settings.region_similarity,
                min_region_size=settings.min_region_size,
                progress_callback=_bake_progress,
                mesh_data=mesh,
                uv_layer_name="MMU_Paint",
            )
        else:
            # Legacy per-pixel quantization
            changed = _quantize_pixels(pixels, filament_colors)

        debug(f"Bake to MMU: quantized {changed} pixels")
        wm.progress_update(85)

        # Apply spatial smoothing (disabled by default, experimental)
        if settings.use_spatial_smoothing:
            self.report({"INFO"}, "Applying UV spatial smoothing (experimental)...")
            flipped = _apply_majority_filter(
                pixels, filament_colors,
                kernel_size=settings.smoothing_kernel_size,
                passes=settings.smoothing_passes,
            )
            debug(f"Bake to MMU: spatial smoothing flipped {flipped} pixels")

        wm.progress_update(90)
        image.pixels.foreach_set(pixels.ravel())
        image.update()
        image.pack()
        wm.progress_update(100)
        wm.progress_end()

        # Finalize: replace material, set properties, switch to paint
        return self._finalize_mmu_paint(
            context, obj, mesh, settings, image, image_name,
            filament_colors, tex_size, changed,
        )

    def _finalize_mmu_paint(
        self, context, obj, mesh, settings, image, image_name,
        filament_colors, tex_size, changed,
    ):
        """Shared finalization for both the Cycles bake and vertex-color fast paths."""
        # --- Replace material with MMU paint material ---
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

        # --- Set up 3mf custom properties ---
        # Write ONLY the physical filament colors to the mesh palette.  Mixed
        # filament display colors are virtual and must NOT be stored as physical
        # entries — _sync_filaments_from_mesh re-appends them from
        # settings.mixed_filaments, but only when it knows the physical boundary.
        num_physical = len(settings.init_filaments)
        colors_dict = {}
        for i in range(num_physical):
            colors_dict[i] = _hex_from_rgb(*filament_colors[i])

        mesh["3mf_is_paint_texture"] = True
        mesh["3mf_paint_default_extruder"] = 1  # 1-based
        mesh["3mf_paint_extruder_colors"] = str(colors_dict)
        mesh["3mf_num_physical_filaments"] = num_physical

        # --- Sync the paint panel ---
        settings.loaded_mesh_name = ""  # Force reload
        from .helpers import _sync_filaments_from_mesh
        _sync_filaments_from_mesh(context)

        # Set active node so texture paint can find the image
        mmu_mat.node_tree.nodes.active = tex_node

        # --- Switch to Texture Paint mode ---
        bpy.ops.object.mode_set(mode="TEXTURE_PAINT")
        from .helpers import _configure_paint_brush
        _configure_paint_brush(context)

        ts = context.tool_settings
        if hasattr(ts.image_paint, "canvas"):
            ts.image_paint.canvas = image

        # Set brush to first filament color
        if len(settings.filaments) > 0:
            from .helpers import _set_brush_color
            _set_brush_color(context, settings.filaments[0].color[:])

        self.report(
            {"INFO"},
            f"Baked and quantized to {len(filament_colors)} filament colors "
            f"at {tex_size}Ã—{tex_size} ({changed} pixels adjusted)",
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
        from .helpers import _get_paint_image
        image = _get_paint_image(obj)
        if image is None:
            self.report({"ERROR"}, "No paint texture found")
            return {"CANCELLED"}

        # Get filament colors — include mixed virtual slots so that pixels
        # painted with a mixed color are preserved (not snapped back to physical).
        filament_colors = _get_filament_colors_from_settings(context)
        if len(filament_colors) < 2:
            # Fall back to reading from mesh if settings are unavailable
            colors_str = mesh.get("3mf_paint_extruder_colors", "")
            if not colors_str:
                self.report({"ERROR"}, "No filament colors stored on mesh")
                return {"CANCELLED"}
            try:
                colors_dict = ast.literal_eval(colors_str)
            except (ValueError, SyntaxError):
                self.report({"ERROR"}, "Failed to parse filament colors")
                return {"CANCELLED"}
            filament_colors = [_rgb_from_hex(colors_dict[idx]) for idx in sorted(colors_dict.keys())]
            if len(filament_colors) < 2:
                self.report({"ERROR"}, "Need at least 2 filament colors")
                return {"CANCELLED"}

        # Quantize
        w, h = image.size
        pixel_count = w * h * 4
        pixels_flat = np.empty(pixel_count, dtype=np.float32)
        image.pixels.foreach_get(pixels_flat)
        pixels = pixels_flat.reshape(h, w, 4)

        settings = context.scene.mmu_paint

        # Use the selected quantization method
        wm = context.window_manager
        wm.progress_begin(0, 100)
        wm.progress_update(5)

        if settings.quantize_method == "REGION":
            def _quant_progress(pct):
                wm.progress_update(5 + int(pct * 0.85))
            uv_name = mesh.uv_layers.active.name if mesh.uv_layers.active else None
            changed = _quantize_by_regions(
                pixels, filament_colors,
                similarity_threshold=settings.region_similarity,
                min_region_size=settings.min_region_size,
                progress_callback=_quant_progress,
                mesh_data=mesh,
                uv_layer_name=uv_name,
            )
        else:
            changed = _quantize_pixels(pixels, filament_colors)

        wm.progress_update(90)

        # Apply spatial smoothing (legacy, deprecated)
        flipped = 0
        if settings.use_spatial_smoothing:
            flipped = _apply_majority_filter(
                pixels, filament_colors,
                kernel_size=settings.smoothing_kernel_size,
                passes=settings.smoothing_passes,
            )

        image.pixels.foreach_set(pixels.ravel())
        image.update()
        wm.progress_update(100)
        wm.progress_end()

        self.report(
            {"INFO"},
            f"Quantized {changed} pixels ({flipped} spatially smoothed) to {len(filament_colors)} colors",
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
        # --- Already has MMU paint — show quantize tools ---
        header = layout.box()
        header.label(text="MMU Paint Active", icon="CHECKMARK")

        quant_box = layout.box()
        quant_box.label(text="Quantization", icon="BRUSH_DATA")
        quant_box.prop(settings, "quantize_method", text="")
        if settings.quantize_method == "REGION":
            col = quant_box.column(align=True)
            col.prop(settings, "region_similarity")
            col.prop(settings, "min_region_size")
        quant_box.separator(factor=0.5)
        row = quant_box.row()
        row.scale_y = 1.3
        row.operator("mmu.quantize_texture", icon="BRUSH_DATA")

        tip = layout.column(align=True)
        tip.scale_y = 0.7
        tip.label(text="Tip: Use Quantize after painting to clean up", icon="INFO")
        tip.label(text="anti-aliased edges or off-color pixels.")
    else:
        # --- Show bake setup ---
        # Material status
        has_material = bool(obj.data.materials and obj.data.materials[0])
        if not has_material:
            layout.label(text="No material assigned", icon="ERROR")
            return

        mat = obj.data.materials[0]
        is_vc = _detect_vertex_color_source(obj) is not None

        src_box = layout.box()
        if is_vc:
            src_box.label(text="Source: Vertex Colors", icon="VPAINT_HLT")
            sub = src_box.column(align=True)
            sub.scale_y = 0.75
            sub.label(text="Fast path — no Cycles bake needed.")
        else:
            src_box.label(text=f"Source: {mat.name}", icon="MATERIAL")

        # Filament palette
        pal_box = layout.box()
        pal_box.label(text="Filament Colors", icon="COLOR")
        if len(settings.init_filaments) == 0:
            pal_box.operator(
                "mmu.reset_init_filaments",
                text="Create Default Palette",
                icon="ADD",
            )
        else:
            row = pal_box.row()
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

            col = row.column(align=True)
            col.operator("mmu.add_init_filament", icon="ADD", text="")
            col.operator("mmu.remove_init_filament", icon="REMOVE", text="")

            # Detect + Reset row
            util_row = pal_box.row(align=True)
            util_row.operator(
                "mmu.detect_material_colors", icon="MATERIAL",
            )
            util_row.operator("mmu.reset_init_filaments", icon="FILE_REFRESH")

        # Mixed filaments section — collapsible, hidden by default.  Users can
        # define blended virtual slots before baking; the bake pipeline includes
        # these colors automatically via _get_filament_colors_from_settings().
        mix_header, mix_body = layout.panel("mmu_bake_mixed_filaments", default_closed=True)
        mix_header.label(text="Mixed Filaments", icon="IPO_LINEAR")
        if mix_body:
            from .helpers import draw_add_mix_form
            if settings.has_mixed_filaments and settings.mixed_filaments:
                row = mix_body.row()
                row.template_list(
                    "MMU_UL_mixed_filaments",
                    "bake_mixed",
                    settings,
                    "mixed_filaments",
                    settings,
                    "active_mixed_filament_index",
                    rows=2,
                    maxrows=6,
                )
                col = row.column(align=True)
                col.prop(
                    settings,
                    "show_add_mix_section",
                    icon="ADD",
                    text="",
                    toggle=True,
                    emboss=True,
                )
                col.operator("mmu.remove_mixed_filament", icon="REMOVE", text="")
            else:
                row = mix_body.row()
                row.label(text="No mixed filaments defined.", icon="INFO")
                row.prop(
                    settings,
                    "show_add_mix_section",
                    icon="ADD",
                    text="",
                    toggle=True,
                    emboss=True,
                )
            if settings.show_add_mix_section:
                box = mix_body.box()
                box.label(text="Add Color", icon="ADD")
                draw_add_mix_form(box, settings)

        # Options
        opts_box = layout.box()
        opts_box.label(text="Options", icon="PREFERENCES")
        opts_box.prop(settings, "skip_dissolve")

        # Bake button
        bake_row = layout.row(align=True)
        bake_row.scale_y = 1.5
        bake_row.operator("mmu.bake_to_mmu", icon="RENDER_STILL")

        info = layout.column(align=True)
        info.scale_y = 0.7
        if settings.skip_dissolve:
            info.label(text="Bakes material to filament colors,", icon="INFO")
            info.label(text="preserving original mesh topology.")
        else:
            info.label(text="Bakes material output to a texture,", icon="INFO")
            info.label(text="then snaps pixels to the nearest")
            info.label(text="filament color for clean 3MF export.")


# ---------------------------------------------------------------------------
#  Panels
# ---------------------------------------------------------------------------

class NODE_PT_mmu_bake(bpy.types.Panel):
    """Bake to MMU Paint Shader Editor sidebar panel."""

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
#     """Bake to MMU Paint Geometry Nodes sidebar panel."""
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
