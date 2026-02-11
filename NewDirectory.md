## Plan: Full 2.0.0 Codebase Restructure

### TL;DR

Break the monolithic `import_3mf.py` (3157 lines, 54 methods) and `export_formats.py` (1891 lines) into domain-focused packages. Replace 23+ mutable instance variables sprinkled on operator `self` with explicit `ImportContext` / `ExportContext` dataclasses. Eliminate all 23 backward-compat wrappers (16 import + 7 export). Consolidate shared utilities into a `common/` package. Expose a clean `api.py` for other addons and CLI scripts. Rewrite all tests against the new structure.

---

### New Directory Structure

```
io_mesh_3mf/
├── __init__.py                    # Addon registration, FileHandler, preferences (simplified)
├── blender_manifest.toml
├── orca_project_template.json
│
├── common/                        # Shared across import & export
│   ├── __init__.py                # Re-exports key symbols
│   ├── types.py                   # All data classes (replacing namedtuples)
│   ├── constants.py               # XML namespaces, file paths, MIME types
│   ├── extensions.py              # ExtensionManager, Extension registry
│   ├── metadata.py                # Metadata / MetadataEntry
│   ├── annotations.py             # ContentType / Relationship / OPC packaging
│   ├── units.py                   # Unit conversion dicts + scale functions
│   ├── colors.py                  # hex↔RGB, sRGB↔linear conversions
│   ├── logging.py                 # debug(), warn(), error()
│   ├── xml.py                     # parse_transformation, resolve_extension_prefixes, is_supported
│   └── segmentation.py            # SegmentationDecoder / Encoder / TriangleSubdivider
│
├── import_3mf/                    # Import package
│   ├── __init__.py                # Re-exports Import3MF operator
│   ├── operator.py                # Import3MF: UI properties, draw, invoke, execute shell
│   ├── context.py                 # ImportContext dataclass ("the bag")
│   ├── archive.py                 # read_archive, read_content_types, assign_content_types, must_preserve
│   ├── geometry.py                # read_objects, read_vertices, read_triangles, read_components
│   ├── builder.py                 # build_items, build_object (decomposed into sub-functions)
│   ├── scene.py                   # Blender mesh creation, material assignment, UV setup, origin, grid layout
│   ├── segmentation.py            # render_segmentation_to_texture, subdivide_prusa_segmentation
│   ├── triangle_sets.py           # Triangle Sets Extension import
│   ├── slicer/
│   │   ├── __init__.py
│   │   ├── detection.py           # detect_vendor
│   │   ├── colors.py              # read_orca/prusa/blender filament colors (consolidated)
│   │   └── paint.py               # ORCA_PAINT_TO_INDEX, parse_paint_color, get_or_create_paint_material
│   └── materials/
│       ├── __init__.py
│       ├── base.py                # basematerials, colorgroups, parse_hex_color, srgb_to_linear
│       ├── textures.py            # texture2d / texture2dgroup parsing + extraction
│       ├── pbr.py                 # PBR display properties (metallic, specular, translucent)
│       └── passthrough.py         # composites, multiproperties, store_passthrough
│
├── export_3mf/                    # Export package
│   ├── __init__.py                # Re-exports Export3MF operator
│   ├── operator.py                # Export3MF: UI properties, draw, invoke, execute dispatch
│   ├── context.py                 # ExportContext dataclass
│   ├── archive.py                 # create_archive, must_preserve, write_core_properties
│   ├── geometry.py                # write_vertices, write_triangles, format_transformation, write_metadata
│   ├── standard.py                # StandardExporter
│   ├── orca.py                    # OrcaExporter
│   ├── prusa.py                   # PrusaExporter
│   ├── components.py              # detect_linked_duplicates, should_use_components
│   ├── thumbnail.py               # Viewport render → PNG thumbnail
│   ├── segmentation.py            # texture_to_segmentation (numpy pipeline)
│   ├── triangle_sets.py           # Triangle Sets Extension export
│   └── materials/
│       ├── __init__.py
│       ├── base.py                # ORCA_FILAMENT_CODES, face colors, basematerials/colorgroups
│       ├── textures.py            # Texture detection, archive writing, texture resources
│       ├── pbr.py                 # PBR property extraction + display property writing
│       └── passthrough.py         # Round-trip passthrough material writing
│
├── paint_panel.py                 # MMU Paint Suite (unchanged — already decoupled)
│
└── api.py                         # Public API: import_3mf(), export_3mf() entry points
```

