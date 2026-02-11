"""
Test runner for Blender 3MF addon integration test suite.

Integration tests exercise full import/export round-trips, operator
registration, and material handling inside Blender's real environment.
The addon is registered once in the base test class ``setUpClass``.

Run with:
    blender --background --factory-startup --python-exit-code 1 \
            -noaudio -q --python tests/run_tests.py

Or with a pattern filter:
    blender --background --factory-startup --python-exit-code 1 \
            -noaudio -q --python tests/run_tests.py -- test_export
"""

import sys
import unittest
from pathlib import Path

# Add project root to path so ``import io_mesh_3mf`` works.
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Add tests directory to path for ``from test_base import ...`` in test files.
TESTS_DIR = Path(__file__).parent
INTEGRATION_DIR = TESTS_DIR / "integration"
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(INTEGRATION_DIR))

from integration.test_base import cleanup_temp_dir

# Parse command line args for test filtering (after "--")
pattern = "test_*.py"
if len(sys.argv) > 1 and "--" in sys.argv:
    idx = sys.argv.index("--")
    if len(sys.argv) > idx + 1:
        arg = sys.argv[idx + 1]
        pattern = arg if arg.endswith(".py") else arg + ".py"

print(f"Discovering integration tests matching: {pattern}")

# Discover and run tests
if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(str(INTEGRATION_DIR), pattern=pattern)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    cleanup_temp_dir()

    # Summary
    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print(f"ALL INTEGRATION TESTS PASSED: {result.testsRun} tests")
    else:
        print("INTEGRATION TESTS FAILED")
        print(f"   Ran: {result.testsRun}")
        print(f"   Failures: {len(result.failures)}")
        print(f"   Errors: {len(result.errors)}")
    print("=" * 70)

    sys.exit(0 if result.wasSuccessful() else 1)
