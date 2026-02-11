# Copilot Instructions for Blender 3MF Format

## Project Overview

Blender addon (extension) for importing/exporting **3MF Core Spec v1.4.0** files with multi-material support for Orca Slicer, BambuStudio, PrusaSlicer, and SuperSlicer. Targets **Blender 4.2+** minimum; primary development on **Blender 5.0**.

- **Version:** 2.0.0
- **Extension ID:** `ThreeMF_io`
- **License:** GPL-3.0-or-later
- **Manifest:** `io_mesh_3mf/blender_manifest.toml`

---

## Architecture

```
io_mesh_3mf/
├── __init__.py                    # Addon registration, FileHandler, preferences, reload logic
├── api.py                         # Public API: import_3mf(), export_3mf(), inspect_3mf(), batch ops
├── paint_panel.py                 # MMU Paint Suite panel (~1050 lines) - texture painting UI
├── orca_project_template.json     # Template JSON for Orca Slicer metadata export
│
├── common/                        # Shared across import & export
│   ├── __init__.py                # Re-exports key symbols (debug, warn, error, hex_to_rgb, etc.)
│   ├── types.py                   # All dataclasses (ResourceObject, ResourceMaterial, etc.)
│   ├── constants.py               # XML namespaces, file paths, MIME types, spec version
│   ├── extensions.py              # ExtensionManager, Extension registry
│   ├── metadata.py                # Metadata / MetadataEntry classes
│   ├── annotations.py             # Annotations class, ContentType / Relationship namedtuples (OPC packaging)
│   ├── units.py                   # Unit conversion dicts + scale functions
│   ├── colors.py                  # hex↔RGB, sRGB↔linear conversions
│   ├── logging.py                 # DEBUG_MODE, debug(), warn(), error(), safe_report()
│   ├── xml.py                     # parse_transformation, format_transformation, resolve_extension_prefixes
│   └── segmentation.py            # SegmentationDecoder / Encoder / TriangleSubdivider
│
├── import_3mf/                    # Import package
│   ├── __init__.py                # Re-exports Import3MF operator
│   ├── operator.py                # Import3MF: UI properties, draw, invoke, execute shell
│   ├── context.py                 # ImportContext / ImportOptions dataclasses
│   ├── archive.py                 # read_archive, read_content_types, assign_content_types, must_preserve, load_external_model
│   ├── geometry.py                # read_objects, read_vertices, read_triangles, read_components
│   ├── builder.py                 # build_items, build_object orchestration
│   ├── scene.py                   # Mesh creation, material assignment, UV setup, origin, grid layout
│   ├── segmentation.py            # Hash segmentation → UV texture rendering (numpy)
│   ├── triangle_sets.py           # Triangle Sets Extension import
│   ├── slicer/                    # Slicer-specific detection and data
│   │   ├── __init__.py
│   │   ├── detection.py           # detect_vendor
│   │   ├── colors.py              # read_orca/prusa/blender/prusa_slic3r filament colors, read_prusa_object_extruders
│   │   └── paint.py               # ORCA_PAINT_TO_INDEX, parse_paint_color_to_index, get_or_create_paint_material, subdivide_prusa_segmentation
│   └── materials/                 # Materials Extension import
│       ├── __init__.py
│       ├── base.py                # basematerials, colorgroups, parse_hex_color, srgb_to_linear
│       ├── textures.py            # texture2d / texture2dgroup parsing + extraction
│       ├── pbr.py                 # PBR display properties (metallic, specular, translucent)
│       └── passthrough.py         # composites, multiproperties, store_passthrough
│
└── export_3mf/                    # Export package
    ├── __init__.py                # Re-exports Export3MF operator
    ├── operator.py                # Export3MF: UI properties, draw, invoke, execute dispatch
    ├── context.py                 # ExportContext / ExportOptions dataclasses
    ├── archive.py                 # create_archive, must_preserve, write_core_properties
    ├── geometry.py                # write_vertices, write_triangles, write_passthrough_triangles, write_metadata, check_non_manifold_geometry
    ├── standard.py                # BaseExporter (shared base class), StandardExporter
    ├── orca.py                    # OrcaExporter
    ├── prusa.py                   # PrusaExporter
    ├── components.py              # detect_linked_duplicates, should_use_components
    ├── thumbnail.py               # Viewport render → PNG thumbnail
    ├── segmentation.py            # UV textures → segmentation hash strings (numpy)
    ├── triangle_sets.py           # Triangle Sets Extension export
    └── materials/                 # Materials Extension export
        ├── __init__.py
        ├── base.py                # ORCA_FILAMENT_CODES, face colors, basematerials/colorgroups
        ├── textures.py            # Texture detection, archive writing, texture resources
        ├── pbr.py                 # PBR property extraction + display property writing
        └── passthrough.py         # Round-trip passthrough material writing (ID remapping)
```

