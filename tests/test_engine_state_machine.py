"""Checkpoint FSM for build orchestration."""

from orchestration.engine_state_machine import EngineBuildPipelineState, EngineStateMachine


def test_checkpoint_roundtrip():
    sm = EngineStateMachine()
    assert sm.transition(EngineBuildPipelineState.awaiting_survey, from_states=None) is True
    sm.record_district_wave_success(0, "Forum")
    d = sm.to_checkpoint_dict()
    assert d["last_successful_district_name"] == "Forum"
    assert d["last_completed_district_index"] == 0


def test_reconcile_notes_out_of_range():
    sm = EngineStateMachine()
    notes = sm.reconcile_loaded_cursors(
        district_index=99,
        district_build_cursor=5,
        districts_len=3,
    )
    assert notes


def test_reconcile_notes_cursor_trails_index():
    sm = EngineStateMachine()
    notes = sm.reconcile_loaded_cursors(
        district_index=3,
        district_build_cursor=1,
        districts_len=5,
    )
    assert any("trails district_index" in n for n in notes)


def test_wave_complete_transition_guarded():
    sm = EngineStateMachine()
    assert sm.transition(EngineBuildPipelineState.building_district_wave, from_states=None) is True
    assert sm.transition(
        EngineBuildPipelineState.wave_complete,
        from_states=(EngineBuildPipelineState.building_district_wave,),
    ) is True
    assert sm.pipeline_state == EngineBuildPipelineState.wave_complete


def test_wave_complete_transition_rejects_idle():
    sm = EngineStateMachine()
    assert sm.transition(
        EngineBuildPipelineState.wave_complete,
        from_states=(EngineBuildPipelineState.building_district_wave,),
    ) is False
    assert sm.pipeline_state == EngineBuildPipelineState.idle