---

### File Migration Map

| Current File | → New Location | Notes |
|---|---|---|
| `utilities.py` | `common/logging.py` + `common/colors.py` | Split by concern |
| `constants.py` | `common/constants.py` | Direct move |
| `extensions.py` | `common/extensions.py` | Direct move |
| `metadata.py` | `common/metadata.py` | Direct move |
| `annotations.py` | `common/annotations.py` | Direct move |
| `unit_conversions.py` | `common/units.py` | Direct move + add `unit_scale()` functions from both operators |
| `hash_segmentation.py` | `common/segmentation.py` | Direct move |
| `import_3mf.py` (3157 lines) | **Split across 10 files** in `import_3mf/` | The big one |
| `import_hash_segmentation.py` | `import_3mf/segmentation.py` | Direct move |
| `import_trianglesets.py` | `import_3mf/triangle_sets.py` | Direct move |
| `import_materials/*` | `import_3mf/materials/*` | Move under import package, change `op` → `ctx` |
| `export_3mf.py` | `export_3mf/operator.py` | Strip wrappers |
| `export_formats.py` (1891 lines) | `export_3mf/standard.py` + `orca.py` + `prusa.py` | One exporter per file |
| `export_utils.py` | `export_3mf/archive.py` + `geometry.py` + `thumbnail.py` | Split by concern, kill re-exports |
| `export_components.py` | `export_3mf/components.py` | Direct move |
| `export_hash_segmentation.py` | `export_3mf/segmentation.py` | Direct move |
| `export_trianglesets.py` | `export_3mf/triangle_sets.py` | Direct move |
| `export_materials/*` | `export_3mf/materials/*` | Move under export package |
| `paint_panel.py` | `paint_panel.py` | No change (already decoupled) |

---

### Steps

**Phase 1: Foundation — `common/` package**

1. Create common/types.py — Convert all 10 `namedtuple` definitions from import_3mf.py to `@dataclass` classes with proper defaults. Both import and export will import from here. Add `__eq__` and `__hash__` where needed for caching.

2. Create common/logging.py — Move `DEBUG_MODE`, `debug()`, `warn()`, `error()` from utilities.py. Add a `safe_report(operator, level, message)` standalone function (currently duplicated on both operators).

3. Create common/colors.py — Move `hex_to_rgb`, `hex_to_linear_rgb`, `rgb_to_hex`, `linear_rgb_to_hex`, `srgb_to_linear`, `linear_to_srgb` from utilities.py.

4. Move constants.py, extensions.py, metadata.py, annotations.py directly into `common/` with no logic changes.

5. Create common/units.py — Move dicts from unit_conversions.py and extract `unit_scale()` logic from both operators into free functions: `import_unit_scale(context, root)` and `export_unit_scale(context)`.

6. Create common/xml.py — Extract `parse_transformation()` (import_3mf.py), `resolve_extension_prefixes()` (import_3mf.py), `is_supported()` (import_3mf.py), `read_metadata()` (import_3mf.py). Also extract `format_transformation()` from export_utils.py. These are shared XML helpers used by both sides.

7. Move hash_segmentation.py → `common/segmentation.py` (direct move, no changes).

**Phase 2: Import Package — `import_3mf/`**

8. Create import_3mf/context.py — Define `ImportContext` dataclass bundling all 23+ instance variables currently on `Import3MF.self`. Include `options` sub-dataclass for operator properties (`import_materials`, `reuse_materials`, `origin_to_geometry`, `import_location`, `global_scale`, `grid_spacing`). The operator will create this in `execute()` and pass it everywhere.