### Key architectural patterns

- **Context dataclasses** — `ImportContext` and `ExportContext` replace mutable `self.*` state on operators. Every helper takes `ctx` as its first argument.
- **Import/Export operators** inherit from `bpy.types.Operator` + `ImportHelper`/`ExportHelper`. They are thin shells that create a context and delegate work to submodules.
- **3MF files** are ZIP archives containing XML model files + OPC structure
- **XML parsing** uses `xml.etree.ElementTree` exclusively (never lxml)
- **Export dispatch:** `Export3MF.execute()` → `StandardExporter` / `OrcaExporter` / `PrusaExporter` (all inherit from `BaseExporter` in `standard.py`)
- **Materials sub-packages** mirror each other: `import_3mf/materials/` and `export_3mf/materials/` with matching module names
- **Public API** (`api.py`) provides `import_3mf()`, `export_3mf()`, `inspect_3mf()`, `batch_import()`, `batch_export()` for headless/programmatic use without `bpy.ops`

---

## Coding Practices

### Logging — NO `logging` module

**Blender addons have no logging infrastructure.** Python's `logging` module does nothing in Blender because there are no handlers configured. **Never use `import logging` or `logging.getLogger()`.**

All logging goes through `common/logging.py`:

```python
from ..common import debug, warn, error
# or
from ..common.logging import debug, warn, error

# Informational / progress messages — silent by default
debug(f"Loaded {count} objects")

# Warnings about malformed data — ALWAYS prints with "WARNING:" prefix
warn(f"Missing vertex coordinate in triangle {idx}")

# Errors — ALWAYS prints with "ERROR:" prefix
error(f"Failed to write archive: {e}")
```

- `debug()` is gated by `DEBUG_MODE = False` in `common/logging.py` — set to `True` during development only
- `warn()` and `error()` always print, so real problems are visible to users

### Color conversions — use `common/colors.py` helpers

```python
from ..common.colors import hex_to_rgb, rgb_to_hex

r, g, b = hex_to_rgb("#CC3319")     # → (0.8, 0.2, 0.098...)
hex_str = rgb_to_hex(0.8, 0.2, 0.1)  # → "#CC3319"
```

**Exception:** `import_3mf/materials/base.py` has its own `parse_hex_color()` that handles RGBA + sRGB-to-linear conversion. That serves a different purpose and should NOT be replaced.

### Unicode safety

Always cache Blender strings to a local variable before passing them to XML/ElementTree operations. Python can garbage-collect the underlying C string otherwise:

```python
object_name = str(blender_object.name)  # Cache before use in XML
```

### Blender property naming

Blender custom properties **cannot start with an underscore**. Use `3mf_` prefix instead.

### Blender 5.0 API differences

Check version before using changed APIs:

```python
if bpy.app.version >= (5, 0, 0):
    # Blender 5.0: image_paint.brush is read-only, use paint_settings API
    # unified_paint_settings accessed via ts.image_paint.unified_paint_settings
else:
    # Blender 4.x: direct brush assignment works
```

### Error reporting in operators and contexts

Use `safe_report()` for messages that should appear in Blender's status bar:

```python
# On ImportContext / ExportContext:
ctx.safe_report({'ERROR'}, "No mesh objects selected")
ctx.safe_report({'WARNING'}, "Non-manifold geometry detected")
ctx.safe_report({'INFO'}, f"Exported {count} objects")

# Standalone function (from common/logging.py):
from ..common.logging import safe_report
safe_report(operator, {'WARNING'}, "Some message")
```

`safe_report()` gracefully falls back when running without a real Blender operator (e.g., API calls or tests).

---

## Custom Mesh Properties

These are stored on `mesh.data` (the Mesh datablock, not the Object):

