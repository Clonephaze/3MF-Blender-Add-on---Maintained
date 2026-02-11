2.0.0 — Architecture Restructure & Public API
====

This is a major restructure of the entire codebase. The monolithic `import_3mf.py` (3055 lines, 56 methods) and `export_3mf.py` have been broken into clean, focused sub-packages. A new public API enables programmatic 3MF workflows without `bpy.ops`. All import/export functionality is unchanged — this is a code organization and robustness release.

> **Breaking:** Scripts that imported internal symbols directly from the old module layout will need updating. The `bpy.ops.import_mesh.threemf()` and `bpy.ops.export_mesh.threemf()` operators are unchanged.

Architecture
----
* **`import_3mf/` package** — Operator, context, archive, geometry, builder, scene, slicer detection, and materials sub-package
* **`export_3mf/` package** — Operator, context, archive, geometry, standard/orca/prusa exporters, components, thumbnail, segmentation, and materials sub-package
* **`common/` package** — Shared types, constants, colors, logging, XML utilities, units, segmentation codec, extensions, metadata, annotations
* **`paint/` package** — MMU Paint Suite panel (`paint/panel.py`) and bake operator (`paint/bake.py`) extracted from monolithic `paint_panel.py`
* **Context dataclasses** — `ImportContext` / `ExportContext` replace mutable operator state. Every helper takes `ctx` as its first argument.
* **Export dispatch** — `StandardExporter`, `OrcaExporter`, `PrusaExporter` classes replace monolithic if/else chains

Features
----
* **Lightmap UV option for MMU Paint** — Add `paint_uv_method` ("SMART"|"LIGHTMAP") to import operator, API, and paint panel. Smart UV Project (default) shares edges between faces for hand-painting; Lightmap Pack isolates faces for procedural bakes with higher fidelity.
* **Texture size override** — Add `paint_texture_size` (Auto/1024–16384) to import operator and API. Allows manual control over paint texture resolution instead of auto-sizing by triangle count.
* **Paint subdivision depth control** — Add `subdivision_depth` (4–10, default 7) to export preferences, operator, API, and `ExportOptions`. Tunes segmentation tree depth for balance between detail and export time.
* **Nested EMPTY support** — Export now recursively collects mesh objects from nested EMPTY hierarchies (respecting `export_hidden`). Meshes parented under EMPTYs are exported and validated correctly.
* **Material color detection** — New "Detect from Materials" button in Shader Editor sidebar scans Principled BSDF node trees for colors. Reads Color Ramp stops, RGB nodes, viewport colors, and populates filament palette automatically.
* **Cycles bake optimization** — Bake-to-MMU workflow uses GPU compute (when available), 1 sample, and temporary Emission shader wiring for 5–10× faster flat/procedural color bakes.
* **Merged Standard/BaseMaterial export modes** — The separate "Standard 3MF" (geometry only) and "Base Material" modes are unified into a single "Standard 3MF" mode that automatically includes material colors when present. 

Public API (`api.py`)
----
* **`import_3mf()`** — Headless import with all operator options as keyword arguments
* **`export_3mf()`** — Headless export with explicit object lists, format selection, callbacks
* **`inspect_3mf()`** — Read-only archive inspection without creating Blender objects (returns object counts, materials, textures, metadata, extensions, vendor format)
* **`batch_import()` / `batch_export()`** — Multi-file operations with per-file error isolation
* **Callbacks** — `on_progress`, `on_warning`, `on_object_created` for monitoring and integration
* **Building blocks** — Re-exports `colors`, `types`, `segmentation`, `units`, `extensions`, `xml_tools`, `metadata`, `components` sub-namespaces
* See **[API.md](API.md)** for full documentation and examples

