# Blender add-on to import and export 3MF files.
# Copyright (C) 2026 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Three-tier progress system for 3MF operations.

Modes
-----
``"NONE"``
    Operation is too quick to warrant any indicator.  Nothing is shown.

``"VIEWPORT"``
    A compact branded card is drawn in the bottom-left corner of the active
    3D viewport using Blender's GPU/blf APIs.  No subprocess overhead —
    everything runs in the main thread.  Used for medium-duration ops
    (e.g. mid-size exports, moderate imports, smaller bakes).

``"VIEWPORT"``
    A compact branded card is drawn in the bottom-left corner of the active
    3D viewport using Blender's GPU/blf APIs.  No subprocess overhead —
    everything runs in the main thread.

Thresholds are controlled by the module-level constants
(``EXPORT_VIEWPORT_TRI_MIN``, etc.) — edit them directly to tune
sensitivity without touching any logic.

Usage::

    from io_mesh_3mf.progress import get_progress_mode, ProgressReporter, PHASES

    mode = get_progress_mode("export", tri_count=tri_count, has_paint=has_paint)
    with ProgressReporter(mode) as pr:
        pr.start(context, "export", "model.3mf", phases=PHASES["export"])
        pr.update(0.4, 1, "Writing geometry...")
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import time
from typing import List, Optional, Tuple

import bpy

from .common.logging import warn

# Shared IPC state file — written by both VIEWPORT and BROWSER reporters so
# that ``get_active_progress()`` and external addon observers work uniformly.
_STATE_JSON: pathlib.Path = pathlib.Path(tempfile.gettempdir()) / "3mf_progress.json"


# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------
# Each entry is (display_name, weight_percent).
# Weights are approximate — they're used by the JS to size the phase dots
# proportionally in the stepper but do not affect the percent value Blender
# passes to update().


PHASES: dict[str, List[Tuple[str, int]]] = {

    "export": [
        ("Preparing", 5),
        ("Geometry", 40),
        ("Materials", 20),
        ("Segmentation", 25),
        ("Thumbnail", 5),
        ("Packaging", 5),
    ],
    "import": [
        ("Reading Archive", 10),
        ("Parsing Objects", 40),
        ("Materials", 30),
        ("Building Scene", 20),
    ],
    "bake_cycles": [
        ("UV Unwrap", 15),
        ("Setting Up", 10),
        ("Baking", 55),
        ("Quantizing", 15),
        ("Finalizing", 5),
    ],
    "bake_vc": [
        ("UV Unwrap", 20),
        ("Assigning Colors", 30),
        ("Quantizing", 40),
        ("Finalizing", 10),
    ],
}


# ---------------------------------------------------------------------------
# Preference helpers
# ---------------------------------------------------------------------------


def _is_background() -> bool:
    """Return True when Blender is running headless (--background).

    Thin wrapper around ``bpy.app.background`` so that unit tests can patch
    it — ``bpy.app.background`` is a read-only C attribute and cannot be set
    directly via ``unittest.mock.patch``.
    """
    return bool(bpy.app.background)


# ---------------------------------------------------------------------------
# Adjustable threshold constants
# ---------------------------------------------------------------------------
# Edit these values to tune how sensitive the progress system is.
# Raising a threshold makes indicators appear less often (only on heavier ops);
# lowering it makes them appear more eagerly.

# ── Export ────────────────────────────────────────────────────────────────────
EXPORT_VIEWPORT_TRI_MIN: int = 5_000
"""Minimum triangle count to show the viewport bar for exports without paint."""

# ── Import ────────────────────────────────────────────────────────────────────
IMPORT_VIEWPORT_BYTES_MIN: int = 500_000
"""Minimum archive size (bytes) to show the viewport bar on import."""

# ── Bake — Cycles render path ─────────────────────────────────────────────
BAKE_CYCLES_VIEWPORT_FACE_MIN: int = 1_000
"""Minimum face count to show the viewport bar for a Cycles bake."""

# ── Bake — vertex-color fast path ─────────────────────────────────────────────
BAKE_VC_VIEWPORT_FACE_MIN: int = 2_000
"""Minimum face count to show the viewport bar for a vertex-color bake."""


# ---------------------------------------------------------------------------
# Three-tier threshold system
# ---------------------------------------------------------------------------


