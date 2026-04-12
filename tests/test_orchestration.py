"""Unit tests for reference DB, placement checks, validation, spatial, schema,
prompt building, and collision detection.

Original 18 tests preserved; additional tests appended below.
"""

import unittest
import json

from agents import llm_routing as llm_agents
from agents.providers import build_provider_from_spec
from agents.providers.claude_cli import ClaudeCliProvider
from agents.providers.openai_compatible import OpenAICompatibleProvider

from tests.conftest import SYSTEM_CONFIGURATION
from orchestration import reference_db
from orchestration.placement import (
    check_functional_placement,
    log_functional_placement_warnings,
    COMMERCIAL_TYPES,
    WATER_ADJACENT_TYPES,
    CEREMONIAL_APPROACH_TYPES,
)
from core.errors import UrbanistaValidationError
from orchestration.validation import (
    validate_master_plan,
    validate_urbanista_arch_result,
    validate_urbanista_tiles,
    sanitize_urbanista_output,
    check_component_collisions,
    _aabb_overlap_volume,
)
from orchestration.schema import (
    RENDERER_COMPONENT_TYPES,
    PROCEDURAL_SHAPES,
    STACK_ROLES,
    PARAMETRIC_TEMPLATE_IDS,
    MAX_PROCEDURAL_PARTS,
    _NAMED_COLOR_MAP,
    _HEX_COLOR,
)
from orchestration.spatial import enforce_spacing, occupancy_summary_for_survey
from orchestration.world_commit import (
    apply_tile_placements,
    normalize_tile_dict_for_world,
)
from world.state import WorldState


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

    def test_open_terrain_tiles_no_components_ok(self):
        validate_urbanista_arch_result({
            "tiles": [
                {
                    "x": 1,
                    "y": 2,
                    "terrain": "road",
                    "spec": {"color": "#707070", "scenery": {"pavement_detail": 0.55}},
                },
                {
                    "x": 3,
                    "y": 2,
                    "terrain": "garden",
                    "spec": {"scenery": {"vegetation_density": 0.4}},
                },
            ],
        })

    def test_surface_detail_fields_ok(self):
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 0,
                "y": 0,
                "terrain": "building",
                "spec": {
                    "components": [{
                        "type": "podium",
                        "steps": 1,
                        "height": 0.1,
                        "color": "#F5E6C8",
                        "surface_detail": 0.5,
                        "detail_repeat": 10,
                    }],
                },
            }],
        })

    def test_invalid_surface_detail_rejected(self):
        with self.assertRaises(UrbanistaValidationError):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0,
                    "y": 0,
                    "terrain": "building",
                    "spec": {
                        "components": [{
                            "type": "podium",
                            "steps": 1,
                            "height": 0.1,
                            "color": "#F5E6C8",
                            "surface_detail": 1.5,
                        }],
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
        out = validate_master_plan(mp)
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
        out = validate_urbanista_tiles(tiles)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["x"], 1)
        self.assertEqual(out[0]["y"], 2)


class LlmAgentsConfigTests(unittest.TestCase):
    def test_each_engine_agent_key_has_spec(self):
        for key in (
            llm_agents.KEY_CARTOGRAPHUS_SKELETON,
            llm_agents.KEY_CARTOGRAPHUS_REFINE,
            llm_agents.KEY_CARTOGRAPHUS_SURVEY,
            llm_agents.KEY_URBANISTA,
        ):
            spec = llm_agents.get_agent_llm_spec(key)
            self.assertIn("provider", spec)
            self.assertIn("model", spec)

    def test_default_specs_match_llm_defaults_file(self):
        base = SYSTEM_CONFIGURATION.load_llm_defaults()["agents"][llm_agents.KEY_URBANISTA]
        spec = llm_agents.get_agent_llm_spec(llm_agents.KEY_URBANISTA)
        self.assertEqual(spec.get("provider"), base["provider"])
        self.assertEqual(spec.get("model"), base["model"])
        p = build_provider_from_spec(spec, SYSTEM_CONFIGURATION)
        if base["provider"] in ("xai", "grok"):
            self.assertIsInstance(p, OpenAICompatibleProvider)
        else:
            self.assertIsInstance(p, ClaudeCliProvider)


# ═══════════════════════════════════════════════════════════════════════════
# Additional tests (spatial, schema, validation edge cases, collisions, etc.)
# ═══════════════════════════════════════════════════════════════════════════

import pytest


# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------


class TestSchemaConstants:
    def test_component_types_non_empty(self):
        assert len(RENDERER_COMPONENT_TYPES) > 0

    def test_procedural_in_component_types(self):
        assert "procedural" in RENDERER_COMPONENT_TYPES

    def test_procedural_shapes_non_empty(self):
        assert len(PROCEDURAL_SHAPES) > 0
        assert "box" in PROCEDURAL_SHAPES
        assert "cylinder" in PROCEDURAL_SHAPES

    def test_stack_roles_non_empty(self):
        assert len(STACK_ROLES) > 0
        assert "foundation" in STACK_ROLES
        assert "roof" in STACK_ROLES

    def test_parametric_template_ids(self):
        assert "open" in PARAMETRIC_TEMPLATE_IDS
        assert "temple" in PARAMETRIC_TEMPLATE_IDS

    def test_hex_color_regex(self):
        assert _HEX_COLOR.match("#AABBCC")
        assert _HEX_COLOR.match("#000000")
        assert not _HEX_COLOR.match("#GGG000")
        assert not _HEX_COLOR.match("AABBCC")
        assert not _HEX_COLOR.match("#AAA")

    def test_named_color_map(self):
        assert "white" in _NAMED_COLOR_MAP
        assert "marble" in _NAMED_COLOR_MAP
        assert _NAMED_COLOR_MAP["white"].startswith("#")


