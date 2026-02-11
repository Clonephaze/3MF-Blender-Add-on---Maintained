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
MMU Paint Suite — sidebar panel for multi-filament texture painting.

Provides a complete workflow for painting multi-material segmentation
textures on Blender objects, compatible with PrusaSlicer and Orca Slicer
3MF export.

Features:
- Filament palette with quick-select color swatches
- Initialize painting on new geometry (UV unwrap, texture, material)
- Re-assign filament colors (bulk pixel replacement)
- Add/remove filaments with printer warnings
- Brush falloff warning and auto-fix
- Post-import popup to switch to Texture Paint mode
"""

import ast
import bmesh
import numpy as np
import bpy
import bpy.props
import bpy.types


# ---------------------------------------------------------------------------
#  Default palette — visually distinct colors for up to 16 filaments
# ---------------------------------------------------------------------------

DEFAULT_PALETTE = [
    (0.800, 0.800, 0.800),  # 1: Light gray (typical default/base)
    (0.900, 0.200, 0.100),  # 2: Red
    (0.100, 0.600, 0.200),  # 3: Green
    (0.200, 0.400, 0.900),  # 4: Blue
    (0.950, 0.750, 0.100),  # 5: Yellow
    (0.900, 0.400, 0.900),  # 6: Magenta
    (0.100, 0.800, 0.800),  # 7: Cyan
    (0.950, 0.550, 0.100),  # 8: Orange
    (0.500, 0.250, 0.600),  # 9: Purple
    (0.400, 0.250, 0.150),  # 10: Brown
    (0.950, 0.450, 0.550),  # 11: Pink
    (0.350, 0.650, 0.450),  # 12: Teal
    (0.600, 0.050, 0.050),  # 13: Dark red
    (0.050, 0.350, 0.550),  # 14: Navy
    (0.450, 0.500, 0.100),  # 15: Olive
    (0.200, 0.200, 0.200),  # 16: Dark gray
]


# ===================================================================
#  PropertyGroups
# ===================================================================


class MMUFilamentItem(bpy.types.PropertyGroup):
    """One filament/extruder entry in the palette list."""

    color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype="COLOR_GAMMA",
        size=3,
        min=0.0,
        max=1.0,
        default=(0.8, 0.8, 0.8),
        description="Filament swatch color (read-only display, sRGB)",
    )
    index: bpy.props.IntProperty(
        name="Extruder Index",
        description="0-based extruder index",
        default=0,
    )


class MMUInitFilamentItem(bpy.types.PropertyGroup):
    """Filament entry for initialization setup (editable color)."""

    color: bpy.props.FloatVectorProperty(
        name="Color",
        subtype="COLOR_GAMMA",
        size=3,
        min=0.0,
        max=1.0,
        default=(0.8, 0.8, 0.8),
        description="Filament color for initialization (sRGB)",
    )
    name: bpy.props.StringProperty(
        name="Name",
        default="Filament",
    )


class MMUPaintSettings(bpy.types.PropertyGroup):
    """Per-scene settings for the MMU Paint panel."""

    filaments: bpy.props.CollectionProperty(type=MMUFilamentItem)
    active_filament_index: bpy.props.IntProperty(
        name="Active Filament",
        default=0,
        update=lambda self, ctx: _on_active_filament_changed(self, ctx),
    )

    # Initialization setup
    init_filaments: bpy.props.CollectionProperty(type=MMUInitFilamentItem)
    active_init_filament_index: bpy.props.IntProperty(
        name="Active Init Filament",
        default=0,
    )

    # UV method for painting / baking
    uv_method: bpy.props.EnumProperty(
        name="UV Method",
        description=(
            "UV unwrap strategy for MMU paint textures.\n"
            "Smart UV Project shares edges between faces for seamless painting.\n"
            "Lightmap Pack gives every face its own rectangle — best for "
            "procedural bakes but may show edge bleed when hand-painting"
        ),
        items=[
            ("SMART", "Smart UV Project",
             "Groups adjacent coplanar faces into shared islands. "
             "Best for hand-painting (shared edges reduce bleed)"),
            ("LIGHTMAP", "Lightmap Pack",
             "Every face gets its own UV rectangle. "
             "Best for procedural/baked textures"),
        ],
        default="SMART",
    )
    lightmap_divisions: bpy.props.IntProperty(
        name="Lightmap Divisions",
        description="Grid divisions for Lightmap Pack (higher = more UV precision, slower)",
        min=1,
        max=48,
        default=12,
    )

    # Internal: tracks which mesh the filament list was loaded from
    loaded_mesh_name: bpy.props.StringProperty(default="")


# ===================================================================
#  Helpers
# ===================================================================

from ..common.colors import hex_to_rgb as _rgb_from_hex  # noqa: E402
from ..common.colors import rgb_to_hex as _hex_from_rgb  # noqa: E402
from ..common.colors import srgb_to_linear as _srgb_to_linear  # noqa: E402
from ..common.colors import linear_to_srgb as _linear_to_srgb  # noqa: E402


def _get_paint_image(obj):
    """Find the MMU paint texture image on the object's material, or None."""
    if not obj or not obj.data or not obj.data.materials:
        return None
    for mat in obj.data.materials:
        if mat and mat.use_nodes:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    return node.image
    return None


