# 🗺️ Development Roadmap

> **3MF Import/Export for Blender** — Future Development Plan

Features and improvements organized by priority. Complexity ratings help with planning but don't determine feasibility — we can tackle hard problems with proper research.

---

## 📊 Legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Implemented |
| 📋 | Planned or manual validation pending |
| 💭 | Needs Research |

**Complexity:** `🟢 Easy` `🟡 Medium` `🔴 Hard`

---

## 🖨️ Slicer Compatibility

### OrcaSlicer / BambuStudio Mixed Filaments

| Status | Feature | Complexity | Description |
|--------|---------|------------|-------------|
| ✅ Implemented | Per-slot mixed-filament support | 🟢 | Read, author, and export Orca/Bambu `filament_is_mixed` and `filament_mixed_*` slot data. |
| ✅ Implemented | Blender authoring and paint palette | 🟡 | Edit physical slots and two-/three-component mixed slots; configure gradients; assign slots to objects; include mixed slots in paint palettes and bake/quantize color lists. |
| ✅ Implemented | Color prediction and recommendations | 🟡 | Recompute mixed-slot swatches using the bundled Orca recipe data and blend model; recommend recipes from project filaments or CMYW/RYBW tables. |
| ✅ Implemented | Mixed-settings AUTO export | 🟡 | Route projects with stashed Orca mixed slots to Orca export; integration tests cover authored settings through export and re-import. |
| 📋 Manual validation pending | Slicer open and slice | 🟡 | Open Blender-authored and round-tripped files in OrcaSlicer or BambuStudio; inspect slot recipes, object assignments, colors, gradients, and painted regions, then slice a representative project. See `MANUAL_SLICER_VALIDATION.md`. |

### OrcaSlicer-FullSpectrum (legacy format)

FullSpectrum's `mixed_filament_definitions` format is **not supported**. On import, references to virtual slots fall back to filament 1 with a warning; FullSpectrum-only settings are removed on export. This is compatibility fallback, not FullSpectrum recipe import or conversion.

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

*Current version: 2.7.8*