Bug Fixes
----
* **Large colorgroup blocking** — 3MF files with 50,000+ colors in a single colorgroup (e.g. 3D scans with per-vertex coloring) no longer hang Blender. Groups exceeding 1,000 entries are skipped with a user-facing warning.
* **Menu duplicate fix** — Reinstalling the addon via drag-and-drop no longer causes duplicate Import/Export menu entries or `list.remove(x): x not in list` errors. Menu cleanup now matches by function identity rather than object reference.
* **Multiproperties per-vertex UV fix** — Multiproperties with multiple texture2dgroups now correctly resolve per-vertex UV coordinates from `p1`/`p2`/`p3` indices independently, instead of assigning the same UV to all three vertices.
* **PBR texture UV round-trip** — PBR textured materials (roughness, metallic, etc.) now export with proper `texture2dgroup` / `tex2coord` UV coordinate data alongside `pbmetallictexturedisplayproperties`. Previously, PBR materials were excluded from texture2dgroup creation, losing UV data on export.
* **Import texture coordinate source** — Textured materials imported from `texture2dgroup` data now use UV coordinates (not Generated), since a UV layer is guaranteed from the `tex2coord` import. PBR textures on meshes without UV data still fall back to Generated projection.
* **PBR base color deduplication** — When both `setup_textured_material` and `apply_pbr_textures_to_material` run on the same material, the base color texture is no longer wired twice. The PBR function detects and skips already-connected Base Color inputs.
* **Texture archive deduplication** — Texture images already written to the archive by the PBR pass are detected and reused instead of being written a second time.
* **Paint import crash fix** — Fixed `TypeError: render_segmentation_to_texture() got an unexpected keyword argument 'bpy'` caused by a leftover `bpy=bpy` kwarg from the restructure (parameter is named `bpy_module`).
* **Triangle indexing fix** — Segmentation export now correctly uses `mesh.loop_triangles` indices (`tri_idx`) instead of polygon indices, fixing incorrect segmentation mappings for meshes with quads/ngons.
* **Initialize undo fix** — MMU Paint initialization now pushes a single undo step before all operations, so Ctrl+Z restores the complete pre-initialization state instead of fragmenting across mode switches and UV unwraps.

Technical
----
* All custom logging uses `common/logging.py` (`debug`, `warn`, `error`, `safe_report`) — no Python `logging` module
* All tests run in real Blender headless mode (no mocking of Blender APIs)
* Test suite: 150 unit tests + 128 integration tests, all passing
* Build: `ThreeMF_io-2.0.0.zip` via `blender --command extension build`
* **Dedicated MMU_Paint UV layer** — Paint initialization and import create/use a dedicated "MMU_Paint" UV layer for segmentation data, keeping painted UVs separate from material texture UVs.
* **Limited Dissolve pre-unwrap** — Bake and initialize paths run `bmesh.ops.dissolve_limit` (~2° tolerance) before UV unwrap to merge coplanar triangles, giving faces more UV space and reducing blurriness in painted regions.
* **Segmentation rasterizer improvements** — Add `expand_px` support for per-edge normalized threshold expansion (eliminates 1-pixel gaps at Lightmap Pack UV island boundaries). UV-method-aware gap closing: 2 dilation passes for Smart UV, 6 passes for Lightmap Pack.
* **Cycles bake optimizations** — Bake temporarily sets `cycles.samples=1`, attempts GPU compute, and rewires material to Emission shader (EMIT bake skips BSDF/lighting for 5–10× speed). Restores original settings on success or failure.
* **Component EMPTY recursion** — Component child handling now recurses into EMPTY hierarchies and only exports MESH/EMPTY children when assembling 3MF component definitions.
* **Mesh collection helper** — New `collect_mesh_objects()` recursively gathers MESH objects from scene collections and nested EMPTY hierarchies. Replaces ad-hoc mesh filtering across operator, materials, and exporter modules.

---

1.4.0 — MMU Paint Import/Export Support w/ 3MF Paint panel
====

Features
----
* **MMU Paint Import/Export:** Full support for importing and exporting multi-material paint data for Orca Slicer and PrusaSlicer, enabling round-trip workflows between Blender and Orca/Prusa for multi-filament prints.
* **MMU Paint Suite:** New panel in the N-panel `3MF`. Allows users to initialize multi-material painting on any mesh, manage filament colors, and paint per-triangle filament assignments using Blender's native texture paint tools.
* **Paint import dialog:** Instructions for finding MMU Paint Suite panel after import, and a button to switch automatically. 

Bug Fixes
----
* **Fixed critical color space mismatch:** 3MF colors are sRGB, Blender materials are linear — now properly converting between them on import, export, and painting
* **Improved filament operations:** Filament removal and reassignment now provide feedback on pixel counts
* **Fixed PBR and Normal material import issues:** Materials with PBR textures now import correctly without creating duplicate materials or losing texture relationships

