"""
Integration tests for OrcaSlicer per-slot mixed filament round trips.

The fixture ``mixed_filament_orca.3mf`` was saved by OrcaSlicer 2.5.0-dev with
ten slots: five physical (slots 4 and 5 share ``#00C1AE``) and five mixed
(6-10, one of them a gradient).  Five cylinders use slots 6-10 through the
object ``extruder``.  Painted and >17-slot variants are derived from it here
so the test data stays a real slicer file.

Every check reads the raw archive XML/JSON, so the importer is not used to
validate its own output.
"""

import itertools
import json
import re
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import bpy
from test_base import Blender3mfTestCase

from io_mesh_3mf.api import export_3mf, import_3mf
from io_mesh_3mf.common.colors import srgb_to_linear
from io_mesh_3mf.common.color_recipes import recompute_mixed_slot_colors
from io_mesh_3mf.common.mixed_slots import (
    FULLSPECTRUM_KEYS,
    MIXED_KEYS,
    mixed_slot_flags,
    parse_mixed_slots,
    resize_slot_arrays,
)
from io_mesh_3mf.common.segmentation import decode_segmentation_string
from io_mesh_3mf.export_3mf.materials.base import ORCA_FILAMENT_CODES
from io_mesh_3mf.import_3mf.archive import get_stashed_config
from io_mesh_3mf.paint.mixed_filament_editor import (
    _gradient_preview_colors,
    _replace_image_color,
    _write_project_settings,
)
from io_mesh_3mf.paint.roundtrip import (
    collision_safe_palette,
    store_original_segmentation,
    unchanged_original_segmentation,
)

FIXTURE = Path(__file__).parent.parent / "resources" / "mixed_filament_orca.3mf"
CONFIG = "Metadata/project_settings.config"
MODEL_SETTINGS = "Metadata/model_settings.config"
PAINT_MODEL = "3D/Objects/Cylinder_8.model"
CORE_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PROD_NS = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"


def _config(path):
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read(CONFIG))


def _paint_triangles(xml_text, paint):
    counter = itertools.count()

    def sub(match):
        code = paint.get(next(counter))
        if code is None:
            return match.group(0)
        return match.group(0)[:-2] + f' paint_color="{code}"/>'

    return re.sub(r"<triangle [^>]*/>", sub, xml_text)


def _triangle_attributes(xml_text, triangle_attrs):
    counter = itertools.count()

    def sub(match):
        attrs = triangle_attrs.get(next(counter))
        if not attrs:
            return match.group(0)
        return match.group(0)[:-2] + "".join(
            f" {key}={quoteattr(value)}" for key, value in attrs.items()
        ) + "/>"

    return re.sub(r"<triangle [^>]*/>", sub, xml_text)


def _make_variant(
    dst,
    config_fn=None,
    paint=None,
    settings_fn=None,
    triangle_attrs=None,
    extra_files=None,
):
    """Copy the fixture, editing its config, paint, settings and extra entries."""
    extra_files = dict(extra_files or {})
    with zipfile.ZipFile(FIXTURE) as src, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if config_fn and info.filename == CONFIG:
                cfg = json.loads(data)
                config_fn(cfg)
                data = json.dumps(cfg, indent=4).encode()
            elif paint and info.filename == PAINT_MODEL:
                data = _paint_triangles(data.decode(), paint).encode()
            elif triangle_attrs and info.filename == PAINT_MODEL:
                data = _triangle_attributes(data.decode(), triangle_attrs).encode()
            elif settings_fn and info.filename == MODEL_SETTINGS:
                data = settings_fn(data.decode()).encode()
            if info.filename in extra_files:
                data = extra_files.pop(info.filename)
            out.writestr(info.filename, data)
        for name, data in extra_files.items():
            out.writestr(name, data)


def _leaf_states(node):
    if node.children:
        for child in node.children:
            yield from _leaf_states(child)
    else:
        yield int(node.state)


