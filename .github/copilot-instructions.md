# Copilot Instructions for Blender 3MF Format

## Project Overview

Blender addon (extension) for importing/exporting **3MF Core Spec v1.4.0** files with multi-material support for Orca Slicer, BambuStudio, PrusaSlicer, and SuperSlicer. Targets **Blender 4.2+** minimum; primary development on **Blender 5.0**.

- **Version:** 1.3.2
- **Extension ID:** `ThreeMF_io`
- **License:** GPL-3.0-or-later
- **Manifest:** `io_mesh_3mf/blender_manifest.toml`

---

## Architecture

```
io_mesh_3mf/
├── __init__.py                  # Addon registration, FileHandler, preferences, reload logic
├── utilities.py                 # DEBUG_MODE flag, debug()/warn()/error(), hex↔RGB helpers
├── constants.py                 # 3MF spec constants, all XML namespaces, file paths, MIME types
├── extensions.py                # ExtensionManager, Extension dataclass, extension registry
├── metadata.py                  # Metadata/MetadataEntry classes for scene/object metadata
├── annotations.py               # ContentType/Relationship classes (OPC packaging)
├── unit_conversions.py          # Unit scale conversions (mm, inch, m, ft, µm)
│
├── import_3mf.py                # Import3MF operator (~2980 lines) - main import orchestration
├── import_trianglesets.py       # Triangle Sets Extension import
├── import_hash_segmentation.py  # Render segmentation trees → UV textures (numpy)
├── import_materials/            # Materials Extension import (sub-package)
│   ├── __init__.py              # Re-exports all public functions
│   ├── base.py                  # basematerials, colorgroups, material creation/reuse
│   ├── textures.py              # texture2d / texture2dgroup parsing + extraction
│   ├── pbr.py                   # PBR display properties (metallic, specular, translucent)
│   └── passthrough.py           # Round-trip preservation (composites, multiproperties)
│
├── export_3mf.py                # Export3MF operator - UI, preferences, dispatches to exporters
├── export_formats.py            # StandardExporter, OrcaExporter, PrusaExporter classes
├── export_utils.py              # Archive management, geometry writing, thumbnail generation
├── export_components.py         # Linked-duplicate detection for component optimization
├── export_trianglesets.py       # Triangle Sets Extension export
├── export_hash_segmentation.py  # UV textures → segmentation hash strings (numpy)
├── export_materials/            # Materials Extension export (sub-package)
│   ├── __init__.py              # Re-exports all public functions
│   ├── base.py                  # Face color collection, basematerials/colorgroups writing
│   ├── textures.py              # Texture detection, archive writing, texture2d/group resources
│   ├── pbr.py                   # PBR property extraction and display property writing
│   └── passthrough.py           # Round-trip passthrough material writing (ID remapping)
│
├── hash_segmentation.py         # Core segmentation codec: SegmentationDecoder/Encoder, tree structures
├── paint_panel.py               # MMU Paint Suite panel (~1050 lines) - texture painting UI
└── orca_project_template.json   # Template JSON for Orca Slicer metadata export
```

### Key architectural patterns

- **Import/Export operators** inherit from `bpy.types.Operator` + `ImportHelper`/`ExportHelper`
- **3MF files** are ZIP archives containing XML model files + OPC structure
- **XML parsing** uses `xml.etree.ElementTree` exclusively (never lxml)
- **Export dispatch:** `Export3MF.execute()` → `StandardExporter` / `OrcaExporter` / `PrusaExporter`
- **Materials sub-packages** mirror each other: `import_materials/` and `export_materials/` with matching module names
- **Backward-compatible wrappers** on `Export3MF` delegate to refactored utility modules for unit test compatibility

---

## Coding Practices

### Logging — NO `logging` module

**Blender addons have no logging infrastructure.** Python's `logging` module does nothing in Blender because there are no handlers configured. **Never use `import logging` or `logging.getLogger()`.**

All logging goes through `utilities.py`:

```python
from .utilities import debug, warn, error

# Informational / progress messages — silent by default
debug(f"Loaded {count} objects")

# Warnings about malformed data — ALWAYS prints with "WARNING:" prefix  
warn(f"Missing vertex coordinate in triangle {idx}")

# Errors — ALWAYS prints with "ERROR:" prefix
error(f"Failed to write archive: {e}")
```

- `debug()` is gated by `DEBUG_MODE = False` in `utilities.py` — set to `True` during development only
- `warn()` and `error()` always print, so real problems are visible to users
- For sub-packages (`import_materials/`, `export_materials/`), use `from ..utilities import debug, warn, error`

### Color conversions — use `utilities.py` helpers

```python
from .utilities import hex_to_rgb, rgb_to_hex

r, g, b = hex_to_rgb("#CC3319")     # → (0.8, 0.2, 0.098...)
hex_str = rgb_to_hex(0.8, 0.2, 0.1)  # → "#CC3319"
```