Technical
----
* Added sRGB ↔ linear color space conversion utilities (`srgb_to_linear()`, `linear_to_srgb()`, `hex_to_linear_rgb()`, `linear_rgb_to_hex()`)
* Applied conversions throughout: import materials (sRGB → linear), export materials (linear → sRGB), paint panel (all color operations)

---

1.3.2 — Component Instances
====
Features
----
* Linked duplicates (Alt+D) export as 3MF component references (shared mesh, smaller file size)
* Import restores linked duplicates
* Quick options popup for drag-drop

Technical
----
* Component definitions stored in `<resources>` with full mesh data
* Instance containers use `<components>` with `<component objectid="ref"/>` references
* Transforms applied at build item level
* Compatible with all material types (basematerials, textures, PBR, Orca color zones)

1.3.1 — Drag & Drop Import
====
Features
----
* Drag & drop .3mf files directly into Blenders viewport
* Grid Layout: arrange imported objects in a grid
* Origin Placement: choose original, center, or bottom center
* Faster Manifold Check: use bmesh module for faster non-manifold detection

1.3.0 — Textured PBR Materials
====
Complete implementation of textured PBR display properties from the 3MF Materials Extension v1.2.1, enabling full round-trip support for base color, roughness, and metallic texture maps.

Features
----
* **Base Color Textures:** Import/export `basecolortextureid` from `<pbmetallictexturedisplayproperties>` with proper Principled BSDF node connections
* **Roughness Textures:** Import/export `roughnesstextureid` with automatic Non-Color space assignment
* **Metallic Textures:** Import/export `metallictextureid` with proper shader node setup
* **Specular Workflow Textures:** Import/export `diffusetextureid`, `speculartextureid`, and `glossinesstextureid`
* **Texture Extraction:** Automatic extraction and packing of texture images from 3MF archives

Bug Fixes
----
* Fixed material reuse incorrectly matching default Blender materials when PBR textures were present
* Fixed stale texture relationships in passthrough data causing export issues

Technical
----
* Implements textured PBR display properties per 3MF Materials Extension v1.2.1 specification
* Texture images packed into blend file for portability
* Texture metadata preserved as custom properties for perfect round-trips
* Note: Normal maps are exported to archive but cannot be imported (3MF spec has no `normaltextureid` attribute). Will re-visit this in the future.

Code Organization
----
* **Import/Export Module Refactoring:** Split large monolithic files into extension-centric packages for better maintainability and easier future additions:
  - `export_materials/` package: base.py, textures.py, pbr.py, passthrough.py
  - `import_materials/` package: base.py, textures.py, pbr.py, passthrough.py
  - Standalone `export_trianglesets.py` and `import_trianglesets.py` modules
* Backward-compatible re-exports preserve existing API

---

1.2.7 — Full PBR Materials Extension Support
====
Complete implementation of all three PBR workflows from the 3MF Materials and Properties Extension, bringing the add-on 1 step closer to full Material Extension support. 

Features
----
* **Metallic Workflow:** Import/export `<pbmetallicdisplayproperties>` with metallicness and roughness
* **Specular Workflow:** Import/export `<pbspeculardisplayproperties>` with specular color and glossiness
* **Translucent Workflow:** Import/export `<translucentdisplayproperties>` with IOR, attenuation color, and roughness
* **Transmission Preservation:** Custom `blender_transmission` attribute preserves Blender's transmission value through translucent workflow round-trips

Technical
----
* Implements PBR display properties from 3MF Materials Extension specification
* Workflow selection is automatic based on material properties (metallic → specular → translucent priority)
* Note: 3MF PBR workflows are mutually exclusive per spec — materials using multiple workflows will use the highest-priority workflow that applies

---

1.2.6 — Triangle Sets Extension
====
Full implementation of the 3MF Triangle Sets extension for grouping triangles.

Features
----
* **Triangle Sets Import:** Parse `<triangleset>` elements including `<ref>` and `<refrange>` children
* **Triangle Sets Export:** Export triangle sets with name/identifier attributes and `<refrange>` optimization
* **Selection Workflows:** Triangle sets enable selection grouping for property assignment

User Interface
----
* Added "Export Triangle Sets" checkbox to export operator (disabled when Multi-Material is active)
* Added preference setting for default Triangle Sets export behavior
* Triangle sets stored as integer face attributes with mesh custom property for names

