# OrcaSlicer-FullSpectrum: Implementation Guide

> **Source audit completed April 2026** against `ReferenceFiles/OrcaSlicer-FullSpectrum-main/` (v0.9.7).
> This document captures everything needed to implement FullSpectrum support in the Blender 3MF addon.
> Delete this file once the features are shipped.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Serialization Format](#2-serialization-format)
3. [Virtual Filament ID Mapping](#3-virtual-filament-id-mapping)
4. [FilamentMixer Color Model](#4-filamentmixer-color-model)
5. [Config Keys](#5-config-keys)
6. [Resolution Pipeline](#6-resolution-pipeline-slicer-side-only)
7. [Implementation Tasks](#7-implementation-tasks)
8. [Data Flow](#8-data-flow)
9. [Risks & Open Questions](#9-risks--open-questions)
10. [Implementation Order](#10-implementation-order)
11. [Source File Reference](#11-source-file-reference)

---

## 1. Overview

**Fork chain:** Slic3r → PrusaSlicer → BambuStudio → OrcaSlicer → Snapmaker/OrcaSlicer → ratdoux/OrcaSlicer-FullSpectrum

**Key insight:** The 3D model geometry (`3dmodel.model`, `Objects/*.model`, `paint_color` per-triangle attributes) is **completely identical** to standard OrcaSlicer. All FullSpectrum data lives exclusively in `Metadata/project_settings.config` JSON keys. The OPC archive structure, vertices, triangles, components, seam/support paint — all unchanged. `bbs_3mf.cpp` is effectively unmodified for geometry I/O.

**What's new:** "Virtual mixed-color filaments" — computed blends of physical filaments that the slicer resolves to layer-by-layer physical filament switches at slice time. These appear as additional entries in the `paint_color` code table beyond the physical filament indices.

---

## 2. Serialization Format

### 2.1 Container

`mixed_filament_definitions` is a `coString` config option stored in `project_settings.config`. Its value is a semicolon-separated list of row definitions.

**Source:** `MixedFilament.cpp` — `serialize_custom_entries()` (L1673-1703), `load_custom_entries()` (L1706-1889)

### 2.2 Row Format

Each row is comma-separated. Parsed by `parse_row_definition()` (L361-580).

**Fixed positional fields (tokens 0–4):**

| Index | Field | Type | Notes |
|-------|-------|------|-------|
| 0 | `component_a` | uint | 1-based physical filament ID |
| 1 | `component_b` | uint | 1-based physical filament ID |
| 2 | `enabled` | int→bool | 0/1 |
| 3 | `custom` | int→bool | 0 = auto-generated pair, 1 = user-created |
| 4 | `mix_b_percent` | int | 0–100, percentage of component B |

Legacy 4-token format: `a,b,enabled,mix` — `custom` defaults to `true`.

**Variable metadata tokens (index 5+), identified by single-char prefix:**

| Prefix | Field | Type | Notes |
|--------|-------|------|-------|
| `g`/`G` | `gradient_component_ids` | string | Digit string of 1-based physical IDs, e.g. `"123"` for 3-way blend |
| `w`/`W` | `gradient_component_weights` | string | Slash-separated ints, e.g. `"50/25/25"` |
| `m`/`M` | `distribution_mode` | int | 0 = LayerCycle, 1 = SameLayerPointillisme, 2 = Simple |
| `z`/`Z` | `local_z_max_sublayers` | int | Max sublayer depth for local-Z |
| `xa`/`Xa` | `component_a_surface_offset` | float | mm, clamped ±5.0 |
| `xb`/`Xb` | `component_b_surface_offset` | float | mm, clamped ±5.0 |
| `d`/`D` | `deleted` | int→bool | Soft-deleted (force-disables `enabled`) |
| `o`/`O` | `origin_auto` | int→bool | Was auto-generated originally |
| `u`/`U` | `stable_id` | uint64 | Persistent identity for round-trip stability |
| *(none)* | `manual_pattern` | string | Remaining unrecognized tokens joined with commas. Digit string (1–9) with optional `,` group separators |

### 2.3 Serialization Output Order

`serialize_custom_entries()` writes fields in this order:

```
a,b,enabled,custom,mix_b_percent,pointillism_all_filaments,gIDs,wWeights,mMode,zSublayers,xaOffset,xbOffset,dDeleted,oOriginAuto,uStableId[,pattern]
```

**Quirk:** `pointillism_all_filaments` is written positionally at index 5 during serialization (`0` or `1`), but during parsing the tokens at index 5+ are all prefix-matched. The parser sees bare `0`/`1` as an unrecognized token that falls through to pattern accumulation — except it's actually consumed at position 5 before prefix matching begins. Handle this carefully.

### 2.4 Example Rows

```
# Simple 50/50 auto-pair, component 1+2, stable_id=1
1,2,1,0,50,0,g0,w0,m2,z0,xa0.0,xb0.0,d0,o1,u1

# Custom 70/30 blend, component 1+3, with 3-way pattern
1,3,1,1,30,0,g123,w50/25/25,m0,z0,xa0.0,xb0.0,d0,o0,u19,12321

# Disabled/deleted pair
2,3,0,0,50,0,g0,w0,m2,z0,xa0.0,xb0.0,d1,o1,u4
```

### 2.5 Pattern Field Details

- Digits `1`–`9` are physical filament indices
- `'a'` → `'1'` (component_a), `'b'` → `'2'` (component_b) — aliases
- Commas separate **perimeter groups** (e.g., `"12,21"` = outer perimeters use pattern `12`, inner use `21`)
- `flatten_manual_pattern_groups()` strips commas for flat layer-cycle use
- `normalize_manual_pattern()` validates and canonicalizes

**Resolution:** `'1'` always maps to `component_a`, `'2'` to `component_b`, `'3'`–`'9'` are direct physical filament IDs. (See `physical_filament_from_pattern_step()`)

### 2.6 Auto-Generate vs Custom

`auto_generate()` creates all C(N,2) pairwise combinations of N physical filaments at 50/50 ratio. These have `custom=false, origin_auto=true`. User-created rows have `custom=true`. On load, auto rows are matched by canonical pair key; custom rows are appended after.

---

## 3. Virtual Filament ID Mapping

**Source:** `mixed_index_from_filament_id()` (L2104-2118)

```
virtual_filament_id = num_physical + 1 + enabled_index
```

Where `enabled_index` counts only entries where `enabled=true AND deleted=false`, walking the `m_mixed` array in order.

**The `stable_id` (uN) is NOT the virtual slot number.** It's a persistent identity marker that survives rebuilds of the mixed list. Virtual slot numbers are computed dynamically from array position.

**Total filament count:** `total_filaments(n) = n + enabled_count()`

**For our addon:** When `paint_color` codes map to filament indices > `num_physical`, those are virtual filaments. Walk the definitions list counting enabled entries to resolve the index to a specific `MixedFilament` entry.

---

## 4. FilamentMixer Color Model

### 4.1 Architecture

**Source:** `filament_mixer_model.h` (L1-812), `filament_mixer.cpp` (L1-82)

This is **NOT** simple RGB lerp. It's a degree-4 polynomial regression trained to approximate [Mixbox](https://scrtwpns.com/mixbox/) pigment/subtractive mixing. Blue + Yellow → **Green** (not Teal).

| Property | Value |
|----------|-------|
| Model type | Degree-4 polynomial regression |
| Inputs | 7 (R1, G1, B1, R2, G2, B2, t) — sRGB 0–255 |
| Features | 330 (all monomials up to degree 4) |
| Outputs | 3 (R, G, B) — sRGB 0–255, clamped |
| Accuracy | Mean Delta-E ≈ 2.07 |
| License | **MIT** (Copyright 2026 Justin Hayes) — separate from AGPL slicer |
| Generator | `scripts/export_poly_coefficients.py` (not in public repo) |

### 4.2 Data Tables

| Table | Dimensions | Type | Data Size |
|-------|-----------|------|-----------|
| `POWERS` | 330 × 7 | int | 2,310 values |
| `COEF` | 330 × 3 | double | 990 values |
| `INTERCEPT` | 3 | double | 3 values |
| **Total** | | | **3,303 values** (~35KB) |

### 4.3 Algorithm

```python
# Pseudocode for filament_mixer_lerp
def lerp(r1, g1, b1, r2, g2, b2, t):
    if t <= 0: return (r1, g1, b1)
    if t >= 1: return (r2, g2, b2)

    x = [r1, g1, b1, r2, g2, b2, t]  # 7 inputs

    # Compute 330 polynomial features
    features = []
    for exponents in POWERS:  # 330 rows of 7 exponents each
        val = 1.0
        for j in range(7):
            if exponents[j] != 0:
                val *= x[j] ** exponents[j]
        features.append(val)

    # Dot product with coefficients
    rgb = []
    for c in range(3):
        val = INTERCEPT[c]
        for i in range(330):
            val += features[i] * COEF[i][c]
        rgb.append(clamp(int(val), 0, 255))

    return tuple(rgb)
```

### 4.4 Multi-Color Blending

`blend_color_multi()` does **sequential pairwise accumulation**:

```python
# Start with first color
result = colors[0]
accumulated_weight = weights[0]

for i in range(1, len(colors)):
    new_total = accumulated_weight + weights[i]
    t = weights[i] / new_total
    result = filament_mixer_lerp(*result, *colors[i], t)
    accumulated_weight = new_total
```

This is order-dependent but matches the slicer's display.

### 4.5 Porting Notes

- The polynomial evaluation is pure math — no external dependencies
- With numpy: vectorize the feature computation across all 330 monomials
- The `POWERS` table is sparse (most exponents are 0) — can skip zero-power terms
- A `_linear_float` variant exists that converts linear → sRGB before blending, then sRGB → linear after — use for Blender's linear color space
- Coefficients can be stored inline in Python source or as a JSON data file

### 4.6 Display Color Computation Priority

From `compute_mixed_filament_display_color()` (L1399-1458):

1. **Bias-apparent blend** — if component bias enabled, adjust percentages by nozzle diameter
2. **Manual pattern sequence** — count digit frequencies, build weighted sequence, multi-blend
3. **Gradient sequence** — ≥3 gradient_component_ids, weighted sequence, multi-blend
4. **Effective pair preview** — Bresenham-style interleaved A/B sequence from mix_b_percent
5. **Simple ratio blend** — `blend(colorA, colorB, 100-mix_b, mix_b)`
6. **Fallback** — `#26A69A` (teal)

For our addon's purposes, a simplified version covering cases 2, 3, and 5 covers the vast majority of real-world files.

---

## 5. Config Keys

### 5.1 Keys in `project_settings.config` (13 keys)

From `PresetBundle.cpp` L62-86 (`s_project_options`):

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `mixed_filament_definitions` | `coString` | `""` | The serialized definitions string |
| `mixed_filament_gradient_mode` | `coBool` | `false` | Height-weighted cadence toggle |
| `mixed_filament_height_lower_bound` | `coFloat` | `0.04` | Gradient lower bound (mm) |
| `mixed_filament_height_upper_bound` | `coFloat` | `0.16` | Gradient upper bound (mm) |
| `mixed_filament_advanced_dithering` | `coBool` | `false` | Ordered dithering pattern |
| `mixed_filament_component_bias_enabled` | `coBool` | `false` | Per-row nozzle-diameter bias control |
| `mixed_filament_surface_indentation` | `coFloat` | `0.0` | XY offset for mixed regions (±2.0mm) |
| `mixed_filament_region_collapse` | `coBool` | `true` | Merge same-color mixed regions |
| `mixed_color_layer_height_a` | `coFloat` | `0.0` | Dithering cadence height A |
| `mixed_color_layer_height_b` | `coFloat` | `0.0` | Dithering cadence height B |
| `dithering_z_step_size` | `coFloat` | `0.0` | Layer height in Z dithering zones |
| `dithering_local_z_mode` | `coBool` | `false` | Local Z dithering toggle |
| `dithering_step_painted_zones_only` | `coBool` | `true` | Limit Z step to painted zones |

### 5.2 Keys NOT in project_settings (print preset only)

These are stored in the print preset, not the project config. Our passthrough should handle them if present, but they won't appear in `project_settings.config` under normal circumstances.

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `mixed_filament_pointillism_pixel_size` | `coFloat` | `0.0` | Same-layer pointillism segment length |
| `mixed_filament_pointillism_line_gap` | `coFloat` | `0.0` | Pointillism spacing |
| `local_z_wipe_tower_purge_lines` | `coFloat` | `3.0` | Wipe tower purge for local Z |

---

## 6. Resolution Pipeline (Slicer-Side Only)

We don't need to implement this — it's how the slicer decides which physical filament to extrude on each layer. Understanding it informs what metadata matters for round-trip.

**Priority order per extrusion entity:**

1. **Same-Layer Pointillism** (`distribution_mode == 1`): Paths split into segments of `pointillism_pixel_size` length, each segment assigned from a weighted repeating sequence. Phase advances by layer for offset.
2. **Grouped Manual Pattern** (pattern contains `,`): Perimeters split by index, each gets a physical filament from the corresponding pattern group.
3. **Height-weighted cadence** (`layer_height_a/b > 0`): Z-position-based cycling.
4. **Manual pattern** (flat, no groups): `layer_index % pattern_length` → digit → physical filament.
5. **Gradient sequence** (3+ `gradient_component_ids`): Weighted sequence, `layer_index % len`.
6. **Advanced dithering**: Bresenham-style phase-shifted interleaving.
7. **Simple ratio cycle**: `layer_index % (ratio_a + ratio_b) < ratio_a` → component_a, else component_b.

---

## 7. Implementation Tasks

### 7.1 Passthrough Verification 🟢

**Effort:** Small — integration test only

Our existing passthrough preserves arbitrary JSON keys in `project_settings.config`. Verify all 13 keys round-trip without data loss.

**Action:** Add a round-trip integration test with a FullSpectrum reference file.

**Files:** Test files only.

### 7.2 Detection 🟢

**Effort:** ~20 lines

Add FullSpectrum detection to `detect_vendor()` in `import_3mf/slicer/detection.py`. Check if `project_settings.config` JSON contains `mixed_filament_definitions` with a non-empty value.

**Decision needed:** New vendor string (`"orca_fullspectrum"`) vs sub-flag on `"orca"`. Flag approach is cleaner since the file format is Orca-compatible.

**Files:** `import_3mf/slicer/detection.py`

### 7.3 Core Module — Parse, Serialize, Compute Colors 🟡

**Effort:** Medium — new module, ~300-400 lines

**New file:** `common/mixed_filaments.py`

Contents:
1. **`MixedFilament` dataclass** mirroring the C++ struct (all fields from §2.2)
2. **`parse_mixed_filament_definitions(defs_string) -> list[MixedFilament]`** — port of `parse_row_definition()` + semicolon split
3. **`serialize_mixed_filament_definitions(entries) -> str`** — port of `serialize_custom_entries()`
4. **`compute_display_color(entry, physical_colors) -> str`** — simplified `compute_mixed_filament_display_color()`
5. **`resolve_virtual_filament_index(filament_id, num_physical, entries) -> int`** — port of `mixed_index_from_filament_id()`
6. **`normalize_manual_pattern(pattern) -> str`** — validation + canonicalization
7. **`auto_generate_pairs(num_physical) -> list[MixedFilament]`** — C(N,2) pairwise generation

### 7.4 FilamentMixer Port 🟡

**Effort:** Medium — data extraction + ~50 lines of logic

**New file:** `common/filament_mixer.py`

Contents:
1. `POWERS`, `COEF`, `INTERCEPT` tables extracted from `filament_mixer_model.h`
2. `filament_mixer_lerp(r1, g1, b1, r2, g2, b2, t) -> (r, g, b)` — polynomial evaluation
3. `filament_mixer_lerp_linear(r1, g1, b1, r2, g2, b2, t) -> (r, g, b)` — linear-space variant for Blender
4. `blend_multi(color_percents: list[tuple[str, int]]) -> str` — sequential pairwise accumulation
5. `blend_two(hex_a, hex_b, ratio_a, ratio_b) -> str` — convenience wrapper

**Data storage decision:** Inline in Python source (~35KB), separate JSON, or numpy `.npy`. Inline is simplest for a Blender addon. The `POWERS` table is especially sparse — could compress by only storing non-zero exponents.

**Testing:** Compare output against known C++ results from reference files. The MIT license permits porting the coefficient data.

### 7.5 Import — Extend Paint Code Table 🟡

**Effort:** Small-medium

Current `ORCA_PAINT_TO_INDEX` / `ORCA_FILAMENT_CODES` stop around index 29-32. Virtual filament IDs can reach 44+ (PeggyPalette has 40 definitions with 4 physical filaments).

**Changes:**
- Extend tables to cover indices up to at least 64
- Or compute codes programmatically (the Bambu hex series has a pattern)
- When a `paint_color` maps to a virtual index, look up the computed display color from parsed definitions
- The `filament_colour` array in `project_settings.config` already includes virtual display colors appended after physical ones

**Files:** `import_3mf/slicer/paint.py`, `export_3mf/materials/base.py`

### 7.6 Import — Store Definitions as Custom Properties 🟢

**Effort:** Small

Store parsed data as scene-level custom properties for round-trip:
- `scene["3mf_mixed_filament_definitions"]` = raw string (exact round-trip)
- Optionally structured properties for UI display

**Files:** `import_3mf/slicer/colors.py` (reading), `import_3mf/scene.py` (storing)

### 7.7 Export — Serialize Definitions 🟡

**Effort:** Medium

In the OrcaExporter:
- If mixed filament definitions exist (stashed from import or user-created), serialize back to `mixed_filament_definitions`
- Extend `ctx.vertex_colors` to include virtual filament entries with indices > `num_physical`
- Extend `ORCA_FILAMENT_CODES` for virtual indices
- Write computed display colors into `filament_colour` array (physical first, then virtual)
- Preserve all 13 project-settings keys

**Critical:** `paint_color` attribute uses the same Bambu hex code series for physical and virtual. Triangle painted with virtual filament 5 (first enabled mixed entry, 4 physical filaments) gets the code for index 5.

**Files:** `export_3mf/orca.py`, `export_3mf/materials/base.py`, `export_3mf/context.py`

### 7.8 MMU Paint UI — Virtual Filament Palette 🔴

**Effort:** Large — full UI feature

**User spec:**
- The entire advanced (mixed filament) feature is **hidden by default**. It becomes visible either when the user explicitly enables it in addon Preferences, OR when importing a file that contains `mixed_filament_definitions`.
- The "Mix Colors" section lives in a **separate, collapsed-by-default sub-panel** (dropdown) inside the texture paint mode panel only — NOT in the seam or support paint panels. It should be visually distinct from the regular filament palette above it.
- Within that sub-panel: show/add/remove/reorder virtual mixed filaments.

**UI structure:**
```
[MMU Paint Panel - Texture Paint mode only]
  ┌─────────────────────────────────────┐
  │  [Regular filament palette here]    │
  ├─────────────────────────────────────┤
  │  ▶ Mix Colors  (collapsed by default)│  ← separate sub-panel
  │    [Mixed filament list]            │
  │    [+ Add Mix] [× Remove]           │
  │    Per-entry: A picker, B picker,   │
  │    ratio slider, mode dropdown,     │
  │    pattern field, color swatch      │
  └─────────────────────────────────────┘
```

**Visibility logic:**
- Addon preference: `show_mixed_filaments: BoolProperty` (default False)
- Scene property: `has_mixed_filaments: BoolProperty` (set True on import if defs found)
- Panel polls: `show_mixed_filaments OR has_mixed_filaments`
- The "Mix Colors" sub-panel is its own `bpy.types.Panel` with `bl_parent_id` pointing to the main MMU panel — this lets it collapse independently

**New properties:**
- `MMUMixedFilamentItem` PropertyGroup with all MixedFilament fields
- Scene-level `CollectionProperty` on `MMUPaintSettings`
- Addon preference `show_mixed_filaments` in the existing preferences class in `__init__.py`

**Files:** `paint/properties.py`, `paint/mmu_panel.py`, `paint/operators.py`, `__init__.py`

### 7.9 Bake Pipeline — Virtual Filament Matching 🔴

**Effort:** Large — algorithm design needed

Extend bake and quantize pipelines:
- Include virtual filament display colors in quantization palette
- Record virtual filament index when pixel quantizes to a virtual color
- Handle ambiguity: virtual colors can be very close (40%/50%/60% blends of same pair)

**Files:** `paint/bake.py`, `paint/quantize.py`

---

## 8. Data Flow

```
IMPORT:
  project_settings.config JSON
    → read "mixed_filament_definitions" string
    → parse_mixed_filament_definitions() → [MixedFilament, ...]
    → read "filament_colour" array → physical colors
    → compute_display_color() for each enabled entry → virtual colors
    → extend palette: physical + virtual colors
    → paint_color attribute on triangles → ORCA_PAINT_TO_INDEX → filament index
      → index ≤ num_physical: physical filament
      → index > num_physical: virtual filament (look up by enabled_index)
    → assign colors to paint texture
    → store raw definitions string as scene property

EXPORT:
  MMU Paint texture + definitions (stashed or user-created)
    → serialize_mixed_filament_definitions() → string
    → write to project_settings.config JSON
    → physical + virtual display colors → "filament_colour" array
    → per-triangle filament index → ORCA_FILAMENT_CODES[index] → paint_color
    → preserve all 13 mixed/dithering config keys
```

---

## 9. Risks & Open Questions

### Known Risks

1. **FilamentMixer accuracy** — Python port must match C++ polynomial evaluation exactly. Off-by-one in integer clamping or feature computation produces wrong colors. Unit tests against known C++ results are essential.

2. **Paint code table overflow** — Current table stops around index 32. Must verify the Bambu hex series pattern continues predictably, or compute codes programmatically.

3. **stable_id management** — When users create new mixed filaments in Blender, stable_ids must not collide with imported ones. The slicer uses `dedupe_stable_id()` with a seen-set. We should replicate this.

4. **auto_generate vs custom rows** — The slicer auto-generates C(N,2) pairs and separately tracks custom rows. On export, decide: auto-generate all, or only emit what the user defined? Emitting only defined rows is safer for round-trip.

5. **Quantization ambiguity** — Virtual filament colors (e.g., 40% vs 50% vs 60% blends of same pair) can be very similar. The HSV-weighted distance metric may need priority rules or minimum-distance thresholds.

### Open Questions

1. **Detection: new vendor or flag?** `"orca_fullspectrum"` vs `vendor="orca"` with `has_mixed=True`. Flag is cleaner since the format is Orca-compatible.

2. **Coefficient storage format?** Inline Python (~35KB), JSON file, or numpy `.npy`? Inline avoids file I/O at import time.

3. **Phase 1 scope?** Ship passthrough + display first (tasks 7.1–7.7), defer full editing (7.8–7.9) to phase 2?

4. **Pointillism/grouped patterns in UI?** These are slicer-side resolution only. Show pattern field in UI for authoring, but don't try to preview the per-perimeter effect.

5. **Fallback for missing FilamentMixer?** Offer simple sRGB lerp as a fast path when pigment accuracy isn't needed?

---

## 10. Implementation Order

| Phase | Task | Section | Complexity |
|-------|------|---------|------------|
| 1 | Passthrough verification | §7.1 | 🟢 |
| 1 | Core parse/serialize module | §7.3 | 🟡 |
| 1 | FilamentMixer port | §7.4 | 🟡 |
| 1 | Detection | §7.2 | 🟢 |
| 1 | Import display colors | §7.5 + §7.6 | 🟡 |
| 1 | Export serialization | §7.7 | 🟡 |
| 2 | MMU Paint UI | §7.8 | 🔴 |
| 2 | Bake virtual matching | §7.9 | 🔴 |

Phase 1 gets faithful import → display → export round-trip without the full editing UI.
Phase 2 adds authoring capabilities in Blender.

---

## 11. Source File Reference

### FullSpectrum Source (read during audit)

| File | What we read | Key findings |
|------|-------------|--------------|
| `src/libslic3r/MixedFilament.hpp` | Full (L1-400) | MixedFilament struct, MixedFilamentManager class, DistributionMode enum |
| `src/libslic3r/MixedFilament.cpp` | L1-2207 | serialize/parse/resolve/auto_generate/color computation/pattern normalization |
| `src/libslic3r/filament_mixer_model.h` | Full (L1-812) | POWERS/COEF/INTERCEPT tables, compute_poly_features(), lerp() |
| `src/libslic3r/filament_mixer.cpp` | Full (L1-82) | Wrapper functions, linear-space variant |
| `src/libslic3r/PrintConfig.cpp` | Mixed/dithering options | 16 config option definitions with types and defaults |
| `src/libslic3r/PresetBundle.cpp` | L62-86 | 13 project-settings keys (s_project_options) |
| `src/libslic3r/Format/bbs_3mf.cpp` | L611-622 | Import reads definitions → auto_generate → load_custom_entries → total_filaments |
| `src/libslic3r/GCode.cpp` | L3797-5345 | Pointillism path splitting, grouped pattern perimeter splitting |
| `src/libslic3r/GCode/ToolOrdering.cpp` | L30-134 | resolve_mixed_with_layer_heights(), height-weighted cadence, resolve_perimeter() |

### Our Addon Files (to modify)

| File | Changes needed |
|------|---------------|
| `common/mixed_filaments.py` | **NEW** — MixedFilament dataclass, parse/serialize, display color computation |
| `common/filament_mixer.py` | **NEW** — polynomial regression color model port |
| `import_3mf/slicer/detection.py` | Add FullSpectrum detection |
| `import_3mf/slicer/colors.py` | Read + parse mixed_filament_definitions, compute virtual colors |
| `import_3mf/slicer/paint.py` | Extend ORCA_PAINT_TO_INDEX / ORCA_FILAMENT_CODES for virtual indices |
| `import_3mf/scene.py` | Store definitions as scene custom property |
| `export_3mf/orca.py` | Serialize definitions, write virtual display colors, preserve config keys |
| `export_3mf/materials/base.py` | Extend filament code tables |
| `export_3mf/context.py` | Possibly extend ExportContext for mixed filament state |
| `paint/properties.py` | (Phase 2) MMUMixedFilamentItem PropertyGroup |
| `paint/mmu_panel.py` | (Phase 2) Virtual filament UI |
| `paint/operators.py` | (Phase 2) Mixed filament creation/editing operators |
| `paint/bake.py` | (Phase 2) Virtual color quantization |
| `paint/quantize.py` | (Phase 2) Extended palette matching |

### Reference Files

| File | Description |
|------|-------------|
| `Multi-color Cute Dragon by IK3Digital/` | Simple 6-definition file (u1–u6), 6 physical filaments |
| `ReferenceFiles/PeggyPalette38+Mini+BRYW/` | Complex 40-definition file with patterns, 4 physical (CMYW) |
| `ReferenceFiles/OrcaSlicer-FullSpectrum-main/` | Full slicer source code |

---

## 12. Phase 2 UI Implementation Plan

### Overview

Phase 2 adds the authoring UI for mixed filaments inside the MMU Paint panel.
The feature is **hidden by default** and becomes visible automatically when a
FullSpectrum file has been imported, or when the user enables it in Preferences.

### Chunk 1 — Preference toggle + scene property plumbing

**Files:** `__init__.py`, `paint/properties.py`, `paint/__init__.py`

- Add `show_mixed_filaments: BoolProperty` to `ThreeMFPreferences` (shown in ADVANCED tab)
- Add `MMUMixedFilamentItem` PropertyGroup with fields: `component_a`, `component_b`,
  `mix_b_percent`, `distribution_mode`, `manual_pattern`, `display_color` (FloatVector RGB),
  `stable_id`, `enabled`, `deleted`, `label` (computed display name)
- Add to `MMUPaintSettings`: `has_mixed_filaments: BoolProperty`,
  `mixed_filaments: CollectionProperty(type=MMUMixedFilamentItem)`,
  `active_mixed_filament_index: IntProperty`
- Register `MMUMixedFilamentItem` before `MMUPaintSettings` in `paint/__init__.py`
- In `import_3mf/operator.py`: after storing scene property, also populate
  `scene.mmu_paint.mixed_filaments` from `ctx.mixed_filament_entries` and set
  `scene.mmu_paint.has_mixed_filaments = True`

### Chunk 2 — Sync virtual slots into the filament palette

**Files:** `paint/helpers.py`, `import_3mf/operator.py`

- After populating `mixed_filaments`, also append them to `scene.mmu_paint.filaments`
  (the existing `MMUFilamentItem` list used by the live palette)
- Virtual slots use `display_color` as their swatch. Their `index` values continue
  the sequence above the physical filament count
- Existing brush-switching (`MMU_OT_select_filament`) requires zero changes — virtual
  slots are just more entries in the same list

### Chunk 3 — "Mix Colors" sub-panel draw

**Files:** `paint/mmu_panel.py`, `paint/__init__.py`

- New `VIEW3D_PT_mmu_mix_colors` panel with `bl_parent_id = "VIEW3D_PT_mmu_paint"`,
  `bl_options = {'DEFAULT_CLOSED'}`, same `bl_context = "imagepaint"`
- `poll()`: `(prefs.show_mixed_filaments OR scene.mmu_paint.has_mixed_filaments)
  AND _get_paint_mesh(context) is not None`
- `draw()`: `MMU_UL_mixed_filaments` UIList + Add/Remove buttons
- Per-entry row: blended color swatch (read-only), component A index label,
  component B index label, mix % display

### Chunk 4 — Mix Colors operators

**Files:** `paint/operators.py`

- `MMU_OT_add_mixed_filament` — adds new entry, auto-picks next C(N,2) unused pair,
  assigns `stable_id = max_existing + 1`, computes `display_color`, appends to palette
- `MMU_OT_remove_mixed_filament` — soft-deletes active entry (sets `deleted=True`),
  removes from palette filament list
- `MMU_OT_recompute_mix_color` — recomputes `display_color` on an entry;
  called as `update=` callback on `mix_b_percent`, `component_a`, `component_b`

### Chunk 5 — Export round-trip from UI edits

**Files:** `export_3mf/orca.py`, `common/mixed_filaments.py`

- In `generate_project_settings()`: if `scene.mmu_paint.mixed_filaments` has entries
  AND those entries differ from the raw stash (or stash is empty), re-serialize from
  the PropertyGroup instead of using the raw string
- Conversion: `MMUMixedFilamentItem` → `MixedFilament` dataclass →
  `serialize_mixed_filament_definitions()` → write to config JSON
- This closes the loop: user can add a mix in the UI and export a valid FullSpectrum file
