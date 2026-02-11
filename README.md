# Blender 3MF Format

> [!NOTE]
> This is an actively maintained fork of the [original Blender 3MF add-on](https://github.com/Ghostkeeper/Blender3mfFormat), updated for modern Blender versions (4.2+) and ongoing development.

This is an add-on for Blender for importing and exporting **3MF (3D Manufacturing Format)** files.

3MF is a modern format for 3D printing. Unlike STL, it carries more than geometry: units, materials, colors, metadata, and slicer-relevant information. Blender sits upstream of slicers in many workflows, and this add-on helps make that process smooth and predictable.

The goal is simple: make **Blender a reliable, spec-compliant tool in real 3MF workflows**, with solid behavior and interoperability with modern slicers.

---

## Status

- **Version 2.0.0** — Major architecture restructure with public API
- Compatible with **Blender 4.2+**
- Actively maintained

For Blender versions **2.80–3.6**, see the [original releases](https://github.com/Ghostkeeper/Blender3mfFormat/releases/latest).

---

## Features

- Import and export 3MF files
- Material and color support using modern Blender material APIs
- Embedded viewport thumbnails in exported 3MF files
- Correct handling of units and build structure
- **Public API** for programmatic/headless workflows (see [API.md](API.md))
- Multiple 3MF spec-compliant extensions:
  - Core Materials (basematerials)
  - Production Extension (multi-object builds, color zones)
  - Vendor extensions for Orca Slicer, BambuStudio, and PrusaSlicer

### Slicer Compatibility

| Slicer                        | Round-Trip Support | Notes                                                                                          |
| ----------------------------- | ----------------- | ---------------------------------------------------------------------------------------------- |
| **Orca Slicer / BambuStudio** | Partial           | Per-triangle material/color zones preserved. Does **not** reproduce slicer paint workflows    |
| **PrusaSlicer**               | Partial           | Per-triangle material/color zones preserved. No Blender paint-mode support                     |
| **Standard 3MF**              | Full              | Geometry, materials, metadata                                                                  |

---

## Installation

### Blender 4.2+ (Recommended)

[**Official Blender Extensions Platform**](https://extensions.blender.org/add-ons/threemf-io) – Includes automatic updates!

1. Open Blender
2. Go to *Edit → Preferences → Get Extensions*
3. Search for **"3MF"**
4. Click *Install* on **3MF Import/Export**

### Manual Installation

**Option 1: Drag & Drop**
1. Download the ZIP from [Releases](https://github.com/Ghostkeeper/Blender3mfFormat/releases)
2. Open Blender
3. Drag the downloaded ZIP file into Blender
4. Enable the add-on

**Option 2: Preferences**
1. Download the ZIP from [Releases](https://github.com/Ghostkeeper/Blender3mfFormat/releases)
2. Open *Edit → Preferences → Add-ons*
3. Click *Install…* and select the downloaded ZIP file
4. Enable **3MF Import/Export**

---

## Usage

Menus after installation:
- **File → Import → 3D Manufacturing Format (.3mf)**
- **File → Export → 3D Manufacturing Format (.3mf)**

### Import Options
- **Scale** – Uniform scale applied from the scene origin
- **Import Materials** – Import material colors (disable for geometry-only)
- **Placement** – Choose object placement:
  - **Keep** – Keep positions from the 3MF file
  - **World Origin** – Move to scene origin
  - **3D Cursor** – Place at the current 3D cursor
- **Reset Object Origins** – Reset each object’s origin before placement

### Export Options
- **Selection Only**
- **Scale**
- **Apply Modifiers**
- **Coordinate Precision**
- **Export Hidden Objects**
- **Multi-Material Format** – Per-triangle material assignment using Standard 3MF, Orca/Bambu Slicer, or PrusaSlicer MMU
  - **Orca Slicer** – `Production Extension` with `paint_color` attributes
  - **PrusaSlicer** – `slic3rpe:mmu_segmentation` attributes for color metadata and round-trip fidelity

### MMU Paint Suite

Built-in multi-material texture painting system for creating per-triangle filament assignments directly in Blender's 3D Viewport.

**Features:**
- **Texture-Based Painting** – Paint multi-filament regions using Blender's native paint tools
- **Visual Filament Palette** – Click-to-switch color swatches in the 3D View sidebar (N-panel → 3MF tab)
- **Filament Management** – Add, remove, and reassign filament colors during painting

**Usage:**
1. Import a 3MF file with multi-material data, or select any mesh object
2. Open sidebar (N-panel) → 3MF tab → MMU Paint Suite
3. Add filaments and click "Initialize Painting"
4. Click filament swatches to switch active color, then paint in Texture Paint mode
5. Export to 3MF with desired slicer format

---

## Programmatic API

Version 2.0.0 introduces a public Python API for headless/programmatic use without `bpy.ops`:

```python
from io_mesh_3mf.api import import_3mf, export_3mf, inspect_3mf

# Inspect without importing
info = inspect_3mf("model.3mf")
print(info.unit, info.num_objects, info.num_triangles_total)

# Import
result = import_3mf("model.3mf", import_materials="PAINT")
print(result.status, result.num_loaded)

# Export specific objects
result = export_3mf("output.3mf", objects=my_objects, use_orca_format="BASEMATERIAL")

# Batch operations
from io_mesh_3mf.api import batch_import
results = batch_import(["a.3mf", "b.3mf"], target_collection="Imports")
```

Full documentation: **[API.md](API.md)**

---

## Development & Contributing

Current features and roadmap are in **[ROADMAP.md](ROADMAP.md)** | Full changelog in **[CHANGELOG.md](CHANGELOG.md)**

---

## 3MF Specification Support

This add-on targets **3MF Core Specification v1.4.0**. It includes checks to warn or stop on specification-specific conditions.

### Behavior Notes

The 3MF spec requires consumers to fail hard on malformed files. In Blender, this is often impractical, so the add-on handles recoverable issues gracefully:
- Core requirements (ZIP/OPC structure, model XML, units, build definitions) are enforced on export
- Partial or malformed files may import with warnings instead of failing
- Conflicting metadata from multiple files may be skipped to preserve scene integrity

### Extensions

Supported 3MF extensions for improved slicer interoperability:
| Extension                        | Namespace                                                         | Support       |
| -------------------------------- | ----------------------------------------------------------------- | ------------- |
| Core Materials (`basematerials`) | Core Spec v1.3.0                                                  | Full          |
| Production Extension             | `http://schemas.microsoft.com/3dmanufacturing/production/2015/06` | Full          |
| Materials Extension v1.2.1       | `http://schemas.microsoft.com/3dmanufacturing/material/2015/02`   | Full (Active PBR) |

**Materials Extension Features:**
- **Colorgroups & Textures**: Full import/export with UV coordinates
- **PBR Metallic**: Metallic/roughness values applied to Principled BSDF
- **PBR Specular**: Specular color/glossiness mapped to Blender materials
- **Translucent**: IOR, transmission, and attenuation for glass-like materials
- **Round-trip**: All element types preserved for lossless re-export

---

## Orca Slicer / BambuStudio

Per-triangle material handling for Orca Slicer and BambuStudio files. Does **not** include slicer paint-mode workflows.

**Import**
- Reads multi-file Production Extension structure (`3D/Objects/*.model`)
- Imports `paint_color` attributes as Blender materials
- Loads filament colors from `Metadata/project_settings.config`
- Supports Orca, BambuStudio, and PrusaSlicer files

**Export**
- Writes multi-file Production Extension structure
- Exports per-triangle `paint_color` attributes
- Generates `project_settings.config` with filament colors
- Creates correct OPC relationships
- Embeds viewport thumbnail previews

Filament colors reload automatically from metadata for accurate material recreation.

---

## PrusaSlicer Compatibility

**Import**
- Reads `slic3rpe:mmu_segmentation` attributes
- Preserves multi-material zones as Blender materials

**Export**
- Standard 3MF export works
- Orca-format color zones compatible with PrusaSlicer painting tools

PrusaSlicer does not embed actual RGB colors in 3MF files; it uses filament indices referencing local profiles. Round-tripping through Blender generates colors based on zone indices and may not match original filament colors exactly.

---

## Project History

Forked from Ghostkeeper’s original Blender 3MF add-on and modernized by Jack (2025–).

- Original author: Ghostkeeper (2020–2023)
- Fork & maintenance: Jack (2025–)

All original attribution and **GPL v2+ license** are preserved.

---

## License

GPL v2+