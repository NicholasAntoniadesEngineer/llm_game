"""Persistence resume helpers (cursor clamp, save version policy hook)."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.errors import SaveIndexError
from core.fingerprint import SAVE_FORMAT_VERSION
from core.persistence import clamp_district_resume_indices_for_loaded_save, load_state
from world.state import WorldState

from tests.conftest import SYSTEM_CONFIGURATION


def test_clamp_district_resume_indices_empty_districts():
    assert clamp_district_resume_indices_for_loaded_save(5, 9, 0) == (0, 0)


def test_clamp_district_resume_indices_in_range():
    assert clamp_district_resume_indices_for_loaded_save(1, 2, 3) == (1, 2)


def test_clamp_district_resume_indices_high_values():
    assert clamp_district_resume_indices_for_loaded_save(99, 100, 3) == (3, 3)


def test_clamp_district_resume_indices_negative():
    assert clamp_district_resume_indices_for_loaded_save(-1, -5, 4) == (0, 0)


def test_load_state_rejects_version_when_strict(tmp_path, monkeypatch):
    """When mismatch flag is 1, incompatible save_format_version must raise."""
    strict_cfg = replace(
        SYSTEM_CONFIGURATION,
        persistence_fail_on_save_format_version_mismatch_flag=1,
    )
    saves = tmp_path / "saves"
    chunks = saves / "chunks"
    chunks.mkdir(parents=True)
    index = {
        "save_format_version": int(SAVE_FORMAT_VERSION) + 999,
        "chunk_size": 64,
        "turn": 0,
        "current_period": "",
        "current_year": 0,
        "chunk_manifest": [],
        "district_index": 0,
        "district_build_cursor": 0,
        "build_wave_phase": "landmark",
        "districts": [],
        "generation": 0,
    }
    (saves / "index.json").write_text(json.dumps(index), encoding="utf-8")

    def fake_layout_paths(_cfg):
        return SimpleNamespace(
            saves_dir=saves,
            chunks_dir=chunks,
            index_file=saves / "index.json",
            districts_cache_file=saves / "districts_cache.json",
            surveys_cache_file=saves / "surveys_cache.json",
            blueprint_file=saves / "blueprint.json",
        )

    monkeypatch.setattr("core.persistence._save_layout_paths", fake_layout_paths)

    world = WorldState(
        chunk_size_tiles=strict_cfg.grid.chunk_size_tiles,
        system_configuration=strict_cfg,
    )
    with pytest.raises(SaveIndexError):
        load_state(world, system_configuration=strict_cfg)


def test_load_state_rejects_chunk_tile_oob_when_coordinate_guard_on(tmp_path, monkeypatch):
    """Chunk tiles outside limits must fail load when world_place_tile_reject_out_of_bounds=1."""
    guard_cfg = replace(
        SYSTEM_CONFIGURATION,
        world_place_tile_reject_out_of_bounds_flag=1,
    )
    hi = int(guard_cfg.maximum_coordinate_value)
    saves = tmp_path / "saves"
    chunks = saves / "chunks"
    chunks.mkdir(parents=True)
    index = {
        "save_format_version": int(SAVE_FORMAT_VERSION),
        "chunk_size": 64,
        "turn": 0,
        "current_period": "",
        "current_year": 0,
        "chunk_manifest": ["0_0"],
        "district_index": 0,
        "district_build_cursor": 0,
        "build_wave_phase": "landmark",
        "districts": [],
        "generation": 0,
    }
    (saves / "index.json").write_text(json.dumps(index), encoding="utf-8")
    bad_tile = {
        "x": hi + 1,
        "y": 0,
        "terrain": "road",
        "elevation": 0.0,
    }
    (chunks / "chunk_0_0.json").write_text(json.dumps([bad_tile]), encoding="utf-8")

    def fake_layout_paths(_cfg):
        return SimpleNamespace(
            saves_dir=saves,
            chunks_dir=chunks,
            index_file=saves / "index.json",
            districts_cache_file=saves / "districts_cache.json",
            surveys_cache_file=saves / "surveys_cache.json",
            blueprint_file=saves / "blueprint.json",
        )

    monkeypatch.setattr("core.persistence._save_layout_paths", fake_layout_paths)

    world = WorldState(
        chunk_size_tiles=guard_cfg.grid.chunk_size_tiles,
        system_configuration=guard_cfg,
    )
    with pytest.raises(SaveIndexError, match="rejected"):
        load_state(world, system_configuration=guard_cfg)