Bug Fixes
----
* Fixed "please wait" messages not displaying before UI freeze during import/export

Technical
----
* Implements Triangle Sets extension from 3MF Core Spec v1.3+
* Uses Blender 4.x custom face attributes (face maps removed in Blender 4.0)
* Consecutive triangle indices exported as `<refrange>` to reduce XML size

---

1.2.5 — Quality of Life Improvements
====
Export validation, smart material reuse, and flexible import placement options.

Features
----
* **Non-Manifold Detection:** Pre-export geometry validation warns about problematic meshes that may cause slicer issues
* **Material Reuse:** Import now matches and reuses existing Blender materials by name and color, preventing duplication on re-import
* **Selection Validation:** "Selection Only" export validates mesh objects are selected before proceeding
* **Import Placement Options:** Control object placement (World Origin / 3D Cursor / Keep Original) and origin calculation on import

Improvements
----
* All new features have preference defaults and integrate seamlessly into existing UI
* Import origin-to-geometry calculated before transformation for correct positioning

---

1.2.4 — PrusaSlicer MMU Export & Color Preservation
====
Full round-trip color support for PrusaSlicer MMU workflows and improved user feedback.

Features
----
* **PrusaSlicer MMU Export:** Face colors exported with `slic3rpe:mmu_segmentation` attributes and color metadata for perfect round-trips
* **Progress Messages:** Status feedback during import/export operations
* **Color Fidelity:** Fixed color space handling for accurate material preservation across all formats

Improvements
----
* Refactored export code following DRY principles (extracted helper methods, merged duplicate code)

---

1.2.3 — 3MF Core Specification v1.4.0 Compliance
====
Updated to target the latest 3MF Core Specification v1.4.0 (published February 6, 2025).

Features
----
* **3MF Core Spec v1.4.0:** Updated to latest specification version
* **Triangle Sets Extension:** Added namespace support for `t:trianglesets` (defined in Core Spec v1.3+)
* **Framework Ready:** Infrastructure prepared for future Triangle Sets import/export features

Changes
----
* Updated `SPEC_VERSION` constant from "1.3.0" to "1.4.0"
* Added `TRIANGLE_SETS_NAMESPACE` constant for grouping triangles
* No breaking changes — v1.4.0 primarily removed deprecated "mirror" functionality (which we never implemented) and added documentation clarifications

---

1.2.2 — PrusaSlicer Compatibility & Thumbnails
====
This extremely small patch simply adds a tag to the blender manifest.toml

---

1.2.1 — PrusaSlicer Compatibility & Thumbnails
====
This patch adds PrusaSlicer multi-material import support and automatic thumbnail generation.

Features
----
* **PrusaSlicer Support:** Import multi-material color zones from PrusaSlicer files (`slic3rpe:mmu_segmentation` attributes)
* **Thumbnails:** Automatic viewport snapshot embedded in exported 3MF files (256×256 PNG)
* **Cross-Slicer Compatibility:** Color zones now work with both PrusaSlicer and Orca Slicer formats

Bug Fixes
----
* Fixed PrusaSlicer imports not showing color zones/materials
* Fixed thumbnails not displaying in file browsers due to missing content type declaration

---

1.2.0 — Production Extension & Orca Slicer Compatibility
====
This release adds full support for the 3MF Production Extension and comprehensive Orca Slicer/BambuStudio compatibility, enabling multi-color workflows between Blender and modern slicers.

Features
----
* **Production Extension Support:**
  - Full implementation of the 3MF Production Extension (`http://schemas.microsoft.com/3dmanufacturing/production/2015/06`)
  - Multi-file export structure with individual object models in `3D/Objects/`
  - `p:path` and `p:UUID` component references for external model files
  - Proper OPC relationship files (`3D/_rels/3dmodel.model.rels`)
  - Import support for external model file references

* **Orca Slicer / BambuStudio Compatibility:**
  - **Export:** New "Orca Slicer Color Zones" option exports face colors as filament zones
  - **Export:** Per-triangle `paint_color` attributes for precise filament assignment
  - **Export:** Generates `Metadata/project_settings.config` with filament colors
  - **Import:** Reads `paint_color` attributes and creates corresponding Blender materials
  - **Import:** Extracts actual filament colors from `project_settings.config`
  - **Round-trip:** Full color preservation between Blender and Orca Slicer