def _object_extruders(path):
    """``{object id: extruder}`` from model_settings.config."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read(MODEL_SETTINGS))
    out = {}
    for obj in root.iter("object"):
        for meta in obj.findall("metadata"):
            if meta.get("key") == "extruder":
                out[obj.get("id")] = int(meta.get("value"))
    return out


def _build_items(path):
    """``[(object id, rounded translation, object model path)]`` of build items."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    paths = {}
    for obj in root.iter(f"{CORE_NS}object"):
        comp = obj.find(f"{CORE_NS}components/{CORE_NS}component")
        if comp is not None:
            paths[obj.get("id")] = comp.get(f"{PROD_NS}path", "").lstrip("/")
    items = []
    for item in root.iter(f"{CORE_NS}item"):
        values = [float(v) for v in item.get("transform").split()]
        translation = tuple(round(v, 1) for v in values[9:12])
        items.append((item.get("objectid"), translation, paths.get(item.get("objectid"))))
    return items


def _extruders_by_position(path):
    extruders = _object_extruders(path)
    return sorted((translation, extruders[obj_id]) for obj_id, translation, _ in _build_items(path))


def _effective_states(path, translation):
    """Per-triangle state tuples of the object at ``translation``.

    Unpainted triangles and state-0 leaves resolve to the object extruder.
    """
    extruders = _object_extruders(path)
    for obj_id, item_translation, model_path in _build_items(path):
        if item_translation == translation:
            break
    else:
        raise AssertionError(f"No build item at {translation}")
    extruder = extruders[obj_id]
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read(model_path))
    states = []
    for tri in root.iter(f"{CORE_NS}triangle"):
        code = tri.get("paint_color")
        if not code:
            states.append((extruder,))
            continue
        leaves = {s or extruder for s in _leaf_states(decode_segmentation_string(code))}
        states.append(tuple(sorted(leaves)))
    return states


def _paint_translation():
    """Build-item translation of the object stored in PAINT_MODEL."""
    for _, translation, model_path in _build_items(FIXTURE):
        if model_path == PAINT_MODEL:
            return translation
    raise AssertionError("fixture layout changed")


def _extend_to_20_slots(cfg):
    resize_slot_arrays(cfg, 10, list(range(10)) + [0] * 10, new_slots=range(10, 20))
    cfg["filament_colour"][10:] = [
        "#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#00FFFF",
        "#FF00FF", "#FF8000", "#8000FF", "#0080FF", "#80FF00",
    ]


class MixedFilamentRoundTripBase(Blender3mfTestCase):
    def round_trip(self, src, import_materials="MATERIALS"):
        result = import_3mf(str(src), import_materials=import_materials)
        self.assertEqual(result.status, "FINISHED")
        out = self.temp_file
        exported = export_3mf(str(out), use_orca_format="AUTO")
        self.assertEqual(exported.status, "FINISHED")
        return result, out

    def assertStatesEqual(self, got, expected):
        self.assertEqual(len(got), len(expected), "triangle count")
        diff = [i for i, (a, b) in enumerate(zip(got, expected)) if a != b]
        if diff:
            i = diff[0]
            self.fail(f"{len(diff)} triangles differ; first #{i}: {got[i]} != {expected[i]}")