def _get_paint_mesh(context):
    """Return the active mesh if it has MMU paint data, else None."""
    obj = context.active_object
    if obj and obj.type == "MESH" and obj.data.get("3mf_is_paint_texture"):
        return obj.data
    return None


def _sync_filaments_from_mesh(context):
    """
    Load the filament palette from the active mesh's custom properties
    into the scene-level MMUPaintSettings collection.
    """
    settings = context.scene.mmu_paint
    mesh = _get_paint_mesh(context)

    if mesh is None:
        settings.filaments.clear()
        settings.loaded_mesh_name = ""
        return

    # Already in sync?
    if settings.loaded_mesh_name == mesh.name and len(settings.filaments) > 0:
        return

    colors_str = mesh.get("3mf_paint_extruder_colors", "")
    if not colors_str:
        settings.filaments.clear()
        settings.loaded_mesh_name = ""
        return

    try:
        colors_dict = ast.literal_eval(colors_str)
    except (ValueError, SyntaxError):
        settings.filaments.clear()
        settings.loaded_mesh_name = ""
        return

    settings.filaments.clear()
    for idx in sorted(colors_dict.keys()):
        item = settings.filaments.add()
        item.index = idx
        item.name = f"Filament {idx + 1}"
        hex_col = colors_dict[idx]
        rgb = _rgb_from_hex(hex_col)
        item.color = rgb

    settings.loaded_mesh_name = mesh.name
    # Clamp active index
    if settings.active_filament_index >= len(settings.filaments):
        settings.active_filament_index = 0


def _write_colors_to_mesh(context):
    """Write the current filament palette back to the mesh custom property."""
    mesh = _get_paint_mesh(context)
    if mesh is None:
        return
    settings = context.scene.mmu_paint
    colors_dict = {}
    for item in settings.filaments:
        colors_dict[item.index] = _hex_from_rgb(*item.color)
    mesh["3mf_paint_extruder_colors"] = str(colors_dict)


def _configure_paint_brush(context):
    """
    Configure or create a texture paint brush for MMU painting.

    Blender 4.x: Create/get a custom '3MF Paint' brush and assign it.
    Blender 5.0+: Configure the currently active brush (read-only assignment).

    Returns the brush object or None.
    """
    ts = context.tool_settings

    if bpy.app.version >= (5, 0, 0):
        # Blender 5.0+: Configure active brush (read-only assignment)
        brush = ts.image_paint.brush if ts.image_paint else None
        if brush is None:
            return None
    else:
        # Blender 4.x: Create/get custom brush and assign it
        brush_name = "3MF Paint"
        brush = bpy.data.brushes.get(brush_name)
        if brush is None:
            brush = bpy.data.brushes.new(name=brush_name, mode="TEXTURE_PAINT")
        # Try to assign (writable in 4.x)
        try:
            ts.image_paint.brush = brush
        except AttributeError:
            pass  # Fall back to active brush if assignment fails

    # Configure brush settings (common to both versions)
    if brush:
        brush.blend = "MIX"
        brush.strength = 1.0
        brush.curve_distance_falloff_preset = "CONSTANT"

    return brush


