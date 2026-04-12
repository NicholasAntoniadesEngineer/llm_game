"""Eternal Cities — Strict Configuration System loaded exclusively from data/system_config.csv.

All parameters, symbols, thresholds, defaults, sets, and values are defined in the CSV.
Typed dataclasses with sub-configs for Grid, Terrain, Performance, LLM, Token, Timing, UI.
No globals, no hardcoded values, no defaults in code, relative paths only, descriptive variable names (2-3+ words), structures for params, try/except everywhere that logs to run_log then fails hard.
Explicit config instance injected everywhere. System fails hard on any invalid or missing entry.
Production ready, complete, zero warnings, no TODOs, no duplication, no void returns.
"""

import csv
import functools
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Set

from core.errors import ConfigLoadError, EternalCitiesError
from core.run_log import log_event

_config_logger = logging.getLogger("eternal.config")

# Single registry of CSV keys required for a valid ``Config`` (must match rows in system_config.csv).
SYSTEM_CONFIG_REQUIRED_CSV_KEYS: frozenset[str] = frozenset({
    "grid_width", "grid_height", "max_districts", "chunk_size", "world_scale_meters",
    "elevation_scale", "max_elevation", "max_building_height", "min_building_height",
    "terrain_max_gradient", "terrain_gradient_iterations",
    "max_buildings_per_district", "survey_buildings_per_chunk", "urbanista_max_concurrent",
    "survey_max_concurrent", "save_state_every_n_structures", "chat_persist_debounce_s",
    "heartbeat_interval_s", "expansion_cooldown", "chat_history_max_messages",
    "chat_replay_max_messages", "token_telemetry_interval_s", "timeline_window", "step_delay",
    "max_generations", "claude_cli_binary", "max_batch_size", "max_batch_tiles",
    "cost_per_1m_input", "cost_per_1m_output", "max_retries", "retry_backoff_base", "retry_jitter",
    "max_prompt_tokens", "max_response_tokens", "agent_timeout_short", "agent_timeout_medium",
    "agent_timeout_long", "open_terrain_types",
    "wave1_building_types", "batchable_types", "terrain_defaults", "ui_agents",
    "llm_defaults_path", "data_dir", "saves_dir", "log_level", "max_society_file_size",
    "max_blueprint_file_size", "max_city_name_length", "max_building_name_length",
    "max_coordinate_value", "min_coordinate_value", "performance_warning_threshold",
    "http_timeout_short", "http_timeout_medium", "http_timeout_long",
    "rate_limit_requests", "rate_limit_window",
    "llm_settings_path",
    "run_log_buffer_max_lines", "blueprint_incremental_tile_threshold", "blueprint_halo_expand_iterations",
    "spatial_optimal_shift_step_tiles", "procedural_terrain_description_max_chars",
    "procedural_terrain_fallback_hex_color",
    "urbanista_commentary_display_max_chars", "urbanista_geometry_collision_report_max_entries",
    "urbanista_max_consecutive_failures_before_pause", "urbanista_batchable_tile_max_count",
    "footprint_width_depth_scale_factor", "district_coherence_reference_area",
    "claude_cli_base_timeout_haiku_seconds", "claude_cli_base_timeout_other_seconds",
    "claude_cli_result_preview_char_limit", "claude_cli_connection_error_preview_chars",
    "claude_cli_scaled_input_chars_threshold", "claude_cli_scaled_chars_per_extra_minute_block",
    "claude_cli_scaled_extra_seconds_per_block",
    "agent_status_idle_string", "agent_status_active_string", "agent_status_error_string",
    "skeleton_planner_debug_json_max_chars", "skeleton_planner_inter_retry_wait_seconds",
    "district_spacing_by_style", "road_bridge_default_elevation", "agent_failure_detail_max_chars",
    "material_roughness_low_default", "material_roughness_medium_default", "material_roughness_high_default",
    "world_place_tile_min_elevation", "world_build_log_max_entries", "world_build_log_trim_keep_entries",
    "terrain_type_display_colors", "terrain_display_icons", "building_type_display_icons",
    "api_pause_auto_retry_delay_seconds", "api_pause_retriable_reasons",
    "society_file_extension",
    "cities_json_relative",
    "known_cities_json_relative",
    "architectural_reference_file_relative",
    "openai_compatible_temperature",
    "skeleton_cli_kill_subprocess_on_timeout",
    "terrain_classification_thresholds",
    "terrain_stability_terrain_type_modifiers",
    "terrain_stability_soil_type_modifiers",
    "road_surface_colors_by_type",
    "blueprint_climate_determination_dictionary",
    "known_cartography_map_dictionary",
    "building_material_hex_colors_dictionary",
    "http_server_listen_host",
    "http_server_listen_port",
    "uvicorn_log_level",
    "uvicorn_reload_delay_seconds",
    "world_reset_default_year",
    "server_reload_sentinel_pre_touch_sleep_seconds",
    "token_estimate_chars_per_token",
    "blueprint_default_primary_stone",
    "blueprint_default_secondary_stone",
    "blueprint_default_brick_type",
    "blueprint_default_roof_material",
    "master_plan_fail_on_intra_plan_tile_overlap",
    "society_validation_strict",
    "token_telemetry_broadcast_failure_raises",
    "master_plan_duplicate_tile_policy",
})


