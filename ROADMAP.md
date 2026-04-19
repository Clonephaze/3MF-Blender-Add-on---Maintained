# 🗺️ Development Roadmap

> **3MF Import/Export for Blender** — Future Development Plan

Features and improvements organized by priority. Complexity ratings help with planning but don't determine feasibility — we can tackle hard problems with proper research.

---

## 📊 Legend

| Symbol | Meaning |
|--------|---------|
| 📋 | Planned |
| 💭 | Needs Research |

**Complexity:** `🟢 Easy` `🟡 Medium` `🔴 Hard`

---

## 🖨️ Slicer Compatibility

### OrcaSlicer-FullSpectrum (Snapmaker U1)
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | Round-trip passthrough | 🟢 | Verify `project_settings.config` passthrough preserves `mixed_filament_definitions` and related keys without data loss |
| 📋 | Detection | 🟢 | Detect FullSpectrum files via `mixed_filament_definitions` key in `project_settings.config` |
| 📋 | Import — blended colors | 🟡 | Parse `mixed_filament_definitions`; for 2-way blends use `lerp(A, B, ratio/100)`; for patterned blends compute weighted average from digit frequencies in the pattern field. Extend paint code table to cover virtual IDs (5+). |
| 📋 | Import — metadata preservation | 🟢 | Store parsed virtual filament definitions as a custom mesh/scene property so they survive round-trip export |
| 📋 | MMU Paint UI — virtual filament palette | 🔴 | Extend MMU Paint sidebar to show and define virtual (mixed) filaments: two-physical-filament picker, ratio slider, optional pattern field for 3-way+ blends, dithering mode selector, live blended color preview swatch. Two-tier display: "Mini" (6 quick 50/50 pairs) and "Full" (custom ratio/pattern entries). |
| 📋 | Bake — virtual filament mapping | 🔴 | Extend bake pipeline so quantized regions can be assigned to virtual filaments; baked colors that fall between physical filament colors can be auto-matched to the nearest virtual filament's computed blend color (including 3-way pastel/earth tones via pattern blends) |
| 📋 | Export — mixed filament definitions | 🟡 | Serialize virtual filament definitions back to `mixed_filament_definitions` string on Orca export; preserve u-numbers and trailing pattern field; write virtual IDs as paint codes using extended Bambu hex series |

#### Research Notes

**Format:** FullSpectrum is a fork of Snapmaker/OrcaSlicer. The 3D model geometry (`3dmodel.model`, `paint_color` attributes) is **identical** to standard Orca — all new data lives exclusively in `Metadata/project_settings.config` as additional JSON keys.

**`mixed_filament_definitions` encoding:** Semicolon-separated row entries. Each row is CSV: `A,B,enabled,custom,mix_b_percent[,prefixed_metadata...[,pattern]]`. Field 3 is `custom` (0=auto pair, 1=user-created), NOT "gradient" as initially assumed. Metadata tokens use single-char prefixes (`g`=gradient IDs, `w`=weights, `m`=mode, `z`=sublayers, `xa`/`xb`=offsets, `d`=deleted, `o`=origin_auto, `u`=stable_id). Trailing unrecognized tokens form the manual pattern.

**u-numbers are stable_ids, NOT virtual slot numbers.** The `uN` value is a persistent identity marker for round-trip stability. Virtual filament slot = `num_physical + 1 + enabled_index` (counting only enabled && !deleted entries in array order). Gaps in u-numbers are normal.

**Two-tier palette system:**
- **Auto pairs:** C(N,2) pairwise 50/50 blends, `custom=0, origin_auto=1`
- **Custom entries:** User-defined ratios + patterns, `custom=1, origin_auto=0`

**Color blending uses FilamentMixer** — a degree-4 polynomial regression (MIT licensed) approximating Mixbox pigment mixing. Blue+Yellow → Green, not Teal. NOT simple RGB lerp. Multi-color blending is sequential pairwise accumulation.

**Full implementation guide:** See `docs/fullspectrum-implementation-guide.md`

**Reference file:** `PeggyPalette38+Mini+BRYW` (Cyan, Magenta, Yellow, White) is a developer-made calibration model. It encodes a complete CMY color wheel: 6 primary pairwise blends at 5 ratio steps each (outer ring), 9 three-way White pastel blends (inner ring), 1 equal 3-way "drab" center, and 9 advanced cadence-pattern blends — 34 virtual filaments used in this model, with 6 additional "Mini" definitions available but unused by the model geometry.

---

### Cura
| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 💭 | MMU Research Needed | 🔴 | Import/Export of cura MMU Data Needed (I'll be honest, I don't want to do this. PRs would be very welcome) |

---

## 🧪 Testing & Docs

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| 📋 | User Guide | 🟡 | Usage documentation (needs improvement — tutorial-style docs for common workflows) |
| 📋 | API Documentation | 🟡 | Public API reference (needs improvement — auto-generated docs) |
| 📋 | Performance Benchmarks | 🟡 | Establish benchmarks for large-file import/export (100k+ triangles, many objects) to catch regressions |

---

### Research Notes

#### Cura MMU Data
Cura stores MMU data in a PNG texture file using the blue hue channel. It supports 8 colors, mapping them to the first 8 values of blue: 1/255 blue = material index 1, 2/255 = index 2, etc. 
It reads this for color zones, but uses another file for actual color data. No seam or support data seems to be stored in the texture file. Cura doesn't appear to have seam painting at all.

---

## 🤝 Contributing

Help wanted:
1. **Testing** — Try different slicers, report issues
2. **Research** — Document undocumented slicer formats
3. **Bug fixes** — If there's an open issue you think you can tackle, comment to claim it and we can discuss the approach
4. **Code** — Pick something from the roadmap and PR it

---

*Current version: 2.2.1*
