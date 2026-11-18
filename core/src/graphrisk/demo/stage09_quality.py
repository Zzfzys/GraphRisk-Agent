"""Frozen Route5A local experiments. Source facts, raw judgments and safety are separate."""
from pathlib import Path
from datetime import UTC, datetime
from collections import Counter
import gc
import hashlib
import json
import threading
import time
import joblib
import numpy as np
import pandas as pd
import torch
import httpx
from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.quality_contracts import QUALITY_VERSION, QualityDraft
from graphrisk.agent.quality_evidence import investigation_packet
from graphrisk.agent.quality_reporting import QUALITY_PROMPT
from graphrisk.agent.reporting import ReportDraft, _eligible_hits, _prompt_payload
from graphrisk.agent.workflow import Stage05GraphWorkflow
from graphrisk.agent.stage09_preflight import build_freeze, check_freeze, load_contract, check_resources
from graphrisk.demo.stage09_evaluation import (AttemptLedger, ObservedProvider, digest, read_json,
    write_json, stable, grade, exclusive)
from graphrisk.demo.stage09_replay import api_smoke, grade_replay
from graphrisk.graph.stage09_monitor import DiagnosisMonitor
from graphrisk.graph.train_graphsage import _sha256
from graphrisk.models.tabular import FEATURE_COLUMNS
from graphrisk.serving.investigation_service import Stage06Service
from graphrisk.serving.investigation_views import project_investigation

PREVIOUS = Path('reports/generated/stage09/route5/20261007-replay-v1')
SCORING = {'version':'stage09-agent-quality-evaluation-v1', 'phase_attempt_cap':66,'global_attempt_cap':144,
    'seed':20261008,'selection':'validation score equal-rank tertiles, coverage quota, hash(seed:id); no labels',
    'dev_quotas':[[1,1],[1,1],[1,0]],'new_quotas':[[2,2],[2,1],[2,1]],
    'variants':{'A':'configs/stage09.yaml','B':'configs/stage09_quality.yaml','C':'configs/stage09_quality_4b.yaml'},
    'selection_rule':'highest dev source+raw semantic pass count; then lower latency median; then smaller model',
    'limitations':'bounded judgment, not free text reasoning or fraud accuracy; personal human review pending',
    'base_calls':63,'revision':2,'first_development_calls':15,'additional_development_calls':5,'temperature':0,'max_output_tokens':512,'no_paid_calls':True}


def fresh_cases(frame, scores, covered, old_ids):
    ranked=pd.DataFrame({'transaction_id':frame.transaction_id.astype(str),'score':np.asarray(scores,dtype=float)})
    if ranked.transaction_id.duplicated().any() or not np.isfinite(ranked.score).all():
        raise ValueError('invalid selection input')
    ranked=ranked.sort_values(['score','transaction_id'],kind='stable').reset_index(drop=True)
    dev,new=[],[]
    for j,ix in enumerate(np.array_split(np.arange(len(ranked)),3)):
        part=ranked.iloc[ix];part=part[~part.transaction_id.isin(old_ids)]
        for k,cov in enumerate((True,False)):
            pool=part[part.transaction_id.isin(covered)==cov].to_dict('records')
            pool.sort(key=lambda r:hashlib.sha256(f"{SCORING['seed']}:{r['transaction_id']}".encode()).hexdigest())
            dq,nq=SCORING['dev_quotas'][j][k],SCORING['new_quotas'][j][k]
            if len(pool)<dq+nq:raise ValueError('fresh selection quota unavailable')
            for rows,target,prefix in ((pool[:dq],dev,'dev'),(pool[dq:dq+nq],new,'new')):
                for row in rows:target.append({**row,'stratum':('low','medium','high')[j],
                    'graph_covered':cov,'case_id':f'{prefix}-{len(target)+1:02d}','group':prefix})
    return dev,new


