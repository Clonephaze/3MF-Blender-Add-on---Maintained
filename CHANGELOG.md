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
* Export_3mf has been refactored into 3 separated files for better maintainability:

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
