# Blender add-on to import and export 3MF files.
# Copyright (C) 2026 Jack
# This file is part of the Blender 3MF add-on and is distributed under GPL-3.0-or-later.

"""Blender authoring UI for OrcaSlicer/BambuStudio mixed-filament slots."""

import base64
import ast
import json
import os

import bpy
import numpy as np

from ..common.color_recipes import (
    PhysicalFilament,
    RecipeMode,
    lookup_standard_recipe,
    recommend_from_physical_filaments,
    recompute_mixed_slot_colors,
    slot_display_color,
)
from ..common.mixed_slots import (
    MAX_FILAMENT_SLOTS,
    MixedSlot,
    check_type_consistency,
    find_fixed_mixed_slot,
    format_components,
    parse_gradient_curve,
    sample_gradient_curve,
    mixed_slot_flags,
    parse_mixed_slots,
    parse_ratios,
    resize_slot_arrays,
    write_mixed_slots,
)
from ..common.colors import hex_to_rgb, rgb_to_hex
from ..common.filament_mixer import blend_color_multi
from ..common.constants import conflicting_mustpreserve_contents
from ..import_3mf.archive import get_stashed_config
from .roundtrip import collision_safe_palette


_CONFIG_PATH = "Metadata/project_settings.config"
_CONFIG_TEXT = f".3mf_config/{_CONFIG_PATH}"
_SEQUENCE_TEXT = ".3mf_preserved/Metadata/filament_sequence.json"


def _read_project_settings():
    text = bpy.data.texts.get(_CONFIG_TEXT)
    if text is not None and text.as_string() == conflicting_mustpreserve_contents:
        raise ValueError(
            "Imported project settings conflict. Resolve the 3MF config conflict "
            "before editing filament slots."
        )
    raw = get_stashed_config(_CONFIG_PATH)
    if raw is not None:
        settings = json.loads(raw.decode("utf-8"))
    elif text is not None:
        raise ValueError("The stashed project settings are invalid or unreadable.")
    else:
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
            "orca_project_template.json",
        )
        with open(template_path, "r", encoding="utf-8") as template:
            settings = json.load(template)
    if not isinstance(settings, dict):
        raise ValueError("Project settings must be a JSON object.")
    return settings


