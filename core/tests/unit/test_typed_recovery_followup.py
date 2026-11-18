from copy import deepcopy
from pathlib import Path
import pytest
from test_controlled_recovery import recovery,reserve,settle
from test_recovery_successor import successor
from graphrisk.agent.controlled_recovery import register,preview,unknown_record
from graphrisk.agent.autonomous_budget import InvestigationBudget,ExecutionStop
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


@pytest.fixture
def followup(successor,tmp_path):
    budget,store,approval,sha=successor;register(budget,approval,'successor',sha)
    cells=['A1','A2','B1','B2','C1'];directory=tmp_path/'second-parent';directory.mkdir()
    for n,cell in enumerate(cells):
        settle(budget,reserve(budget,store,n,campaign='successor',key='parent2-'+str(n)))
        p=directory/(cell+'-result.json');p.write_text(canonical({'cell':cell}),encoding='utf-8')
        (directory/(cell+'-review.json')).write_text(canonical({'completed':True,'quality':'Pass' if n<3 else 'Fail','result_sha256':file_hash(p)}),encoding='utf-8')
    stop=directory/'STOP.json';stop.write_text(canonical({'reason':'two_consecutive_completed_acceptance_failures','cells':cells,'new_requests':5}),encoding='utf-8')
    new=deepcopy(approval);new.update(version='controlled-recovery-v3',authorization='User explicitly approved one new typed-support ten-request batch.',parent={'campaign_id':'successor','manifest_sha':sha,'approval_sha':digest(approval),'stop_path':str(stop),'files':{p.name:file_hash(p) for p in directory.iterdir()}})
    newsha=digest('typed-followup');store.campaign('typed',newsha,cap_each=10,group_key='route5_supplement_v3')
    return budget,store,new,newsha


def test_approved_typed_batch_retains_all_grants_unknown_and_ten_cap(followup):
    budget,store,approval,sha=followup
    with budget.transaction() as db:before=[db.execute('SELECT * FROM '+t).fetchall() for t in ('controlled_recovery','controlled_recovery_successors')]
    preview(budget,approval);register(budget,approval,'typed',sha)
    reopened=InvestigationBudget(budget.path,budget.contract,mode='fixture');store=SessionStore(reopened)
    register(reopened,approval,'typed',sha)
    for n in range(10):settle(reopened,reserve(reopened,store,n,campaign='typed',key='typed-'+str(n)))
    with pytest.raises(ExecutionStop,match='recovery_ten_request_cap'):reserve(reopened,store,10,campaign='typed')
    with reopened.transaction() as db:
        assert [db.execute('SELECT * FROM '+t).fetchall() for t in ('controlled_recovery','controlled_recovery_successors')]==before
        assert unknown_record(db,approval['unknown']['attempt_id'])==approval['unknown']
    assert reopened.snapshot()['total_attempts']==18


def test_previous_batch_cannot_borrow_remaining_five_calls(followup):
    budget,store,approval,sha=followup;register(budget,approval,'typed',sha)
    with pytest.raises(ExecutionStop,match='recovery_parent_closed'):reserve(budget,store,5,campaign='successor')


@pytest.mark.parametrize('what',['parent_stop','ancestor_stop','new_unknown'])
def test_closure_and_new_unknown_remain_hard_stops(followup,what):
    budget,store,approval,sha=followup;register(budget,approval,'typed',sha)
    if what=='parent_stop':Path(approval['parent']['stop_path']).write_text('{}',encoding='utf-8')
    elif what=='ancestor_stop':
        with budget.transaction() as db:
            import json
            old=json.loads(db.execute('SELECT approval_json FROM controlled_recovery_successors WHERE campaign_id=?',('successor',)).fetchone()[0])
        Path(old['parent']['stop_path']).write_text('{}',encoding='utf-8')
    else:
        attempt=reserve(budget,store,0,campaign='typed',key='typed-0');budget.unknown(attempt,'timeout')
    with pytest.raises(ExecutionStop):reserve(budget,store,1,campaign='typed',key='typed-1')


def test_no_implicit_or_duplicate_followup_authorization(followup):
    budget,store,approval,sha=followup
    old=deepcopy(approval);old['version']='controlled-recovery-v2'
    with pytest.raises(ExecutionStop):register(budget,old,'typed',sha)
    register(budget,approval,'typed',sha)
    store.campaign('unapproved',digest('other'),cap_each=10,group_key='route5_supplement_v3')
    with pytest.raises(ExecutionStop):register(budget,approval,'unapproved',digest('other'))


def test_version_four_does_not_automatically_open_another_batch(followup):
    budget,store,approval,sha=followup;approval=deepcopy(approval);approval['version']='controlled-recovery-v4'
    with pytest.raises(ExecutionStop,match='invalid_controlled'):preview(budget,approval)
