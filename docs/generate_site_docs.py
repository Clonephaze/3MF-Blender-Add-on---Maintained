#!/usr/bin/env python3
"""
Generate Nuxt Content markdown docs from the Python API source.

This script parses ``io_mesh_3mf/api.py`` (and common modules) with the
``ast`` module — no Blender or bpy required — and produces the 5 markdown
files consumed by the Nuxt site's ``@nuxt/content`` docs collection.

Usage::

    # From the repo root:
    python docs/generate_site_docs.py

    # Custom output directory (e.g. the Nuxt site's content folder):
    python docs/generate_site_docs.py --output-dir /path/to/content/docs/3mf

The generated files are:

    1.guide.md          — Getting Started (template + export table from source)
    2.recipes.md        — Recipes (template with verified signatures)
    3.api-reference.md  — Full API reference (auto-generated from source)
    4.discovery.md      — Discovery functions (auto-generated from source)
    5.building-blocks.md — Building block modules (auto-generated from source)
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Paths ─────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
API_PY = REPO_ROOT / "io_mesh_3mf" / "api.py"
COLORS_PY = REPO_ROOT / "io_mesh_3mf" / "common" / "colors.py"
UNITS_PY = REPO_ROOT / "io_mesh_3mf" / "common" / "units.py"
TYPES_PY = REPO_ROOT / "io_mesh_3mf" / "common" / "types.py"
SEGMENTATION_PY = REPO_ROOT / "io_mesh_3mf" / "common" / "segmentation.py"
EXTENSIONS_PY = REPO_ROOT / "io_mesh_3mf" / "common" / "extensions.py"
METADATA_PY = REPO_ROOT / "io_mesh_3mf" / "common" / "metadata.py"


# ── AST helpers ───────────────────────────────────────────────────────────

@dataclass
class ParamInfo:
    """Extracted parameter metadata."""
    name: str
    annotation: str = ""
    default: str = ""
    description: str = ""  # from docstring :param: lines


@dataclass
class FuncInfo:
    """Extracted function metadata."""
    name: str
    params: List[ParamInfo] = field(default_factory=list)
    return_annotation: str = ""
    docstring: str = ""
    lineno: int = 0


@dataclass
class ClassInfo:
    """Extracted dataclass metadata."""
    name: str
    fields: List[ParamInfo] = field(default_factory=list)
    docstring: str = ""
    lineno: int = 0


@dataclass
class ConstInfo:
    """Extracted module-level constant."""
    name: str
    value_repr: str = ""
    comment: str = ""
    lineno: int = 0


def _unparse_annotation(node: ast.expr) -> str:
    """Convert an AST annotation node to a readable string."""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _unparse_default(node: ast.expr) -> str:
    """Convert default value node to a string."""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _get_docstring(node: ast.AST) -> str:
    """Extract docstring from a function or class node."""
    if (node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)):
        val = node.body[0].value
        return val.value if isinstance(val.value, str) else ""
    return ""


def _parse_param_descriptions(docstring: str) -> Dict[str, str]:
    """Extract :param name: description lines from a docstring."""
    descriptions: Dict[str, str] = {}
    if not docstring:
        return descriptions
    pattern = re.compile(r':param\s+(\w+):\s*(.*?)(?=\n\s*:|$)', re.DOTALL)
    for match in pattern.finditer(docstring):
        name = match.group(1)
        desc = match.group(2).strip()
        # Collapse continuation lines
        desc = re.sub(r'\s*\n\s+', ' ', desc)
        descriptions[name] = desc
    return descriptions


def _parse_return_description(docstring: str) -> str:
    """Extract :return: description from a docstring.

    Stops at Example:: blocks to avoid leaking code into the description.
    """
    match = re.search(r':return:\s*(.*?)(?=\n\s*:|\n\s*Example::|$)', docstring, re.DOTALL)
    if match:
        desc = match.group(1).strip()
        return re.sub(r'\s*\n\s+', ' ', desc)
    return ""


def extract_functions(tree: ast.Module, names: Optional[set] = None) -> List[FuncInfo]:
    """Extract function definitions from a module AST."""
    results = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith('_'):
                continue
            if names and node.name not in names:
                continue

            docstring = _get_docstring(node)
            param_descs = _parse_param_descriptions(docstring)

            params = []
            args = node.args
            # Positional and keyword-only args
            all_args = list(args.args) + list(args.kwonlyargs)
            # Defaults: args.defaults align to the END of args.args;
            # args.kw_defaults align 1:1 with args.kwonlyargs
            num_pos = len(args.args)
            pos_defaults = [None] * (num_pos - len(args.defaults)) + list(args.defaults)
            kw_defaults = list(args.kw_defaults)

            for i, arg in enumerate(all_args):
                if arg.arg == 'self':
                    continue
                ann = _unparse_annotation(arg.annotation) if arg.annotation else ""
                # Determine default value
                default = ""
                if i < num_pos:
                    d = pos_defaults[i] if i < len(pos_defaults) else None
                    if d is not None:
                        default = _unparse_default(d)
                else:
                    kw_idx = i - num_pos
                    d = kw_defaults[kw_idx] if kw_idx < len(kw_defaults) else None
                    if d is not None:
                        default = _unparse_default(d)

                params.append(ParamInfo(
                    name=arg.arg,
                    annotation=ann,
                    default=default,
                    description=param_descs.get(arg.arg, ""),
                ))

            # **kwargs
            if args.kwarg:
                params.append(ParamInfo(
                    name=f"**{args.kwarg.arg}",
                    annotation="",
                    default="",
                    description=param_descs.get(args.kwarg.arg, ""),
                ))

            ret = _unparse_annotation(node.returns) if node.returns else ""

            results.append(FuncInfo(
                name=node.name,
                params=params,
                return_annotation=ret,
                docstring=docstring,
                lineno=node.lineno,
            ))
    return results


def extract_classes(tree: ast.Module, names: Optional[set] = None) -> List[ClassInfo]:
    """Extract dataclass definitions from a module AST."""
    results = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            if names and node.name not in names:
                continue
            docstring = _get_docstring(node)
            fields = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    ann = _unparse_annotation(item.annotation) if item.annotation else ""
                    default = ""
                    if item.value is not None:
                        default = _unparse_default(item.value)
                    fields.append(ParamInfo(
                        name=item.target.id,
                        annotation=ann,
                        default=default,
                    ))
            results.append(ClassInfo(
                name=node.name,
                fields=fields,
                docstring=docstring,
                lineno=node.lineno,
            ))
    return results


def extract_constants(tree: ast.Module, source_lines: List[str],
                      names: Optional[set] = None) -> List[ConstInfo]:
    """Extract module-level constant assignments."""
    results = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if names and target.id not in names:
                        continue
                    if target.id.startswith('_'):
                        continue
                    value_repr = _unparse_default(node.value)
                    # Check for inline #: comment on preceding line
                    comment = ""
                    if node.lineno >= 2:
                        prev = source_lines[node.lineno - 2].strip()
                        if prev.startswith("#:"):
                            comment = prev[2:].strip()
                    results.append(ConstInfo(
                        name=target.id,
                        value_repr=value_repr,
                        comment=comment,
                        lineno=node.lineno,
                    ))
    return results


def _extract_attributes_from_docstring(docstring: str) -> Dict[str, str]:
    """Extract Attributes: section from a dataclass docstring.

    Returns {field_name: description}.
    Handles multi-line indented descriptions and nested list items.
    """
    descs: Dict[str, str] = {}
    if not docstring:
        return descs
    # Find "Attributes:" block — grab everything after it
    match = re.search(r'Attributes:\s*\n(.*)', docstring, re.DOTALL)
    if not match:
        return descs
    block = match.group(1)
    current_name = None
    current_desc: List[str] = []
    for line in block.split('\n'):
        stripped = line.strip()
        # Lines like "status: ``"FINISHED"`` on success, ..."
        field_match = re.match(r'^(\w+):\s*(.*)', stripped)
        if field_match and not stripped.startswith('-'):
            if current_name:
                descs[current_name] = ' '.join(current_desc).strip()
            current_name = field_match.group(1)
            current_desc = [field_match.group(2)]
        elif current_name and stripped:
            # Continuation line — append unless it's a new section
            current_desc.append(stripped)
    if current_name:
        descs[current_name] = ' '.join(current_desc).strip()
    return descs


# ── Module parsers ────────────────────────────────────────────────────────

def _parse_module(path: Path) -> Tuple[ast.Module, List[str]]:
    """Parse a Python file, returning (AST, source_lines)."""
    source = path.read_text(encoding='utf-8')
    return ast.parse(source), source.splitlines()


def _extract_color_functions(tree: ast.Module) -> List[FuncInfo]:
    """Extract public functions from colors.py."""
    target = {
        "srgb_to_linear", "linear_to_srgb",
        "hex_to_rgb", "hex_to_linear_rgb",
        "rgb_to_hex", "linear_rgb_to_hex",
    }
    return extract_functions(tree, target)


def _extract_unit_dicts(tree: ast.Module, source_lines: List[str]) -> List[ConstInfo]:
    """Extract dict constants from units.py."""
    results = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith('_'):
                    # Only include the top-level dicts and functions
                    results.append(ConstInfo(
                        name=target.id,
                        value_repr="dict" if isinstance(node.value, ast.Dict) else _unparse_default(node.value),
                        lineno=node.lineno,
                    ))
    return results


def _extract_unit_functions(tree: ast.Module) -> List[FuncInfo]:
    """Extract public functions from units.py."""
    return [f for f in extract_functions(tree) if not f.name.startswith('_')]


def _extract_types_classes(tree: ast.Module) -> List[ClassInfo]:
    """Extract all dataclass definitions from types.py."""
    return extract_classes(tree)


def _extract_segmentation_classes(tree: ast.Module) -> List[ClassInfo]:
    """Extract key classes from segmentation.py."""
    target = {"SegmentationDecoder", "SegmentationEncoder", "SegmentationNode", "TriangleSubdivider"}
    return extract_classes(tree, target)


def _extract_extension_classes(tree: ast.Module) -> List[ClassInfo]:
    """Extract key classes from extensions.py."""
    target = {"Extension", "ExtensionManager"}
    return extract_classes(tree, target)


# ── Markdown generation ──────────────────────────────────────────────────

def _md_escape(text: str) -> str:
    """Escape pipe characters for markdown tables."""
    return text.replace('|', '\\|').replace('\n', ' ')


def _clean_description(desc: str) -> str:
    """Clean up RST-style markup in descriptions for markdown."""
    # Convert ``code`` to `code`
    desc = re.sub(r'``(.*?)``', r'`\1`', desc)
    # Convert :func:`name` to `name`
    desc = re.sub(r':(?:func|class|mod|meth|attr):`([^`]+)`', r'`\1`', desc)
    # Convert *text* (RST emphasis) — leave as-is (also valid markdown)
    return desc.strip()


# Canonical order & descriptions for API_CAPABILITIES entries
_CAPABILITY_COMMENTS = {
    "import": "import_3mf() available",
    "export": "export_3mf() available",
    "inspect": "inspect_3mf() available",
    "batch": "batch_import/batch_export available",
    "callbacks": "on_progress, on_warning, on_object_created",
    "target_collection": "import to specific collection",
    "orca_format": "Orca/BambuStudio export format",
    "prusa_format": "PrusaSlicer export format",
    "paint_mode": "MMU paint segmentation",
    "project_template": "Custom Orca project template",
    "object_settings": "Per-object Orca settings",
    "building_blocks": "colors, types, segmentation sub-namespaces",
}


def _format_capabilities(value_repr: str) -> str:
    """Format a frozenset literal into a readable multi-line block."""
    # Extract string items from the repr
    items = re.findall(r"'([^']+)'", value_repr)
    # Sort by canonical order
    ordered = [k for k in _CAPABILITY_COMMENTS if k in items]
    # Add any extras not in our canonical list
    for item in items:
        if item not in ordered:
            ordered.append(item)

    lines = ["API_CAPABILITIES = frozenset({"]
    for cap in ordered:
        comment = _CAPABILITY_COMMENTS.get(cap, "")
        pad = max(1, 23 - len(cap))
        if comment:
            lines.append(f'    "{cap}",{" " * pad}# {comment}')
        else:
            lines.append(f'    "{cap}",')
    lines.append("})")
    return "\n".join(lines)


def _signature_str(func: FuncInfo) -> str:
    """Build a Python function signature string."""
    parts = []
    has_kw_only = False
    for p in func.params:
        if p.name.startswith('**'):
            parts.append(p.name)
            continue
        piece = p.name
        if p.annotation:
            # Simplify Optional[X] to X | None
            ann = p.annotation
            ann = re.sub(r'Optional\[(.+)\]', r'\1 | None', ann)
            piece += f": {ann}"
        if p.default:
            piece += f" = {p.default}"
        parts.append(piece)

    sig = f"def {func.name}("
    # Check if there are keyword-only params (after a bare *)
    # We detect this by checking if the original function had keyword-only args
    # For simplicity, just join all params
    sig += ",\n    ".join(parts)
    sig += f",\n)"
    if func.return_annotation:
        ret = func.return_annotation
        ret = re.sub(r'Optional\[(.+)\]', r'\1 | None', ret)
        sig += f" -> {ret}"
    return sig


def _param_table(func: FuncInfo) -> str:
    """Generate a markdown parameter table for a function."""
    lines = [
        "| Parameter | Type | Default | Description |",
        "| --- | --- | --- | --- |",
    ]
    for p in func.params:
        name = f"`{p.name}`"
        ann = f"`{p.annotation}`" if p.annotation else ""
        # Simplify annotation display
        ann = ann.replace('Optional[', '').rstrip(']`') + '`' if 'Optional[' in ann else ann
        if 'Optional' in ann:
            ann = ann.replace('Optional[', '').replace(']', ' | None')
        # Fix None annotations
        ann = re.sub(r'Optional\[(.+?)\]', r'\1 \\| None', ann)
        if p.name.startswith('**'):
            default = ""
        elif p.default:
            default = f"`{p.default}`"
        else:
            default = "*required*"
        desc = _md_escape(_clean_description(p.description))
        lines.append(f"| {name} | {ann} | {default} | {desc} |")
    return "\n".join(lines)


def _simple_param_table(func: FuncInfo) -> str:
    """Generate a 3-column parameter table (no default column)."""
    lines = [
        "| Parameter | Type | Description |",
        "| --- | --- | --- |",
    ]
    for p in func.params:
        name = f"`{p.name}`"
        ann = f"`{p.annotation}`" if p.annotation else ""
        desc = _md_escape(_clean_description(p.description))
        lines.append(f"| {name} | {ann} | {desc} |")
    return "\n".join(lines)


def _field_table(cls: ClassInfo) -> str:
    """Generate a field table for a dataclass."""
    attr_descs = _extract_attributes_from_docstring(cls.docstring)
    lines = [
        "| Field | Type | Description |",
        "| --- | --- | --- |",
    ]
    for f in cls.fields:
        name = f"`{f.name}`"
        ann = f"`{f.annotation}`" if f.annotation else ""
        desc = _md_escape(_clean_description(attr_descs.get(f.name, "")))
        lines.append(f"| {name} | {ann} | {desc} |")
    return "\n".join(lines)


# ── Page generators ──────────────────────────────────────────────────────

def generate_guide(api_funcs: List[FuncInfo], consts: List[ConstInfo]) -> str:
    """Generate 1.guide.md — Getting Started page."""
    # This is mostly prose with some verified references to the API
    return textwrap.dedent("""\
        ---
        title: Getting Started
        description: Programmatic 3MF import, export, and inspection for Blender — without bpy.ops.
        ---

        # Getting Started

        The public API in `io_mesh_3mf.api` provides headless/programmatic access to the full 3MF pipeline. It runs the same code as the Blender operators but skips UI-specific behaviour (progress bars, popups, camera zoom), making it suitable for:

        - **CLI automation** — batch processing from Blender's `--python` mode
        - **Addon integration** — other Blender addons importing/exporting 3MF
        - **Headless pipelines** — render farms, CI/CD, asset processing
        - **Custom workflows** — building on top of the low-level building blocks

        ## Quick Start

        ```python
        from io_mesh_3mf.api import import_3mf, export_3mf, inspect_3mf

        # Import a 3MF file
        result = import_3mf("/path/to/model.3mf")
        print(result.status, result.num_loaded)

        # Export selected objects
        result = export_3mf("/path/to/output.3mf", use_selection=True)
        print(result.status, result.num_written)

        # Inspect without importing (no Blender objects created)
        info = inspect_3mf("/path/to/model.3mf")
        print(info.unit, info.num_objects, info.num_triangles_total)
        ```

        All functions return lightweight dataclasses — they never raise exceptions for normal failures (corrupt files, empty scenes, etc.). Check `result.status` instead.

        ## Export Format Reference

        The export dispatch uses a three-way mode controlled by `use_orca_format`:

        | `use_orca_format` | `mmu_slicer_format` | Output |
        | --- | --- | --- |
        | `"AUTO"` | — | Chooses best format based on scene content |
        | `"STANDARD"` | — | Spec-compliant single-model 3MF |
        | `"PAINT"` | `"ORCA"` | Multi-file Orca/Bambu structure with `paint_color` attributes |
        | `"PAINT"` | `"PRUSA"` | Single-file with `slic3rpe:mmu_segmentation` hash strings |

        In **AUTO** mode the addon inspects your scene and picks the best path:

        - Objects with MMU paint textures → Orca exporter with segmentation
        - Objects with material slots → Standard exporter with basematerials/colorgroups
        - Geometry-only objects → Standard exporter, geometry only
        - If `project_template` or `object_settings` is provided → Orca exporter

        ## Callbacks

        All three callback types are optional and work the same way across `import_3mf`, `export_3mf`, and the batch helpers.

        ```python
        def on_progress(percentage: int, message: str):
            \\"\\"\\"Called with 0-100 percentage and a status message.\\"\\"\\"
            print(f"[{percentage:3d}%] {message}")

        def on_warning(message: str):
            \\"\\"\\"Called for each warning (non-manifold geometry, missing data, etc.).\\"\\"\\"
            print(f"WARNING: {message}")

        def on_object_created(blender_object, resource_id: str):
            \\"\\"\\"Called after each Blender object is built during import.\\"\\"\\"
            blender_object.color = (1, 0, 0, 1)  # Tint red
        ```

        ## Error Handling

        All API functions return result dataclasses instead of raising exceptions. Check `result.status`:

        ```python
        result = import_3mf("model.3mf")
        if result.status == "FINISHED":
            print(f"Success: {result.num_loaded} objects")
        else:
            print(f"Failed: {result.warnings}")
        ```

        - Archive-level errors (corrupt ZIP, missing model files) set `status = "CANCELLED"`.
        - Per-object warnings (non-manifold geometry, missing textures) are collected in `warnings` but don't prevent completion.
        - `inspect_3mf` uses `status = "OK"` / `"ERROR"` with a separate `error_message` field.

        ## CLI Usage

        Run from the command line using Blender's `--python` flag:

        ```bash
        # Inspect a file
        blender --background --python-expr "
        from io_mesh_3mf.api import inspect_3mf
        info = inspect_3mf('model.3mf')
        print(f'{info.num_objects} objects, {info.num_triangles_total} triangles')
        "

        # Batch convert
        blender --background --python my_script.py
        ```

        **Example script** (`convert_to_orca.py`):

        ```python
        \\"\\"\\"Convert a standard 3MF to Orca Slicer format.\\"\\"\\"
        import sys
        from io_mesh_3mf.api import import_3mf, export_3mf

        input_path = sys.argv[sys.argv.index("--") + 1]
        output_path = input_path.replace(".3mf", "_orca.3mf")

        result = import_3mf(input_path, import_materials="MATERIALS")
        if result.status == "FINISHED":
            export_result = export_3mf(
                output_path,
                objects=result.objects,
                use_orca_format="AUTO",
            )
            print(f"Converted: {export_result.num_written} objects → {output_path}")
        ```

        ```bash
        blender --background --python convert_to_orca.py -- input.3mf
        ```

        ## Notes

        - **Blender context required** — `import_3mf` and `export_3mf` need `bpy.context`. They work in `--background` mode but not outside Blender entirely.
        - **inspect_3mf is lightweight** — it only opens the ZIP and parses XML. No Blender objects, materials, or images are created.
        - **Thread safety** — Blender's Python API is not thread-safe. Don't call these functions from background threads.
        - **Batch isolation** — `batch_import` and `batch_export` catch per-file exceptions so one failure doesn't stop the batch.
        - **API vs addon version** — `API_VERSION` tracks the API contract stability. It increments independently of the addon release version.
    """).replace('\\"\\"\\"', '"""')


