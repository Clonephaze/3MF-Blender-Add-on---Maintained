"""
Unit tests for ``io_mesh_3mf.progress``.

Covers:
- ``get_progress_mode()`` threshold logic for all op_types
- ``should_show_progress()`` boolean gate
- ``ProgressReporter`` mode selection and no-op behaviour for "NONE"
- ``ProgressReporter`` context-manager protocol
- ``ViewportProgressBar`` start/update/finish state transitions
- ``get_active_progress()`` JSON round-trip
- All tests run inside real Blender (``--background --factory-startup``).
"""

import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import bpy

import io_mesh_3mf.progress as prog_mod
from io_mesh_3mf.progress import (
    PHASES,
    ProgressReporter,
    ViewportProgressBar,
    get_active_progress,
    get_progress_mode,
    should_show_progress,
    EXPORT_VIEWPORT_TRI_MIN,
    EXPORT_BROWSER_TRI_MIN,
    IMPORT_VIEWPORT_BYTES_MIN,
    IMPORT_BROWSER_BYTES_MIN,
    BAKE_CYCLES_VIEWPORT_FACE_MIN,
    BAKE_CYCLES_BROWSER_FACE_MIN,
    BAKE_VC_VIEWPORT_FACE_MIN,
    BAKE_VC_BROWSER_FACE_MIN,
)


# ---------------------------------------------------------------------------
# Base class: patches _get_progress_pref->True and _is_background->False so
# threshold tests work inside --background mode.
# ---------------------------------------------------------------------------

class ProgressThresholdTestCase(unittest.TestCase):
    def setUp(self):
        self._p_pref = patch.object(prog_mod, "_get_progress_pref", return_value=True)
        self._p_bg = patch.object(prog_mod, "_is_background", return_value=False)
        self._p_pref.start()
        self._p_bg.start()

    def tearDown(self):
        patch.stopall()


# ============================================================================
# get_progress_mode -- export
# ============================================================================

class TestGetProgressModeExport(ProgressThresholdTestCase):

    def test_tiny_mesh_no_paint_returns_none(self):
        self.assertEqual(get_progress_mode("export", tri_count=100, has_paint=False), "NONE")

    def test_medium_mesh_no_paint_returns_viewport(self):
        self.assertEqual(get_progress_mode("export", tri_count=EXPORT_VIEWPORT_TRI_MIN, has_paint=False), "VIEWPORT")

    def test_small_painted_mesh_returns_viewport(self):
        self.assertEqual(get_progress_mode("export", tri_count=10, has_paint=True), "VIEWPORT")

    def test_large_painted_mesh_returns_browser(self):
        self.assertEqual(get_progress_mode("export", tri_count=EXPORT_BROWSER_TRI_MIN, has_paint=True), "BROWSER")

    def test_large_unpainted_mesh_returns_viewport_not_browser(self):
        self.assertEqual(get_progress_mode("export", tri_count=EXPORT_BROWSER_TRI_MIN + 1, has_paint=False), "VIEWPORT")

    def test_thumbnail_only_small_mesh_returns_none(self):
        self.assertEqual(get_progress_mode("export", tri_count=100, has_paint=False, thumbnail_render=True), "NONE")

    def test_thumbnail_medium_mesh_returns_viewport(self):
        t = EXPORT_VIEWPORT_TRI_MIN // 4
        self.assertEqual(get_progress_mode("export", tri_count=t, has_paint=False, thumbnail_render=True), "VIEWPORT")

    def test_thumbnail_never_triggers_browser(self):
        mode = get_progress_mode(
            "export", tri_count=EXPORT_BROWSER_TRI_MIN * 10, has_paint=False, thumbnail_render=True
        )
        self.assertEqual(mode, "VIEWPORT")

    def test_pref_disabled_returns_none(self):
        with patch.object(prog_mod, "_get_progress_pref", return_value=False):
            mode = get_progress_mode("export", tri_count=EXPORT_BROWSER_TRI_MIN, has_paint=True)
        self.assertEqual(mode, "NONE")

    def test_background_returns_none(self):
        with patch.object(prog_mod, "_is_background", return_value=True):
            mode = get_progress_mode("export", tri_count=EXPORT_BROWSER_TRI_MIN, has_paint=True)
        self.assertEqual(mode, "NONE")


# ============================================================================
# get_progress_mode -- import
# ============================================================================

class TestGetProgressModeImport(ProgressThresholdTestCase):

    def test_tiny_file_returns_none(self):
        self.assertEqual(get_progress_mode("import", file_size_bytes=1000), "NONE")

    def test_medium_file_returns_viewport(self):
        self.assertEqual(get_progress_mode("import", file_size_bytes=IMPORT_VIEWPORT_BYTES_MIN), "VIEWPORT")

    def test_large_file_returns_browser(self):
        self.assertEqual(get_progress_mode("import", file_size_bytes=IMPORT_BROWSER_BYTES_MIN), "BROWSER")

    def test_just_below_browser_returns_viewport(self):
        self.assertEqual(get_progress_mode("import", file_size_bytes=IMPORT_BROWSER_BYTES_MIN - 1), "VIEWPORT")


