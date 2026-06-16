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
Progress window subprocess — self-contained HTTP server + HTML card.

This script is spawned as a subprocess by progress.py.  It does NOT import
``bpy`` — it runs under Blender's Python interpreter but entirely outside
Blender's process context.

IPC files (paths received as argv[1]):
  - <json_path>                     JSON state written by Blender
  - <json_path>.parent / 3mf_progress_port.json    signals Blender the port
  - <json_path>.parent / 3mf_progress.cancel       written on Cancel click

The script:
  1. Binds an HTTPServer on 127.0.0.1:<random port>
  2. Starts serve_forever() on a daemon thread
  3. Writes the port file so Blender can call bpy.ops.wm.url_open
  4. Polls until active=False in the JSON, then shuts down the server
"""

import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ---------------------------------------------------------------------------
# HTML card template — loaded from progress_win.html at startup.
# __PORT__ is replaced with the real port before serving.
# ---------------------------------------------------------------------------

_HTML: str = (pathlib.Path(__file__).with_name("progress_win.html")
              .read_text(encoding="utf-8"))
# --- legacy marker so grep can find the template origin ---
_HTML_TEMPLATE_FILE = "progress_win.html"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Bind to port 0 and return the OS-assigned free port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_chromium() -> str | None:
    """Return a path to Chrome, Edge, or Chromium that supports --app, or None.

    Checks (in order):
      1. Windows registry — both HKCU and HKLM, for Edge and Chrome
      2. Well-known install locations including per-user %LOCALAPPDATA% paths
      3. macOS application bundle paths
      4. Linux PATH entries
    """
    if sys.platform == "win32":
        import winreg  # type: ignore

        # Registry: exe name → registry value name to look up
        _reg_names = ["msedge.exe", "chrome.exe", "brave.exe"]
        _reg_hives = [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]
        _reg_base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

        for hive in _reg_hives:
            for exe in _reg_names:
                try:
                    key = winreg.OpenKey(hive, rf"{_reg_base}\{exe}")
                    path, _ = winreg.QueryValueEx(key, "")
                    winreg.CloseKey(key)
                    if path and os.path.exists(path):
                        return path
                except Exception:
                    pass

        # Per-user install paths (%LOCALAPPDATA%)
        local_app = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

        candidates = [
            # Edge — per-user (most common on modern Windows 10/11)
            os.path.join(local_app, r"Microsoft\Edge\Application\msedge.exe"),
            # Edge — system-wide
            os.path.join(program_files, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(program_files_x86, r"Microsoft\Edge\Application\msedge.exe"),
            # Chrome — per-user
            os.path.join(local_app, r"Google\Chrome\Application\chrome.exe"),
            # Chrome — system-wide
            os.path.join(program_files, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(program_files_x86, r"Google\Chrome\Application\chrome.exe"),
            # Brave — per-user
            os.path.join(local_app, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
            # Brave — system-wide
            os.path.join(program_files, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
        for p in candidates:
            if p and os.path.exists(p):
                return p

    elif sys.platform == "darwin":
        for p in [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]:
            if os.path.exists(p):
                return p

    else:
        import shutil
        for name in ("google-chrome", "microsoft-edge", "chromium-browser", "chromium", "brave-browser"):
            found = shutil.which(name)
            if found:
                return found

    return None


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    """Minimal HTTP handler — serves the HTML page, state JSON, and cancel endpoint."""

    html_bytes: bytes = b""
    json_path: pathlib.Path = pathlib.Path()
    cancel_path: pathlib.Path = pathlib.Path()

    def do_GET(self) -> None:
        if self.path == "/":
            self._respond(200, "text/html; charset=utf-8", self.__class__.html_bytes)
        elif self.path == "/state":
            try:
                body = self.__class__.json_path.read_bytes()
            except Exception:
                body = (
                    b'{"active":true,"percent":0,"phase":"","phases":[],'
                    b'"phase_index":0,"message":"","elapsed":0,'
                    b'"can_cancel":false,"filament_colors":[]}'
                )
            self._respond(200, "application/json; charset=utf-8", body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/cancel":
            try:
                self.__class__.cancel_path.write_text("1", encoding="utf-8")
            except Exception:
                pass
            self._respond(200, "text/plain; charset=utf-8", b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def _respond(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Prevent browser caching so state polls always get fresh data
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # Silence the default request/error logging
    def log_message(self, *_) -> None:
        pass

    def log_error(self, *_) -> None:
        pass


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _run(json_path_str: str) -> None:
    json_path = pathlib.Path(json_path_str)
    cancel_path = json_path.parent / "3mf_progress.cancel"
    port_path = json_path.parent / "3mf_progress_port.json"

    port = _free_port()

    # Bake the port into the HTML page
    html_bytes = _HTML.replace("__PORT__", str(port)).encode("utf-8")

    # Wire class-level attributes so handler instances share them
    _Handler.html_bytes = html_bytes
    _Handler.json_path = json_path
    _Handler.cancel_path = cancel_path

    server = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{port}/"

    # ── Open the progress card ────────────────────────────────────────────────
    # Primary: launch a Chromium-based browser with --app=URL so the card
    # appears as a frameless floating window (no tabs, no address bar).
    # Fallback: webbrowser.open() — opens a new tab in whatever the user has
    # set as their default browser.  Either way this subprocess handles it
    # entirely; Blender's main thread does NOT need to call url_open.
    #
    # CRITICAL: we launch with a dedicated --user-data-dir so Chromium spawns
    # a *fresh, independent* browser process instead of handing the URL off to
    # an already-running Chrome/Edge instance.  That guarantees ``browser_proc``
    # is the real window process whose PID we can record and kill later —
    # otherwise stale progress windows can never be closed programmatically.
    browser_pid = None
    profile_dir = json_path.parent / "3mf_progress_profile"
    chromium = _find_chromium()
    if chromium:
        try:
            browser_proc = subprocess.Popen(
                [
                    chromium,
                    f"--app={url}",
                    "--window-size=440,175",
                    f"--user-data-dir={profile_dir}",
                    "--disable-extensions",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-background-networking",
                    "--disable-sync",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            browser_pid = browser_proc.pid
        except Exception:
            import webbrowser
            webbrowser.open(url)
    else:
        import webbrowser
        webbrowser.open(url)

    # Signal Blender that the server is ready.  browser_opened is always True
    # from Blender's perspective — the subprocess has already handled opening
    # the browser, so bpy.ops.wm.url_open must NOT be called.
    # Include both PIDs so Blender can kill the lingering server *and* browser
    # window when starting a new operation (prevents stale windows from
    # surviving after a failed or superseded run).
    port_path.write_text(
        json.dumps({
            "port": port,
            "browser_opened": True,
            "pid": os.getpid(),
            "browser_pid": browser_pid,
        }),
        encoding="utf-8",
    )

    # ── Wait until the operation completes ──────────────────────────────────
    while True:
        time.sleep(0.25)
        try:
            state = json.loads(json_path.read_text(encoding="utf-8"))
            if not state.get("active", True):
                # Give the browser time to receive the final 100% state and
                # run its own window.close() after the "Done" flash.
                time.sleep(1.5)
                break
        except Exception:
            pass

    server.shutdown()

    # Best-effort: ensure the browser window is gone even if its JS failed to
    # self-close (e.g. it was opened as a plain tab via the webbrowser
    # fallback, or window.close() was blocked).  Killing our own dedicated
    # browser process only affects this card, never the user's main browser.
    if browser_pid:
        try:
            _kill_pid(browser_pid)
        except Exception:
            pass


def _kill_pid(pid: int) -> None:
    """Terminate a process by PID, cross-platform, best-effort."""
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)  # PROCESS_TERMINATE
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        import signal as _signal
        os.kill(pid, _signal.SIGTERM)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    _run(sys.argv[1])
