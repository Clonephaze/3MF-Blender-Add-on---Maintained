# SPDX-License-Identifier: AGPL-3.0-or-later
# Portions ported from OrcaSlicer src/libslic3r/ColorDecomposeRecipe.cpp and
# FilamentMixer.cpp; recipe data is from OrcaSlicer resources/filament_mixing.

"""Measured OrcaSlicer filament blend colors and mixed-slot display colors."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import List, Sequence, Tuple

from .mixed_slots import MixedSlot

__all__ = [
    "PhysicalFilament",
    "RecipeComponent",
    "RecipeMode",
    "RecipeResult",
    "delta_e76",
    "lab_to_srgb_hex",
    "lookup_measured_blend_color",
    "lookup_standard_recipe",
    "recommend_from_physical_filaments",
    "recompute_mixed_slot_colors",
    "rgb_to_lab",
    "slot_display_color",
]

_RECIPE_PATH = Path(__file__).with_name("standard_color_recipes.json")
_SOURCES_USED_BY_ORCA = frozenset({"measured", "interpolated"})


class RecipeMode(str, Enum):
    """OrcaSlicer color-recipe modes."""

    MATERIAL_LIST = "MaterialList"
    CMYW = "CMYW"
    RYBW = "RYBW"


@dataclass(frozen=True)
class PhysicalFilament:
    """A physical filament candidate; ``filament_index`` is 1-based."""

    color_hex: str
    filament_index: int
    filament_type: str = ""
    is_mixed: bool = False
    name: str = ""


@dataclass(frozen=True)
class RecipeComponent:
    """One component of a recommended mixed-filament recipe."""

    color_hex: str
    ratio: int
    filament_index: int = 0
    base_color: str = ""


@dataclass
class RecipeResult:
    """Solver result matching Orca's validity, mode, color, and components."""

    valid: bool = False
    mode: RecipeMode = RecipeMode.MATERIAL_LIST
    matched_color_hex: str = ""
    components: List[RecipeComponent] = field(default_factory=list)


@dataclass(frozen=True)
class _Recipe:
    mode: RecipeMode
    material: str
    component_keys: Tuple[str, ...]
    component_hexes: Tuple[str, ...]
    ratios: Tuple[int, ...]
    source: str
    measured_hex: str
    measured_lab: Tuple[float, float, float]


def _parse_hex(hex_color: str) -> Tuple[int, int, int] | None:
    value = str(hex_color).strip()
    if len(value) < 7 or not value.startswith("#"):
        return None
    try:
        red = int(value[1:3], 16)
        green = int(value[3:5], 16)
        blue = int(value[5:7], 16)
    except ValueError:
        return None
    return (red, green, blue)


def _normal_hex(hex_color: str) -> str:
    rgb = _parse_hex(hex_color)
    if rgb is None:
        return ""
    return "#%02X%02X%02X" % rgb


def _srgb_to_linear(channel: int) -> float:
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def _xyz_to_lab_component(value: float) -> float:
    epsilon = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    return value ** (1.0 / 3.0) if value > epsilon else (kappa * value + 16.0) / 116.0


def rgb_to_lab(rgb: Tuple[int, int, int]) -> Tuple[float, float, float]:
    """Convert 8-bit sRGB to CIE Lab using OrcaSlicer's D65 matrices."""
    red, green, blue = (_srgb_to_linear(c) for c in rgb)
    x = (0.4124564 * red + 0.3575761 * green + 0.1804375 * blue) / 0.95047
    y = 0.2126729 * red + 0.7151522 * green + 0.0721750 * blue
    z = (0.0193339 * red + 0.1191920 * green + 0.9503041 * blue) / 1.08883
    fx, fy, fz = (
        _xyz_to_lab_component(value) for value in (x, y, z)
    )
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def _lab_to_srgb_channel(value: float) -> int:
    value = max(0.0, min(1.0, value))
    if value <= 0.0031308:
        gamma = 12.92 * value
    else:
        gamma = 1.055 * value ** (1.0 / 2.4) - 0.055
    return max(0, min(255, int(math.floor(gamma * 255.0 + 0.5))))


