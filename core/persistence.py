"""Chunked persistence — saves world state in per-chunk tile files + metadata index."""

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestration.world_commit import apply_tile_placements
from world.state import WorldState

if TYPE_CHECKING:
    from core.config import Config
from core.application_services import ApplicationServices
from core.errors import ConfigLoadError, SaveIndexError
from core.fingerprint import (
    CACHE_WRAP_VERSION,
    SAVE_FORMAT_VERSION,
    compute_run_fingerprint,
    compute_districts_layout_fingerprint,
)
from agents import llm_routing as llm_agents

logger = logging.getLogger("eternal.persistence")

_REPOSITORY_ROOT_PATH = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class _SaveLayoutPaths:
    saves_dir: Path
    chunks_dir: Path
    index_file: Path
    districts_cache_file: Path
    surveys_cache_file: Path
    blueprint_file: Path


def _save_layout_paths(system_configuration: "Config") -> _SaveLayoutPaths:
    rel = str(system_configuration.saves_directory_relative).strip()
    if not rel or ".." in rel:
        raise ConfigLoadError("saves_directory_relative must be a non-empty safe relative path")
    base = _REPOSITORY_ROOT_PATH / rel
    return _SaveLayoutPaths(
        saves_dir=base,
        chunks_dir=base / "chunks",
        index_file=base / "index.json",
        districts_cache_file=base / "districts_cache.json",
        surveys_cache_file=base / "surveys_cache.json",
        blueprint_file=base / "blueprint.json",
    )


def _llm_settings_file_path(system_configuration: "Config") -> Path:
    rel = str(system_configuration.llm_settings_file_relative).strip()
    if not rel or ".." in rel:
        raise ConfigLoadError("llm_settings_file_relative must be a safe relative path")
    return _REPOSITORY_ROOT_PATH / rel