# ---------------------------------------------------------------------------
# Spatial — enforce_spacing
# ---------------------------------------------------------------------------


class TestEnforceSpacing:
    def test_empty_plan(self):
        assert enforce_spacing([], system_configuration=SYSTEM_CONFIGURATION) == []

    def test_single_building_unchanged(self):
        mp = [{"name": "A", "tiles": [{"x": 5, "y": 5}]}]
        result = enforce_spacing(mp, system_configuration=SYSTEM_CONFIGURATION)
        assert result[0]["tiles"][0]["x"] == 5

    def test_non_overlapping_buildings_unchanged(self):
        mp = [
            {"name": "A", "tiles": [{"x": 0, "y": 0}]},
            {"name": "B", "tiles": [{"x": 10, "y": 10}]},
        ]
        result = enforce_spacing(mp, system_configuration=SYSTEM_CONFIGURATION)
        assert result[1]["tiles"][0]["x"] == 10

    def test_overlapping_buildings_shifted(self):
        mp = [
            {"name": "A", "tiles": [{"x": 5, "y": 5}]},
            {"name": "B", "tiles": [{"x": 5, "y": 5}]},  # exact overlap
        ]
        result = enforce_spacing(mp, system_configuration=SYSTEM_CONFIGURATION)
        # B should have been shifted somewhere
        bx = result[1]["tiles"][0]["x"]
        by = result[1]["tiles"][0]["y"]
        assert (bx, by) != (5, 5)

    def test_adjacent_buildings_shifted(self):
        """With min_gap=1, adjacent buildings should be shifted apart."""
        mp = [
            {"name": "A", "tiles": [{"x": 5, "y": 5}]},
            {"name": "B", "tiles": [{"x": 6, "y": 5}]},  # within gap
        ]
        result = enforce_spacing(mp, min_gap=1, system_configuration=SYSTEM_CONFIGURATION)
        bx = result[1]["tiles"][0]["x"]
        by = result[1]["tiles"][0]["y"]
        # B should have been moved
        assert abs(bx - 5) > 1 or abs(by - 5) > 1

    def test_invalid_tile_coords_skipped(self):
        mp = [
            {"name": "A", "tiles": [{"x": 5, "y": 5}]},
            {"name": "B", "tiles": [{"bad": "data"}]},
        ]
        result = enforce_spacing(mp, system_configuration=SYSTEM_CONFIGURATION)
        assert len(result) == 2


class TestOccupancySummary:
    def test_empty_plan(self):
        result = occupancy_summary_for_survey([])
        assert "none" in result.lower()

    def test_single_structure(self):
        mp = [{"name": "Temple", "tiles": [{"x": 0, "y": 0}, {"x": 1, "y": 0}]}]
        result = occupancy_summary_for_survey(mp)
        assert "2 tiles" in result
        assert "Temple" in result

    def test_many_structures_truncated(self):
        mp = [{"name": f"B{i}", "tiles": [{"x": i, "y": 0}]} for i in range(15)]
        result = occupancy_summary_for_survey(mp)
        assert "+5 more" in result


# ---------------------------------------------------------------------------
# Reference DB — additional
# ---------------------------------------------------------------------------


class TestReferenceDbAdditional:
    def setup_method(self):
        reference_db._CACHE = None
        reference_db._LOOKUP_CACHE.clear()

    def test_lookup_caches_result(self):
        r1 = reference_db.lookup_architectural_reference("temple", "Rome", -44)
        r2 = reference_db.lookup_architectural_reference("temple", "Rome", -44)
        assert r1 is r2  # exact same object from cache

    def test_lookup_none_for_unknown_type(self):
        r = reference_db.lookup_architectural_reference("spaceship", "Mars", 3000)
        # May or may not return None depending on generic entries
        # Just verify no exception

    def test_format_reference_none_returns_empty(self):
        assert reference_db.format_reference_for_prompt(None) == ""

    def test_format_reference_empty_dict(self):
        assert reference_db.format_reference_for_prompt({}) == ""

    def test_load_entries_idempotent(self):
        entries1 = reference_db.load_architectural_entries()
        entries2 = reference_db.load_architectural_entries()
        assert entries1 is entries2  # cached


# ---------------------------------------------------------------------------
# Placement — additional
# ---------------------------------------------------------------------------


