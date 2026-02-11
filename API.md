# Public API Reference

> Programmatic 3MF import, export, and inspection without `bpy.ops`

The public API in `io_mesh_3mf.api` provides headless/programmatic access to the full 3MF pipeline. It runs the same code as the Blender operators but skips UI-specific behaviour (progress bars, popups, camera zoom), making it suitable for:

- **CLI automation** — batch processing from Blender's `--python` mode
- **Addon integration** — other Blender addons importing/exporting 3MF
- **Headless pipelines** — render farms, CI/CD, asset processing
- **Custom workflows** — building on top of the low-level building blocks

---

## Quick Start

```python
from io_mesh_3mf.api import import_3mf, export_3mf, inspect_3mf

# Import a 3MF file
result = import_3mf("/path/to/model.3mf")
print(result.status, result.num_loaded)

# Export selected objects
result = export_3mf("/path/to/output.3mf", use_selection=True)
print(result.status, result.num_written)

# Inspect without importing (no Blender objects created)
info = inspect_3mf("/path/to/model.3mf")
print(info.unit, info.num_objects, info.num_triangles_total)
```

---

## Core Functions

### `import_3mf(filepath, **kwargs) → ImportResult`

Import a 3MF file into the current Blender scene.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | `str` | required | Path to the `.3mf` file |
| `global_scale` | `float` | `1.0` | Scale multiplier |
| `import_materials` | `str` | `"MATERIALS"` | `"MATERIALS"`, `"PAINT"`, or `"NONE"` |
| `reuse_materials` | `bool` | `True` | Reuse existing Blender materials by name/color |
| `import_location` | `str` | `"KEEP"` | `"ORIGIN"`, `"CURSOR"`, `"KEEP"`, or `"GRID"` |
| `origin_to_geometry` | `str` | `"KEEP"` | `"KEEP"`, `"CENTER"`, or `"BOTTOM"` |
| `grid_spacing` | `float` | `0.1` | Spacing between objects in grid layout mode |
| `target_collection` | `str \| None` | `None` | Name of collection to place objects in (created if missing) |
| `on_progress` | `callable` | `None` | `(percentage: int, message: str)` callback |
| `on_warning` | `callable` | `None` | `(message: str)` callback for warnings |
| `on_object_created` | `callable` | `None` | `(blender_object, resource_id)` callback |

**Returns:** `ImportResult` dataclass

```python
@dataclass
class ImportResult:
    status: str          # "FINISHED" or "CANCELLED"
    num_loaded: int      # Number of objects imported
    objects: list        # List of bpy.types.Object instances
    warnings: list[str]  # Any warning messages
```

**Example — Import with material painting and progress tracking:**

```python
from io_mesh_3mf.api import import_3mf

def on_progress(pct, msg):
    print(f"[{pct}%] {msg}")

result = import_3mf(
    "/models/multicolor.3mf",
    import_materials="PAINT",
    import_location="ORIGIN",
    on_progress=on_progress,
)

for obj in result.objects:
    print(f"  {obj.name}: {len(obj.data.vertices)} verts")
```

**Example — Import into a specific collection:**

```python
result = import_3mf(
    "/models/part.3mf",
    target_collection="Imported Parts",
    reuse_materials=True,
)
```

---

### `export_3mf(filepath, **kwargs) → ExportResult`

Export Blender objects to a 3MF file.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filepath` | `str` | required | Destination `.3mf` path |
| `objects` | `list \| None` | `None` | Explicit list of objects (overrides `use_selection`) |
| `use_selection` | `bool` | `False` | Export selected objects only |
| `export_hidden` | `bool` | `False` | Include hidden objects |
| `global_scale` | `float` | `1.0` | Scale multiplier |
| `use_mesh_modifiers` | `bool` | `True` | Apply modifiers before export |
| `coordinate_precision` | `int` | `9` | Decimal precision for vertex coordinates |
| `use_orca_format` | `str` | `"BASEMATERIAL"` | `"STANDARD"`, `"BASEMATERIAL"`, or `"PAINT"` |
| `export_triangle_sets` | `bool` | `False` | Export face maps as triangle sets |
| `use_components` | `bool` | `True` | Use component instances for linked duplicates |
| `mmu_slicer_format` | `str` | `"ORCA"` | `"ORCA"` or `"PRUSA"` (for `PAINT` mode) |
| `on_progress` | `callable` | `None` | `(percentage: int, message: str)` callback |
| `on_warning` | `callable` | `None` | `(message: str)` callback for warnings |

**Returns:** `ExportResult` dataclass

```python
@dataclass
class ExportResult:
    status: str          # "FINISHED" or "CANCELLED"
    num_written: int     # Number of objects written
    filepath: str        # Absolute path of written file
    warnings: list[str]  # Any warning messages
```

**Example — Export specific objects with Orca format:**

```python
from io_mesh_3mf.api import export_3mf
import bpy

