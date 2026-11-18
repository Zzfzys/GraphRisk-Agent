from copy import deepcopy
import json
import pytest
from test_controlled_recovery import recovery,reserve,settle
from graphrisk.agent.controlled_recovery import register,preview,unknown_record
from graphrisk.agent.autonomous_budget import InvestigationBudget,ExecutionStop
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash


@pytest.fixture
def successor(recovery,tmp_path):
    budget,store,approval,sha=recovery
    register(budget,approval,'new',sha)
    for n in range(2):settle(budget,reserve(budget,store,n))
    directory=tmp_path/'parent';directory.mkdir()
    for cell in ('A1','A2'):
        result=directory/(cell+'-result.json');result.write_text(canonical({'cell':cell,'quality':'Fail'}),encoding='utf-8')
        (directory/(cell+'-review.json')).write_text(canonical({'completed':True,'quality':'Fail','result_sha256':file_hash(result)}),encoding='utf-8')
    stop=directory/'STOP.json';stop.write_text(canonical({'reason':'two_consecutive_completed_acceptance_failures','cells':['A1','A2'],'new_requests':2}),encoding='utf-8')
    new=deepcopy(approval);new.update(version='controlled-recovery-v2',authorization='User approved exactly one new ten-call batch after wire-format fix.',parent={'campaign_id':'new','manifest_sha':sha,'approval_sha':digest(approval),'stop_path':str(stop),'files':{p.name:file_hash(p) for p in directory.iterdir()}})
    newsha=digest('new-wire-contract');store.campaign('successor',newsha,cap_each=10,group_key='route5_supplement_v3')
    return budget,store,new,newsha


def test_successor_preserves_original_grant_unknown_and_hard_ten_call_cap(successor):
    budget,store,approval,sha=successor
    with budget.transaction() as db:original=db.execute('SELECT * FROM controlled_recovery').fetchall()
    preview(budget,approval);register(budget,approval,'successor',sha)
    reopened=InvestigationBudget(budget.path,budget.contract,mode='fixture');store=SessionStore(reopened)
    register(reopened,approval,'successor',sha)
    for n in range(10):settle(reopened,reserve(reopened,store,n,campaign='successor',key=f'second-{n}'))
    with pytest.raises(ExecutionStop,match='recovery_ten_request_cap'):reserve(reopened,store,10,campaign='successor')
    with reopened.transaction() as db:
        assert db.execute('SELECT * FROM controlled_recovery').fetchall()==original
        assert unknown_record(db,approval['unknown']['attempt_id'])==approval['unknown']
    assert reopened.snapshot()['total_attempts']==13 and reopened.snapshot()['accounted_micro_cny']==2103296+1200


def test_parent_and_other_campaign_cannot_borrow_remaining_calls(successor):
    budget,store,approval,sha=successor;register(budget,approval,'successor',sha)
    with pytest.raises(ExecutionStop,match='recovery_parent_closed'):reserve(budget,store,2)
    store.campaign('unapproved',digest('other'),cap_each=10,group_key='route5_supplement_v3')
    with pytest.raises(ExecutionStop,match='unresolved_attempt'):reserve(budget,store,0,campaign='unapproved')


@pytest.mark.parametrize('change',['stop','review','result','parent_sha','third_campaign','unknown','new_unknown','integrity'])
def test_successor_rejects_changed_closure_or_budget_state(successor,change):
    budget,store,approval,sha=successor;register(budget,approval,'successor',sha)
    parent=approval['parent'];directory=__import__('pathlib').Path(parent['stop_path']).parent
    if change in ('stop','review','result'):
        path=directory/({'stop':'STOP.json','review':'A2-review.json','result':'A1-result.json'}[change])
        path.write_text('{}',encoding='utf-8')
    elif change=='parent_sha':
        approval=deepcopy(approval);approval['parent']['manifest_sha']='f'*64
        with pytest.raises(ExecutionStop):register(budget,approval,'successor',sha)
        return
    elif change=='third_campaign':
        store.campaign('third',digest('third'),cap_each=10,group_key='route5_supplement_v3')
        with pytest.raises(ExecutionStop,match='successor_registration_changed'):register(budget,approval,'third',digest('third'))
        return
    elif change=='unknown':
        with budget.transaction() as db:db.execute('UPDATE execution_requests SET accounted=0 WHERE attempt_id=?',(approval['unknown']['attempt_id'],))
    elif change=='new_unknown':
        attempt=reserve(budget,store,0,campaign='successor',key='successor-0');budget.unknown(attempt,'timeout')
    else:
        attempt=reserve(budget,store,0,campaign='successor',key='successor-0')
        with pytest.raises(ExecutionStop):budget.settle(attempt,cost_micro=20000,receipt_sha='f'*64,receipt_path='fixture',identity='fixture',usage={},result_code='bad')
    with pytest.raises(ExecutionStop):reserve(budget,store,1,campaign='successor',key='successor-1')


def test_successor_requires_explicit_new_authorization_and_does_not_expand_to_chain(successor):
    budget,store,approval,sha=successor
    old=deepcopy(approval);old.pop('parent');old['version']='controlled-recovery-v1'
    with pytest.raises(ExecutionStop,match='registration_changed'):register(budget,old,'successor',sha)
    register(budget,approval,'successor',sha)
    chained=deepcopy(approval);chained['parent']['campaign_id']='successor';chained['parent']['manifest_sha']=sha;chained['parent']['approval_sha']=digest(approval)
    with pytest.raises(ExecutionStop):preview(budget,chained)


def test_unclosed_parent_is_not_eligible(successor):
    budget,store,approval,sha=successor
    p=__import__('pathlib').Path(approval['parent']['stop_path'])
    p.write_text(canonical({'reason':'running','cells':[],'new_requests':2}),encoding='utf-8')
    approval['parent']['files']['STOP.json']=file_hash(p)
    with pytest.raises(ExecutionStop):register(budget,approval,'successor',sha)