def lab_to_srgb_hex(lab: Tuple[float, float, float]) -> str:
    """Convert CIE Lab to an 8-bit sRGB hex string using OrcaSlicer's matrices."""
    lightness, a, b = lab

    def inverse(value: float) -> float:
        epsilon = 216.0 / 24389.0
        kappa = 24389.0 / 27.0
        cube = value * value * value
        return cube if cube > epsilon else (value * 116.0 - 16.0) / kappa

    fy = (lightness + 16.0) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b / 200.0
    x = 0.95047 * inverse(fx)
    y = inverse(fy)
    z = 1.08883 * inverse(fz)

    linear_rgb = (
        3.2406 * x - 1.5372 * y - 0.4986 * z,
        -0.9689 * x + 1.8758 * y + 0.0415 * z,
        0.0557 * x - 0.2040 * y + 1.0570 * z,
    )
    return "#%02X%02X%02X" % tuple(
        _lab_to_srgb_channel(channel) for channel in linear_rgb
    )


def delta_e76(
    first: Tuple[float, float, float],
    second: Tuple[float, float, float],
) -> float:
    """Euclidean CIE76 distance between two Lab colors."""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def _parse_recipe_mode(value: str) -> RecipeMode:
    if value in ("RYBW", "RGBY"):
        return RecipeMode.RYBW
    return RecipeMode.CMYW


def _coerce_standard_mode(mode: RecipeMode | str) -> RecipeMode:
    if isinstance(mode, RecipeMode):
        selected = mode
    elif mode == "RGBY":
        selected = RecipeMode.RYBW
    else:
        try:
            selected = RecipeMode(mode)
        except ValueError as e:
            raise ValueError(f"Unknown color recipe mode: {mode!r}") from e
    if selected is RecipeMode.MATERIAL_LIST:
        raise ValueError("Standard recipe lookup requires CMYW or RYBW mode")
    return selected


def _validate_rgb(rgb: Sequence[int]) -> Tuple[int, int, int]:
    if (
        len(rgb) != 3
        or any(not isinstance(channel, int) or not 0 <= channel <= 255 for channel in rgb)
    ):
        raise ValueError("RGB target must contain exactly three integer channels in 0..255")
    return (rgb[0], rgb[1], rgb[2])


def _rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def _material_matches(first: str, second: str) -> bool:
    if not first or not second:
        return False
    return first == second or first == second + " Basic" or second == first + " Basic"


def _ratio_grid(component_count: int) -> List[Tuple[int, ...]]:
    if component_count == 2:
        return [(first, 100 - first) for first in range(20, 81, 5)]
    if component_count == 3:
        return [
            (first, second, 100 - first - second)
            for first in range(20, 61, 5)
            for second in range(20, 81 - first, 5)
            if 100 - first - second >= 20
        ]
    return []


@lru_cache(maxsize=1)
def _standard_recipes() -> Tuple[_Recipe, ...]:
    try:
        with _RECIPE_PATH.open("r", encoding="utf-8") as recipe_file:
            data = json.load(recipe_file)
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Unable to load OrcaSlicer color recipes: {e}") from e

    raw_entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(raw_entries, list):
        raise ValueError("OrcaSlicer color recipe data has no entries list")

    recipes = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        components = item.get("components")
        ratios = item.get("ratios")
        measured_hex = _normal_hex(item.get("measured_rgb", ""))
        if (
            not isinstance(components, list)
            or not isinstance(ratios, list)
            or len(components) < 2
            or len(components) != len(ratios)
            or not measured_hex
        ):
            continue
        component_keys = tuple(
            str(component.get("key", ""))
            for component in components
            if isinstance(component, dict)
        )
        component_hexes = tuple(
            _normal_hex(component.get("rgb", ""))
            for component in components
            if isinstance(component, dict)
        )
        if len(component_hexes) != len(components) or not all(component_hexes):
            continue
        try:
            ratio_values = tuple(int(ratio) for ratio in ratios)
            raw_lab = item.get("measured_lab")
            measured_lab = (
                tuple(float(value) for value in raw_lab[:3])
                if isinstance(raw_lab, list) and len(raw_lab) >= 3
                else rgb_to_lab(_parse_hex(measured_hex))
            )
        except (TypeError, ValueError):
            continue
        recipes.append(_Recipe(
            mode=_parse_recipe_mode(str(item.get("mode", "CMYW"))),
            material=str(item.get("material", "")),
            component_keys=component_keys,
            component_hexes=component_hexes,
            ratios=ratio_values,
            source=str(item.get("source", "")),
            measured_hex=measured_hex,
            measured_lab=measured_lab,
        ))
    if not recipes:
        raise ValueError("OrcaSlicer color recipe data contains no usable entries")
    return tuple(recipes)


