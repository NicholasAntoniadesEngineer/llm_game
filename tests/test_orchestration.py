"""Unit tests for reference DB, placement checks, and validation (stdlib unittest)."""

import unittest

import llm_agents
from agents.provider import ClaudeCliProvider, build_provider_from_spec
from orchestration import reference_db
from orchestration.placement import check_functional_placement
from orchestration.validation import (
    UrbanistaValidationError,
    validate_master_plan,
    validate_urbanista_arch_result,
    validate_urbanista_tiles,
)


class ReferenceDbTests(unittest.TestCase):
    def setUp(self):
        reference_db._CACHE = None

    def test_temple_rome_republican(self):
        r = reference_db.lookup_architectural_reference("temple", "Rome", -44)
        self.assertIsNotNone(r)
        self.assertIn("roman_republic", r["id"])

    def test_temple_athens(self):
        r = reference_db.lookup_architectural_reference("temple", "Athens", -400)
        self.assertIsNotNone(r)
        self.assertIn("greek", r["id"])

    def test_thermae_rome(self):
        r = reference_db.lookup_architectural_reference("thermae", "Rome", 100)
        self.assertIsNotNone(r)
        self.assertIn("thermae", r["id"])

    def test_tenochtitlan_temple(self):
        r = reference_db.lookup_architectural_reference("temple", "Tenochtitlan", 1500)
        self.assertIsNotNone(r)
        self.assertIn("mesoamerican", r["id"])

    def test_format_non_empty(self):
        r = reference_db.lookup_architectural_reference("aqueduct", "Rome", -50)
        self.assertIsNotNone(r)
        t = reference_db.format_reference_for_prompt(r)
        self.assertIn("proportion", t.lower())


class PlacementTests(unittest.TestCase):
    def test_taberna_isolated_from_road(self):
        mp = [
            {"name": "T1", "building_type": "taberna", "tiles": [{"x": 0, "y": 0}]},
            {"name": "R1", "building_type": "road", "tiles": [{"x": 5, "y": 5}]},
        ]
        w = check_functional_placement(mp)
        self.assertTrue(any("taberna" in x and "road" in x for x in w))

    def test_taberna_next_to_road_ok(self):
        mp = [
            {"name": "T1", "building_type": "taberna", "tiles": [{"x": 1, "y": 1}]},
            {"name": "R1", "building_type": "road", "tiles": [{"x": 1, "y": 2}]},
        ]
        self.assertEqual(check_functional_placement(mp), [])

    def test_temple_far_from_everything_warns(self):
        mp = [
            {"name": "Temple", "building_type": "temple", "tiles": [{"x": 0, "y": 0}]},
            {"name": "R1", "building_type": "road", "tiles": [{"x": 10, "y": 10}]},
        ]
        w = check_functional_placement(mp)
        self.assertTrue(any("approach" in x.lower() or "plaza" in x.lower() for x in w))

    def test_temple_adjacent_forum_ok(self):
        mp = [
            {"name": "Temple", "building_type": "temple", "tiles": [{"x": 5, "y": 5}]},
            {"name": "F", "building_type": "forum", "tiles": [{"x": 5, "y": 6}]},
            {"name": "R1", "building_type": "road", "tiles": [{"x": 20, "y": 20}]},
        ]
        w = check_functional_placement(mp)
        self.assertEqual(w, [])


class ValidationTests(unittest.TestCase):
    def test_template_open_ok(self):
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 0,
                "y": 0,
                "terrain": "building",
                "spec": {
                    "template": {
                        "id": "open",
                        "params": {
                            "components": [
                                {"type": "podium", "steps": 1, "height": 0.1, "color": "#F5E6C8"},
                            ],
                        },
                    },
                },
            }],
        })

    def test_both_template_and_components_rejected(self):
        with self.assertRaises(UrbanistaValidationError):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0,
                    "y": 0,
                    "terrain": "building",
                    "spec": {
                        "template": {"id": "temple"},
                        "components": [{"type": "podium", "steps": 1, "height": 0.1, "color": "#F5E6C8"}],
                    },
                }],
            })

    def test_master_plan_normalizes_string_coordinates_to_int(self):
        mp = [
            {
                "name": "A",
                "building_type": "taberna",
                "tiles": [
                    {"x": "2", "y": "3"},
                    {"x": 2.0, "y": 4.0},
                ],
            },
        ]
        out = validate_master_plan(mp, 10, 10)
        self.assertEqual(len(out), 1)
        xs = [t["x"] for t in out[0]["tiles"]]
        ys = [t["y"] for t in out[0]["tiles"]]
        self.assertEqual(xs, [2, 2])
        self.assertEqual(ys, [3, 4])
        for t in out[0]["tiles"]:
            self.assertIsInstance(t["x"], int)
            self.assertIsInstance(t["y"], int)

    def test_validate_urbanista_tiles_normalizes_coordinates(self):
        tiles = [{"x": "1", "y": "2", "terrain": "building"}]
        out = validate_urbanista_tiles(tiles, 10, 10)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["x"], 1)
        self.assertEqual(out[0]["y"], 2)


class LlmAgentsConfigTests(unittest.TestCase):
    def test_each_engine_agent_key_has_spec(self):
        for key in (
            llm_agents.KEY_CARTOGRAPHUS_SKELETON,
            llm_agents.KEY_CARTOGRAPHUS_REFINE,
            llm_agents.KEY_CARTOGRAPHUS_SURVEY,
            llm_agents.KEY_HISTORICUS,
            llm_agents.KEY_URBANISTA,
        ):
            spec = llm_agents.get_agent_llm_spec(key)
            self.assertIn("provider", spec)
            self.assertIn("model", spec)

    def test_default_specs_resolve_to_claude_cli(self):
        spec = llm_agents.get_agent_llm_spec(llm_agents.KEY_URBANISTA)
        self.assertEqual(spec.get("provider"), "claude_cli")
        p = build_provider_from_spec(spec)
        self.assertIsInstance(p, ClaudeCliProvider)


if __name__ == "__main__":
    unittest.main()
