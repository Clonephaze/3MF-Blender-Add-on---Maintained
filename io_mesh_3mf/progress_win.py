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
# HTML card template
# __PORT__ is replaced with the real port at startup.
# Design principles (taste-skill): off-black background, electric-blue accent,
# system-ui font, monospace for numbers, SVG icons (no emoji), CSS-only
# animations, hardware-accelerated (transform/opacity only).
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3MF Progress</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%230c0c10'/%3E%3Ccircle cx='8' cy='8' r='5.5' stroke='%233b7ef6' stroke-width='1.5' fill='none'/%3E%3Cpath d='M8 4.5v3.75L10.5 10' stroke='%233b7ef6' stroke-width='1.4' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:          #0c0c10;
    --surface:     #141418;
    --border:      rgba(255,255,255,0.07);
    --accent:      #3b7ef6;
    --accent-dim:  rgba(59,126,246,0.18);
    --text-1:      #f1f1f3;
    --text-2:      #8b8b99;
    --text-3:      #4a4a56;
    --done:        #27272e;
    --radius:      10px;
    --mono:        'JetBrains Mono', 'Cascadia Code', 'Fira Mono', Consolas, monospace;
    --sans:        system-ui, -apple-system, 'Segoe UI', sans-serif;
  }

  html, body {
    width: 100%;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    background: var(--bg);
    color: var(--text-1);
    font-family: var(--sans);
    font-size: 13px;
    -webkit-font-smoothing: antialiased;
    overflow: hidden;
    user-select: none;
  }

  .card {
    display: flex;
    flex-direction: column;
    gap: 0;
    width: 100%;
    height: auto;
    /* Constrain to card size — critical when opened as a full browser tab */
    max-width: 440px;
    min-width: 300px;
    max-height: 340px;
    padding: 14px 16px 14px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    /* Liquid glass edge refraction */
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.055),
                0 8px 32px rgba(0,0,0,0.5);
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 11px;
    min-height: 22px;
  }

  .op-badge {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 8px 2px 6px;
    background: var(--accent-dim);
    border: 1px solid rgba(59,126,246,0.28);
    border-radius: 4px;
    color: var(--accent);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    flex-shrink: 0;
  }

  .op-badge svg { flex-shrink: 0; }

  .filename {
    flex: 1;
    font-size: 12px;
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .elapsed {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-3);
    flex-shrink: 0;
    min-width: 36px;
    text-align: right;
  }

  /* ── Phase stepper ── */
  .stepper {
    display: flex;
    align-items: center;
    gap: 0;
    margin-bottom: 10px;
    overflow: hidden;
  }

  .step {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
    flex: 1;
    position: relative;
    min-width: 0;
  }

  /* connector line between steps */
  .step:not(:last-child)::after {
    content: '';
    position: absolute;
    top: 7px;
    left: calc(50% + 8px);
    right: calc(-50% + 8px);
    height: 1px;
    background: var(--border);
    transition: background 0.4s ease;
  }

  .step.done:not(:last-child)::after {
    background: rgba(59,126,246,0.3);
  }

  .step-dot {
    width: 15px;
    height: 15px;
    border-radius: 50%;
    border: 1.5px solid var(--border);
    background: var(--bg);
    display: flex;
    align-items: center;
    justify-content: center;
    transition: border-color 0.3s ease, background 0.3s ease, box-shadow 0.3s ease;
    flex-shrink: 0;
    position: relative;
    z-index: 1;
  }

  .step.done .step-dot {
    border-color: rgba(59,126,246,0.4);
    background: var(--done);
  }

  .step.active .step-dot {
    border-color: var(--accent);
    background: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
    animation: pulse-dot 2s ease-in-out infinite;
  }

  @keyframes pulse-dot {
    0%, 100% { box-shadow: 0 0 0 3px var(--accent-dim); }
    50%       { box-shadow: 0 0 0 5px rgba(59,126,246,0.1); }
  }

  .step-check {
    display: none;
  }
  .step.done .step-check {
    display: block;
  }
  .step.done .step-ring,
  .step.active .step-ring {
    display: none;
  }

  /* active inner ring */
  .step.active .step-dot::after {
    content: '';
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: #fff;
    position: absolute;
  }

  .step-label {
    font-size: 9px;
    letter-spacing: 0.02em;
    color: var(--text-3);
    text-align: center;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 100%;
    padding: 0 2px;
    transition: color 0.3s ease;
  }

  .step.active .step-label { color: var(--text-1); }
  .step.done  .step-label  { color: var(--text-3); }

  /* ── Progress bar ── */
  .progress-track {
    height: 3px;
    background: rgba(255,255,255,0.06);
    border-radius: 2px;
    overflow: hidden;
    margin-bottom: 9px;
  }

  .progress-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 2px;
    width: 0%;
    transform-origin: left;
    transition: width 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  }

  /* ── Message row ── */
  .msg-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-height: 16px;
    margin-bottom: 9px;
  }

  .message {
    font-size: 11px;
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    transition: opacity 0.2s ease;
  }

  .pct-label {
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-3);
    flex-shrink: 0;
  }

  /* ── Filament swatches ── */
  .swatches {
    display: none;
    flex-wrap: wrap;
    gap: 5px;
    align-items: center;
    margin-bottom: 9px;
    min-height: 18px;
  }

  .swatches.visible { display: flex; }

  .swatch-dot {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 1.5px solid rgba(0,0,0,0.25);
    box-shadow: 0 0 0 1px rgba(255,255,255,0.08);
    flex-shrink: 0;
  }

  .swatch-label {
    font-size: 10px;
    color: var(--text-3);
    margin-left: 1px;
  }

  /* ── Cancel button ── */
  .cancel-wrap {
    display: none;
    margin-top: auto;
  }
  .cancel-wrap.visible { display: block; }

  .cancel-btn {
    width: 100%;
    padding: 7px 0;
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text-2);
    font-family: var(--sans);
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.18s ease, border-color 0.18s ease, color 0.18s ease,
                transform 0.1s ease;
    letter-spacing: 0.01em;
  }

  .cancel-btn:hover {
    background: rgba(255,255,255,0.04);
    border-color: rgba(255,255,255,0.14);
    color: var(--text-1);
  }

  .cancel-btn:active {
    transform: translateY(1px) scale(0.99);
    background: rgba(255,255,255,0.06);
  }

  .cancel-btn:disabled {
    opacity: 0.35;
    cursor: not-allowed;
    transform: none;
  }

  /* ── Done overlay ── */
  .done-flash {
    position: fixed;
    inset: 0;
    background: rgba(59,126,246,0.12);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    font-weight: 500;
    color: var(--accent);
    letter-spacing: 0.05em;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s ease;
    z-index: 10;
  }

  .done-flash.visible { opacity: 1; pointer-events: all; }
