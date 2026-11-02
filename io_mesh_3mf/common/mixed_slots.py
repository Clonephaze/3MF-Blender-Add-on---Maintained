# Blender add-on to import and export 3MF files.
# Copyright (C) 2025 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Portions of this file are ported from OrcaSlicer (https://github.com/OrcaSlicer/OrcaSlicer),
# src/libslic3r/FilamentMixer.cpp and src/libslic3r/PrintConfig.cpp, which are licensed under the
# GNU Affero General Public License v3.0. Those portions are distributed under AGPL-3.0-or-later terms.

# <pep8 compliant>

"""
OrcaSlicer / BambuStudio per-slot mixed filaments.

A mixed filament is an ordinary filament slot whose ``filament_is_mixed``
flag is set.  Its recipe lives in six sibling per-slot arrays of
``Metadata/project_settings.config``::

    filament_is_mixed               ["0", "1", ...]
    filament_mixed_components       ["", "1,3", ...]      1-based physical slots
    filament_mixed_sublayer_ratios  ["", "0.5000,0.5000"] normalized, %.4f
    filament_mixed_gradient         ["0", "1", ...]
    filament_mixed_gradient_range   ["", "0.9000,0.1000"] first component's ratio
    filament_mixed_gradient_curve   ["", "x,y[,m_in,m_out]|..."]
    filament_mixed_gradient_per_part["0", "1", ...]

Every array has one entry per slot and is resized in lockstep
(``resize_mixed_filament_metadata`` in ``PrintConfig.cpp``).  This module is
pure Python so it can be unit tested without Blender.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "MIXED_KEYS",
    "MIXED_BOOL_KEYS",
    "MAX_FILAMENT_SLOTS",
    "GRADIENT_MIN_RATIO",
    "GRADIENT_MAX_RATIO",
    "FULLSPECTRUM_KEYS",
    "FILAMENT_OPTION_KEYS",
    "CurvePoint",
    "MixedSlot",
    "parse_bool",
    "format_bool",
    "parse_components",
    "format_components",
    "parse_ratios",
    "format_ratios",
    "parse_gradient_curve",
    "serialize_gradient_curve",
    "compute_pchip_default_tangents",
    "sample_gradient_curve",
    "sanitize_gradient_curve_array",
    "parse_mixed_slots",
    "write_mixed_slots",
    "mixed_slot_flags",
    "validate_mixed_filament_params",
    "remap_components_on_delete",
    "find_fixed_mixed_slot",
    "check_type_consistency",
    "is_fullspectrum_key",
    "strip_fullspectrum_keys",
    "resize_slot_arrays",
]

MIXED_BOOL_KEYS = (
    "filament_is_mixed",
    "filament_mixed_gradient",
    "filament_mixed_gradient_per_part",
)

MIXED_KEYS = (
    "filament_is_mixed",
    "filament_mixed_components",
    "filament_mixed_sublayer_ratios",
    "filament_mixed_gradient",
    "filament_mixed_gradient_range",
    "filament_mixed_gradient_curve",
    "filament_mixed_gradient_per_part",
)

# Upstream ``EnforcerBlockerType::ExtruderMax`` — paint states are slot index + 1.
MAX_FILAMENT_SLOTS = 32

GRADIENT_MIN_RATIO = 0.1
GRADIENT_MAX_RATIO = 0.9

# Keys introduced only by the unmerged OrcaSlicer-FullSpectrum fork.  They are
# meaningless to current OrcaSlicer and are removed on export.
FULLSPECTRUM_KEYS = frozenset({
    "mixed_filament_definitions",
    "mixed_filament_advanced_dithering",
    "mixed_filament_component_bias_enabled",
    "mixed_filament_gradient_mode",
    "mixed_filament_height_lower_bound",
    "mixed_filament_height_upper_bound",
    "mixed_filament_pointillism_line_gap",
    "mixed_filament_pointillism_pixel_size",
    "mixed_filament_region_collapse",
    "mixed_filament_surface_indentation",
    "mixed_color_layer_height_a",
    "mixed_color_layer_height_b",
    "local_z_wipe_tower_purge_lines",
})

# Per-filament keys without the ``filament_`` prefix, from upstream
# ``s_Preset_filament_options`` (Preset.cpp).  Preset compatibility fields
# (``compatible_*``, ``inherits``) are excluded: they are not per-slot arrays
# in ``project_settings.config``.
FILAMENT_OPTION_KEYS = frozenset({
    "default_filament_colour", "required_nozzle_HRC", "pellet_flow_coefficient",
    "volumetric_speed_coefficients", "nozzle_temperature", "nozzle_temperature_initial_layer",
    "cool_plate_temp", "textured_cool_plate_temp", "eng_plate_temp", "hot_plate_temp",
    "textured_plate_temp", "cool_plate_temp_initial_layer", "textured_cool_plate_temp_initial_layer",
    "eng_plate_temp_initial_layer", "hot_plate_temp_initial_layer", "textured_plate_temp_initial_layer",
    "supertack_plate_temp_initial_layer", "supertack_plate_temp", "temperature_vitrification",
    "reduce_fan_stop_start_freq", "dont_slow_down_outer_wall", "slow_down_for_layer_cooling",
    "fan_min_speed", "fan_max_speed", "enable_overhang_bridge_fan", "overhang_fan_speed",
    "overhang_fan_threshold", "close_fan_the_first_x_layers", "close_additional_fan_first_x_layers",
    "first_x_layer_fan_speed", "full_fan_speed_layer", "initial_layer_fan_speed",
    "additional_fan_full_speed_layer", "fan_cooling_layer_time", "slow_down_layer_time",
    "slow_down_min_speed", "activate_air_filtration", "activate_air_filtration_during_print",
    "activate_air_filtration_on_completion", "during_print_exhaust_fan_speed",
    "complete_print_exhaust_fan_speed", "additional_cooling_fan_speed",
    "nozzle_temperature_range_low", "nozzle_temperature_range_high", "enable_pressure_advance",
    "pressure_advance", "adaptive_pressure_advance", "adaptive_pressure_advance_model",
    "adaptive_pressure_advance_overhangs", "adaptive_pressure_advance_bridges",
    "chamber_temperature", "support_material_interface_fan_speed", "internal_bridge_fan_speed",
    "ironing_fan_speed", "activate_chamber_temp_control", "chamber_minimal_temperature",
    "idle_temperature", "long_retractions_when_ec", "retraction_distances_when_ec",
})

# Defaults for new slots (``PresetBundle::set_num_filaments``).
_NEW_SLOT_DEFAULTS = {
    "filament_map": "1",
    "filament_nozzle_map": "0",
    "filament_volume_map": "0",
}


def _f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------

def parse_bool(value) -> bool:
    """Parse an Orca config boolean (``"1"``/``"0"``, ``"true"``, ``1``, ``True``)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes")