def generate_recipes() -> str:
    """Generate 2.recipes.md — practical code patterns."""
    return textwrap.dedent("""\
        ---
        title: Recipes
        description: Practical code patterns for common 3MF workflows.
        ---

        # Recipes

        Ready-to-use patterns for common workflows.

        ## Import with Material Painting

        ```python
        from io_mesh_3mf.api import import_3mf

        result = import_3mf(
            "/models/multicolor.3mf",
            import_materials="PAINT",
            import_location="ORIGIN",
        )

        for obj in result.objects:
            print(f"  {obj.name}: {len(obj.data.vertices)} verts")
        ```

        ## Import into a Specific Collection

        ```python
        result = import_3mf(
            "/models/part.3mf",
            target_collection="Imported Parts",
            reuse_materials=True,
        )
        ```

        ## Export for Orca Slicer

        The export dispatch uses a three-way mode: `AUTO`, `STANDARD`, or `PAINT`.

        - **AUTO** (default) — detects materials and paint data, choosing the best exporter automatically.
        - **STANDARD** — always uses the spec-compliant StandardExporter.
        - **PAINT** — forces segmentation export for multi-material painting.

        ```python
        from io_mesh_3mf.api import export_3mf
        import bpy

        cubes = [o for o in bpy.data.objects if o.type == "MESH" and "Cube" in o.name]

        result = export_3mf(
            "/output/cubes.3mf",
            objects=cubes,
            use_orca_format="AUTO",
        )
        print(f"Exported {result.num_written} objects")
        ```

        ## Export for PrusaSlicer with MMU Paint

        ```python
        result = export_3mf(
            "/output/painted.3mf",
            use_orca_format="PAINT",
            mmu_slicer_format="PRUSA",
            use_selection=True,
        )
        ```

        ## Custom Orca Project Template

        Use a custom printer/filament profile extracted from Orca Slicer:

        ```python
        result = export_3mf(
            "/output/custom_printer.3mf",
            use_orca_format="PAINT",
            mmu_slicer_format="ORCA",
            project_template="/templates/bambu_x1c_asa.json",
            object_settings={
                supports_obj: {
                    "layer_height": "0.12",
                    "wall_loops": "2",
                    "sparse_infill_density": "10%",
                },
                detail_part: {
                    "layer_height": "0.08",
                    "outer_wall_speed": "50",
                },
            },
        )
        ```

        ::alert{type="info"}
        **Getting custom templates:** Export a project from Orca Slicer as `.3mf`, open the archive with a ZIP tool, and extract `Metadata/project_settings.config`. This JSON file contains all printer, filament, and print settings. The addon patches `filament_colour` automatically based on your painted objects.
        ::

        ## Round-Trip Conversion

        ```python
        from io_mesh_3mf.api import import_3mf, export_3mf

        # Import from one format, export to another
        result = import_3mf("/input/prusa_model.3mf", import_materials="PAINT")
        if result.status == "FINISHED":
            export_3mf(
                "/output/orca_model.3mf",
                objects=result.objects,
                use_orca_format="PAINT",
                mmu_slicer_format="ORCA",
            )
        ```

        ## Inspect Without Importing

        ```python
        from io_mesh_3mf.api import inspect_3mf

        info = inspect_3mf("/models/assembly.3mf")

        if info.status == "OK":
            print(f"Unit: {info.unit}")
            print(f"Objects: {info.num_objects}")
            print(f"Total triangles: {info.num_triangles_total}")
            print(f"Vendor: {info.vendor_format or 'standard'}")
            print(f"Extensions: {info.extensions_used}")

            for obj in info.objects:
                flags = []
                if obj["has_materials"]:
                    flags.append("materials")
                if obj["has_segmentation"]:
                    flags.append("MMU paint")
                print(f"  {obj['name']}: {obj['num_triangles']} tris [{', '.join(flags)}]")
        else:
            print(f"Error: {info.error_message}")
        ```

        ## Batch Operations

        ```python
        from io_mesh_3mf.api import batch_import, batch_export
        import bpy

        # Import multiple files with per-file error isolation
        results = batch_import(
            ["part_a.3mf", "part_b.3mf", "part_c.3mf"],
            import_materials="PAINT",
            target_collection="Batch Import",
        )

        total = sum(r.num_loaded for r in results)
        failed = [r for r in results if r.status != "FINISHED"]
        print(f"Imported {total} objects, {len(failed)} failures")

        # Export multiple files
        cubes = [o for o in bpy.data.objects if "Cube" in o.name]
        spheres = [o for o in bpy.data.objects if "Sphere" in o.name]

        results = batch_export(
            [
                ("cubes.3mf", cubes),
                ("spheres.3mf", spheres),
                ("everything.3mf", None),  # None = all scene objects
            ],
            use_orca_format="AUTO",
        )
        ```
    """)


