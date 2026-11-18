"""Six-case report successor fixtures; never read or mutate the live ledger."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import pytest

from test_rank_successor import ready,phase_for,reserve,settle
from test_autonomous_tool_contracts import scope,index,registry,executor_for,run_core
from test_autonomous_action_providers import profile
from graphrisk.agent import rank_successor as rs
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_tools import DailyInvestigationScope,DailyScoringContext
from graphrisk.agent.judgment_v3 import CONTRACT,DAY_END_VARIANT,JudgmentProvider
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash
from graphrisk.serving.daily_narrative import prepare_daily_narrative


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(canonical(value),encoding='utf-8')
    return path


@pytest.fixture
def daily_ready(ready,scope,index,registry,profile):
    budget,store,old,_,tmp=ready
    with budget.transaction() as db:
        for provider,n in (('deepseek',1361),('local',23)):
            db.executemany('INSERT INTO model_attempts VALUES(?,?,?,?)',
                [(f'daily-prior-{provider}-{i}',provider,'settled',1) for i in range(n)])
            db.executemany('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',
                [(f'daily-prior-{provider}-{i}',f'daily-prior-{provider}-{i}','existing','a'*64,1) for i in range(n)])
    source=tmp/'bound-source';source_file=source/'src/fixture.py'
    source_file.parent.mkdir(parents=True);source_file.write_text('fixture = True\n',encoding='utf-8')
    budget_config=source/'configs/agent_investigation_experiment.json'
    write(budget_config,{'fixture':'bound budget contract'})
    profile=quality_report_profile(profile)
    profile['deepseek']['pricing_verified_date']=date.today().isoformat()
    profile_path=write(tmp/'six/profile.json',profile)
    files={str(source_file):file_hash(source_file),str(profile_path):file_hash(profile_path),
           str(budget_config):file_hash(budget_config)}
    formatter=object.__new__(JudgmentProvider);formatter.provider,formatter.profile='deepseek',profile
    cases=[]
    for number,key in enumerate(rs.DAILY_CASES):
        risk=['high','high','medium','medium','low','low'][number]
        context=DailyScoringContext(relative_day=0,batch_end_second=86400,available_through_second=86401,
            batch_id='day-0-fixture',revision=1,sealed_content_sha256=scope.transaction_revision,
            model_version=scope.primary_model_version,model_sha256=scope.model_revision,
            feature_protocol_sha256=scope.feature_revision,category_maps_sha256='e'*64,
            policy_sha256='f'*64,score=.8,risk_level=risk)
        bound=DailyInvestigationScope(**{**scope.model_dump(),'case_alias':digest(key)[:32],
            'transaction_id':'PRIVATE-'+key},scoring_context=context)
        def mutate(name,result):
            if name=='get_transaction':result['data']['transaction_amount']=17.0+number
            return result
        executor,_=executor_for(bound,index,mutate=mutate);run_core(executor)
        bundle=build_evidence(bound,executor.observations,registry)
        packet=prepare_daily_narrative(bound,executor.observations,registry,profile,
            report_contract=CONTRACT);packet['logical_key']='0'
        payload=formatter.payload(packet)
        payload_path=write(tmp/('six/'+key+'-payload.json'),payload)
        packet_path=write(tmp/('six/'+key+'-packet-private.json'),packet)
        raw_path=write(tmp/('six/'+key+'-source-private.json'),
            {'scope':bound.model_dump(mode='json'),'evidence':bundle.model_dump(mode='json')})
        for p in (payload_path,packet_path,raw_path):files[str(p)]=file_hash(p)
        cases.append(dict(review_case=key,risk_level=risk,scope_sha256=bound.binding_sha256,
            packet_sha256=digest(packet),payload_path=str(payload_path),
            payload_file_sha256=file_hash(payload_path),canonical_payload_sha256=digest(payload),
            source_contract_issues=[]))
    manifest=write(tmp/'six/review-manifest.json',dict(version='six-day-end-v12-review-prepared-v1',
        source=str(source),source_python_sha256={'src/fixture.py':file_hash(source_file)},
        max_requested_first_drafts=6,automatic_retries=0,repairs=0,paid_judges=0,
        labels_read=False,historical_replay=True,selected_cases=cases,profile_sha256=digest(profile)))
    files[str(manifest)]=file_hash(manifest)
    cfg={**old,'version':rs.DAILY_VERSION,'campaign':'daily-report-six','max_requests':6,
        'cap_micro_cny':5_000_000,'retries':0,'automatic_retries':0,'repairs':0,'judge_calls':0,
        'pause_cny':80,'overall_max_cny':100,'report_contract':CONTRACT,'generation_variant':DAY_END_VARIANT,
        'case_keys':rs.DAILY_CASES,'deepseek_before':1364,'attempts_before':1387,
        'pricing_required':False,'review_manifest_path':str(manifest),
        'source_root':str(source),
        'review_manifest_sha256':file_hash(manifest),'profile_path':str(profile_path),
        'payloads':[row['canonical_payload_sha256'] for row in cases],'files':files}
    path=write(tmp/'daily-contract.json',cfg)
    store.campaign(cfg['campaign'],file_hash(path),cap_each=6,group_key=rs.GROUP_DAILY_REPORT)
    rs.register(budget,path,'Explicit fixture approval for these six day-end drafts')
    return budget,store,cfg,path,tmp


def test_six_draft_successor_exceeds_old_caps_without_reset_or_identity_change(daily_ready):
    budget,_,cfg,_,_=daily_ready
    assert budget.snapshot()['total_attempts']==1387
    for i in range(6):
        attempt=reserve(daily_ready,i);saved=settle(daily_ready,attempt)
        rs.record(budget,cfg['campaign'],i,attempt,saved)
    snapshot=budget.snapshot()
    assert snapshot['total_attempts']==1393 and snapshot['deepseek_attempts']==1370
    assert snapshot['unresolved_attempts']==2 and snapshot['integrity_stops']==1
    with budget.transaction() as db:
        assert db.execute("SELECT identity FROM provider_identity WHERE provider='deepseek'").fetchone()==(cfg['old_identity'],)
        phase='loop-'+cfg['campaign']+'-C6-0'
        assert rs.global_cloud_cap(db,phase)==1370 and rs.global_total_cap(db,phase)==1393
    rs.close(budget,cfg['campaign'])
    with pytest.raises(ExecutionStop,match='not_open'):reserve(daily_ready,0)


@pytest.mark.parametrize('key,value,code',[
    ('max_requests',7,'bounds'),('cap_micro_cny',5_000_001,'bounds'),
    ('repairs',1,'daily_bounds'),('retries',1,'bounds'),('judge_calls',1,'daily_bounds'),
    ('automatic_retries',1,'daily_bounds'),('pause_cny',81,'daily_bounds'),
    ('overall_max_cny',101,'daily_bounds'),('deepseek_before',1365,'global_allocation'),
    ('attempts_before',1388,'global_allocation'),('generation_variant','v13','daily_scope')])
def test_daily_scope_cannot_expand_or_change_generation(daily_ready,key,value,code):
    cfg=deepcopy(daily_ready[2]);cfg[key]=value
    path=write(daily_ready[4]/'bad-contract.json',cfg)
    with pytest.raises(ExecutionStop,match=code):rs.read_contract(path)


def test_exact_payload_order_and_bound_files_cannot_be_swapped(daily_ready):
    with pytest.raises(ExecutionStop,match='exact_payload'):
        reserve(daily_ready,packet_sha='f'*64)
    with pytest.raises(ExecutionStop,match='session'):reserve(daily_ready,1)
    cfg=deepcopy(daily_ready[2]);cfg['payloads'].reverse()
    with pytest.raises(ExecutionStop,match='daily_payload_whitelist'):
        rs.read_contract(write(daily_ready[4]/'swapped.json',cfg))
    manifest=json.loads(Path(cfg['review_manifest_path']).read_text(encoding='utf-8'))
    payload=manifest['selected_cases'][0]['payload_path']
    write(Path(payload),{'model':'other'})
    with pytest.raises(ExecutionStop,match='file_changed'):reserve(daily_ready)


def test_source_inventory_and_manifest_are_bound(daily_ready):
    cfg=deepcopy(daily_ready[2]);manifest=json.loads(Path(cfg['review_manifest_path']).read_text(encoding='utf-8'))
    source=Path(manifest['source'])/'src/fixture.py'
    source.write_text('changed = True\n',encoding='utf-8')
    cfg['files'][str(source)]=file_hash(source)
    with pytest.raises(ExecutionStop,match='daily_source_inventory'):
        rs.read_contract(write(daily_ready[4]/'changed-source.json',cfg))


def test_pending_pricing_can_be_prepared_but_cannot_reserve(daily_ready):
    budget,store,cfg,_,tmp=daily_ready
    cfg=deepcopy(cfg);cfg.update(campaign='pending-daily-price',pricing_required=True)
    path=write(tmp/'pending-contract.json',cfg)
    assert rs.read_contract(path)['pricing_required'] is True
    store.campaign(cfg['campaign'],file_hash(path),cap_each=6,group_key=rs.GROUP_DAILY_REPORT)
    rs.register(budget,path,'Fixture approval remains subject to pricing verification')
    data=(budget,store,cfg,path,tmp)
    with pytest.raises(ExecutionStop,match='daily_pricing_required'):reserve(data)


def test_new_work_unknown_identity_and_money_still_stop_daily_successor(daily_ready):
    with pytest.raises(ExecutionStop,match='pilot_cost_cap'):
        reserve(daily_ready,reserve_micro=5_000_001)
    budget=daily_ready[0];attempt=reserve(daily_ready)
    budget.unknown(attempt,'provider_timeout_unknown')
    with pytest.raises(ExecutionStop,match='unknowns_changed'):reserve(daily_ready,1)
    budget.register_phase('unrelated-report',digest({}),cloud_cap=1,local_cap=0)
    with pytest.raises(ExecutionStop):
        budget.reserve(provider='deepseek',phase='unrelated-report',request_key='unrelated-report',
            packet_sha='f'*64,reserve_micro=1)


def test_new_campaign_does_not_allow_wider_session_or_group_cap(daily_ready):
    budget,store,cfg,_,_=daily_ready
    with pytest.raises(ExecutionStop,match='invalid_daily_report_campaign_cap'):
        store.campaign('oversized','a'*64,cap_each=7,group_key=rs.GROUP_DAILY_REPORT)
    session=cfg['campaign']+'-C1'
    store.open(session,cfg['campaign'],{'campaign':cfg['campaign'],'position':0,
        'payload_sha':cfg['payloads'][0]},'deepseek',calls=2)
    wrapper=digest({'fixture':'wider-session'});phase=store.register_packet(session,0,wrapper,'deepseek')
    with pytest.raises(ExecutionStop,match='session'):
        budget.reserve(provider='deepseek',phase=phase,request_key=digest([wrapper,'deepseek','0']),
            packet_sha=cfg['payloads'][0],reserve_micro=100)


def test_daily_identity_drift_creates_new_stop_and_preserves_old_rows(daily_ready):
    budget=daily_ready[0];attempt=reserve(daily_ready)
    with pytest.raises(ExecutionStop,match='backend_revision'):
        settle(daily_ready,attempt,canonical(['deepseek-flash','another-fingerprint']))
    assert budget.snapshot()['integrity_stops']==2
    with pytest.raises(ExecutionStop,match='stops_changed'):reserve(daily_ready,1)


def test_interleaved_non_report_work_invalidates_exact_daily_allocation(daily_ready):
    budget=daily_ready[0]
    with budget.transaction() as db:
        db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)',('interloper','local','settled',0))
        db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',
            ('interloper','interloper','other','f'*64,0))
    with pytest.raises(ExecutionStop,match='global_allocation_changed'):reserve(daily_ready)


def test_daily_reservation_rejects_stale_pricing_outside_cli(daily_ready,monkeypatch):
    class LaterDate:
        @staticmethod
        def today():return date(2099,1,1)
    monkeypatch.setattr(rs,'date',LaterDate)
    with pytest.raises(ExecutionStop,match='daily_pricing_refresh_required'):reserve(daily_ready)


@pytest.mark.parametrize('remove',[False,True])
def test_saved_receipt_tampering_or_loss_blocks_next_report(daily_ready,remove):
    budget,_,cfg,_,tmp=daily_ready
    attempt=reserve(daily_ready);receipt=settle(daily_ready,attempt)
    saved=write(tmp/'separate-first-result.json',{'attempt_id':attempt,'report':None})
    rs.record(budget,cfg['campaign'],0,attempt,saved)
    with budget.transaction() as db:
        assert rs.verify_daily_saved_result(db,cfg,cfg['campaign'],0)==(str(saved.resolve()),file_hash(saved))
        assert rs.verify_daily_saved_result(db,cfg,cfg['campaign'],1) is None
    if remove:receipt.unlink()
    else:receipt.write_text('tampered receipt',encoding='utf-8')
    with pytest.raises(ExecutionStop,match='daily_saved_receipt'):reserve(daily_ready,1)
    rs.close(budget,cfg['campaign'])
    with budget.transaction() as db:
        with pytest.raises(ExecutionStop,match='daily_saved_receipt'):
            rs.verify_daily_saved_result(db,cfg,cfg['campaign'],0)
