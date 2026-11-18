import json
from pathlib import Path
import pytest
from graphrisk.agent.upgrade_contract import load_contract,register_budget,digest,canonical,file_hash
from graphrisk.agent.autonomous_budget import InvestigationBudget,ExecutionStop
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent import rank_successor as rs

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def ready(tmp_path):
    contract=load_contract(ROOT/'configs/agent_investigation_experiment.json')
    path=tmp_path/'ledger.sqlite3';register_budget(path,contract)
    budget=InvestigationBudget(path,contract,mode='fixture');store=SessionStore(budget)
    old=canonical(['deepseek-flash','old-fp']);new=canonical(['deepseek-flash','new-fp'])
    receipt=tmp_path/'old-receipt.json';receipt.write_text(canonical({'body':{'model':'deepseek-flash','system_fingerprint':'new-fp'}}),encoding='utf-8')
    asset=tmp_path/'packet.json';asset.write_text('payload',encoding='utf-8')
    with budget.transaction() as db:
        db.execute('INSERT INTO provider_identity VALUES(?,?)',('deepseek',old))
        for i,state in enumerate(('unknown','unknown','settled')):
            attempt='prior'+str(i);db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(attempt,'deepseek',state,10))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted,receipt_sha,receipt_path,result_code,identity,usage_json,integrity_stop) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (attempt,'old-key'+str(i),'old-phase','old-packet'+str(i),10,file_hash(receipt) if i==2 else None,str(receipt) if i==2 else None,'ok' if i==2 else 'unknown',new if i==2 else None,None,int(i==2)))
        cfg={'version':'route6-rank-successor-v1','campaign':'ranking-test','max_requests':6,'cap_micro_cny':15000000,'retries':0,
             'endpoint':'https://api.deepseek.com/chat/completions','model':'deepseek-flash','old_identity':old,'expected_identity':new,
             'payloads':[digest({'case':i}) for i in range(6)],'files':{str(asset):file_hash(asset)},
             'historical_unknowns':rs.rows(db,"a.state IN ('reserved','unknown')"),'historical_stops':rs.rows(db,'r.integrity_stop=1')}
    cfgfile=tmp_path/'contract.json';cfgfile.write_text(canonical(cfg),encoding='utf-8')
    store.campaign('ranking-test',file_hash(cfgfile),cap_each=6,group_key=rs.GROUP)
    rs.register(budget,cfgfile,'User authorized six-case pilot')
    return budget,store,cfg,cfgfile,tmp_path

def phase_for(data,position):
    budget,store,cfg,_,_=data;campaign=cfg['campaign']; name=campaign+'-C'+str(position+1)
    sm={'campaign':campaign,'position':position,'payload_sha':cfg['payloads'][position]}
    store.open(name,campaign,sm,'deepseek',calls=1)
    wrapper=digest({'fixture':position});phase=store.register_packet(name,0,wrapper,'deepseek')
    return phase,digest([wrapper,'deepseek','0'])

def reserve(data,position=0,**changes):
    phase,key=phase_for(data,position);kw={'provider':'deepseek','phase':phase,'request_key':key,'packet_sha':data[2]['payloads'][position],'reserve_micro':100}
    kw.update(changes);return data[0].reserve(**kw)

def settle(data,attempt,identity=None):
    p=data[4]/(attempt+'.json');p.write_text('{}',encoding='utf-8');sha=file_hash(p)
    data[0].attach_receipt(attempt,sha,str(p))
    data[0].settle(attempt,cost_micro=1,receipt_sha=sha,receipt_path=str(p),identity=identity or data[2]['expected_identity'],usage={},result_code='ok')
    return p

def test_old_stops_preserved_and_new_identity_is_scoped(ready):
    before=ready[0].snapshot();attempt=reserve(ready);p=settle(ready,attempt)
    rs.record(ready[0],ready[2]['campaign'],0,attempt,p)
    assert ready[0].snapshot()['integrity_stops']==before['integrity_stops']==1
    assert ready[0].snapshot()['unresolved_attempts']==before['unresolved_attempts']==2
    with ready[0].transaction() as db:
        assert db.execute("select identity from provider_identity where provider='deepseek'").fetchone()[0]==ready[2]['old_identity']
    assert reserve(ready,1)

def test_payload_or_order_mismatch_cannot_reserve(ready):
    with pytest.raises(ExecutionStop,match='exact_payload'):reserve(ready,0,packet_sha='f'*64)
    with pytest.raises(ExecutionStop,match='session'):reserve(ready,1)
    assert ready[0].snapshot()['total_attempts']==3

def test_new_unknown_blocks_entire_successor(ready):
    a=reserve(ready);ready[0].unknown(a,'provider_timeout_unknown')
    with pytest.raises(ExecutionStop,match='unknowns_changed'):reserve(ready,1)