def _round_away_from_zero(value: float) -> int:
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def lookup_measured_blend_color(
    component_hexes: Sequence[str],
    ratios: Sequence[int],
) -> str:
    """Return Orca's measured/interpolated recipe color, or ``""`` if unmatched."""
    if len(component_hexes) < 2 or len(component_hexes) != len(ratios):
        return ""

    pairs = []
    for hex_color, ratio in zip(component_hexes, ratios):
        normalized = _normal_hex(hex_color)
        if not normalized:
            return ""
        try:
            pairs.append((normalized, int(ratio)))
        except (TypeError, ValueError):
            return ""
    pairs.sort()
    input_hexes = tuple(pair[0] for pair in pairs)
    input_ratios = [pair[1] for pair in pairs]

    ratio_sum = sum(input_ratios)
    if ratio_sum > 0 and ratio_sum != 100:
        normalized_ratios = [
            _round_away_from_zero(ratio * 100.0 / ratio_sum)
            for ratio in input_ratios
        ]
        difference = 100 - sum(normalized_ratios)
        if difference:
            largest = max(range(len(normalized_ratios)), key=normalized_ratios.__getitem__)
            normalized_ratios[largest] += difference
        input_ratios = normalized_ratios

    if len(input_ratios) == 2:
        if any(ratio < 20 or ratio > 80 for ratio in input_ratios):
            return ""
    elif any(ratio < 20 for ratio in input_ratios):
        return ""

    anchors = []
    for recipe in _standard_recipes():
        if recipe.source not in _SOURCES_USED_BY_ORCA:
            continue
        if len(recipe.component_hexes) != len(input_hexes):
            continue
        recipe_pairs = sorted(zip(recipe.component_hexes, recipe.ratios))
        if tuple(pair[0] for pair in recipe_pairs) != input_hexes:
            continue
        anchor_ratios = tuple(pair[1] for pair in recipe_pairs)
        if anchor_ratios == tuple(input_ratios):
            return recipe.measured_hex
        anchors.append((anchor_ratios, recipe.measured_lab, recipe.measured_hex))

    if len(anchors) < 2:
        return ""

    if len(input_hexes) == 2:
        anchors.sort(key=lambda anchor: anchor[0][0])
        target = float(input_ratios[0])
        lower = 0
        while lower + 2 < len(anchors) and anchors[lower + 1][0][0] <= target:
            lower += 1
        first, second = anchors[lower], anchors[lower + 1]
        span = float(second[0][0] - first[0][0])
        interpolation = (target - first[0][0]) / span if span > 0.0 else 0.0
        return lab_to_srgb_hex(tuple(
            first[1][index] + interpolation * (second[1][index] - first[1][index])
            for index in range(3)
        ))

    ratio_a, ratio_b = float(input_ratios[0]), float(input_ratios[1])
    distances = []
    for anchor in anchors:
        distance = math.sqrt(
            (ratio_a - anchor[0][0]) ** 2 + (ratio_b - anchor[0][1]) ** 2
        )
        if distance == 0.0:
            return anchor[2]
        distances.append((distance, anchor))
    distances.sort(key=lambda item: item[0])
    nearest = distances[:3]
    weights = [1.0 / (distance * distance) for distance, _ in nearest]
    denominator = sum(weights)
    return lab_to_srgb_hex(tuple(
        sum(weight * anchor[1][index] for weight, (_, anchor) in zip(weights, nearest))
        / denominator
        for index in range(3)
    ))


