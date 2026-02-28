Getting Started
===============

The public API in :mod:`io_mesh_3mf.api` provides headless/programmatic access
to the full 3MF pipeline.  It runs the same code as the Blender operators but
skips UI-specific behaviour (progress bars, popups, camera zoom), making it
suitable for:

- **CLI automation** — batch processing from Blender's ``--python`` mode
- **Addon integration** — other Blender addons importing/exporting 3MF
- **Headless pipelines** — render farms, CI/CD, asset processing
- **Custom workflows** — building on top of the low-level building blocks


Quick Start
-----------

.. code-block:: python

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

All functions return lightweight dataclasses — they never raise exceptions for
normal failures (corrupt files, empty scenes, etc.).  Check ``result.status``
instead.


Export Format Reference
-----------------------

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - ``use_orca_format``
     - ``mmu_slicer_format``
     - Output
   * - ``"AUTO"``
     - —
     - Chooses best format based on scene content
   * - ``"STANDARD"``
     - —
     - Spec-compliant single-model 3MF
   * - ``"PAINT"``
     - ``"ORCA"``
     - Multi-file Orca/Bambu structure with ``paint_color`` attributes
   * - ``"PAINT"``
     - ``"PRUSA"``
     - Single-file with ``slic3rpe:mmu_segmentation`` hash strings

In **AUTO** mode the addon inspects your scene and picks the best path:

- Objects with MMU paint textures → Orca exporter with segmentation
- Objects with material slots → Standard exporter with basematerials/colorgroups
- Geometry-only objects → Standard exporter, geometry only
- If *project_template* or *object_settings* is provided → Orca exporter


Callbacks
---------

All three callback types are optional and work the same way across
:func:`~io_mesh_3mf.api.import_3mf`, :func:`~io_mesh_3mf.api.export_3mf`, and
the batch helpers.

.. code-block:: python

   def on_progress(percentage: int, message: str):
       """Called with 0-100 percentage and a status message."""
       print(f"[{percentage:3d}%] {message}")

   def on_warning(message: str):
       """Called for each warning (non-manifold geometry, missing data, etc.)."""
       print(f"WARNING: {message}")

   def on_object_created(blender_object, resource_id: str):
       """Called after each Blender object is built during import."""
       blender_object.color = (1, 0, 0, 1)  # Tint red


Error Handling
--------------

All API functions return result dataclasses instead of raising exceptions.
Check ``result.status``:

.. code-block:: python

   result = import_3mf("model.3mf")
   if result.status == "FINISHED":
       print(f"Success: {result.num_loaded} objects")
   else:
       print(f"Failed: {result.warnings}")

- Archive-level errors (corrupt ZIP, missing model files) set
  ``status = "CANCELLED"``.
- Per-object warnings (non-manifold geometry, missing textures) are collected in
  ``warnings`` but don't prevent completion.
- :func:`~io_mesh_3mf.api.inspect_3mf` uses ``status = "OK"`` / ``"ERROR"``
  with a separate ``error_message`` field.


CLI Usage
---------

Run from the command line using Blender's ``--python`` flag:

.. code-block:: bash

   # Inspect a file
   blender --background --python-expr "
   from io_mesh_3mf.api import inspect_3mf
   info = inspect_3mf('model.3mf')
   print(f'{info.num_objects} objects, {info.num_triangles_total} triangles')
   "

   # Batch convert
   blender --background --python my_script.py

**Example script** (``convert_to_orca.py``):

.. code-block:: python

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
           use_orca_format="AUTO",
       )
       print(f"Converted: {export_result.num_written} objects → {output_path}")

.. code-block:: bash

   blender --background --python convert_to_orca.py -- input.3mf


Notes
-----

- **Blender context required** — :func:`~io_mesh_3mf.api.import_3mf` and
  :func:`~io_mesh_3mf.api.export_3mf` need ``bpy.context``.  They work in
  ``--background`` mode but not outside Blender entirely.
- **inspect_3mf is lightweight** — it only opens the ZIP and parses XML.
  No Blender objects, materials, or images are created.
- **Thread safety** — Blender's Python API is not thread-safe.  Don't call
  these functions from background threads.
- **Batch isolation** — :func:`~io_mesh_3mf.api.batch_import` and
  :func:`~io_mesh_3mf.api.batch_export` catch per-file exceptions so one
  failure doesn't stop the batch.
- **API vs addon version** — ``API_VERSION`` tracks the API contract stability.
  It increments independently of the addon release version.