def test_new_identity_mismatch_creates_stop(ready):
    a=reserve(ready)
    with pytest.raises(ExecutionStop,match='backend_revision'):settle(ready,a,canonical(['deepseek-flash','other-fp']))
    assert ready[0].snapshot()['integrity_stops']==2
    with pytest.raises(ExecutionStop,match='stops_changed'):reserve(ready,1)

def test_changed_receipt_contract_or_missing_result_stops(ready):
    a=reserve(ready);settle(ready,a)
    with pytest.raises(ExecutionStop,match='prior_result'):reserve(ready,1)
    ready[3].write_text('{}',encoding='utf-8')
    with pytest.raises(ExecutionStop,match='contract_changed'):reserve(ready,1)

def test_arbitrary_other_phase_still_blocked(ready):
    ready[0].register_phase('other',digest({}),cloud_cap=1,local_cap=0)
    with pytest.raises(ExecutionStop):ready[0].reserve(provider='deepseek',phase='other',request_key='other',packet_sha='f'*64,reserve_micro=1)

def test_closing_is_permanent(ready):
    rs.close(ready[0],ready[2]['campaign'])
    with pytest.raises(ExecutionStop,match='not_open'):reserve(ready)
    with pytest.raises(ExecutionStop,match='already_registered'):rs.register(ready[0],ready[3],'replacement')


def test_old_unknown_mutation_and_cap_block(ready):
    with pytest.raises(ExecutionStop,match='pilot_cost_cap'):reserve(ready,0,reserve_micro=15000001)
    with ready[0].transaction() as db:
        db.execute("UPDATE execution_requests SET accounted=11 WHERE attempt_id='prior0'")
    with pytest.raises(ExecutionStop,match='unknowns_changed'):reserve(ready)

def test_changed_historical_receipt_blocks(ready):
    old=Path(ready[2]['historical_stops'][0]['receipt_path']);old.write_text('{}',encoding='utf-8')
    with pytest.raises(ExecutionStop,match='old_receipt'):reserve(ready)



def body_for_mock(i):
    return {'model':'deepseek-flash','messages':[{'role':'system','content':'return JSON'}, {'role':'user','content':canonical({'facts':{'graph_score':.3},'case_alias':str(i)})}], 'response_format':{'type':'json_object'},'thinking':{'type':'disabled'},'max_tokens':256}

@pytest.fixture
def ready200(ready):
    budget,store,old,_,tmp=ready;cfg=dict(old)
    cfg.update(version='route6-rank-successor-v2-200',campaign='ranking-200',max_requests=200,cap_micro_cny=5000000,
               payloads=[digest({'new_case':i}) for i in range(200)])
    for i in range(2): cfg['payloads'][i]=digest(body_for_mock(i))
    path=tmp/'contract200.json';path.write_text(canonical(cfg),encoding='utf-8')
    store.campaign(cfg['campaign'],file_hash(path),cap_each=200,group_key=rs.GROUP_200)
    rs.register(budget,path,'User requested two hundred cases')
    return budget,store,cfg,path,tmp

def test_two_hundred_version_records_sequential_and_keeps_old_stops(ready200):
    a=reserve(ready200);p=settle(ready200,a)
    rs.record(ready200[0],ready200[2]['campaign'],0,a,p)
    assert reserve(ready200,1)
    assert ready200[0].snapshot()['integrity_stops']==1

def test_two_hundred_contract_does_not_expand_or_allow_payload_swap(ready200):
    cfg=ready200[2].copy();cfg['max_requests']=201
    p=ready200[4]/'wrong200.json';p.write_text(canonical(cfg),encoding='utf-8')
    with pytest.raises(ExecutionStop,match='bounds'):rs.read_contract(p)
    with pytest.raises(ExecutionStop,match='exact_payload'):reserve(ready200,packet_sha='f'*64)

def test_two_hundred_cap_does_not_borrow_previous_batch(ready200):
    with pytest.raises(ExecutionStop,match='pilot_cost_cap'):reserve(ready200,reserve_micro=5000001)


@pytest.fixture
def ready500(ready):
    budget,store,old,_,tmp=ready
    with budget.transaction() as db:
        for provider,n in [('deepseek',361),('local',23)]:
            for i in range(n):
                attempt=f'old-{provider}-{i}'
                db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(attempt,provider,'settled',1))
                db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',(attempt,attempt,'pre-existing','z'*64,1))
    cfg=dict(old);cfg.update(version='route6-rank-successor-v3-500',campaign='ranking-500',max_requests=500,cap_micro_cny=5000000,
        deepseek_before=364,attempts_before=387,payloads=[digest({'new_case':i}) for i in range(500)])
    for i in range(2): cfg['payloads'][i]=digest(body_for_mock(i))
    path=tmp/'contract500.json';path.write_text(canonical(cfg),encoding='utf-8')
    store.campaign(cfg['campaign'],file_hash(path),cap_each=500,group_key=rs.GROUP_500)
    rs.register(budget,path,'User authorized 500 case external ranking')
    return budget,store,cfg,path,tmp

