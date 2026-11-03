"""Tests for OrcaSlicer measured filament blend colors."""

import json
import unittest
from pathlib import Path

from io_mesh_3mf.common.color_recipes import (
    PhysicalFilament,
    RecipeMode,
    lookup_measured_blend_color,
    lookup_standard_recipe,
    recommend_from_physical_filaments,
    recompute_mixed_slot_colors,
    rgb_to_lab,
    slot_display_color,
    delta_e76,
    _ratio_grid,
)
from io_mesh_3mf.common.filament_mixer import blend_color_multi, filament_mixer_lerp
from io_mesh_3mf.common.mixed_slots import MixedSlot


_RECIPE_PATH = (
    Path(__file__).resolve().parents[2]
    / "io_mesh_3mf"
    / "common"
    / "standard_color_recipes.json"
)


class TestMeasuredRecipes(unittest.TestCase):
    def test_all_orca_eligible_recipes_match_exactly(self):
        data = json.loads(_RECIPE_PATH.read_text(encoding="utf-8"))
        eligible = [
            entry for entry in data["entries"]
            if entry.get("source") in {"measured", "interpolated"}
        ]
        self.assertTrue(eligible)

        expected_by_recipe = {}
        for entry in eligible:
            recipe_key = tuple(sorted(
                (component["rgb"].upper(), ratio)
                for component, ratio in zip(entry["components"], entry["ratios"])
            ))
            expected_by_recipe.setdefault(recipe_key, entry["measured_rgb"])

        for recipe_key, expected in expected_by_recipe.items():
            with self.subTest(
                recipe=recipe_key,
            ):
                color = lookup_measured_blend_color(
                    [component for component, _ in recipe_key],
                    [ratio for _, ratio in recipe_key],
                )
                self.assertEqual(color, expected)

    def test_two_component_lab_interpolation(self):
        self.assertEqual(
            lookup_measured_blend_color(
                ["#0086D6", "#EC008C"],
                [27, 73],
            ),
            "#92609F",
        )

    def test_three_component_idw_interpolation(self):
        self.assertEqual(
            lookup_measured_blend_color(
                ["#0086D6", "#EC008C", "#F4EE2A"],
                [22, 24, 54],
            ),
            "#8D8857",
        )

    def test_gradient_display_color_blends_first_and_last_components(self):
        slot = MixedSlot(
            slot=2,
            components=[1, 2, 3],
            ratios=[0.2, 0.3, 0.5],
            gradient=True,
        )
        palette = ["#0086D6", "#F4EE2A", "#EC008C", "#000000"]

        self.assertEqual(
            slot_display_color(slot, palette),
            blend_color_multi(["#0086D6", "#EC008C"], [5000, 5000]),
        )
        self.assertEqual(
            recompute_mixed_slot_colors(palette, [slot])[2],
            slot_display_color(slot, palette),
        )


class TestFilamentMixerCompatibility(unittest.TestCase):
    def test_float32_blend_factor_matches_orca(self):
        self.assertEqual(
            filament_mixer_lerp(10, 20, 30, 200, 180, 90, 0.123456789),
            (17, 41, 47),
        )

    def test_invalid_hex_uses_orca_gray_fallback(self):
        self.assertEqual(blend_color_multi(["not-a-color"], [1]), "#808080")

    def test_rgba_hex_ignores_alpha(self):
        # Orca writes physical filament colors as #RRGGBBAA; the FullSpectrum
        # reference file stores #7C356C for the 50/50 blend of these two.
        self.assertEqual(
            blend_color_multi(["#DE1619FF", "#0F80FF"], [5000, 5000]),
            "#7C356C",
        )
        self.assertEqual(
            blend_color_multi(["#DE1619FF", "#0F80FF"], [5000, 5000]),
            blend_color_multi(["#DE1619", "#0F80FF"], [5000, 5000]),
        )