def generate_api_reference(
    funcs: List[FuncInfo],
    classes: List[ClassInfo],
    consts: List[ConstInfo],
) -> str:
    """Generate 3.api-reference.md — complete reference page."""
    sections = []

    # Frontmatter
    sections.append(textwrap.dedent("""\
        ---
        title: API Reference
        description: Complete parameter reference for all 3MF API functions.
        ---

        # API Reference

        All functions live in `io_mesh_3mf.api`. Import them directly:

        ```python
        from io_mesh_3mf.api import import_3mf, export_3mf, inspect_3mf
        ```
    """))

    # Version & Capabilities
    api_version = None
    api_caps = None
    for c in consts:
        if c.name == "API_VERSION":
            api_version = c
        elif c.name == "API_CAPABILITIES":
            api_caps = c

    sections.append("## Version & Capabilities\n")
    if api_version:
        sections.append(f"```python\nAPI_VERSION = {api_version.value_repr}\n"
                        f"API_VERSION_STRING = \".\".join(str(v) for v in API_VERSION)\n")
    if api_caps:
        # Format the frozenset nicely with one entry per line and comments
        caps_repr = _format_capabilities(api_caps.value_repr)
        sections.append(f"\n{caps_repr}\n```\n")
    else:
        sections.append("```\n")

    # Result Dataclasses
    sections.append("## Result Dataclasses\n")
    class_map = {c.name: c for c in classes}
    for cls_name in ("ImportResult", "ExportResult", "InspectResult"):
        cls = class_map.get(cls_name)
        if cls:
            sections.append(f"### {cls_name}\n")
            sections.append(_field_table(cls))
            sections.append("")

    # Functions
    func_map = {f.name: f for f in funcs}
    for func_name in ("import_3mf", "export_3mf", "inspect_3mf", "batch_import", "batch_export"):
        func = func_map.get(func_name)
        if not func:
            continue

        sections.append(f"## {func_name}\n")

        # Signature block
        sig = _signature_str(func)
        sections.append(f"```python\n{sig}\n```\n")

        # Brief description (first line of docstring)
        if func.docstring:
            first_line = func.docstring.strip().split('\n')[0]
            sections.append(f"{_clean_description(first_line)}\n")

        # Parameter table
        if func.params:
            has_defaults = any(p.default for p in func.params)
            if has_defaults:
                sections.append(_param_table(func))
            else:
                sections.append(_simple_param_table(func))
            sections.append("")

        # Return value
        if func.return_annotation:
            ret = func.return_annotation
            ret = re.sub(r'Optional\[(.+)\]', r'\1 | None', ret)
            ret_desc = _parse_return_description(func.docstring)
            ret_text = f"`{ret}`"
            if ret_desc:
                ret_text += f" — {_clean_description(ret_desc)}"
            sections.append(f"**Returns:** {ret_text}\n")

    # Callback Types
    sections.append("## Callback Types\n")
    sections.append("| Type | Signature | Description |")
    sections.append("| --- | --- | --- |")
    sections.append("| `ProgressCallback` | `(int, str) -> None` | `(percentage 0-100, message)` |")
    sections.append("| `WarningCallback` | `(str,) -> None` | `(warning_message)` |")
    sections.append("| `ObjectCreatedCallback` | `(Any, str) -> None` | `(blender_object, resource_id)` |")
    sections.append("")

    return "\n".join(sections)


