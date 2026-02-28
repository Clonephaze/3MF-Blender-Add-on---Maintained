API Discovery
=============

Other Blender addons can discover and feature-check the 3MF API without
hard-importing it.  The addon registers itself in
``bpy.app.driver_namespace["io_mesh_3mf"]`` on startup.

Direct Discovery (from ``api``)
-------------------------------

.. autofunction:: io_mesh_3mf.api.is_available

.. autofunction:: io_mesh_3mf.api.get_api

.. autofunction:: io_mesh_3mf.api.has_capability

.. autofunction:: io_mesh_3mf.api.check_version

Standalone Discovery Helper
----------------------------

For addons that want **zero runtime dependency** on the 3MF addon, copy
``io_mesh_3mf/threemf_discovery.py`` into your addon.

.. automodule:: io_mesh_3mf.threemf_discovery
   :members:
   :undoc-members:
   :exclude-members: import_3mf, export_3mf, inspect_3mf