class TestPlacementAdditional:
    def test_empty_plan(self):
        assert check_functional_placement([]) == []

    def test_non_list_returns_empty(self):
        assert check_functional_placement(None) == []
        assert check_functional_placement("bad") == []

    def test_market_needs_road(self):
        mp = [
            {"name": "M1", "building_type": "market", "tiles": [{"x": 0, "y": 0}]},
            {"name": "R1", "building_type": "road", "tiles": [{"x": 10, "y": 10}]},
        ]
        warnings = check_functional_placement(mp)
        assert any("market" in w.lower() for w in warnings)

    def test_warehouse_needs_road(self):
        mp = [
            {"name": "W1", "building_type": "warehouse", "tiles": [{"x": 0, "y": 0}]},
            {"name": "R1", "building_type": "road", "tiles": [{"x": 10, "y": 10}]},
        ]
        warnings = check_functional_placement(mp)
        assert any("warehouse" in w.lower() for w in warnings)

    def test_commercial_no_roads_at_all(self):
        mp = [
            {"name": "T1", "building_type": "taberna", "tiles": [{"x": 0, "y": 0}]},
        ]
        warnings = check_functional_placement(mp)
        assert any("no road" in w.lower() for w in warnings)

    def test_basilica_near_road_ok(self):
        mp = [
            {"name": "Bas", "building_type": "basilica", "tiles": [{"x": 5, "y": 5}]},
            {"name": "R1", "building_type": "road", "tiles": [{"x": 5, "y": 6}]},
        ]
        warnings = check_functional_placement(mp)
        assert warnings == []

    def test_monument_near_garden_ok(self):
        mp = [
            {"name": "Mon", "building_type": "monument", "tiles": [{"x": 5, "y": 5}]},
            {"name": "G", "building_type": "garden", "tiles": [{"x": 5, "y": 6}]},
            {"name": "R1", "building_type": "road", "tiles": [{"x": 20, "y": 20}]},
        ]
        warnings = check_functional_placement(mp)
        assert warnings == []

    def test_log_functional_placement_warnings_no_error(self):
        # Just ensure logging works without error
        mp = [
            {"name": "T1", "building_type": "taberna", "tiles": [{"x": 0, "y": 0}]},
        ]
        log_functional_placement_warnings(mp, "test context")


# ---------------------------------------------------------------------------
# Validation — additional edge cases
# ---------------------------------------------------------------------------


class TestValidationEdgeCases:
    def test_validate_master_plan_empty(self):
        assert validate_master_plan([]) == []
        assert validate_master_plan(None) == []

    def test_validate_master_plan_drops_non_dict(self):
        mp = [
            "not a dict",
            {"name": "A", "tiles": [{"x": 0, "y": 0}]},
        ]
        result = validate_master_plan(mp)
        assert len(result) == 1

    def test_validate_master_plan_drops_no_tiles(self):
        mp = [{"name": "A"}]
        result = validate_master_plan(mp)
        assert len(result) == 0

    def test_validate_master_plan_drops_empty_tiles(self):
        mp = [{"name": "A", "tiles": []}]
        result = validate_master_plan(mp)
        assert len(result) == 0

    def test_validate_master_plan_deduplicates(self):
        mp = [
            {"name": "A", "tiles": [{"x": 0, "y": 0}]},
            {"name": "B", "tiles": [{"x": 0, "y": 0}]},  # duplicate coords
        ]
        result = validate_master_plan(mp)
        # First claim wins; B should have its tile dropped
        all_coords = []
        for s in result:
            for t in s["tiles"]:
                all_coords.append((t["x"], t["y"]))
        assert len(all_coords) == len(set(all_coords))

    def test_validate_master_plan_none_xy_skipped(self):
        mp = [{"name": "A", "tiles": [{"x": None, "y": 0}, {"x": 0, "y": 0}]}]
        result = validate_master_plan(mp)
        assert len(result[0]["tiles"]) == 1

    def test_validate_urbanista_tiles_empty(self):
        assert validate_urbanista_tiles([]) == []
        assert validate_urbanista_tiles(None) == []

    def test_validate_urbanista_tiles_non_dict_skipped(self):
        result = validate_urbanista_tiles(["bad", 42, None])
        assert result == []

    def test_validate_urbanista_tiles_none_coords_skipped(self):
        result = validate_urbanista_tiles([{"x": None, "y": 0}])
        assert result == []

    def test_validate_urbanista_result_non_dict_raises(self):
        with pytest.raises(UrbanistaValidationError):
            validate_urbanista_arch_result("not a dict")

    def test_validate_urbanista_result_no_tiles_ok(self):
        # Missing tiles key is ok
        result = validate_urbanista_arch_result({"commentary": "test"})
        assert result == {"commentary": "test"}

    def test_validate_urbanista_tiles_not_list_raises(self):
        with pytest.raises(UrbanistaValidationError):
            validate_urbanista_arch_result({"tiles": "not a list"})

    def test_building_anchor_no_spec_raises(self):
        with pytest.raises(UrbanistaValidationError, match="spec.template or"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {},
                }],
            })

    def test_secondary_tile_ok(self):
        """A secondary tile with spec.anchor pointing elsewhere is allowed."""
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 1, "y": 0, "terrain": "building",
                "spec": {"anchor": {"x": 0, "y": 0}},
            }],
        })

    def test_unknown_component_type_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="unknown component type"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{"type": "laser_cannon", "height": 1.0}],
                    },
                }],
            })

    def test_colonnade_missing_style_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="colonnade"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{"type": "colonnade", "columns": 6, "height": 0.7, "radius": 0.02}],
                    },
                }],
            })

    def test_colonnade_valid(self):
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{
                        "type": "colonnade",
                        "style": "doric",
                        "columns": 6,
                        "height": 0.7,
                        "radius": 0.02,
                    }],
                },
            }],
        })

    def test_procedural_missing_parts_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="non-empty parts"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{
                            "type": "procedural",
                            "stack_role": "structural",
                            "parts": [],
                        }],
                    },
                }],
            })

    def test_procedural_too_many_parts_rejected(self):
        parts = [{"shape": "box", "width": 0.1, "height": 0.1, "depth": 0.1, "color": "#AAA"}
                 for _ in range(MAX_PROCEDURAL_PARTS + 1)]
        with pytest.raises(UrbanistaValidationError, match="exceed max"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{
                            "type": "procedural",
                            "stack_role": "structural",
                            "parts": parts,
                        }],
                    },
                }],
            })

    def test_procedural_unknown_shape_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="shape must be one of"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{
                            "type": "procedural",
                            "stack_role": "structural",
                            "parts": [{"shape": "pentagon", "color": "#AAA"}],
                        }],
                    },
                }],
            })

    def test_procedural_box_missing_dimensions_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="box needs"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{
                            "type": "procedural",
                            "stack_role": "structural",
                            "parts": [{"shape": "box", "color": "#AAAAAA"}],
                        }],
                    },
                }],
            })

    def test_procedural_box_with_size_array(self):
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{
                        "type": "procedural",
                        "stack_role": "structural",
                        "parts": [{"shape": "box", "size": [0.1, 0.2, 0.3], "color": "#AAAAAA"}],
                    }],
                },
            }],
        })

    def test_valid_template_temple(self):
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "template": {"id": "temple"},
                },
            }],
        })

    def test_invalid_template_id_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="template.id must be one of"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "template": {"id": "skyscraper"},
                    },
                }],
            })

    def test_roughness_out_of_range_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="roughness"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{
                            "type": "podium",
                            "steps": 1,
                            "height": 0.1,
                            "color": "#AAA",
                            "roughness": 2.0,
                        }],
                    },
                }],
            })

    def test_detail_repeat_out_of_range_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="detail_repeat"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{
                            "type": "podium",
                            "steps": 1,
                            "height": 0.1,
                            "color": "#AAA",
                            "detail_repeat": 0.1,
                        }],
                    },
                }],
            })