def recommend_from_physical_filaments(
    target_rgb: Sequence[int],
    physical_filaments: Sequence[PhysicalFilament],
    preferred_material_type: str = "",
) -> RecipeResult:
    """Recommend a physical-filament blend using Orca's exhaustive ratio grid.

    Matching-material physical filaments are preferred. If fewer than two are
    available, all valid non-mixed physical filaments become candidates.
    """
    target = _validate_rgb(target_rgb)
    target_hex = _rgb_to_hex(target)
    target_lab = rgb_to_lab(target)

    valid_filaments = []
    matching_filaments = []
    for filament in physical_filaments:
        rgb = _parse_hex(filament.color_hex)
        if filament.is_mixed or rgb is None:
            continue
        entry = (filament, rgb)
        valid_filaments.append(entry)
        if (
            not preferred_material_type
            or _material_matches(filament.filament_type, preferred_material_type)
        ):
            matching_filaments.append(entry)

    for filament, rgb in matching_filaments:
        if _rgb_to_hex(rgb) == target_hex:
            return RecipeResult(
                valid=True,
                mode=RecipeMode.MATERIAL_LIST,
                matched_color_hex=filament.color_hex,
                components=[
                    RecipeComponent(
                        color_hex=filament.color_hex,
                        ratio=100,
                        filament_index=filament.filament_index,
                    )
                ],
            )

    candidates = matching_filaments
    if len(candidates) < 2:
        candidates = valid_filaments
    if len(candidates) > 8:
        candidates = sorted(
            candidates,
            key=lambda item: delta_e76(target_lab, rgb_to_lab(item[1])),
        )[:8]

    from .filament_mixer import blend_color_multi

    best = RecipeResult()
    best_score = math.inf

    def consider(
        selected: Sequence[Tuple[PhysicalFilament, Tuple[int, int, int]]],
        ratios: Tuple[int, ...],
    ) -> None:
        nonlocal best, best_score

        mixed_hex = blend_color_multi(
            [filament.color_hex for filament, _ in selected],
            ratios,
        )
        mixed_rgb = _parse_hex(mixed_hex)
        if mixed_rgb is None:
            return
        score = delta_e76(target_lab, rgb_to_lab(mixed_rgb))
        if score >= best_score:
            return

        best_score = score
        best = RecipeResult(
            valid=True,
            mode=RecipeMode.MATERIAL_LIST,
            matched_color_hex=mixed_hex,
            components=[
                RecipeComponent(
                    color_hex=filament.color_hex,
                    ratio=ratio,
                    filament_index=filament.filament_index,
                )
                for (filament, _), ratio in zip(selected, ratios)
            ],
        )

    for first in range(len(candidates)):
        for second in range(first + 1, len(candidates)):
            selected_pair = (candidates[first], candidates[second])
            for ratios in _ratio_grid(2):
                consider(selected_pair, ratios)

            for third in range(second + 1, len(candidates)):
                selected_triplet = (
                    candidates[first],
                    candidates[second],
                    candidates[third],
                )
                for ratios in _ratio_grid(3):
                    consider(selected_triplet, ratios)

    return best


def lookup_standard_recipe(
    target_rgb: Sequence[int],
    mode: RecipeMode | str,
    preferred_material_type: str = "",
) -> RecipeResult:
    """Return the closest matching CMYW or RYBW recipe by measured Lab color."""
    target = _validate_rgb(target_rgb)
    selected_mode = _coerce_standard_mode(mode)
    target_lab = rgb_to_lab(target)
    best = RecipeResult()
    best_score = math.inf

    def consider(require_material_match: bool) -> None:
        nonlocal best, best_score

        for recipe in _standard_recipes():
            if recipe.mode is not selected_mode:
                continue
            if require_material_match and not _material_matches(
                recipe.material, preferred_material_type
            ):
                continue
            if (
                not require_material_match
                and preferred_material_type
                and _material_matches(recipe.material, preferred_material_type)
            ):
                continue

            score = delta_e76(target_lab, recipe.measured_lab)
            if score >= best_score:
                continue
            best_score = score
            best = RecipeResult(
                valid=True,
                mode=selected_mode,
                matched_color_hex=recipe.measured_hex,
                components=[
                    RecipeComponent(
                        color_hex=color_hex,
                        base_color=(
                            recipe.component_keys[index]
                            if index < len(recipe.component_keys)
                            else ""
                        ),
                        ratio=recipe.ratios[index],
                    )
                    for index, color_hex in enumerate(recipe.component_hexes)
                ],
            )

    consider(require_material_match=True)
    if not best.valid:
        consider(require_material_match=False)
    return best


def slot_display_color(slot: MixedSlot, palette: Sequence[str]) -> str:
    """Return the Orca swatch color for one mixed slot and its 1-based components."""
    if not slot.components:
        return "#808080"
    if slot.gradient:
        component_ids = (slot.components[0], slot.components[-1])
        weights = (5000, 5000)
    else:
        component_ids = tuple(slot.components)
        weights = tuple(_round_away_from_zero(ratio * 10000.0) for ratio in slot.ratios)
    if len(component_ids) != len(weights):
        return "#808080"
    try:
        colors = [palette[component_id - 1] for component_id in component_ids]
    except (IndexError, TypeError):
        return "#808080"
    if any(component_id < 1 for component_id in component_ids):
        return "#808080"

    from .filament_mixer import blend_color_multi

    return blend_color_multi(colors, weights)


def recompute_mixed_slot_colors(
    colors: Sequence[str],
    mixed_slots: Sequence[MixedSlot],
) -> list[str]:
    """Recalculate all mixed-slot colors from the current physical palette."""
    result = [str(color) for color in colors]
    for slot in mixed_slots:
        if 0 <= slot.slot < len(result):
            result[slot.slot] = slot_display_color(slot, result)
    return result