* **3MF Core Specification v1.3.0 Compliance:**
  - Added `SPEC_VERSION` constant tracking specification version
  - Support for `recommendedextensions` attribute (v1.3.0 addition)
  - OPC Core Properties with Dublin Core metadata
  - Improved extension validation with prefix-to-namespace resolution

* **Extension Framework:**
  - New `extensions.py` module with `ExtensionManager` class
  - Extensible architecture for adding future 3MF extensions
  - Support for required vs optional extensions
  - Human-readable extension names in warnings

* **Import Options:**
  - New "Import Materials" checkbox to control material/color import
  - Configurable default via addon preferences
  - Automatic vendor format detection (Orca/Bambu/Prusa)

Technical Improvements
----
* **Extension Prefix Resolution:**
  - Fixed `requiredextensions="p"` validation (was comparing prefix to namespace URI)
  - `resolve_extension_prefixes()` maps XML prefixes to full namespace URIs
  - Known prefix mappings for Production, Materials, and Slic3r extensions

* **Color Indexing:**
  - 1-based filament indexing matching Orca Slicer's `paint_color` encoding
  - Proper mapping between paint codes ("4", "8", "0C", etc.) and filament array indices
  - Case-insensitive paint code parsing

* **Code Organization:**
  - Separated Orca export into dedicated methods (`execute_orca_export`, `write_orca_object_model`, etc.)
  - Standard export preserved in `execute_standard_export`
  - Clean separation of vendor-specific and standard 3MF handling

Documentation
----
* Updated README with comprehensive Extensions section
* Added Orca Slicer compatibility documentation
* Round-trip workflow instructions

---

1.1.3 — Unicode String Caching & Garbage Collection Protection
====
This release adds comprehensive defensive string caching throughout the add-on to protect Unicode characters from Python's garbage collector. This ensures users with non-ASCII characters (Chinese, Japanese, Korean, Arabic, emoji, etc.) in object names, material names, file paths, and metadata will not experience corruption or data loss.

Features
----
* **Defensive String Caching:**
  - Cache all object names, material names, metadata (names, values, datatypes), and file names before XML or export operations to protect Unicode from garbage collection
  - Explicit `str()` conversion ensures strings persist while Blender's UI or export processes are active

* **Unicode Support Improvements:**
  - Full support for all non-standard Unicode characters in object names, material names, metadata, and file path

Testing
----
* **New comprehensive Unicode test suite** (`tests/test_unicode.py`):
  - 20+ tests covering Chinese, Japanese, Korean, Arabic, emoji, and mixed Unicode
  - Tests for object names, material names, metadata, and roundtrip preservation
  - Tests for edge cases: RTL text, combining characters, surrogate pairs, very long names
* **Added to mock test suite** (`test/metadata.py`, `test/export_3mf.py`):
  - 6 new tests for Unicode metadata compatibility and conflict detection
  - 2 new tests for Unicode object and material name caching
* All existing tests continue to pass, ensuring backward compatibility

1.1.1 - 1.1.2 — Unit Handling, Precision & Preferences
====
This release refines the modernized addon with improved unit handling, higher-fidelity coordinate export, better object naming, and configurable defaults via addon preferences.

Features
----
* **Unit & Scale Handling:**
  - Fix export scaling for combinations of scene scale and units (e.g. scale_length = 0.001 with millimeter units), so 3MF files now match Blender dimensions across common unit setups.
  - Improve import scaling by correctly interpreting the 3MF model `unit` attribute and converting to Blender scene units.
* **Object Naming & Visibility:**
  - Export the Blender object name into the 3MF model, improving round‑trip fidelity and interoperability with slicers.
  - Import object names from 3MF back into Blender objects when available.
  - Add an `Export hidden objects` option to the export operator so viewport‑hidden objects can be explicitly included or excluded.
  - **User notification** when hidden objects are skipped during export, with a count and hint to enable the option.
* **Coordinate Precision & Formatting:**
  - Increase default coordinate precision to 9 decimal places to preserve full 32‑bit float resolution and reduce the risk of non‑manifold issues from rounding.
  - Standardize transformation matrix formatting to 9 decimal places for consistent, high‑precision output.

