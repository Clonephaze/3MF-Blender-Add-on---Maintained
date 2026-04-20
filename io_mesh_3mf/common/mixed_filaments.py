# Blender add-on to import and export 3MF files.
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
OrcaSlicer-FullSpectrum mixed filament definitions.

Handles parsing, serialization, virtual-ID mapping, and display color
computation for the ``mixed_filament_definitions`` field in
``Metadata/project_settings.config``.

Source reference: OrcaSlicer-FullSpectrum ``src/libslic3r/MixedFilament.cpp``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .logging import debug, warn

__all__ = [
    "MixedFilament",
    "DIST_LAYER_CYCLE",
    "DIST_POINTILLISM",
    "DIST_SIMPLE",
    "parse_mixed_filament_definitions",
    "serialize_mixed_filament_definitions",
    "compute_display_color",
    "resolve_virtual_filament_index",
    "virtual_filament_id_to_index",
    "normalize_manual_pattern",
    "total_filaments",
]

# Distribution mode constants (mirrors MixedFilament::DistributionMode)
DIST_LAYER_CYCLE = 0
DIST_POINTILLISM = 1
DIST_SIMPLE = 2


@dataclass
class MixedFilament:
    """One entry in the mixed filament table.

    Mirrors the C++ ``MixedFilament`` struct from ``MixedFilament.hpp``.
    All component IDs are 1-based physical filament indices.
    """

    component_a: int = 0
    component_b: int = 0
    stable_id: int = 0          # persistent round-trip identity (NOT the virtual slot number)
    mix_b_percent: int = 50     # 0-100: percentage of component_b
    ratio_a: int = 1            # layer cycle ratio for component_a
    ratio_b: int = 1            # layer cycle ratio for component_b
    enabled: bool = True
    deleted: bool = False
    custom: bool = True         # False = auto-generated C(N,2) pair
    origin_auto: bool = False
    pointillism_all_filaments: bool = False
    gradient_component_ids: str = ""   # digit string e.g. "123" for 3-way
    gradient_component_weights: str = ""  # slash-separated ints e.g. "50/25/25"
    manual_pattern: str = ""    # normalized digit string, commas = perimeter groups
    distribution_mode: int = DIST_SIMPLE
    local_z_max_sublayers: int = 0
    component_a_surface_offset: float = 0.0
    component_b_surface_offset: float = 0.0
    display_color: str = ""     # computed "#RRGGBB", populated by compute_display_color()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_row(row: str) -> Optional[MixedFilament]:
    """Parse one semicolon-separated row definition.

    Port of ``parse_row_definition()`` from MixedFilament.cpp (L361-580).
    Returns None for invalid or empty rows.
    """
    row = row.strip()
    if not row:
        return None

    tokens = [t.strip() for t in row.split(",")]
    if len(tokens) < 4:
        warn(f"mixed_filament_definitions: row has fewer than 4 tokens, skipping: {row!r}")
        return None

    # --- Positional fields (0-4) ---
    try:
        a = int(tokens[0])
        b = int(tokens[1])
        enabled = bool(int(tokens[2]))
        if len(tokens) == 4:
            # Legacy 4-token format: a,b,enabled,mix — custom defaults True
            custom = True
            mix = max(0, min(100, int(tokens[3])))
            meta_start = 4
        else:
            custom = bool(int(tokens[3]))
            mix = max(0, min(100, int(tokens[4])))
            meta_start = 5
    except (ValueError, IndexError) as exc:
        warn(f"mixed_filament_definitions: could not parse positional fields: {exc} — row: {row!r}")
        return None

    if a == 0 or b == 0 or a == b:
        warn(f"mixed_filament_definitions: invalid component pair ({a},{b}), skipping")
        return None

    mf = MixedFilament(
        component_a=a,
        component_b=b,
        mix_b_percent=mix,
        enabled=enabled,
        custom=custom,
    )

    # --- Positional field at index 5: pointillism_all_filaments ---
    # In the 5-field format the slicer always writes a bare "0" or "1" at
    # position 5 (the slot right after mix_b_percent).  Consume it before
    # entering the prefixed-token loop so it is not misinterpreted as a
    # manual pattern digit.
    if meta_start == 5 and len(tokens) > 5 and tokens[5].strip() in ("0", "1"):
        mf.pointillism_all_filaments = bool(int(tokens[5].strip()))
        meta_start = 6

    # --- Prefixed metadata tokens (index meta_start+) ---
    pattern_tokens: List[str] = []
    for token in tokens[meta_start:]:
        token = token.strip()
        if not token:
            continue
        tl = token.lower()
        if tl.startswith("g"):
            mf.gradient_component_ids = token[1:]
        elif tl.startswith("w"):
            mf.gradient_component_weights = token[1:]
        elif tl.startswith("m"):
            try:
                mf.distribution_mode = max(DIST_LAYER_CYCLE, min(DIST_SIMPLE, int(token[1:])))
            except ValueError:
                pass
        elif tl.startswith("z"):
            try:
                mf.local_z_max_sublayers = max(0, int(token[1:]))
            except ValueError:
                pass
        elif tl.lower().startswith("xa"):
            try:
                mf.component_a_surface_offset = max(-5.0, min(5.0, float(token[2:])))
            except ValueError:
                pass
        elif tl.lower().startswith("xb"):
            try:
                mf.component_b_surface_offset = max(-5.0, min(5.0, float(token[2:])))
            except ValueError:
                pass
        elif tl.startswith("d"):
            try:
                mf.deleted = bool(int(token[1:]))
                if mf.deleted:
                    mf.enabled = False
            except ValueError:
                pass
        elif tl.startswith("o"):
            try:
                mf.origin_auto = bool(int(token[1:]))
            except ValueError:
                pass
        elif tl.startswith("u"):
            try:
                mf.stable_id = int(token[1:])
            except ValueError:
                pass
        else:
            # Unrecognized token — accumulates into manual_pattern
            pattern_tokens.append(token)

    if pattern_tokens:
        mf.manual_pattern = normalize_manual_pattern(",".join(pattern_tokens))

    return mf


