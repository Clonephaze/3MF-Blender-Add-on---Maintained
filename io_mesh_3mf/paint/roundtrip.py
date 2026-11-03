# Blender add-on to import and export 3MF files.
# Copyright (C) 2026 Jack
# This file is part of the Blender 3MF add-on and is distributed under GPL-3.0-or-later.

"""Preserve original Orca paint segmentation for unchanged imported meshes."""

import hashlib
import json
import uuid
from typing import Dict, Mapping, Tuple

import bpy
import numpy as np


_TEXT_PREFIX = ".3mf_paint_source/"


def collision_safe_palette(colors: Mapping[int, str]) -> Dict[int, str]:
    """Choose distinct paint codes with a gap wider than the exporter tolerance."""
    assigned: list[Tuple[int, int, int]] = []
    result = {}
    for index in sorted(colors):
        value = str(colors[index]).lstrip("#")
        try:
            base = tuple(int(value[offset:offset + 2], 16) for offset in (0, 2, 4))
        except (ValueError, IndexError):
            base = (128, 128, 128)

        chosen = None
        for radius in range(65):
            candidates = []
            for red in range(max(0, base[0] - radius), min(255, base[0] + radius) + 1):
                for green in range(max(0, base[1] - radius), min(255, base[1] + radius) + 1):
                    for blue in range(max(0, base[2] - radius), min(255, base[2] + radius) + 1):
                        if max(abs(red - base[0]), abs(green - base[1]), abs(blue - base[2])) != radius:
                            continue
                        candidate = (red, green, blue)
                        if any(all(abs(a - b) < 6 for a, b in zip(candidate, other)) for other in assigned):
                            continue
                        distance = sum((a - b) ** 2 for a, b in zip(candidate, base))
                        candidates.append((distance, candidate))
            if candidates:
                chosen = min(candidates)[1]
                break
        if chosen is None:
            raise ValueError(f"Unable to create a unique paint code for filament slot {index + 1}")
        assigned.append(chosen)
        result[index] = "#%02X%02X%02X" % chosen
    return result


_SIGNATURE_PREFIX = "b2:"


def paint_source_signature(mesh, image, legacy: bool = False) -> str:
    """Hash the mesh geometry, active UVs, and image pixels used by paint export.

    New signatures use blake2b (``b2:`` prefix); ``legacy=True`` reproduces the
    older chunked SHA-256 digest so blend files saved earlier still match.
    """
    digest = hashlib.sha256() if legacy else hashlib.blake2b(digest_size=32)
    mesh.calc_loop_triangles()
    digest.update(len(mesh.vertices).to_bytes(8, "little"))
    digest.update(len(mesh.polygons).to_bytes(8, "little"))
    digest.update(len(mesh.loop_triangles).to_bytes(8, "little"))

    vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", vertices)
    digest.update(vertices.tobytes())

    loop_vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertices)
    digest.update(loop_vertices.tobytes())

    polygon_starts = np.empty(len(mesh.polygons), dtype=np.int32)
    polygon_sizes = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", polygon_starts)
    mesh.polygons.foreach_get("loop_total", polygon_sizes)
    digest.update(polygon_starts.tobytes())
    digest.update(polygon_sizes.tobytes())

    if not mesh.uv_layers or not mesh.uv_layers.active:
        digest.update(b"no-active-uv")
    else:
        uvs = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        mesh.uv_layers.active.data.foreach_get("uv", uvs)
        digest.update(uvs.tobytes())

    width, height = image.size
    digest.update(width.to_bytes(8, "little"))
    digest.update(height.to_bytes(8, "little"))
    # Slicing image.pixels re-reads the buffer per call and is far too slow
    # on 8192x8192 textures; foreach_get reads it once.
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    digest.update(pixels.tobytes())
    return digest.hexdigest() if legacy else _SIGNATURE_PREFIX + digest.hexdigest()


def _signature_matches(mesh, image, signature: str) -> bool:
    legacy = not str(signature).startswith(_SIGNATURE_PREFIX)
    return paint_source_signature(mesh, image, legacy=legacy) == signature


def store_original_segmentation(mesh, segmentation_strings) -> None:
    """Store imported triangle strings and the baseline required to reuse them."""
    if not segmentation_strings:
        return
    image = _paint_image(mesh)
    if image is None or not mesh.uv_layers or not mesh.uv_layers.active:
        return
    name = f"{_TEXT_PREFIX}{uuid.uuid4().hex}.json"
    text = bpy.data.texts.new(name)
    text.write(json.dumps(
        {str(index): value for index, value in segmentation_strings.items()},
        separators=(",", ":"),
    ))
    mesh["3mf_paint_source_text"] = name
    mesh["3mf_paint_source_signature"] = paint_source_signature(mesh, image)
    mesh["3mf_paint_source_face_count"] = len(mesh.polygons)


def unchanged_original_segmentation(mesh, image, source_mesh=None):
    """Return original per-triangle strings only while their source is unchanged."""
    source_mesh = source_mesh or mesh
    text_name = source_mesh.get("3mf_paint_source_text")
    signature = source_mesh.get("3mf_paint_source_signature")
    if not text_name or not signature:
        return None
    source_face_count = source_mesh.get("3mf_paint_source_face_count", -1)
    if len(mesh.polygons) != source_face_count:
        return None
    text = bpy.data.texts.get(text_name)
    if text is None:
        return None
    if not _signature_matches(mesh, image, str(signature)):
        return None
    try:
        source = json.loads(text.as_string())
        result = {int(index): str(value) for index, value in source.items()}
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if any(index < 0 or index >= source_face_count for index in result):
        return None
    return result


def _paint_image(mesh):
    for material in mesh.materials:
        if material is None or not material.use_nodes or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None:
                return node.image
    return None
