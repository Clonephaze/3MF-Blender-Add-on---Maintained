# Blender add-on to import and export 3MF files.
# Copyright (C) 2025 Jack (modernization for Blender 4.2+)
# This add-on is free software; you can redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later
# version.
# This add-on is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program; if not, write to the Free
# Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Logging utilities for the 3MF add-on.

Blender addons have no logging infrastructure — Python's ``logging`` module
does nothing because there are no handlers configured.  **Never use
``import logging``.**  All console output goes through the functions here.

Usage::

    from ..common import debug, warn, error

    debug(f"Loaded {count} objects")     # Silent unless DEBUG_MODE is True
    warn(f"Missing vertex at {idx}")     # Always prints  WARNING: ...
    error(f"Failed to write: {e}")       # Always prints  ERROR: ...
"""

__all__ = ["DEBUG_MODE", "debug", "warn", "error", "safe_report", "timing_debug"]


DEBUG_MODE = False
"""Set to True to enable verbose console output for development/debugging."""


def _is_blender_debug() -> bool:
    """Return True if Blender was launched with --debug or --debug-all."""
    try:
        import bpy
        return bpy.app.debug
    except (ImportError, AttributeError):
        return False


def debug(*args, **kwargs):
    """Print to console only when DEBUG_MODE is enabled or Blender is in --debug mode."""
    if DEBUG_MODE or _is_blender_debug():
        print(*args, **kwargs)


def timing_debug(label: str, elapsed_ms: float) -> None:
    """Print a [3MF TIMING] line when Blender is in --debug mode or DEBUG_MODE is True.

    Use this for performance-critical sections so timing is always available
    without touching source code — just relaunch Blender with ``--debug``.

    Example output::

        [3MF TIMING] write_vertices foreach_get (50000 verts): 0.8ms
        [3MF TIMING] write_vertices str format (50000 verts): 12.4ms
        [3MF TIMING] write_vertices SubElement loop (50000 verts): 45.1ms
        [3MF TIMING] write_vertices TOTAL (50000 verts): 58.3ms
    """
    if DEBUG_MODE or _is_blender_debug():
        print(f"[3MF TIMING] {label}: {elapsed_ms:.2f}ms")


def warn(*args, **kwargs):
    """Always print a warning message to the console."""
    print("WARNING:", *args, **kwargs)


def error(*args, **kwargs):
    """Always print an error message to the console."""
    print("ERROR:", *args, **kwargs)


def safe_report(operator, level, message):
    """Report a message through Blender's UI if available, with graceful fallback.

    Use this instead of bare ``operator.report()`` so that unit tests
    (which run without a real Blender context) don't crash.

    :param operator: A ``bpy.types.Operator`` instance (or mock).
    :param level: Report level set, e.g. ``{'INFO'}``, ``{'WARNING'}``, ``{'ERROR'}``.
    :param message: The message string.
    """
    try:
        operator.report(level, message)
    except Exception:
        # Running in a test or headless context without full operator support
        if "ERROR" in level:
            error(message)
        elif "WARNING" in level:
            warn(message)
        else:
            debug(message)