class TestFixtureRoundTrip(MixedFilamentRoundTripBase):
    def test_project_settings_identical(self):
        _, out = self.round_trip(FIXTURE)
        src, rt = _config(FIXTURE), _config(out)
        differing = sorted(k for k in set(src) | set(rt) if src.get(k) != rt.get(k))
        self.assertEqual(differing, [])

    def test_mixed_arrays_and_colours_by_slot(self):
        _, out = self.round_trip(FIXTURE)
        src, rt = _config(FIXTURE), _config(out)
        for key in sorted(set(MIXED_KEYS) | {"filament_colour", "filament_self_index"}):
            if key in src:
                self.assertEqual(rt.get(key), src[key], key)
        self.assertEqual(len(rt["flush_volumes_matrix"]), 100)
        self.assertEqual(len(rt["flush_volumes_vector"]), 20)

    def test_stale_mixed_swatches_recomputed_on_import_and_export(self):
        src_path = self.temp_file.with_name(self.temp_file.stem + "_stale_swatch.3mf")

        def stale_first_mixed_swatch(config):
            config["filament_colour"][5] = "#123456"

        _make_variant(src_path, config_fn=stale_first_mixed_swatch)
        _, out = self.round_trip(src_path)

        fixture_config = _config(FIXTURE)
        expected = recompute_mixed_slot_colors(
            fixture_config["filament_colour"],
            parse_mixed_slots(fixture_config),
        )
        output_config = _config(out)
        self.assertEqual(output_config["filament_colour"], expected)
        self.assertEqual(output_config["filament_multi_colour"][5], expected[5])

        materials = {
            material.get("3mf_filament_slot"): material
            for material in bpy.data.materials
            if material.get("3mf_extruder_material")
        }
        color = materials[6].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value
        expected_linear = [
            srgb_to_linear(int(expected[5][index:index + 2], 16) / 255.0)
            for index in (1, 3, 5)
        ]
        for got, want in zip(color[:3], expected_linear):
            self.assertAlmostEqual(got, want, places=4)

    def test_object_extruders_preserved(self):
        _, out = self.round_trip(FIXTURE)
        self.assertEqual(_extruders_by_position(out), _extruders_by_position(FIXTURE))
        self.assertEqual(sorted(e for _, e in _extruders_by_position(out)), [6, 7, 8, 9, 10])

    def test_no_paint_on_extruder_only_objects(self):
        _, out = self.round_trip(FIXTURE)
        with zipfile.ZipFile(out) as z:
            for name in z.namelist():
                if name.endswith(".model"):
                    self.assertNotIn(b"paint_color=", z.read(name), name)

    def test_paint_mode_round_trip(self):
        _, out = self.round_trip(FIXTURE, import_materials="PAINT")
        src, rt = _config(FIXTURE), _config(out)
        for key in sorted(set(MIXED_KEYS) | {"filament_colour"}):
            if key in src:
                self.assertEqual(rt.get(key), src[key], key)
        self.assertEqual(_extruders_by_position(out), _extruders_by_position(FIXTURE))

    def test_extruder_material_colour_is_linearised(self):
        self.round_trip(FIXTURE)
        mats = {m.get("3mf_filament_slot"): m for m in bpy.data.materials if m.get("3mf_extruder_material")}
        self.assertEqual(sorted(mats), [6, 7, 8, 9, 10])
        principled = mats[7].node_tree.nodes.get("Principled BSDF")
        colour = principled.inputs["Base Color"].default_value
        for got, byte in zip(colour[:3], (0x68, 0x04, 0x50)):
            self.assertAlmostEqual(got, srgb_to_linear(byte / 255.0), places=4)

    def test_double_round_trip_is_stable(self):
        _, first = self.round_trip(FIXTURE)
        self.clean_scene()
        second = first.with_name(first.stem + "_2.3mf")
        import_3mf(str(first))
        self.assertEqual(export_3mf(str(second), use_orca_format="AUTO").status, "FINISHED")
        self.assertEqual(_config(second), _config(FIXTURE))