def test_five_hundred_allocation_is_exact_and_pinned(ready500):
    budget,_,cfg,_,tmp=ready500
    assert reserve(ready500)
    wrong=dict(cfg);wrong['max_requests']=501;p=tmp/'wrong500.json';p.write_text(canonical(wrong),encoding='utf-8')
    with pytest.raises(ExecutionStop,match='bounds'):rs.read_contract(p)

def test_other_work_during_batch_stops_successor(ready500):
    budget=ready500[0]
    with budget.transaction() as db:
        db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',('other-live','local','settled',0))
        db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',('other-live','other-live','other','z'*64,0))
    with pytest.raises(ExecutionStop,match='global_allocation_changed'):reserve(ready500)

def test_authorized_successor_crosses_old_600_cap_without_reset(ready500):
    budget,_,cfg,_,tmp=ready500;result=tmp/'fixture-saved.json';result.write_text('{}',encoding='utf-8');sha=file_hash(result)
    with budget.transaction() as db:
        for i in range(236):
            session=cfg['campaign']+'-C'+str(i+1);phase='synthetic-phase-'+str(i);attempt='synthetic-attempt-'+str(i)
            db.execute('INSERT INTO autonomous_sessions VALUES(?,?,?,?,?,?,NULL,NULL)',(session,cfg['campaign'],'m','deepseek',1,9999999999))
            db.execute('INSERT INTO autonomous_phases VALUES(?,?,?)',(phase,session,0))
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(attempt,'deepseek','settled',100))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',(attempt,attempt,phase,cfg['payloads'][i],1))
            db.execute('INSERT INTO rank_successor_results VALUES(?,?,?,?,?)',(cfg['campaign'],i,attempt,str(result),sha))
    assert budget.snapshot()['deepseek_attempts']==600
    new=reserve(ready500,236)
    assert new and budget.snapshot()['deepseek_attempts']==601
    with budget.transaction() as db:
        assert db.execute("select identity from provider_identity where provider='deepseek'").fetchone()[0]==cfg['old_identity']

def test_500_cap_and_total_cannot_expand(ready500):
    cfg=ready500[2].copy();cfg['attempts_before']=501
    p=ready500[4]/'invalid-total.json';p.write_text(canonical(cfg),encoding='utf-8')
    with pytest.raises(ExecutionStop,match='global_allocation'):rs.read_contract(p)
    with pytest.raises(ExecutionStop,match='pilot_cost_cap'):reserve(ready500,reserve_micro=5000001)


@pytest.fixture
def ready_rich(ready):
    budget,store,old,_,tmp=ready
    with budget.transaction() as db:
        for provider,n in [('deepseek',861),('local',23)]:
            for i in range(n):
                attempt=f'rich-prior-{provider}-{i}'
                db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(attempt,provider,'settled',1))
                db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',(attempt,attempt,'pre-existing','z'*64,1))
    cfg=dict(old);cfg.update(version='route6-rich-successor-v4-500',campaign='ranking-rich-500',max_requests=500,cap_micro_cny=5000000,
        deepseek_before=864,attempts_before=887,payloads=[digest({'rich_case':i}) for i in range(500)])
    path=tmp/'contract-rich.json';path.write_text(canonical(cfg),encoding='utf-8')
    store.campaign(cfg['campaign'],file_hash(path),cap_each=500,group_key=rs.GROUP_RICH_500)
    rs.register(budget,path,'User authorized 500 richer-evidence requests')
    return budget,store,cfg,path,tmp

def test_rich_500_exact_bound_preserves_old_stops(ready_rich):
    assert reserve(ready_rich)
    assert ready_rich[0].snapshot()['integrity_stops']==1
    assert ready_rich[0].snapshot()['unresolved_attempts']==3  # two historical plus this reservation

def test_rich_allocation_cannot_expand_or_use_stale_ledger(ready_rich):
    cfg=ready_rich[2].copy();cfg['max_requests']=501;p=ready_rich[4]/'wrong-rich.json';p.write_text(canonical(cfg),encoding='utf-8')
    with pytest.raises(ExecutionStop,match='bounds'):rs.read_contract(p)
    with ready_rich[0].transaction() as db:
        db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',('interloper','local','settled',0))
        db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',('interloper','interloper','other','z'*64,0))
    with pytest.raises(ExecutionStop,match='global_allocation_changed'):reserve(ready_rich)