def _set_brush_color(context, color_rgb):
    """Set the active texture paint brush color to the given (r, g, b) sRGB tuple.

    The palette stores colors as raw sRGB values (matching the hex colors in the
    3MF file).  Blender's brush.color expects **linear** values — it will convert
    linear → sRGB internally when writing to an sRGB-tagged image.  We therefore
    convert sRGB → linear here so that the painted pixels end up with the same
    raw sRGB values that the import renderer wrote via foreach_set.

    CRITICAL: Blender has a "Unified Color" system where the paint color can be
    stored either in the brush OR in the unified paint settings (shared across all
    brushes).  We set BOTH to ensure the color updates correctly.
    """
    # Ensure we have a proper 3-element tuple
    color_rgb = tuple(color_rgb[:3])

    # Convert sRGB → linear so Blender's paint system round-trips correctly.
    linear_rgb = (
        _srgb_to_linear(color_rgb[0]),
        _srgb_to_linear(color_rgb[1]),
        _srgb_to_linear(color_rgb[2]),
    )

    ts = context.tool_settings
    if not ts.image_paint:
        return

    brush = ts.image_paint.brush
    if not brush:
        return

    try:
        # 1. Set brush color (used when unified color is OFF)
        brush.color = linear_rgb

        # 2. Set unified paint settings color (used when unified color is ON)
        # This is the key - most users have "Unified Color" enabled by default
        # ts.image_paint is the Paint settings object with unified_paint_settings
        ups = ts.image_paint.unified_paint_settings
        if ups:
            # ALWAYS set the unified color - this is what actually controls the paint color
            # when "use_unified_color" is enabled (which is the default)
            ups.color = linear_rgb

        # 3. Force UI refresh to show the new color
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    except Exception:
        pass


# ===================================================================
#  Property update callbacks
# ===================================================================


def _on_active_filament_changed(self, context):
    """When user selects a different filament in the list, update brush color."""
    try:
        settings = context.scene.mmu_paint
        idx = settings.active_filament_index
        if 0 <= idx < len(settings.filaments):
            color = tuple(settings.filaments[idx].color[:])
            _set_brush_color(context, color)
    except Exception:
        pass  # Silently ignore context errors during undo/redo


# ===================================================================
#  Operators
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
        # more UV space and reducing blurriness.  ~2° is conservative
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

        # Switch to Texture Paint mode FIRST — ts.image_paint / brush
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
        "and viewport display colors, then populates the filament list"
    )
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (
            obj is not None
            and obj.type == "MESH"
            and obj.data.materials
            and not obj.data.get("3mf_is_paint_texture")
        )

    def execute(self, context):
        obj = context.active_object
        settings = context.scene.mmu_paint
        colors = _collect_material_colors(obj)

        if not colors:
            self.report({"WARNING"}, "No colors detected in materials")
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
        self.report({"INFO"}, f"Detected {len(colors)} colors from materials")
        return {"FINISHED"}


# -------------------------------------------------------------------
#  Material / node-tree color detection helpers
# -------------------------------------------------------------------

def _collect_material_colors(obj):
    """Extract unique colors from material slots on *obj*.

    Walks every material's node tree looking for Principled BSDF nodes.
    If the Base Color input is linked to a Color Ramp, all stop colors
    are collected.  Other simple nodes (RGB, Hue/Sat, Mix) yield a
    single color.  Unlinked Base Color default values are read directly.
    As a last resort the material's viewport ``diffuse_color`` is used.

    Near-duplicate colors (within ~5/255) are merged.
    """
    raw_colors = []

    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            continue

        found = []
        if mat.use_nodes and mat.node_tree:
            found = _extract_node_colors(mat.node_tree)

        # Fallback: viewport display color (already sRGB)
        if not found:
            dc = mat.diffuse_color
            found = [(dc[0], dc[1], dc[2])]

        raw_colors.extend(found)

    return _deduplicate_colors(raw_colors, tolerance=5.0 / 255.0)


def _extract_node_colors(node_tree):
    """Return a list of (r, g, b) sRGB tuples from the node tree.

    Looks at every Principled BSDF and extracts colors from whatever
    feeds its Base Color input.
    """
    colors = []
    for node in node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue

        base_input = node.inputs.get("Base Color")
        if base_input is None:
            continue

        if base_input.is_linked:
            linked_node = base_input.links[0].from_node
            colors.extend(_colors_from_node(linked_node))
        else:
            v = base_input.default_value
            colors.append((
                _linear_to_srgb(v[0]),
                _linear_to_srgb(v[1]),
                _linear_to_srgb(v[2]),
            ))

    return colors