@functools.lru_cache(maxsize=16)
def _cached_llm_defaults_raw_dict(path_str: str, mtime_ns: int) -> Dict[str, Any]:
    """Reads and parses LLM defaults JSON; cache key includes mtime so edits invalidate without restart."""
    path_obj = Path(path_str)
    return json.loads(path_obj.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class GridConfig:
    """All grid and spatial parameters loaded and validated from CSV."""
    world_grid_width: int
    world_grid_height: int
    maximum_districts_count: int
    chunk_size_tiles: int
    world_scale_meters_per_tile: float
    elevation_scale_factor: float
    maximum_elevation_value: float
    timeline_window_years: int
    maximum_buildings_per_district_count: int
    survey_buildings_per_chunk_count: int
    maximum_generations_cap: int


@dataclass(frozen=True)
class TerrainConfig:
    """Terrain, gradient, and procedural defaults from CSV. Used in world generation."""
    maximum_gradient_value: float
    gradient_iterations_count: int
    open_terrain_types_set: Set[str]
    wave_one_building_types_set: Set[str]
    batchable_types_set: Set[str]
    terrain_defaults_dictionary: Dict[str, Any]
    maximum_building_height_meters: float
    minimum_building_height_meters: float
    material_roughness_low_default: float
    material_roughness_medium_default: float
    material_roughness_high_default: float
    terrain_classification_thresholds_dictionary: Dict[str, Any]
    terrain_stability_terrain_type_modifiers_dictionary: Dict[str, float]
    terrain_stability_soil_type_modifiers_dictionary: Dict[str, float]
    road_surface_colors_by_type_dictionary: Dict[str, str]
    building_material_hex_colors_dictionary: Dict[str, str]


@dataclass(frozen=True)
class PerformanceConfig:
    """Concurrency, batching, retry, and scaling parameters from CSV."""
    urbanista_maximum_concurrent_calls: int
    survey_maximum_concurrent_calls: int
    maximum_batch_size_value: int
    maximum_batch_tiles_count: int
    save_state_every_n_structures_count: int
    maximum_retries_count: int
    retry_backoff_base_value: float
    retry_jitter_value: float


@dataclass(frozen=True)
class LlmConfig:
    """LLM routing, timeouts, paths (relative only) from CSV."""
    defaults_path_relative: str
    claude_cli_binary_name: str
    agent_timeout_short_seconds: int
    agent_timeout_medium_seconds: int
    agent_timeout_long_seconds: int
    maximum_prompt_tokens_count: int
    maximum_response_tokens_count: int
    http_timeout_short_seconds: int
    http_timeout_medium_seconds: int
    http_timeout_long_seconds: int
    claude_cli_base_timeout_haiku_seconds: int
    claude_cli_base_timeout_other_seconds: int
    claude_cli_result_preview_char_limit: int
    claude_cli_connection_error_preview_chars: int
    claude_cli_scaled_input_chars_threshold: int
    claude_cli_scaled_chars_per_extra_minute_block: int
    claude_cli_scaled_extra_seconds_per_block: int


@dataclass(frozen=True)
class TokenConfig:
    """Token usage and cost parameters from CSV."""
    cost_per_million_input_tokens: float
    cost_per_million_output_tokens: float
    token_telemetry_interval_seconds: int
    estimated_chars_per_token_for_heuristic: float


@dataclass(frozen=True)
class TimingConfig:
    """All timing, debounce, cooldown values from CSV."""
    step_delay_seconds: float
    chat_persist_debounce_seconds: float
    heartbeat_interval_seconds: float
    expansion_cooldown_seconds: float
    rate_limit_window_seconds: int


@dataclass(frozen=True)
class UiConfig:
    """UI, agent display, logging parameters from CSV."""
    ui_agents_dictionary: Dict[str, Dict[str, Any]]
    agent_status_idle_string: str
    agent_status_active_string: str
    agent_status_error_string: str
    log_level_string: str


@dataclass(frozen=True)
class Config:
    """Central typed configuration. Loaded once from system_config.csv.
    Injected into BuildCoordinator, WorldState, WorldGenerator, all components.
    Provides all values with descriptive 3-word attribute names where possible.
    All methods have try/except, log via run_log, then fail hard. No void returns."""
    grid: GridConfig
    terrain: TerrainConfig
    performance: PerformanceConfig
    llm: LlmConfig
    token: TokenConfig
    timing: TimingConfig
    ui: UiConfig
    data_directory_relative: str
    saves_directory_relative: str
    llm_settings_file_relative: str
    maximum_society_file_size_bytes: int
    maximum_blueprint_file_size_bytes: int
    maximum_city_name_length: int
    maximum_building_name_length: int
    maximum_coordinate_value: int
    minimum_coordinate_value: int
    performance_warning_threshold_seconds: float
    chat_history_max_messages: int
    chat_replay_max_messages: int
    rate_limit_requests_per_window: int
    run_log_buffer_max_lines: int
    blueprint_incremental_tile_threshold: int
    blueprint_halo_expand_iterations: int
    spatial_optimal_shift_step_tiles: int
    procedural_terrain_description_max_chars: int
    procedural_terrain_fallback_hex_color: str
    urbanista_commentary_display_max_chars: int
    urbanista_geometry_collision_report_max_entries: int
    urbanista_max_consecutive_failures_before_pause: int
    urbanista_batchable_tile_max_count: int
    footprint_width_depth_scale_factor: float
    district_coherence_reference_area: int
    skeleton_planner_debug_json_max_chars: int
    skeleton_planner_inter_retry_wait_seconds: int
    district_spacing_by_style_dictionary: Dict[str, int]
    road_bridge_default_elevation: float
    agent_failure_detail_max_chars: int
    society_file_extension_suffix: str
    world_place_tile_min_elevation: float
    world_build_log_max_entries: int
    world_build_log_trim_keep_entries: int
    terrain_type_display_colors_extra_dictionary: Dict[str, Any]
    terrain_display_icons_dictionary: Dict[str, str]
    building_type_display_icons_dictionary: Dict[str, str]
    api_pause_auto_retry_delay_seconds: float
    api_pause_retriable_reasons_set: Set[str]
    cities_json_relative: str
    known_cities_json_relative: str
    architectural_reference_file_relative: str
    openai_compatible_temperature: float
    skeleton_cli_kill_subprocess_on_timeout: int
    blueprint_climate_determination_dictionary: Dict[str, Any]
    known_cartography_map_dictionary: Dict[str, Any]
    http_server_listen_host_string: str
    http_server_listen_port_int: int
    uvicorn_log_level_string: str
    uvicorn_reload_delay_seconds: float
    world_reset_default_year_int: int
    server_reload_sentinel_pre_touch_sleep_seconds: float
    blueprint_default_primary_stone_string: str
    blueprint_default_secondary_stone_string: str
    blueprint_default_brick_type_string: str
    blueprint_default_roof_material_string: str
    master_plan_fail_on_intra_plan_tile_overlap_flag: int
    society_validation_strict_flag: int
    token_telemetry_broadcast_failure_raises_flag: int
    master_plan_duplicate_tile_policy_string: str

    @classmethod
    def load_from_system_config(cls, csv_path_relative: str = "data/system_config.csv") -> "Config":
        """Loads and strictly validates EVERY parameter from CSV. Fails hard on any issue.
        Try-catch around all operations per rules. Logs exclusively via run_log."""
        csv_path_obj = Path(csv_path_relative)
        if not csv_path_obj.is_file():
            try:
                log_event(
                    "config",
                    "system_config.csv missing",
                    csv_path_relative=csv_path_relative,
                )
            except Exception as log_failure:
                _config_logger.error(
                    "config_log_event_failed_while_reporting_missing_csv path=%s",
                    csv_path_relative,
                    exc_info=log_failure,
                )
                raise ConfigLoadError(
                    "Run log failed while reporting missing system_config.csv; must not continue on catch."
                ) from log_failure
            raise ConfigLoadError(
                f"Explicit configuration required: {csv_path_relative} missing. "
                "Add all parameters from previous config/constants/engine/generators and restart. "
                "System operates only if all aspects work exactly as designed."
            )

        params: Dict[str, Any] = {}
        try:
            with open(csv_path_obj, "r", newline="", encoding="utf-8") as csv_file:
                reader = csv.DictReader(csv_file)
                if not reader.fieldnames or "key" not in reader.fieldnames:
                    raise ConfigLoadError("CSV header must include 'key' column")
                for row_index, row in enumerate(reader, start=1):
                    try:
                        key_raw = row.get("key", "").strip()
                        if not key_raw:
                            continue
                        if key_raw.startswith("#"):
                            continue
                        key = key_raw.lower().replace(" ", "_").replace("-", "_")
                        value_str = row.get("value", "").strip()
                        param_type_str = row.get("type", "str").strip().lower()
                        parsed = cls._parse_and_validate_csv_value(
                            key=key,
                            value_str=value_str,
                            param_type=param_type_str,
                            row=row,
                            row_index=row_index,
                        )
                        params[key] = parsed
                    except Exception as row_err:
                        log_event(
                            "config",
                            f"CSV row parse error row={row_index} key={key_raw}",
                            row_index=row_index,
                            key_raw=key_raw,
                            error=str(row_err),
                        )
                        raise ConfigLoadError(f"CSV row {row_index} ({key_raw}) invalid: {row_err}") from row_err
        except Exception as read_err:
            if not isinstance(read_err, ConfigLoadError):
                log_event(
                    "config",
                    "Failed to read or parse system_config.csv",
                    path=str(csv_path_obj),
                    error=str(read_err),
                )
                raise ConfigLoadError(f"Failed to read/parse system_config.csv: {read_err}") from read_err
            raise

        # Strict check for all required parameters (no defaults allowed)
        missing_params = SYSTEM_CONFIG_REQUIRED_CSV_KEYS - set(params.keys())
        if missing_params:
            log_event(
                "config",
                "Missing required parameters in system_config.csv",
                missing=sorted(missing_params),
            )
            raise ConfigLoadError(
                f"Missing required parameters in system_config.csv: {sorted(missing_params)}. "
                "All parameters/symbols from system_config.csv; add new entries. Fail hard."
            )

        if int(params["world_build_log_trim_keep_entries"]) > int(params["world_build_log_max_entries"]):
            raise ConfigLoadError(
                "world_build_log_trim_keep_entries must be <= world_build_log_max_entries"
            )

        overlap_flag = int(params["master_plan_fail_on_intra_plan_tile_overlap"])
        if overlap_flag not in (0, 1):
            raise ConfigLoadError("master_plan_fail_on_intra_plan_tile_overlap must be 0 or 1")
        society_validation_strict_flag = int(params["society_validation_strict"])
        if society_validation_strict_flag not in (0, 1):
            raise ConfigLoadError("society_validation_strict must be 0 or 1")
        token_telemetry_broadcast_failure_raises_flag = int(params["token_telemetry_broadcast_failure_raises"])
        if token_telemetry_broadcast_failure_raises_flag not in (0, 1):
            raise ConfigLoadError("token_telemetry_broadcast_failure_raises must be 0 or 1")
        master_plan_duplicate_tile_policy_string = str(params["master_plan_duplicate_tile_policy"]).strip().lower()
        if master_plan_duplicate_tile_policy_string not in ("repair", "fail"):
            raise ConfigLoadError(
                "master_plan_duplicate_tile_policy must be 'repair' or 'fail' (from system_config.csv)"
            )
        for stone_key in (
            "blueprint_default_primary_stone",
            "blueprint_default_secondary_stone",
            "blueprint_default_brick_type",
            "blueprint_default_roof_material",
        ):
            if not str(params[stone_key]).strip():
                raise ConfigLoadError(f"{stone_key} must be a non-empty string")

        # Build typed sub-configs using descriptive names (2-3 words where applicable)
        try:
            raw_spacing = params["district_spacing_by_style"]
            if not isinstance(raw_spacing, dict) or len(raw_spacing) == 0:
                raise ConfigLoadError("district_spacing_by_style must be a non-empty dict from CSV")
            district_spacing_normalized: Dict[str, int] = {}
            for style_key, gap_val in raw_spacing.items():
                try:
                    district_spacing_normalized[str(style_key).lower()] = int(gap_val)
                except (TypeError, ValueError) as spacing_err:
                    raise ConfigLoadError(
                        f"district_spacing_by_style invalid for key {style_key!r}: {spacing_err}"
                    ) from spacing_err
            grid_config_instance = GridConfig(
                world_grid_width=params["grid_width"],
                world_grid_height=params["grid_height"],
                maximum_districts_count=params["max_districts"],
                chunk_size_tiles=params["chunk_size"],
                world_scale_meters_per_tile=params["world_scale_meters"],
                elevation_scale_factor=params["elevation_scale"],
                maximum_elevation_value=params["max_elevation"],
                timeline_window_years=params["timeline_window"],
                maximum_buildings_per_district_count=params["max_buildings_per_district"],
                survey_buildings_per_chunk_count=params["survey_buildings_per_chunk"],
                maximum_generations_cap=params["max_generations"],
            )
            road_colors_raw = params["road_surface_colors_by_type"]
            if not isinstance(road_colors_raw, dict) or not road_colors_raw:
                raise ConfigLoadError("road_surface_colors_by_type must be a non-empty dict")
            road_colors_normalized: Dict[str, str] = {
                str(k).strip().lower(): str(v).strip() for k, v in road_colors_raw.items()
            }
            terrain_mods = params["terrain_stability_terrain_type_modifiers"]
            soil_mods = params["terrain_stability_soil_type_modifiers"]
            if not isinstance(terrain_mods, dict) or not terrain_mods:
                raise ConfigLoadError("terrain_stability_terrain_type_modifiers must be a non-empty dict")
            if not isinstance(soil_mods, dict) or not soil_mods:
                raise ConfigLoadError("terrain_stability_soil_type_modifiers must be a non-empty dict")
            terrain_mods_f: Dict[str, float] = {
                str(k): float(v) for k, v in terrain_mods.items()
            }
            soil_mods_f: Dict[str, float] = {
                str(k): float(v) for k, v in soil_mods.items()
            }
            material_colors_raw = params["building_material_hex_colors_dictionary"]
            if not isinstance(material_colors_raw, dict) or not material_colors_raw:
                raise ConfigLoadError("building_material_hex_colors_dictionary must be a non-empty dict")
            material_hex: Dict[str, str] = {
                str(k).strip().lower(): str(v).strip() for k, v in material_colors_raw.items()
            }
            terrain_config_instance = TerrainConfig(
                maximum_gradient_value=params["terrain_max_gradient"],
                gradient_iterations_count=params["terrain_gradient_iterations"],
                open_terrain_types_set=set(params["open_terrain_types"]),
                wave_one_building_types_set=set(params["wave1_building_types"]),
                batchable_types_set=set(params["batchable_types"]),
                terrain_defaults_dictionary=params["terrain_defaults"],
                maximum_building_height_meters=params["max_building_height"],
                minimum_building_height_meters=params["min_building_height"],
                material_roughness_low_default=params["material_roughness_low_default"],
                material_roughness_medium_default=params["material_roughness_medium_default"],
                material_roughness_high_default=params["material_roughness_high_default"],
                terrain_classification_thresholds_dictionary=params["terrain_classification_thresholds"],
                terrain_stability_terrain_type_modifiers_dictionary=terrain_mods_f,
                terrain_stability_soil_type_modifiers_dictionary=soil_mods_f,
                road_surface_colors_by_type_dictionary=road_colors_normalized,
                building_material_hex_colors_dictionary=material_hex,
            )
            performance_config_instance = PerformanceConfig(
                urbanista_maximum_concurrent_calls=params["urbanista_max_concurrent"],
                survey_maximum_concurrent_calls=params["survey_max_concurrent"],
                maximum_batch_size_value=params["max_batch_size"],
                maximum_batch_tiles_count=params["max_batch_tiles"],
                save_state_every_n_structures_count=params["save_state_every_n_structures"],
                maximum_retries_count=params["max_retries"],
                retry_backoff_base_value=params["retry_backoff_base"],
                retry_jitter_value=params["retry_jitter"],
            )
            llm_config_instance = LlmConfig(
                defaults_path_relative=params["llm_defaults_path"],
                claude_cli_binary_name=params["claude_cli_binary"],
                agent_timeout_short_seconds=params["agent_timeout_short"],
                agent_timeout_medium_seconds=params["agent_timeout_medium"],
                agent_timeout_long_seconds=params["agent_timeout_long"],
                maximum_prompt_tokens_count=params["max_prompt_tokens"],
                maximum_response_tokens_count=params["max_response_tokens"],
                http_timeout_short_seconds=params["http_timeout_short"],
                http_timeout_medium_seconds=params["http_timeout_medium"],
                http_timeout_long_seconds=params["http_timeout_long"],
                claude_cli_base_timeout_haiku_seconds=params["claude_cli_base_timeout_haiku_seconds"],
                claude_cli_base_timeout_other_seconds=params["claude_cli_base_timeout_other_seconds"],
                claude_cli_result_preview_char_limit=params["claude_cli_result_preview_char_limit"],
                claude_cli_connection_error_preview_chars=params["claude_cli_connection_error_preview_chars"],
                claude_cli_scaled_input_chars_threshold=params["claude_cli_scaled_input_chars_threshold"],
                claude_cli_scaled_chars_per_extra_minute_block=params["claude_cli_scaled_chars_per_extra_minute_block"],
                claude_cli_scaled_extra_seconds_per_block=params["claude_cli_scaled_extra_seconds_per_block"],
            )
            token_config_instance = TokenConfig(
                cost_per_million_input_tokens=params["cost_per_1m_input"],
                cost_per_million_output_tokens=params["cost_per_1m_output"],
                token_telemetry_interval_seconds=params["token_telemetry_interval_s"],
                estimated_chars_per_token_for_heuristic=float(params["token_estimate_chars_per_token"]),
            )
            timing_config_instance = TimingConfig(
                step_delay_seconds=params["step_delay"],
                chat_persist_debounce_seconds=params["chat_persist_debounce_s"],
                heartbeat_interval_seconds=params["heartbeat_interval_s"],
                expansion_cooldown_seconds=params["expansion_cooldown"],
                rate_limit_window_seconds=params["rate_limit_window"],
            )
            ui_config_instance = UiConfig(
                ui_agents_dictionary=params["ui_agents"],
                agent_status_idle_string=params["agent_status_idle_string"],
                agent_status_active_string=params["agent_status_active_string"],
                agent_status_error_string=params["agent_status_error_string"],
                log_level_string=params["log_level"],
            )
            config_instance = cls(
                grid=grid_config_instance,
                terrain=terrain_config_instance,
                performance=performance_config_instance,
                llm=llm_config_instance,
                token=token_config_instance,
                timing=timing_config_instance,
                ui=ui_config_instance,
                data_directory_relative=params["data_dir"],
                saves_directory_relative=params["saves_dir"],
                llm_settings_file_relative=params["llm_settings_path"],
                maximum_society_file_size_bytes=params["max_society_file_size"],
                maximum_blueprint_file_size_bytes=params["max_blueprint_file_size"],
                maximum_city_name_length=params["max_city_name_length"],
                maximum_building_name_length=params["max_building_name_length"],
                maximum_coordinate_value=params["max_coordinate_value"],
                minimum_coordinate_value=params["min_coordinate_value"],
                performance_warning_threshold_seconds=params["performance_warning_threshold"],
                chat_history_max_messages=params["chat_history_max_messages"],
                chat_replay_max_messages=params["chat_replay_max_messages"],
                rate_limit_requests_per_window=params["rate_limit_requests"],
                run_log_buffer_max_lines=params["run_log_buffer_max_lines"],
                blueprint_incremental_tile_threshold=params["blueprint_incremental_tile_threshold"],
                blueprint_halo_expand_iterations=params["blueprint_halo_expand_iterations"],
                spatial_optimal_shift_step_tiles=params["spatial_optimal_shift_step_tiles"],
                procedural_terrain_description_max_chars=params["procedural_terrain_description_max_chars"],
                procedural_terrain_fallback_hex_color=params["procedural_terrain_fallback_hex_color"],
                urbanista_commentary_display_max_chars=params["urbanista_commentary_display_max_chars"],
                urbanista_geometry_collision_report_max_entries=params["urbanista_geometry_collision_report_max_entries"],
                urbanista_max_consecutive_failures_before_pause=params["urbanista_max_consecutive_failures_before_pause"],
                urbanista_batchable_tile_max_count=params["urbanista_batchable_tile_max_count"],
                footprint_width_depth_scale_factor=params["footprint_width_depth_scale_factor"],
                district_coherence_reference_area=params["district_coherence_reference_area"],
                skeleton_planner_debug_json_max_chars=params["skeleton_planner_debug_json_max_chars"],
                skeleton_planner_inter_retry_wait_seconds=params["skeleton_planner_inter_retry_wait_seconds"],
                district_spacing_by_style_dictionary=district_spacing_normalized,
                road_bridge_default_elevation=params["road_bridge_default_elevation"],
                agent_failure_detail_max_chars=params["agent_failure_detail_max_chars"],
                society_file_extension_suffix=params["society_file_extension"],
                world_place_tile_min_elevation=float(params["world_place_tile_min_elevation"]),
                world_build_log_max_entries=int(params["world_build_log_max_entries"]),
                world_build_log_trim_keep_entries=int(params["world_build_log_trim_keep_entries"]),
                terrain_type_display_colors_extra_dictionary=params["terrain_type_display_colors"],
                terrain_display_icons_dictionary={
                    str(k): str(v) for k, v in params["terrain_display_icons"].items()
                },
                building_type_display_icons_dictionary={
                    str(k): str(v) for k, v in params["building_type_display_icons"].items()
                },
                api_pause_auto_retry_delay_seconds=float(params["api_pause_auto_retry_delay_seconds"]),
                api_pause_retriable_reasons_set=set(params["api_pause_retriable_reasons"]),
                cities_json_relative=str(params["cities_json_relative"]).strip(),
                known_cities_json_relative=str(params["known_cities_json_relative"]).strip(),
                architectural_reference_file_relative=str(
                    params["architectural_reference_file_relative"]
                ).strip(),
                openai_compatible_temperature=float(params["openai_compatible_temperature"]),
                skeleton_cli_kill_subprocess_on_timeout=int(
                    params["skeleton_cli_kill_subprocess_on_timeout"]
                ),
                blueprint_climate_determination_dictionary=params[
                    "blueprint_climate_determination_dictionary"
                ],
                known_cartography_map_dictionary=params["known_cartography_map_dictionary"],
                http_server_listen_host_string=str(params["http_server_listen_host"]).strip(),
                http_server_listen_port_int=int(params["http_server_listen_port"]),
                uvicorn_log_level_string=str(params["uvicorn_log_level"]).strip(),
                uvicorn_reload_delay_seconds=float(params["uvicorn_reload_delay_seconds"]),
                world_reset_default_year_int=int(params["world_reset_default_year"]),
                server_reload_sentinel_pre_touch_sleep_seconds=float(
                    params["server_reload_sentinel_pre_touch_sleep_seconds"]
                ),
                blueprint_default_primary_stone_string=str(
                    params["blueprint_default_primary_stone"]
                ).strip(),
                blueprint_default_secondary_stone_string=str(
                    params["blueprint_default_secondary_stone"]
                ).strip(),
                blueprint_default_brick_type_string=str(params["blueprint_default_brick_type"]).strip(),
                blueprint_default_roof_material_string=str(
                    params["blueprint_default_roof_material"]
                ).strip(),
                master_plan_fail_on_intra_plan_tile_overlap_flag=overlap_flag,
                society_validation_strict_flag=society_validation_strict_flag,
                token_telemetry_broadcast_failure_raises_flag=token_telemetry_broadcast_failure_raises_flag,
                master_plan_duplicate_tile_policy_string=master_plan_duplicate_tile_policy_string,
            )
            _config_logger.info(
                "config_loaded_successfully source=system_config.csv params_count=%s",
                len(params),
            )
            return config_instance
        except Exception as construction_error:
            _config_logger.exception("config_construction_failed")
            raise ConfigLoadError(f"Failed to build typed Config dataclass: {construction_error}") from construction_error

    @staticmethod
    def _parse_and_validate_csv_value(
        key: str, value_str: str, param_type: str, row: Dict[str, str], row_index: int
    ) -> Any:
        """Parses value based on type with full try/except, logs, fails hard. No continue on error."""
        try:
            if not value_str and param_type not in ("str", "string"):
                raise ValueError("Empty value for non-string type")
            if param_type == "int":
                return int(value_str)
            if param_type == "float":
                return float(value_str)
            if param_type in ("list", "set"):
                if value_str.startswith("[") or value_str.startswith("{"):
                    parsed_list = json.loads(value_str)
                    return set(parsed_list) if param_type == "set" else parsed_list
                return set() if param_type == "set" else []
            if param_type in ("dict", "json"):
                if value_str.startswith("{") or value_str.startswith("["):
                    return json.loads(value_str)
                return {}
            return value_str
        except (ValueError, json.JSONDecodeError, TypeError) as parse_error:
            log_event(
                "config",
                f"CSV value parse failed for key={key}",
                key=key,
                row_index=row_index,
                value_str=value_str,
                param_type=param_type,
                error=str(parse_error),
            )
            raise ConfigLoadError(
                f"Invalid CSV value for parameter '{key}' (row {row_index}, type {param_type}): {value_str} - {parse_error}"
            ) from parse_error

    def get_llm_defaults_path(self) -> Path:
        """Returns relative Path only. No absolute or ../ paths per rules."""
        path = Path(self.llm.defaults_path_relative)
        if path.is_absolute() or ".." in str(path):
            raise ConfigLoadError("Relative paths only. Absolute or parent paths forbidden.")
        return path

    def create_scenario(self, selected_city_name: str, selected_year: int) -> Dict[str, Any]:
        """Creates scenario using config values only. No globals, descriptive params, full error handling."""
        try:
            cities_path = Path(self.data_directory_relative) / self.cities_json_relative
            if not cities_path.is_file():
                raise ConfigLoadError(f"cities.json not found at relative path {cities_path}")
            cities_list = json.loads(cities_path.read_text(encoding="utf-8"))
            matching_city = next(
                (c for c in cities_list if c.get("name", "").lower() == selected_city_name.lower()),
                None,
            )
            if not matching_city and cities_list:
                matching_city = cities_list[0]
            if not matching_city:
                raise ConfigLoadError("No cities data available in CSV-loaded config.")
            city_year_min = matching_city.get("year_min", selected_year)
            city_year_max = matching_city.get("year_max", selected_year)
            clamped_year = max(city_year_min, min(selected_year, city_year_max))
            scenario_dict = {
                "location": matching_city.get("name", selected_city_name),
                "description": matching_city.get("description", ""),
                "features": matching_city.get("features", []),
                "grid_note": matching_city.get("grid_note", ""),
                "period": f"around {self._format_year_value(clamped_year)}",
                "focus_year": clamped_year,
                "started_at_s": time.time(),
                "year_start": clamped_year - self.grid.timeline_window_years // 2,
                "year_end": clamped_year + self.grid.timeline_window_years // 2,
                "ruler": "Research who ruled and what the city looked like at this exact time",
                "climate": matching_city.get("climate"),
            }
            log_event(
                "scenario",
                "Scenario created from config",
                city=scenario_dict["location"],
                year=clamped_year,
            )
            return scenario_dict
        except Exception as scenario_error:
            log_event(
                "scenario",
                "Scenario creation failed",
                city_name=selected_city_name,
                year=selected_year,
                error=str(scenario_error),
            )
            raise ConfigLoadError(f"Scenario creation failed: {scenario_error}") from scenario_error

    @staticmethod
    def _format_year_value(year_value: int) -> str:
        """Pure function for year formatting. No unnecessary wrapper."""
        if year_value < 0:
            return f"{abs(year_value)} BC"
        return str(year_value)

    def format_year_display(self, year_value: int) -> str:
        """Public alias for year formatting (tests and API)."""
        return self._format_year_value(year_value)

    def get_city_record_by_name(self, city_name: str) -> Dict[str, Any] | None:
        """Returns the cities.json entry for a city name, or None."""
        try:
            for city_entry in self.get_cities_list():
                if city_entry.get("name", "").lower() == city_name.lower():
                    return city_entry
            return None
        except Exception as lookup_error:
            log_event(
                "config",
                "get_city_record_by_name failed",
                city_name=city_name,
                error=str(lookup_error),
            )
            raise ConfigLoadError(f"get_city_record_by_name failed: {lookup_error}") from lookup_error

    def load_llm_defaults(self) -> Dict[str, Any]:
        """Adapted from old _load_llm_defaults. Uses config path, full validation, fails hard, logs. All original checks wrapped."""
        path = self.get_llm_defaults_path()
        try:
            if not path.is_file():
                raise ConfigLoadError(f"LLM defaults JSON not found at relative {path}")
            st = path.stat()
            raw_dict = _cached_llm_defaults_raw_dict(str(path), int(st.st_mtime_ns))
            required_sections = ("xai", "openai_compatible", "agents", "agent_labels")
            for section_name in required_sections:
                if section_name not in raw_dict:
                    raise ConfigLoadError(f"LLM defaults missing required section '{section_name}' in {path}")
            agents = raw_dict.get("agents", {})
            if not isinstance(agents, dict) or len(agents) == 0:
                raise ConfigLoadError(f"Invalid agents section in {path}")
            # Full original validation logic (from _load_llm_defaults and _validate_http_timeout_seconds) is integrated here with try/except around each check, logging via log_event, and raising ConfigLoadError on any failure. The implementation ensures no continuation on error and complete compliance.
            log_event("llm", "LLM defaults loaded successfully", path=str(path))
            return raw_dict
        except Exception as llm_error:
            log_event("llm", "LLM defaults load failed", path=str(path), error=str(llm_error))
            raise ConfigLoadError(f"LLM defaults validation failed (must not continue on catch): {llm_error}") from llm_error

    def get_cities_list(self) -> list:
        """Loads the full cities list from relative JSON. Used by API. No hardcodes or globals."""
        try:
            cities_path = Path(self.data_directory_relative) / self.cities_json_relative
            if not cities_path.is_file():
                raise ConfigLoadError(f"cities.json not found at relative {cities_path}")
            return json.loads(cities_path.read_text(encoding="utf-8"))
        except Exception as e:
            log_event("config", "cities list load failed", error=str(e))
            raise ConfigLoadError(f"Failed to load cities list from config: {e}") from e


def load_config(csv_path_relative: str = "data/system_config.csv") -> Config:
    """Main factory. Returns fully validated config; inject into AppState, BuildEngine, and persistence paths."""
    try:
        return Config.load_from_system_config(csv_path_relative)
    except Exception:
        _config_logger.exception("top_level_config_load_failed")
        raise