def parse_mixed_filament_definitions(defs_string: str) -> List[MixedFilament]:
    """Parse the ``mixed_filament_definitions`` string into a list of entries.

    :param defs_string: The raw value of the ``mixed_filament_definitions``
        config key — a semicolon-separated list of row definitions.
    :return: List of :class:`MixedFilament` entries (invalid rows are skipped).
    """
    if not defs_string or not defs_string.strip():
        return []

    result: List[MixedFilament] = []
    for row in defs_string.split(";"):
        mf = _parse_row(row)
        if mf is not None:
            result.append(mf)

    debug(f"Parsed {len(result)} mixed filament definitions")
    return result


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def serialize_mixed_filament_definitions(entries: List[MixedFilament]) -> str:
    """Serialize a list of :class:`MixedFilament` entries back to the wire format.

    Port of ``serialize_custom_entries()`` from MixedFilament.cpp (L1673-1703).
    Output matches the field order written by the slicer.

    :param entries: List of mixed filament entries.
    :return: Semicolon-separated definitions string.
    """
    rows: List[str] = []
    for mf in entries:
        parts = [
            str(mf.component_a),
            str(mf.component_b),
            "1" if mf.enabled else "0",
            "1" if mf.custom else "0",
            str(mf.mix_b_percent),
            "1" if mf.pointillism_all_filaments else "0",
            f"g{mf.gradient_component_ids}",
            f"w{mf.gradient_component_weights}",
            f"m{mf.distribution_mode}",
            f"z{mf.local_z_max_sublayers}",
            f"xa{mf.component_a_surface_offset:.1f}",
            f"xb{mf.component_b_surface_offset:.1f}",
            "d1" if mf.deleted else "d0",
            "o1" if mf.origin_auto else "o0",
            f"u{mf.stable_id}",
        ]
        if mf.manual_pattern:
            parts.append(mf.manual_pattern)
        rows.append(",".join(parts))
    return ";".join(rows)


# ---------------------------------------------------------------------------
# Pattern normalization
# ---------------------------------------------------------------------------