def snapshot(graph, store, case):
    history=store.ego_graph(case['transaction_id']).selected_history_ids if case['graph_covered'] else ()
    if case['graph_covered']:
        target=store.index.by_id[case['transaction_id']]
        if any(store.index.by_id[i].transaction_time>=target.transaction_time for i in history):
            raise ValueError('nonhistorical_context')
    hits=_eligible_hits(graph)
    return {'request':graph.request.model_dump(mode='json'),
        'investigation_report':graph.investigation_report.model_dump(mode='json'),
        'policy_retrieval':graph.policy_retrieval.model_dump(mode='json'),
        'eligible_hits':[h.model_dump(mode='json') for h in hits],
        'expected_prompt_payload':_prompt_payload(graph,hits),'quality_packet':investigation_packet(graph),
        'legal_history_ids':list(history)}


def prepare(root,directory):
    """Same sealed IDs/facts; avoid reloading the whole validation set to select again."""
    from types import SimpleNamespace
    from graphrisk.agent.contracts import Stage05Request
    from graphrisk.agent.policy_retrieval import RetrievalResult
    from graphrisk.investigation.workflow import InvestigationReport
    if (directory/'cases.json').exists():raise ValueError('quality experiment already registered')
    parent=root/'reports/generated/stage09/route5a/20261008-quality-v1'
    previous=read_json(parent/'cases.json')
    if digest({k:v for k,v in previous.items() if k!='content_sha256'})!=previous['content_sha256']:
        raise ValueError('parent manifest changed')
    closure=read_json(parent/'development-closure.json')
    if closure['manifest_sha256']!=previous['content_sha256'] or closure['calls']!=15:
        raise ValueError('parent development not closed')
    expected_changes={'src/graphrisk/agent/quality_evidence.py','src/graphrisk/agent/quality_reporting.py',
        'src/graphrisk/demo/stage09_quality.py','scripts/evaluate_stage09_quality.py'}
    evolved=[]
    old_inputs=read_json(parent/'inputs.json')
    for item in old_inputs['artifacts']+previous['files']:
        if _sha256(root/item['path'])!=item['sha256']:
            archived=root/'artifacts/stage-09/route5a/20261008-quality-v1/source-at-freeze'/item['path']
            if item['path'] not in expected_changes or not archived.is_file() or _sha256(archived)!=item['sha256']:
                raise ValueError('unregistered parent input drift:'+item['path'])
            evolved.append(item['path'])
    contract=load_contract(root/'configs/stage09_experiment.json')
    monitor=DiagnosisMonitor(contract.resources,directory/f'prepare-resources-{time.time_ns()}.json')
    if check_resources(contract.resources,monitor.sample(True))['status']!='passed':raise ValueError('prepare_resource_gate_failed')
    cases=previous['cases']
    for case in cases:
        monitor.sample(False);monitor.require()
        old=read_json(parent/'snapshots'/(case['case_id']+'.json'))
        if digest(old)!=case['snapshot_sha256']:raise ValueError('parent facts changed')
        graph=SimpleNamespace(request=Stage05Request.model_validate(old['request']),
            investigation_report=InvestigationReport.model_validate(old['investigation_report']),
            policy_retrieval=RetrievalResult.model_validate(old['policy_retrieval']))
        snap={**old,'quality_packet':investigation_packet(graph)}
        write_json(directory/'snapshots'/(case['case_id']+'.json'),snap)
        case['parent_snapshot_sha256']=case['snapshot_sha256'];case['snapshot_sha256']=digest(snap)
    # All old results keep their first-round grading and source bindings.
    original=[]
    for case in cases:
        if case['group']!='dev':continue
        snap=read_json(parent/'snapshots'/(case['case_id']+'.json'))
        for v in ('A','B','C'):
            r=read_json(parent/'results'/f"{v}-{case['case_id']}.json")
            if result_grade(snap,r['record'],r['http_calls'],r['public_view'],previous['decision_sha256'],v)!=r['grade']:
                raise ValueError('parent raw regrade changed')
            original.append(r)
    if digest(original)!=closure['results_sha256']:raise ValueError('parent closure results drift')
    inputs=build_freeze(root,root/'configs/stage09_experiment.json');write_json(directory/'inputs.json',inputs)
    extra=[root/'streamlit_app.py',root/'uv.lock']
    for base_dir in ('src/graphrisk','api','scripts','configs'):
        extra+=sorted(p for p in (root/base_dir).rglob('*') if p.is_file() and p.suffix in {'.py','.yaml','.json'} and '__pycache__' not in p.parts)
    profile_path=root/'artifacts/stage-09/route5/20261007-replay-v1/deployment.json';profile=read_json(profile_path)
    extra.append(profile_path)
    for key in ('authority_files','graph_files','primary_files'):extra.extend(root/i['path'] for i in profile[key])
    extra+=sorted((parent/'results').glob('*.json'))+sorted((parent/'http').glob('*.json'))+sorted((parent/'snapshots').glob('*.json'))
    extra += [parent/'cases.json',parent/'selection.json',parent/'development-closure.json',root/PREVIOUS/'acceptance.json']
    manifest={'schema_version':SCORING['version'],'created_at':datetime.now(UTC).isoformat(),'scoring':SCORING,
        'parent_development_manifest_sha256':previous['content_sha256'],'cases':cases,'repeats':previous['repeats'],
        'inputs_sha256':inputs['content_sha256'],'decision_sha256':previous['decision_sha256'],
        'files':[{'path':p.relative_to(root).as_posix(),'sha256':_sha256(p)} for p in sorted(set(extra))],
        'schemas':{'legacy':digest(ReportDraft.model_json_schema()),'quality':digest(QualityDraft.model_json_schema())},
        'generation_calls_at_registration':0,'ledger_count_at_registration':len(read_json(root/'reports/generated/stage09/generation-ledger.json')['attempts']),
        'intentional_source_evolution':sorted(set(evolved)),'snapshot_mode':'reuse immutable native v1 facts; actual final tools are independently matched'}
    manifest['content_sha256']=digest(manifest);write_json(directory/'cases.json',manifest)
    print(json.dumps({'registered':len(cases),'generation_calls':0,'manifest_sha256':manifest['content_sha256']}))

