# Code Review: Blender 3MF Import/Export Addon

**Date:** February 27, 2026  
**Scope:** 63 Python files (~14,800 LOC), 30 test files (~3,400 LOC)  
**Status:** All actionable issues resolved. 600 tests (331 unit + 269 integration), 0 failures. +246 new tests added across enhancement phases.

Overall this is an impressively well-structured addon with clear separation of concerns, proper context-based state management, and solid spec coverage. The architecture (context dataclasses, exporter dispatch, mirrored import/export sub-packages) is strong. Below are the findings ranked by severity.

---

## 🔴 Critical Issues

### 1. ~~`TriangleState.NONE` does not exist~~ — **FIXED**

`common/segmentation.py` line 404: Changed `TriangleState.NONE` → `TriangleState.DEFAULT`.

### 2. ~~Missing `to_mesh_clear()` in `StandardExporter.write_object_resource()`~~ — **FIXED**

`standard.py`: Added `blender_object.to_mesh_clear()` before the return statement.

### 3. ~~Specular color not converted from linear to sRGB~~ — **FIXED**

`export_3mf/materials/pbr.py`: Added `linear_to_srgb()` conversion before hex encoding.

### 4. ~~`_thumbnail_image_items()` missing enum cache~~ — **FIXED**

`export_3mf/operator.py`: Added `_thumbnail_image_cache` module-level list and cache assignment.

---

## 🟡 Suggestions

### 5. ~~Orca UUID format breaks for >9 objects~~ — **FIXED**

`orca.py`: Changed to `f"{object_counter:08x}-..."` zero-padded hex formatting.

### 6. ~~Redundant ZIP archive opens in slicer color readers~~ — **FIXED**

`import_3mf/slicer/colors.py`: Added optional `archive` parameter to all 5 functions + `read_all_slicer_colors()` consolidated wrapper. Updated `operator.py` and `api.py` callers.

### 7. Guard debug() calls that do numpy work — **FIXED**

`export_3mf/segmentation.py`: Guarded `np.unique(state_map)` with `DEBUG_MODE` check.

### 8. `ast.literal_eval()` safety — **ALREADY SAFE** (no fix needed)

All 7 callsites are already wrapped in try/except.

### 9-12. Various items — **ALREADY ADDRESSED or N/A**

- Spelling "ANALAGOUS" — not found in codebase
- Slicer detection hardening — already properly guarded
- Numeric metadata validation — already has try/except
- Bake pixel access — properly handled

### 13. ~~Orca exporter ignores `coordinate_precision`~~ — **FIXED**

`orca.py`: Changed `str(vertex.co.x)` to `f"{vertex.co.x:.{decimals}}"` using `ctx.options.coordinate_precision`.

### 14-17. Structural suggestions — **DEFERRED**

- Thumbnail error handling — already comprehensive
- Depsgraph deduplication — accessor is cheap, not a real issue
- Atomic file writes — good idea, deferred for future work  
- Operator.py size reduction — structural refactor, deferred

---

## ✅ Good Practices — What's done well

1. **Context dataclasses** (`ImportContext`/`ExportContext`) replacing operator `self.*` state — clean, testable, and well-documented.

2. **`safe_report()` pattern** — graceful fallback for headless/test execution. Used consistently throughout.

3. **Defensive XML parsing** — all `ParseError` exceptions are caught, warned, and the import continues with degraded data rather than crashing.

4. **Unicode safety** — the `str()` caching pattern for Blender C strings before XML operations is correctly applied.

5. **Vectorized numpy rasterizer** in `import_3mf/segmentation.py` — edge-function triangle fill with gap-closing dilation is well-implemented.

6. **Comprehensive Materials Extension support** — basematerials, colorgroups, composites, multiproperties, texture2d, PBR metallic/specular/translucent with textured variants. The round-trip passthrough architecture preserves data the addon doesn't natively understand.

7. **Segmentation codec** in `common/segmentation.py` — faithful implementation of PrusaSlicer's binary tree format with excellent documentation.

8. **Public API** (`api.py`) — well-designed keyword-only interface with callbacks, progress reporting, and building-block re-exports for custom workflows.

9. **Component/linked-duplicate optimization** — correctly detects shared mesh data and uses 3MF `<component>` references to avoid duplicating geometry.

10. **Test quality** — integration tests create real Blender objects and round-trip through actual 3MF files. The materials extension tests are particularly thorough with per-node-value verification.

---

## Test Coverage Assessment

**600 tests total** (331 unit + 269 integration), 0 failures.

| Module | Tested? | Notes |
|--------|---------|-------|
| `common/colors.py` | ✅ Thorough | - |
| `common/segmentation.py` | ✅ Thorough | - |
| `common/types.py` | ✅ Good | - |
| `common/xml.py` | ✅ Good | - |
| Export (standard, orca) | ✅ **Excellent** | +30 tests: write_triangles material modes, archive structure, metadata, Orca multi-file |
| Import (basic, roundtrip) | ✅ **Excellent** | +48 tests: read_triangles direct, options/modes, scene helpers |
| Materials Extension | ✅ **Excellent** | - |
| API surface | ✅ **Excellent** | +22 tests: versioning, discovery, capabilities, registry |
| **paint/ (bake, color_detection, helpers)** | ✅ Good | +40 tests (`test_paint.py`) — quantize, HSV, binning, palette |
| **panels/ (metadata, triangle sets)** | ✅ Good | +24 tests (`test_panels.py`) — vendor detect, set CRUD, formatting |
| **slicer_profiles/** | ✅ Good | +24 tests (`test_slicer_profiles.py`) — CRUD, config decode, 3MF extract |
| **Prusa export** | ✅ Good | +12 tests (`test_prusa_export.py`) — ZIP, metadata, multi-material |
| **Import PAINT mode (UV rasterizer)** | ✅ Good | +17 tests (`test_import_segmentation.py`) — subdivide, render, dilate |
| **Thumbnail generation** | ✅ Good | +7 tests (`test_thumbnail_geometry.py`) — bbox helper; 12 pre-existing |
| **Component export** | ✅ Good | 4 pre-existing tests cover key scenarios |

Remaining gaps (operator-level UI code, not easily unit-testable):
- Paint panel operators (`MMU_OT_initialize`, `MMU_OT_select_filament`, etc.) — require full Blender UI context
- Slicer profile operators — UI-bound preset management
