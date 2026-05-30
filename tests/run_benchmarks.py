"""
Performance benchmark runner for the Blender 3MF addon.

Measures import and export time on a real .3mf file using the public API
(no bpy.ops overhead).  Designed to be run manually after significant
changes to establish or compare against a baseline.

Usage
-----
    blender --background --factory-startup -noaudio -q \
            --python tests/run_benchmarks.py -- tests/resources/large_multicolor.3mf

Optional extra args (all after --):
    --modes   STANDARD PAINT   (which import modes to test; default: STANDARD)
    --no-export                 (skip export benchmarks)
    --export-format  AUTO STANDARD PAINT   (export formats; default: AUTO STANDARD)

Example (all modes):
    blender --background --factory-startup -noaudio -q \
            --python tests/run_benchmarks.py -- \
            tests/resources/large_multicolor.3mf \
            --modes STANDARD PAINT \
            --export-format AUTO STANDARD

Baseline
--------
Record the first numbers here after the initial run so future runs can be
compared by eye.

    Blender 5.1.1 — 2026-05-30 — large_multicolor.3mf (494,847 verts / 949,834 tris / 11.3 MB)
    Pre-optimization (from_pydata):

    Operation   Mode      File                                Time Objects         Verts          Tris      Size Warns
    ------------------------------------------------------------------------------------------
    IMPORT      STANDARD  large_multicolor.3mf              11.07s       4       494,847       949,834   11.3 MB     0
    EXPORT      AUTO      bench_output_auto.3mf              3.42s       4       494,847       949,834   11.9 MB     1
    EXPORT      STANDARD  bench_output_standard.3mf          3.34s       5       494,847       949,834   11.9 MB     1

    Post-optimization (numpy foreach_set in create_mesh_from_data):

    Operation   Mode      File                                Time Objects         Verts          Tris      Size Warns
    ------------------------------------------------------------------------------------------
    IMPORT      STANDARD  large_multicolor.3mf              10.55s       4       494,847       949,834   11.3 MB     0
    EXPORT      AUTO      bench_output_auto.3mf              3.37s       4       494,847       949,834   11.9 MB     1
    EXPORT      STANDARD  bench_output_standard.3mf          3.22s       5       494,847       949,834   11.9 MB     1

    Import delta: -0.52s (~5%). XML parsing (~8-9s) dominates; mesh creation saving is real but small at this scale.

Notes
-----
- Single run per operation — these are multi-second operations, averaging adds noise.
- Import is measured end-to-end including Blender mesh creation.
- Export is measured on the objects created by the preceding import run,
  so it reflects realistic scene complexity.
- Warnings are counted but not printed unless --verbose is passed.
"""

import sys
import time
import tempfile
import os
from pathlib import Path

import bpy

# ---------------------------------------------------------------------------
# Bootstrap: add project root so "import io_mesh_3mf" works
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Argument parsing  (everything after "--" on the blender CLI)
# ---------------------------------------------------------------------------

def _parse_args():
    """Parse benchmark CLI args from sys.argv after the '--' separator."""
    raw = sys.argv[:]
    sep = raw.index("--") if "--" in raw else len(raw)
    args = raw[sep + 1:]

    filepath = None
    modes = ["STANDARD"]
    export_formats = ["AUTO", "STANDARD"]
    run_export = True
    verbose = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            if arg == "--modes" and i + 1 < len(args):
                modes = []
                i += 1
                while i < len(args) and not args[i].startswith("--"):
                    modes.append(args[i].upper())
                    i += 1
                continue
            elif arg == "--export-format" and i + 1 < len(args):
                export_formats = []
                i += 1
                while i < len(args) and not args[i].startswith("--"):
                    export_formats.append(args[i].upper())
                    i += 1
                continue
            elif arg == "--no-export":
                run_export = False
            elif arg == "--verbose":
                verbose = True
        else:
            filepath = arg
        i += 1

    return filepath, modes, export_formats, run_export, verbose


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scene_vert_tri_counts():
    """Return (total_verts, total_tris) across all mesh objects in the scene."""
    total_verts = 0
    total_tris = 0
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.data:
            total_verts += len(obj.data.vertices)
            total_tris += len(obj.data.polygons)
    return total_verts, total_tris


