from copy import deepcopy
import sqlite3
import pytest
from test_autonomous_action_providers import ledger
from graphrisk.agent.autonomous_budget import InvestigationBudget,ExecutionStop
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.controlled_recovery import unknown_record,preview,register
from graphrisk.agent.upgrade_contract import digest


@pytest.fixture
def recovery(tmp_path):
    budget=ledger(tmp_path,{'test':'quarantine'},cap=100)
    old=budget.reserve(provider='deepseek',request_key='old-request',packet_sha='a'*64,phase='pilot',reserve_micro=2103296)
    budget.unknown(old,'provider_http_rejected')
    with budget.transaction() as db:record=unknown_record(db,old)
    approval={'version':'controlled-recovery-v1','unknown':record,'max_requests':10,
              'authorization':'User approved retaining unknown/reserve and one ten-call batch.',
              'purpose':'controlled_judgment_repairs'}
    store=SessionStore(budget);sha=digest({'frozen':'new'})
    store.campaign('new',sha,cap_each=10,group_key='route5_supplement_v3')
    return budget,store,approval,sha


def reserve(budget,store,n,*,campaign='new',key=None,payload=None,provider='deepseek'):
    session=campaign+'-'+str(n)
    store.open(session,campaign,{'n':n},provider,calls=1,seconds=240)
    phase=store.register_packet(session,0,digest({'n':n,'campaign':campaign}),provider)
    return budget.reserve(provider=provider,request_key=key or 'new-'+str(n),packet_sha=payload or digest(n),phase=phase,reserve_micro=0 if provider=='local' else 10000)


def settle(budget,attempt):
    budget.settle(attempt,cost_micro=100,receipt_sha='f'*64,receipt_path='fixture',identity='fixture',usage={'total_tokens':1},result_code='ok')


def test_default_and_unregistered_campaign_remain_blocked(recovery):
    budget,store,approval,sha=recovery
    with pytest.raises(ExecutionStop,match='unresolved_attempt_blocks_execution'):reserve(budget,store,0)
    preview(budget,approval);register(budget,approval,'new',sha)
    store.campaign('other',digest('other'),cap_each=10,group_key='route5_supplement_v3')
    with pytest.raises(ExecutionStop,match='unresolved_attempt_blocks_execution'):reserve(budget,store,0,campaign='other')
    with pytest.raises(ExecutionStop,match='unresolved_attempt_blocks_execution'):
        budget.reserve(provider='deepseek',request_key='production',packet_sha=digest('production'),phase='pilot',reserve_micro=1000)
    assert budget.snapshot()['total_attempts']==1


def test_reopen_retains_quarantine_reserve_counts_and_hard_ten_cap(recovery):
    budget,store,approval,sha=recovery
    register(budget,approval,'new',sha);register(budget,approval,'new',sha)
    reopened=InvestigationBudget(budget.path,budget.contract,mode='fixture');store=SessionStore(reopened)
    for n in range(10):settle(reopened,reserve(reopened,store,n))
    with pytest.raises(ExecutionStop,match='recovery_ten_request_cap'):reserve(reopened,store,10)
    with reopened.transaction() as db:assert unknown_record(db,approval['unknown']['attempt_id'])==approval['unknown']
    snapshot=reopened.snapshot()
    assert snapshot['total_attempts']==11 and snapshot['unresolved_attempts']==1
    assert snapshot['accounted_micro_cny']==2103296+10*100


@pytest.mark.parametrize('change',['payload','request_key','local'])
def test_old_request_and_local_are_never_permitted(recovery,change):
    budget,store,approval,sha=recovery;register(budget,approval,'new',sha)
    kw={'payload':approval['unknown']['packet_sha']} if change=='payload' else {'key':'old-request'} if change=='request_key' else {'provider':'local'}
    with pytest.raises(ExecutionStop):reserve(budget,store,0,**kw)
    assert budget.snapshot()['total_attempts']==1


