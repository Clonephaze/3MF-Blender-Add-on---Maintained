"""
Unit tests for ``io_mesh_3mf.common.logging``.

Tests debug/warn/error output gating and safe_report fallback behaviour.
Pure Python — no bpy or mathutils required.
"""

import io
import unittest
from unittest.mock import patch

import io_mesh_3mf.common.logging as log_mod
from io_mesh_3mf.common.logging import debug, warn, error, safe_report


# ============================================================================
# debug()
# ============================================================================


class TestDebug(unittest.TestCase):
    """debug() should only print when DEBUG_MODE is True."""

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_prints_when_enabled(self, mock_stdout):
        original = log_mod.DEBUG_MODE
        try:
            log_mod.DEBUG_MODE = True
            debug("hello")
            self.assertIn("hello", mock_stdout.getvalue())
        finally:
            log_mod.DEBUG_MODE = original

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_silent_when_disabled(self, mock_stdout):
        original = log_mod.DEBUG_MODE
        try:
            log_mod.DEBUG_MODE = False
            debug("secret")
            self.assertEqual(mock_stdout.getvalue(), "")
        finally:
            log_mod.DEBUG_MODE = original

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_debug_multiple_args(self, mock_stdout):
        original = log_mod.DEBUG_MODE
        try:
            log_mod.DEBUG_MODE = True
            debug("a", "b", "c")
            self.assertIn("a b c", mock_stdout.getvalue())
        finally:
            log_mod.DEBUG_MODE = original


# ============================================================================
# warn()
# ============================================================================


class TestWarn(unittest.TestCase):
    """warn() always prints with WARNING: prefix."""

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_warn_always_prints(self, mock_stdout):
        original = log_mod.DEBUG_MODE
        try:
            log_mod.DEBUG_MODE = False
            warn("trouble")
            output = mock_stdout.getvalue()
            self.assertIn("WARNING:", output)
            self.assertIn("trouble", output)
        finally:
            log_mod.DEBUG_MODE = original

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_warn_prefix(self, mock_stdout):
        warn("something wrong")
        self.assertTrue(mock_stdout.getvalue().startswith("WARNING:"))


# ============================================================================
# error()
# ============================================================================


class TestError(unittest.TestCase):
    """error() always prints with ERROR: prefix."""

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_error_always_prints(self, mock_stdout):
        original = log_mod.DEBUG_MODE
        try:
            log_mod.DEBUG_MODE = False
            error("fatal")
            output = mock_stdout.getvalue()
            self.assertIn("ERROR:", output)
            self.assertIn("fatal", output)
        finally:
            log_mod.DEBUG_MODE = original

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_error_prefix(self, mock_stdout):
        error("kaboom")
        self.assertTrue(mock_stdout.getvalue().startswith("ERROR:"))


# ============================================================================
# safe_report()
# ============================================================================


class _FakeOperator:
    """Minimal operator stub with a report() method."""

    def __init__(self):
        self.reports = []

    def report(self, level, message):
        self.reports.append((level, message))


class _BrokenOperator:
    """Operator stub whose report() always raises."""

    def report(self, level, message):
        raise RuntimeError("No UI context")


class TestSafeReport(unittest.TestCase):
    """safe_report() delegates to operator.report() or falls back gracefully."""

    def test_delegates_to_operator(self):
        op = _FakeOperator()
        safe_report(op, {"INFO"}, "done")
        self.assertEqual(len(op.reports), 1)
        self.assertEqual(op.reports[0], ({"INFO"}, "done"))

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_fallback_error(self, mock_stdout):
        op = _BrokenOperator()
        safe_report(op, {"ERROR"}, "oops")
        self.assertIn("ERROR:", mock_stdout.getvalue())

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_fallback_warning(self, mock_stdout):
        op = _BrokenOperator()
        safe_report(op, {"WARNING"}, "hmm")
        self.assertIn("WARNING:", mock_stdout.getvalue())

    @patch("sys.stdout", new_callable=io.StringIO)
    def test_fallback_info(self, mock_stdout):
        """INFO fallback prints via debug() — only visible when DEBUG_MODE."""
        op = _BrokenOperator()
        original = log_mod.DEBUG_MODE
        try:
            log_mod.DEBUG_MODE = True
            safe_report(op, {"INFO"}, "just a note")
            self.assertIn("just a note", mock_stdout.getvalue())
        finally:
            log_mod.DEBUG_MODE = original

    def test_none_operator_does_not_crash(self):
        """Passing None as operator should not raise."""
        # None has no .report(), so the try block raises AttributeError,
        # which is caught and we fall back to console output.
        safe_report(None, {"ERROR"}, "no context")


if __name__ == "__main__":
    unittest.main()