def normalize_manual_pattern(pattern: str) -> str:
    """Validate and canonicalize a manual layer pattern string.

    Port of ``normalize_manual_pattern()`` from MixedFilament.cpp (L1619-1647).

    Valid characters:
    - ``'1'``–``'9'``: physical filament step (``'a'``/``'A'`` → ``'1'``, ``'b'``/``'B'`` → ``'2'``)
    - ``','``: separates perimeter groups

    Returns an empty string if the pattern is invalid.

    :param pattern: Raw pattern string.
    :return: Normalized pattern or ``""`` for invalid input.
    """
    if not pattern:
        return ""

    normalized: List[str] = []
    group_has_steps = False

    for ch in pattern:
        if ch in "123456789":
            normalized.append(ch)
            group_has_steps = True
        elif ch.lower() == "a":
            normalized.append("1")
            group_has_steps = True
        elif ch.lower() == "b":
            normalized.append("2")
            group_has_steps = True
        elif ch == ",":
            if not group_has_steps:
                return ""  # empty group
            normalized.append(",")
            group_has_steps = False
        elif ch in " \t\r\n":
            pass  # strip whitespace
        else:
            return ""  # unknown character → invalid

    result = "".join(normalized)
    if result.endswith(","):
        return ""  # trailing comma → invalid
    return result


# ---------------------------------------------------------------------------
# Virtual filament ID mapping
# ---------------------------------------------------------------------------

def _enabled_entries(entries: List[MixedFilament]) -> List[MixedFilament]:
    """Return only enabled, non-deleted entries in order."""
    return [mf for mf in entries if mf.enabled and not mf.deleted]


def total_filaments(num_physical: int, entries: List[MixedFilament]) -> int:
    """Total filament count including virtual mixed filaments.

    :param num_physical: Number of physical filaments.
    :param entries: All (including disabled) mixed filament entries.
    :return: num_physical + count of enabled/non-deleted entries.
    """
    return num_physical + len(_enabled_entries(entries))


def virtual_filament_id_to_index(
    filament_id: int,
    num_physical: int,
    entries: List[MixedFilament],
) -> int:
    """Map a virtual filament ID to an index into *entries*.

    Port of ``mixed_index_from_filament_id()`` from MixedFilament.cpp (L2104-2118).

    :param filament_id: 1-based filament ID (must be > num_physical to be virtual).
    :param num_physical: Number of physical filaments.
    :param entries: All mixed filament entries (including disabled ones).
    :return: Index into *entries*, or ``-1`` if the ID is physical or not found.
    """
    if filament_id <= num_physical:
        return -1

    enabled_virtual_idx = filament_id - num_physical - 1
    seen = 0
    for i, mf in enumerate(entries):
        if not mf.enabled or mf.deleted:
            continue
        if seen == enabled_virtual_idx:
            return i
        seen += 1
    return -1


def resolve_virtual_filament_index(
    filament_id: int,
    num_physical: int,
    entries: List[MixedFilament],
) -> Optional[MixedFilament]:
    """Resolve a virtual filament ID to its :class:`MixedFilament` entry.

    :param filament_id: 1-based filament ID.
    :param num_physical: Number of physical filaments.
    :param entries: All mixed filament entries.
    :return: The :class:`MixedFilament` entry, or ``None`` if not found.
    """
    idx = virtual_filament_id_to_index(filament_id, num_physical, entries)
    if idx < 0:
        return None
    return entries[idx]


# ---------------------------------------------------------------------------
# Display color computation
# ---------------------------------------------------------------------------

def _decode_gradient_ids(gradient_ids_str: str, num_physical: int) -> List[int]:
    """Decode a gradient component IDs string to a list of 1-based physical IDs."""
    ids: List[int] = []
    for ch in gradient_ids_str:
        if ch.isdigit():
            gid = int(ch)
            if 1 <= gid <= num_physical:
                ids.append(gid)
    return ids