def test_explicit_rich_successor_can_cross_old_total_limit_without_reset(ready_rich):
    budget,_,cfg,_,tmp=ready_rich;result=tmp/'rich-saved.json';result.write_text('{}',encoding='utf-8');sha=file_hash(result)
    with budget.transaction() as db:
        for i in range(113):
            session=cfg['campaign']+'-C'+str(i+1);phase='rich-phase-'+str(i);attempt='rich-attempt-'+str(i)
            db.execute('INSERT INTO autonomous_sessions VALUES(?,?,?,?,?,?,NULL,NULL)',(session,cfg['campaign'],'m','deepseek',1,9999999999))
            db.execute('INSERT INTO autonomous_phases VALUES(?,?,?)',(phase,session,0))
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(attempt,'deepseek','settled',100))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',(attempt,attempt,phase,cfg['payloads'][i],1))
            db.execute('INSERT INTO rank_successor_results VALUES(?,?,?,?,?)',(cfg['campaign'],i,attempt,str(result),sha))
    assert budget.snapshot()['total_attempts']==1000
    assert reserve(ready_rich,113)
    assert budget.snapshot()['total_attempts']==1001

def test_rich_batch_keeps_five_yuan_reservation_bound(ready_rich):
    with pytest.raises(ExecutionStop,match='pilot_cost_cap'):reserve(ready_rich,reserve_micro=5000001)


@pytest.fixture
def ready_continuation(ready_rich):
    budget,store,parent,parent_path,tmp=ready_rich
    receipt=tmp/'parent-receipt.json';receipt.write_text('{}',encoding='utf-8');saved=tmp/'parent-result.json';saved.write_text('{}',encoding='utf-8')
    with budget.transaction() as db:
        for i in range(38):
            session=parent['campaign']+'-C'+str(i+1);phase='prior-rich-phase-'+str(i);attempt='prior-rich-attempt-'+str(i)
            db.execute('INSERT INTO autonomous_sessions VALUES(?,?,?,?,?,?,NULL,NULL)',(session,parent['campaign'],'m','deepseek',1,9999999999))
            db.execute('INSERT INTO autonomous_phases VALUES(?,?,?)',(phase,session,0))
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',(attempt,'deepseek','settled',100))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted,receipt_sha,receipt_path,identity,integrity_stop) VALUES(?,?,?,?,?,?,?,?,?)',
               (attempt,attempt,phase,parent['payloads'][i],1,file_hash(receipt),str(receipt),parent['expected_identity'],0))
            db.execute('INSERT INTO rank_successor_results VALUES(?,?,?,?,?)',(parent['campaign'],i,attempt,str(saved),file_hash(saved)))
        db.execute('UPDATE rank_successors SET closed=1 WHERE campaign_id=?',(parent['campaign'],))
    cfg=dict(parent);cfg.update(version='route6-rich-continuation-v5',campaign='rich-continuation',max_requests=462,cap_micro_cny=4999962,
        deepseek_before=902,attempts_before=925,payloads=[digest({'cont_case':i}) for i in range(462)],parent_campaign=parent['campaign'],
        parent_contract_sha=file_hash(parent_path),parent_completed=38,parent_cost_micro=38)
    path=tmp/'contract-cont.json';path.write_text(canonical(cfg),encoding='utf-8')
    store.campaign(cfg['campaign'],file_hash(path),cap_each=462,group_key=rs.GROUP_RICH_CONT)
    rs.register(budget,path,'Continuation of already-authorized 500, no resend')
    return budget,store,cfg,path,tmp

def test_continuation_preserves_prefix_and_shared_limits(ready_continuation):
    budget,_,cfg,_,_=ready_continuation
    assert reserve(ready_continuation)
    assert budget.snapshot()['total_attempts']==926
    with budget.transaction() as db:
        assert db.execute('SELECT closed FROM rank_successors WHERE campaign_id=?',(cfg['parent_campaign'],)).fetchone()==(1,)

def test_continuation_rejects_old_payload_and_parent_receipt_change(ready_continuation):
    cfg=ready_continuation[2]
    with pytest.raises(ExecutionStop,match='exact_payload'):reserve(ready_continuation,packet_sha='f'*64)
    (ready_continuation[4]/'parent-receipt.json').write_text('tampered',encoding='utf-8')
    with pytest.raises(ExecutionStop,match='parent_receipt'):reserve(ready_continuation)

def test_continuation_parent_cost_and_remaining_calls_are_bound(ready_continuation):
    budget,_,cfg,_,tmp=ready_continuation
    with budget.transaction() as db:
        altered=dict(cfg);altered['parent_cost_micro']=37
        with pytest.raises(ExecutionStop,match='parent_cost'):rs.check_parent(db,altered)
        altered=dict(cfg);altered['max_requests']=463
        with pytest.raises(ExecutionStop,match='parent_count'):rs.check_parent(db,altered)

