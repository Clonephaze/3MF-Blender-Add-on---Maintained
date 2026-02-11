"""
Test runner for Blender 3MF addon unit test suite.

Unit tests verify individual functions/classes inside Blender's real
Python environment — no mocking, no fakes.  All tests import the real
``io_mesh_3mf`` package; modules that need ``bpy`` / ``mathutils`` get
them for free because we run inside Blender.

Run with:
    blender --background --factory-startup --python-exit-code 1 \
            -noaudio -q --python tests/run_unit_tests.py

Or with a pattern filter:
    blender --background --factory-startup --python-exit-code 1 \
            -noaudio -q --python tests/run_unit_tests.py -- test_colors
"""

import sys
import unittest
from pathlib import Path

# Add project root to path so ``import io_mesh_3mf`` works.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Add unit tests directory so test modules can be discovered.
TESTS_DIR = Path(__file__).parent
UNIT_DIR = TESTS_DIR / "unit"
sys.path.insert(0, str(UNIT_DIR))

# Parse command line args for test filtering (after "--")
pattern = "test_*.py"
if len(sys.argv) > 1 and "--" in sys.argv:
    idx = sys.argv.index("--")
    if len(sys.argv) > idx + 1:
        arg = sys.argv[idx + 1]
        pattern = arg if arg.endswith(".py") else arg + ".py"

print(f"Discovering unit tests matching: {pattern}")

# Discover and run tests
if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(str(UNIT_DIR), pattern=pattern)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print(f"ALL UNIT TESTS PASSED: {result.testsRun} tests")
    else:
        print("UNIT TESTS FAILED")
        print(f"   Ran: {result.testsRun}")
        print(f"   Failures: {len(result.failures)}")
        print(f"   Errors: {len(result.errors)}")
    print("=" * 70)

    sys.exit(0 if result.wasSuccessful() else 1)