# ---------------------------------------------------------------------------
# Sanitize urbanista output
# ---------------------------------------------------------------------------


class TestSanitizeUrbanistaOutput:
    def test_color_name_to_hex(self):
        arch = {
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{"type": "podium", "color": "marble", "height": 0.1}],
                },
            }],
        }
        result = sanitize_urbanista_output(arch)
        comp = result["tiles"][0]["spec"]["components"][0]
        assert comp["color"].startswith("#")

    def test_procedural_missing_parts_auto_fixed(self):
        arch = {
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{"type": "procedural", "color": "#AAA"}],
                },
            }],
        }
        result = sanitize_urbanista_output(arch)
        comp = result["tiles"][0]["spec"]["components"][0]
        assert "parts" in comp
        assert len(comp["parts"]) > 0
        assert comp.get("stack_role") == "structural"

    def test_non_dict_input_passthrough(self):
        assert sanitize_urbanista_output("not a dict") == "not a dict"

    def test_no_tiles_key_passthrough(self):
        result = sanitize_urbanista_output({"commentary": "ok"})
        assert result == {"commentary": "ok"}


# ---------------------------------------------------------------------------
# Geometry collision detection
# ---------------------------------------------------------------------------


class TestAabbOverlap:
    def test_no_overlap(self):
        a = {"min_x": 0, "max_x": 1, "min_y": 0, "max_y": 1, "min_z": 0, "max_z": 1}
        b = {"min_x": 5, "max_x": 6, "min_y": 5, "max_y": 6, "min_z": 5, "max_z": 6}
        assert _aabb_overlap_volume(a, b) == 0.0

    def test_full_overlap(self):
        a = {"min_x": 0, "max_x": 1, "min_y": 0, "max_y": 1, "min_z": 0, "max_z": 1}
        assert _aabb_overlap_volume(a, a) == pytest.approx(1.0)

    def test_partial_overlap(self):
        a = {"min_x": 0, "max_x": 2, "min_y": 0, "max_y": 2, "min_z": 0, "max_z": 2}
        b = {"min_x": 1, "max_x": 3, "min_y": 1, "max_y": 3, "min_z": 1, "max_z": 3}
        assert _aabb_overlap_volume(a, b) == pytest.approx(1.0)


class TestComponentCollisions:
    def test_empty_components(self):
        assert check_component_collisions({"components": []}, 2.0, 2.0) == []

    def test_single_component_no_collision(self):
        spec = {"components": [{"type": "podium", "height": 0.1, "steps": 3}]}
        assert check_component_collisions(spec, 2.0, 2.0) == []

    def test_stacked_no_collision(self):
        """Foundation + structural should stack properly without collision."""
        spec = {
            "components": [
                {"type": "podium", "height": 0.1, "steps": 3, "stack_role": "foundation"},
                {"type": "colonnade", "height": 0.7, "columns": 6, "radius": 0.02,
                 "style": "doric", "stack_role": "structural"},
            ]
        }
        collisions = check_component_collisions(spec, 2.0, 2.0)
        # These should stack, not collide
        assert len(collisions) == 0

    def test_height_violation_detected(self):
        """Extreme height relative to footprint should be flagged (needs >= 2 components)."""
        spec = {
            "components": [
                {"type": "podium", "height": 0.1, "steps": 3, "stack_role": "foundation"},
                {"type": "block", "stories": 20, "storyHeight": 1.0, "stack_role": "structural"},
            ]
        }
        collisions = check_component_collisions(spec, 1.0, 1.0)
        assert any("HEIGHT" in c for c in collisions)


# ---------------------------------------------------------------------------
# Phase4 validation
# ---------------------------------------------------------------------------


