"""Exact six-quote successor scope, using a fresh fixture ledger."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_daily_candidate_execution import candidate_ready,reserve
from test_daily_report_successor import daily_ready,write
from test_rank_successor import ready,settle
from test_autonomous_tool_contracts import scope,index,registry
from test_autonomous_action_providers import profile
from graphrisk.agent import rank_successor as rs,judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.upgrade_contract import canonical,digest,file_hash
import run_daily_report_review as cli


@pytest.fixture
def quotes_ready(candidate_ready):
    budget,store,parent,parent_path,tmp=candidate_ready
    for i in range(6):
        attempt=reserve(candidate_ready,i)
        rs.record(budget,parent['campaign'],i,attempt,settle(candidate_ready,attempt))
    rs.close(budget,parent['campaign'])
    cfg=deepcopy(parent)
    cfg.update(version=rs.DAILY_QUOTES_VERSION,campaign='quotes-six',deepseek_before=1376,
        attempts_before=1399,parent_campaign=parent['campaign'],parent_contract_sha=file_hash(parent_path),
        generation_variant=quotes.GENERATION,report_contract=quotes.CONTRACT)
    manifest=json.loads(Path(parent['review_manifest_path']).read_text(encoding='utf-8'))
    profile=json.loads(Path(parent['profile_path']).read_text(encoding='utf-8'))
    formatter=object.__new__(quotes.Provider);formatter.provider,formatter.profile='deepseek',profile
    for row in manifest['selected_cases']:
        key=row['review_case'];raw=json.loads((tmp/'six'/(key+'-source-private.json')).read_text(encoding='utf-8'))
        original=json.loads((tmp/'six'/(key+'-packet-private.json')).read_text(encoding='utf-8'))
        bundle=EvidenceBundle.model_validate_json(canonical(raw['evidence']),strict=True)
        packet=quotes.prepare_packet(original,bundle,profile);payload=formatter.payload(packet)
        paths=[write(tmp/'quotes'/(key+suffix),value) for suffix,value in
            [('-source-private.json',raw),('-packet-private.json',packet),('-payload.json',payload)]]
        cfg['files'].update({str(p):file_hash(p) for p in paths})
        row.update(payload_path=str(paths[-1]),payload_file_sha256=file_hash(paths[-1]),
                   packet_sha256=digest(packet),canonical_payload_sha256=digest(payload))
    manifest_path=write(tmp/'quotes/review-manifest.json',manifest)
    cfg.update(review_manifest_path=str(manifest_path),review_manifest_sha256=file_hash(manifest_path),
               payloads=[r['canonical_payload_sha256'] for r in manifest['selected_cases']])
    cfg['files'][str(manifest_path)]=file_hash(manifest_path)
    path=write(tmp/'quotes-config.json',cfg)
    store.campaign(cfg['campaign'],file_hash(path),cap_each=6,group_key=rs.GROUP_DAILY_QUOTES)
    rs.register(budget,path,'Fixture authorization for exact six quotes-v2 payloads')
    return budget,store,cfg,path,tmp


def test_six_quotes_keep_ancestor_caps_results_and_identity(quotes_ready):
    budget,_,cfg,_,_=quotes_ready
    assert cli.report_implementation(cfg)==(quotes.Provider,quotes.Draft,quotes.verify)
    for i in range(6):
        attempt=reserve(quotes_ready,i)
        rs.record(budget,cfg['campaign'],i,attempt,settle(quotes_ready,attempt))
    assert budget.snapshot()['deepseek_attempts']==1382
    assert budget.snapshot()['total_attempts']==1405
    assert budget.snapshot()['unresolved_attempts']==2 and budget.snapshot()['integrity_stops']==1
    rs.close(budget,cfg['campaign'])
    with pytest.raises(ExecutionStop):reserve(quotes_ready,0)


@pytest.mark.parametrize('change',['old_provider','request_count','allocation','payload','ancestor_open','new_unknown','overspend'])
def test_quotes_cannot_expand_or_skip_previous_batches(quotes_ready,change):
    budget,_,cfg,_,tmp=quotes_ready
    if change in ('old_provider','request_count','allocation'):
        cfg=deepcopy(cfg)
        key,value={'old_provider':('generation_variant','v12-daily-citation-review-v1'),
            'request_count':('max_requests',7),'allocation':('attempts_before',1400)}[change]
        cfg[key]=value
        with pytest.raises(ExecutionStop):rs.read_contract(write(tmp/'bad.json',cfg))
        return
    if change=='ancestor_open':
        with budget.transaction() as db:
            db.execute("UPDATE rank_successors SET closed=0 WHERE campaign_id='daily-report-six'")
    if change=='new_unknown':budget.unknown(reserve(quotes_ready),'timeout_unknown')
    changes={'packet_sha':'f'*64} if change=='payload' else {'reserve_micro':5_000_001} if change=='overspend' else {}
    with pytest.raises(ExecutionStop):reserve(quotes_ready,1 if change=='new_unknown' else 0,**changes)


def test_quotes_cli_stays_read_only_until_specific_approval(quotes_ready,monkeypatch):
    cfg=quotes_ready[2]
    monkeypatch.setattr(cli,'check',lambda p:(cfg,(1376,1399)))
    monkeypatch.setattr(cli,'InvestigationBudget',lambda *a,**k:pytest.fail('no live budget without approval'))
    assert cli.run(quotes_ready[3])['paid_calls']==0
    monkeypatch.setenv('GRAPHRISK_UPGRADE_LIVE','1')
    with pytest.raises(ValueError,match='approval'):cli.run(quotes_ready[3],live=True)
