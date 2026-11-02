Orca / Bambu Mixed-Filament Authoring
=====================================

The add-on can read and write OrcaSlicer/BambuStudio per-slot mixed-filament
settings. The editor works with the project's filament slot table and stores
changes in the stashed ``Metadata/project_settings.config``. This page
documents the implemented Blender controls and the export limitations.

Open the **3MF** tab in the 3D Viewport sidebar. In Object Mode, the standalone
**Mixed Filaments** panel exposes the slot editor. In Texture Paint mode, the
same editor is available as **Mix Colors** under **MMU Paint** when the active
mesh has an MMU paint texture.

Slot editor
-----------

The list displays slots in project order, labels mixed slots, and shows the
current mixed-slot color. Physical filaments can be added, edited, and removed.
Adding a physical filament inserts it before the mixed-slot tail. Removing a
physical slot requires a replacement physical slot; mixed recipes and scene
slot references are remapped during the removal.

Use **Add Mix** to create a two-component recipe. Select an existing mixed
slot's edit control to change it, including switching between two and three
components. Each component must refer to a physical slot. Ratios must total
100 percent and each component must be at least 10 percent. The editor rejects
known mismatches between component filament types.

An identical existing non-gradient recipe is reused instead of adding a
duplicate slot. Mixed colors are derived from the component colors; editing a
physical filament refreshes the derived swatches and paint palettes.

Gradients
---------

Enable **Gradient** on a two-component mixed slot, then set the start and end
ratios and, optionally, custom curve anchors. The curve field accepts
``x,y[,incoming_tangent,outgoing_tangent]|...`` segments. For example::

   0.0000,0.9000|0.5000,0.5000|1.0000,0.1000

The dialog previews five sampled colors from 0% through 100%. The curve is
edited as serialized text; this editor does not provide draggable graph
anchors. The **Per Part** option is stored with the gradient settings.

Color recommendations
---------------------

Choose **Recommend from Color** to search either the project's physical
filaments or the CMYW/RYBW standard recipe data. The operation uses the target
color and material type entered in the dialog. For standard recipe modes,
**Add Missing Base Filaments** can add recipe base colors that are not already
available as compatible physical slots. If a target resolves directly to a
physical slot, no mixed slot is created.

Object assignment and painting
------------------------------

Select an object and use the assignment control on a slot row to store its
one-based project filament slot. In Texture Paint mode, the mixed slots appear
in the MMU paint palette for a mesh with an MMU paint texture. Select a mixed
swatch to use that slot's paint color.

The paint palette uses collision-safe internal colors when two project slots
have the same displayed hex color. These internal colors are for distinguishing
painted regions; the project settings keep the predicted slot colors.

Export and compatibility
------------------------

Use **AUTO** or the Orca **PAINT** export path to retain Orca/Bambu mixed-slot
definitions. AUTO detects mixed slots in the stashed project settings and
selects Orca export. Explicit **STANDARD** and **PRUSA** exports cannot encode
these Orca-specific definitions and report a warning; do not use those paths
when the mixed definitions must be preserved.

The add-on does not support OrcaSlicer-FullSpectrum's
``mixed_filament_definitions`` format and does not convert it to the per-slot
format. FullSpectrum virtual-slot references fall back to filament 1 with a
warning, and FullSpectrum-only settings are removed on export.

For manual validation in a slicer, see
``MANUAL_SLICER_VALIDATION.md`` in the repository root.