def _write_project_settings(settings):
    text = bpy.data.texts.get(_CONFIG_TEXT)
    if text is not None and text.as_string() == conflicting_mustpreserve_contents:
        raise ValueError(
            "Imported project settings conflict. Resolve the 3MF config conflict "
            "before editing filament slots."
        )
    encoded = base64.b85encode(
        json.dumps(settings, indent=4, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    if text is None:
        text = bpy.data.texts.new(_CONFIG_TEXT)
    text.clear()
    text.write(encoded)


def _colors(settings):
    values = settings.get("filament_colour")
    if not isinstance(values, list):
        raise ValueError("Project settings do not contain a filament_colour array.")
    if len(values) > MAX_FILAMENT_SLOTS:
        raise ValueError(f"Project has more than {MAX_FILAMENT_SLOTS} filament slots.")
    return [str(value) for value in values]


def _physical_count(settings):
    from ..common.mixed_slots import mixed_slot_flags

    flags = mixed_slot_flags(settings, len(_colors(settings)))
    return sum(not flag for flag in flags)


def _refresh_paint_meshes(settings):
    """Keep paint images and their slot-code maps aligned with project colors."""
    colors = _colors(settings)
    safe_colors = collision_safe_palette(dict(enumerate(colors)))
    for mesh in bpy.data.meshes:
        if not mesh.get("3mf_is_paint_texture"):
            continue
        try:
            old_display = {
                int(index): str(color)
                for index, color in ast.literal_eval(
                    mesh.get("3mf_paint_extruder_colors", "{}")
                ).items()
            }
            old_paint = {
                int(index): str(color)
                for index, color in ast.literal_eval(
                    mesh.get("3mf_paint_internal_colors", mesh.get("3mf_paint_extruder_colors", "{}")),
                ).items()
            }
        except (SyntaxError, TypeError, ValueError):
            continue

        updated_display = {
            index: colors[index] if index < len(colors) else color
            for index, color in old_display.items()
        }
        updated_display.update({
            index: color
            for index, color in enumerate(colors)
            if index not in updated_display
        })
        updated_paint = {
            index: safe_colors.get(index, color)
            for index, color in old_paint.items()
        }
        updated_paint.update({
            index: color
            for index, color in safe_colors.items()
            if index not in updated_paint
        })
        if updated_display == old_display and updated_paint == old_paint:
            continue

        image = None
        for material in mesh.materials:
            if material is None or not material.use_nodes or material.node_tree is None:
                continue
            image_node = next(
                (node for node in material.node_tree.nodes
                 if node.type == "TEX_IMAGE" and node.image is not None),
                None,
            )
            if image_node is not None:
                image = image_node.image
                break
        if image is not None:
            pixels = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            pixels = pixels.reshape(image.size[1], image.size[0], 4)
            tolerance = 3.0 / 255.0
            for index, old_hex in old_paint.items():
                new_hex = updated_paint.get(index, old_hex)
                if new_hex == old_hex:
                    continue
                old_rgb = np.asarray(hex_to_rgb(old_hex), dtype=np.float32)
                new_rgb = np.asarray(hex_to_rgb(new_hex), dtype=np.float32)
                mask = np.all(np.abs(pixels[:, :, :3] - old_rgb) < tolerance, axis=2)
                pixels[mask, :3] = new_rgb
            image.pixels.foreach_set(pixels.ravel())
            image.update()

        mesh["3mf_paint_extruder_colors"] = str(updated_display)
        mesh["3mf_paint_internal_colors"] = str(updated_paint)
        flags = mixed_slot_flags(settings, len(colors))
        mesh["3mf_num_physical_filaments"] = sum(not flag for flag in flags)
        if "3mf_paint_source_signature" in mesh:
            del mesh["3mf_paint_source_signature"]
        mesh["3mf_paint_mixed_slots"] = [
            index for index, is_mixed in enumerate(flags) if is_mixed
        ]


def _save_and_refresh(settings, context):
    palette = _colors(settings)
    mixed_slots = parse_mixed_slots(settings)
    colors = recompute_mixed_slot_colors(palette, mixed_slots)
    settings["filament_colour"] = colors
    if isinstance(settings.get("filament_multi_colour"), list):
        multi = list(settings["filament_multi_colour"])
        if len(multi) < len(colors):
            multi.extend(colors[len(multi):])
        for slot in mixed_slots:
            multi[slot.slot] = colors[slot.slot]
        settings["filament_multi_colour"] = multi[:len(colors)]
    _write_project_settings(settings)
    _refresh_paint_meshes(settings)
    paint_settings = getattr(context.scene, "mmu_paint", None)
    if paint_settings is not None:
        paint_settings.loaded_mesh_name = ""
        from .helpers import _set_brush_color, _sync_filaments_from_mesh

        _sync_filaments_from_mesh(context)
        index = paint_settings.active_filament_index
        if 0 <= index < len(paint_settings.filaments):
            _set_brush_color(
                context, paint_settings.filaments[index].paint_color[:]
            )


def _replace_setting_text(text, replacement):
    text.clear()
    text.write(base64.b85encode(
        json.dumps(replacement, separators=(",", ":")).encode("utf-8")
    ).decode("ascii"))


def _remap_slot_number(slot, operation, position, replacement=None):
    if not isinstance(slot, int) or slot < 1:
        return slot
    if operation == "INSERT":
        return slot + 1 if slot >= position else slot
    if slot == position:
        return replacement
    return slot - 1 if slot > position else slot


def _remap_json_slot_references(data, operation, position, replacement=None):
    if isinstance(data, dict):
        for key, value in list(data.items()):
            if key == "sequence" and isinstance(value, list):
                mapped = []
                for slot in value:
                    try:
                        numeric = int(slot)
                    except (TypeError, ValueError):
                        mapped.append(slot)
                        continue
                    mapped.append(_remap_slot_number(
                        numeric, operation, position, replacement,
                    ))
                data[key] = mapped
            else:
                _remap_json_slot_references(value, operation, position, replacement)


def _remap_object_settings(raw, operation, position, replacement):
    if not isinstance(raw, str):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw
    changed = _remap_json_extruders(data, operation, position, replacement)
    return json.dumps(data) if changed else raw


def _remap_json_extruders(data, operation, position, replacement):
    changed = False
    if isinstance(data, dict):
        for key, value in list(data.items()):
            if key == "extruder":
                try:
                    data[key] = str(_remap_slot_number(
                        int(value), operation, position, replacement,
                    ))
                    changed = True
                except (TypeError, ValueError):
                    pass
            elif _remap_json_extruders(value, operation, position, replacement):
                changed = True
    elif isinstance(data, list):
        for item in data:
            if _remap_json_extruders(item, operation, position, replacement):
                changed = True
    return changed


def _remap_scene_slots(operation, position, replacement=None):
    for obj in bpy.data.objects:
        for key in ("3mf_object_extruder", "3mf_paint_default_extruder"):
            if key in obj:
                obj[key] = _remap_slot_number(int(obj[key]), operation, position, replacement)
        for key in ("3mf_orca_settings", "3mf_orca_wrapper_settings"):
            if key in obj:
                obj[key] = _remap_object_settings(
                    obj[key], operation, position, replacement,
                )

    for material in bpy.data.materials:
        if "3mf_filament_slot" in material:
            material["3mf_filament_slot"] = _remap_slot_number(
                int(material["3mf_filament_slot"]), operation, position, replacement,
            )

    for mesh in bpy.data.meshes:
        if not mesh.get("3mf_is_paint_texture"):
            continue
        for key in ("3mf_paint_default_extruder",):
            if key in mesh:
                mesh[key] = _remap_slot_number(
                    int(mesh[key]), operation, position, replacement,
                )
        for key in ("3mf_paint_extruder_colors", "3mf_paint_internal_colors"):
            try:
                palette = {
                    int(index): color
                    for index, color in ast.literal_eval(mesh.get(key, "{}")).items()
                }
            except (SyntaxError, TypeError, ValueError):
                continue
            if operation == "DELETE" and key == "3mf_paint_internal_colors":
                old_color = palette.get(position - 1)
                new_color = palette.get(replacement - 1) if replacement else None
                if old_color and new_color and old_color != new_color:
                    image = _image_for_mesh(mesh)
                    if image is not None:
                        _replace_image_color(image, old_color, new_color)
            mapped = {}
            for index, color in palette.items():
                old_slot = index + 1
                new_slot = _remap_slot_number(
                    old_slot, operation, position, replacement,
                )
                if new_slot is not None:
                    mapped[new_slot - 1] = color
            mesh[key] = str(mapped)
        mixed_slots = mesh.get("3mf_paint_mixed_slots")
        if mixed_slots is not None:
            new_mixed = []
            for index in mixed_slots:
                new_index = _remap_slot_number(
                    int(index) + 1, operation, position, replacement,
                )
                if new_index is not None:
                    new_mixed.append(new_index - 1)
            mesh["3mf_paint_mixed_slots"] = new_mixed
        if "3mf_num_physical_filaments" in mesh:
            mesh["3mf_num_physical_filaments"] = max(
                0,
                int(mesh["3mf_num_physical_filaments"])
                + (1 if operation == "INSERT" else -1),
            )
        if operation in {"INSERT", "DELETE"} and "3mf_paint_source_signature" in mesh:
            del mesh["3mf_paint_source_signature"]

    for text_name, remapper in (
        (_SEQUENCE_TEXT, _remap_json_slot_references),
        (f".3mf_config/Metadata/model_settings.config", _remap_json_extruders),
    ):
        text = bpy.data.texts.get(text_name)
        if text is None or text.as_string() == conflicting_mustpreserve_contents:
            continue
        try:
            data = json.loads(base64.b85decode(text.as_string().encode("ascii")))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        remapper(data, operation, position, replacement)
        _replace_setting_text(text, data)


def _image_for_mesh(mesh):
    for material in mesh.materials:
        if material is None or not material.use_nodes or material.node_tree is None:
            continue
        node = next(
            (item for item in material.node_tree.nodes
             if item.type == "TEX_IMAGE" and item.image is not None),
            None,
        )
        if node is not None:
            return node.image
    return None


def _replace_image_color(image, old_hex, new_hex):
    old_rgb = np.asarray(hex_to_rgb(old_hex), dtype=np.float32)
    new_rgb = np.asarray(hex_to_rgb(new_hex), dtype=np.float32)
    pixel_count = image.size[0] * image.size[1] * 4
    chunk_size = 1_048_576
    tolerance = 3.0 / 255.0
    for start in range(0, pixel_count, chunk_size):
        end = min(start + chunk_size, pixel_count)
        values = np.asarray(image.pixels[start:end], dtype=np.float32).reshape(-1, 4)
        mask = np.all(np.abs(values[:, :3] - old_rgb) < tolerance, axis=1)
        values[mask, :3] = new_rgb
        image.pixels[start:end] = values.ravel().tolist()
    image.update()


def _resize_slots(settings, sources, new_slots=()):
    old_count = len(_colors(settings))
    resize_slot_arrays(
        settings, old_count, sources, new_slots=new_slots,
    )


def _type_for_slot(settings, slot):
    values = settings.get("filament_type")
    return str(values[slot - 1]) if isinstance(values, list) and slot <= len(values) else ""


def _validate_recipe(settings, components, ratios, gradient):
    physical_count = _physical_count(settings)
    if len(components) not in (2, 3) or len(set(components)) != len(components):
        raise ValueError("A mix requires two or three distinct physical filaments.")
    if any(component < 1 or component > physical_count for component in components):
        raise ValueError("Mixed-filament components must reference physical slots.")
    if any(ratio < 10 for ratio in ratios) or sum(ratios) != 100:
        raise ValueError("Ratios must total 100%, with at least 10% per component.")
    if gradient and len(components) != 2:
        raise ValueError("Gradient mixes require exactly two components.")
    types = [_type_for_slot(settings, component) for component in components]
    known_types = [value for value in types if value]
    if len(set(known_types)) > 1:
        raise ValueError("OrcaSlicer does not allow mixing different filament types.")


def _gradient_preview_colors(settings, components, curve_text, gradient_range):
    colors = _colors(settings)
    physical_count = _physical_count(settings)
    if len(components) != 2 or any(
        component < 1 or component > physical_count for component in components
    ):
        raise ValueError("Gradient preview requires two valid physical filaments.")
    component_colors = [colors[component - 1] for component in components]
    curve = parse_gradient_curve(curve_text)
    start, end = gradient_range or (0.9, 0.1)
    samples = []
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        ratio = (
            sample_gradient_curve(curve, t)
            if curve
            else start + (end - start) * t
        )
        weight_a = int(round(ratio * 10000))
        samples.append(hex_to_rgb(blend_color_multi(
            component_colors, [weight_a, 10000 - weight_a],
        )))
    return samples


def _new_mixed_slot(settings, components, ratios, gradient=False, gradient_range=None, curve=None, per_part=False):
    palette = _colors(settings)
    count = len(palette)
    _validate_recipe(settings, components, ratios, gradient)
    if not gradient:
        existing = find_fixed_mixed_slot(settings, components, ratios)
        if existing >= 0:
            return existing + 1, False
    if count >= MAX_FILAMENT_SLOTS:
        raise ValueError(f"Maximum of {MAX_FILAMENT_SLOTS} filament slots reached.")
    sources = list(range(count)) + ([count - 1] if count else [])
    _resize_slots(settings, sources, {count})
    slot = MixedSlot(
        slot=count,
        components=list(components),
        ratios=[ratio / 100.0 for ratio in ratios],
        gradient=gradient,
        gradient_range=gradient_range,
        gradient_curve=curve or [],
        per_part=per_part,
        dirty=True,
    )
    slots = parse_mixed_slots(settings)
    slots.append(slot)
    write_mixed_slots(settings, slots, count + 1)
    colors = _colors(settings)
    colors[count] = slot_display_color(slot, colors)
    settings["filament_colour"] = colors
    if isinstance(settings.get("filament_multi_colour"), list):
        multi = list(settings["filament_multi_colour"])
        if len(multi) < count + 1:
            multi.extend([colors[count]] * (count + 1 - len(multi)))
        multi[count] = colors[count]
        settings["filament_multi_colour"] = multi
    return count + 1, True


def _edit_mixed_slot(settings, slot_index, components, ratios, gradient, gradient_range, curve_text, per_part):
    slots = parse_mixed_slots(settings)
    slot = next((entry for entry in slots if entry.slot == slot_index), None)
    if slot is None:
        raise ValueError("The selected slot is not a mixed filament.")
    _validate_recipe(settings, components, ratios, gradient)
    curve = parse_gradient_curve(curve_text) if curve_text else []
    if curve_text and len(curve) < 2:
        raise ValueError("Gradient curve needs at least two valid control points.")
    slot.components = list(components)
    slot.ratios = [ratio / 100.0 for ratio in ratios]
    slot.gradient = gradient
    slot.gradient_range = gradient_range if gradient else None
    slot.gradient_curve = curve if gradient else []
    slot.per_part = per_part if gradient else False
    slot.raw = {}
    slot.dirty = True
    write_mixed_slots(settings, slots, len(_colors(settings)))


def _delete_mixed_slot(settings, slot_index):
    count = len(_colors(settings))
    if not any(slot.slot == slot_index for slot in parse_mixed_slots(settings)):
        raise ValueError("The selected slot is not a mixed filament.")
    sources = [index for index in range(count) if index != slot_index]
    _resize_slots(settings, sources)


def _insert_physical_slot(settings, color, filament_type=""):
    colors = _colors(settings)
    if len(colors) >= MAX_FILAMENT_SLOTS:
        raise ValueError(f"Maximum of {MAX_FILAMENT_SLOTS} filament slots reached.")
    flags = mixed_slot_flags(settings, len(colors))
    insert_at = next((index for index, mixed in enumerate(flags) if mixed), len(colors))
    sources = (
        list(range(insert_at))
        + [max(0, insert_at - 1)]
        + list(range(insert_at, len(colors)))
    )
    _resize_slots(settings, sources, {insert_at})

    color = rgb_to_hex(*hex_to_rgb(color)) if isinstance(color, str) else rgb_to_hex(*color)
    settings["filament_colour"][insert_at] = color
    types = settings.get("filament_type")
    if isinstance(types, list) and insert_at < len(types):
        types[insert_at] = filament_type or types[insert_at]
    multi = settings.get("filament_multi_colour")
    if isinstance(multi, list):
        if len(multi) <= insert_at:
            multi.extend([color] * (insert_at + 1 - len(multi)))
        multi[insert_at] = color
    _remap_scene_slots("INSERT", insert_at + 1)
    return insert_at + 1


class MMU_OT_add_project_filament(bpy.types.Operator):
    bl_idname = "mmu.add_project_filament"
    bl_label = "Add Physical Filament"
    bl_options = {"REGISTER", "UNDO"}

    color: bpy.props.FloatVectorProperty(
        name="Color", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
        default=(0.8, 0.8, 0.8),
    )
    filament_type: bpy.props.StringProperty(name="Filament Type", default="PLA Basic")

    def invoke(self, context, event):
        try:
            settings = _read_project_settings()
            self.filament_type = _type_for_slot(settings, 1) or "PLA Basic"
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "color")
        self.layout.prop(self, "filament_type")

    def execute(self, context):
        try:
            settings = _read_project_settings()
            _insert_physical_slot(settings, self.color, self.filament_type)
            _save_and_refresh(settings, context)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Physical filament added before the mixed-slot tail.")
        return {"FINISHED"}


class MMU_OT_edit_project_filament(bpy.types.Operator):
    bl_idname = "mmu.edit_project_filament"
    bl_label = "Edit Physical Filament"
    bl_options = {"REGISTER", "UNDO"}

    slot_index: bpy.props.IntProperty(min=1, max=MAX_FILAMENT_SLOTS)
    color: bpy.props.FloatVectorProperty(
        name="Color", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
        default=(0.8, 0.8, 0.8),
    )
    filament_type: bpy.props.StringProperty(name="Filament Type", default="PLA Basic")

    def invoke(self, context, event):
        try:
            settings = _read_project_settings()
            index = self.slot_index - 1
            self.color = hex_to_rgb(_colors(settings)[index])
            self.filament_type = _type_for_slot(settings, self.slot_index) or "PLA Basic"
        except (IndexError, ValueError, OSError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "color")
        self.layout.prop(self, "filament_type")

    def execute(self, context):
        try:
            settings = _read_project_settings()
            index = self.slot_index - 1
            if index < 0 or index >= len(_colors(settings)):
                raise ValueError("The selected filament slot does not exist.")
            from ..common.mixed_slots import mixed_slot_flags

            if mixed_slot_flags(settings, len(_colors(settings)))[index]:
                raise ValueError("Edit mixed slots with the mixed-filament editor.")
            color = rgb_to_hex(*self.color)
            settings["filament_colour"][index] = color
            types = settings.get("filament_type")
            if isinstance(types, list) and index < len(types):
                types[index] = self.filament_type
            if isinstance(settings.get("filament_multi_colour"), list):
                settings["filament_multi_colour"][index] = color
            _save_and_refresh(settings, context)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MMU_OT_add_mixed_project_filament(bpy.types.Operator):
    bl_idname = "mmu.add_mixed_project_filament"
    bl_label = "Add Mixed Filament"
    bl_options = {"REGISTER", "UNDO"}

    component_a: bpy.props.IntProperty(name="Component A", min=1, max=MAX_FILAMENT_SLOTS, default=1)
    component_b: bpy.props.IntProperty(name="Component B", min=1, max=MAX_FILAMENT_SLOTS, default=2)
    ratio_a: bpy.props.IntProperty(name="A %", min=10, max=90, default=50)
    gradient: bpy.props.BoolProperty(name="Gradient", default=False)

    def invoke(self, context, event):
        try:
            settings = _read_project_settings()
            count = _physical_count(settings)
            if count >= 2:
                self.component_a, self.component_b = 1, 2
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        col = self.layout.column(align=True)
        col.prop(self, "component_a")
        col.prop(self, "component_b")
        col.prop(self, "ratio_a", slider=True)
        col.prop(self, "gradient")

    def execute(self, context):
        try:
            settings = _read_project_settings()
            slot_number, created = _new_mixed_slot(
                settings,
                [self.component_a, self.component_b],
                [self.ratio_a, 100 - self.ratio_a],
                gradient=self.gradient,
                gradient_range=(0.9, 0.1) if self.gradient else None,
            )
            _save_and_refresh(settings, context)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not created:
            self.report({"INFO"}, f"An identical mixed filament already exists in slot {slot_number}.")
        return {"FINISHED"}


class MMU_OT_edit_mixed_project_filament(bpy.types.Operator):
    bl_idname = "mmu.edit_mixed_project_filament"
    bl_label = "Edit Mixed Filament"
    bl_options = {"REGISTER", "UNDO"}

    slot_index: bpy.props.IntProperty(min=1, max=MAX_FILAMENT_SLOTS)
    component_count: bpy.props.EnumProperty(
        name="Components",
        items=(("2", "Two", ""), ("3", "Three", "")),
        default="2",
    )
    component_a: bpy.props.IntProperty(name="Component A", min=1, max=MAX_FILAMENT_SLOTS, default=1)
    component_b: bpy.props.IntProperty(name="Component B", min=1, max=MAX_FILAMENT_SLOTS, default=2)
    component_c: bpy.props.IntProperty(name="Component C", min=1, max=MAX_FILAMENT_SLOTS, default=3)
    ratio_a: bpy.props.IntProperty(name="A %", min=10, max=90, default=50)
    ratio_b: bpy.props.IntProperty(name="B %", min=10, max=90, default=50)
    gradient: bpy.props.BoolProperty(name="Gradient", default=False)
    range_start: bpy.props.FloatProperty(name="Start Ratio", min=0.1, max=0.9, default=0.9, precision=3)
    range_end: bpy.props.FloatProperty(name="End Ratio", min=0.1, max=0.9, default=0.1, precision=3)
    per_part: bpy.props.BoolProperty(name="Per Part", default=False)
    curve: bpy.props.StringProperty(
        name="Curve anchors",
        description="Orca curve points: x,y[,incoming tangent,outgoing tangent]|...",
    )
    preview_start: bpy.props.FloatVectorProperty(
        name="0%", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
        default=(0.5, 0.5, 0.5),
    )
    preview_quarter: bpy.props.FloatVectorProperty(
        name="25%", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
        default=(0.5, 0.5, 0.5),
    )
    preview_middle: bpy.props.FloatVectorProperty(
        name="50%", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
        default=(0.5, 0.5, 0.5),
    )
    preview_three_quarters: bpy.props.FloatVectorProperty(
        name="75%", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
        default=(0.5, 0.5, 0.5),
    )
    preview_end: bpy.props.FloatVectorProperty(
        name="100%", subtype="COLOR_GAMMA", size=3, min=0.0, max=1.0,
        default=(0.5, 0.5, 0.5),
    )

    def invoke(self, context, event):
        try:
            settings = _read_project_settings()
            slot = next(
                item for item in parse_mixed_slots(settings)
                if item.slot == self.slot_index - 1
            )
            components = list(slot.components)
            ratios = [round(value * 100) for value in slot.ratios]
            self.component_count = "3" if len(components) == 3 else "2"
            self.component_a = components[0]
            self.component_b = components[1]
            self.component_c = components[2] if len(components) == 3 else 3
            self.ratio_a = ratios[0] if ratios else 50
            self.ratio_b = ratios[1] if len(ratios) > 1 else 50
            self.gradient = slot.gradient
            if slot.gradient_range:
                self.range_start, self.range_end = slot.gradient_range
            self.per_part = slot.per_part
            from ..common.mixed_slots import serialize_gradient_curve

            self.curve = serialize_gradient_curve(slot.gradient_curve)
        except (StopIteration, OSError, ValueError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, f"Unable to load mixed slot: {exc}")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "component_count")
        layout.prop(self, "component_a")
        layout.prop(self, "component_b")
        if self.component_count == "3":
            layout.prop(self, "component_c")
        layout.prop(self, "ratio_a", slider=True)
        if self.component_count == "3":
            layout.prop(self, "ratio_b", slider=True)
            layout.label(text=f"C: {100 - self.ratio_a - self.ratio_b}%")
        else:
            layout.label(text=f"B: {100 - self.ratio_a}%")
        layout.prop(self, "gradient")
        if self.gradient:
            layout.prop(self, "range_start")
            layout.prop(self, "range_end")
            layout.prop(self, "curve", text="Curve anchors")
            layout.prop(self, "per_part")
            try:
                preview_colors = _gradient_preview_colors(
                    _read_project_settings(),
                    [self.component_a, self.component_b],
                    self.curve,
                    (self.range_start, self.range_end),
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                layout.label(text=f"Ramp preview unavailable: {exc}", icon="ERROR")
            else:
                preview = layout.column(align=True)
                preview.label(text="A → B ramp samples")
                labels = preview.row(align=True)
                for label in ("0%", "25%", "50%", "75%", "100%"):
                    labels.label(text=label)
                swatches = preview.row(align=True)
                swatches.enabled = False
                for prop, color in zip(
                    (
                        "preview_start", "preview_quarter", "preview_middle",
                        "preview_three_quarters", "preview_end",
                    ),
                    preview_colors,
                ):
                    setattr(self, prop, color)
                    swatches.prop(self, prop, text="", emboss=False)

    def execute(self, context):
        try:
            settings = _read_project_settings()
            components = [self.component_a, self.component_b]
            ratios = [self.ratio_a, 100 - self.ratio_a]
            if self.component_count == "3":
                components.append(self.component_c)
                ratios = [self.ratio_a, self.ratio_b, 100 - self.ratio_a - self.ratio_b]
            if any(value < 10 for value in ratios):
                raise ValueError("Every component must be at least 10%.")
            gradient_range = (
                (self.range_start, self.range_end) if self.gradient else None
            )
            _edit_mixed_slot(
                settings, self.slot_index - 1, components, ratios,
                self.gradient, gradient_range, self.curve, self.per_part,
            )
            _save_and_refresh(settings, context)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MMU_OT_remove_project_filament(bpy.types.Operator):
    bl_idname = "mmu.remove_project_filament"
    bl_label = "Remove Physical Filament"
    bl_options = {"REGISTER", "UNDO"}

    slot_index: bpy.props.IntProperty(min=1, max=MAX_FILAMENT_SLOTS)
    replacement_slot: bpy.props.IntProperty(name="Replacement Slot", min=1, max=MAX_FILAMENT_SLOTS, default=1)

    def invoke(self, context, event):
        try:
            settings = _read_project_settings()
            colors = _colors(settings)
            self.replacement_slot = 2 if self.slot_index == 1 and len(colors) > 1 else 1
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Mixed recipes and assignments will be remapped.")
        layout.prop(self, "replacement_slot")

    def execute(self, context):
        try:
            settings = _read_project_settings()
            old_colors = _colors(settings)
            slot_index = self.slot_index - 1
            physical_count = _physical_count(settings)
            if slot_index < 0 or slot_index >= len(old_colors):
                raise ValueError("The selected slot does not exist.")
            from ..common.mixed_slots import mixed_slot_flags

            if mixed_slot_flags(settings, len(old_colors))[slot_index]:
                raise ValueError("Remove mixed slots with the mixed-filament controls.")
            if physical_count <= 1:
                raise ValueError("At least one physical filament must remain.")
            replacement = self.replacement_slot
            if (
                replacement < 1
                or replacement > physical_count
                or replacement == slot_index + 1
            ):
                raise ValueError("Choose a different existing physical replacement slot.")
            references = [
                mixed for mixed in parse_mixed_slots(settings)
                if slot_index + 1 in mixed.components
            ]
            if references:
                for mixed in references:
                    replaced = [
                        replacement if component == slot_index + 1
                        else component - (component > slot_index + 1)
                        for component in mixed.components
                    ]
                    if len(set(replaced)) != len(replaced):
                        raise ValueError(
                            f"Replacement would duplicate a component in mixed slot {mixed.slot + 1}."
                        )

            old_count = len(old_colors)
            sources = [index for index in range(old_count) if index != slot_index]
            replacement_after = replacement - (replacement > slot_index + 1)
            slots = parse_mixed_slots(settings)
            new_index = {old: new for new, old in enumerate(sources)}
            for mixed in slots:
                mixed.slot = new_index[mixed.slot]
                mixed.components = [
                    replacement_after if component == slot_index + 1
                    else component - (component > slot_index + 1)
                    for component in mixed.components
                ]
                if mixed.components:
                    mixed.raw = {}
                    mixed.dirty = True
            _resize_slots(settings, sources)
            write_mixed_slots(settings, slots, old_count - 1)
            _remap_scene_slots("DELETE", slot_index + 1, replacement_after)
            _save_and_refresh(settings, context)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MMU_OT_remove_mixed_project_filament(bpy.types.Operator):
    bl_idname = "mmu.remove_mixed_project_filament"
    bl_label = "Remove Mixed Filament"
    bl_options = {"REGISTER", "UNDO"}

    slot_index: bpy.props.IntProperty(min=1, max=MAX_FILAMENT_SLOTS)

    def execute(self, context):
        try:
            settings = _read_project_settings()
            _delete_mixed_slot(settings, self.slot_index - 1)
            _save_and_refresh(settings, context)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class MMU_OT_assign_project_filament(bpy.types.Operator):
    bl_idname = "mmu.assign_project_filament"
    bl_label = "Assign Filament to Object"
    bl_options = {"REGISTER", "UNDO"}

    slot_index: bpy.props.IntProperty(min=1, max=MAX_FILAMENT_SLOTS)

    def execute(self, context):
        obj = context.active_object
        if obj is None:
            self.report({"WARNING"}, "Select an object to assign a filament.")
            return {"CANCELLED"}
        try:
            settings = _read_project_settings()
            if not 1 <= self.slot_index <= len(_colors(settings)):
                raise ValueError("The selected filament slot does not exist.")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        obj["3mf_object_extruder"] = self.slot_index
        return {"FINISHED"}


class MMU_OT_recommend_project_filament(bpy.types.Operator):
    bl_idname = "mmu.recommend_project_filament"
    bl_label = "Recommend Mixed Filament"
    bl_options = {"REGISTER", "UNDO"}

    target_color: bpy.props.FloatVectorProperty(
        name="Target Color", subtype="COLOR_GAMMA", size=3,
        min=0.0, max=1.0, default=(0.5, 0.5, 0.5),
    )
    recipe_mode: bpy.props.EnumProperty(
        name="Recipe",
        items=(
            (RecipeMode.MATERIAL_LIST.value, "Physical Filaments", "Find a mix from this project palette"),
            (RecipeMode.CMYW.value, "CMYW", "Use OrcaSlicer's CMYW standard recipe"),
            (RecipeMode.RYBW.value, "RYBW", "Use OrcaSlicer's RYBW standard recipe"),
        ),
        default=RecipeMode.MATERIAL_LIST.value,
    )
    material_type: bpy.props.StringProperty(name="Material Type", default="PLA Basic")
    create_missing_base: bpy.props.BoolProperty(
        name="Add Missing Base Filaments",
        description="Add recipe base colors that are not already physical slots",
        default=False,
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(self, "target_color")
        self.layout.prop(self, "recipe_mode")
        self.layout.prop(self, "material_type")
        if self.recipe_mode != RecipeMode.MATERIAL_LIST.value:
            self.layout.prop(self, "create_missing_base")

    def execute(self, context):
        try:
            settings = _read_project_settings()
            colors = _colors(settings)
            types = settings.get("filament_type", [])
            flags = mixed_slot_flags(settings, len(colors))
            physical = [
                PhysicalFilament(
                    color_hex=color,
                    filament_index=index + 1,
                    filament_type=str(types[index]) if index < len(types) else "",
                    is_mixed=False,
                )
                for index, color in enumerate(colors)
                if not flags[index]
            ]
            rgb = tuple(round(channel * 255) for channel in self.target_color)
            if self.recipe_mode == RecipeMode.MATERIAL_LIST.value:
                recipe = recommend_from_physical_filaments(
                    rgb, physical, self.material_type,
                )
            else:
                recipe = lookup_standard_recipe(
                    rgb, self.recipe_mode, self.material_type,
                )
            if not recipe.valid:
                raise ValueError("No matching mixed-filament recipe was found.")
            if len(recipe.components) == 1:
                self.report(
                    {"INFO"},
                    f"Target matches physical slot {recipe.components[0].filament_index}; no mix created.",
                )
                return {"FINISHED"}
            components = []
            missing = []
            physical_by_color = {
                color.strip().upper(): index + 1
                for index, color in enumerate(colors)
                if not flags[index]
                and (
                    not self.material_type
                    or _type_for_slot(settings, index + 1) == self.material_type
                )
            }
            for component in recipe.components:
                color_key = component.color_hex.strip().upper()
                slot = component.filament_index
                if slot and self.material_type and (
                    _type_for_slot(settings, slot) != self.material_type
                ):
                    slot = 0
                if not slot:
                    slot = physical_by_color.get(color_key, 0)
                if slot:
                    components.append(slot)
                elif self.create_missing_base:
                    components.append(0)
                    missing.append(component)
                else:
                    raise ValueError(
                        "This recipe needs missing base filaments. Enable 'Add Missing Base "
                        "Filaments' to create compatible slots."
                    )
            if len(colors) + len(missing) + 1 > MAX_FILAMENT_SLOTS:
                raise ValueError("Not enough free slots for the base filaments and mix.")
            added_base_count = 0
            for index, slot in enumerate(components):
                if slot:
                    continue
                components[index] = _insert_physical_slot(
                    settings,
                    recipe.components[index].color_hex,
                    self.material_type,
                )
                added_base_count += 1
            ratios = [item.ratio for item in recipe.components]
            slot_number, created = _new_mixed_slot(settings, components, ratios)
            _save_and_refresh(settings, context)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"{'Created' if created else 'Reused'} recipe in slot {slot_number} "
            f"for {recipe.matched_color_hex}."
            + (f" Added {added_base_count} base filaments." if added_base_count else ""),
        )
        return {"FINISHED"}


def draw_project_filament_editor(layout, context):
    box = layout.box()
    box.label(text="3MF Filament Slots", icon="COLOR")
    try:
        settings = _read_project_settings()
        colors = _colors(settings)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        box.label(text=str(exc), icon="ERROR")
        return

    mixed = {slot.slot: slot for slot in parse_mixed_slots(settings)}
    display_colors = recompute_mixed_slot_colors(colors, list(mixed.values()))
    physical_count = _physical_count(settings)
    box.label(text=f"{physical_count} physical, {len(mixed)} mixed, {len(colors)}/{MAX_FILAMENT_SLOTS} total")
    if context.active_object is None:
        box.label(text="Select an object to assign a filament slot.", icon="INFO")

    for index, color in enumerate(display_colors):
        row = box.row(align=True)
        try:
            row.label(text="", icon="NODE_MATERIAL" if index in mixed else "MATERIAL")
            row.label(text=f"F{index + 1}" + ("  Mixed" if index in mixed else ""))
            row.label(text=color)
        except (IndexError, TypeError):
            row.label(text="Invalid color")
        if index in mixed:
            edit = row.operator("mmu.edit_mixed_project_filament", text="", icon="PREFERENCES")
            edit.slot_index = index + 1
            remove = row.operator("mmu.remove_mixed_project_filament", text="", icon="X")
            remove.slot_index = index + 1
        else:
            edit = row.operator("mmu.edit_project_filament", text="", icon="PREFERENCES")
            edit.slot_index = index + 1
            remove = row.operator("mmu.remove_project_filament", text="", icon="TRASH")
            remove.slot_index = index + 1
        if context.active_object is not None:
            assign = row.operator("mmu.assign_project_filament", text="", icon="OBJECT_DATA")
            assign.slot_index = index + 1

    row = box.row(align=True)
    add_physical = row.row(align=True)
    add_physical.enabled = len(colors) < MAX_FILAMENT_SLOTS
    add_physical.operator("mmu.add_project_filament", text="Add Physical", icon="ADD")
    add_mixed = row.row(align=True)
    add_mixed.enabled = physical_count >= 2 and len(colors) < MAX_FILAMENT_SLOTS
    add_mixed.operator("mmu.add_mixed_project_filament", text="Add Mix", icon="ADD")
    box.operator("mmu.recommend_project_filament", text="Recommend from Color", icon="EYEDROPPER")


class VIEW3D_PT_3mf_mixed_filaments(bpy.types.Panel):
    """Standalone 3MF filament authoring panel for non-paint projects."""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "3MF"
    bl_label = "Mixed Filaments"
    bl_context = "objectmode"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_project_filament_editor(self.layout, context)


_EDITOR_CLASSES = (
    MMU_OT_add_project_filament,
    MMU_OT_edit_project_filament,
    MMU_OT_add_mixed_project_filament,
    MMU_OT_edit_mixed_project_filament,
    MMU_OT_remove_project_filament,
    MMU_OT_remove_mixed_project_filament,
    MMU_OT_assign_project_filament,
    MMU_OT_recommend_project_filament,
    VIEW3D_PT_3mf_mixed_filaments,
)
