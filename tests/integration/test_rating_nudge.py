"""
Integration tests for the export-count / rating-nudge system.

Tests the preference counting logic in ``_maybe_show_rating_nudge`` and the
snooze/dismiss behaviour of ``THREEMF_OT_rating_nudge.execute()``.

All tests run inside Blender's real Python environment via the integration
test runner (no mocking framework).  ``bpy.app.timers.register`` is
temporarily monkey-patched to let us verify scheduling without needing a
display.
"""

import bpy
import unittest

from test_base import Blender3mfTestCase
from io_mesh_3mf.export_3mf.operator import _maybe_show_rating_nudge


# ---------------------------------------------------------------------------
# Lightweight fake objects used instead of preferences.addons
# (preferences.addons is empty under --factory-startup unless the addon is
# enabled via bpy.ops, which is unreliable in headless test environments).
# ---------------------------------------------------------------------------


class _FakePrefs:
    """Minimal stand-in for ThreeMFPreferences with just the nudge fields."""

    def __init__(self, count=0, threshold=5):
        self.export_count = count
        self.rating_prompt_after = threshold


class _FakeContext:
    """Minimal stand-in for bpy.context with a controllable prefs entry."""

    def __init__(self, prefs: _FakePrefs):
        self._prefs = prefs
        self.preferences = self
        self.addons = self

    def get(self, key):
        class _Entry:
            pass

        e = _Entry()
        e.preferences = self._prefs
        return e


def _run_nudge(count, threshold):
    """Run _maybe_show_rating_nudge with a fake context and return (prefs, scheduled_count)."""
    prefs = _FakePrefs(count=count, threshold=threshold)
    ctx = _FakeContext(prefs)

    scheduled = []
    _real_register = bpy.app.timers.register
    try:
        bpy.app.timers.register = lambda fn, first_interval=0: scheduled.append(fn)
        _maybe_show_rating_nudge(ctx)
    finally:
        bpy.app.timers.register = _real_register

    return prefs, len(scheduled)


class TestRatingNudgePreferenceLogic(Blender3mfTestCase):
    """_maybe_show_rating_nudge() — preference state transitions."""

    # ------------------------------------------------------------------ counts

    def test_count_increments_on_export(self):
        prefs, _ = _run_nudge(count=2, threshold=10)
        self.assertEqual(prefs.export_count, 3)

    def test_count_does_not_increment_when_dismissed(self):
        """rating_prompt_after == -1 is a hard stop — even the count freezes."""
        prefs, _ = _run_nudge(count=3, threshold=-1)
        self.assertEqual(prefs.export_count, 3)

    def test_count_increments_multiple_times(self):
        prefs = _FakePrefs(count=0, threshold=99)
        ctx = _FakeContext(prefs)
        _real_register = bpy.app.timers.register
        try:
            bpy.app.timers.register = lambda fn, first_interval=0: None
            for _ in range(4):
                _maybe_show_rating_nudge(ctx)
        finally:
            bpy.app.timers.register = _real_register
        self.assertEqual(prefs.export_count, 4)

    # --------------------------------------------------------- timer scheduling

    def test_timer_registered_when_threshold_hit(self):
        """A timer should be scheduled exactly when count reaches threshold."""
        _, n = _run_nudge(count=4, threshold=5)
        self.assertEqual(n, 1, "Timer should be registered exactly once")

    def test_timer_not_registered_below_threshold(self):
        """No timer when count is still below threshold."""
        _, n = _run_nudge(count=2, threshold=5)
        self.assertEqual(n, 0)

    def test_timer_not_registered_when_dismissed(self):
        """No timer if the user has permanently dismissed the nudge."""
        _, n = _run_nudge(count=99, threshold=-1)
        self.assertEqual(n, 0)

    def test_timer_registered_when_count_exceeds_threshold(self):
        """Timer fires if count is already past threshold (e.g. prefs changed externally)."""
        _, n = _run_nudge(count=7, threshold=5)
        self.assertEqual(n, 1)


class TestRatingNudgeOperatorActions(Blender3mfTestCase):
    """THREEMF_OT_rating_nudge.execute() — snooze and dismiss paths.

    The operator reads/writes preferences.addons[__package__].preferences.
    We enable the addon in setUpClass so that entry exists, then reset the
    nudge fields in setUp before each test.
    """

    _addon_pkg = None  # resolved in setUpClass

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Enable the addon so preferences.addons has an entry for it.
        # Calling addon_enable on an already-registered module is safe;
        # Blender just adds the prefs entry without double-registering.
        import io_mesh_3mf

        pkg = io_mesh_3mf.__package__ or "io_mesh_3mf"
        cls._addon_pkg = pkg
        try:
            bpy.ops.preferences.addon_enable(module=pkg)
        except Exception:
            pass  # May already be enabled or unavailable headless

    def setUp(self):
        super().setUp()
        entry = bpy.context.preferences.addons.get(self._addon_pkg)
        if entry is None:
            self.skipTest(
                f"Addon '{self._addon_pkg}' not in preferences.addons — "
                "cannot test operator execute paths headless"
            )
        entry.preferences.export_count = 5
        entry.preferences.rating_prompt_after = 5

    def _prefs(self):
        return bpy.context.preferences.addons[self._addon_pkg].preferences

    def _run_action(self, action: str):
        return bpy.ops.threemf.rating_nudge("EXEC_DEFAULT", action=action)

    def test_dismiss_sets_threshold_to_minus_one(self):
        self._run_action("DISMISS")
        self.assertEqual(self._prefs().rating_prompt_after, -1)

    def test_snooze_adds_five_to_current_count(self):
        self._prefs().export_count = 6
        self._run_action("SNOOZE")
        self.assertEqual(self._prefs().rating_prompt_after, 11)  # 6 + 5

    def test_rate_sets_threshold_to_minus_one(self):
        # RATE permanently dismisses (after opening the URL).
        # We cannot verify the browser opened headless, but the pref must change.
        self._run_action("RATE")
        self.assertEqual(self._prefs().rating_prompt_after, -1)

    def test_snooze_is_relative_to_current_count_not_old_threshold(self):
        """Snoozing on export 20 should remind at 25, not threshold+5."""
        self._prefs().export_count = 20
        self._prefs().rating_prompt_after = 5
        self._run_action("SNOOZE")
        self.assertEqual(self._prefs().rating_prompt_after, 25)


if __name__ == "__main__":
    unittest.main()