class TestPhase4Validation:
    def test_valid_phase4(self):
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{"type": "podium", "steps": 1, "height": 0.1, "color": "#AAA"}],
                    "phase4": {"disable_all": True},
                },
            }],
        })

    def test_phase4_hex_key(self):
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{"type": "podium", "steps": 1, "height": 0.1, "color": "#AAA"}],
                    "phase4": {"step_color": "#FF0000"},
                },
            }],
        })

    def test_phase4_invalid_bool_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="boolean"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{"type": "podium", "steps": 1, "height": 0.1, "color": "#AAA"}],
                        "phase4": {"disable_all": "yes"},
                    },
                }],
            })

    def test_phase4_unknown_key_rejected(self):
        with pytest.raises(UrbanistaValidationError, match="unknown key"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{"type": "podium", "steps": 1, "height": 0.1, "color": "#AAA"}],
                        "phase4": {"laser_mode": True},
                    },
                }],
            })


# ---------------------------------------------------------------------------
# Grammar tile handling
# ---------------------------------------------------------------------------


class TestGrammarTiles:
    def test_grammar_tile_validates_ok(self):
        """Grammar tiles with valid grammar id pass validation."""
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 10, "y": 5, "terrain": "building",
                "grammar": "roman_temple",
                "grammar_params": {"order": "corinthian", "cols": 8},
            }],
        })

    def test_grammar_tile_invalid_id_rejected(self):
        """Grammar tiles with unknown grammar id are rejected."""
        with pytest.raises(UrbanistaValidationError, match="grammar must be one of"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 10, "y": 5, "terrain": "building",
                    "grammar": "flying_saucer",
                }],
            })

    def test_grammar_tile_sanitize_sets_terrain(self):
        """Sanitizer sets terrain='building' on grammar tiles."""
        arch = {
            "tiles": [{
                "x": 10, "y": 5,
                "grammar": "roman_temple",
                "grammar_params": {"order": "ionic"},
            }],
        }
        result = sanitize_urbanista_output(arch)
        assert result["tiles"][0].get("terrain") == "building"

    def test_grammar_tile_skip_spec_validation(self):
        """Grammar tiles skip spec validation (no components/template required)."""
        # A grammar tile without spec should pass validation
        validate_urbanista_arch_result({
            "tiles": [{
                "x": 10, "y": 5, "terrain": "building",
                "grammar": "basilica",
            }],
        })

    def test_grammar_params_must_be_object_if_present(self):
        """grammar_params must be an object if present."""
        with pytest.raises(UrbanistaValidationError, match="grammar_params must be an object"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 10, "y": 5, "terrain": "building",
                    "grammar": "roman_temple",
                    "grammar_params": "not_an_object",
                }],
            })


# ---------------------------------------------------------------------------
# Rendering pipeline end-to-end tests
# ---------------------------------------------------------------------------


from orchestration.schema import (
    expand_dense_shape,
    expand_dense_tile,
    expand_dense_shapes_in_result,
    DENSE_SHAPE_CODES,
    resolve_color,
)