class TestPaintedRoundTrip(MixedFilamentRoundTripBase):
    PAINT = {
        **{i: ORCA_FILAMENT_CODES[2] for i in range(0, 100)},
        **{i: ORCA_FILAMENT_CODES[4] for i in range(100, 200)},
        **{i: ORCA_FILAMENT_CODES[5] for i in range(200, 300)},
        **{i: ORCA_FILAMENT_CODES[9] for i in range(300, 400)},
    }

    def setUp(self):
        super().setUp()
        self.src = self.temp_file.with_name(self.temp_file.stem + "_painted.3mf")
        _make_variant(self.src, paint=self.PAINT)

    def test_per_triangle_states_preserved(self):
        _, out = self.round_trip(self.src)
        where = _paint_translation()
        self.assertStatesEqual(_effective_states(out, where), _effective_states(self.src, where))

    def test_paint_import_mode_preserves_original_segmentation(self):
        _, out = self.round_trip(self.src, import_materials="PAINT")
        where = _paint_translation()
        self.assertStatesEqual(_effective_states(out, where), _effective_states(self.src, where))

    def test_same_hex_slots_stay_distinct(self):
        _, out = self.round_trip(self.src)
        states = _effective_states(out, _paint_translation())
        self.assertEqual(set(states[100:200]), {(4,)})
        self.assertEqual(set(states[200:300]), {(5,)})

    def test_config_unchanged_by_paint(self):
        _, out = self.round_trip(self.src)
        self.assertEqual(_config(out), _config(FIXTURE))


class TestManySlotsRoundTrip(MixedFilamentRoundTripBase):
    PAINT = {
        **{i: ORCA_FILAMENT_CODES[18] for i in range(0, 50)},
        **{i: ORCA_FILAMENT_CODES[20] for i in range(50, 100)},
        **{i: ORCA_FILAMENT_CODES[3] for i in range(100, 150)},
    }

    def setUp(self):
        super().setUp()
        self.src = self.temp_file.with_name(self.temp_file.stem + "_20slots.3mf")
        _make_variant(self.src, config_fn=_extend_to_20_slots, paint=self.PAINT)

    def test_materials_mode_preserves_high_states(self):
        _, out = self.round_trip(self.src)
        where = _paint_translation()
        self.assertStatesEqual(_effective_states(out, where), _effective_states(self.src, where))
        self.assertEqual(_config(out), _config(self.src))

    def test_paint_mode_preserves_high_states(self):
        _, out = self.round_trip(self.src, import_materials="PAINT")
        states = set(itertools.chain.from_iterable(_effective_states(out, _paint_translation())))
        self.assertTrue({3, 18, 20} <= states, states)
        rt, src = _config(out), _config(self.src)
        self.assertEqual(rt["filament_colour"], src["filament_colour"])
        self.assertEqual(rt["filament_is_mixed"], src["filament_is_mixed"])


class TestFullSpectrumFallback(MixedFilamentRoundTripBase):
    def setUp(self):
        super().setUp()
        self.src = self.temp_file.with_name(self.temp_file.stem + "_fs.3mf")

        def add_fs_keys(cfg):
            cfg["mixed_filament_definitions"] = "1,2,50,1"
            cfg["mixed_filament_gradient_mode"] = "1"

        def use_virtual_slot(text):
            return text.replace('<metadata key="extruder" value="9"/>', '<metadata key="extruder" value="12"/>', 1)

        _make_variant(self.src, config_fn=add_fs_keys, settings_fn=use_virtual_slot)

    def test_virtual_slot_falls_back_with_warning(self):
        result, out = self.round_trip(self.src)
        self.assertTrue(any("FullSpectrum" in w for w in result.warnings), result.warnings)
        extruders = sorted(e for _, e in _extruders_by_position(out))
        self.assertEqual(extruders, [1, 6, 7, 8, 10])

    def test_fullspectrum_keys_removed_on_export(self):
        _, out = self.round_trip(self.src)
        rt = _config(out)
        self.assertFalse(FULLSPECTRUM_KEYS & set(rt))
        self.assertEqual(rt["filament_colour"], _config(FIXTURE)["filament_colour"])