# ============================================================================
# get_progress_mode -- bake_cycles
# ============================================================================

class TestGetProgressModeBakeCycles(ProgressThresholdTestCase):

    def test_tiny_returns_none(self):
        self.assertEqual(get_progress_mode("bake_cycles", face_count=10), "NONE")

    def test_medium_returns_viewport(self):
        self.assertEqual(get_progress_mode("bake_cycles", face_count=BAKE_CYCLES_VIEWPORT_FACE_MIN), "VIEWPORT")

    def test_large_returns_browser(self):
        self.assertEqual(get_progress_mode("bake_cycles", face_count=BAKE_CYCLES_BROWSER_FACE_MIN), "BROWSER")


# ============================================================================
# get_progress_mode -- bake_vc
# ============================================================================

class TestGetProgressModeBakeVC(ProgressThresholdTestCase):

    def test_tiny_returns_none(self):
        self.assertEqual(get_progress_mode("bake_vc", face_count=100), "NONE")

    def test_medium_returns_viewport(self):
        self.assertEqual(get_progress_mode("bake_vc", face_count=BAKE_VC_VIEWPORT_FACE_MIN), "VIEWPORT")

    def test_large_returns_browser(self):
        self.assertEqual(get_progress_mode("bake_vc", face_count=BAKE_VC_BROWSER_FACE_MIN), "BROWSER")


# ============================================================================
# get_progress_mode -- batch / unknown
# ============================================================================

class TestGetProgressModeOther(ProgressThresholdTestCase):

    def test_batch_always_viewport(self):
        self.assertEqual(get_progress_mode("batch"), "VIEWPORT")

    def test_unknown_op_type_returns_none(self):
        self.assertEqual(get_progress_mode("unrecognised_op"), "NONE")


# ============================================================================
# should_show_progress
# ============================================================================

class TestShouldShowProgress(ProgressThresholdTestCase):

    def test_returns_false_for_none_mode(self):
        self.assertFalse(should_show_progress("export", tri_count=0, has_paint=False))

    def test_returns_true_for_viewport_mode(self):
        self.assertTrue(should_show_progress("export", tri_count=EXPORT_VIEWPORT_TRI_MIN, has_paint=False))

    def test_returns_true_for_browser_mode(self):
        self.assertTrue(should_show_progress("export", tri_count=EXPORT_BROWSER_TRI_MIN, has_paint=True))


# ============================================================================
# ProgressReporter -- mode selection
# ============================================================================

class TestProgressReporterModeSelection(unittest.TestCase):

    def test_none_impl_is_none(self):
        pr = ProgressReporter("NONE")
        self.assertIsNone(pr._impl)
        self.assertEqual(pr.mode, "NONE")

    def test_viewport_creates_viewport_bar(self):
        pr = ProgressReporter("VIEWPORT")
        self.assertIsInstance(pr._impl, ViewportProgressBar)
        self.assertEqual(pr.mode, "VIEWPORT")

    def test_browser_creates_progress_window(self):
        from io_mesh_3mf.progress import ProgressWindow
        pr = ProgressReporter("BROWSER")
        self.assertIsInstance(pr._impl, ProgressWindow)
        self.assertEqual(pr.mode, "BROWSER")


# ============================================================================
# ProgressReporter("NONE") -- all methods are no-ops
# ============================================================================

class TestProgressReporterNone(unittest.TestCase):

    def test_start_noop(self):
        ProgressReporter("NONE").start(None, "export", "file.3mf", phases=PHASES["export"])

    def test_update_noop(self):
        ProgressReporter("NONE").update(0.5, 1, "msg")

    def test_finish_noop(self):
        ProgressReporter("NONE").finish()

    def test_is_cancel_requested_false(self):
        self.assertFalse(ProgressReporter("NONE").is_cancel_requested())

    def test_active_is_false(self):
        self.assertFalse(ProgressReporter("NONE").active)

    def test_context_manager_no_raise(self):
        with ProgressReporter("NONE") as pr:
            self.assertEqual(pr.mode, "NONE")


# ============================================================================
# ProgressReporter -- context manager protocol
# ============================================================================

class TestProgressReporterContextManager(unittest.TestCase):

    def test_enter_returns_self(self):
        pr = ProgressReporter("NONE")
        self.assertIs(pr.__enter__(), pr)

    def test_exit_calls_finish(self):
        pr = ProgressReporter("NONE")
        with patch.object(pr, "finish") as mock_finish:
            pr.__exit__(None, None, None)
        mock_finish.assert_called_once()

    def test_exit_does_not_suppress_exceptions(self):
        pr = ProgressReporter("NONE")
        self.assertFalse(pr.__exit__(ValueError, ValueError("oops"), None))


# ============================================================================
# ViewportProgressBar -- start / update / finish state
# ============================================================================