class TestRenderingPipelineEndToEnd:
    """Verify data flows correctly through the pipeline:
    LLM output → sanitize → validate → tile data that renderer expects.
    """

    def test_grammar_tile_through_sanitize(self):
        """Grammar tiles pass through sanitize with terrain set."""
        arch = {
            "tiles": [{
                "x": 10, "y": 5,
                "grammar": "roman_temple",
                "grammar_params": {"order": "corinthian", "cols": {"front": 8}},
            }],
        }
        result = sanitize_urbanista_output(arch)
        tile = result["tiles"][0]
        assert tile.get("terrain") == "building"
        assert tile.get("grammar") == "roman_temple"

    def test_dense_shapes_expansion_preserves_position(self):
        """Dense shapes expand with correct position and size fields."""
        dense = ["b", [0.1, 0.5, -0.2], [0.8, 1.0, 0.8], "travertine"]
        result = expand_dense_shape(dense)
        assert result["shape"] == "box"
        assert result["position"] == [0.1, 0.5, -0.2]
        assert result["width"] == 0.8
        assert result["height"] == 1.0
        assert result["depth"] == 0.8
        assert result["color"].startswith("#")

    def test_dense_cylinder_expansion(self):
        """Dense cylinder shapes expand correctly with radius and height."""
        dense = ["c", [0.3, 0.7, 0], [0.04, 0.6], "marble"]
        result = expand_dense_shape(dense)
        assert result["shape"] == "cylinder"
        assert result["position"] == [0.3, 0.7, 0]
        assert result["radius"] == 0.04
        assert result["height"] == 0.6

    def test_dense_shapes_in_procedural_parts_expanded(self):
        """Dense arrays inside procedural parts[] are expanded to dicts."""
        arch = {
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{
                        "type": "procedural",
                        "stack_role": "structural",
                        "parts": [
                            ["b", [0, 0.5, 0], [0.8, 1.0, 0.8], "travertine"],
                            ["c", [0.3, 0.7, 0], [0.04, 0.6], "marble"],
                        ],
                    }],
                },
            }],
        }
        result = sanitize_urbanista_output(arch)
        parts = result["tiles"][0]["spec"]["components"][0]["parts"]
        assert len(parts) == 2
        assert isinstance(parts[0], dict)
        assert parts[0]["shape"] == "box"
        assert parts[0]["position"] == [0, 0.5, 0]
        assert parts[0]["width"] == 0.8
        assert isinstance(parts[1], dict)
        assert parts[1]["shape"] == "cylinder"
        assert parts[1]["position"] == [0.3, 0.7, 0]
        assert parts[1]["radius"] == 0.04

    def test_spec_as_list_expanded_to_procedural(self):
        """A spec that is a raw list of dense shapes gets wrapped correctly."""
        arch = {
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "building_name": "Test",
                "spec": [
                    ["b", [0, 0.1, 0], [0.8, 0.2, 0.8], "travertine"],
                    ["b", [0, 0.5, 0], [0.6, 0.6, 0.6], "brick"],
                ],
            }],
        }
        result = expand_dense_shapes_in_result(arch)
        tile = result["tiles"][0]
        spec = tile["spec"]
        assert isinstance(spec, dict), "spec should be converted to a dict"
        assert "components" in spec
        comp = spec["components"][0]
        assert comp["type"] == "procedural"
        assert comp["stack_role"] == "structural"
        assert len(comp["parts"]) == 2
        assert comp["parts"][0]["shape"] == "box"
        assert comp["parts"][0]["position"] == [0, 0.1, 0]

    def test_dense_tile_keys_expansion(self):
        """Dense tile keys (n, bt, g) are expanded to verbose form."""
        tile = {"n": "Temple", "bt": "temple", "x": 5, "y": 10,
                "g": "roman_temple", "p": {"order": "ionic"}}
        result = expand_dense_tile(tile)
        assert result["building_name"] == "Temple"
        assert result["building_type"] == "temple"
        assert result["grammar"] == "roman_temple"
        assert result["grammar_params"] == {"order": "ionic"}
        assert result["terrain"] == "building"

    def test_mixed_spec_position_normalization(self):
        """When a procedural component is mixed with foundation, Y positions
        that include the foundation offset are normalized down."""
        arch = {
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [
                        {"type": "podium", "steps": 3, "height": 0.18, "color": "#F5E6C8"},
                        {"type": "procedural", "stack_role": "structural", "parts": [
                            # LLM used absolute Y including podium height
                            {"shape": "box", "position": [0, 0.18, 0],
                             "width": 0.6, "height": 0.5, "depth": 0.9, "color": "#F0F0F0"},
                            {"shape": "box", "position": [-0.2, 0.43, 0],
                             "width": 0.05, "height": 0.4, "depth": 0.05, "color": "#F0F0F0"},
                        ]},
                    ],
                },
            }],
        }
        result = sanitize_urbanista_output(arch)
        parts = result["tiles"][0]["spec"]["components"][1]["parts"]
        # Positions should be shifted down by ~0.18 (foundation height)
        # so the renderer's anchorY addition produces correct results
        assert parts[0]["position"][1] < 0.18, (
            f"Y position {parts[0]['position'][1]} should be shifted below foundation height 0.18"
        )
        # The Y should be approximately 0 (0.18 - 0.18 = 0.0)
        assert abs(parts[0]["position"][1]) < 0.05

    def test_missing_position_gets_default(self):
        """Parts without position get [0,0,0] explicitly during sanitize."""
        arch = {
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{
                        "type": "procedural",
                        "stack_role": "structural",
                        "parts": [
                            {"shape": "box", "width": 0.8, "height": 1.0,
                             "depth": 0.8, "color": "#F5E6C8"},
                        ],
                    }],
                },
            }],
        }
        result = sanitize_urbanista_output(arch)
        part = result["tiles"][0]["spec"]["components"][0]["parts"][0]
        assert part.get("position") == [0, 0, 0]

    def test_shapes_have_spatial_spread(self):
        """Validation warns when all parts share the same X,Z (vertical line)."""
        # This should not raise but should log a warning
        arch = {
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{
                        "type": "procedural",
                        "stack_role": "structural",
                        "parts": [
                            {"shape": "box", "position": [0, 0, 0],
                             "width": 0.8, "height": 0.1, "depth": 0.8, "color": "#AAAAAA"},
                            {"shape": "box", "position": [0, 0.3, 0],
                             "width": 0.6, "height": 0.5, "depth": 0.6, "color": "#AAAAAA"},
                            {"shape": "cone", "position": [0, 0.7, 0],
                             "radius": 0.3, "height": 0.15, "color": "#AAAAAA"},
                        ],
                    }],
                },
            }],
        }
        # Should pass validation (warning only, not error)
        validate_urbanista_arch_result(arch)

    def test_all_dense_shape_codes_have_renderer_shapes(self):
        """Every dense shape code maps to a shape in PROCEDURAL_SHAPES."""
        for code, shape in DENSE_SHAPE_CODES.items():
            assert shape in PROCEDURAL_SHAPES, (
                f"Dense code '{code}' maps to '{shape}' which is not in PROCEDURAL_SHAPES"
            )

    def test_grammar_tile_validates_then_moves_to_spec(self):
        """Grammar data flows correctly: tile-level → validated → moved to spec."""
        arch = {
            "tiles": [{
                "x": 10, "y": 5, "terrain": "building",
                "grammar": "roman_temple",
                "grammar_params": {"order": "corinthian"},
            }],
        }
        # Sanitize and validate
        result = sanitize_urbanista_output(arch)
        validate_urbanista_arch_result(result)

        # Simulate engine.py: move grammar into spec
        tile = result["tiles"][0]
        g = tile.pop("grammar", None)
        gp = tile.pop("grammar_params", None)
        assert g == "roman_temple"
        if not tile.get("spec"):
            tile["spec"] = {}
        tile["spec"]["grammar"] = g
        if gp:
            tile["spec"]["params"] = gp

        # Verify final format matches what renderer expects
        assert tile["spec"]["grammar"] == "roman_temple"
        assert tile["spec"]["params"]["order"] == "corinthian"
        assert "grammar" not in tile  # moved to spec

    def test_material_names_resolved_to_hex(self):
        """Material names in parts are resolved to hex colors."""
        arch = {
            "tiles": [{
                "x": 0, "y": 0, "terrain": "building",
                "spec": {
                    "components": [{
                        "type": "procedural",
                        "stack_role": "structural",
                        "parts": [
                            {"shape": "box", "position": [0, 0, 0],
                             "width": 0.8, "height": 0.5, "depth": 0.8,
                             "color": "travertine"},
                        ],
                    }],
                },
            }],
        }
        result = sanitize_urbanista_output(arch)
        part = result["tiles"][0]["spec"]["components"][0]["parts"][0]
        assert part["color"].startswith("#"), f"Color should be hex, got {part['color']}"

    def test_position_validation_rejects_invalid(self):
        """Position with wrong format is rejected."""
        with pytest.raises(UrbanistaValidationError, match="position must be"):
            validate_urbanista_arch_result({
                "tiles": [{
                    "x": 0, "y": 0, "terrain": "building",
                    "spec": {
                        "components": [{
                            "type": "procedural",
                            "stack_role": "structural",
                            "parts": [{
                                "shape": "box",
                                "position": [0, 0],  # Only 2 elements
                                "width": 0.5, "height": 0.5, "depth": 0.5,
                                "color": "#AAAAAA",
                            }],
                        }],
                    },
                }],
            })