class TestOrcaMetadataRoundTrip(MixedFilamentRoundTripBase):
    def setUp(self):
        super().setUp()
        self.src = self.temp_file.with_name(self.temp_file.stem + "_metadata.3mf")

        def add_settings(text):
            root = ET.fromstring(text)
            obj = root.find(".//object")
            part = obj.find("part")
            ET.SubElement(
                obj, "metadata", key="test_wrapper_setting", value="wrapper-value"
            )
            ET.SubElement(
                part, "metadata", key="test_part_setting", value="part-value"
            )
            return ET.tostring(root, encoding="unicode")

        _make_variant(
            self.src,
            settings_fn=add_settings,
            triangle_attrs={
                0: {"paint_texture_0": "01,02"},
                4: {"paint_texture_3": "mask&value"},
                8: {"paint_texture_7": "ff"},
            },
            extra_files={
                "Metadata/filament_sequence.json": b'{"0":{"sequence":[1,3,2]}}',
                "Metadata/texture_displacement/101.json": b'{"layers":[]}',
                "Metadata/texture_displacement/101_0.png": b"\x89PNG\r\n\x1a\nphase2b",
            },
        )

    def test_slicer_metadata_round_trip(self):
        _, out = self.round_trip(self.src)

        def triangle_attributes_at(path, translation):
            for _, item_translation, model_path in _build_items(path):
                if item_translation == translation and model_path:
                    with zipfile.ZipFile(path) as archive:
                        root = ET.fromstring(archive.read(model_path))
                    return [
                        {
                            key: value
                            for key, value in triangle.attrib.items()
                            if key.startswith("paint_texture_")
                        }
                        for triangle in root.iter(f"{CORE_NS}triangle")
                    ]
            self.fail(f"No model at {translation} in {path}")

        with zipfile.ZipFile(self.src) as src_zip, zipfile.ZipFile(out) as out_zip:
            for path in (
                "Metadata/filament_sequence.json",
                "Metadata/texture_displacement/101.json",
                "Metadata/texture_displacement/101_0.png",
            ):
                self.assertEqual(out_zip.read(path), src_zip.read(path), path)

            self.assertEqual(
                triangle_attributes_at(out, _paint_translation()),
                triangle_attributes_at(self.src, _paint_translation()),
            )

            settings_root = ET.fromstring(out_zip.read(MODEL_SETTINGS))
            wrapper_metadata = {
                meta.get("key"): meta.get("value")
                for meta in settings_root.findall(".//object/metadata")
            }
            part_metadata = {
                meta.get("key"): meta.get("value")
                for meta in settings_root.findall(".//part/metadata")
            }
            self.assertEqual(wrapper_metadata["test_wrapper_setting"], "wrapper-value")
            self.assertEqual(part_metadata["test_part_setting"], "part-value")