def format_bool(value: bool) -> str:
    return "1" if value else "0"


def parse_components(text: str) -> List[int]:
    """Lenient parse of ``"1,3"``; mirrors ``parse_mixed_components``."""
    components: List[int] = []
    if not text:
        return components
    for token in str(text).split(","):
        try:
            val = int(token.strip())
        except ValueError:
            continue
        if val >= 0:
            components.append(val)
    return components


def format_components(components: Iterable[int]) -> str:
    return ",".join(str(int(c)) for c in components)


def parse_ratios(text: str, n_components: int) -> List[float]:
    """Parse and normalize ratios; mirrors ``parse_mixed_ratios``.

    Falls back to an even split when the count does not match.
    """
    ratios: List[float] = []
    if text:
        for token in str(text).split(","):
            try:
                val = float(token)
            except ValueError:
                continue
            if val > 0.0:
                ratios.append(val)
    if len(ratios) != n_components or n_components == 0:
        return [1.0 / n_components] * n_components if n_components > 0 else []
    total = sum(ratios)
    if total > 0.0 and abs(total - 1.0) > 1e-6:
        ratios = [r / total for r in ratios]
    return ratios


def format_ratios(weights: Sequence[int]) -> str:
    """Format integer weights as normalized ``%.4f`` ratios (float32 division, as upstream)."""
    total = sum(int(w) for w in weights)
    if total <= 0:
        total = 100
    return ",".join(
        "%.4f" % _f32(_f32(float(w)) / _f32(float(total))) for w in weights
    )