def generate_discovery(funcs: List[FuncInfo]) -> str:
    """Generate 4.discovery.md — API discovery functions."""
    sections = []

    sections.append(textwrap.dedent("""\
        ---
        title: API Discovery
        description: Detect and feature-check the 3MF API from other Blender addons.
        ---

        # API Discovery

        Other Blender addons can detect and use the 3MF API at runtime.  Three strategies are available, from simplest to most robust.

        ## Direct Import (recommended)

        If you know the addon is installed, a plain `try`/`except` is the simplest approach:

        ```python
        try:
            from io_mesh_3mf.api import import_3mf, export_3mf
        except ImportError:
            import_3mf = export_3mf = None
        ```

        This is Python-idiomatic and survives Blender restarts.

        ## Discovery Functions
    """))

    func_map = {f.name: f for f in funcs}
    for func_name in ("is_available", "get_api", "has_capability", "check_version"):
        func = func_map.get(func_name)
        if not func:
            continue

        sections.append(f"### {func_name}\n")

        sig = _signature_str(func)
        sections.append(f"```python\n{sig}\n```\n")

        if func.docstring:
            first_line = func.docstring.strip().split('\n')[0]
            sections.append(f"{_clean_description(first_line)}\n")

        if func.params:
            sections.append(_simple_param_table(func))
            sections.append("")

        if func.return_annotation:
            ret = func.return_annotation
            ret_desc = _parse_return_description(func.docstring)
            ret_text = f"`{ret}`"
            if ret_desc:
                ret_text += f" — {_clean_description(ret_desc)}"
            sections.append(f"**Returns:** {ret_text}\n")

    # Standalone helper
    sections.append(textwrap.dedent("""\
        ## Standalone Discovery Helper

        For addons that want **zero runtime dependency** on the 3MF addon, copy `io_mesh_3mf/threemf_discovery.py` into your addon. It resolves the addon's import path automatically via `addon_utils`, caches the result, and works regardless of extension repo prefix or addon load order.

        ```python
        # In your addon — no dependency on io_mesh_3mf at import time
        from .threemf_discovery import get_threemf_api

        api = get_threemf_api()
        if api is not None:
            if api.has_capability("paint_mode"):
                result = api.export_3mf("output.3mf", use_orca_format="PAINT")
        ```

        The standalone module also provides `import_3mf`, `export_3mf`, and `inspect_3mf` as convenience wrappers that return `None` if the 3MF addon isn't installed.
    """))

    return "\n".join(sections)


