"""
Test utilities and base classes for Blender 3MF addon tests.

All tests run inside real Blender (``--background --factory-startup``).
No mocking — every ``bpy`` object is the real thing.

Run integration tests:
    blender --background --factory-startup --python-exit-code 1 \
            -noaudio -q --python tests/run_tests.py
"""

import bpy
import unittest
import tempfile
import shutil
from pathlib import Path


# ============================================================================
# Global Test Configuration
# ============================================================================

_temp_test_dir = None
_test_resources_dir = Path(__file__).parent.parent / "resources"


def get_temp_test_dir():
    """Get or create shared temporary directory for tests."""
    global _temp_test_dir
    if _temp_test_dir is None:
        _temp_test_dir = Path(tempfile.mkdtemp(prefix="3mf_test_"))
        print(f"[test utils] Test output directory: {_temp_test_dir}")
    return _temp_test_dir


def get_test_resources_dir():
    """Get test resources directory."""
    return _test_resources_dir


def cleanup_temp_dir():
    """Clean up temporary test directory."""
    global _temp_test_dir
    if _temp_test_dir and _temp_test_dir.exists():
        shutil.rmtree(_temp_test_dir)
        print("[test utils] Cleaned up test directory")
        _temp_test_dir = None


# ============================================================================
# Base Test Classes
# ============================================================================

class Blender3mfTestCase(unittest.TestCase):
    """Base test case for Blender 3MF addon integration tests.

    Registers the addon once (``setUpClass``), resets the scene before and
    after every test, and provides helper methods for materials and temp files.
    """

    @classmethod
    def setUpClass(cls):
        """Register the addon once for the entire test class."""
        import io_mesh_3mf
        try:
            io_mesh_3mf.register()
        except ValueError as e:
            # ``register()`` raises if already registered — that's fine.
            if "already registered" not in str(e):
                raise
        print(f"[{cls.__name__}] Addon registered")

    def setUp(self):
        """Clean scene and create a unique temp file path."""
        self.clean_scene()
        import uuid
        self.temp_file = get_temp_test_dir() / f"test_{uuid.uuid4().hex[:8]}.3mf"
        self.test_resources_dir = get_test_resources_dir()

    def tearDown(self):
        self.clean_scene()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def clean_scene(self):
        """Reset Blender to an empty state."""
        bpy.ops.wm.read_homefile(use_empty=True)
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        for mesh in bpy.data.meshes:
            bpy.data.meshes.remove(mesh)
        for material in bpy.data.materials:
            bpy.data.materials.remove(material)

    def create_red_material(self):
        """Create and return a red Principled BSDF material."""
        mat = bpy.data.materials.new(name="RedMaterial")
        mat.use_nodes = True
        principled = mat.node_tree.nodes.get("Principled BSDF")
        if principled:
            principled.inputs["Base Color"].default_value = (1.0, 0.0, 0.0, 1.0)
        return mat

    def create_blue_material(self):
        """Create and return a blue Principled BSDF material."""
        mat = bpy.data.materials.new(name="BlueMaterial")
        mat.use_nodes = True
        principled = mat.node_tree.nodes.get("Principled BSDF")
        if principled:
            principled.inputs["Base Color"].default_value = (0.0, 0.0, 1.0, 1.0)
        return mat