# ---------------------------------------------------------------------------
# Gradient curve
# ---------------------------------------------------------------------------

@dataclass
class CurvePoint:
    """Gradient curve anchor.  ``m_in`` / ``m_out`` are NaN for PCHIP defaults."""

    x: float
    y: float
    m_in: float = math.nan
    m_out: float = math.nan


def _parse_tangent(token: str) -> float:
    token = token.strip()
    if not token or token.lower() == "nan":
        return math.nan
    try:
        val = float(token)
    except ValueError:
        return math.nan
    return val if math.isfinite(val) else math.nan


def parse_gradient_curve(text: str) -> List[CurvePoint]:
    """Parse ``"x,y[,m_in,m_out]|..."``.  Returns ``[]`` when fewer than 2 valid points."""
    points: List[CurvePoint] = []
    if not text:
        return points
    for segment in str(text).split("|"):
        if not segment:
            continue
        fields = segment.split(",")
        if len(fields) not in (2, 4):
            continue
        try:
            x = float(fields[0])
            y = float(fields[1])
        except ValueError:
            continue
        if math.isnan(x) or math.isnan(y):
            continue
        x = max(0.0, min(1.0, x))
        y = max(GRADIENT_MIN_RATIO, min(GRADIENT_MAX_RATIO, y))
        point = CurvePoint(x, y)
        if len(fields) == 4:
            point.m_in = _parse_tangent(fields[2])
            point.m_out = _parse_tangent(fields[3])
        points.append(point)
    if len(points) < 2:
        return []
    points.sort(key=lambda p: p.x)
    return points


def serialize_gradient_curve(points: Sequence[CurvePoint]) -> str:
    parts = []
    for p in points:
        has_in = math.isfinite(p.m_in)
        has_out = math.isfinite(p.m_out)
        if has_in or has_out:
            m_in = "%.4f" % p.m_in if has_in else ""
            m_out = "%.4f" % p.m_out if has_out else ""
            parts.append("%.4f,%.4f,%s,%s" % (p.x, p.y, m_in, m_out))
        else:
            parts.append("%.4f,%.4f" % (p.x, p.y))
    return "|".join(parts)


def compute_pchip_default_tangents(points: Sequence[CurvePoint]) -> List[float]:
    """Fritsch–Carlson monotone tangents (``compute_pchip_default_tangents``)."""
    n = len(points)
    m = [0.0] * n
    if n < 2:
        return m
    d = []
    for i in range(n - 1):
        h = max(1e-12, points[i + 1].x - points[i].x)
        d.append((points[i + 1].y - points[i].y) / h)
    m[0] = d[0]
    m[n - 1] = d[n - 2]
    for i in range(1, n - 1):
        m[i] = 0.5 * (d[i - 1] + d[i])
    for i in range(n - 1):
        if d[i] == 0.0:
            m[i] = 0.0
            m[i + 1] = 0.0
            continue
        a = m[i] / d[i]
        b = m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9.0:
            tau = 3.0 / math.sqrt(s)
            m[i] = tau * a * d[i]
            m[i + 1] = tau * b * d[i]
    return m


def sample_gradient_curve(points: Sequence[CurvePoint], t: float) -> float:
    """Evaluate the curve at ``t`` (``sample_gradient_curve``); 0.5 when undefined."""
    if len(points) < 2:
        return 0.5
    if t <= points[0].x:
        return points[0].y
    if t >= points[-1].x:
        return points[-1].y
    m_def = compute_pchip_default_tangents(points)
    for i in range(1, len(points)):
        x0, x1 = points[i - 1].x, points[i].x
        if t > x1:
            continue
        y0, y1 = points[i - 1].y, points[i].y
        h = max(1e-12, x1 - x0)
        m_left = points[i - 1].m_out if math.isfinite(points[i - 1].m_out) else m_def[i - 1]
        m_right = points[i].m_in if math.isfinite(points[i].m_in) else m_def[i]
        u = (t - x0) / h
        u2 = u * u
        u3 = u2 * u
        y = ((2 * u3 - 3 * u2 + 1) * y0 + (u3 - 2 * u2 + u) * h * m_left
             + (-2 * u3 + 3 * u2) * y1 + (u3 - u2) * h * m_right)
        return max(GRADIENT_MIN_RATIO, min(GRADIENT_MAX_RATIO, y))
    return points[-1].y