def get_progress_mode(op_type: str, **hints) -> str:
    """Return the appropriate progress mode for an operation.

    Evaluates the thresholds above to decide which — if any — progress
    indicator to display.

    :param op_type: One of ``"export"``, ``"import"``,
        ``"bake_cycles"``, ``"bake_vc"``, ``"batch"``.
    :param hints: Operation-specific keyword arguments:

        - ``tri_count`` (int): total triangle count (export).
        - ``has_paint`` (bool): export has MMU paint texture data.
        - ``thumbnail_render`` (bool): export will render a thumbnail.
        - ``file_size_bytes`` (int): archive file size in bytes (import).
        - ``face_count`` (int): polygon count (bake_vc / bake_cycles).

    :returns:
        - ``"NONE"``     — too quick; no indicator shown.
        - ``"VIEWPORT"`` — lightweight in-viewport progress bar.
    """
    if _is_background():
        return "NONE"

    if op_type == "bake_cycles":
        faces = hints.get("face_count", 0)
        if faces < BAKE_CYCLES_VIEWPORT_FACE_MIN:
            return "NONE"
        return "VIEWPORT"

    if op_type == "bake_vc":
        faces = hints.get("face_count", 0)
        if faces < BAKE_VC_VIEWPORT_FACE_MIN:
            return "NONE"
        return "VIEWPORT"

    if op_type == "export":
        tris = hints.get("tri_count", 0)
        has_paint = hints.get("has_paint", False)
        thumbnail = hints.get("thumbnail_render", False)
        if (
            tris >= EXPORT_VIEWPORT_TRI_MIN
            or has_paint
            or (thumbnail and tris >= EXPORT_VIEWPORT_TRI_MIN // 4)
        ):
            return "VIEWPORT"
        return "NONE"

    if op_type == "import":
        size = hints.get("file_size_bytes", 0)
        if size < IMPORT_VIEWPORT_BYTES_MIN:
            return "NONE"
        return "VIEWPORT"

    if op_type == "batch":
        return "VIEWPORT"

    return "NONE"


def should_show_progress(op_type: str, **hints) -> bool:
    """Deprecated compatibility wrapper — use :func:`get_progress_mode` instead.

    Returns ``True`` when :func:`get_progress_mode` returns anything other
    than ``"NONE"``.  Kept for backwards-compatibility with existing call sites
    that only need a boolean gate.
    """
    return get_progress_mode(op_type, **hints) != "NONE"


# ---------------------------------------------------------------------------
# Viewport progress bar — in-process GPU draw, no subprocess
# ---------------------------------------------------------------------------

# Module-level state dict read directly by the draw callback — no file I/O
# on the hot path.  Updated by ViewportProgressBar.update().
_VIEWPORT_STATE: dict = {"active": False}
_vp_draw_handle = None


def _write_vp_state(data: dict) -> None:
    """Write viewport bar state to the shared JSON file for external observers."""
    try:
        _STATE_JSON.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _draw_viewport_progress() -> None:
    """SpaceView3D POST_PIXEL draw callback — renders the branded progress card.

    Imports ``gpu`` and ``blf`` lazily so the module can be imported outside
    Blender (e.g. in unit tests that stub bpy).  Returns immediately if the
    bar is not active — zero overhead when idle.
    """
    import math as _math  # noqa: PLC0415
    import gpu             # noqa: PLC0415
    import blf             # noqa: PLC0415
    from gpu_extras.batch import batch_for_shader  # noqa: PLC0415

    state = _VIEWPORT_STATE
    if not state.get("active"):
        return

    try:
        region = bpy.context.region
        if region is None:
            return
    except Exception:
        return

    percent = float(state.get("percent", 0.0))
    phase = str(state.get("phase", ""))
    message = str(state.get("message", ""))
    elapsed = float(state.get("elapsed", 0.0))
    operation = str(state.get("operation", ""))

    # ── Drawing helpers (same pattern as modal_base.py) ───────────────────────
    _FONT = 0
    _BLUE = (0.231, 0.494, 0.965, 1.0)

    shader = gpu.shader.from_builtin("UNIFORM_COLOR")

    def _rrect_verts(x, y, w, h, r, segs=8):
        cx_, cy_ = x + w / 2, y + h / 2
        verts = [(cx_, cy_)]
        corners = [
            (x + r, y + r, _math.pi, 1.5 * _math.pi),
            (x + w - r, y + r, 1.5 * _math.pi, 2.0 * _math.pi),
            (x + w - r, y + h - r, 0.0, 0.5 * _math.pi),
            (x + r, y + h - r, 0.5 * _math.pi, _math.pi),
        ]
        for ox, oy, a0, a1 in corners:
            for i in range(segs + 1):
                a = a0 + (a1 - a0) * i / segs
                verts.append((ox + _math.cos(a) * r, oy + _math.sin(a) * r))
        verts.append(verts[1])
        return verts

    def _fan_tris(fan):
        c = fan[0]
        out = []
        for i in range(1, len(fan) - 1):
            out.extend([c, fan[i], fan[i + 1]])
        return out

    def _rrect(x, y, w, h, r, color):
        if w <= 0 or h <= 0:
            return
        tris = _fan_tris(_rrect_verts(x, y, w, h, max(r, 0.01)))
        b = batch_for_shader(shader, "TRIS", {"pos": tris})
        shader.bind()
        shader.uniform_float("color", color)
        b.draw(shader)

    # ── Layout ────────────────────────────────────────────────────────────────
    MARGIN = 30
    CARD_W = 300
    PAD = 12
    ROW_GAP = 6
    BADGE_PAD_X = 6
    BADGE_PAD_Y = 3
    BAR_H = 8
    CORNER_R = 6
    TITLE_SIZE = 12
    STEP_SIZE = 10
    BADGE_SIZE = 10

    blf.size(_FONT, TITLE_SIZE)
    _, title_h = blf.dimensions(_FONT, "Ag")

    blf.size(_FONT, STEP_SIZE)
    _, step_h = blf.dimensions(_FONT, "Ag")

    badge_label = operation.upper() if operation else "3MF"
    blf.size(_FONT, BADGE_SIZE)
    badge_tw, badge_th = blf.dimensions(_FONT, badge_label)
    badge_w = badge_tw + BADGE_PAD_X * 2
    badge_h = badge_th + BADGE_PAD_Y * 2

    CARD_H = (
        PAD
        + max(title_h, badge_h)
        + ROW_GAP
        + BAR_H
        + ROW_GAP
        + step_h
        + PAD
    )

    cx = MARGIN + 20
    cy = MARGIN + 70

    # ── Draw card ─────────────────────────────────────────────────────────────
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")

    # Background
    _rrect(cx, cy, CARD_W, CARD_H, CORNER_R, (0.08, 0.08, 0.10, 0.90))

    # ── Row 1: badge pill + phase title + elapsed/pct ─────────────────────────
    row1_y = cy + CARD_H - PAD - max(title_h, badge_h)

    # Badge pill (solid blue, dark text)
    badge_x = cx + PAD
    badge_y = row1_y + (max(title_h, badge_h) - badge_h) / 2
    _rrect(badge_x, badge_y, badge_w, badge_h, 3, _BLUE)
    blf.size(_FONT, BADGE_SIZE)
    blf.color(_FONT, 0.05, 0.05, 0.05, 1.0)
    blf.position(_FONT, badge_x + BADGE_PAD_X, badge_y + BADGE_PAD_Y, 0)
    blf.draw(_FONT, badge_label)

    # Phase title (clipped so it never overlaps the right-side metric)
    title_x = badge_x + badge_w + 8
    pct_text = f"{elapsed:.1f}s  {int(percent * 100)}%"
    blf.size(_FONT, STEP_SIZE)
    pct_tw, _ = blf.dimensions(_FONT, pct_text)
    title_clip_r = cx + CARD_W - PAD - pct_tw - 8

    blf.size(_FONT, TITLE_SIZE)
    blf.enable(_FONT, blf.CLIPPING)
    blf.clipping(_FONT, title_x, row1_y, title_clip_r, row1_y + title_h + 2)
    blf.color(_FONT, 1.0, 1.0, 1.0, 0.95)
    blf.position(_FONT, title_x, row1_y, 0)
    blf.draw(_FONT, phase)
    blf.disable(_FONT, blf.CLIPPING)

    # Elapsed · pct — right-aligned, accent colour
    blf.size(_FONT, STEP_SIZE)
    blf.color(_FONT, *_BLUE)
    blf.position(_FONT, cx + CARD_W - PAD - pct_tw, row1_y, 0)
    blf.draw(_FONT, pct_text)

    # ── Row 2: progress bar (rounded track + fill) ────────────────────────────
    row2_y = row1_y - ROW_GAP - BAR_H
    bar_x = cx + PAD
    bar_w = CARD_W - PAD * 2

    # Track
    _rrect(bar_x, row2_y, bar_w, BAR_H, BAR_H / 2, (0.15, 0.15, 0.18, 1.0))
    # Fill — minimum width = corner radius so pill shape is preserved
    fill_w = max(bar_w * max(0.0, min(1.0, percent)), BAR_H)
    _rrect(bar_x, row2_y, fill_w, BAR_H, BAR_H / 2, _BLUE)

    # ── Row 3: status message ─────────────────────────────────────────────────
    row3_y = row2_y - ROW_GAP - step_h
    if message:
        blf.size(_FONT, STEP_SIZE)
        blf.enable(_FONT, blf.CLIPPING)
        blf.clipping(_FONT, cx + PAD, row3_y, cx + CARD_W - PAD, row3_y + step_h + 2)
        blf.color(_FONT, 0.55, 0.55, 0.60, 0.85)
        blf.position(_FONT, cx + PAD, row3_y, 0)
        blf.draw(_FONT, message)
        blf.disable(_FONT, blf.CLIPPING)

    gpu.state.blend_set("NONE")


class ViewportProgressBar:
    """Lightweight in-viewport progress bar for medium-duration operations.

    Draws a compact branded card (350×58 px) in the bottom-left corner of
    every open 3D viewport using Blender's GPU/blf drawing APIs.

    The draw handler is registered **lazily** when :meth:`start` is called and
    **unregistered** when :meth:`finish` is called — zero idle overhead.

    Thread safety: must be called from Blender's main thread.
    """

    def __init__(self) -> None:
        self._active: bool = False
        self._start_time: float = 0.0
        self._phases: List[str] = []

    def __enter__(self) -> "ViewportProgressBar":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.finish()
        return False

    @property
    def active(self) -> bool:
        return self._active

    def start(
        self,
        context,
        operation: str,
        filename: str,
        phases: List[Tuple[str, int]],
        can_cancel: bool = False,
        filament_colors: Optional[List[str]] = None,
    ) -> None:
        """Activate the viewport bar and register the draw handler."""
        global _vp_draw_handle, _VIEWPORT_STATE

        self._start_time = time.time()
        self._active = True
        self._phases = [name for name, _ in phases]

        _VIEWPORT_STATE = {
            "active": True,
            "operation": operation,
            "filename": filename,
            "percent": 0.0,
            "phase": self._phases[0] if self._phases else "",
            "phases": self._phases,
            "phase_index": 0,
            "message": "",
            "elapsed": 0.0,
            "can_cancel": False,
            "filament_colors": filament_colors or [],
        }
        _write_vp_state(_VIEWPORT_STATE)

        if _vp_draw_handle is None:
            try:
                _vp_draw_handle = bpy.types.SpaceView3D.draw_handler_add(
                    _draw_viewport_progress, (), "WINDOW", "POST_PIXEL"
                )
            except Exception as e:
                warn(f"ViewportProgressBar: could not register draw handler: {e}")

        self._force_redraw()

    def update(
        self,
        percent: float,
        phase_index: int,
        message: str = "",
    ) -> None:
        """Update displayed progress and trigger a synchronous viewport repaint."""
        global _VIEWPORT_STATE
        if not self._active:
            return
        phase_name = (
            self._phases[phase_index]
            if 0 <= phase_index < len(self._phases)
            else ""
        )
        _VIEWPORT_STATE.update({
            "percent": float(max(0.0, min(1.0, percent))),
            "phase": phase_name,
            "phase_index": int(phase_index),
            "message": str(message),
            "elapsed": time.time() - self._start_time,
        })
        _write_vp_state(_VIEWPORT_STATE)
        self._force_redraw()

    def finish(self) -> None:
        """Clear the bar, unregister the draw handler, and repaint.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        global _vp_draw_handle, _VIEWPORT_STATE
        if not self._active:
            return
        self._active = False

        _VIEWPORT_STATE = {"active": False, "percent": 1.0}
        _write_vp_state({
            "active": False,
            "percent": 1.0,
            "elapsed": time.time() - self._start_time,
        })

        if _vp_draw_handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(_vp_draw_handle, "WINDOW")
            except Exception:
                pass
            _vp_draw_handle = None

        self._force_redraw()

    def is_cancel_requested(self) -> bool:
        """Always False — the viewport bar has no cancel button."""
        return False

    def _force_redraw(self) -> None:
        """Tag all 3D viewport areas dirty and request an immediate swap.

        ``tag_redraw()`` schedules the repaint; ``wm.redraw_timer`` forces it
        to happen *now* even during a blocking operator execute().  The call
        costs ~1–5 ms and is made at most once per ``update()`` / ``finish()``.
        """
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == "VIEW_3D":
                        area.tag_redraw()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ProgressReporter — unified facade for all three modes
# ---------------------------------------------------------------------------


class ProgressReporter:
    """Unified progress facade — delegates to ``ViewportProgressBar``,
    ``ViewportProgressBar``, or a no-op stub depending on *mode*.

    Call sites never need to branch on the mode; ``update()``, ``finish()``,
    and ``is_cancel_requested()`` are always safe to call regardless of which
    implementation is active.

    :param mode: One of ``"NONE"``, ``"VIEWPORT"``, ``"BROWSER"``.
        Obtain the correct mode for an operation with :func:`get_progress_mode`.

    Usage::

        mode = get_progress_mode("export", tri_count=tri_count)
        with ProgressReporter(mode) as pr:
            pr.start(context, "export", "model.3mf", phases=PHASES["export"])
            pr.update(0.5, 2, "Writing materials...")
        # finish() called automatically on __exit__
    """

    def __init__(self, mode: str) -> None:
        # "BROWSER" is kept as an accepted value for backwards-compatibility
        # but falls back to the viewport bar (no subprocess / no browser).
        self._mode = "VIEWPORT" if mode == "BROWSER" else mode
        if self._mode == "VIEWPORT":
            self._impl: Optional[object] = ViewportProgressBar()
        else:
            self._impl = None  # "NONE" — all methods are no-ops

    def __enter__(self) -> "ProgressReporter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.finish()
        return False

    @property
    def mode(self) -> str:
        """The active mode string: ``"NONE"``, ``"VIEWPORT"``, or ``"BROWSER"``."""
        return self._mode

    @property
    def active(self) -> bool:
        return self._impl is not None and getattr(self._impl, "_active", False)

    def start(
        self,
        context,
        operation: str,
        filename: str,
        phases: List[Tuple[str, int]],
        can_cancel: bool = False,
        filament_colors: Optional[List[str]] = None,
    ) -> None:
        """Start the progress indicator (no-op for mode ``"NONE"``)."""
        if self._impl is not None:
            self._impl.start(  # type: ignore[union-attr]
                context, operation, filename, phases, can_cancel, filament_colors
            )

    def update(self, percent: float, phase_index: int, message: str = "") -> None:
        """Update progress (no-op for mode ``"NONE"``)."""
        if self._impl is not None:
            self._impl.update(percent, phase_index, message)  # type: ignore[union-attr]

    def finish(self) -> None:
        """Finish and tear down the indicator (no-op for mode ``"NONE"``)."""
        if self._impl is not None:
            self._impl.finish()  # type: ignore[union-attr]

    def is_cancel_requested(self) -> bool:
        """Return ``True`` only when browser card Cancel was clicked."""
        if self._impl is not None:
            return self._impl.is_cancel_requested()  # type: ignore[union-attr]
        return False


# ---------------------------------------------------------------------------
# Public helpers — for other addons that want to observe progress
# ---------------------------------------------------------------------------

#: Absolute path to the JSON state file written during active operations.
#: Other addons (e.g. TrailPrint3D) can poll this file to read live progress::
#:
#:     import json
#:     from io_mesh_3mf.progress import STATE_PATH
#:
#:     try:
#:         state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
#:         if state.get("active"):
#:             percent = state["percent"]       # 0.0 – 1.0
#:             phase   = state["phase"]         # current phase name
#:             message = state["message"]       # status message
#:             elapsed = state["elapsed"]       # seconds since start
#:     except (FileNotFoundError, ValueError):
#:         pass  # no operation in progress
STATE_PATH: pathlib.Path = _STATE_JSON


def get_active_progress() -> Optional[dict]:
    """Return the live progress state dict if an operation is running.

    Returns ``None`` when no operation is active or the state file cannot be
    read.  The returned dict has these keys:

    ============== ========== =============================================
    Key            Type       Description
    ============== ========== =============================================
    ``active``     bool       Always ``True`` when this function returns
    ``operation``  str        ``"export"``, ``"import"``, ``"bake_cycles"``, etc.
    ``filename``   str        Display name of the file/mesh being processed
    ``percent``    float      Overall progress fraction, 0.0–1.0
    ``phase``      str        Current phase display name
    ``phase_index``int        Index into ``phases`` list
    ``phases``     list[str]  All phase names for this operation
    ``message``    str        Current status line
    ``elapsed``    float      Seconds elapsed since the operation started
    ``can_cancel`` bool       Whether Cancel is available
    ============== ========== =============================================

    Usage in another addon::

        from io_mesh_3mf.progress import get_active_progress

        state = get_active_progress()
        if state:
            pct = int(state["percent"] * 100)
            my_panel.update_label(f"3MF export: {pct}% — {state['phase']}")
    """
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if data.get("active", False):
            return data
    except Exception:
        pass
    return None