**Exception:** `import_materials/base.py` has its own `parse_hex_color()` that handles RGBA + sRGB-to-linear conversion. That serves a different purpose and should NOT be replaced.

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

### Error reporting in operators

Use `safe_report()` for messages that should appear in Blender's status bar:

```python
self.safe_report({'ERROR'}, "No mesh objects selected")
self.safe_report({'WARNING'}, "Non-manifold geometry detected")
self.safe_report({'INFO'}, f"Exported {count} objects")
```

This method exists on both `Import3MF` and `Export3MF` and gracefully falls back when running in unit tests without a real Blender context.

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

1. **`hash_segmentation.py`** — Core codec: `SegmentationDecoder`, `SegmentationEncoder`, `SegmentationNode` tree, `TriangleSubdivider`. Hex strings encode recursive subdivision trees where each nibble = `xxyy` (state/split info).

2. **`import_hash_segmentation.py`** — Renders segmentation trees as colored UV textures: subdivide triangles in UV space → fill pixels with extruder colors → gap filling. Uses numpy vectorized ops.

3. **`export_hash_segmentation.py`** — Reverses the process: pre-compute state map from texture pixels (numpy) → sample at triangle corners/interior → recursively build segmentation tree → encode to hex string. Performance-critical.

---

## Extension System

### Adding namespace support

1. Add constant in `constants.py`: `NEW_NAMESPACE = "http://..."`
2. Add to `SUPPORTED_EXTENSIONS` set
3. Register in `extensions.py` with `Extension` dataclass
4. Add to `MODEL_NAMESPACES` dict for XML parsing

### Extension prefix resolution

`requiredextensions="p"` uses prefixes, not URIs. Use `resolve_extension_prefixes()`:

```python
known_prefix_mappings = {
    "p": PRODUCTION_NAMESPACE,
    "m": MATERIAL_NAMESPACE,
}
```

---

## Testing

Tests require **Blender's Python** (not system Python). Three runners:

```powershell
# All tests (unit + integration, spawns separate Blender processes)
python tests/run_all_tests.py

# Unit tests only (fast, mocked bpy — tests/unit/)
blender --background --python tests/run_unit_tests.py

# Integration tests only (real Blender objects — tests/integration/)
blender --background --python tests/run_tests.py

# Specific test module
blender --background --python tests/run_tests.py -- test_export
```

- **Unit tests** (`tests/unit/`) use mocked `bpy` from `tests/unit/mock/bpy.py`
- **Integration tests** (`tests/integration/`) create real Blender objects, import/export real `.3mf` files
- **Test resources** in `tests/resources/` and `tests/resources/3mf_consortium/`

---

## Build & Install

```powershell
cd io_mesh_3mf
blender --command extension build   # → ThreeMF_io-{version}.zip
```

Drag the resulting `.zip` into Blender → Preferences → Add-ons to install.

---

## Key Files Quick Reference

| File | Purpose |
|------|---------|
| `utilities.py` | `debug()`, `warn()`, `error()`, `hex_to_rgb()`, `rgb_to_hex()`, `DEBUG_MODE` |
| `constants.py` | All XML namespaces, file paths, MIME types, spec version |
| `extensions.py` | Extension registry, `ExtensionManager`, `Extension` dataclass |
| `export_formats.py` | `StandardExporter`, `OrcaExporter`, `PrusaExporter` |
| `hash_segmentation.py` | Core segmentation tree codec (decode/encode hex strings) |
| `paint_panel.py` | MMU Paint Suite sidebar panel |
| `orca_project_template.json` | Template JSON for Orca metadata export |

---

## Caveats & Gotchas

1. **No `logging` module** — use `utilities.debug/warn/error` exclusively
2. **No `print()` calls** — use `debug()` for dev output, `warn()`/`error()` for real issues
3. **Blender properties can't start with `_`** — use `3mf_` prefix for custom properties
4. **Cache strings before XML ops** — Blender may GC the C string behind `blender_object.name`
5. **numpy is available** in Blender's Python — used extensively for pixel operations
6. **Blender 5.0 broke brush APIs** — `image_paint.brush` is read-only; version-check before use
7. **Export backward-compat wrappers** — `Export3MF` has wrapper methods that delegate to utility modules; don't remove them (unit tests depend on them)
8. **Sub-package imports** — `import_materials/` and `export_materials/` use `..utilities` (double-dot) for relative imports
9. **`safe_report()`** — use on operators instead of bare `self.report()` so tests don't crash
10. **sRGB vs linear** — `import_materials/base.py` has `srgb_to_linear()` for material colors; `utilities.hex_to_rgb()` returns raw values (no gamma conversion)