def sanitize_gradient_curve_array(values: Sequence[str]) -> List[str]:
    """Clear curves with fewer than 2 valid points (``sanitize_mixed_gradient_curve_array``)."""
    return [v if not v or parse_gradient_curve(v) else "" for v in values]


# ---------------------------------------------------------------------------
# Slot model
# ---------------------------------------------------------------------------

@dataclass
class MixedSlot:
    """One mixed filament slot.

    ``raw`` holds the original string of every mixed key for this slot.  While
    ``dirty`` is False, :func:`write_mixed_slots` writes ``raw`` back unchanged
    so unedited slots round-trip byte-for-byte.
    """

    slot: int  # 0-based slot index
    components: List[int] = field(default_factory=list)  # 1-based physical slots
    ratios: List[float] = field(default_factory=list)
    gradient: bool = False
    gradient_range: Optional[Tuple[float, float]] = None
    gradient_curve: List[CurvePoint] = field(default_factory=list)
    per_part: bool = False
    raw: Dict[str, str] = field(default_factory=dict)
    dirty: bool = False

    def to_values(self) -> Dict[str, str]:
        """Return the upstream string value of each mixed key for this slot."""
        if not self.dirty and self.raw:
            return {key: self.raw.get(key, "0" if key in MIXED_BOOL_KEYS else "")
                    for key in MIXED_KEYS}
        weights = [int(round(r * 10000)) for r in self.ratios]
        rng = ""
        if self.gradient_range is not None:
            rng = "%.4f,%.4f" % self.gradient_range
        return {
            "filament_is_mixed": "1",
            "filament_mixed_components": format_components(self.components),
            "filament_mixed_sublayer_ratios": format_ratios(weights) if weights else "",
            "filament_mixed_gradient": format_bool(self.gradient),
            "filament_mixed_gradient_range": rng,
            "filament_mixed_gradient_curve": serialize_gradient_curve(self.gradient_curve),
            "filament_mixed_gradient_per_part": format_bool(self.per_part),
        }


def _slot_value(settings: dict, key: str, index: int) -> str:
    values = settings.get(key)
    if isinstance(values, list) and index < len(values):
        return str(values[index])
    return "0" if key in MIXED_BOOL_KEYS else ""


def mixed_slot_flags(settings: dict, slot_count: Optional[int] = None) -> List[bool]:
    """Per-slot ``filament_is_mixed`` flags padded/truncated to ``slot_count``."""
    flags = [parse_bool(v) for v in settings.get("filament_is_mixed", []) or []]
    if slot_count is None:
        slot_count = len(settings.get("filament_colour", []) or []) or len(flags)
    flags = flags[:slot_count]
    flags += [False] * (slot_count - len(flags))
    return flags


def parse_mixed_slots(settings: dict) -> List[MixedSlot]:
    """Parse all mixed slots from a ``project_settings.config`` dict."""
    slots: List[MixedSlot] = []
    for index, is_mixed in enumerate(mixed_slot_flags(settings)):
        if not is_mixed:
            continue
        raw = {key: _slot_value(settings, key, index) for key in MIXED_KEYS}
        components = parse_components(raw["filament_mixed_components"])
        rng = None
        rng_text = raw["filament_mixed_gradient_range"]
        if rng_text:
            try:
                a, b = (float(v) for v in rng_text.split(",")[:2])
                rng = (a, b)
            except ValueError:
                rng = None
        slots.append(MixedSlot(
            slot=index,
            components=components,
            ratios=parse_ratios(raw["filament_mixed_sublayer_ratios"], len(components)),
            gradient=parse_bool(raw["filament_mixed_gradient"]),
            gradient_range=rng,
            gradient_curve=parse_gradient_curve(raw["filament_mixed_gradient_curve"]),
            per_part=parse_bool(raw["filament_mixed_gradient_per_part"]),
            raw=raw,
        ))
    return slots