def require_registered(root,directory):
    m=read_json(directory/'cases.json')
    if digest({k:v for k,v in m.items() if k!='content_sha256'})!=m['content_sha256'] or m['scoring']!=SCORING:
        raise ValueError('quality contract drift')
    if check_freeze(root,read_json(directory/'inputs.json'))['status']!='passed':raise ValueError('quality input drift')
    for item in m['files']:
        p=(root/item['path']).resolve()
        if not p.is_relative_to(root) or _sha256(p)!=item['sha256']:raise ValueError('quality source drift:'+item['path'])
    for case in m['cases']:
        if digest(read_json(directory/'snapshots'/(case['case_id']+'.json')))!=case['snapshot_sha256']:
            raise ValueError('quality snapshot drift')
    return m


def raw_judgments(snapshot,calls):
    """Independent recomputation from frozen tool fields, not published status."""
    tool=snapshot['investigation_report'];h=tool['historical_features'];r=tool['related_entities'];g=tool['risky_neighbors']
    expected={'history_activity':'unavailable' if h['status']!='ok' else 'observed' if h['data']['tx_count_last_7d']>0 else 'none',
        'relationship_evidence':'unavailable' if r['status']!='ok' else 'observed' if len(r['data']['matches'])>0 else 'none',
        'graph_evidence':'available' if g['status']=='ok' else 'unavailable',
        'policy_applicability':'not_established','risk_interpretation':'uncalibrated_signal'}
    unknowns={'policy_conditions','verified_identity','calibration'}|({'graph_evidence'} if g['status']!='ok' else set())
    checks=[]
    for c in calls:
        if 'ledger_sequence' not in c:continue
        try:
            d=json.loads(c.get('response',{}).get('response',''));QualityDraft.model_validate(d)
            issues=[key for key,value in expected.items() if d.get(key)!=value]
            if d['policy_ref'] not in {p['ref'] for p in snapshot['quality_packet']['policies']}:issues.append('policy_ref')
            refs=d['evidence_refs'];u=d['unknowns']
            if not {'F2','F3','F4','F5'}<=set(refs) or not set(refs)<={'F1','F2','F3','F4','F5'} or len(refs)!=len(set(refs)):issues.append('fact_refs')
            if set(u)!=unknowns or len(u)!=len(set(u)):issues.append('unknowns')
            checks.append({'schema':True,'semantic_pass':not issues,'issues':issues,'draft':d})
        except (ValueError,KeyError,TypeError):checks.append({'schema':False,'semantic_pass':False,'issues':['invalid_or_missing_draft']})
    return checks


