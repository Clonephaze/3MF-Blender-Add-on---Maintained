Core API
========

The public API lives in :mod:`io_mesh_3mf.api`.  All functions return
lightweight result dataclasses — they never raise exceptions for normal
failures (corrupt files, empty scenes, etc.).

.. module:: io_mesh_3mf.api
   :synopsis: Programmatic 3MF import, export, and inspection.

Version & Capabilities
----------------------

.. autodata:: API_VERSION
   :annotation:

.. autodata:: API_VERSION_STRING
   :annotation:

.. autodata:: API_CAPABILITIES
   :annotation:

Result Dataclasses
------------------

.. autoclass:: ImportResult
   :members:
   :no-undoc-members:

.. autoclass:: ExportResult
   :members:
   :no-undoc-members:

.. autoclass:: InspectResult
   :members:
   :no-undoc-members:

Import
------

.. autofunction:: import_3mf

Export
------

.. autofunction:: export_3mf

Inspect
-------

.. autofunction:: inspect_3mf

Batch Operations
----------------

.. autofunction:: batch_import

.. autofunction:: batch_export

Callback Types
--------------

The following type aliases describe the callback signatures accepted by
the import/export functions:

``ProgressCallback``
   ``Callable[[int, str], None]`` — receives ``(percentage, message)``.

``WarningCallback``
   ``Callable[[str], None]`` — receives a warning message string.

``ObjectCreatedCallback``
   ``Callable[[Any, str], None]`` — receives ``(blender_object, resource_id)``.