class TestMixedFilamentAuthoring(Blender3mfTestCase):
    def setUp(self):
        super().setUp()
        _write_project_settings(_config(FIXTURE))

    def _project_settings(self):
        raw = get_stashed_config(CONFIG)
        self.assertIsNotNone(raw)
        return json.loads(raw.decode("utf-8"))

    def test_add_mixed_reuses_existing_fixed_recipe(self):
        before = self._project_settings()
        result = bpy.ops.mmu.add_mixed_project_filament(
            "EXEC_DEFAULT",
            component_a=2,
            component_b=3,
            ratio_a=50,
        )
        self.assertEqual(result, {"FINISHED"})
        after = self._project_settings()
        self.assertEqual(after["filament_colour"], before["filament_colour"])
        self.assertEqual(after["filament_is_mixed"], before["filament_is_mixed"])

    def test_slot_operators_remap_and_keep_mixed_slots_editable(self):
        self.assertEqual(
            bpy.ops.mmu.add_project_filament(
                "EXEC_DEFAULT",
                color=(0.2, 0.3, 0.4),
                filament_type="PETG",
            ),
            {"FINISHED"},
        )
        settings = self._project_settings()
        self.assertEqual(len(settings["filament_colour"]), 11)
        self.assertEqual(settings["filament_type"][5], "PETG")
        self.assertEqual(settings["filament_is_mixed"][:6], ["0"] * 6)
        self.assertEqual(settings["filament_is_mixed"][6:], ["1"] * 5)

        self.assertEqual(
            bpy.ops.mmu.edit_project_filament(
                "EXEC_DEFAULT",
                slot_index=1,
                color=(0.1, 0.2, 0.3),
                filament_type="PLA Basic",
            ),
            {"FINISHED"},
        )
        for slot_index, color in (
            (2, (0.0, 0.0, 0.0)),
            (3, (236 / 255, 0.0, 140 / 255)),
        ):
            self.assertEqual(
                bpy.ops.mmu.edit_project_filament(
                    "EXEC_DEFAULT",
                    slot_index=slot_index,
                    color=color,
                    filament_type="PLA Basic",
                ),
                {"FINISHED"},
            )
        self.assertEqual(self._project_settings()["filament_type"][:3], ["PLA Basic"] * 3)
        self.assertEqual(
            bpy.ops.mmu.add_mixed_project_filament(
                "EXEC_DEFAULT",
                component_a=1,
                component_b=2,
                ratio_a=60,
            ),
            {"FINISHED"},
        )
        self.assertEqual(
            bpy.ops.mmu.edit_mixed_project_filament(
                "EXEC_DEFAULT",
                slot_index=12,
                component_count="3",
                component_a=1,
                component_b=2,
                component_c=3,
                ratio_a=40,
                ratio_b=35,
            ),
            {"FINISHED"},
        )
        three_component = next(
            slot for slot in parse_mixed_slots(self._project_settings())
            if slot.slot == 11
        )
        self.assertEqual(three_component.components, [1, 2, 3])
        self.assertEqual(three_component.ratios, [0.4, 0.35, 0.25])

        self.assertEqual(
            bpy.ops.mmu.add_mixed_project_filament(
                "EXEC_DEFAULT",
                component_a=1,
                component_b=2,
                ratio_a=50,
                gradient=True,
            ),
            {"FINISHED"},
        )
        gradient = next(
            slot for slot in parse_mixed_slots(self._project_settings())
            if slot.slot == 12
        )
        self.assertTrue(gradient.gradient)
        authored_settings = self._project_settings()
        bpy.ops.mesh.primitive_cube_add()
        bpy.context.active_object["3mf_object_extruder"] = 13
        self.assertEqual(
            export_3mf(str(self.temp_file), use_orca_format="AUTO").status,
            "FINISHED",
        )
        exported_settings = _config(self.temp_file)
        for key in (*MIXED_KEYS, "filament_colour"):
            self.assertEqual(exported_settings[key], authored_settings[key], key)
        self.clean_scene()
        self.assertEqual(
            import_3mf(str(self.temp_file), import_materials="NONE").status,
            "FINISHED",
        )
        reimported_settings = json.loads(get_stashed_config(CONFIG).decode("utf-8"))
        for key in (*MIXED_KEYS, "filament_colour"):
            self.assertEqual(reimported_settings[key], authored_settings[key], key)
        self.assertEqual(
            bpy.ops.mmu.remove_mixed_project_filament(
                "EXEC_DEFAULT", slot_index=13,
            ),
            {"FINISHED"},
        )
        with self.assertRaisesRegex(RuntimeError, "selected slot is not a mixed filament"):
            bpy.ops.mmu.remove_mixed_project_filament(
                "EXEC_DEFAULT", slot_index=1,
            )
        self.assertEqual(
            bpy.ops.mmu.remove_project_filament(
                "EXEC_DEFAULT",
                slot_index=6,
                replacement_slot=1,
            ),
            {"FINISHED"},
        )
        final_settings = self._project_settings()
        self.assertEqual(len(final_settings["filament_colour"]), 11)
        self.assertEqual(sum(mixed_slot_flags(final_settings)), 6)
        self.assertEqual(
            [slot.slot for slot in parse_mixed_slots(final_settings)],
            list(range(5, 11)),
        )

        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        self.assertEqual(
            bpy.ops.mmu.assign_project_filament(
                "EXEC_DEFAULT", slot_index=6,
            ),
            {"FINISHED"},
        )
        self.assertEqual(obj["3mf_object_extruder"], 6)

    def test_gradient_ramp_preview_uses_curve_anchors(self):
        settings = _config(FIXTURE)
        linear = _gradient_preview_colors(settings, [1, 2], "", (0.9, 0.1))
        curved = _gradient_preview_colors(
            settings, [1, 2], "0,0.9|0.5,0.9|1,0.1", (0.9, 0.1),
        )
        self.assertEqual(len(curved), 5)
        self.assertNotEqual(linear[2], curved[2])

    def test_recommendation_can_create_missing_standard_bases(self):
        before = self._project_settings()
        result = bpy.ops.mmu.recommend_project_filament(
            "EXEC_DEFAULT",
            target_color=(0.6, 0.3, 0.2),
            recipe_mode="CMYW",
            material_type="PLA Basic",
            create_missing_base=True,
        )
        self.assertEqual(result, {"FINISHED"})
        after = self._project_settings()
        self.assertGreater(len(after["filament_colour"]), len(before["filament_colour"]))
        self.assertEqual(len(parse_mixed_slots(after)), len(parse_mixed_slots(before)) + 1)

    def test_auto_export_preserves_mixed_settings_and_object_assignment(self):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.active_object
        obj["3mf_object_extruder"] = 6

        result = export_3mf(str(self.temp_file), use_orca_format="AUTO")
        self.assertEqual(result.status, "FINISHED", result.warnings)
        self.assertEqual(_config(self.temp_file), _config(FIXTURE))
        self.assertIn(6, _object_extruders(self.temp_file).values())