def generate_building_blocks(
    color_funcs: List[FuncInfo],
    unit_funcs: List[FuncInfo],
    type_classes: List[ClassInfo],
    seg_classes: List[ClassInfo],
    ext_classes: List[ClassInfo],
) -> str:
    """Generate 5.building-blocks.md — low-level module docs."""
    sections = []

    sections.append(textwrap.dedent("""\
        ---
        title: Building Blocks
        description: Low-level modules re-exported by the API for custom workflows.
        ---

        # Building Blocks

        The API re-exports common modules for custom workflows. These are the same modules used internally by the import/export pipeline.

        ```python
        from io_mesh_3mf.api import colors, types, segmentation, units
        ```

        ## Colors

        `io_mesh_3mf.common.colors` — hex/RGB conversion and sRGB/linear transforms.

    """))

    # Colors table
    sections.append("| Function | Signature | Description |")
    sections.append("| --- | --- | --- |")
    for func in color_funcs:
        params = ", ".join(
            f"{p.name}: {p.annotation}" if p.annotation else p.name
            for p in func.params
        )
        ret = func.return_annotation or ""
        sig = f"`({params}) -> {ret}`" if ret else f"`({params})`"
        desc = ""
        if func.docstring:
            desc = _clean_description(func.docstring.strip().split('\n')[0])
        sections.append(f"| `{func.name}` | {sig} | {desc} |")
    sections.append("")

    # Units
    sections.append("## Units\n")
    sections.append("`io_mesh_3mf.common.units` — unit conversion between 3MF and Blender.\n")

    sections.append("| Variable | Description |")
    sections.append("| --- | --- |")
    sections.append("| `threemf_to_metre` | Dict mapping 3MF unit strings to metre scale factors |")
    sections.append("| `blender_to_metre` | Dict mapping Blender unit strings to metre scale factors |")
    sections.append("")

    sections.append("| Function | Signature | Description |")
    sections.append("| --- | --- | --- |")
    for func in unit_funcs:
        params = ", ".join(
            f"{p.name}: {p.annotation}" if p.annotation else p.name
            for p in func.params
        )
        ret = func.return_annotation or ""
        sig = f"`({params}) -> {ret}`" if ret else f"`({params})`"
        desc = ""
        if func.docstring:
            desc = _clean_description(func.docstring.strip().split('\n')[0])
        sections.append(f"| `{func.name}` | {sig} | {desc} |")
    sections.append("")

    # Types
    sections.append("## Types (Dataclasses)\n")
    sections.append("`io_mesh_3mf.common.types` — internal data structures used throughout the pipeline.\n")
    sections.append("Key dataclasses:\n")
    for cls in type_classes:
        desc = ""
        if cls.docstring:
            desc = _clean_description(cls.docstring.strip().split('\n')[0])
        sections.append(f"- **`{cls.name}`** — {desc}")
    sections.append("")

    # Segmentation
    sections.append("## Segmentation Codec\n")
    sections.append("`io_mesh_3mf.common.segmentation` — encode/decode hex segmentation strings "
                    "used for multi-material paint data.\n")
    sections.append("| Class | Description |")
    sections.append("| --- | --- |")
    for cls in seg_classes:
        desc = ""
        if cls.docstring:
            desc = _clean_description(cls.docstring.strip().split('\n')[0])
        sections.append(f"| `{cls.name}` | {desc} |")
    sections.append("")
    sections.append("The codec implements the recursive 4-bit nibble encoding used by Orca Slicer "
                    "and PrusaSlicer, where each nibble `xxyy` encodes the state (2 bits) and "
                    "split direction (2 bits) of a triangle subdivision.\n")

    # Extensions
    sections.append("## Extensions\n")
    sections.append("`io_mesh_3mf.common.extensions` — extension registry and validation.\n")
    sections.append("| Class | Description |")
    sections.append("| --- | --- |")
    for cls in ext_classes:
        desc = ""
        if cls.docstring:
            desc = _clean_description(cls.docstring.strip().split('\n')[0])
        sections.append(f"| `{cls.name}` | {desc} |")
    sections.append("")

    # Metadata
    sections.append("## Metadata\n")
    sections.append("`io_mesh_3mf.common.metadata` — metadata storage and merging.\n")

    return "\n".join(sections)


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate Nuxt Content markdown docs from the Python API source."
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=None,
        help="Output directory for the generated .md files. "
             "Defaults to content/docs/3mf/ in the Nuxt site if found, "
             "otherwise ./generated_docs/",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Print output paths without writing files.",
    )
    args = parser.parse_args()

    # Resolve output directory
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        # Try to detect the Nuxt site relative to the repo
        nuxt_candidate = REPO_ROOT.parent.parent.parent / "Web Development" / "my-site-nuxt2" / "content" / "docs" / "3mf"
        if nuxt_candidate.exists():
            out_dir = nuxt_candidate
        else:
            out_dir = REPO_ROOT / "generated_docs"
    out_dir = out_dir.resolve()

    print(f"Output directory: {out_dir}")
    print(f"Parsing {API_PY.relative_to(REPO_ROOT)}...")

    # ── Parse API module ──
    api_tree, api_lines = _parse_module(API_PY)

    api_funcs = extract_functions(api_tree, {
        "import_3mf", "export_3mf", "inspect_3mf",
        "batch_import", "batch_export",
        "is_available", "get_api", "has_capability", "check_version",
    })
    api_classes = extract_classes(api_tree, {
        "ImportResult", "ExportResult", "InspectResult",
    })
    api_consts = extract_constants(api_tree, api_lines, {
        "API_VERSION", "API_VERSION_STRING", "API_CAPABILITIES",
    })

    print(f"  Found {len(api_funcs)} functions, {len(api_classes)} classes, {len(api_consts)} constants")

    # ── Parse building-block modules ──
    print(f"Parsing building-block modules...")

    color_tree, _ = _parse_module(COLORS_PY)
    color_funcs = _extract_color_functions(color_tree)
    print(f"  colors: {len(color_funcs)} functions")

    unit_tree, _ = _parse_module(UNITS_PY)
    unit_funcs = _extract_unit_functions(unit_tree)
    print(f"  units: {len(unit_funcs)} functions")

    types_tree, _ = _parse_module(TYPES_PY)
    type_classes = _extract_types_classes(types_tree)
    print(f"  types: {len(type_classes)} classes")

    seg_tree, _ = _parse_module(SEGMENTATION_PY)
    seg_classes = _extract_segmentation_classes(seg_tree)
    print(f"  segmentation: {len(seg_classes)} classes")

    ext_tree, _ = _parse_module(EXTENSIONS_PY)
    ext_classes = _extract_extension_classes(ext_tree)
    print(f"  extensions: {len(ext_classes)} classes")

    # ── Generate pages ──
    pages = {
        "1.guide.md": generate_guide(api_funcs, api_consts),
        "2.recipes.md": generate_recipes(),
        "3.api-reference.md": generate_api_reference(api_funcs, api_classes, api_consts),
        "4.discovery.md": generate_discovery(api_funcs),
        "5.building-blocks.md": generate_building_blocks(
            color_funcs, unit_funcs, type_classes, seg_classes, ext_classes,
        ),
    }

    # ── Write output ──
    if args.dry_run:
        print("\n[DRY RUN] Would write:")
        for name, content in pages.items():
            path = out_dir / name
            lines = content.count('\n')
            print(f"  {path}  ({lines} lines)")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in pages.items():
        path = out_dir / name
        path.write_text(content, encoding='utf-8')
        lines = content.count('\n')
        print(f"  Wrote {path.name}  ({lines} lines)")

    print(f"\nDone! Generated {len(pages)} files in {out_dir}")


if __name__ == "__main__":
    main()
