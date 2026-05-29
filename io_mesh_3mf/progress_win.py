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
    width: 100%; height: 100%;
    display: flex;
    align-items: center;
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

  /* ── Tab-mode info banner (hidden in app mode) ── */
  .tab-info {
    display: none;  /* shown by JS when not in app mode */
    margin-bottom: 14px;
    padding: 10px 12px;
    background: rgba(59,126,246,0.07);
    border: 1px solid rgba(59,126,246,0.18);
    border-radius: 7px;
    font-size: 11px;
    color: var(--text-2);
    line-height: 1.55;
  }
  .tab-info.visible { display: block; }
  .tab-info strong { color: var(--text-1); font-weight: 600; }
  .tab-info .tip {
    margin-top: 7px;
    padding-top: 7px;
    border-top: 1px solid rgba(255,255,255,0.06);
    color: var(--text-3);
    font-size: 10.5px;
  }
  .tab-info .tip code {
    font-family: var(--mono);
    font-size: 10px;
    background: rgba(255,255,255,0.06);
    padding: 1px 4px;
    border-radius: 3px;
    color: var(--text-2);
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

  /* ── App-mode: card fills the window edge-to-edge, no floating-card look ── */
  @media (display-mode: standalone) {
    html, body { align-items: stretch; justify-content: stretch; }
    .card {
      max-width: 100%;
      max-height: none;
      height: 100vh;
      border-radius: 0;
      border-left: none;
      border-right: none;
      border-bottom: none;
      box-shadow: none;
    }
  }
</style>
</head>
<body>
<div class="card">

  <!-- Tab-mode info banner (hidden in --app mode, shown in regular tab) -->
  <div class="tab-info" id="tab-info">
    <strong>3MF Format — Live Progress</strong><br>
    This tab opened automatically because a long-running 3MF operation is in
    progress in Blender. It will close itself when the operation completes.<br>
    <div class="tip">
      For a compact floating card instead of a full tab, install
      <strong>Chrome</strong> or <strong>Edge</strong> — they support
      a frameless app-window mode.<br>
      To disable this window entirely: <strong>Blender → Edit → Preferences →
      Add-ons → 3MF Format → Advanced → Show Progress Window</strong>
      and toggle it off. You can also use the <code>on_progress</code>
      callback in the Python API for headless use.
    </div>
  </div>

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
  let lastPhases   = [];
  let initialized  = false;
  let cancelSent   = false;
  let done         = false;
  let elapsedStart = Date.now();

  // ── Independent elapsed timer — pure local clock, 0.1 s tick ──
  setInterval(function () {
    if (done) return;
    const s = (Date.now() - elapsedStart) / 1000;
    elElapsed.textContent = formatElapsed(s);
  }, 100);

  function formatElapsed(s) {
    if (s < 60) return s.toFixed(1) + 's';
    const m = Math.floor(s / 60);
    const sec = (s % 60).toFixed(1);
    return m + 'm ' + (parseFloat(sec) < 10 ? '0' : '') + sec + 's';
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
        applyState(d);
        if (!done) setTimeout(poll, 250);
      })
      .catch(function () {
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

  // ── App-mode detection ──
  // display-mode: standalone is true when launched via --app=URL (Chromium).
  // In tab mode we show an info banner explaining what this page is and how to
  // disable it, and we remove the max-height cap so the banner has room.
  var isAppMode = window.matchMedia('(display-mode: standalone)').matches;

  if (!isAppMode) {
    document.getElementById('tab-info').classList.add('visible');
    // Remove the max-height constraint so the banner fits without clipping
    document.querySelector('.card').style.maxHeight = 'none';
  }

  // Position once on initial load — user can freely move the window after
  window.addEventListener('load', function () {
    if (!isAppMode) return;
    var mx = 24, my = 48;
    window.moveTo(mx, screen.availHeight - window.outerHeight - my);
  });
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
    """Return path to Edge, Chrome, or Chromium (supports --app mode), or None."""
    if sys.platform == "darwin":
        for p in [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]:
            if os.path.exists(p):
                return p
        return None

    if sys.platform != "win32":
        import shutil
        for name in ("google-chrome", "chromium-browser", "chromium", "microsoft-edge"):
            found = shutil.which(name)
            if found:
                return found
        return None

    # Windows: registry first, then well-known paths
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
        )
        path, _ = winreg.QueryValueEx(key, "")
        winreg.CloseKey(key)
        if path and os.path.exists(path):
            return path
    except Exception:
        pass
    for p in candidates:
        if os.path.exists(p):
            return p
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
                body = b'{"active":true,"percent":0,"phase":"","phases":[],"phase_index":0,"message":"","elapsed":0,"can_cancel":false,"filament_colors":[]}'
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
    print(f"3MF Progress URL: {url}", flush=True)  # copy this into any browser for testing

    # Pass --force-tab as argv[2] to skip Chromium and open in the default browser
    force_tab = len(sys.argv) > 2 and sys.argv[2] == "--force-tab"

    # Calculate exact window height from initial state — no JS resizing needed.
    # Breakdown (px): Edge title bar ~34 | card v-padding 28 | header 33 |
    #   stepper 37 | progress bar 12 | message row 25 | swatches +27 | cancel +35
    try:
        _init = json.loads(json_path.read_text(encoding="utf-8"))
        _has_swatches = bool(_init.get("filament_colors"))
        _can_cancel = _init.get("can_cancel", False)
    except Exception:
        _has_swatches = False
        _can_cancel = False
    win_h = 200
    if _has_swatches:
        win_h += 27
    if _can_cancel:
        win_h += 35

    # Open the browser from this subprocess — Blender's main thread never
    # calls url_open (see progress.py).  Prefer Chromium --app for a
    # frameless card; fall back to webbrowser.open for a regular tab.
    browser = None if force_tab else _find_chromium()
    if browser:
        subprocess.Popen([
            browser,
            f"--app={url}",
            f"--window-size=440,{win_h}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
        ])
    else:
        import webbrowser
        webbrowser.open(url)

    # ── Wait until the operation completes ──────────────────────────────────
    while True:
        time.sleep(0.25)
        try:
            state = json.loads(json_path.read_text(encoding="utf-8"))
            if not state.get("active", True):
                # Give the browser time to receive the final 100% state
                time.sleep(1.0)
                break
        except Exception:
            pass

    server.shutdown()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    _run(sys.argv[1])
