# Blender 3MF Format

> **Note**  
> This is an actively maintained fork of the [original Blender 3MF add-on](https://github.com/Ghostkeeper/Blender3mfFormat), updated for modern Blender versions (4.2+) and ongoing development.

This is an add-on for Blender for importing and exporting **3MF (3D Manufacturing Format)** files.

3MF is a modern format for 3D printing. Unlike STL, it carries more than geometry: units, materials, colors, metadata, and slicer-relevant information. Blender sits upstream of slicers in many workflows, and this add-on helps make that process smooth and predictable.

The goal is simple: make **Blender a reliable, spec-compliant tool in real 3MF workflows**, with solid behavior and interoperability with modern slicers.

---

## Status

- Compatible with **Blender 4.2+**
- Actively maintained

For Blender versions **2.80–3.6**, see the [original releases](https://github.com/Ghostkeeper/Blender3mfFormat/releases/latest).

---

## Features

- Import and export 3MF files
- Material and color support using modern Blender material APIs
- Embedded viewport thumbnails in exported 3MF files
- Correct handling of units and build structure
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

---

## Development & Contributing

Current features and roadmap are in **[ROADMAP.md](ROADMAP.md)**

- Completed: PrusaSlicer MMU export, Orca Slicer compatibility, automatic thumbnails
- In progress: Triangle Sets Extension

---

## 3MF Specification Support

This add-on targets **3MF Core Specification v1.3.0** but is ready for v1.4.0. It includes checks to warn or stop on 1.4-specific conditions, which currently pass since 1.3.0 does not enforce them.

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