class TestViewportProgressBar(unittest.TestCase):

    def test_initially_inactive(self):
        self.assertFalse(ViewportProgressBar()._active)

    def test_start_sets_active(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
        self.assertTrue(bar._active)

    def test_start_sets_viewport_state(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
        state = prog_mod._VIEWPORT_STATE
        self.assertTrue(state["active"])
        self.assertEqual(state["operation"], "export")
        self.assertEqual(state["filename"], "f.3mf")
        self.assertEqual(state["percent"], 0.0)

    def test_update_changes_percent(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
            bar.update(0.42, 1, "Writing...")
        self.assertAlmostEqual(prog_mod._VIEWPORT_STATE["percent"], 0.42)

    def test_update_clamps_high(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
            bar.update(2.0, 0)
        self.assertAlmostEqual(prog_mod._VIEWPORT_STATE["percent"], 1.0)

    def test_update_clamps_low(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
            bar.update(-5.0, 0)
        self.assertAlmostEqual(prog_mod._VIEWPORT_STATE["percent"], 0.0)

    def test_finish_sets_inactive(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
            bar.finish()
        self.assertFalse(bar._active)
        self.assertFalse(prog_mod._VIEWPORT_STATE.get("active", True))

    def test_finish_idempotent(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
            bar.finish()
            bar.finish()

    def test_update_after_finish_is_noop(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
            bar.finish()
            bar.update(0.99, 0)
        self.assertNotAlmostEqual(prog_mod._VIEWPORT_STATE.get("percent", 0.0), 0.99)

    def test_is_cancel_requested_always_false(self):
        self.assertFalse(ViewportProgressBar().is_cancel_requested())

    def test_phase_name_resolved_from_index(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
            bar.update(0.1, 2, "")  # index 2 = "Materials"
        self.assertEqual(prog_mod._VIEWPORT_STATE["phase"], "Materials")

    def test_out_of_range_phase_gives_empty_string(self):
        bar = ViewportProgressBar()
        with patch.object(bar, "_force_redraw"):
            bar.start(None, "export", "f.3mf", phases=PHASES["export"])
            bar.update(0.1, 999, "")
        self.assertEqual(prog_mod._VIEWPORT_STATE["phase"], "")


# ============================================================================
# get_active_progress -- JSON round-trip
# ============================================================================

class TestGetActiveProgress(unittest.TestCase):

    def test_returns_none_when_file_missing(self):
        fake = pathlib.Path(tempfile.gettempdir()) / "_no_such_file_3mf_test.json"
        with patch.object(prog_mod, "STATE_PATH", fake):
            self.assertIsNone(get_active_progress())

    def test_returns_none_when_active_false(self):
        tmp = pathlib.Path(tempfile.gettempdir()) / "_3mf_progress_test_inactive.json"
        tmp.write_text(json.dumps({"active": False, "percent": 1.0}), encoding="utf-8")
        try:
            with patch.object(prog_mod, "STATE_PATH", tmp):
                self.assertIsNone(get_active_progress())
        finally:
            tmp.unlink(missing_ok=True)

    def test_returns_dict_when_active_true(self):
        tmp = pathlib.Path(tempfile.gettempdir()) / "_3mf_progress_test_active.json"
        state = {
            "active": True, "operation": "export", "filename": "cube.3mf",
            "percent": 0.5, "phase": "Geometry", "phase_index": 1,
            "phases": ["Preparing", "Geometry"], "message": "Writing",
            "elapsed": 3.2, "can_cancel": False,
        }
        tmp.write_text(json.dumps(state), encoding="utf-8")
        try:
            with patch.object(prog_mod, "STATE_PATH", tmp):
                result = get_active_progress()
            self.assertIsNotNone(result)
            self.assertEqual(result["operation"], "export")
            self.assertAlmostEqual(result["percent"], 0.5)
        finally:
            tmp.unlink(missing_ok=True)

    def test_returns_none_on_corrupt_json(self):
        tmp = pathlib.Path(tempfile.gettempdir()) / "_3mf_progress_test_corrupt.json"
        tmp.write_text("not valid {{{{", encoding="utf-8")
        try:
            with patch.object(prog_mod, "STATE_PATH", tmp):
                self.assertIsNone(get_active_progress())
        finally:
            tmp.unlink(missing_ok=True)


# ============================================================================
# PHASES sanity checks
# ============================================================================

class TestPhases(unittest.TestCase):

    def test_all_op_types_present(self):
        for key in ("export", "import", "bake_cycles", "bake_vc"):
            self.assertIn(key, PHASES)

    def test_each_phase_is_name_weight_tuple(self):
        for op, phases in PHASES.items():
            for item in phases:
                self.assertEqual(len(item), 2, f"{op}: expected (name, weight)")
                self.assertIsInstance(item[0], str)
                self.assertIsInstance(item[1], int)

    def test_each_op_has_at_least_one_phase(self):
        for op, phases in PHASES.items():
            self.assertGreater(len(phases), 0, f"{op} has no phases")


if __name__ == "__main__":
    unittest.main()