def write_mixed_slots(settings: dict, slots: Sequence[MixedSlot], slot_count: int) -> None:
    """Write mixed slots into ``settings`` in lockstep.

    Existing per-slot values are kept (``resize_mixed_filament_metadata``
    semantics); only slots listed in ``slots`` are overwritten.  Nothing is
    added to configs that never had mixed keys and have no mixed slots.
    """
    if not slots and not any(key in settings for key in MIXED_KEYS):
        return
    for key in MIXED_KEYS:
        default = "0" if key in MIXED_BOOL_KEYS else ""
        values = settings.get(key)
        values = [str(v) for v in values] if isinstance(values, list) else []
        values = values[:slot_count] + [default] * max(0, slot_count - len(values))
        settings[key] = values
    for slot in slots:
        if not 0 <= slot.slot < slot_count:
            continue
        for key, value in slot.to_values().items():
            settings[key][slot.slot] = value
    settings["filament_mixed_gradient_curve"] = sanitize_gradient_curve_array(
        settings["filament_mixed_gradient_curve"]
    )


# ---------------------------------------------------------------------------
# Validation and editing helpers
# ---------------------------------------------------------------------------

def _strict_components(text: str) -> Tuple[List[int], str]:
    if not text:
        return [], "empty component list"
    components = []
    for token in text.split(","):
        if not token:
            return [], "empty component index"
        try:
            val = int(token)
        except ValueError:
            return [], f'invalid component index "{token}"'
        if val < 1:
            return [], f"component index must be >= 1 (got {token})"
        components.append(val)
    if len(components) < 2:
        return [], f"at least 2 components required (got {len(components)})"
    if len(set(components)) != len(components):
        dup = next(c for c in components if components.count(c) > 1)
        return [], f"duplicate component index {dup}"
    return components, ""


def _strict_ratios(text: str, n_components: int) -> str:
    if not text:
        return ""
    ratios = []
    for token in text.split(","):
        if not token:
            return "empty ratio value"
        try:
            val = float(token)
        except ValueError:
            return f'invalid ratio "{token}"'
        if not val > 0.0:
            return f"ratio must be positive (got {token})"
        ratios.append(val)
    if len(ratios) != n_components:
        return (f"the ratio count must match the component count: "
                f"expected {n_components}, got {len(ratios)}")
    return ""


def _strict_range(text: str) -> str:
    if not text:
        return ""
    try:
        v0, v1 = (float(v) for v in text.split(",")[:2])
    except ValueError:
        return 'expected two comma-separated floats, e.g. "0.10,0.90"'
    if not (0.0 < v0 < 1.0 and 0.0 < v1 < 1.0):
        return "start and end ratios must be in (0, 1)"
    return ""


def validate_mixed_filament_params(settings: dict) -> Dict[str, str]:
    """Port of ``validate_mixed_filament_params``.  Returns ``{key: message}``."""
    errors: Dict[str, str] = {}

    def add(key: str, msg: str) -> None:
        errors[key] = f"{errors[key]}; {msg}" if key in errors else msg

    is_mixed = [parse_bool(v) for v in settings.get("filament_is_mixed", []) or []]
    comps = [str(v) for v in settings.get("filament_mixed_components", []) or []]
    ratios = [str(v) for v in settings.get("filament_mixed_sublayer_ratios", []) or []]
    gradient = [parse_bool(v) for v in settings.get("filament_mixed_gradient", []) or []]
    ranges = [str(v) for v in settings.get("filament_mixed_gradient_range", []) or []]
    curves = [str(v) for v in settings.get("filament_mixed_gradient_curve", []) or []]

    any_mixed = any(is_mixed)
    if (any(comps) or any(ratios) or any(gradient)) and not any_mixed:
        add("filament_is_mixed", "must be set when mixed filament parameters are specified")
        return errors
    if not any_mixed:
        return errors

    count = len(is_mixed)

    def check_size(key: str, size: int) -> None:
        if size != count:
            add(key, f"array size {size} does not match filament slot count {count}")

    check_size("filament_mixed_components", len(comps))
    check_size("filament_mixed_sublayer_ratios", len(ratios))
    gradient_specified = any(gradient)
    if gradient_specified:
        check_size("filament_mixed_gradient", len(gradient))
        check_size("filament_mixed_gradient_range", len(ranges))
    curve_specified = any(curves)
    if curve_specified:
        check_size("filament_mixed_gradient_curve", len(curves))

    num_physical = sum(1 for v in is_mixed if not v)
    for i, mixed in enumerate(is_mixed):
        if not mixed:
            continue
        slot = i + 1
        components, err = _strict_components(comps[i] if i < len(comps) else "")
        if err:
            add("filament_mixed_components", f"slot {slot}: {err}")
            continue
        for c in components:
            if c > num_physical:
                add("filament_mixed_components",
                    f"slot {slot}: component {c} out of range "
                    f"(max physical filament index is {num_physical})")
                break
            if c == slot:
                add("filament_mixed_components", f"slot {slot}: cannot reference itself as a component")
                break
            if c - 1 < count and is_mixed[c - 1]:
                add("filament_mixed_components",
                    f"slot {slot}: component {c} references a mixed filament slot")
                break
        err = _strict_ratios(ratios[i] if i < len(ratios) else "", len(components))
        if err:
            add("filament_mixed_sublayer_ratios", f"slot {slot}: {err}")
        if i < len(gradient) and gradient[i]:
            if len(components) != 2:
                add("filament_mixed_gradient", f"slot {slot}: gradient requires exactly 2 components")
            if gradient_specified:
                err = _strict_range(ranges[i] if i < len(ranges) else "")
                if err:
                    add("filament_mixed_gradient_range", f"slot {slot}: {err}")
            if curve_specified:
                curve = curves[i] if i < len(curves) else ""
                if curve and not parse_gradient_curve(curve):
                    add("filament_mixed_gradient_curve",
                        f"slot {slot}: invalid curve (need at least 2 valid control points)")
    return errors


