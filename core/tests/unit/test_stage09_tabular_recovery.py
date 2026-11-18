from pathlib import Path

import pytest

from graphrisk.models.enhanced_execution import recovery_attempt_name, training_awake, validate_host_pause


def test_runtime_repair_uses_new_failure_attempt_without_renaming_parent():
    report=Path('original-run')
    parent={'name':'original-run-x1','state':'failed','family':'xgboost','lane':'search'}
    assert recovery_attempt_name({'id':'x1'},report,[parent],{'allowed_failed_task':'x1'})==('original-run-x1-retry-1','original-run-x1')
    assert recovery_attempt_name({'id':'x1'},report,[parent],None)==('original-run-x1',None)


def test_repair_cannot_be_used_for_failed_different_candidate_or_control():
    parent={'name':'original-run-x2','state':'failed','family':'xgboost','lane':'search'}
    with pytest.raises(ValueError,match='only registered'):
        recovery_attempt_name({'id':'x2'},Path('original-run'),[parent],{'repair':True})


def test_completed_tasks_keep_the_original_attempt_and_are_not_retried():
    parent={'name':'original-run-control-core','state':'completed','family':'xgboost','lane':'control'}
    assert recovery_attempt_name({'id':'control-core'},Path('original-run'),[parent],{'repair':True})==('original-run-control-core',None)


def test_sleep_recovery_is_bound_to_failed_x3_and_does_not_reset_search():
    parent={'name':'original-run-x3','state':'failed','family':'xgboost','lane':'search'}
    assert recovery_attempt_name({'id':'x3'},Path('original-run'),[parent],{'allowed_failed_task':'x3'})==('original-run-x3-retry-1','original-run-x3')
    with pytest.raises(ValueError,match='bound'):
        recovery_attempt_name({'id':'x3'},Path('original-run'),[parent],{'allowed_failed_task':'x1'})


def test_host_pause_must_explain_the_same_deadline_and_have_no_saved_model():
    parent={'name':'run-x3','state':'failed','family':'xgboost','lane':'search','registration':{'candidate_id':'x3'},'cap_seconds':3600,'at':'2026-10-09T13:43:00+08:00'}
    failure={'error_type':'TimeoutError','attempt':'run-x3','seconds':4800}
    evidence={'provider':'Microsoft-Windows-Power-Troubleshooter','event_id':1,'sleep_time_utc':'2026-10-09T05:44:00Z','wake_time_utc':'2026-10-09T07:03:00Z','sleep_seconds':4740}
    validate_host_pause(failure,evidence,parent,False)
    with pytest.raises(ValueError,match='saved model'):
        validate_host_pause(failure,evidence,parent,True)
    with pytest.raises(ValueError,match='unrelated'):
        validate_host_pause(failure,{**evidence,'wake_time_utc':'2026-10-09T06:45:00Z'},parent,False)
    with pytest.raises(ValueError,match='bound deadline'):
        validate_host_pause({**failure,'attempt':'different-run'},evidence,parent,False)


@pytest.mark.parametrize('raise_inside',[False,True])
def test_process_wake_request_always_released_without_display_or_power_plan_changes(raise_inside):
    calls=[]
    def api(flags):
        calls.append(flags)
        return 1
    try:
        with training_awake(api):
            if raise_inside:
                raise RuntimeError('training failure')
    except RuntimeError:
        pass
    assert calls==[0x80000001,0x80000000]


def test_failed_wake_request_stops_before_training():
    with pytest.raises(OSError,match='wake lock'):
        with training_awake(lambda flags:0):
            pytest.fail('must stop before any fit')