def _decode_gradient_weights(weights_str: str, n: int) -> List[int]:
    """Decode a slash-separated weights string to a list of ints, padded/truncated to n."""
    if not weights_str:
        return [1] * n
    parts = weights_str.split("/")
    weights: List[int] = []
    for p in parts:
        try:
            weights.append(max(1, int(p.strip())))
        except ValueError:
            weights.append(1)
    # Pad or truncate to n
    while len(weights) < n:
        weights.append(1)
    return weights[:n]


def _build_weighted_sequence(ids: List[int], weights: List[int]) -> List[int]:
    """Build a weighted repeating sequence of filament IDs."""
    seq: List[int] = []
    for fid, w in zip(ids, weights):
        seq.extend([fid] * w)
    return seq


def _count_frequencies(sequence: List[int]) -> dict:
    """Count occurrences of each value in a sequence."""
    counts: dict = {}
    for v in sequence:
        counts[v] = counts.get(v, 0) + 1
    return counts


def compute_display_color(
    mf: MixedFilament,
    physical_colors: List[str],
) -> str:
    """Compute a display hex color for a virtual mixed filament entry.

    Simplified port of ``compute_mixed_filament_display_color()`` from
    MixedFilament.cpp (L1399-1458).  Covers the three most common cases:
    manual pattern, gradient sequence, and simple A/B ratio blend.

    :param mf: The mixed filament entry.
    :param physical_colors: List of ``"#RRGGBB"`` hex colors for each physical
        filament (0-indexed, so ``physical_colors[0]`` = filament 1).
    :return: A ``"#RRGGBB"`` display color string, or ``"#26A69A"`` on failure.
    """
    from .filament_mixer import blend_multi, blend_two

    fallback = "#26A69A"
    n = len(physical_colors)
    if n == 0:
        return fallback

    def _color(fid: int) -> Optional[str]:
        """1-based filament ID → hex color or None."""
        idx = fid - 1
        if 0 <= idx < n:
            return physical_colors[idx]
        return None

    # --- Manual pattern: count digit frequencies, weighted blend ---
    if mf.manual_pattern:
        flat = mf.manual_pattern.replace(",", "")
        freq: dict = {}
        for ch in flat:
            fid: int
            if ch == "1":
                fid = mf.component_a
            elif ch == "2":
                fid = mf.component_b
            elif ch.isdigit():
                fid = int(ch)
            else:
                continue
            col = _color(fid)
            if col:
                freq[col] = freq.get(col, 0) + 1
        if freq:
            color_percents = list(freq.items())
            if len(color_percents) == 1:
                return color_percents[0][0]
            return blend_multi(color_percents)

    # --- Gradient sequence (≥3 component IDs) ---
    if mf.distribution_mode != DIST_SIMPLE and mf.gradient_component_ids:
        gids = _decode_gradient_ids(mf.gradient_component_ids, n)
        if len(gids) >= 3:
            weights = _decode_gradient_weights(mf.gradient_component_weights, len(gids))
            seq = _build_weighted_sequence(gids, weights)
            freq = _count_frequencies(seq)
            color_percents = [(c, w) for fid, w in freq.items() if (c := _color(fid))]
            if color_percents:
                if len(color_percents) == 1:
                    return color_percents[0][0]
                return blend_multi(color_percents)

    # --- Simple A/B ratio blend ---
    col_a = _color(mf.component_a)
    col_b = _color(mf.component_b)
    if col_a and col_b:
        ratio_a = 100 - mf.mix_b_percent
        ratio_b = mf.mix_b_percent
        if ratio_a == 0:
            return col_b
        if ratio_b == 0:
            return col_a
        return blend_two(col_a, col_b, ratio_a, ratio_b)
    if col_a:
        return col_a
    if col_b:
        return col_b

    return fallback


def populate_display_colors(
    entries: List[MixedFilament],
    physical_colors: List[str],
) -> None:
    """Compute and assign ``display_color`` for every entry in *entries* in-place.

    :param entries: List of mixed filament entries to update.
    :param physical_colors: List of ``"#RRGGBB"`` hex colors (0-indexed).
    """
    for mf in entries:
        mf.display_color = compute_display_color(mf, physical_colors)
