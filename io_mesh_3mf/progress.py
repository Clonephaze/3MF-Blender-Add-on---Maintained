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
Progress Window — floating browser card for long-running 3MF operations.

Architecture
------------
Blender main thread:
  ProgressWindow.start()
    ├─ writes $TEMP/3mf_progress.json   (initial state)
    ├─ spawns progress_win.py           (subprocess, Blender's own Python)
    └─ polls $TEMP/3mf_progress_port.json, then calls bpy.ops.wm.url_open()

Subprocess (progress_win.py):
  ├─ starts HTTPServer on 127.0.0.1:<random port>  (daemon thread)
  ├─ writes $TEMP/3mf_progress_port.json            (signals Blender)
  └─ waits until JSON shows active=False, then shuts down

Browser:
  ├─ GET /       → full HTML page
  ├─ GET /state  → current JSON state (polled every 250ms)
  └─ POST /cancel → writes $TEMP/3mf_progress.cancel flag

IPC is entirely file-based + a local HTTP server.  No sockets between
Blender and the subprocess.  No threads in Blender's main process.

Usage (context-manager — recommended)::

    with ProgressWindow() as pw:
        pw.start(context, "export", "model.3mf",
                 phases=PHASES["export"], can_cancel=False)
        for i, obj in enumerate(objects):
            pw.update(i / len(objects), 1, f"Object {i+1} of {len(objects)}")

Usage (manual)::

    pw = ProgressWindow()
    pw.start(context, "bake_cycles", "MyMesh",
             phases=PHASES["bake_cycles"], can_cancel=True,
             filament_colors=["#FF0000", "#00FF00"])
    try:
        ...
        if pw.is_cancel_requested():
            pw.finish()
            return {"CANCELLED"}
        ...
    finally:
        pw.finish()
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import List, Optional, Tuple

import bpy

from .common.logging import warn, debug

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

def _get_addon_package() -> str:
    """Return the top-level addon package name."""
    pkg = __package__ or ""
    return pkg.rsplit(".", 1)[0] if "." in pkg else pkg


def _get_progress_pref() -> bool:
    """Return the show_progress_window preference value, defaulting to True."""
    try:
        pkg = _get_addon_package()
        addon = bpy.context.preferences.addons.get(pkg)
        if addon is not None:
            return bool(addon.preferences.show_progress_window)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Threshold heuristic
# ---------------------------------------------------------------------------

def should_show_progress(op_type: str, **hints) -> bool:
    """Decide whether the progress window should be shown for a given operation.

    The window is only shown when the operation is expected to take a
    meaningful amount of time AND the ``show_progress_window`` preference
    is enabled.  Blender background mode always returns False.

    :param op_type: One of ``"export"``, ``"import"``,
        ``"bake_cycles"``, ``"bake_vc"``, ``"batch"``.
    :param hints: Optional keyword arguments:

        - ``tri_count`` (int): total triangle count, used for export threshold.
        - ``has_paint`` (bool): export has MMU paint texture data.
        - ``thumbnail_render`` (bool): export will render an auto thumbnail.
        - ``file_size_bytes`` (int): archive size, used for import threshold.
        - ``face_count`` (int): polygon count, used for bake_vc threshold.

    :return: ``True`` if the window should be spawned, ``False`` otherwise.
    """
    if not _get_progress_pref():
        debug("ProgressWindow: skipped — 'Show Progress Window' preference is disabled")
        return False
    if bpy.app.background:
        debug("ProgressWindow: skipped — running in background mode")
        return False

    if op_type == "bake_cycles":
        return True
    if op_type == "bake_vc":
        result = hints.get("face_count", 0) > 10_000
        if not result:
            warn(f"ProgressWindow: skipped bake_vc — face_count={hints.get('face_count', 0)} (threshold 10 000)")
        return result
    if op_type == "export":
        result = (
            hints.get("has_paint", False)
            or hints.get("tri_count", 0) > 50_000
            or hints.get("thumbnail_render", False)
        )
        if not result:
            warn(
                f"ProgressWindow: skipped export — "
                f"tri_count={hints.get('tri_count', 0)} (threshold 50 000), "
                f"has_paint={hints.get('has_paint', False)}, "
                f"thumbnail_render={hints.get('thumbnail_render', False)}"
            )
        return result
    if op_type == "import":
        result = hints.get("file_size_bytes", 0) > 5_000_000
        if not result:
            warn(f"ProgressWindow: skipped import — file_size={hints.get('file_size_bytes', 0)} bytes (threshold 5 MB)")
        return result
    if op_type == "batch":
        return True
    return False


# ---------------------------------------------------------------------------
# ProgressWindow
# ---------------------------------------------------------------------------

class ProgressWindow:
    """Manages a floating browser progress card for a long-running operation.

    Thread safety: all methods must be called from Blender's main thread.
    The HTTP server runs in a completely separate subprocess — Blender only
    writes JSON files and never blocks on network I/O.
    """

    _json_path: pathlib.Path = pathlib.Path(tempfile.gettempdir()) / "3mf_progress.json"
    _cancel_path: pathlib.Path = pathlib.Path(tempfile.gettempdir()) / "3mf_progress.cancel"

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._active: bool = False
        self._start_time: float = 0.0
        self._phases: List[str] = []

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "ProgressWindow":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.finish()
        return False  # never suppress exceptions

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(
        self,
        context,
        operation: str,
        filename: str,
        phases: List[Tuple[str, int]],
        can_cancel: bool = False,
        filament_colors: Optional[List[str]] = None,
    ) -> None:
        """Spawn the progress window subprocess and open the browser.

        :param context: Blender context (must be called from the main thread).
        :param operation: Operation type string — one of ``PHASES`` keys.
        :param filename: Short display name (e.g. ``"model.3mf"``).
        :param phases: List of ``(name, weight)`` tuples — use ``PHASES[op]``.
        :param can_cancel: When ``True``, a Cancel button appears in the card.
        :param filament_colors: Optional list of ``"#RRGGBB"`` strings shown as
            filament swatches (bake operations).
        """
        # Clear stale cancel flag from a previous run.
        try:
            self._cancel_path.unlink()
        except FileNotFoundError:
            pass

        self._start_time = time.time()
        self._active = True
        self._phases = [name for name, _weight in phases]

        initial_state = {
            "active": True,
            "operation": operation,
            "filename": filename,
            "percent": 0.0,
            "phase": self._phases[0] if self._phases else "",
            "phases": self._phases,
            "phase_index": 0,
            "message": "",
            "elapsed": 0.0,
            "can_cancel": can_cancel,
            "filament_colors": filament_colors or [],
        }
        self._write_state(initial_state)

        script = pathlib.Path(__file__).parent / "progress_win.py"
        if not script.exists():
            warn(f"ProgressWindow: progress_win.py not found at {script}")
            self._active = False
            return

        # Redirect subprocess stderr to a temp log so crashes are diagnosable.
        # Check $TEMP/3mf_progress_err.txt if the window doesn't appear.
        log_path = pathlib.Path(tempfile.gettempdir()) / "3mf_progress_err.txt"
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        try:
            log_file = open(log_path, "w", encoding="utf-8")
            self._proc = subprocess.Popen(
                [sys.executable, str(script), str(self._json_path)],
                creationflags=creationflags,
                stdout=log_file,
                stderr=log_file,
            )
            log_file.close()
        except Exception as e:
            warn(f"ProgressWindow: could not spawn subprocess: {e}")
            self._active = False
            return
        debug(f"ProgressWindow: spawned subprocess PID {self._proc.pid} for '{operation}'")
        warn(f"ProgressWindow: progress window launched — check {log_path} if it doesn't appear")

    def update(
        self,
        percent: float,
        phase_index: int,
        message: str = "",
    ) -> None:
        """Write updated progress state.

        :param percent: Overall progress fraction 0.0–1.0.
        :param phase_index: Index into ``phases`` list; drives the stepper highlight.
        :param message: Short status line (e.g. ``"Object 5 of 12"``).
        """
        if not self._active:
            return
        try:
            existing = json.loads(self._json_path.read_text(encoding="utf-8"))
            phase_name = (
                self._phases[phase_index]
                if 0 <= phase_index < len(self._phases)
                else ""
            )
            existing.update(
                {
                    "percent": float(max(0.0, min(1.0, percent))),
                    "phase": phase_name,
                    "phase_index": int(phase_index),
                    "message": str(message),
                    "elapsed": time.time() - self._start_time,
                }
            )
            self._write_state(existing)
        except Exception:
            pass

    def finish(self) -> None:
        """Signal completion and wait for the subprocess to exit.

        Safe to call multiple times; subsequent calls are no-ops.
        """
        if not self._active:
            return
        self._active = False

        try:
            existing = json.loads(self._json_path.read_text(encoding="utf-8"))
            existing.update(
                {
                    "active": False,
                    "percent": 1.0,
                    "elapsed": time.time() - self._start_time,
                }
            )
            self._write_state(existing)
        except Exception:
            self._write_state({"active": False, "percent": 1.0})

        if self._proc is not None:
            try:
                self._proc.wait(timeout=4)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def is_cancel_requested(self) -> bool:
        """Return ``True`` if the Cancel button was clicked in the progress card."""
        return self._cancel_path.exists()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_state(self, data: dict) -> None:
        try:
            self._json_path.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass


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
STATE_PATH: pathlib.Path = ProgressWindow._json_path


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