@pytest.mark.parametrize('state',['reserved','unknown'])
def test_any_new_unresolved_stops_even_approved_batch(recovery,state):
    budget,store,approval,sha=recovery;register(budget,approval,'new',sha)
    attempt=reserve(budget,store,0)
    if state=='unknown':budget.unknown(attempt,'timeout')
    with pytest.raises(ExecutionStop,match='additional_unknown_blocks_recovery'):reserve(budget,store,1)
    assert budget.snapshot()['total_attempts']==2


def test_new_identity_integrity_stop_is_not_waived(recovery):
    budget,store,approval,sha=recovery;register(budget,approval,'new',sha)
    attempt=reserve(budget,store,0)
    with pytest.raises(ExecutionStop):
        budget.settle(attempt,cost_micro=100,receipt_sha='f'*64,receipt_path='fixture',identity='changed',usage={},result_code='bad',integrity_stop=True)
    with pytest.raises(ExecutionStop,match='integrity_review_required'):reserve(budget,store,1)


@pytest.mark.parametrize('change',['release_reserve','fake_zero_bill','new_manifest','second_campaign','approval_change','more_calls'])
def test_immutable_single_batch_authorization(recovery,change):
    budget,store,approval,sha=recovery;register(budget,approval,'new',sha)
    other=deepcopy(approval)
    if change=='release_reserve':
        with budget.transaction() as db:db.execute('UPDATE execution_requests SET accounted=0 WHERE attempt_id=?',(approval['unknown']['attempt_id'],))
        with pytest.raises(ExecutionStop,match='record_changed'):reserve(budget,store,0)
    elif change=='fake_zero_bill':
        budget.reconcile_bill(approval['unknown']['attempt_id'],0,'b'*64)
        with pytest.raises(ExecutionStop,match='record_changed'):reserve(budget,store,0)
    elif change=='new_manifest':
        with pytest.raises(ExecutionStop):register(budget,approval,'new',digest('changed'))
    elif change=='second_campaign':
        store.campaign('other',digest('other'),cap_each=10,group_key='route5_supplement_v3')
        with pytest.raises(ExecutionStop,match='registration_changed'):register(budget,approval,'other',digest('other'))
    elif change=='approval_change':
        other['authorization']='different'
        with pytest.raises(ExecutionStop,match='registration_changed'):register(budget,other,'new',sha)
    else:
        other['max_requests']=11
        with pytest.raises(ExecutionStop,match='invalid_controlled'):register(budget,other,'new',sha)


@pytest.mark.parametrize('guard',['money','group'])
def test_original_money_and_supplement_caps_still_apply(recovery,guard):
    budget,store,approval,sha=recovery;register(budget,approval,'new',sha)
    if guard=='money':
        with budget.transaction() as db:
            db.execute("INSERT INTO model_attempts VALUES('previous','deepseek','settled',80000000)")
            db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES('previous','previous','pilot','previous',79000000)")
        with pytest.raises(ExecutionStop,match='pause_for_budget'):reserve(budget,store,0)
    else:
        # Counts are retained across other campaigns; quarantine doesn't allocate a new group.
        store.campaign('earlier',digest('earlier'),cap_each=100,group_key='route5_supplement_v3')
        with budget.transaction() as db:
            for n in range(100):
                db.execute('INSERT INTO autonomous_sessions VALUES(?,?,?,?,?,?,NULL,NULL)',(f'prior-{n}','earlier',digest(n),'deepseek',1,9999999999))
                db.execute('INSERT INTO autonomous_phases VALUES(?,?,0)',(f'prior-phase-{n}',f'prior-{n}'))
                db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(f'prior-attempt-{n}','deepseek','settled',1))
                db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',(f'prior-attempt-{n}',f'prior-key-{n}',f'prior-phase-{n}',digest(n),1))
        with pytest.raises(ExecutionStop,match='supplement_100_request_cap'):reserve(budget,store,0)
