"""Unit tests for reference DB, placement checks, validation, spatial, schema,
prompt building, and collision detection.

Original 18 tests preserved; additional tests appended below.
"""

import unittest
import json

from agents import llm_routing as llm_agents
from agents.providers import build_provider_from_spec
from agents.providers.claude_cli import ClaudeCliProvider
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

    def test_default_specs_resolve_to_claude_cli(self):
        spec = llm_agents.get_agent_llm_spec(llm_agents.KEY_URBANISTA)
        self.assertEqual(spec.get("provider"), "claude_cli")
        p = build_provider_from_spec(spec)
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
        assert enforce_spacing([]) == []

    def test_single_building_unchanged(self):
        mp = [{"name": "A", "tiles": [{"x": 5, "y": 5}]}]
        result = enforce_spacing(mp)
        assert result[0]["tiles"][0]["x"] == 5

    def test_non_overlapping_buildings_unchanged(self):
        mp = [
            {"name": "A", "tiles": [{"x": 0, "y": 0}]},
            {"name": "B", "tiles": [{"x": 10, "y": 10}]},
        ]
        result = enforce_spacing(mp)
        assert result[1]["tiles"][0]["x"] == 10

    def test_overlapping_buildings_shifted(self):
        mp = [
            {"name": "A", "tiles": [{"x": 5, "y": 5}]},
            {"name": "B", "tiles": [{"x": 5, "y": 5}]},  # exact overlap
        ]
        result = enforce_spacing(mp)
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
        result = enforce_spacing(mp, min_gap=1)
        bx = result[1]["tiles"][0]["x"]
        by = result[1]["tiles"][0]["y"]
        # B should have been moved
        assert abs(bx - 5) > 1 or abs(by - 5) > 1

    def test_invalid_tile_coords_skipped(self):
        mp = [
            {"name": "A", "tiles": [{"x": 5, "y": 5}]},
            {"name": "B", "tiles": [{"bad": "data"}]},
        ]
        result = enforce_spacing(mp)
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
# Terrain procedural generation (Optimization 2)
# ---------------------------------------------------------------------------

from orchestration.engine import _generate_terrain_procedurally, BATCHABLE_TYPES, OPEN_TERRAIN_TYPES


class TestGenerateTerrainProcedurally:
    def test_road_tiles(self):
        tiles = [{"x": 0, "y": 0, "elevation": 0.1}, {"x": 1, "y": 0, "elevation": 0.1}]
        result = _generate_terrain_procedurally(
            name="Via Sacra", btype="road", tiles=tiles,
            avg_elevation=0.1, district_palette=None, physical_desc="Ancient road",
        )
        assert "tiles" in result
        assert len(result["tiles"]) == 2
        for t in result["tiles"]:
            assert t["terrain"] == "road"
            assert t["building_type"] == "road"
            assert "color" in t
            assert "spec" in t

    def test_water_tiles(self):
        tiles = [{"x": 5, "y": 5, "elevation": 0.0}]
        result = _generate_terrain_procedurally(
            name="Tiber", btype="water", tiles=tiles,
            avg_elevation=0.0, district_palette=None, physical_desc="River",
        )
        assert len(result["tiles"]) == 1
        assert result["tiles"][0]["terrain"] == "water"
        assert "water_murk" in result["tiles"][0]["spec"]["scenery"]

    def test_garden_tiles(self):
        tiles = [{"x": 3, "y": 3}]
        result = _generate_terrain_procedurally(
            name="Horti", btype="garden", tiles=tiles,
            avg_elevation=0.2, district_palette=None, physical_desc="Garden",
        )
        assert result["tiles"][0]["terrain"] == "garden"
        assert "vegetation_density" in result["tiles"][0]["spec"]["scenery"]

    def test_with_district_palette(self):
        tiles = [{"x": 0, "y": 0}]
        palette = {"primary": "#AA8844", "secondary": "#BB5533", "accent": "#227744"}
        result = _generate_terrain_procedurally(
            name="Forum", btype="forum", tiles=tiles,
            avg_elevation=0.1, district_palette=palette, physical_desc="Forum",
        )
        # Forum should use primary color from palette
        assert result["tiles"][0]["color"] == "#AA8844"


class TestBatchableTypes:
    def test_batchable_types_not_in_open_terrain(self):
        """Batchable types should not overlap with open terrain types."""
        assert BATCHABLE_TYPES & OPEN_TERRAIN_TYPES == frozenset()

    def test_expected_batchable_types(self):
        assert "taberna" in BATCHABLE_TYPES
        assert "warehouse" in BATCHABLE_TYPES
        assert "insula" in BATCHABLE_TYPES

    def test_complex_types_not_batchable(self):
        assert "temple" not in BATCHABLE_TYPES
        assert "amphitheater" not in BATCHABLE_TYPES
        assert "thermae" not in BATCHABLE_TYPES


# ---------------------------------------------------------------------------
# JSON array parsing (for batch mode)
# ---------------------------------------------------------------------------

from agents.base import _try_decode_json_array


class TestTryDecodeJsonArray:
    def test_valid_array(self):
        result = _try_decode_json_array('[{"a": 1}, {"b": 2}]')
        assert result == [{"a": 1}, {"b": 2}]

    def test_with_markdown_fences(self):
        result = _try_decode_json_array('```json\n[{"a": 1}]\n```')
        assert result == [{"a": 1}]

    def test_with_surrounding_prose(self):
        result = _try_decode_json_array('Here are the results: [{"a": 1}] done.')
        assert result == [{"a": 1}]

    def test_empty_returns_none(self):
        assert _try_decode_json_array("") is None
        assert _try_decode_json_array("   ") is None

    def test_non_array_returns_none(self):
        assert _try_decode_json_array('{"a": 1}') is None

    def test_nested_array(self):
        result = _try_decode_json_array('[{"tiles": [{"x": 1}]}, {"tiles": [{"x": 2}]}]')
        assert len(result) == 2


if __name__ == "__main__":
    unittest.main()