def remap_components_on_delete(
    is_mixed: Sequence[bool], comp_strs: List[str], deleted_1based: int,
) -> None:
    """Port of ``remap_mixed_components_on_delete`` (in place; deleted → 0)."""
    for i, mixed in enumerate(is_mixed):
        if not mixed or i >= len(comp_strs) or not comp_strs[i]:
            continue
        out = []
        for c in parse_components(comp_strs[i]):
            if c == deleted_1based:
                out.append(0)
            elif c > deleted_1based:
                out.append(c - 1)
            else:
                out.append(c)
        comp_strs[i] = format_components(out)


def find_fixed_mixed_slot(settings: dict, components: Sequence[int], weights: Sequence[int]) -> int:
    """Port of ``find_fixed_mixed_filament``: 0-based slot of an identical non-gradient mix, or -1."""
    is_mixed = settings.get("filament_is_mixed")
    comps = settings.get("filament_mixed_components")
    ratios = settings.get("filament_mixed_sublayer_ratios")
    if is_mixed is None or comps is None or ratios is None:
        return -1
    gradient = settings.get("filament_mixed_gradient")
    comp_str = format_components(components)
    ratio_str = format_ratios(weights)
    for i, flag in enumerate(is_mixed):
        if not parse_bool(flag) or i >= len(comps) or i >= len(ratios):
            continue
        if gradient is not None and i < len(gradient) and parse_bool(gradient[i]):
            continue
        if comps[i] == comp_str and ratios[i] == ratio_str:
            return i
    return -1


def check_type_consistency(
    is_mixed: Sequence[bool], comp_strs: Sequence[str], filament_types: Sequence[str],
) -> List[int]:
    """Port of ``check_mixed_filament_type_consistency``: 0-based slots mixing different types."""
    result = []
    for i, mixed in enumerate(is_mixed):
        if not mixed or i >= len(comp_strs) or not comp_strs[i]:
            continue
        comps = parse_components(comp_strs[i])
        if len(comps) < 2:
            continue
        ref = None
        for c in comps:
            if c == 0 or c - 1 >= len(filament_types):
                continue
            if ref is None:
                ref = filament_types[c - 1]
            elif filament_types[c - 1] != ref:
                result.append(i)
                break
    return result


# ---------------------------------------------------------------------------
# project_settings.config housekeeping
# ---------------------------------------------------------------------------

def is_fullspectrum_key(key: str) -> bool:
    return key in FULLSPECTRUM_KEYS or key.startswith("dithering_")


def strip_fullspectrum_keys(settings: dict) -> List[str]:
    """Remove FullSpectrum-only keys in place; returns the removed key names."""
    removed = [key for key in settings if is_fullspectrum_key(key)]
    for key in removed:
        del settings[key]
    return removed