</style>
</head>
<body>
<div class="card">

  <!-- Header -->
  <div class="header">
    <span class="op-badge" id="op-badge">
      <!-- SVG icon injected by JS -->
      <svg id="op-icon" width="10" height="10" viewBox="0 0 10 10" fill="none"></svg>
      <span id="op-label">EXPORT</span>
    </span>
    <span class="filename" id="filename" title=""></span>
    <span class="elapsed" id="elapsed">0.0s</span>
  </div>

  <!-- Phase stepper (populated by JS) -->
  <div class="stepper" id="stepper"></div>

  <!-- Progress bar -->
  <div class="progress-track">
    <div class="progress-fill" id="progress-fill"></div>
  </div>

  <!-- Message + percentage -->
  <div class="msg-row">
    <span class="message" id="message"></span>
    <span class="pct-label" id="pct-label">0%</span>
  </div>

  <!-- Filament swatches (bake only) -->
  <div class="swatches" id="swatches"></div>

  <!-- Cancel button -->
  <div class="cancel-wrap" id="cancel-wrap">
    <button class="cancel-btn" id="cancel-btn">Cancel</button>
  </div>

</div>

<!-- Done overlay -->
<div class="done-flash" id="done-flash">
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style="margin-right:6px">
    <path d="M2 7l3.5 3.5L12 3.5" stroke="currentColor" stroke-width="1.8"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>
  Done