cubes = [o for o in bpy.data.objects if o.type == "MESH" and "Cube" in o.name]

result = export_3mf(
    "/output/cubes.3mf",
    objects=cubes,
    use_orca_format="BASEMATERIAL",
    mmu_slicer_format="ORCA",
)
print(f"Exported {result.num_written} objects")
```

**Example — Export for PrusaSlicer with MMU paint:**

```python
result = export_3mf(
    "/output/painted.3mf",
    use_orca_format="PAINT",
    mmu_slicer_format="PRUSA",
    use_selection=True,
)
```

---

### `inspect_3mf(filepath) → InspectResult`

Inspect a 3MF file without creating any Blender objects. Useful for previewing, validation, or building file selection UIs.

**Returns:** `InspectResult` dataclass

```python
@dataclass
class InspectResult:
    status: str                    # "OK" or "ERROR"
    error_message: str             # Human-readable error (when status == "ERROR")
    unit: str                      # "millimeter", "centimeter", "meter", "inch", etc.
    metadata: dict[str, str]       # Top-level <metadata> key/value pairs
    objects: list[dict]            # Per-object summaries (see below)
    materials: list[dict]          # Material group summaries
    textures: list[dict]           # Texture resource summaries
    extensions_used: set[str]      # Namespace URIs of referenced extensions
    vendor_format: str | None      # "orca", "prusa", or None
    archive_files: list[str]       # All file paths inside the ZIP
    num_objects: int               # Total object count
    num_triangles_total: int       # Sum of all triangle counts
    num_vertices_total: int        # Sum of all vertex counts
    warnings: list[str]            # Warnings during inspection
```

Each object in `objects` is a dict with:

```python
{
    "id": "1",                  # Resource ID
    "name": "Cube",             # Object name
    "type": "model",            # "model", "solidsupport", "support", "surface"
    "num_vertices": 8,
    "num_triangles": 12,
    "num_components": 0,
    "has_materials": True,
    "has_segmentation": False,  # MMU paint data
}
```

**Example — File preview / validation:**

```python
from io_mesh_3mf.api import inspect_3mf

info = inspect_3mf("/models/assembly.3mf")

if info.status == "OK":
    print(f"Unit: {info.unit}")
    print(f"Objects: {info.num_objects}")
    print(f"Total triangles: {info.num_triangles_total}")
    print(f"Vendor: {info.vendor_format or 'standard'}")
    print(f"Extensions: {info.extensions_used}")

    for obj in info.objects:
        flags = []
        if obj["has_materials"]:
            flags.append("materials")
        if obj["has_segmentation"]:
            flags.append("MMU paint")
        print(f"  {obj['name']}: {obj['num_triangles']} tris [{', '.join(flags)}]")

    for mat in info.materials:
        print(f"  Material group {mat['id']}: {mat['type']} ({mat['count']} entries)")
else:
    print(f"Error: {info.error_message}")
```

---

## Batch Operations

### `batch_import(filepaths, **kwargs) → list[ImportResult]`

Import multiple 3MF files with per-file error isolation. A failure in one file does not prevent others from importing.

```python
from io_mesh_3mf.api import batch_import

results = batch_import(
    ["part_a.3mf", "part_b.3mf", "part_c.3mf"],
    import_materials="PAINT",
    target_collection="Batch Import",
)

total = sum(r.num_loaded for r in results)
failed = [r for r in results if r.status != "FINISHED"]
print(f"Imported {total} objects, {len(failed)} failures")
```

### `batch_export(items, **kwargs) → list[ExportResult]`

Export multiple 3MF files. Each item is a `(filepath, objects_or_None)` tuple.

```python
from io_mesh_3mf.api import batch_export
import bpy

cubes = [o for o in bpy.data.objects if "Cube" in o.name]
spheres = [o for o in bpy.data.objects if "Sphere" in o.name]

results = batch_export(
    [
        ("cubes.3mf", cubes),
        ("spheres.3mf", spheres),
        ("everything.3mf", None),  # None = all scene objects
    ],
    use_orca_format="BASEMATERIAL",
)
```

---

## Building Blocks

The API re-exports common building blocks for custom workflows. These are the same modules used internally by the import/export pipeline.

```python
from io_mesh_3mf.api import colors, types, segmentation, units, extensions, xml_tools, metadata, components
```

### `colors` — Color Conversions

```python
from io_mesh_3mf.api import colors

r, g, b = colors.hex_to_rgb("#CC3319")      # → (0.8, 0.2, 0.098...)
hex_str = colors.rgb_to_hex(0.8, 0.2, 0.1)  # → "#CC3319"

# sRGB ↔ linear conversions for Blender material colors
linear = colors.srgb_to_linear(0.5)
srgb = colors.linear_to_srgb(0.214)
```

### `types` — Data Types

All dataclasses used throughout the pipeline:

```python
from io_mesh_3mf.api import types