9. Create import_3mf/operator.py — ~200 lines. Contains `Import3MF(bpy.types.Operator, ImportHelper)` with: `bl_idname`, `bl_label`, Blender properties, `draw()`, `invoke()`, `execute()`. The `execute()` method creates an `ImportContext`, delegates all work to functions, handles progress bars and camera zoom. **No wrappers. No business logic.** Zero of the 16 wrapper methods survive.

10. Create import_3mf/archive.py — Extract `read_archive()` (import_3mf.py), `read_content_types()` (import_3mf.py), `assign_content_types()` (import_3mf.py), `must_preserve()` (import_3mf.py), `load_external_model()` (import_3mf.py). All become free functions taking `ctx: ImportContext` where needed.

11. Create import_3mf/geometry.py — Extract `read_objects()` (import_3mf.py), `read_vertices()` (import_3mf.py), `read_components()` (import_3mf.py), `read_external_model_objects()` (import_3mf.py). **Merge** `read_triangles()` (import_3mf.py) and `read_triangles_with_paint_color()` (import_3mf.py) into a single unified function with a `paint_mode` parameter — eliminating ~80% code duplication. Also merge `_resolve_multiproperties_material()` (import_3mf.py) into this module since it's geometry-adjacent material resolution.

12. Create import_3mf/builder.py — Extract `build_items()` (import_3mf.py). **Decompose** the 494-line `build_object()` method (import_3mf.py) into clearly-named sub-functions that `build_object()` orchestrates.

13. Create import_3mf/scene.py — The sub-functions extracted from `build_object()`: `create_mesh_from_data()` (pydata → Blender mesh), `assign_materials_to_mesh()` (material creation + slot assignment loop), `apply_uv_coordinates()` (UV layer creation), `apply_triangle_sets()` (face attribute setup), `set_object_origin()` (BOTTOM/CENTER placement), `apply_import_location()` (ORIGIN/CURSOR/GRID/KEEP), `apply_grid_layout()` (the 105-line grid arranger from import_3mf.py).

14. Create import_3mf/slicer/detection.py — Move `detect_vendor()` (import_3mf.py).

15. Create import_3mf/slicer/colors.py — Consolidate all 5 filament color readers: `read_orca_filament_colors()` (import_3mf.py), `read_prusa_slic3r_colors()` (import_3mf.py), `read_blender_addon_colors()` (import_3mf.py), `read_prusa_object_extruders()` (import_3mf.py), `read_prusa_filament_colors()` (import_3mf.py). **Fix the anti-pattern** where each opens the ZIP independently — pass the already-opened archive.

16. Create import_3mf/slicer/paint.py — Move `ORCA_PAINT_TO_INDEX` dict (import_3mf.py), `parse_paint_color_to_index()` (import_3mf.py), `get_or_create_paint_material()` (import_3mf.py), `_subdivide_prusa_segmentation()` (import_3mf.py).

17. Move import_materials/ → `import_3mf/materials/`. Change every function signature from `op: "Import3MF"` to `ctx: ImportContext`. Remove the dead `op` parameter from `find_existing_material`, `setup_textured_material`, `setup_multi_textured_material`, and `apply_pbr_to_principled` (4 functions that currently accept `op` but never use it).

18. Move import_hash_segmentation.py → `import_3mf/segmentation.py`. Move import_trianglesets.py → `import_3mf/triangle_sets.py`. Change `op` → `ctx` for `safe_report` calls.

19. Delete `PRODUCTION_NAMESPACES` dict at import_3mf.py — it's defined but never used.

**Phase 3: Export Package — `export_3mf/`**

20. Create export_3mf/context.py — Define `ExportContext` dataclass bundling: `next_resource_id`, `material_resource_id`, `vertex_colors`, `material_name_to_index`, `orca_object_files`, `passthrough_id_remap`, `texture_groups`, `pbr_material_names`, `extension_manager`, plus operator options (`use_mesh_modifiers`, `coordinate_precision`, `export_hidden`, `mmu_slicer_format`, `material_export_mode`, `export_triangle_sets`, `use_components`, `global_scale`). Also holds the `archive` (zipfile) reference.