</div>

<script>
(function () {
  'use strict';

  const PORT = __PORT__;
  const BASE = 'http://127.0.0.1:' + PORT;

  // ── SVG icon paths per operation type ──
  const OP_ICONS = {
    export: '<path d="M5 1v6M2.5 4.5L5 7l2.5-2.5M1 8v1.5A.5.5 0 001.5 10h7a.5.5 0 00.5-.5V8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
    import: '<path d="M5 9V3M2.5 5.5L5 3l2.5 2.5M1 8v1.5A.5.5 0 001.5 10h7a.5.5 0 00.5-.5V8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
    bake_cycles: '<circle cx="5" cy="5" r="2" stroke="currentColor" stroke-width="1.3"/><path d="M5 1v1M5 8v1M1 5h1M8 5h1M2.05 2.05l.71.71M7.24 7.24l.71.71M7.24 2.76l-.71.71M2.76 7.24l-.71.71" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    bake_vc:     '<path d="M2 7.5C2 5.567 3.343 4 5 4s3 1.567 3 3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="5" cy="3" r="1.2" stroke="currentColor" stroke-width="1.2"/><path d="M1 9h8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
  };

  const OP_LABELS = {
    export:      'EXPORT',
    import:      'IMPORT',
    bake_cycles: 'BAKE',
    bake_vc:     'BAKE',
  };

  // ── DOM refs ──
  const elBadge    = document.getElementById('op-badge');
  const elOpIcon   = document.getElementById('op-icon');
  const elOpLabel  = document.getElementById('op-label');
  const elFilename = document.getElementById('filename');
  const elElapsed  = document.getElementById('elapsed');
  const elStepper  = document.getElementById('stepper');
  const elFill     = document.getElementById('progress-fill');
  const elMessage  = document.getElementById('message');
  const elPct      = document.getElementById('pct-label');
  const elSwatches = document.getElementById('swatches');
  const elCancelW  = document.getElementById('cancel-wrap');
  const elCancelB  = document.getElementById('cancel-btn');
  const elDone     = document.getElementById('done-flash');

  // ── State ──
  let lastPhases       = [];
  let initialized      = false;
  let cancelSent       = false;
  let done             = false;
  let elapsedStart     = Date.now();
  let consecutiveFails = 0;  // auto-close if server becomes unreachable

  // ── Independent elapsed timer — ticks every 100 ms from wall clock ──
  // Driven purely by Date.now() so it never freezes during blocking Blender
  // calls (e.g. bpy.ops.object.bake).  elapsedStart is calibrated once on
  // first server response using the server's elapsed value.
  setInterval(function () {
    if (done) return;
    elElapsed.textContent = formatElapsed((Date.now() - elapsedStart) / 1000);
  }, 100);

  function formatElapsed(s) {
    if (s < 60) return s.toFixed(1) + 's';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return m + 'm ' + (sec < 10 ? '0' : '') + sec + 's';
  }

  // ── Build stepper DOM ──
  function buildStepper(phases) {
    if (!phases || phases.length === 0) { elStepper.style.display = 'none'; return; }
    elStepper.innerHTML = '';
    phases.forEach(function (name) {
      const step = document.createElement('div');
      step.className = 'step';
      step.innerHTML =
        '<div class="step-dot">' +
          '<svg class="step-ring" width="5" height="5" viewBox="0 0 5 5" fill="none">' +
            '<circle cx="2.5" cy="2.5" r="2" stroke="currentColor" stroke-width="1" opacity="0.3"/>' +
          '</svg>' +
          '<svg class="step-check" width="7" height="6" viewBox="0 0 7 6" fill="none">' +
            '<path d="M1 3l2 2 3-4" stroke="rgba(59,126,246,0.7)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>' +
          '</svg>' +
        '</div>' +
        '<span class="step-label" title="' + name + '">' + name + '</span>';
      elStepper.appendChild(step);
    });
    lastPhases = phases;
  }

  // ── Update stepper highlights ──
  function updateStepper(phaseIndex) {
    const steps = elStepper.querySelectorAll('.step');
    steps.forEach(function (el, i) {
      el.classList.remove('done', 'active');
      if (i < phaseIndex)       el.classList.add('done');
      else if (i === phaseIndex) el.classList.add('active');
    });
  }

  // ── Build filament swatches ──
  function buildSwatches(colors) {
    elSwatches.innerHTML = '';
    if (!colors || colors.length === 0) {
      elSwatches.classList.remove('visible');
      return;
    }
    colors.forEach(function (hex, i) {
      const dot = document.createElement('span');
      dot.className = 'swatch-dot';
      dot.style.background = hex;
      dot.title = 'Filament ' + (i + 1);
      elSwatches.appendChild(dot);
    });
    elSwatches.classList.add('visible');
  }

  // ── Apply a full state snapshot ──
  function applyState(d) {
    // First-time initialization
    if (!initialized) {
      initialized = true;
      elapsedStart = Date.now() - ((d.elapsed || 0) * 1000);

      // Operation badge
      const opKey = d.operation || 'export';
      elOpIcon.innerHTML = OP_ICONS[opKey] || OP_ICONS['export'];
      elOpIcon.setAttribute('viewBox', '0 0 10 10');
      elOpLabel.textContent = OP_LABELS[opKey] || 'WORK';

      // Filename
      elFilename.textContent = d.filename || '';
      elFilename.title = d.filename || '';

      // Build stepper from phase list
      buildStepper(d.phases || []);

      // Filament swatches
      buildSwatches(d.filament_colors || []);

      // Cancel button
      if (d.can_cancel) {
        elCancelW.classList.add('visible');
      }
    }

    // Rebuild stepper only if phases changed (shouldn't happen, but guard)
    if (JSON.stringify(d.phases) !== JSON.stringify(lastPhases) && d.phases) {
      buildStepper(d.phases);
    }

    // Progress bar
    const pct = Math.max(0, Math.min(1, d.percent || 0));
    elFill.style.width = (pct * 100).toFixed(1) + '%';
    elPct.textContent = Math.round(pct * 100) + '%';

    // Stepper position
    updateStepper(d.phase_index || 0);

    // Message
    elMessage.textContent = d.message || d.phase || '';

    // Completion
    if (!d.active) {
      done = true;
      elFill.style.width = '100%';
      elPct.textContent = '100%';
      elMessage.textContent = 'Complete';
      updateStepper((d.phases || []).length);  // all done
      elDone.classList.add('visible');
      if (elCancelB) elCancelB.disabled = true;
      // Close after brief pause
      setTimeout(function () { window.close(); }, 900);
    }
  }

  // ── Poll /state ──
  function poll() {
    if (done) return;
    fetch(BASE + '/state')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        consecutiveFails = 0;
        applyState(d);
        if (!done) setTimeout(poll, 250);
      })
      .catch(function () {
        consecutiveFails++;
        // If the server has been unreachable for ~10 s, the Blender-side
        // subprocess was likely killed (e.g. new operation started).
        // Close the stale window rather than leaving it open forever.
        if (consecutiveFails >= 20) { window.close(); return; }
        if (!done) setTimeout(poll, 500);
      });
  }

  // ── Cancel handler ──
  if (elCancelB) {
    elCancelB.addEventListener('click', function () {
      if (cancelSent) return;
      cancelSent = true;
      elCancelB.disabled = true;
      elCancelB.textContent = 'Cancelling\u2026';
      fetch(BASE + '/cancel', { method: 'POST' }).catch(function () {});
    });
  }

  // Start polling
  poll();
})();
</script>
</body>
</html>
"""


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