# ---------------------------------------------------------------------------
# Urban planning intelligence — road connectivity, spacing, prompt hints
# ---------------------------------------------------------------------------

from types import SimpleNamespace

from orchestration.spatial import get_district_spacing
from orchestration.prompt_builder import _detect_road_facing, _height_gradient_hint
from orchestration.generators import Generators

def _generators_for_road_connectivity_tests() -> Generators:
    return Generators(SimpleNamespace(system_configuration=SYSTEM_CONFIGURATION))


class TestDistrictSpacing:
    def test_monumental_gets_wide_spacing(self):
        assert get_district_spacing("monumental", system_configuration=SYSTEM_CONFIGURATION) == 2

    def test_commercial_gets_zero_spacing(self):
        assert get_district_spacing("commercial", system_configuration=SYSTEM_CONFIGURATION) == 0

    def test_residential_gets_default(self):
        assert get_district_spacing("residential", system_configuration=SYSTEM_CONFIGURATION) == 1

    def test_garden_gets_wide_spacing(self):
        assert get_district_spacing("garden", system_configuration=SYSTEM_CONFIGURATION) == 2

    def test_none_returns_default(self):
        assert get_district_spacing(None, system_configuration=SYSTEM_CONFIGURATION) == 1

    def test_unknown_style_returns_default(self):
        assert get_district_spacing("alien", system_configuration=SYSTEM_CONFIGURATION) == 1

    def test_case_insensitive(self):
        assert get_district_spacing("COMMERCIAL", system_configuration=SYSTEM_CONFIGURATION) == 0
        assert get_district_spacing("Monumental", system_configuration=SYSTEM_CONFIGURATION) == 2


class TestRoadConnectivity:
    def test_no_roads_returns_unchanged(self):
        mp = [{"name": "A", "building_type": "temple", "tiles": [{"x": 5, "y": 5}]}]
        region = {"x1": 0, "y1": 0, "x2": 10, "y2": 10}
        gen = _generators_for_road_connectivity_tests()
        result = gen._ensure_road_connectivity(mp, region)
        assert len(result) == 1  # unchanged

    def test_single_road_tile_returns_unchanged(self):
        mp = [{"name": "R1", "building_type": "road", "tiles": [{"x": 5, "y": 5}]}]
        region = {"x1": 0, "y1": 0, "x2": 10, "y2": 10}
        gen = _generators_for_road_connectivity_tests()
        result = gen._ensure_road_connectivity(mp, region)
        # May add boundary road but original structure unchanged
        assert result[0]["name"] == "R1"

    def test_connected_roads_not_modified(self):
        mp = [
            {"name": "R1", "building_type": "road",
             "tiles": [{"x": 5, "y": 5}, {"x": 6, "y": 5}, {"x": 7, "y": 5}]},
        ]
        region = {"x1": 5, "y1": 5, "x2": 7, "y2": 5}
        gen = _generators_for_road_connectivity_tests()
        result = gen._ensure_road_connectivity(mp, region)
        # Roads are already connected and on boundary — no changes
        assert result[0]["name"] == "R1"

    def test_isolated_segments_get_connected(self):
        # Two road segments with a gap between them
        mp = [
            {"name": "R1", "building_type": "road",
             "tiles": [{"x": 0, "y": 5}]},
            {"name": "R2", "building_type": "road",
             "tiles": [{"x": 5, "y": 5}]},
        ]
        region = {"x1": 0, "y1": 0, "x2": 10, "y2": 10}
        gen = _generators_for_road_connectivity_tests()
        result = gen._ensure_road_connectivity(mp, region)
        # Should have added a connecting road structure
        road_names = [s["name"] for s in result if s["building_type"] == "road"]
        assert len(road_names) >= 3  # R1, R2, Connecting road (and maybe boundary)

    def test_bridge_tiles_avoid_buildings(self):
        # Road segments with a building in between
        mp = [
            {"name": "R1", "building_type": "road",
             "tiles": [{"x": 0, "y": 5}]},
            {"name": "Temple", "building_type": "temple",
             "tiles": [{"x": 2, "y": 5}]},
            {"name": "R2", "building_type": "road",
             "tiles": [{"x": 5, "y": 5}]},
        ]
        region = {"x1": 0, "y1": 0, "x2": 10, "y2": 10}
        gen = _generators_for_road_connectivity_tests()
        result = gen._ensure_road_connectivity(mp, region)
        # Bridge tiles should NOT include (2, 5) where temple is
        for s in result:
            if s["name"] == "Connecting road":
                bridge_coords = {(t["x"], t["y"]) for t in s["tiles"]}
                assert (2, 5) not in bridge_coords

    def test_boundary_road_added_when_roads_internal(self):
        # Roads exist but none touch the boundary
        mp = [
            {"name": "R1", "building_type": "road",
             "tiles": [{"x": 5, "y": 5}, {"x": 6, "y": 5}]},
        ]
        region = {"x1": 0, "y1": 0, "x2": 10, "y2": 10}
        gen = _generators_for_road_connectivity_tests()
        result = gen._ensure_road_connectivity(mp, region)
        # Should have added a "District edge road" structure
        names = [s["name"] for s in result]
        assert "District edge road" in names