def resize_slot_arrays(
    settings: dict,
    old_count: int,
    sources: Sequence[int],
    new_slots: Iterable[int] = (),
    extra_keys: Iterable[str] = (),
) -> None:
    """Rebuild every per-slot array for a new slot layout.

    :param old_count: Slot count of ``settings`` before the change.
    :param sources: For each output slot, the 0-based input slot to copy
        values from.  ``list(range(old_count))`` is a no-op.
    :param new_slots: Output slot indices that are newly created.  Mixed keys
        get upstream defaults there instead of copies.
    :param extra_keys: Non ``filament_*`` keys that are also per-slot.

    Arrays of length ``old_count`` are per-slot.  Arrays whose length matches
    ``filament_self_index`` (per extruder-variant, e.g. 2 entries per slot)
    are rebuilt group-wise.  ``flush_volumes_vector`` (2 per slot) and
    ``flush_volumes_matrix`` (nozzles x N x N) follow
    ``PresetBundle::update_multi_material_filament_presets``.  Other lengths
    are left untouched.
    """
    sources = list(sources)
    new_slots = set(new_slots)
    if sources == list(range(old_count)) and not new_slots:
        return

    self_index = settings.get("filament_self_index")
    variant_len = None
    groups: Dict[int, List[int]] = {}
    if isinstance(self_index, list) and self_index and len(self_index) != old_count:
        variant_len = len(self_index)
        for pos, value in enumerate(self_index):
            try:
                groups.setdefault(int(value) - 1, []).append(pos)
            except (TypeError, ValueError):
                variant_len = None
                break

    extra = set(extra_keys)
    for key, values in list(settings.items()):
        if not isinstance(values, list) or not values:
            continue
        if not (key.startswith("filament_") or key in FILAMENT_OPTION_KEYS or key in extra):
            continue
        if len(values) == old_count:
            if key == "filament_self_index":
                settings[key] = [str(i + 1) for i in range(len(sources))]
                continue
            if key in MIXED_KEYS:
                default = "0" if key in MIXED_BOOL_KEYS else ""
            else:
                default = _NEW_SLOT_DEFAULTS.get(key)
            settings[key] = [
                default if (default is not None and i in new_slots) else values[min(src, old_count - 1)]
                for i, src in enumerate(sources)
            ]
        elif variant_len is not None and len(values) == variant_len:
            out = []
            for i, src in enumerate(sources):
                for pos in groups.get(src, groups.get(old_count - 1, [])):
                    out.append(str(i + 1) if key == "filament_self_index" else values[pos])
            settings[key] = out

    _resize_flush_volumes(settings, old_count, sources, new_slots)


def _resize_flush_volumes(settings: dict, old_count: int, sources: List[int], new_slots: set) -> None:
    new_count = len(sources)
    vector = settings.get("flush_volumes_vector")
    if isinstance(vector, list) and len(vector) == 2 * old_count:
        out = []
        for i, src in enumerate(sources):
            # Upstream pads new slots with the first slot's load/unload pair.
            base = 0 if i in new_slots else 2 * min(src, old_count - 1)
            out += [vector[base], vector[base + 1]]
        settings["flush_volumes_vector"] = vector = out
    else:
        vector = None

    matrix = settings.get("flush_volumes_matrix")
    if not isinstance(matrix, list) or not matrix or old_count <= 0:
        return
    nozzles = len(settings.get("flush_multiplier") or [1]) or 1
    old_size = old_count * old_count
    if len(matrix) != old_size * nozzles:
        return

    def fresh(i: int, j: int) -> str:
        if i == j or vector is None:
            return "0"
        total = float(vector[2 * i]) + float(vector[2 * j + 1])
        return str(int(total)) if total.is_integer() else str(total)

    out = []
    for nozzle in range(nozzles):
        for i in range(new_count):
            for j in range(new_count):
                if i in new_slots or j in new_slots:
                    out.append(fresh(i, j))
                elif i == j:
                    out.append("0")
                else:
                    si = min(sources[i], old_count - 1)
                    sj = min(sources[j], old_count - 1)
                    out.append(matrix[nozzle * old_size + si * old_count + sj])
    settings["flush_volumes_matrix"] = out
