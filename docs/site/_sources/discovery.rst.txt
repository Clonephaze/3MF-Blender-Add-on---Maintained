API Discovery
=============

Other Blender addons can detect and use the 3MF API at runtime.  Three
strategies are available, from simplest to most robust.

Direct Import (recommended)
---------------------------

If you know the addon is installed, a plain ``try``/``except`` is the
simplest and most Pythonic approach::

    try:
        from io_mesh_3mf.api import import_3mf, export_3mf
    except ImportError:
        import_3mf = export_3mf = None

Discovery Functions (from ``api``)
----------------------------------

.. autofunction:: io_mesh_3mf.api.is_available

.. autofunction:: io_mesh_3mf.api.get_api

.. autofunction:: io_mesh_3mf.api.has_capability

.. autofunction:: io_mesh_3mf.api.check_version

Standalone Discovery Helper
----------------------------

For addons that want **zero runtime dependency** on the 3MF addon, copy
``io_mesh_3mf/threemf_discovery.py`` into your addon.  It resolves the
addon's import path automatically via ``addon_utils``, caches the result,
and works regardless of extension repo prefix or addon load order.

.. automodule:: io_mesh_3mf.threemf_discovery
   :members:
   :undoc-members:
   :exclude-members: import_3mf, export_3mf, inspect_3mf