def _fmt(seconds):
    """Format a duration nicely."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    m, s = divmod(seconds, 60)
    return f"{int(m)}m {s:.1f}s"


def _fmtn(n):
    """Format an integer with thousands separators."""
    return f"{n:,}"


def _clear_scene():
    """Remove all objects, meshes and materials from the scene."""
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


def _warn_callback(warnings_list):
    def _cb(msg):
        warnings_list.append(msg)
    return _cb


# ---------------------------------------------------------------------------
# Result row formatting
# ---------------------------------------------------------------------------

_COL = {
    "op": 12,
    "mode": 10,
    "file": 30,
    "time": 10,
    "objects": 8,
    "verts": 14,
    "tris": 14,
    "size": 10,
    "warns": 6,
}


def _header():
    return (
        f"{'Operation':<{_COL['op']}}"
        f"{'Mode':<{_COL['mode']}}"
        f"{'File':<{_COL['file']}}"
        f"{'Time':>{_COL['time']}}"
        f"{'Objects':>{_COL['objects']}}"
        f"{'Verts':>{_COL['verts']}}"
        f"{'Tris':>{_COL['tris']}}"
        f"{'Size':>{_COL['size']}}"
        f"{'Warns':>{_COL['warns']}}"
    )


def _row(op, mode, filename, elapsed, n_objects, verts, tris, size_bytes, n_warns):
    size_str = f"{size_bytes / 1_048_576:.1f} MB" if size_bytes else "—"
    verts_str = _fmtn(verts) if verts else "—"
    tris_str = _fmtn(tris) if tris else "—"
    return (
        f"{op:<{_COL['op']}}"
        f"{mode:<{_COL['mode']}}"
        f"{filename:<{_COL['file']}}"
        f"{_fmt(elapsed):>{_COL['time']}}"
        f"{n_objects:>{_COL['objects']}}"
        f"{verts_str:>{_COL['verts']}}"
        f"{tris_str:>{_COL['tris']}}"
        f"{size_str:>{_COL['size']}}"
        f"{n_warns:>{_COL['warns']}}"
    )


# ---------------------------------------------------------------------------
# Benchmark operations
# ---------------------------------------------------------------------------

def bench_import(filepath, mode, verbose):
    """Time one import run. Returns a result row dict."""
    from io_mesh_3mf.api import import_3mf

    _clear_scene()
    filename = Path(filepath).name
    warnings = []

    print(f"  Importing ({mode})…", end="", flush=True)
    t0 = time.perf_counter()
    result = import_3mf(
        filepath,
        import_materials=mode,
        on_warning=_warn_callback(warnings),
    )
    elapsed = time.perf_counter() - t0
    print(f" done — {_fmt(elapsed)}")

    verts, tris = _scene_vert_tri_counts()
    size_bytes = Path(filepath).stat().st_size

    if verbose and warnings:
        for w in warnings:
            print(f"    WARN: {w}")

    return {
        "op": "IMPORT",
        "mode": mode,
        "filename": filename,
        "elapsed": elapsed,
        "n_objects": result.num_loaded,
        "verts": verts,
        "tris": tris,
        "size_bytes": size_bytes,
        "n_warns": len(warnings),
        "status": result.status,
    }


def bench_export(out_path, fmt, n_objects, verbose):
    """Time one export run on the current scene. Returns a result row dict."""
    from io_mesh_3mf.api import export_3mf

    filename = Path(out_path).name
    warnings = []

    print(f"  Exporting ({fmt})…", end="", flush=True)
    t0 = time.perf_counter()
    result = export_3mf(
        out_path,
        use_orca_format=fmt,
        thumbnail_mode="NONE",   # skip thumbnail render — not what we're measuring
        on_warning=_warn_callback(warnings),
    )
    elapsed = time.perf_counter() - t0
    print(f" done — {_fmt(elapsed)}")

    verts, tris = _scene_vert_tri_counts()
    size_bytes = Path(out_path).stat().st_size if Path(out_path).exists() else 0

    if verbose and warnings:
        for w in warnings:
            print(f"    WARN: {w}")

    return {
        "op": "EXPORT",
        "mode": fmt,
        "filename": filename,
        "elapsed": elapsed,
        "n_objects": result.num_written,
        "verts": verts,
        "tris": tris,
        "size_bytes": size_bytes,
        "n_warns": len(warnings),
        "status": result.status,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    filepath, import_modes, export_formats, run_export, verbose = _parse_args()

    if not filepath:
        print("ERROR: No filepath provided.")
        print("  Usage: blender ... --python tests/run_benchmarks.py -- <file.3mf>")
        sys.exit(1)

    filepath = str(Path(filepath).resolve())
    if not Path(filepath).exists():
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    file_mb = Path(filepath).stat().st_size / 1_048_576

    print()
    print("=" * 90)
    print("  BLENDER 3MF ADDON — PERFORMANCE BENCHMARKS")
    print(f"  File:  {filepath}")
    print(f"  Size:  {file_mb:.1f} MB")
    print("=" * 90)
    print()

    rows = []

    # ── Import benchmarks ──────────────────────────────────────────────────
    last_import_mode = None
    for mode in import_modes:
        print(f"[IMPORT mode={mode}]")
        row = bench_import(filepath, mode, verbose)
        rows.append(row)
        last_import_mode = mode
        print()

    # ── Export benchmarks (on scene from last import) ─────────────────────
    if run_export and rows:
        # Reload with STANDARD if last mode was PAINT (paint textures confuse
        # standard export; re-import cleanly for a fair comparison).
        if last_import_mode != "STANDARD" and "STANDARD" in import_modes:
            print("[Re-importing with STANDARD for export benchmarks]")
            bench_import(filepath, "STANDARD", verbose=False)
            print()

        tmpdir = Path(tempfile.mkdtemp(prefix="3mf_bench_"))
        try:
            n_objects = len([o for o in bpy.data.objects if o.type == "MESH"])
            for fmt in export_formats:
                out_path = str(tmpdir / f"bench_output_{fmt.lower()}.3mf")
                print(f"[EXPORT format={fmt}]")
                row = bench_export(out_path, fmt, n_objects, verbose)
                rows.append(row)
                print()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    # ── Results table ──────────────────────────────────────────────────────
    print("=" * 90)
    print("  RESULTS")
    print("=" * 90)
    print(_header())
    print("-" * 90)
    for r in rows:
        status_flag = "" if r["status"] in ("FINISHED", "OK") else f"  ← {r['status']}"
        print(_row(
            r["op"], r["mode"], r["filename"],
            r["elapsed"], r["n_objects"],
            r["verts"], r["tris"],
            r["size_bytes"], r["n_warns"],
        ) + status_flag)
    print("=" * 90)
    print()

    # ── Warnings summary ───────────────────────────────────────────────────
    total_warns = sum(r["n_warns"] for r in rows)
    if total_warns > 0 and not verbose:
        print(f"  {total_warns} warning(s) total. Re-run with --verbose to see them.")
    print()


if __name__ == "__main__":
    main()
