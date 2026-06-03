"""
Unit tests for ``io_mesh_3mf.export_3mf.context``.

Tests ExportOptions and ExportContext dataclass defaults and field types.
"""

import types
import unittest
from io_mesh_3mf.export_3mf.context import ExportOptions, ExportContext


class TestExportOptionsDefaults(unittest.TestCase):
    """ExportOptions defaults are correct."""

    def test_thumbnail_mode_default(self):
        opts = ExportOptions()
        self.assertEqual(opts.thumbnail_mode, "AUTO")

    def test_thumbnail_resolution_default(self):
        opts = ExportOptions()
        self.assertEqual(opts.thumbnail_resolution, 256)

    def test_thumbnail_image_default(self):
        opts = ExportOptions()
        self.assertEqual(opts.thumbnail_image, "")

    def test_all_thumbnail_modes_accepted(self):
        for mode in ("AUTO", "CUSTOM", "NONE"):
            opts = ExportOptions(thumbnail_mode=mode)
            self.assertEqual(opts.thumbnail_mode, mode)

    def test_custom_resolution(self):
        opts = ExportOptions(thumbnail_resolution=512)
        self.assertEqual(opts.thumbnail_resolution, 512)

    def test_custom_image_path(self):
        opts = ExportOptions(thumbnail_image="/some/path.png")
        self.assertEqual(opts.thumbnail_image, "/some/path.png")


class TestExportContextDefaults(unittest.TestCase):
    """ExportContext creates with sane defaults."""

    def test_default_options(self):
        ctx = ExportContext()
        self.assertIsInstance(ctx.options, ExportOptions)
        self.assertEqual(ctx.options.thumbnail_mode, "AUTO")

    def test_options_pass_through(self):
        opts = ExportOptions(thumbnail_mode="NONE", thumbnail_resolution=128)
        ctx = ExportContext(options=opts)
        self.assertEqual(ctx.options.thumbnail_mode, "NONE")
        self.assertEqual(ctx.options.thumbnail_resolution, 128)


def _make_ctx_with_progress_context():
    """Return an ExportContext wired to a minimal fake Blender context."""
    fake_wm = types.SimpleNamespace(
        progress_update=lambda v: None,
        status_text_set=lambda msg: None,
    )
    fake_context = types.SimpleNamespace(window_manager=fake_wm)
    ctx = ExportContext()
    ctx._progress_context = fake_context
    return ctx


class TestProgressUpdate(unittest.TestCase):
    """_progress_update() signature and behaviour (regression: Issue #33)."""

    def test_accepts_value_only(self):
        """Must not raise when called with just a value."""
        ctx = _make_ctx_with_progress_context()
        ctx._progress_update(50)  # no TypeError

    def test_accepts_value_and_message(self):
        """Must not raise when called with value and message."""
        ctx = _make_ctx_with_progress_context()
        ctx._progress_update(50, "Writing objects...")

    def test_accepts_phase_keyword(self):
        """Must not raise when called with the phase keyword argument (Issue #33)."""
        ctx = _make_ctx_with_progress_context()
        ctx._progress_update(5, "Writing objects...", phase=1)

    def test_accepts_phase_positional_via_keyword(self):
        """phase= works at all phase values used by exporters."""
        ctx = _make_ctx_with_progress_context()
        for phase_val in (1, 2, 3):
            ctx._progress_update(10, "msg", phase=phase_val)

    def test_monotonic_value(self):
        """A lower value passed after a higher one should not decrease _progress_value."""
        ctx = _make_ctx_with_progress_context()
        ctx._progress_update(60)
        ctx._progress_update(10)
        self.assertEqual(ctx._progress_value, 60)

    def test_no_context_is_noop(self):
        """When there is no progress context, _progress_update must not raise."""
        ctx = ExportContext()
        ctx._progress_update(50, "msg", phase=1)

    def test_phase_forwarded_to_progress_window(self):
        """When ctx.progress is set and phase= is given, that phase is passed through."""
        calls = []

        class FakeProgress:
            def update(self, pct, phase_idx, msg):
                calls.append((pct, phase_idx, msg))

        ctx = _make_ctx_with_progress_context()
        ctx.progress = FakeProgress()
        ctx._progress_update(50, "hello", phase=2)

        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(calls[0][0], 0.5)
        self.assertEqual(calls[0][1], 2)
        self.assertEqual(calls[0][2], "hello")

    def test_phase_auto_derived_when_omitted(self):
        """When phase= is omitted, the phase index is auto-derived from the value."""
        calls = []

        class FakeProgress:
            def update(self, pct, phase_idx, msg):
                calls.append((pct, phase_idx, msg))

        ctx = _make_ctx_with_progress_context()
        ctx.progress = FakeProgress()
        ctx._progress_update(3)  # below first break (5) → phase 0

        self.assertEqual(calls[0][1], 0)


if __name__ == "__main__":
    unittest.main()