class TestRecipeSolvers(unittest.TestCase):
    def setUp(self):
        self.cyan = PhysicalFilament("#0086D6", 1, "PLA Basic")
        self.magenta = PhysicalFilament("#EC008C", 2, "PLA")
        self.yellow = PhysicalFilament("#F4EE2A", 3, "PETG")

    def test_exact_physical_color_returns_single_component(self):
        result = recommend_from_physical_filaments(
            (0, 134, 214),
            [self.cyan, PhysicalFilament("#0086D6", 4, "PLA", is_mixed=True)],
            "PLA",
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.mode, RecipeMode.MATERIAL_LIST)
        self.assertEqual(result.matched_color_hex, "#0086D6")
        self.assertEqual(len(result.components), 1)
        self.assertEqual(result.components[0].ratio, 100)
        self.assertEqual(result.components[0].filament_index, 1)

    def test_physical_solver_selects_exact_measured_grid_recipe(self):
        result = recommend_from_physical_filaments(
            (150, 94, 158),
            [self.cyan, self.magenta],
            "PLA Basic",
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.mode, RecipeMode.MATERIAL_LIST)
        self.assertEqual(result.matched_color_hex, "#965E9E")
        self.assertEqual(
            [(component.filament_index, component.ratio) for component in result.components],
            [(1, 20), (2, 80)],
        )

    def test_material_filter_and_fallback_when_too_few_matches(self):
        filtered = recommend_from_physical_filaments(
            (150, 94, 158),
            [self.cyan, self.magenta, self.yellow],
            "PLA",
        )
        fallback = recommend_from_physical_filaments(
            (150, 94, 158),
            [self.cyan, self.yellow],
            "PLA",
        )

        self.assertEqual(
            {component.filament_index for component in filtered.components},
            {1, 2},
        )
        self.assertEqual(
            {component.filament_index for component in fallback.components},
            {1, 3},
        )

    def test_physical_solver_excludes_mixed_and_invalid_filaments(self):
        result = recommend_from_physical_filaments(
            (150, 94, 158),
            [
                self.cyan,
                self.magenta,
                PhysicalFilament("#965E9E", 3, "PLA", is_mixed=True),
                PhysicalFilament("invalid", 4, "PLA"),
            ],
        )

        self.assertTrue(result.valid)
        self.assertEqual(
            {component.filament_index for component in result.components},
            {1, 2},
        )

    def test_eight_candidate_cap_discards_a_far_ninth_color(self):
        target_hex = blend_color_multi(
            [self.cyan.color_hex, self.magenta.color_hex],
            [8000, 2000],
        )
        target_rgb = tuple(
            int(target_hex[index:index + 2], 16)
            for index in (1, 3, 5)
        )
        target_lab = rgb_to_lab(target_rgb)
        self.assertLess(
            delta_e76(target_lab, rgb_to_lab((0, 134, 214))),
            delta_e76(target_lab, rgb_to_lab((236, 0, 140))),
        )

        with_all_candidates = recommend_from_physical_filaments(
            target_rgb,
            [self.cyan, self.magenta],
        )
        capped_candidates = [
            PhysicalFilament("#0086D6", index, "PLA")
            for index in range(1, 9)
        ] + [PhysicalFilament(self.magenta.color_hex, 9, self.magenta.filament_type)]
        capped = recommend_from_physical_filaments(target_rgb, capped_candidates)

        self.assertEqual(with_all_candidates.matched_color_hex, target_hex)
        self.assertIn(2, {component.filament_index for component in with_all_candidates.components})
        self.assertNotIn(9, {component.filament_index for component in capped.components})

    def test_ratio_grids_match_orca_boundaries(self):
        pairs = _ratio_grid(2)
        triplets = _ratio_grid(3)

        self.assertEqual(len(pairs), 13)
        self.assertEqual((pairs[0], pairs[-1]), ((20, 80), (80, 20)))
        self.assertEqual(len(triplets), 45)
        self.assertTrue(all(sum(ratios) == 100 for ratios in triplets))
        self.assertTrue(all(min(ratios) >= 20 for ratios in triplets))
        self.assertEqual(_ratio_grid(4), [])

    def test_standard_recipe_lookup_uses_mode_and_material_fallback(self):
        data = json.loads(_RECIPE_PATH.read_text(encoding="utf-8"))
        for mode in ("CMYW", "RYBW"):
            entries = [
                entry for entry in data["entries"]
                if entry["mode"] == mode and entry["material"] == "PLA Basic"
            ]
            target_hex = entries[0]["measured_rgb"]
            target_rgb = tuple(
                int(target_hex[index:index + 2], 16)
                for index in (1, 3, 5)
            )
            target_lab = rgb_to_lab(target_rgb)
            expected = min(
                entries,
                key=lambda entry: delta_e76(target_lab, tuple(entry["measured_lab"])),
            )

            preferred = lookup_standard_recipe(target_rgb, mode, "PLA")
            fallback = lookup_standard_recipe(target_rgb, mode, "PETG")

            with self.subTest(mode=mode):
                self.assertTrue(preferred.valid)
                self.assertEqual(preferred.mode.value, mode)
                self.assertEqual(preferred.matched_color_hex, expected["measured_rgb"])
                self.assertEqual(
                    [(component.base_color, component.ratio) for component in preferred.components],
                    [
                        (component["key"], ratio)
                        for component, ratio in zip(expected["components"], expected["ratios"])
                    ],
                )
                self.assertEqual(fallback, preferred)
        self.assertEqual(
            lookup_standard_recipe((150, 94, 158), "RGBY").mode,
            RecipeMode.RYBW,
        )

    def test_standard_recipe_rejects_material_list_mode(self):
        with self.assertRaises(ValueError):
            lookup_standard_recipe((150, 94, 158), RecipeMode.MATERIAL_LIST)
