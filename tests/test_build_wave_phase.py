"""Tests for persisted build-wave FSM helpers."""

from orchestration.build_wave_phase import (
    BuildWavePhase,
    coerce_build_wave_phase_string,
    compute_build_generation_progress_percent,
)


def test_coerce_build_wave_phase_string_defaults():
    assert coerce_build_wave_phase_string(None) == BuildWavePhase.landmark
    assert coerce_build_wave_phase_string("") == BuildWavePhase.landmark
    assert coerce_build_wave_phase_string("INFILL") == BuildWavePhase.infill


def test_compute_build_generation_progress_percent_two_waves():
    n = 4
    p0 = compute_build_generation_progress_percent(
        district_build_cursor=0,
        district_index=0,
        build_wave_phase="landmark",
        total_districts=n,
    )
    assert p0 == 0
    p_mid = compute_build_generation_progress_percent(
        district_build_cursor=2,
        district_index=0,
        build_wave_phase="landmark",
        total_districts=n,
    )
    assert p_mid == int(100 * (2 / (2 * n)))
    p_infill = compute_build_generation_progress_percent(
        district_build_cursor=1,
        district_index=0,
        build_wave_phase="infill",
        total_districts=n,
    )
    assert p_infill == int(100 * ((n + 1) / (2 * n)))
    p_done = compute_build_generation_progress_percent(
        district_build_cursor=99,
        district_index=n,
        build_wave_phase="infill",
        total_districts=n,
    )
    assert p_done == 100