21. Create export_3mf/operator.py — `Export3MF` operator with properties, `draw()`, `invoke()`, `execute()`. No wrappers. Creates `ExportContext`, dispatches to Standard/Orca/Prusa exporter.

22. Split export_formats.py (1891 lines) into three files:
    - `export_3mf/standard.py` — `StandardExporter` (export_formats.py). **Decompose** the 352-line `write_object_resource()` into sub-functions.
    - `export_3mf/orca.py` — `OrcaExporter` (export_formats.py).
    - `export_3mf/prusa.py` — `PrusaExporter` (export_formats.py).
    
    All exporters take `ctx: ExportContext` instead of `self.op`. **Deduplicate**: extract shared segmentation extraction logic (currently copied 3x) into a helper in `export_3mf/segmentation.py`.

23. Split export_utils.py (637 lines):
    - `export_3mf/archive.py` — `create_archive()`, `must_preserve()`, `write_core_properties()` 
    - `export_3mf/geometry.py` — `write_vertices()`, `write_triangles()`, `write_metadata()` 
    - `export_3mf/thumbnail.py` — `write_thumbnail()` (the 105-line viewport render) 
    - `check_non_manifold_geometry()` → `export_3mf/geometry.py` 
    - **Delete all 18 re-exports** from export_utils.py

24. Move export_components.py → `export_3mf/components.py`, export_hash_segmentation.py → `export_3mf/segmentation.py`, export_trianglesets.py → `export_3mf/triangle_sets.py`.

25. Move export_materials/ → `export_3mf/materials/`. Remove the `base.py` ↔ `pbr.py` lazy import workaround (restructured imports should resolve the circular dependency).