def _colors_from_node(node):
    """Extract one or more (r, g, b) sRGB colors from a shader node.

    Supported node types:
    - **Color Ramp** (``VALTORGB``) — every color stop
    - **RGB** — the node's output value
    - **Hue/Sat, Gamma, Bright/Contrast** — the Color input default
    - **Mix / Mix RGB** — both A and B input defaults
    - **Separate/Combine** and other connector nodes — walks upstream
    """
    # ---- Color Ramp (most important for procedural setups) ----
    if node.type == "VALTORGB":
        found = []
        for stop in node.color_ramp.elements:
            c = stop.color  # linear RGBA
            found.append((
                _linear_to_srgb(c[0]),
                _linear_to_srgb(c[1]),
                _linear_to_srgb(c[2]),
            ))
        return found

    # ---- RGB node ----
    if node.type == "RGB":
        v = node.outputs[0].default_value
        return [(
            _linear_to_srgb(v[0]),
            _linear_to_srgb(v[1]),
            _linear_to_srgb(v[2]),
        )]

    # ---- Adjustment nodes with a Color input ----
    if node.type in ("HUE_SAT", "GAMMA", "BRIGHTCONTRAST"):
        inp = node.inputs.get("Color")
        if inp and not inp.is_linked:
            v = inp.default_value
            return [(
                _linear_to_srgb(v[0]),
                _linear_to_srgb(v[1]),
                _linear_to_srgb(v[2]),
            )]
        if inp and inp.is_linked:
            return _colors_from_node(inp.links[0].from_node)

    # ---- Mix / Mix RGB — collect both sides ----
    if node.type in ("MIX", "MIX_RGB"):
        found = []
        a_input = node.inputs.get("A") or node.inputs.get(6)
        b_input = node.inputs.get("B") or node.inputs.get(7)
        for inp in (a_input, b_input):
            if inp is None:
                continue
            if inp.is_linked:
                found.extend(_colors_from_node(inp.links[0].from_node))
            else:
                v = inp.default_value
                found.append((
                    _linear_to_srgb(v[0]),
                    _linear_to_srgb(v[1]),
                    _linear_to_srgb(v[2]),
                ))
        return found

    # ---- Walk upstream through passthrough / connector nodes ----
    color_input = node.inputs.get("Color") or node.inputs.get(0)
    if color_input and color_input.is_linked:
        return _colors_from_node(color_input.links[0].from_node)

    return []


def _deduplicate_colors(colors, tolerance=0.02):
    """Remove near-duplicate (r, g, b) tuples, preserving order."""
    unique = []
    for c in colors:
        is_dup = False
        for u in unique:
            if all(abs(a - b) < tolerance for a, b in zip(c, u)):
                is_dup = True
                break
        if not is_dup:
            unique.append(c)
    return unique


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
    """Reassign a filament color — replaces all pixels of old color with new color"""

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
        """User dismissed the popup — stay in Object mode."""
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


# ===================================================================
#  UIList
# ===================================================================


class MMU_UL_init_filaments(bpy.types.UIList):
    """Two-column initialization filament list: color picker + name."""

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_property, index
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            # Editable color swatch column
            swatch = row.row()
            swatch.ui_units_x = 1.5
            swatch.prop(item, "color", text="")
            # Wider name column
            row.label(text=item.name)
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.prop(item, "color", text="")


class MMU_UL_filaments(bpy.types.UIList):
    """Two-column filament list: color swatch + name label."""

    def draw_item(
        self, context, layout, data, item, icon, active_data, active_property, index
    ):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            # Skinny color swatch column (read-only display)
            swatch = row.row()
            swatch.ui_units_x = 1.5
            swatch.enabled = False  # Make read-only
            swatch.prop(item, "color", text="")
            # Wider name column
            row.label(text=item.name)
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.prop(item, "color", text="")


# ===================================================================
#  Panel
# ===================================================================


