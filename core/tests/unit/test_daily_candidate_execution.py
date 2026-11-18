"""Candidate execution reuses the real ledger protocol, on isolated fixtures only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_daily_report_successor import daily_ready, write
from test_rank_successor import ready, reserve as parent_reserve, settle
from test_autonomous_tool_contracts import scope, index, registry
from test_autonomous_action_providers import profile
from graphrisk.agent import rank_successor as rs, judgment_v12_daily_review as candidate
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
import run_daily_report_review as cli


def reserve(data,position=0,**changes):
    budget,store,cfg,_,_=data
    name=cfg['campaign']+'-C'+str(position+1)
    store.open(name,cfg['campaign'],{'campaign':cfg['campaign'],'position':position,
               'payload_sha':cfg['payloads'][position]},'deepseek',calls=1)
    wrapper=digest({'fixture':position,'campaign':cfg['campaign']})
    phase=store.register_packet(name,0,wrapper,'deepseek')
    values=dict(provider='deepseek',phase=phase,request_key=digest([wrapper,'deepseek','0']),
                packet_sha=cfg['payloads'][position],reserve_micro=100)
    values.update(changes)
    return budget.reserve(**values)


@pytest.fixture
def candidate_ready(daily_ready):
    budget,store,old,parent_path,tmp=daily_ready
    for i in range(6):
        attempt=parent_reserve(daily_ready,i)
        rs.record(budget,old['campaign'],i,attempt,settle(daily_ready,attempt))
    rs.close(budget,old['campaign'])
    cfg=deepcopy(old)
    cfg.update(version=rs.DAILY_CANDIDATE_VERSION,campaign='candidate-six',
        deepseek_before=1370,attempts_before=1393,parent_campaign=old['campaign'],
        parent_contract_sha=file_hash(parent_path),report_contract=candidate.CONTRACT,
        generation_variant=candidate.GENERATION)
    manifest=json.loads(Path(old['review_manifest_path']).read_text(encoding='utf-8'))
    profile=json.loads(Path(old['profile_path']).read_text(encoding='utf-8'))
    formatter=object.__new__(candidate.Provider)
    formatter.provider,formatter.profile='deepseek',profile
    for row in manifest['selected_cases']:
        key=row['review_case'];parent=Path(row['payload_path']).parent
        raw=json.loads((parent/(key+'-source-private.json')).read_text(encoding='utf-8'))
        bundle=EvidenceBundle.model_validate_json(canonical(raw['evidence']),strict=True)
        packet=candidate.prepare_packet(json.loads((parent/(key+'-packet-private.json')).read_text(encoding='utf-8')),bundle,profile)
        payload=formatter.payload(packet)
        paths=[write(tmp/'candidate'/(key+suffix),value) for suffix,value in
            [('-source-private.json',raw),('-packet-private.json',packet),('-payload.json',payload)]]
        cfg['files'].update({str(p):file_hash(p) for p in paths})
        row.update(payload_path=str(paths[-1]),payload_file_sha256=file_hash(paths[-1]),
                   canonical_payload_sha256=digest(payload),packet_sha256=digest(packet))
    manifest_path=write(tmp/'candidate/review-manifest.json',manifest)
    cfg.update(review_manifest_path=str(manifest_path),review_manifest_sha256=file_hash(manifest_path),
               payloads=[row['canonical_payload_sha256'] for row in manifest['selected_cases']])
    cfg['files'][str(manifest_path)]=file_hash(manifest_path)
    path=write(tmp/'candidate-config.json',cfg)
    store.campaign(cfg['campaign'],file_hash(path),cap_each=6,group_key=rs.GROUP_DAILY_CANDIDATE)
    rs.register(budget,path,'Fixture approval for exact six candidate payloads')
    return budget,store,cfg,path,tmp


def test_candidate_six_and_old_campaign_remain_closed(candidate_ready):
    budget,_,cfg,_,_=candidate_ready
    for i in range(6):
        attempt=reserve(candidate_ready,i)
        rs.record(budget,cfg['campaign'],i,attempt,settle(candidate_ready,attempt))
    snap=budget.snapshot()
    assert (snap['deepseek_attempts'],snap['total_attempts'])==(1376,1399)
    assert (snap['unresolved_attempts'],snap['integrity_stops'])==(2,1)
    with budget.transaction() as db:
        assert db.execute('SELECT closed FROM rank_successors WHERE campaign_id=?',
                          (cfg['parent_campaign'],)).fetchone()==(1,)
    rs.close(budget,cfg['campaign'])
    with pytest.raises(ExecutionStop):reserve(candidate_ready,0)


@pytest.mark.parametrize('mutation',['parent_open','parent_receipt','parent_evidence','new_unknown','identity','overspend','payload'])
def test_candidate_stops_on_integrity_or_scope_change(candidate_ready,mutation):
    budget,_,cfg,_,_=candidate_ready
    if mutation.startswith('parent_'):
        with budget.transaction() as db:
            if mutation=='parent_open':
                db.execute('UPDATE rank_successors SET closed=0 WHERE campaign_id=?',(cfg['parent_campaign'],))
            elif mutation=='parent_receipt':
                path=db.execute('SELECT receipt_path FROM execution_requests r JOIN rank_successor_results s USING(attempt_id) '
                    'WHERE s.campaign_id=? AND position=0',(cfg['parent_campaign'],)).fetchone()[0]
                Path(path).write_text('changed')
            else:
                manifest=json.loads(Path(cfg['review_manifest_path']).read_text())
                path=Path(manifest['selected_cases'][0]['payload_path']).parent/'H1-source-private.json'
                path.write_text('{}')
    elif mutation=='new_unknown':
        budget.unknown(reserve(candidate_ready),'timeout_unknown')
    elif mutation=='identity':
        with pytest.raises(ExecutionStop):settle(candidate_ready,reserve(candidate_ready),canonical(['deepseek-flash','wrong']))
    changes={'reserve_micro':5_000_001} if mutation=='overspend' else {'packet_sha':'f'*64} if mutation=='payload' else {}
    with pytest.raises(ExecutionStop):reserve(candidate_ready,1 if mutation in ('identity','new_unknown') else 0,**changes)


@pytest.mark.parametrize('key,value',[('deepseek_before',1371),('attempts_before',1394),('max_requests',7),
    ('generation_variant','v12-day-end-adapter-v1'),('report_contract','program-facts-judgment-v3'),('repairs',1)])
def test_candidate_contract_cannot_widen_or_use_old_provider(candidate_ready,key,value):
    cfg=deepcopy(candidate_ready[2]);cfg[key]=value
    with pytest.raises(ExecutionStop):rs.read_contract(write(candidate_ready[4]/'bad.json',cfg))


def test_cli_selects_actual_candidate_and_requires_approval(candidate_ready,monkeypatch):
    cfg=candidate_ready[2]
    assert cli.report_implementation(cfg)==(candidate.Provider,candidate.Draft,candidate.verify)
    monkeypatch.setattr(cli,'check',lambda path:(cfg,(1370,1393)))
    monkeypatch.setattr(cli,'InvestigationBudget',lambda *a,**k:pytest.fail('must not open live ledger'))
    assert cli.run(candidate_ready[3])['paid_calls']==0
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE','1')
    with pytest.raises(ValueError,match='approval'):cli.run(candidate_ready[3],live=True)


def test_new_source_is_validated_even_when_rehashed(candidate_ready):
    cfg=deepcopy(candidate_ready[2])
    manifest=json.loads(Path(cfg['review_manifest_path']).read_text())
    path=Path(manifest['selected_cases'][0]['payload_path']).parent/'H1-packet-private.json'
    packet=json.loads(path.read_text(encoding='utf-8'));packet['view']['writing_plan']='changed'
    write(path,packet);cfg['files'][str(path)]=file_hash(path)
    with pytest.raises(ExecutionStop):rs.read_contract(write(candidate_ready[4]/'changed.json',cfg))
