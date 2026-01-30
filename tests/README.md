# Blender 3MF Format

> [!NOTE]  
> This repository is an actively maintained fork of the original Blender 3MF add-on by [Ghosteeper](https://github.com/Ghostkeeper), updated for modern Blender versions (4.2+) and ongoing development.

This is a Blender add-on for importing and exporting **3MF (3D Manufacturing Format)** files.

3MF is a modern interchange format for additive manufacturing. Unlike STL, it is designed to carry not only geometry, but also units, materials, colors, metadata, and other information relevant to real 3D printing workflows. In this context, Blender serves as a modeling and preparation tool upstream of slicers and manufacturing software.

The goal of this add-on is to make **Blender a practical and reliable tool in 3MF-based workflows**, with spec-aligned behavior and useful interoperability with modern slicers.

---

## Status

- ✓ Compatible with **Blender 4.2+**
- ✓ Actively maintained
- ✓ Production-ready
- ✓ 3mf Material spec ready
- ✓ Orca/Bambu slicer specific color ready

For Blender versions **2.80–3.6**, use the [original repository releases](https://github.com/Ghostkeeper/Blender3mfFormat/releases/latest).

---

## Features

- Import and export 3MF files
- Correct handling of units and build structure
- Material and color support compatible with modern Blender material APIs
- Verified round-trip import/export
- Scriptable import/export operators
- Extensive automated testing

---

## Installation

### Blender 4.2+

**Option 1: Drag & Drop (Recommended)**
1. Download this repository as a ZIP
2. Open Blender
3. Drag the `io_mesh_3mf` folder into the Blender window
4. Confirm installation and enable the add-on

**Option 2: Preferences**
1. Extract the ZIP
2. Open *Edit → Preferences → Add-ons*
3. Click *Install…* and select the `io_mesh_3mf` folder
4. Enable **Import-Export: 3MF format**

Reload scripts and enable the add-on.

---

## Usage

After installation, the following menu entries are available:

- **Import**: File → Import → 3D Manufacturing Format (.3mf)
- **Export**: File → Export → 3D Manufacturing Format (.3mf)
- **Preferences**: Edit → Preferences → Add-ons → 3MF Format

![Screenshot](screenshot.png)

### Import Options
- **Scale**: Uniform scale applied from the scene origin
- **Import Materials**: Import material colors from the file, auto detects standard 3mf materials and slicer specific data from Orca/Bambu (disable for geometry-only import)

### Export Options
- **Selection Only**
- **Scale**
- **Apply Modifiers**
- **Coordinate Precision**
- **Export Hidden Objects**
- **Orca/Bambu Slicer Color Zones**: Export face colors as Orca Slicer filament zones (vendor-specific, works with bambu slicer too)

---

## Testing

This add-on includes comprehensive automated testing to ensure reliability.

See [`tests/README.md`](tests/README.md) for detailed testing information.

---

## 3MF Specification Support

This add-on targets the **3MF Core Specification v1.3.0**.

### Behavior Notes

> **NOTE**  
> The 3MF specification requires consumers to fail hard on malformed files. In practice, this add-on favors recoverability in a DCC environment.

- Core requirements (ZIP/OPC structure, model XML, units, build definitions) are enforced on export.
- On import, partially malformed files may load with warnings rather than failing entirely.
- When conflicts arise while importing multiple 3MF files into a single scene, conflicting metadata may be skipped to preserve scene integrity.

### Extensions

This add-on supports several 3MF extensions for enhanced interoperability with slicers and manufacturing software.

#### Supported Extensions

| Extension | Namespace | Support Level |
|-----------|-----------|---------------|
| **Core Materials** (`basematerials`) | Core Spec v1.3.0 | ✅ Full |
| **Production Extension** | `http://schemas.microsoft.com/3dmanufacturing/production/2015/06` | ✅ Full |
| **Materials Extension** | `http://schemas.microsoft.com/3dmanufacturing/material/2015/02` | 🔶 Partial |

#### Orca Slicer / BambuStudio Compatibility (Partial Support for PrusaSlicer)

This add-on includes special support for **Orca Slicer** and **BambuStudio** multi-color workflows:

**Import:**
- Reads multi-file Production Extension structure (`3D/Objects/*.model`)
- Imports `paint_color` attributes as Blender materials
- Reads actual filament colors from `Metadata/project_settings.config`
- Supports files exported from Orca Slicer, BambuStudio, and PrusaSlicer

**Export (Orca Slicer Color Zones option):**
- Exports using Production Extension multi-file structure
- Writes per-triangle `paint_color` attributes for filament assignment
- Generates `project_settings.config` with filament colors
- Creates proper OPC relationships for slicer compatibility

**Round-trip Workflow:**
1. Create a mesh in Blender with multiple materials (different colors per face)
2. Export with "Orca Slicer Color Zones" enabled
3. Open in Orca Slicer - colors appear as filament zones
4. Re-import into Blender - material colors are preserved

> **NOTE**  
> The Orca color zone export uses vendor-specific attributes (`paint_color`) that are not part of the official 3MF specification. Standard 3MF consumers will still read the geometry correctly but may not display colors.

See [EXTENSIONS.md](EXTENSIONS.md) for detailed documentation on extension support, vendor-specific features, and adding new extensions.

---

## Project History

This project began as a modernization of the original Blender 3MF add-on by Ghostkeeper and has since continued as an independently maintained fork.

- Original Author: Ghostkeeper (2020–2023)
- Modernization & Ongoing Maintenance: Jack (2025–)

Original authorship, attribution, and the **GPL v2+ license** are fully preserved.

---

## License

GPL v2+