class VIEW3D_PT_mmu_paint(bpy.types.Panel):
    """MMU Paint Suite — multi-filament texture painting for 3MF export."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "MMU Paint"
    bl_context = "imagepaint"

    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None and context.active_object.type == "MESH"
        )

    def draw(self, context):
        layout = self.layout
        settings = context.scene.mmu_paint
        mesh = _get_paint_mesh(context)

        if mesh is None:
            # ============================
            #  STATE A: Uninitialized
            # ============================
            box = layout.box()
            box.label(text="Setup MMU Painting", icon="BRUSH_DATA")

            # Initialize list if empty
            if len(settings.init_filaments) == 0:
                box.operator(
                    "mmu.reset_init_filaments",
                    text="Create Default Palette",
                    icon="ADD",
                )
            else:
                # Show filament list
                row = box.row()
                row.template_list(
                    "MMU_UL_init_filaments",
                    "",
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

                # Reset and Initialize buttons
                row = box.row(align=True)
                row.operator("mmu.reset_init_filaments", icon="FILE_REFRESH")
                row.operator("mmu.initialize_painting", icon="PLAY", text="Initialize")

                # UV method setting
                uv_box = box.box()
                uv_box.prop(settings, "uv_method")
                if settings.uv_method == "LIGHTMAP":
                    uv_box.prop(settings, "lightmap_divisions")

                # Bake to MMU — for procedural/complex materials
                obj = context.active_object
                if obj and obj.data.materials and obj.data.materials[0]:
                    layout.separator()
                    bake_box = layout.box()
                    bake_box.label(text="From Existing Material", icon="RENDER_STILL")
                    bake_row = bake_box.row()
                    bake_row.scale_y = 1.2
                    bake_row.operator("mmu.bake_to_mmu", icon="RENDER_STILL")
                    detect_row = bake_box.row()
                    detect_row.operator(
                        "mmu.detect_material_colors", icon="MATERIAL",
                    )
                    info = bake_box.column(align=True)
                    info.scale_y = 0.7
                    info.label(text="Bake a procedural material to")
                    info.label(text="discrete filament colors for export")

        else:
            # ============================
            #  STATE B: Active palette
            # ============================

            # --- Filament list ---
            box = layout.box()
            box.label(text="Filament Palette", icon="COLOR")

            row = box.row()
            row.template_list(
                "MMU_UL_filaments",
                "",
                settings,
                "filaments",
                settings,
                "active_filament_index",
                rows=3,
                maxrows=6,
            )

            # Add/Remove buttons
            col = row.column(align=True)
            col.operator("mmu.add_filament", icon="ADD", text="")
            col.operator("mmu.remove_filament", icon="REMOVE", text="")

            # Reassign color button below list
            box.operator("mmu.reassign_filament_color", icon="COLORSET_01_VEC")

            # --- Brush falloff warning ---
            brush = context.tool_settings.image_paint.brush
            if brush:
                is_constant = False
                try:
                    is_constant = brush.curve_distance_falloff_preset == "CONSTANT"
                except AttributeError:
                    pass

                if not is_constant:
                    warn_box = layout.box()
                    warn_box.alert = True
                    warn_box.label(text="Soft edges will cause banding", icon="ERROR")
                    warn_box.label(text="issues on export")
                    warn_box.operator("mmu.fix_falloff", icon="CHECKMARK")

            # --- Quantize button ---
            layout.separator()
            quant_box = layout.box()
            quant_box.label(text="Cleanup", icon="BRUSH_DATA")
            quant_box.operator("mmu.quantize_texture", icon="SNAP_ON")
            info = quant_box.column(align=True)
            info.scale_y = 0.7
            info.label(text="Snap all pixels to the nearest")
            info.label(text="filament color to clean up edges")


# ===================================================================
#  Object-switch handler
# ===================================================================

_last_active_object_name = ""


def _on_depsgraph_update(scene, depsgraph=None):
    """Re-sync the panel palette when the active object changes."""
    global _last_active_object_name

    try:
        ctx = bpy.context
        obj = ctx.active_object
        current_name = obj.name if obj else ""

        if current_name != _last_active_object_name:
            _last_active_object_name = current_name
            if obj and obj.type == "MESH":
                settings = scene.mmu_paint
                settings.loaded_mesh_name = ""  # Force resync
                _sync_filaments_from_mesh(ctx)
    except Exception:
        pass  # Silently ignore context errors during undo/redo/render


# ===================================================================
#  Registration
# ===================================================================

# All classes to register, in dependency order (PropertyGroups first)
panel_classes = (
    MMUFilamentItem,
    MMUInitFilamentItem,
    MMUPaintSettings,
    MMU_OT_initialize,
    MMU_OT_add_init_filament,
    MMU_OT_remove_init_filament,
    MMU_OT_reset_init_filaments,
    MMU_OT_detect_material_colors,
    MMU_OT_select_filament,
    MMU_OT_reassign_filament_color,
    MMU_OT_add_filament,
    MMU_OT_remove_filament,
    MMU_OT_fix_falloff,
    MMU_OT_switch_to_paint,
    MMU_OT_import_paint_popup,
    MMU_UL_init_filaments,
    MMU_UL_filaments,
    VIEW3D_PT_mmu_paint,
)


def register():
    for cls in panel_classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.mmu_paint = bpy.props.PointerProperty(type=MMUPaintSettings)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)


def unregister():
    bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    del bpy.types.Scene.mmu_paint
    for cls in reversed(panel_classes):
        bpy.utils.unregister_class(cls)