| Property | Type | Description |
|----------|------|-------------|
| `3mf_is_paint_texture` | `bool` | Mesh has an MMU paint texture |
| `3mf_paint_extruder_colors` | `str` | Stringified dict of `{extruder_index: "#RRGGBB"}` |
| `3mf_paint_default_extruder` | `int` | Default extruder (1-based) for unpainted regions |
| `3mf_triangle_set` | int attribute | Per-face set index (0 = no set) |
| `3mf_triangle_set_names` | `list` | Ordered list of triangle set names |

---

## Export Modes

### Standard Export (`StandardExporter`)

Spec-compliant single `3D/3dmodel.model` file. Three material modes:

- **STANDARD** — geometry only, no materials
- **BASEMATERIAL** — one solid color per material slot via `<basematerials>`
- **PAINT** — UV-painted regions exported as hash segmentation strings

### Orca Export (`OrcaExporter`)

Production Extension multi-file structure for Orca Slicer / BambuStudio:

- Individual objects in `3D/Objects/*.model` with `paint_color` attributes for per-triangle colors
- Main model with `p:path` component references
- `Metadata/project_settings.config` JSON with filament colors
- Filament color mapping via `blender_filament_colors.xml` fallback metadata

### Prusa Export (`PrusaExporter`)

PrusaSlicer-compatible format:

- Single model file with `slic3rpe:mmu_segmentation` attributes for hash segmentation
- `Slic3r_PE.config` with printer/filament settings

### Paint color encoding (Orca format)

```python
# export: filament index → paint code
ORCA_FILAMENT_CODES = ["", "4", "8", "0C", "1C", ...]  # index 0=none, 1="4", 2="8"

# import: paint code → filament index (1-based)
ORCA_PAINT_TO_INDEX = {"": 0, "4": 1, "8": 2, "0C": 3, ...}
```

---

## MMU Paint Suite (`paint_panel.py`)

Sidebar panel (`VIEW3D_PT_mmu_paint`) for multi-filament texture painting. Two UI states:

1. **Init Setup** — editable filament list, color pickers, "Initialize Painting" button
2. **Active Painting** — read-only swatch palette, click to switch brush color, add/remove/reassign filaments

Key classes:
- **PropertyGroups:** `MMUFilamentItem` (display), `MMUInitFilamentItem` (editable), `MMUPaintSettings` (scene-level)
- **UILists:** `MMU_UL_init_filaments`, `MMU_UL_filaments`
- **Operators:** `MMU_OT_initialize`, `MMU_OT_select_filament`, `MMU_OT_reassign_filament_color`, `MMU_OT_switch_to_paint`, `MMU_OT_import_paint_popup`, etc.

Uses `numpy` for bulk pixel operations (color reassignment, texture scanning).

---

## Hash Segmentation System

Three-module pipeline for slicer-agnostic multi-material data:

1. **`common/segmentation.py`** — Core codec: `SegmentationDecoder`, `SegmentationEncoder`, `SegmentationNode` tree, `TriangleSubdivider`. Hex strings encode recursive subdivision trees where each nibble = `xxyy` (state/split info).

2. **`import_3mf/segmentation.py`** — Renders segmentation trees as colored UV textures: subdivide triangles in UV space → fill pixels with extruder colors → gap filling. Uses numpy vectorized ops.

3. **`export_3mf/segmentation.py`** — Reverses the process: pre-compute state map from texture pixels (numpy) → sample at triangle corners/interior → recursively build segmentation tree → encode to hex string. Performance-critical.

---

## Extension System

### Adding namespace support

1. Add constant in `common/constants.py`: `NEW_NAMESPACE = "http://..."`
2. Add to `SUPPORTED_EXTENSIONS` set
3. Register in `common/extensions.py` with `Extension` dataclass
4. Add to `MODEL_NAMESPACES` dict for XML parsing

### Extension prefix resolution

`requiredextensions="p"` uses prefixes, not URIs. Use `resolve_extension_prefixes()` from `common/xml.py`:

```python
known_prefix_mappings = {
    "p": PRODUCTION_NAMESPACE,
    "m": MATERIAL_NAMESPACE,
}
```

---

## Public API (`api.py`)

For headless/programmatic use without `bpy.ops`:

```python
from io_mesh_3mf.api import import_3mf, export_3mf, inspect_3mf

# Inspect without creating Blender objects
info = inspect_3mf("model.3mf")
print(info.unit, info.num_objects, info.num_triangles_total)

# Import
result = import_3mf("model.3mf", import_materials="PAINT")
print(result.status, result.num_loaded, result.objects)

# Export
result = export_3mf("output.3mf", use_orca_format="BASEMATERIAL")
print(result.status, result.num_written)

# Batch operations
from io_mesh_3mf.api import batch_import, batch_export
results = batch_import(["a.3mf", "b.3mf"])

# Building blocks for custom workflows
from io_mesh_3mf.api import colors, types, segmentation, units
```

---

## Testing

Tests require **Blender's Python** (not system Python). **No mocking** — all tests run inside real Blender headless mode. Three runners:

```powershell
# All tests (unit + integration, spawns separate Blender processes)
python tests/run_all_tests.py

# Unit tests only (real Blender Python, no mocks — tests/unit/)
blender --background --factory-startup --python-exit-code 1 -noaudio -q --python tests/run_unit_tests.py

# Integration tests only (real Blender objects — tests/integration/)
blender --background --factory-startup --python-exit-code 1 -noaudio -q --python tests/run_tests.py
```

- **Unit tests** (`tests/unit/`) test individual functions with real Blender Python (colors, types, constants, segmentation, xml, units, metadata)
- **Integration tests** (`tests/integration/`) create real Blender objects, import/export real `.3mf` files
- **Test resources** in `tests/resources/` and `tests/resources/3mf_consortium/`
- **Blender CLI flags:** `--factory-startup` (deterministic), `--python-exit-code 1` (CI-friendly), `-noaudio` (faster), `-q` (quiet)

---

## Build & Install

```powershell
cd io_mesh_3mf
blender --command extension build   # → ThreeMF_io-2.0.0.zip
```

Drag the resulting `.zip` into Blender → Preferences → Add-ons to install.

---

## Key Files Quick Reference

| File | Purpose |
|------|---------|
| `common/logging.py` | `debug()`, `warn()`, `error()`, `safe_report()`, `DEBUG_MODE` |
| `common/colors.py` | `hex_to_rgb()`, `rgb_to_hex()`, `srgb_to_linear()`, `linear_to_srgb()` |
| `common/constants.py` | All XML namespaces, file paths, MIME types, spec version |
| `common/types.py` | All dataclasses (ResourceObject, ResourceMaterial, etc.) |
| `common/extensions.py` | Extension registry, `ExtensionManager`, `Extension` dataclass |
| `common/segmentation.py` | Core segmentation tree codec (decode/encode hex strings) |
| `import_3mf/context.py` | `ImportContext` / `ImportOptions` dataclasses |
| `export_3mf/context.py` | `ExportContext` / `ExportOptions` dataclasses |
| `export_3mf/standard.py` | `StandardExporter` |
| `export_3mf/orca.py` | `OrcaExporter` |
| `export_3mf/prusa.py` | `PrusaExporter` |
| `api.py` | Public API: `import_3mf()`, `export_3mf()`, `inspect_3mf()` |
| `paint_panel.py` | MMU Paint Suite sidebar panel |
| `orca_project_template.json` | Template JSON for Orca metadata export |

---

## Caveats & Gotchas

1. **No `logging` module** — use `common.logging` `debug`/`warn`/`error` exclusively
2. **No `print()` calls** — use `debug()` for dev output, `warn()`/`error()` for real issues
3. **Blender properties can't start with `_`** — use `3mf_` prefix for custom properties
4. **Cache strings before XML ops** — Blender may GC the C string behind `blender_object.name`
5. **numpy is available** in Blender's Python — used extensively for pixel operations
6. **Blender 5.0 broke brush APIs** — `image_paint.brush` is read-only; version-check before use
7. **Context dataclasses** — `ImportContext` / `ExportContext` are the state bags. Operators create them in `execute()` and pass to all helpers.
8. **Sub-package imports** — use `from ..common import ...` for common utilities
9. **`safe_report()`** — use on contexts (`ctx.safe_report()`) or standalone from `common.logging` — never bare `self.report()` so tests don't crash
10. **sRGB vs linear** — `import_3mf/materials/base.py` has `srgb_to_linear()` for material colors; `common/colors.py` `hex_to_rgb()` returns raw values (no gamma conversion)