class TestPaintRoundTripHelpers(Blender3mfTestCase):
    def test_bake_palette_includes_mixed_slots(self):
        _write_project_settings(_config(FIXTURE))
        from io_mesh_3mf.paint.bake import (
            _get_filament_colors_from_settings,
            _stashed_project_paint_palette,
        )

        palette = _stashed_project_paint_palette()
        self.assertEqual(palette["physical_count"], 5)
        self.assertEqual(palette["mixed_slots"], list(range(5, 10)))
        self.assertEqual(len(palette["paint"]), 10)
        self.assertEqual(len(_get_filament_colors_from_settings(bpy.context)), 10)

    def test_collision_safe_palette_and_edited_texture_invalidation(self):
        palette = collision_safe_palette({
            0: "#AABBCC",
            1: "#AABBCC",
            2: "#AABBCC",
        })
        self.assertEqual(len(set(palette.values())), 3)
        for first in palette.values():
            a = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
            for second in palette.values():
                if first == second:
                    continue
                b = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
                self.assertGreaterEqual(max(abs(x - y) for x, y in zip(a, b)), 6)

        mesh = bpy.data.meshes.new("PaintRoundTripTestMesh")
        mesh.from_pydata(
            [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
            [],
            [(0, 1, 2)],
        )
        uv_layer = mesh.uv_layers.new(name="MMU_Paint")
        for loop, uv in zip(uv_layer.data, ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))):
            loop.uv = uv
        material = bpy.data.materials.new("PaintRoundTripTestMaterial")
        material.use_nodes = True
        image = bpy.data.images.new("PaintRoundTripTestImage", 4, 4, alpha=True)
        image.pixels.foreach_set([17 / 255, 17 / 255, 17 / 255, 1.0] * 16)
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        mesh.materials.append(material)

        try:
            store_original_segmentation(mesh, {0: "4"})
            self.assertEqual(
                unchanged_original_segmentation(mesh, image),
                {0: "4"},
            )
            _replace_image_color(image, "#111111", "#222222")
            self.assertIsNone(unchanged_original_segmentation(mesh, image))
            pixels = list(image.pixels[:])
            self.assertAlmostEqual(pixels[0], 34 / 255, places=5)
        finally:
            text_name = mesh.get("3mf_paint_source_text")
            bpy.data.meshes.remove(mesh)
            bpy.data.materials.remove(material)
            bpy.data.images.remove(image)
            if text_name and text_name in bpy.data.texts:
                bpy.data.texts.remove(bpy.data.texts[text_name])


if __name__ == "__main__":
    unittest.main()
