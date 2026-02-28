Recipes
=======

Practical patterns for common workflows.


Import with Material Painting
-----------------------------

.. code-block:: python

   from io_mesh_3mf.api import import_3mf

   result = import_3mf(
       "/models/multicolor.3mf",
       import_materials="PAINT",
       import_location="ORIGIN",
   )

   for obj in result.objects:
       print(f"  {obj.name}: {len(obj.data.vertices)} verts")


Import into a Specific Collection
----------------------------------

.. code-block:: python

   result = import_3mf(
       "/models/part.3mf",
       target_collection="Imported Parts",
       reuse_materials=True,
   )


Export for Orca Slicer
----------------------

The export dispatch uses a three-way mode: ``AUTO``, ``STANDARD``, or ``PAINT``.

- **AUTO** (default) — detects materials and paint data, choosing the best
  exporter automatically.
- **STANDARD** — always uses the spec-compliant StandardExporter.
- **PAINT** — forces segmentation export for multi-material painting.

.. code-block:: python

   from io_mesh_3mf.api import export_3mf
   import bpy

   cubes = [o for o in bpy.data.objects if o.type == "MESH" and "Cube" in o.name]

   result = export_3mf(
       "/output/cubes.3mf",
       objects=cubes,
       use_orca_format="AUTO",
   )
   print(f"Exported {result.num_written} objects")


Export for PrusaSlicer with MMU Paint
-------------------------------------

.. code-block:: python

   result = export_3mf(
       "/output/painted.3mf",
       use_orca_format="PAINT",
       mmu_slicer_format="PRUSA",
       use_selection=True,
   )


Custom Orca Project Template
-----------------------------

Use a custom printer/filament profile extracted from Orca Slicer:

.. code-block:: python

   result = export_3mf(
       "/output/custom_printer.3mf",
       use_orca_format="PAINT",
       mmu_slicer_format="ORCA",
       project_template="/templates/bambu_x1c_asa.json",
       object_settings={
           supports_obj: {
               "layer_height": "0.12",
               "wall_loops": "2",
               "sparse_infill_density": "10%",
           },
           detail_part: {
               "layer_height": "0.08",
               "outer_wall_speed": "50",
           },
       },
   )

.. tip::

   **Getting custom templates:** Export a project from Orca Slicer as ``.3mf``,
   open the archive with a ZIP tool, and extract
   ``Metadata/project_settings.config``.  This JSON file contains all printer,
   filament, and print settings.  The addon patches ``filament_colour``
   automatically based on your painted objects.


Round-Trip Conversion
---------------------

.. code-block:: python

   from io_mesh_3mf.api import import_3mf, export_3mf

   # Import from one format, export to another
   result = import_3mf("/input/prusa_model.3mf", import_materials="PAINT")
   if result.status == "FINISHED":
       export_3mf(
           "/output/orca_model.3mf",
           objects=result.objects,
           use_orca_format="PAINT",
           mmu_slicer_format="ORCA",
       )


Inspect Without Importing
--------------------------

.. code-block:: python

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
   else:
       print(f"Error: {info.error_message}")


Batch Operations
----------------

.. code-block:: python

   from io_mesh_3mf.api import batch_import, batch_export
   import bpy

   # Import multiple files with per-file error isolation
   results = batch_import(
       ["part_a.3mf", "part_b.3mf", "part_c.3mf"],
       import_materials="PAINT",
       target_collection="Batch Import",
   )

   total = sum(r.num_loaded for r in results)
   failed = [r for r in results if r.status != "FINISHED"]
   print(f"Imported {total} objects, {len(failed)} failures")

   # Export multiple files
   cubes = [o for o in bpy.data.objects if "Cube" in o.name]
   spheres = [o for o in bpy.data.objects if "Sphere" in o.name]

   results = batch_export(
       [
           ("cubes.3mf", cubes),
           ("spheres.3mf", spheres),
           ("everything.3mf", None),  # None = all scene objects
       ],
       use_orca_format="AUTO",
   )