26. Extract `_write_passthrough_triangles()` free function ([export_formats.py L74–198](io_mesh_3mf/export_formats.py#L74-L198)) → `export_3mf/geometry.py` (it's shared geometry writing logic used by standard exporter).

**Phase 4: `__init__.py` and Registration**

27. Rewrite \_\_init\_\_.py to import from new package paths:
    - `from .import_3mf import Import3MF`
    - `from .export_3mf import Export3MF`
    - `from .paint_panel import register as register_paint_panel, unregister as unregister_paint_panel`
    - Update the reload logic to reload `common/`, `import_3mf/`, `export_3mf/` sub-packages (currently only 3 modules are reloaded — sub-packages are missed).

**Phase 5: Public API**

28. Create api.py — Clean entry points for other addons and CLI scripts:
    - `import_3mf(filepath, **options) → ImportResult` — Creates `ImportContext` from options, runs the full import pipeline, returns a result dataclass with imported objects and status. Callable from headless Blender without going through operator invocation.
    - `export_3mf(filepath, objects=None, **options) → ExportResult` — Same pattern for export.
    - These call the same functions as the operators but skip operator-specific UI (progress bars, camera zoom, popups).

**Phase 6: Tests**

29. Delete all existing unit tests in unit and rewrite from scratch:
    - `test_common.py` — Test `types.py` dataclasses, `colors.py` conversions, `xml.py` parsing, `units.py` scale calculations, `metadata.py`, `segmentation.py` codec.
    - `test_import_archive.py` — Test `import_3mf.archive` functions with crafted ZIP files.
    - `test_import_geometry.py` — Test `read_vertices`, `read_triangles`, `read_objects` with XML snippets.
    - `test_import_materials.py` — Test material parsing functions with `ImportContext`.
    - `test_import_slicer.py` — Test filament color reading, vendor detection, paint code parsing.
    - `test_export_geometry.py` — Test `write_vertices`, `write_triangles`, `format_transformation`.
    - `test_export_materials.py` — Test `collect_face_colors`, `write_materials` with `ExportContext`.
    - `test_export_archive.py` — Test archive creation, core properties.
    - `test_api.py` — Test the public API functions.

30. Update integration tests: Fix import paths from `io_mesh_3mf.import_3mf` (module) → `io_mesh_3mf.import_3mf` (package). Integration tests mostly use `bpy.ops` so they should need minimal changes — just update test_base.py if it references internal symbols. Fix test_components.py which doesn't use `Blender3mfTestCase` to align with the pattern.

31. Update mock/bpy.py — Simplify. Unit tests should test functions that take `ImportContext`/`ExportContext` instead of instantiating operator classes. The mock needs less surface area.

**Phase 7: Cleanup**

32. Delete old top-level files: `utilities.py`, `constants.py`, `extensions.py`, `metadata.py`, `annotations.py`, `unit_conversions.py`, `hash_segmentation.py`, `import_3mf.py`, `import_hash_segmentation.py`, `import_trianglesets.py`, `export_3mf.py`, `export_formats.py`, `export_utils.py`, `export_components.py`, `export_hash_segmentation.py`, `export_trianglesets.py`, and the old `import_materials/` and `export_materials/` directories.

33. Update blender_manifest.toml version to `2.0.0`.

34. Update copilot-instructions.md to reflect new architecture.

---

### Key Design Decisions

- **`ImportContext` / `ExportContext` dataclasses** replace all mutable `self.*` state on operators. Every function takes `ctx` as its first argument. This makes functions independently testable and eliminates the operator-as-god-object antipattern.
- **Merge `read_triangles` + `read_triangles_with_paint_color`** into one function — currently ~80% identical code duplicated across import_3mf.py and import_3mf.py.
- **Decompose `build_object()` (494 lines, 12 concerns)** into 7+ focused sub-functions in `scene.py` orchestrated by a slim `build_object()` in `builder.py`.
- **Deduplicate segmentation extraction** — currently copied into `StandardExporter.write_object_resource()`, `OrcaExporter.write_object_model()`, and color collection in `OrcaExporter.execute()` / `PrusaExporter.execute()`.
- **Fix the re-open-archive-per-function anti-pattern** — 5 slicer color readers each independently open the ZIP file. Pass the already-opened archive instead.
- **No backward-compat wrappers** — all 23 are deleted. Tests rewritten for new API.
- **`paint_panel.py` stays unchanged** — it's already fully decoupled (only imports from `utilities`; communicates via mesh custom properties).
- **`common/` naming** instead of `core/` or `shared/` — it's the Blender addon convention and avoids confusion with 3MF Core Spec terminology.

---

### Verification

1. **Build test**: `cd io_mesh_3mf && blender --command extension build` succeeds
2. **Unit tests**: `blender --background --python tests/run_unit_tests.py` — all new unit tests pass
3. **Integration tests**: `blender --background --python tests/run_tests.py` — all round-trip, material, component, unicode, and consortium sample tests pass
4. **Operator registration**: `bpy.ops.import_mesh.threemf` and `bpy.ops.export_mesh.threemf` exist and work
5. **API test**: Headless script `import io_mesh_3mf.api; io_mesh_3mf.api.import_3mf("test.3mf")` succeeds
6. **Reload test**: Disable/re-enable addon in Blender Preferences → no errors
7. **Manual smoke test**: Import a multi-material Orca .3mf, re-export as Prusa, reimport — visual parity

---

### Risk Areas

- **`build_object()` decomposition** is the highest-risk change — 494 lines with deeply interleaved state. Needs careful step-by-step extraction with integration tests validating after each sub-function is pulled out.
- **Reload logic** in `__init__.py` must be updated to handle sub-packages or reloads will silently use stale code.
- **Exporter state sharing** — the three exporters currently mutate `self.op.*` freely. The `ExportContext` needs careful design so `StandardExporter`, `OrcaExporter`, and `PrusaExporter` can all work with it (especially since `PrusaExporter` reuses `StandardExporter.write_objects()`).