Addon Preferences
----
* Add `ThreeMFPreferences` (Edit → Preferences → Add-ons → 3MF) to configure default behavior:
  - **Default Coordinate Precision** for exports.
  - **Export Hidden Objects by Default** toggle.
  - **Apply Modifiers by Default** toggle.
  - **Default Global Scale** shared by import and export operators.
* Enhanced preferences UI with grouped settings, icons, and helpful tooltips.
* Export/import operators read these preferences on `invoke`, so dialogs open with user‑chosen defaults while still allowing per‑export overrides.

Testing & Maintenance
----
* Extend unit tests to cover:
  - New unit/scene scale behavior for export and import.
  - Hidden‑object export behavior and notification.
  - High‑precision transformation formatting.
  - Preferences loading in `invoke()` methods.
* Update tests and documentation to reflect the new defaults and options.

1.1.0 — Modernization for Blender 4.2+
====
**IMPORTANT: This version requires Blender 4.2 or newer. For Blender 2.8-4.1, use version 1.0.2.**

This release modernizes the addon for Blender 4.2+ and Python 3.11+, ensuring compatibility with current and future Blender versions.

Breaking Changes
----
* **Minimum Blender version is now 4.2** (previously supported 2.8-4.0)
* **Minimum Python version is now 3.11** (previously 3.7+)
* Installation now uses Blender Extensions format (drag-and-drop .zip or install via Preferences)

Features
----
* **Blender Compatibility:**
  - Full compatibility with Blender 4.2, 4.3, 4.5, and 5.0 Alpha
  - Verified compatibility with all modern Blender APIs:
    - `PrincipledBSDFWrapper` for material handling
    - `mesh.loop_triangles` for mesh data
    - `evaluated_depsgraph_get()` for modifier evaluation
* **User Experience:**
  - Import/export status messages now appear in Blender's UI
  - Error and warning messages are user-friendly and actionable
  - Warning deduplication prevents UI spam with complex files
  - Console logs still available for detailed debugging
* **Developer Experience:**
  - Comprehensive test suite (142 unit tests + 16 integration tests)
  - Cross-platform integration test runners (Windows PowerShell, macOS/Linux Bash)
  - Multi-version testing support (test against all installed Blender versions)
  - Automated CI/CD testing via GitHub Actions
  - Complete type hints for IDE support and type checking
  - Clear public API with `__all__` exports
  - Updated documentation and contribution guidelines

Technical Improvements
----
* **Code Quality:**
  - Added comprehensive type hints to all 7 modules (100% coverage)
  - Replaced wildcard imports with explicit imports for better code maintainability
  - Converted all string concatenation to modern f-strings
  - Added `__all__` exports to all modules for clear public API definition
  - Removed outdated Python 3.7 references from code comments
* **Operator Improvements:**
  - Removed deprecated `__init__()` methods from operators (Blender 4.2+ requirement)
  - Fixed state variable initialization in export/import classes
  - Added `self.report()` calls for user-visible error/warning/info messages
  - Implemented warning deduplication to prevent UI spam on complex files
* **Error Handling:**
  - All errors now display in Blender's UI (not just console logs)
  - Warnings are deduplicated - each unique issue reported only once
  - Detailed logs still available in console for debugging
  - Better error messages for malformed 3MF files
* **Build System:**
  - Updated manifest format for modern Blender addon structure (blender_manifest.toml)
  - Fixed test mock objects for Python 3.11+ compatibility
  - Added warning deduplication tracker initialization in tests

Bug Fixes
----
* Fixed operator initialization for Blender 4.2+ compatibility
* Fixed material color handling with modern shader node API
* Fixed mesh triangulation with current API patterns
* Corrected depsgraph evaluation for objects with modifiers
* Fixed mock objects in unit tests to support `report()` method
* Added missing `_reported_warnings` initialization in test setup
* Resolved AttributeError issues in CI/CD test runs

Testing
----
* **Unit Tests (142 tests, all passing):**
  - All original unit tests updated and passing (Python 3.11)
  - Mock objects updated for modern operator interface
  - Tests verify type hints don't break runtime behavior
  - Code style validation with pycodestyle
* **Integration Tests (16 tests, all passing):**
  - New integration tests verify real-world Blender functionality
  - Test simple and complex geometry export/import
  - Verify material round-trip preservation
  - Test modifier evaluation
  - Confirm selection-only export
  - Validate Blender 4.2+ API compatibility