# ResourceObject, ResourceMaterial, ResourceTexture,
# ResourceTextureGroup, Component, etc.
```

### `segmentation` — Hash Segmentation Codec

Encode/decode the hash-based segmentation strings used by slicers for per-triangle subdivision trees:

```python
from io_mesh_3mf.api import segmentation

# Decode a segmentation string
decoder = segmentation.SegmentationDecoder()
tree = decoder.decode("A3F0")

# Encode back
encoder = segmentation.SegmentationEncoder()
hex_string = encoder.encode(tree)
```

### `units` — Unit Conversion

```python
from io_mesh_3mf.api import units

# Conversion dictionaries
units.blender_to_metre["METERS"]       # → 1.0
units.threemf_to_metre["millimeter"]   # → 0.001
```

### `extensions` — Extension Registry

```python
from io_mesh_3mf.api import extensions

manager = extensions.ExtensionManager()
manager.activate(extensions.MATERIALS_EXTENSION.namespace)
```

### `metadata` — Metadata Handling

```python
from io_mesh_3mf.api import metadata

meta = metadata.Metadata()
meta["Title"] = metadata.MetadataEntry(name="Title", value="My Model")
```

### `components` — Component Detection

```python
from io_mesh_3mf.api import components

groups = components.detect_linked_duplicates(bpy.context.scene.objects)
```

---

## Callbacks

All three callback types are optional and work the same way across `import_3mf`, `export_3mf`, and batch operations.

### Progress Callback

```python
def on_progress(percentage: int, message: str):
    """Called with 0-100 percentage and a status message."""
    print(f"[{percentage:3d}%] {message}")
```

### Warning Callback

```python
def on_warning(message: str):
    """Called for each warning (non-manifold geometry, missing data, etc.)."""
    logging.warning(message)
```

### Object Created Callback (import only)

```python
def on_object_created(blender_object, resource_id: str):
    """Called after each Blender object is built during import."""
    blender_object.color = (1, 0, 0, 1)  # Tint red
```

---

## CLI Usage

Run from the command line using Blender's `--python` flag:

```bash
# Inspect a file
blender --background --python-expr "
from io_mesh_3mf.api import inspect_3mf
info = inspect_3mf('model.3mf')
print(f'{info.num_objects} objects, {info.num_triangles_total} triangles')
"

# Batch convert
blender --background --python my_script.py
```

**Example script (`convert_to_orca.py`):**

```python
"""Convert a standard 3MF to Orca Slicer format."""
import sys
from io_mesh_3mf.api import import_3mf, export_3mf

input_path = sys.argv[sys.argv.index("--") + 1]
output_path = input_path.replace(".3mf", "_orca.3mf")

result = import_3mf(input_path, import_materials="MATERIALS")
if result.status == "FINISHED":
    export_result = export_3mf(
        output_path,
        objects=result.objects,
        use_orca_format="BASEMATERIAL",
        mmu_slicer_format="ORCA",
    )
    print(f"Converted: {export_result.num_written} objects → {output_path}")
```

```bash
blender --background --python convert_to_orca.py -- input.3mf
```

---

## Export Format Reference

| `use_orca_format` | `mmu_slicer_format` | Output Format |
|-------------------|---------------------|---------------|
| `"STANDARD"` | — | Spec-compliant single-model 3MF, geometry only |
| `"BASEMATERIAL"` | `"ORCA"` | Standard 3MF with basematerials + colorgroups |
| `"BASEMATERIAL"` | `"PRUSA"` | Standard 3MF with basematerials |
| `"PAINT"` | `"ORCA"` | Multi-file Orca/Bambu structure with `paint_color` attributes |
| `"PAINT"` | `"PRUSA"` | Single-file with `slic3rpe:mmu_segmentation` hash strings |

---

## Error Handling

All API functions return result dataclasses instead of raising exceptions. Check `result.status` to determine success:

```python
result = import_3mf("model.3mf")
if result.status == "FINISHED":
    print(f"Success: {result.num_loaded} objects")
else:
    print(f"Failed: {result.warnings}")
```

Archive-level errors (corrupt ZIP, missing model files) set `status = "CANCELLED"`. Per-object warnings (non-manifold geometry, missing textures) are collected in `warnings` but don't prevent completion.

---

## Notes

- **Blender context required** — `import_3mf` and `export_3mf` need `bpy.context` to be available. They work in `--background` mode but not outside Blender entirely.
- **`inspect_3mf` is lightweight** — it only opens the ZIP and parses XML. No Blender objects, materials, or images are created.
- **Thread safety** — Blender's Python API is not thread-safe. Don't call these functions from background threads.
- **Batch isolation** — `batch_import` and `batch_export` catch per-file exceptions so one failure doesn't stop the batch.