class TestHeightGradientHint:
    def test_near_center_suggests_tall(self):
        hint = _height_gradient_hint(10, 10, 10.0, 10.0, 50.0)
        assert "tall" in hint.lower()
        assert "0m" in hint

    def test_mid_distance_suggests_moderate(self):
        # 25 tiles from center, radius 50
        hint = _height_gradient_hint(35, 10, 10.0, 10.0, 50.0)
        assert "moderate" in hint.lower()

    def test_far_from_center_suggests_low(self):
        # 40 tiles from center, radius 50
        hint = _height_gradient_hint(50, 10, 10.0, 10.0, 50.0)
        assert "low" in hint.lower()

    def test_zero_radius_returns_empty(self):
        hint = _height_gradient_hint(10, 10, 10.0, 10.0, 0.0)
        assert hint == ""

    def test_token_budget(self):
        hint = _height_gradient_hint(20, 20, 10.0, 10.0, 50.0)
        # Should be under 30 tokens (rough estimate: words + numbers)
        assert len(hint.split()) <= 15


class TestRoadFacing:
    def test_no_world_returns_empty(self):
        tiles = [{"x": 5, "y": 5}]
        assert _detect_road_facing(tiles, None) == ""

    def test_empty_tiles_returns_empty(self):
        assert _detect_road_facing([], None) == ""

    def test_road_detected_north(self):
        """Mock world where tile (5,4) is a road."""
        class MockTile:
            def __init__(self, terrain):
                self.terrain = terrain
        class MockWorld:
            def get_tile(self, x, y):
                if (x, y) == (5, 4):
                    return MockTile("road")
                return None
        tiles = [{"x": 5, "y": 5}]
        result = _detect_road_facing(tiles, MockWorld())
        assert "FACING" in result
        assert "N" in result

    def test_forum_detected_as_road(self):
        class MockTile:
            def __init__(self, terrain):
                self.terrain = terrain
        class MockWorld:
            def get_tile(self, x, y):
                if (x, y) == (6, 5):
                    return MockTile("forum")
                return None
        tiles = [{"x": 5, "y": 5}]
        result = _detect_road_facing(tiles, MockWorld())
        assert "FACING" in result
        assert "E" in result

    def test_no_roads_returns_empty(self):
        class MockTile:
            def __init__(self, terrain):
                self.terrain = terrain
        class MockWorld:
            def get_tile(self, x, y):
                return MockTile("building")
        tiles = [{"x": 5, "y": 5}]
        result = _detect_road_facing(tiles, MockWorld())
        assert result == ""


def test_normalize_tile_dict_clamps_elevation():
    raw = {"terrain": "road", "elevation": 1.0e6}
    out = normalize_tile_dict_for_world(raw, system_configuration=SYSTEM_CONFIGURATION)
    assert out["elevation"] == SYSTEM_CONFIGURATION.grid.maximum_elevation_value
    low = normalize_tile_dict_for_world(
        {"terrain": "water", "elevation": -1.0e6},
        system_configuration=SYSTEM_CONFIGURATION,
    )
    assert low["elevation"] == SYSTEM_CONFIGURATION.world_place_tile_min_elevation


def test_apply_tile_placements_places_and_skips_bad_coords():
    world = WorldState(
        chunk_size_tiles=SYSTEM_CONFIGURATION.grid.chunk_size_tiles,
        system_configuration=SYSTEM_CONFIGURATION,
    )
    batch = apply_tile_placements(
        world,
        [
            (1, 2, {"terrain": "road", "elevation": 5.0}),
            (None, 2, {"terrain": "road"}),
            ("nope", 0, {"terrain": "road"}),
        ],
        system_configuration=SYSTEM_CONFIGURATION,
    )
    assert batch.attempted_coordinate_pairs == 3
    assert batch.skipped_invalid_coordinates == 2
    assert len(batch.placed_tile_dicts) == 1
    assert (1, 2) in world.tiles


if __name__ == "__main__":
    unittest.main()