def _ensure_dirs(system_configuration: "Config") -> _SaveLayoutPaths:
    paths = _save_layout_paths(system_configuration)
    paths.saves_dir.mkdir(parents=True, exist_ok=True)
    paths.chunks_dir.mkdir(parents=True, exist_ok=True)
    return paths


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _chunk_key(x: int, y: int, chunk_size_tiles: int) -> tuple[int, int]:
    """Return chunk coordinate for a tile at (x, y)."""
    return (x // chunk_size_tiles, y // chunk_size_tiles)


def _chunk_filename(cx: int, cy: int) -> str:
    return f"chunk_{cx}_{cy}.json"


def clamp_district_resume_indices_for_loaded_save(
    district_index: int,
    district_build_cursor: int,
    districts_len: int,
) -> tuple[int, int]:
    """Clamp persisted district pointers to ``[0, districts_len]`` (inclusive upper bound for cursors)."""
    if districts_len <= 0:
        return 0, 0
    di = max(0, min(int(district_index), districts_len))
    dbc = max(0, min(int(district_build_cursor), districts_len))
    return di, dbc


def _ingest_tiles_json_into_world(world: WorldState, tiles_payload: Any, *, chunk_label: str) -> int:
    """Parse chunk JSON array and place tiles. Raises SaveIndexError on corrupt data."""
    if not isinstance(tiles_payload, list):
        raise SaveIndexError(f"Chunk {chunk_label} root must be a JSON array")
    batch: list[tuple[int, int, dict]] = []
    for tile_data in tiles_payload:
        if not isinstance(tile_data, dict):
            raise SaveIndexError(f"Chunk {chunk_label} contains non-object tile entry")
        try:
            x = tile_data["x"]
            y = tile_data["y"]
        except KeyError as missing_key:
            raise SaveIndexError(f"Chunk {chunk_label} tile missing key: {missing_key}") from missing_key
        batch.append((int(x), int(y), dict(tile_data)))
    if not batch:
        return 0
    batch_result = apply_tile_placements(
        world,
        batch,
        system_configuration=world.system_configuration,
    )
    if batch_result.skipped_invalid_coordinates:
        raise SaveIndexError(
            f"Chunk {chunk_label} had {batch_result.skipped_invalid_coordinates} "
            "tile entries with invalid coordinates after coercion"
        )
    if batch_result.place_tile_rejections_count:
        raise SaveIndexError(
            f"Chunk {chunk_label} rejected {batch_result.place_tile_rejections_count} "
            "tile placements (e.g. coordinates outside bounds when "
            "world_place_tile_reject_out_of_bounds=1)"
        )
    return len(batch_result.placed_tile_dicts)


def _load_chunk_file_into_world(world: WorldState, chunk_path: Path) -> int:
    raw_text = chunk_path.read_text(encoding="utf-8")
    try:
        tiles_payload = json.loads(raw_text)
    except json.JSONDecodeError as json_err:
        raise SaveIndexError(f"Chunk file is not valid JSON: {chunk_path}") from json_err
    return _ingest_tiles_json_into_world(
        world, tiles_payload, chunk_label=str(chunk_path.name)
    )


def _current_run_fingerprint(
    scenario: dict[str, Any] | None,
    chunk_size_tiles: int,
    *,
    system_configuration: "Config",
) -> str:
    return compute_run_fingerprint(
        scenario,
        chunk_size_tiles,
        system_configuration.grid.world_grid_width,
        system_configuration.grid.world_grid_height,
    )


def _tiles_payload_for_chunk(world: WorldState, ck: tuple[int, int]) -> list[dict]:
    coords = world.chunk_tile_coords(ck)
    out: list[dict] = []
    for tx, ty in coords:
        tile = world.tiles.get((tx, ty))
        if not tile or tile.terrain == "empty":
            continue
        out.append(tile.to_dict())
    return out


def _delete_stale_chunk_files(active_keys: set[tuple[int, int]], chunks_dir: Path) -> int:
    removed = 0
    if not chunks_dir.exists():
        return removed
    for chunk_path in chunks_dir.glob("chunk_*.json"):
        name = chunk_path.stem
        parts = name.split("_")
        if len(parts) != 3:
            continue
        try:
            cx, cy = int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if (cx, cy) not in active_keys:
            chunk_path.unlink(missing_ok=True)
            removed += 1
    return removed


# ─── Save ───────────────────────────────────────────────────────────


def save_state(
    world: WorldState,
    chat_history: list[dict],
    district_index: int,
    districts: list[dict] | None = None,
    generation: int = 0,
    *,
    scenario: dict[str, Any],
    system_configuration: "Config",
    flush_mode: str = "incremental",
    build_wave_phase: str = "landmark",
    district_build_cursor: int | None = None,
) -> str:
    """Save world state: ``index.json`` + chunk JSON files + ``chat_history.json``.

    ``flush_mode``:
    - ``incremental`` — write only ``world._dirty_chunks``; prune removed chunk files on disk;
      manifest lists all chunks that still contain tiles (from chunk index).
    - ``full`` — rebuild every chunk file from ``world.tiles``, prune stale files, clear dirty set.

    ``scenario`` — required run scenario dict (same object as ``RunSession.scenario``).
    """
    if not isinstance(scenario, dict):
        raise TypeError("save_state requires scenario: dict[str, Any]")

    wave_phase_normalized = str(build_wave_phase or "landmark").strip().lower()
    if wave_phase_normalized not in ("landmark", "infill"):
        raise TypeError("save_state build_wave_phase must be 'landmark' or 'infill'")
    cursor_effective = int(district_index if district_build_cursor is None else district_build_cursor)

    paths = _ensure_dirs(system_configuration)

    scenario_effective: dict[str, Any] = scenario

    run_started_at_s: float | None = None
    run_started_at_s = scenario_effective.get("started_at_s")
    if run_started_at_s is not None:
        run_started_at_s = float(run_started_at_s)
    if run_started_at_s is None:
        run_started_at_s = time.time()
        scenario_effective["started_at_s"] = run_started_at_s

    chunk_sz = world.chunk_size_tiles
    run_fp = _current_run_fingerprint(
        scenario_effective,
        chunk_sz,
        system_configuration=system_configuration,
    )
    layout_fp = compute_districts_layout_fingerprint(districts or [])

    chunks_to_write: set[tuple[int, int]]
    if flush_mode == "full":
        chunks_to_write = set(world.chunk_keys_with_tiles())
    else:
        chunks_to_write = set(world._dirty_chunks)

    chunk_payloads: dict[tuple[int, int], list[dict]] = {}
    for ck in chunks_to_write:
        payload = _tiles_payload_for_chunk(world, ck)
        path = paths.chunks_dir / _chunk_filename(ck[0], ck[1])
        if not payload:
            path.unlink(missing_ok=True)
        else:
            _atomic_write(path, json.dumps(payload))
        chunk_payloads[ck] = payload

    active_chunk_keys = set(world.chunk_keys_with_tiles())
    pruned = _delete_stale_chunk_files(active_chunk_keys, paths.chunks_dir)
    if pruned:
        logger.info("Pruned %d stale chunk file(s) not in active manifest", pruned)

    chunk_manifest = sorted([f"{cx}_{cy}" for cx, cy in active_chunk_keys])
    world._dirty_chunks.clear()

    index_backup_path = paths.saves_dir / "index.json.bak"
    if paths.index_file.is_file():
        try:
            shutil.copy2(paths.index_file, index_backup_path)
        except OSError:
            logger.warning("Could not copy index.json to backup before save", exc_info=True)

    chat_history_path = paths.saves_dir / "chat_history.json"
    _atomic_write(chat_history_path, json.dumps(chat_history))

    index = {
        "save_format_version": SAVE_FORMAT_VERSION,
        "run_fingerprint": run_fp,
        "districts_layout_fingerprint": layout_fp,
        "chunk_manifest": chunk_manifest,
        "district_index": district_index,
        "build_wave_phase": wave_phase_normalized,
        "district_build_cursor": cursor_effective,
        "districts": districts or [],
        "generation": generation,
        "turn": world.turn,
        "current_period": world.current_period,
        "current_year": world.current_year,
        "min_x": world.min_x,
        "max_x": world.max_x,
        "min_y": world.min_y,
        "max_y": world.max_y,
        "chat_history_file": chat_history_path.name,
        "run_started_at_s": run_started_at_s,
        "scenario": scenario_effective,
        "chunk_size": chunk_sz,
    }
    _atomic_write(paths.index_file, json.dumps(index, indent=2))

    total_tiles = sum(1 for t in world.tiles.values() if t.terrain != "empty")
    scen_loc = ""
    if isinstance(scenario_effective, dict):
        scen_loc = str(scenario_effective.get("location") or "")
    logger.info(
        "Saved (%s): %s tiles in %s chunks | district_index=%s cursor=%s wave=%s generation=%s turn=%s | "
        "chat_msgs=%s districts=%s world_bounds=(%s..%s,%s..%s) scenario=%s | pruned=%s",
        flush_mode,
        total_tiles,
        len(chunk_manifest),
        district_index,
        cursor_effective,
        wave_phase_normalized,
        generation,
        world.turn,
        len(chat_history),
        len(districts or []),
        world.min_x,
        world.max_x,
        world.min_y,
        world.max_y,
        scen_loc or "(none)",
        pruned,
    )
    return str(paths.index_file)


# ─── Load ───────────────────────────────────────────────────────────


def _load_index_dict(paths: _SaveLayoutPaths) -> dict[str, Any]:
    """Parse ``index.json``; on JSON failure try ``index.json.bak``."""
    if not paths.index_file.is_file():
        raise SaveIndexError("Missing index.json")
    raw = paths.index_file.read_text(encoding="utf-8")
    try:
        out = json.loads(raw)
    except json.JSONDecodeError as primary_err:
        index_backup_path = paths.saves_dir / "index.json.bak"
        if index_backup_path.is_file():
            try:
                out = json.loads(index_backup_path.read_text(encoding="utf-8"))
                logger.warning("index.json was corrupt — loaded from index.json.bak")
            except json.JSONDecodeError as backup_err:
                raise SaveIndexError(
                    "index.json is corrupt and index.json.bak could not be parsed as JSON."
                ) from backup_err
        else:
            raise SaveIndexError(
                "index.json is not valid JSON and no index.json.bak backup exists."
            ) from primary_err
    if not isinstance(out, dict):
        raise SaveIndexError("index.json root must be a JSON object")
    return out


def load_state(
    world: WorldState,
    *,
    system_configuration: "Config",
) -> tuple[list[dict], int, list[dict], int, dict[str, Any] | None, str, int] | None:
    """Load from chunked format.

    Returns ``(chat_history, district_index, districts, generation, scenario, build_wave_phase,
    district_build_cursor)`` or None.
    """
    paths = _save_layout_paths(system_configuration)
    if not paths.index_file.exists():
        return None

    index = _load_index_dict(paths)

    save_ver_raw = index.get("save_format_version", SAVE_FORMAT_VERSION)
    try:
        save_ver_disk = int(save_ver_raw)
    except (TypeError, ValueError):
        save_ver_disk = 1
    if save_ver_disk != int(SAVE_FORMAT_VERSION):
        if int(system_configuration.persistence_fail_on_save_format_version_mismatch_flag) == 1:
            raise SaveIndexError(
                f"save_format_version {save_ver_disk} does not match code {SAVE_FORMAT_VERSION} "
                "(set persistence_fail_on_save_format_version_mismatch=0 to allow load with warning)"
            )
        logger.warning(
            "save_format_version mismatch: index.json has %s, code expects %s — continuing load",
            save_ver_disk,
            SAVE_FORMAT_VERSION,
        )

    idx_chunk = index.get("chunk_size")
    if isinstance(idx_chunk, int) and idx_chunk >= 1:
        world.chunk_size_tiles = idx_chunk

    world.turn = int(index.get("turn", 0))
    world.current_period = str(index.get("current_period", ""))
    world.current_year = int(index.get("current_year", 0))

    tile_count = 0
    save_ver = save_ver_disk
    manifest_raw = index.get("chunk_manifest")

    if save_ver >= 2 and isinstance(manifest_raw, list) and manifest_raw:
        for entry in manifest_raw:
            if not isinstance(entry, str) or "_" not in entry:
                continue
            a, _, b = entry.partition("_")
            try:
                cx, cy = int(a), int(b)
            except ValueError:
                continue
            chunk_path = paths.chunks_dir / _chunk_filename(cx, cy)
            if not chunk_path.is_file():
                logger.warning("Missing chunk file for manifest entry %s", entry)
                continue
            tile_count += _load_chunk_file_into_world(world, chunk_path)
    else:
        if paths.chunks_dir.exists():
            for chunk_file in paths.chunks_dir.glob("chunk_*.json"):
                tile_count += _load_chunk_file_into_world(world, chunk_file)

    world.build_log.clear()
    world.rebuild_chunk_tile_index()

    chat_ref = index.get("chat_history_file")
    if isinstance(chat_ref, str) and chat_ref.strip():
        chat_path = paths.saves_dir / chat_ref.strip()
        if chat_path.is_file():
            try:
                chat_history = json.loads(chat_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as chat_json_error:
                logger.exception(
                    "chat_history_file invalid JSON path=%s",
                    chat_path,
                    exc_info=chat_json_error,
                )
                raise SaveIndexError(
                    f"chat_history_file is not valid JSON: {chat_path}"
                ) from chat_json_error
        else:
            logger.warning("chat_history_file %s missing — using empty history", chat_path)
            chat_history = []
    elif "chat_history" in index:
        ch = index["chat_history"]
        chat_history = ch if isinstance(ch, list) else []
    else:
        chat_history = []
    district_index = int(index.get("district_index", 0))
    build_wave_phase_raw = str(index.get("build_wave_phase", "landmark")).strip().lower()
    if build_wave_phase_raw not in ("landmark", "infill"):
        build_wave_phase_raw = "landmark"
    district_build_cursor = int(index.get("district_build_cursor", district_index))
    districts = index.get("districts")
    if not isinstance(districts, list):
        districts = []
    raw_district_index_before_clamp = district_index
    raw_district_build_cursor_before_clamp = district_build_cursor
    district_index, district_build_cursor = clamp_district_resume_indices_for_loaded_save(
        district_index,
        district_build_cursor,
        len(districts),
    )
    if (
        raw_district_index_before_clamp != district_index
        or raw_district_build_cursor_before_clamp != district_build_cursor
    ):
        logger.warning(
            "Clamped district resume indices from (district_index=%s, district_build_cursor=%s) "
            "to (%s, %s) for districts_len=%s",
            raw_district_index_before_clamp,
            raw_district_build_cursor_before_clamp,
            district_index,
            district_build_cursor,
            len(districts),
        )
    gen_raw = index.get("generation", 0)
    generation = int(gen_raw) if isinstance(gen_raw, (int, float)) else 0

    scen: dict[str, Any] | None = None
    raw_scen = index.get("scenario")
    if isinstance(raw_scen, dict) and raw_scen:
        scen = dict(raw_scen)
        run_started_at_s = index.get("run_started_at_s")
        if isinstance(run_started_at_s, (int, float)):
            run_started_at_s = float(run_started_at_s)
        else:
            run_started_at_s = None
        if scen.get("started_at_s") is None:
            if run_started_at_s is not None:
                scen = {**scen, "started_at_s": run_started_at_s}
            else:
                scen = {**scen, "started_at_s": time.time()}
        if not (world.current_period or "").strip() and scen.get("period"):
            world.current_period = str(scen["period"])
        fy = scen.get("focus_year")
        if fy is not None:
            world.current_year = int(fy)

    logger.info(
        "Loaded: %s tiles, district #%s, cursor=%s wave=%s, %s districts, generation %s",
        tile_count,
        district_index,
        district_build_cursor,
        build_wave_phase_raw,
        len(districts),
        generation,
    )
    return chat_history, district_index, districts, generation, scen, build_wave_phase_raw, district_build_cursor


def clear_saves(*, system_configuration: "Config") -> int:
    """Delete all save data (full reset). Returns 1 if a saves directory was removed."""
    cfg = system_configuration
    saves_dir = _save_layout_paths(cfg).saves_dir
    if saves_dir.exists():
        shutil.rmtree(saves_dir)
        logger.info("All save data cleared")
        return 1
    logger.info("clear_saves: no saves directory at %s", saves_dir)
    return 0


# ─── District + Survey Caches ───────────────────────────────────────


def save_districts_cache(
    districts: list[dict],
    map_description: str = "",
    *,
    run_fingerprint: str,
    system_configuration: "Config",
) -> None:
    cfg = system_configuration
    paths = _ensure_dirs(cfg)
    fp = run_fingerprint
    layout_fp = compute_districts_layout_fingerprint(districts)
    data = {
        "cache_wrap_version": CACHE_WRAP_VERSION,
        "run_fingerprint": fp,
        "districts_layout_fingerprint": layout_fp,
        "districts": districts,
        "map_description": map_description,
    }
    _atomic_write(paths.districts_cache_file, json.dumps(data, indent=2))
    logger.info("Cached %s districts (fp=%s)", len(districts), fp[:12])


def load_districts_cache(
    *,
    expected_run_fingerprint: str | None = None,
    system_configuration: "Config",
) -> tuple[list[dict], str] | None:
    cfg = system_configuration
    cache_path = _save_layout_paths(cfg).districts_cache_file
    if not cache_path.exists():
        return None
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "districts" in data:
        fp = data.get("run_fingerprint")
        if expected_run_fingerprint is not None and fp != expected_run_fingerprint:
            logger.warning(
                "Districts cache fingerprint mismatch (disk=%s expected=%s) — ignoring",
                fp,
                expected_run_fingerprint,
            )
            return None
        districts = data["districts"]
        map_desc = data.get("map_description", "")
    else:
        return None
    cleaned: list[dict] = []
    for d in districts:
        if not isinstance(d, dict) or "name" not in d or "region" not in d:
            logger.warning("Skipping malformed district cache entry: %s", d)
            continue
        cleaned.append(d)
    if not cleaned:
        logger.warning("Districts cache had no valid entries — ignoring")
        return None
    districts = cleaned
    logger.info("Loaded %s cached districts", len(districts))
    return districts, map_desc


def save_surveys_cache(
    surveys: dict[str, list],
    *,
    run_fingerprint: str,
    system_configuration: "Config",
) -> None:
    cfg = system_configuration
    paths = _ensure_dirs(cfg)
    fp = run_fingerprint
    wrapped = {
        "cache_wrap_version": CACHE_WRAP_VERSION,
        "run_fingerprint": fp,
        "plans": surveys,
    }
    _atomic_write(paths.surveys_cache_file, json.dumps(wrapped, indent=2))


def load_surveys_cache(
    *,
    expected_run_fingerprint: str | None = None,
    system_configuration: "Config",
) -> dict[str, list]:
    cfg = system_configuration
    surveys_path = _save_layout_paths(cfg).surveys_cache_file
    if not surveys_path.exists():
        return {}
    data = json.loads(surveys_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Surveys cache is not a dict")
    if "plans" in data and "run_fingerprint" in data:
        if expected_run_fingerprint is not None and data["run_fingerprint"] != expected_run_fingerprint:
            logger.warning("Survey cache run fingerprint mismatch — ignoring disk cache")
            return {}
        inner = data["plans"]
        if not isinstance(inner, dict):
            raise ValueError("Surveys cache plans must be a dict")
        for k, v in inner.items():
            if not isinstance(v, list):
                raise ValueError(f"Survey cache entry {k!r} is not a list")
        return inner
    logger.warning("Legacy surveys cache format — ignoring")
    return {}


# ─── Blueprint ─────────────────────────────────────────────────────


def save_blueprint(
    blueprint_dict: dict,
    *,
    system_configuration: "Config",
) -> None:
    """Save city blueprint data to disk."""
    cfg = system_configuration
    paths = _ensure_dirs(cfg)
    _atomic_write(paths.blueprint_file, json.dumps(blueprint_dict, indent=2))
    logger.info("Blueprint saved")


def load_blueprint(*, system_configuration: "Config") -> dict | None:
    """Load city blueprint data from disk. Returns dict or None."""
    cfg = system_configuration
    blueprint_path = _save_layout_paths(cfg).blueprint_file
    if not blueprint_path.exists():
        return None
    try:
        data = json.loads(blueprint_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            logger.info("Blueprint loaded from disk")
            return data
    except json.JSONDecodeError as exc:
        logger.exception("Blueprint JSON invalid: %s", blueprint_path)
        raise ConfigLoadError(f"Blueprint file is not valid JSON: {blueprint_path}") from exc
    return None


def validate_blueprint_tile_invariants(world: WorldState, blueprint_dict: dict | None) -> list[str]:
    """Return human-readable issues when blueprint and placed tiles disagree (non-fatal)."""
    issues: list[str] = []
    if not isinstance(blueprint_dict, dict) or not blueprint_dict:
        return issues
    roads = blueprint_dict.get("roads")
    if isinstance(roads, list) and roads:
        road_names = {str(r.get("name", "")) for r in roads if isinstance(r, dict)}
        if road_names:
            matched = sum(
                1
                for t in world.tiles.values()
                if t.terrain == "road" and (t.building_name or "") in road_names
            )
            if matched == 0 and any(t.terrain == "road" for t in world.tiles.values()):
                issues.append("Blueprint lists roads but no road tiles match blueprint road names")
    return issues


# ─── LLM Settings ──────────────────────────────────────────────────


def save_llm_settings(overrides: dict, *, system_configuration: "Config") -> None:
    _atomic_write(
        _llm_settings_file_path(system_configuration),
        json.dumps(overrides, indent=2),
    )


def load_llm_settings(
    *,
    system_configuration: "Config",
    application_services: ApplicationServices,
) -> None:
    path = _llm_settings_file_path(system_configuration)
    if not path.exists():
        return
    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except json.JSONDecodeError as json_err:
        logger.exception("llm_settings JSON parse failed path=%s", path)
        raise ConfigLoadError(f"llm_settings.json is not valid JSON: {path}") from json_err
    if isinstance(data, dict) and data:
        llm_agents.set_runtime_overrides(data, application_services=application_services)
        logger.info("Loaded LLM settings (%s agents)", len(data))


def merge_llm_overrides_from_save(
    current: dict[str, dict[str, Any]],
    incoming: dict[str, dict[str, Any]],
    *,
    application_services: ApplicationServices,
) -> dict[str, dict[str, Any]]:
    """Apply UI save per agent; blank API key keeps previously saved key."""
    out: dict[str, dict[str, Any]] = {k: dict(v) for k, v in current.items()}
    for agent_key, patch in incoming.items():
        if agent_key not in application_services.agent_llm_specs_dictionary or not isinstance(patch, dict):
            continue
        prev = out.get(agent_key, {})
        merged: dict[str, Any] = {}
        for k, v in patch.items():
            if v is None:
                continue
            if k == "openai_api_key" and isinstance(v, str) and not v.strip():
                continue
            merged[k] = v
        out[agent_key] = {**prev, **merged}
    return out
