# Blender 3MF Format
[![Static Badge](https://img.shields.io/badge/Funding-%2465-blue?style=for-the-badge&logo=buymeacoffee)](https://buymeacoffee.com/clonephaze) [![Static Badge](https://img.shields.io/badge/Supporters-3-success?style=for-the-badge&logo=buymeacoffee)](https://buymeacoffee.com/clonephaze) [![GitHub Issues or Pull Requests](https://img.shields.io/github/issues/Clonephaze/3MF-Blender-Add-on---Maintained?style=for-the-badge&logo=github&color=critical)](https://github.com/Clonephaze/3MF-Blender-Add-on---Maintained/issues) [![GitHub Issues or Pull Requests](https://img.shields.io/github/issues-closed/Clonephaze/3MF-Blender-Add-on---Maintained?style=for-the-badge&logo=github)](https://github.com/Clonephaze/3MF-Blender-Add-on---Maintained/issues)

> [!NOTE]
> This is an actively maintained fork of the [original Blender 3MF add-on](https://github.com/Ghostkeeper/Blender3mfFormat), updated for modern Blender versions (4.2+) and ongoing development.

Blender 3MF Format is a Blender add-on for importing, editing, and exporting **3MF (3D Manufacturing Format)** files with real slicer-aware data intact.

It covers far more than raw mesh export. The add-on handles geometry, materials, textures, metadata, triangle sets, Orca/Bambu modifier parts, MMU paint workflows for most slicers, slicer profile round-tripping, and spec-compliant 3MF packaging.

---

## Status

- **Version 2.0.0**
- Compatible with **Blender 4.2+**
- Primary development target: **Blender 5.0**
- Actively maintained

For Blender versions **2.80–3.6**, see the [original releases](https://github.com/Ghostkeeper/Blender3mfFormat/releases/latest).

---

## Why Use It

- Import and export real 3MF files instead of flattening everything to STL-style geometry
- Preserve colors, materials, textures, metadata, and slicer-specific project data
- Export for **standard 3MF**, **Orca Slicer / BambuStudio**, and **PrusaSlicer / SuperSlicer** workflows
- Create paint, seam, and support data directly in Blender with the built-in **MMU Paint Suite**
- Bake existing textures and vertex colors into quantized slicer-ready paint data
- Save and reuse **slicer profiles** so exports open with the right machine and filament setup
- Turn objects into slicer modifier, support, blocker, or negative parts from the 3MF side panel
- Automate import/export/inspection through the **public Python API**

| Slicer                        | Round-Trip Support | Notes                                                                                          |
| ----------------------------- | ----------------- | ---------------------------------------------------------------------------------------------- |
| **Standard 3MF**              | Full              | Spec-compliant geometry, materials, textures, metadata, and extension-aware export            |
| **Orca Slicer / BambuStudio / PrusaSlicer / SuperSlicer** | Full              | Full paint, seam, support, multicolor, modifier-part, and slicer-config workflow support      |

## Documentation

The full user guide lives on CloneCore and covers the add-on in much more detail than this README.

- **Start here:** [Getting Started & Importing](https://clonecore.net/docs/3mf-guide/getting-started)
- [Choosing the Right Mode / Standard Export](https://clonecore.net/docs/3mf-guide/exporting-standard)
- [Object or Face Colors](https://clonecore.net/docs/3mf-guide/exporting-multicolor)
- [Painting or Texture Baking](https://clonecore.net/docs/3mf-guide/mmu-painting)
- [Metadata, Part Types & Triangle Sets](https://clonecore.net/docs/3mf-guide/metadata)
- [Slicer Profiles](https://clonecore.net/docs/3mf-guide/slicer-profiles)
- [Troubleshooting & FAQ](https://clonecore.net/docs/3mf-guide/troubleshooting)

API and developer-facing docs:

- [Generated API docs in this repo](docs/site/index.html)
- [Local API reference source](API.md)


---

## Installation

### Blender 4.2+ 

[**Official Blender Extensions Platform (Recommended)**](https://extensions.blender.org/add-ons/threemf-io) – Includes automatic updates!

1. Open Blender
2. Go to *Edit → Preferences → Get Extensions*
3. Search for **"3MF"**
4. Click *Install* on **3MF Import/Export**

### Manual Installation

**Option 1: Drag & Drop**
1. Download the ZIP from [Releases](https://github.com/Clonephaze/3MF-Blender-Add-on---Maintained/releases/latest)
2. Open Blender
3. Drag the downloaded ZIP file into Blender
4. Enable the add-on

**Option 2: Preferences**
1. Download the ZIP from [Releases](https://github.com/Clonephaze/3MF-Blender-Add-on---Maintained/releases/latest)
2. Open *Edit → Preferences → Add-ons*
3. Click *Install…* and select the downloaded ZIP file
4. Enable **3MF Import/Export**

---

## Quick Start

After installation, the add-on shows up here:

- **File → Import → 3D Manufacturing Format (.3mf)**
- **File → Export → 3D Manufacturing Format (.3mf)**
- **3D Viewport → N → 3MF tab** for metadata, object info, triangle sets, and MMU tools

### Importing

Import from **File → Import → 3D Manufacturing Format (.3mf)**.

The main import choice is **Material Mode**:

- **Import Materials**: best for viewing colors, materials, textures, and round-tripping files
- **Import MMU Paint Data**: converts supported slicer segmentation into an editable paint texture
- **Geometry Only**: ignore color/material data and bring in mesh data only

The importer can also preserve:

- Geometry and component hierarchies
- Basematerials, colorgroups, textures, and PBR properties
- Orca `paint_color` data and Prusa `mmu_segmentation` data
- Scene/object metadata
- Triangle sets
- Embedded slicer config files for later round-trip export

### Exporting

Export from **File → Export → 3D Manufacturing Format (.3mf)**.

The key export setting is **Material Export Mode**:

- **Auto**: inspects the scene and picks the right exporter
- **Standard 3MF**: writes a spec-compliant single-model 3MF
- **Paint Segmentation**: writes slicer-oriented paint segmentation for MMU workflows

Use **Standard 3MF** when you need a clean spec-compliant file for general tools or print services.

Use **Auto** or **Paint Segmentation** when targeting slicers with object/face colors or painted filament regions.

That includes both workflows the add-on supports:

- a simple per-object or per-face material color workflow
- the full paint workflow with editable paint, seam, and support layers

The external guide pages walk through both workflows in detail:

- [Standard export guide](https://clonecore.net/docs/3mf-guide/exporting-standard)
- [Multi-color export guide](https://clonecore.net/docs/3mf-guide/exporting-multicolor)

### Metadata, Part Types, and Triangle Sets

The **3MF** sidebar tab exposes more than import/export dialogs:

- Scene metadata editing
- Object info and preserved object metadata
- Orca/Bambu modifier part types including modifier, support enforcer, support blocker, and negative part
- Slicer info from imported files
- Triangle set naming and export via the Triangle Sets extension

See the full walkthrough here:

- [Metadata, Part Types & Triangle Sets](https://clonecore.net/docs/3mf-guide/metadata)

### MMU Paint Suite

The add-on includes a built-in texture-based multi-material painting workflow for slicer-ready color zones.

- Paint filament zones directly in Blender's Texture Paint mode
- Switch between color, seam, and support painting layers
- Reassign filament colors across an entire painted texture
- Start from scratch with a guided panel in Texture Paint mode
- Bake materials, textures, or vertex color-driven looks down into MMU paint data
- Quantize and clean painted regions before export
- Export painted results as slicer-native paint data that Orca/Bambu and Prusa-family slicers understand

Full guide:

- [Painting or Texture Baking](https://clonecore.net/docs/3mf-guide/mmu-painting)

### Slicer Profiles

You can save printer/filament configuration extracted from existing slicer-generated 3MF files and embed it into future exports.

- Load profiles from Orca/Bambu or Prusa-generated 3MF projects
- Reuse saved profiles across Blender sessions
- Embed the correct config during export so slicers open with the expected machine and filament setup

Guide:

- [Slicer Profiles](https://clonecore.net/docs/3mf-guide/slicer-profiles)

---

## Programmatic API

Version 2.0.0+ includes a public Python API for headless and scripted workflows without `bpy.ops`:

```python
from io_mesh_3mf.api import import_3mf, export_3mf, inspect_3mf

# Inspect without importing
info = inspect_3mf("model.3mf")
print(info.unit, info.num_objects, info.num_triangles_total)

# Import
result = import_3mf("model.3mf", import_materials="PAINT")
print(result.status, result.num_loaded)

# Export specific objects
result = export_3mf("output.3mf", objects=my_objects, use_orca_format="AUTO")

# Batch operations
from io_mesh_3mf.api import batch_import
results = batch_import(["a.3mf", "b.3mf"], target_collection="Imports")
```

Use the API when you want to:

- batch process 3MF files
- inspect archives without creating Blender objects
- integrate 3MF import/export into another add-on
- build custom automation around Blender 3MF workflows

Docs:

- [Generated API docs on github](docs/site/index.html)
- [Nicer Generated docs on CloneCore](https://www.clonecore.net/docs/3mf/guide)

---

## Feature Summary

### Import

- Standard 3MF geometry and build data
- Materials Extension data including colors, textures, and active PBR properties
- Production Extension projects
- Slicer-style MMU segmentation data
- Metadata, custom fields, and preserved slicer config blobs
- Triangle sets and slicer-specific object annotations

### Export

- Standard spec-compliant 3MF archives
- Slicer-style multicolor, paint, seam, support, and modifier-part exports
- Embedded thumbnails
- Component hierarchies or flattened exports for stricter downstream tools
- Metadata and preserved round-trip data

### Blender Tooling

- 3MF sidebar panels for metadata and object info
- Modifier part type assignment for Orca/Bambu workflows (Not Prusa-compatible)
- Triangle set naming UI
- MMU painting, bake-to-paint, support/seam painting, and cleanup tools
- Saved slicer profile management in add-on preferences

---

## Specification Support

This add-on targets **3MF Core Specification v1.4.0** and supports modern 3MF workflows beyond plain geometry.

Supported extension families include:

- Core materials / basematerials
- Materials Extension
- Production Extension
- Triangle Sets support
- Vendor-specific Orca/Bambu and Prusa-compatible data paths

For malformed or incomplete files, the add-on tries to recover where practical while still enforcing critical export-side requirements such as valid archive structure, model XML, units, and build definitions.

---

## Troubleshooting

If something looks wrong on import or export, start here:

- [Troubleshooting & FAQ](https://clonecore.net/docs/3mf-guide/troubleshooting)

Common issues covered there include:

- missing colors after import
- MMU paint data not appearing as editable paint zones
- slow import/export on dense painted meshes
- slicers not picking up filament assignments
- print services rejecting component hierarchies

---

## Project History

Forked from Ghostkeeper’s original Blender 3MF add-on and modernized by Jack (2025–).

- Original author: Ghostkeeper (2020–2023)
- Fork & maintenance: Jack (2025–)

All original attribution and **GPL v2+ license** are preserved.

---

## License

GPL v2+
