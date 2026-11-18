"""Small metadata only: no model loader, provider, labels or live-budget mutation."""
from copy import deepcopy
import pytest
import preflight_route9 as route9


def registration():
    rows=[{'transaction_id':str(i),'case_alias':f'{i:032x}','group':
           'development' if i<15 else 'natural' if i<45 else 'diagnostic',
           'event_second':86400*(i+1)+1,'selection_cutoff':0,
           'split':'validation' if i<40 else 'test'} for i in range(75)]
    return {'cases':rows,'repeat_ids':[str(i) for i in range(20,25)],
        'human_read_ids':[str(i) for i in range(25,30)],'human_read_provider':'deepseek',
        'human_read_version':'first_published_report_or_original_failure'}


def test_preserves_60_and_original_groups_repeats_and_reading():
    value=registration();before=deepcopy(value)
    rows=route9.cases_for(value,selection_cutoff=0)
    assert value==before and len(rows)==60
    assert {r['transaction_id'] for r in rows}=={str(i) for i in range(15,75)}
    assert sum(r['repeat_selected'] for r in rows)==sum(r['human_read_selected'] for r in rows)==5
    assert all(r['formal_execution']=='NotRun' and r['first_report_quality']=='Pending' for r in rows)


def test_current_daily_cutoff_keeps_preblocked_cases_and_equal_time():
    value=registration();cutoff=20*86400
    rows=route9.cases_for(value,selection_cutoff=cutoff)
    blocked=[r for r in rows if not r['daily_model_policy_eligible']]
    assert len(rows)==60 and len(blocked)==5
    assert all(r['relative_day']*86400<=cutoff for r in blocked)
    assert all(r['preflight_block_reason']=='day_not_after_current_policy_selection' for r in blocked)
    assert sum(r['case_alias']==f'{19:032x}' for r in blocked)==1


@pytest.mark.parametrize('bad', ['duplicate','replace_group','repeat_outside','duplicate_read','reading_source','invalid_time','label_alias'])
def test_original_denominators_and_binding_fail_closed(bad):
    v=registration()
    if bad=='duplicate': v['cases'][-1]['transaction_id']=v['cases'][15]['transaction_id']
    if bad=='replace_group': v['cases'][-1]['group']='development'
    if bad=='repeat_outside': v['repeat_ids'][0]='0'
    if bad=='duplicate_read': v['human_read_ids'][1]=v['human_read_ids'][0]
    if bad=='reading_source': v['human_read_version']='best_successful_report'
    if bad=='invalid_time': v['cases'][15]['event_second']=0
    if bad=='label_alias': v['cases'][15]['case_alias']='fraud-known'
    with pytest.raises(ValueError,match='route9_'):route9.cases_for(v,selection_cutoff=0)


def test_full_two_provider_budget_is_a_proposal_and_not_new_quota():
    x=route9.budget_plan({'deepseek':1446,'local':23},5)
    assert x['proposed_max_new_requests']==650 and x['proposed_new_per_provider']==325
    assert x['proposed_absolute_total_cap']==2119 and x['increase_over_current_planning_envelope']==619
    assert x['remaining_in_planning_envelope']==31 and x['currently_authorized_route9_requests']==0
    assert x['planned_episodes']==130 and x['hard_limit_cny']==100 and x['pause_estimate_cny']==80
    assert not x['new_request_allowance_approved'] and x['not_an_execution_authorization']
    with pytest.raises(ValueError):route9.budget_plan({'deepseek':1446,'local':23},6)


def test_existing_output_refuses_before_reading_any_source(tmp_path,monkeypatch):
    def blocked(*a):raise AssertionError('no source or ledger should be read')
    monkeypatch.setattr(route9,'read',blocked)
    with pytest.raises(ValueError,match='new_output_directory'):
        route9.prepare(tmp_path,tmp_path,tmp_path,available_ram_bytes=0)


def test_fresh_output_outside_route9_is_rejected_without_loading_inputs(tmp_path,monkeypatch):
    monkeypatch.setattr(route9,'read',lambda *a:pytest.fail('no input reads'))
    with pytest.raises(ValueError,match='output_must_stay_in_route9'):
        route9.prepare(tmp_path,tmp_path,tmp_path/'outside',available_ram_bytes=0)
