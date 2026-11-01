# Public API Documentation

Full documentation lives in [docs/site/](docs/site/index.html) — open `docs/site/index.html` in a browser.

If you change the API or docstrings, rebuild with:

```powershell
docs/build.ps1          # or: docs/build.ps1 -Clean
```

The docs include:

- **Getting Started** — overview, quick start, export format reference, modifier parts, callbacks, CLI usage
- **Recipes** — ready-to-use patterns (import, export, round-trip, batch, inspect)
- **Core API Reference** — auto-generated parameter docs for `import_3mf`, `export_3mf`, `inspect_3mf`, and batch helpers
- **API Discovery** — version checking, capability detection, standalone helper module
- **Building Blocks** — colors, units, types, segmentation, extensions, metadata

### Quick example

```python
from io_mesh_3mf.api import import_3mf, export_3mf, inspect_3mf

result = import_3mf("model.3mf")
result = export_3mf("output.3mf", use_selection=True)
info   = inspect_3mf("model.3mf")
```

### Assemblies and plates (Orca/Bambu)

The public API exposes `assemblies`, `multi_plate`, and `plate_per_material`
capability flags as of API 1.4.0. These custom properties are stored on
**objects**, not mesh datablocks:

| Property | Meaning |
| --- | --- |
| `3mf_assembly` | Non-empty string. Mesh objects with the same name form one assembly; this overrides an Empty parent. Otherwise the nearest Empty ancestor defines the assembly, provided that Empty is exported (with Selection Only, select the Empty or one of its parents; selecting only its children exports them as standalone objects). |
| `3mf_plate` | One-based plate number, 1–36. An assembly Empty's value wins over member values; otherwise the first member's value wins. Conflicts produce a warning. |
| `3mf_plate_name` | Optional name for the object's assigned plate. Conflicting names produce a warning and the first name is used. |
| `3mf_material_group` | Non-empty string overriding the material key used by Plate per Material. |

```python
from io_mesh_3mf.api import export_3mf, has_capability

if has_capability("plate_per_material"):
    body["3mf_assembly"] = "Widget"
    cap["3mf_assembly"] = "Widget"
    body["3mf_plate"] = 2  # explicit assignments override automatic grouping
    result = export_3mf(
        "widget.3mf",
        objects=[body, cap],
        plate_per_material=True,
        split_assemblies_by_material=True,
    )
    print(result.status, result.warnings)
```

`plate_per_material` defaults to `False`. When enabled, matching materials
share a plate across assemblies. Unassigned objects join the first explicit
plate for the same material, or the first free plate; explicit assignments can
deliberately put different materials together. With
`split_assemblies_by_material=True` (the default), a mixed assembly becomes
one subassembly per material while retaining its assembled positions. Setting
it to `False` keeps the mixed assembly together.

Material grouping applies to **whole objects**: it uses the dominant face
material color or stored default paint extruder, unless `3mf_material_group`
overrides it. It does not divide a single mesh's different face materials or
painted regions into separate objects. The exporter preserves relative XY
placement rather than packing parts; use the slicer's Arrange function if
parts overlap. Oversized plates, invalid assignments, and requests exceeding
the 36-plate limit produce warnings. Values above 36 are clamped, and excess
automatic material groups share plate 36.

In `AUTO` mode, plate properties, `plate_per_material`, or a
`3mf_assembly` property group select the Orca/Bambu exporter, even without
materials. Explicit Standard and Prusa exports warn and ignore plate settings
and property-defined assemblies.

On import, Orca/Bambu `model_instance` entries restore plate numbers and
names, and grid offsets are removed using the saved bed dimensions. Files
with a single plate do not set `3mf_plate`, so re-exporting them is
not treated as a plate request; a named single plate keeps only
`3mf_plate_name`, which Orca/Bambu export applies to plate 1. If the
bed dimensions are unavailable, the importer warns and leaves global
positions unchanged. Saved assembled positions are retained separately in
the internal `3mf_assemble_transform` property; leave it in place for a
round trip, or remove it to use the current Blender placement instead.
Import relies on the saved plate membership entries, which may differ from
the membership a slicer rebuilds from geometry positions. Repeated build
instances import as linked meshes, but re-export currently writes separate
object wrappers rather than preserving the original instance IDs.
