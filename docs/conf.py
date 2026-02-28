# Sphinx configuration for 3MF Format API reference
# Build with: sphinx-build -b html -d docs/_build/doctrees docs docs/site
# Or use:     docs/build.ps1

import sys
import os
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Mock Blender modules so Sphinx can import io_mesh_3mf outside Blender
# ---------------------------------------------------------------------------

class _BlenderMock(MagicMock):
    """A MagicMock that also acts as a module with arbitrary nested attrs."""

    @classmethod
    def __getattr__(cls, name):
        return MagicMock()


MOCK_MODULES = [
    "bpy",
    "bpy.app",
    "bpy.app.handlers",
    "bpy.ops",
    "bpy.props",
    "bpy.types",
    "bpy.utils",
    "bpy_extras",
    "bpy_extras.io_utils",
    "bpy_extras.node_shader_utils",
    "bl_operators",
    "bl_operators.presets",
    "bmesh",
    "mathutils",
    "idprop",
    "idprop.types",
]

for mod_name in MOCK_MODULES:
    sys.modules[mod_name] = _BlenderMock()


# Blender classes used as base classes in the addon.  Python requires real
# classes (not MagicMock instances) when listed in a class's bases, otherwise
# the metaclass resolution between two MagicMock bases will fail.
# Each stand-in must be a *unique* class so Python doesn't complain about
# duplicate base classes (e.g. ``class Foo(Operator, ImportHelper)``).

def _make_base(name: str) -> type:
    """Return a unique empty class with the given name."""
    return type(name, (), {})


# bpy.types.*
_bpy_types = sys.modules["bpy.types"]
for _name in (
    "Operator", "Panel", "UIList", "PropertyGroup",
    "FileHandler", "AddonPreferences",
):
    setattr(_bpy_types, _name, _make_base(_name))

# Menu needs a draw_preset attribute (used by EXPORT_MT_threemf_presets)
_Menu = _make_base("Menu")
_Menu.draw_preset = lambda self, context: None
_bpy_types.Menu = _Menu

# Also set on the parent so `bpy.types.X` resolves either way
_bpy = sys.modules["bpy"]
_bpy.types = _bpy_types

# bpy_extras.io_utils.*
_io_utils = sys.modules["bpy_extras.io_utils"]
_io_utils.ImportHelper = _make_base("ImportHelper")
_io_utils.ExportHelper = _make_base("ExportHelper")
sys.modules["bpy_extras"].io_utils = _io_utils

# bpy_extras.node_shader_utils (keep as mock, nothing inherits from it)
sys.modules["bpy_extras"].node_shader_utils = sys.modules["bpy_extras.node_shader_utils"]

# bl_operators.presets.*
_presets = sys.modules["bl_operators.presets"]
_presets.AddPresetBase = _make_base("AddPresetBase")
sys.modules["bl_operators"].presets = _presets

# Wire up bpy sub-modules so attribute access matches sys.modules lookups
_bpy.app = sys.modules["bpy.app"]
_bpy.app.handlers = sys.modules["bpy.app.handlers"]
_bpy.ops = sys.modules["bpy.ops"]
_bpy.props = sys.modules["bpy.props"]
_bpy.utils = sys.modules["bpy.utils"]

# Make bpy.app.version return a tuple so version checks don't crash
_bpy.app.version = (5, 0, 0)
_bpy.app.driver_namespace = {}

# Add the project root to sys.path so `import io_mesh_3mf` works
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ---------------------------------------------------------------------------
# Project info
# ---------------------------------------------------------------------------

project = "3MF Format for Blender"
copyright = "2025, Jack"
author = "Jack"

# Pull version from the api module
try:
    from io_mesh_3mf.api import API_VERSION_STRING
    release = API_VERSION_STRING
except Exception:
    release = "1.0.0"

version = release

# ---------------------------------------------------------------------------
# Extensions
# ---------------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",          # Google/NumPy docstring support
    "sphinx.ext.viewcode",          # [source] links
    "sphinx.ext.intersphinx",       # Cross-ref to Python stdlib docs
]

# ---------------------------------------------------------------------------
# Autodoc settings
# ---------------------------------------------------------------------------

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
}

# Don't show the full module path for every class/function
add_module_names = False

# Type hints in the signature, not repeated in the body
autodoc_typehints = "signature"

# Napoleon settings (for Google/NumPy style docstrings)
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_use_param = True
napoleon_use_rtype = True

# ---------------------------------------------------------------------------
# Intersphinx
# ---------------------------------------------------------------------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "navigation_depth": 3,
    "collapse_navigation": False,
    "sticky_navigation": True,
    "style_nav_header_background": "#2b2b2b",
}

html_title = f"3MF Format API — v{release}"
html_short_title = "3MF API"
html_show_sourcelink = False

# Output directory (relative to docs/)
# Build creates docs/_build/html/