* **Cross-Platform Test Runners:**
  - PowerShell script for Windows
  - Bash script for macOS/Linux
  - Auto-detection of Blender installations
  - Multi-version testing support
* **CI/CD:**
  - GitHub Actions automatically run all 142 unit tests
  - Python 3.11 validation
  - Code style checks
* **Real-World Testing:**
  - Tested across Blender 4.2 LTS, 4.3, 4.5, and 5.0 Alpha
  - Verified export → import round-trip functionality
  - Confirmed material preservation through round-trip operations
  - Tested with complex multi-object 3MF files (50+ objects)
  - Verified warning deduplication with extension-heavy files

Contributors
----
* Modernization work by Clonephaze (Jack Smith)
* Original addon by Ghostkeeper

1.0.2 - Bug Fixes
====
* Fix support in newer Blender versions, up to 4.0.
* Run tests using Python 3.10.

1.0.1 - Bug Fixes
====
* Fix the resource ID of exported materials to be integer.

1.0.0 - Big Bang
====
For the first stable release, the full core 3MF specification is implemented.

Features
----
* Support for importing materials, and applying them to triangles of your meshes.
* Support for exporting materials from Blender with a diffuse color.
* Metadata is now retained when editing existing 3MF files.
* Relationships are retained when editing existing 3MF files.
* Content types are retained when editing existing 3MF files.
* Added support for the model types "solidsupport", "support" and "surface".
* Support and solidsupport meshes are hidden from any renders.
* 3MF part numbers are retained when editing existing 3MF files.
* Files marked as MustPreserve are retained when editing existing 3MF files.
* PrintTickets are retained when editing existing 3MF files.
* When metadata, relationships and content types clash when loading multiple 3MF files into one scene, the most common denominator is kept.
* Metadata, relationships, content types and part numbers are retained when the scene is shared through a .blend file.
* The object names are now stored in the 3MF files as metadata.
* Content types are now being read out, allowing for any file type to be anywhere in the archive.
* Automated tests improve stability of the add-on.
* Actions are being logged in Blender's log stream.
* If anything goes wrong, errors and warnings are being logged in Blender's log stream.
* The code is now compliant to Blender's code style requirements.
* Added support for new "Adaptive" units in Blender.
* Transformation matrices are written more compactly.
* Vertex coordinates are written more compactly.
* Warn the user if the 3MF document requires 3MF extensions that are not present.
* When exporting, you can now configure the number of decimals to write.
* Material colors are rendered in Blender with a BSDF node, and converted back to sRGB when exporting.
* The exported 3MF archive is now compressed with the Deflate algorithm.
* Allow installation via .zip file.

Bug Fixes
----
* No longer crash if faces are provided with negative vertex indices.
* Importing multiple 3MF files in succession no longer allows resource objects of old files to be used by new files.
* Exporting multiple 3MF files in succession resets the resource ID counter every time.
* No longer crash if there are no access rights to files to read or write.
* Fix writing of transformations for resource objects that have components.
* Fix writing transformations if multiple transformed objects are written.
* Resource objects that have components can no longer have mesh data of their own.
* No longer create meshes when an object has no vertices or faces.
* Transformation matrices and vertex coordinates will no longer use scientific notation for big or tiny numbers.

0.2.0 - Get Out
====
This is another pre-release where the goal is to implement exporting 3MF files from Blender.

Features
----
* A menu item is added to the export menu to export 3D Manufactoring Format files.
* Saving Open Document formatted archives.
* Support for exporting object resources.
* Support for exporting vertices.
* Support for exporting triangles.
* Support for exporting components.
* Support for exporting build items.
* Support for exporting transformations.
* Support for conversion from Blender's units to millimetres.
* You can now scale the models when importing and exporting.

Bug Fixes
----
* The unit is now applied after the 3MF file's own transformations, so that models end up in the correct position.

0.1.0 - Come On In
====
This is a minimum viable product release where the goal is to reliably import at least the geometry of a 3MF file into Blender.

Features
----
* A menu item is added to the import menu to import 3D Manufactoring Format files.
* Opening 3MF archives.
* Support for importing object resources.
* Support for importing vertices.
* Support for importing triangles.
* Support for importing components.
* Support for importing build items.
* Support for transformations on build items and components.
* Transforming the 3MF file units correctly to Blender's units.