def result_grade(snapshot,record,calls,view,decision,variant):
    value=grade_replay(snapshot,record,calls,view,decision)
    raw=raw_judgments(snapshot,calls) if variant!='A' else []
    value['raw_judgments']=raw
    value['raw_semantic_pass']=bool(raw and raw[0]['semantic_pass']) if variant!='A' else None
    value['joint_pass']=value['quality_pass'] and (variant=='A' or value['raw_semantic_pass'])
    if variant!='A' and view and view.get('report') and record['status'] in {'verified','partial_evidence'}:
        analysis=view['report'].get('analysis')
        if not analysis or analysis['packet_sha256']!=hashlib.sha256(json.dumps(snapshot['quality_packet'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest() or not raw or not raw[-1]['semantic_pass']:
            value['safety_pass']=False;value['joint_pass']=False;value['issues'].append('unverified_analysis_published')
    return value


def run_queue(root,directory,variant,queue):
    m=require_registered(root,directory);contract=load_contract(root/'configs/stage09_experiment.json')
    monitor=DiagnosisMonitor(contract.resources,directory/(variant+'-resources-'+queue[0][0]['group']+'.json'))
    if check_resources(contract.resources,monitor.sample(True))['status']!='passed':raise ValueError('quality_start_resource_gate_failed')
    ledger=AttemptLedger(root/'reports/generated/stage09/generation-ledger.json','route5a')
    with exclusive(root/'reports/generated/stage09/real-run.lock'):
        torch.set_num_threads(4);base=Stage06Service.from_config(root/SCORING['variants'][variant]);native=base.runner.provider
        monitor.sample(False);monitor.require()
        thread=threading.Thread(target=monitor.loop,daemon=True);thread.start()
        try:
            for case,key in queue:
                output=directory/'results'/f'{variant}-{key}.json'
                if output.exists():continue
                marker=directory/'started'/f'{variant}-{key}.json'
                if marker.exists():raise ValueError('incomplete attempt requires audit; no auto replay')
                attempts=read_json(ledger.path)['attempts']
                if len(attempts)>=144 or sum(a['phase']=='route5a' for a in attempts)>=66:raise ValueError('quality_budget_exhausted')
                monitor.require();snap=read_json(directory/'snapshots'/(case['case_id']+'.json'))
                write_json(marker,{'at':datetime.now(UTC).isoformat()})
                observer=ObservedProvider(native,ledger,directory/'http',m['content_sha256']+':'+variant+'-'+key,monitor,
                    expected_payload=snap['expected_prompt_payload'] if variant=='A' else snap['quality_packet'], provider_factory=type(native))
                base.runner.provider=observer
                service=Stage06Service(dataset_source=base.dataset_source,versions=base.versions,runner=base.runner,cost_ledger=base.cost_ledger)
                record,view,error=None,None,None;start=time.perf_counter()
                try:
                    actual=service.run(snap['request']);record=actual.model_dump(mode='json');view=project_investigation(actual).model_dump(mode='json')
                    if case['group']=='reg' and actual.approval and not (directory/'api-demo.json').exists():api_smoke(service,actual,directory)
                except Exception as exc:error=type(exc).__name__
                finally:service.close()
                result={'variant':variant,'case_id':key,'transaction_id':case['transaction_id'],'record':record,'public_view':view,
                    'error_type':error,'http_calls':observer.calls,'elapsed_ms':(time.perf_counter()-start)*1000,
                    'grade':result_grade(snap,record,observer.calls,view,m['decision_sha256'],variant)}
                write_json(output,result)
                print(json.dumps({'variant':variant,'case':key,'status':record['status'] if record else error,
                    'joint_pass':result['grade']['joint_pass'],'raw_semantic':result['grade']['raw_semantic_pass']}),flush=True)
                if not result['grade']['safety_pass']:raise ValueError('quality_unsafe_publication_stop')
        finally:
            monitor.stop.set();thread.join(timeout=5);base.close();del base;gc.collect()


def develop(root,directory):
    m=require_registered(root,directory);dev=[(c,c['case_id']) for c in m['cases'] if c['group']=='dev']
    run_queue(root,directory,'C',dev)
    rows=development_rows(root,directory,m)
    decision=selection_from_results(m,rows)
    write_json(directory/'selection.json',decision)
    print(json.dumps(decision,ensure_ascii=False),flush=True)


def development_rows(root,directory,m):
    dev=[c for c in m['cases'] if c['group']=='dev']
    parent=root/'reports/generated/stage09/route5a/20261008-quality-v1'
    return {v:[read_json((directory if v=='C' else parent)/'results'/f"{v}-{c['case_id']}.json") for c in dev] for v in ('A','B','C')}

def selection_from_results(m,rows):
    stats={v:{'source_pass':sum(r['grade']['quality_pass'] for r in rs),
        'joint_pass':sum(r['grade']['joint_pass'] for r in rs),
        'raw_semantic_pass':sum(bool(r['grade']['raw_semantic_pass']) for r in rs) if v!='A' else None,
        'median_ms':float(np.median([r['elapsed_ms'] for r in rs]))} for v,rs in rows.items()}
    selected=min(('B','C'),key=lambda v:(-stats[v]['joint_pass'],stats[v]['median_ms'],0 if v=='B' else 1))
    decision={'stats':stats,'selected':selected,'config':SCORING['variants'][selected],
        'selection_rule':SCORING['selection_rule'],'only_development_observed':True,
        'development_revisions':{'A':'v1 legacy','B':'v1 quality 0.5B','C':'v2 refined quality 4B'},
        'manifest_sha256':m['content_sha256'],'development_sha256':digest(rows)}
    decision['content_sha256']=digest(decision)
    return decision

def final_run(root,directory):
    m=require_registered(root,directory);selection=read_json(directory/'selection.json')
    if digest({k:v for k,v in selection.items() if k!='content_sha256'})!=selection['content_sha256']:raise ValueError('selection altered')
    rows=development_rows(root,directory,m)
    if selection != selection_from_results(m,rows):raise ValueError('selection is not bound to development results')
    queue=[(c,c['case_id']) for c in m['cases'] if c['group']=='reg']
    queue += [(next(c for c in m['cases'] if c['case_id']==key),key+'-repeat') for key in m['repeats']]
    queue += [(c,c['case_id']) for c in m['cases'] if c['group']=='new']
    run_queue(root,directory,selection['selected'],queue)


def check(root,directory):
    m=require_registered(root,directory);selection=read_json(directory/'selection.json');variant=selection['selected']
    results=[]
    for c in m['cases']:
        variants=('C',) if c['group']=='dev' else (variant,)
        keys=(c['case_id'],c['case_id']+'-repeat') if c['case_id'] in m['repeats'] else (c['case_id'],)
        for v in variants:
            for key in keys:
                path=directory/'results'/f'{v}-{key}.json'
                if not path.exists():raise ValueError('quality case not run:'+path.name)
                r=read_json(path);snap=read_json(directory/'snapshots'/(c['case_id']+'.json'))
                if r['transaction_id']!=c['transaction_id'] or r['case_id']!=key or r['variant']!=v:raise ValueError('result ID changed')
                fresh=result_grade(snap,r['record'],r['http_calls'],r['public_view'],m['decision_sha256'],v)
                if fresh!=r['grade']:raise ValueError('independent quality regrade differs')
                results.append(r)
    rows=development_rows(root,directory,m)
    if selection!=selection_from_results(m,rows):raise ValueError('selection differs from dev-only rule')
    attempts=read_json(root/'reports/generated/stage09/generation-ledger.json')['attempts'];phase=[a for a in attempts if a['phase']=='route5a']
    calls=[(r,c) for r in results for c in r['http_calls'] if 'ledger_sequence' in c]
    prior=read_json(root/'reports/generated/stage09/route5a/20261008-quality-v1/cases.json')
    current=[a for a in phase if a['case'].startswith(m['content_sha256']+':')]
    prior_calls=[a for a in phase if a['case'].startswith(prior['content_sha256']+':')]
    if len(calls)!=len(current) or len(prior_calls)!=15 or len(current)+len(prior_calls)!=len(phase) or len(phase)>66 or len(attempts)>144:raise ValueError('generation ledger mismatch')
    if len(set(c['ledger_sequence'] for _,c in calls))!=len(calls):raise ValueError('reused ledger sequence')
    by_seq={a['sequence']:a for a in phase}
    for r,c in calls:
        item=by_seq[c['ledger_sequence']]
        if item['case']!=m['content_sha256']+':'+r['variant']+'-'+r['case_id']:raise ValueError('ledger case differs')
        snap=read_json(directory/'snapshots'/(r['case_id'].removesuffix('-repeat')+'.json'))
        if c != read_json(directory/'http'/f"{r['variant']}-{r['case_id']}-http-{r['http_calls'].index(c)+1}.json"):
            raise ValueError('HTTP observation differs from result')
        req=c['request'];cfg=load_stage05_config(root/SCORING['variants'][r['variant']])
        if req['model']!=cfg.generation.model_id or req['options']!={'temperature':0,'num_predict':512} or req['stream'] is not False:raise ValueError('generation settings drift')
        payload=json.loads(req['prompt'].split('\nEVIDENCE_JSON:\n',1)[1])
        expected=snap['expected_prompt_payload'] if r['variant']=='A' else snap['quality_packet']
        schema=ReportDraft if r['variant']=='A' else QualityDraft
        if payload!=expected or req['format']!=schema.model_json_schema():raise ValueError('prompt packet/schema drift')
        if r['variant']!='A' and (not req['prompt'].startswith(QUALITY_PROMPT+'\nEVIDENCE_JSON:\n') or req.get('keep_alive')!=0):raise ValueError('quality prompt/runtime drift')
        if r['variant']=='C' and req.get('think') is not False:raise ValueError('4B thinking drift')
    groups={}
    for group in ('reg','new'):
        rows=[r for r in results if r['case_id'].startswith(group+'-') and not r['case_id'].endswith('-repeat')]
        groups[group]={'cases':len(rows),'source_pass':sum(r['grade']['quality_pass'] for r in rows),
            'raw_semantic_pass':sum(bool(r['grade']['raw_semantic_pass']) for r in rows),'joint_pass':sum(r['grade']['joint_pass'] for r in rows),
            'statuses':dict(Counter((r['record'] or {}).get('status','execution_error') for r in rows)),
            'median_ms':float(np.median([r['elapsed_ms'] for r in rows])), 'p95_ms':float(np.percentile([r['elapsed_ms'] for r in rows],95)),
            'issues':dict(Counter(issue for r in rows for raw in r['grade']['raw_judgments'] for issue in raw['issues']))}
    repeated=[]
    for key in m['repeats']:
        a=read_json(directory/'results'/f'{variant}-{key}.json');b=read_json(directory/'results'/f'{variant}-{key}-repeat.json')
        reports=[((r['record'] or {}).get('result',{}).get('report_run') or {}).get('report') for r in (a,b)]
        repeated.append({'case':key,'same_report':reports[0] is not None and stable(reports[0])==stable(reports[1])})
    summary={'manifest_sha256':m['content_sha256'],'selection':selection,'groups':groups,'repeats':repeated,
        'all_results':len(results),'prior_development_results':15,'not_run':0,'unique_transactions':45,'new_independent_transactions':10,
        'phase_attempts':len(phase),'global_attempts':len(attempts),'safety_failures':sum(not r['grade']['safety_pass'] for r in results),
        'prompt_tokens':sum(c.get('response',{}).get('prompt_eval_count',0) or 0 for _,c in calls),
        'output_tokens':sum(c.get('response',{}).get('eval_count',0) or 0 for _,c in calls),'paid_calls':0,
        'human_review':'pending','free_text_reasoning_validated':False,'engineering_measurement_complete':True}
    write_json(directory/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False))
    return summary
