"""Post-measurement checks and summaries. This entry point CANNOT send model requests.

The measured v2 executor and every outcome stay byte-identical. Its final summary
mistook 24 component observations for 12 observations. This separate reader fixes
that bookkeeping without changing case selection, grading, or measured execution.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import time
import httpx

from graphrisk.agent.config import load_stage05_config
from graphrisk.demo import stage10_execution as execution
from graphrisk.demo.stage09_evaluation import digest, write_json
from graphrisk.demo.stage10_measurement import PROTOCOL, Sample, binding, quality_summary, summarize
from graphrisk.demo.stage10_registration import checked, check, check_registered_source, inventory, ledger_for, seal


ANALYSIS_FILES = {'src/graphrisk/demo/stage10_results.py', 'scripts/read_stage10_results.py',
                  'tests/unit/test_stage10_results.py'}


def timing_summary(rows, queue):
    observations = []
    for row in rows:
        observations += [Sample.model_validate(s) for s in row['model_samples']]
        observations += [execution.http_sample(e['sample'],row['http_status']) for e in row['envelopes']]
    completed = {row['run_key'] for row in rows}
    for item in queue:
        if item['run_key'] not in completed:
            for component in ('agent','ollama_http'):
                observations.append(Sample(case_id=item['run_key'],input_sha256=digest(item),
                    protocol_sha256=digest(PROTOCOL),scope='real',component=component,outcome='not_run'))
    # expected is the number of *observation records*, never unique transactions.
    # Agent HTTP and Ollama HTTP overlap in time and must stay separate groups.
    value = summarize(observations,len(observations))
    # A pooled success rate across nested HTTP components is not a case metric.
    value.pop('completed_ok',None)
    value.pop('success_rate_of_expected',None)
    return dict(planned_runs=len(queue), observation_records=len(observations),
                denominator_note='two overlapping component measurements per run; not independent cases', **value)


def verify_archive(archive, files):
    # source_inventory deliberately excludes generated/; archives are IN that
    # directory, so verify explicit registered paths instead of re-discovery.
    for name, wanted in files.items():
        if binding(archive/name)['sha256'] != wanted:
            raise ValueError('measured_source_archive_changed')


def validate_saved(source, root, output):
    ex=checked(output/'execution.json')
    prior=check(source,root,root/execution.FROZEN)
    manifest=checked(root/execution.FROZEN/'manifest.json')
    if ex['spec'] != execution.SPEC or ex['manifest_sha256'] != prior['manifest_sha256']:
        raise ValueError('saved_execution_contract_changed')
    additions = check_registered_source(ex['source_inventory'],inventory(source))
    if set(additions) != ANALYSIS_FILES:
        raise ValueError('unexpected_postmeasurement_source_additions')
    verify_archive(output/'source-snapshot',ex['source_inventory'])
    for name,path in [('parent_acceptance',root/execution.ROUTE1/'acceptance.json'),
                      ('final_route1_appendix',root/execution.ROUTE1/'verification-v2/source-appendix.json'),
                      ('prospective_inputs',root/execution.PROSPECTIVE)]:
        if binding(path) != ex[name]:
            raise ValueError('saved_parent_changed')
    if parent := ex.get('observer_fix_parent'):
        for name in ('execution','prepared'):
            if binding(root/parent['path']/(name+'.json')) != parent[name]:
                raise ValueError('saved_diagnostic_parent_changed')
    return ex,manifest,additions


def read_results(source, root, output):
    ex,manifest,additions=validate_saved(source,root,output)
    ledger = ledger_for(root,manifest)
    before = ledger.state()
    prepared = checked(output/'prepared.json')
    if prepared['execution_sha256'] != ex['content_sha256'] or prepared['new_real_calls']:
        raise ValueError('prepared_binding_changed')
    for name, wanted in prepared['snapshots'].items():
        if binding(output/'snapshots'/(name+'.json')) != wanted:
            raise ValueError('tool_snapshot_changed')
    config = load_stage05_config(root/'configs/stage09_coverage.yaml')
    decision = checked(config.resolve_path(config.model_policy))['decision_sha256']
    rows = execution.completed_rows(output,manifest)
    quality = []
    for row in rows:
        if (row['execution_sha256'] != ex['content_sha256']
                or row['prepared_sha256'] != prepared['content_sha256']):
            raise ValueError('measured_result_binding_changed')
        snapshot = checked(output/'snapshots'/(row['case_id']+'.json'))
        grade = execution.evaluate(snapshot,row['record'],row['http_calls'],row['public_view'],decision)
        if grade != row['grade']:
            raise ValueError('independent_regrade_changed')
        quality.append(dict(run_key=row['run_key'],joint_pass=bool(grade['joint_pass']),unsafe=not grade['safety_pass']))
    after = ledger.state()
    if before != after:
        raise ValueError('ledger_changed_during_read_only_analysis')
    result = seal(dict(version='stage10-results-v1', analysis_only_no_generation=True,
        execution_sha256=ex['content_sha256'], analysis_source=additions,
        frozen_execution_source_sha256=ex['source_inventory_sha256'],
        quality=quality_summary([c['case_id'] for c in manifest['cases']],quality),
        performance=timing_summary(rows,manifest['queue']), completed_runs=len(rows),planned_runs=len(manifest['queue']),
        ledger=after, new_requests_during_analysis=0,paid_calls=0,new_real_fits=0,
        result_bindings={r['run_key']:binding(output/'results'/(r['run_key']+'.json')) for r in rows},
        prepared_binding=binding(output/'prepared.json'),
        note='execution completed all saved calls before original summary error; no measured result repaired'))
    return result


def benchmark(source, root, output, repeat_dir):
    """Repeat ONLY actual tools, frozen CPU weights and read APIs, using measured code."""
    current=read_results(source,root,output)
    repeat_dir=Path(repeat_dir).resolve()
    base=(output/'readonly-repeats').resolve()
    if not repeat_dir.is_relative_to(base) or repeat_dir==base or repeat_dir.exists():
        raise ValueError('new_readonly_repeat_subdirectory_required')
    repeat_dir.mkdir(parents=True)
    shutil.copyfile(output/'execution.json',repeat_dir/'execution.json')
    ex,manifest,_=validate_saved(source,root,output)
    before=current['ledger']
    timer=execution.MeasurementRunner()
    config=load_stage05_config(root/'configs/stage09_coverage.yaml')
    value=dict(mode='non_llm_repeat_not_new_agent_quality',tools=[],prospective=[],fast=[],error_type=None)
    service=None
    try:
        with execution.ResourceMonitor(repeat_dir/'resources.json') as monitor:
            timer.resources=monitor.probe
            service=execution.Stage06Service.from_config(root/'configs/stage09_coverage.yaml')
            workflow=execution.Stage05GraphWorkflow(service.runner.investigation,service.runner.retriever,
                dataset_source='ieee_cis',limits=service.runner.limits,top_k=service.runner.top_k)
            for case in manifest['cases']:
                monitor.require()
                graph=timer.measure(execution.context(ex,case,'tool',config.model_policy_sha256),
                    lambda c=case:workflow.run(c['request']))
                snapshot=execution.collect_snapshot(graph)
                write_json(repeat_dir/'snapshots'/(case['case_id']+'.json'),seal(snapshot))
                value['tools'].append(case['case_id'])
            for n,case in enumerate(checked(root/execution.PROSPECTIVE)['cases']):
                monitor.require()
                result=timer.measure(execution.context(ex,manifest['cases'][n],'prospective',config.model_policy_sha256),
                    lambda c=case:service.runner.investigation.graph_service.prospective(c['event'],guard=monitor.require))
                if result['fold']!=case['expected_fold'] or result['label_read'] or result['history_mutated']:
                    raise ValueError('readonly_weight_contract_changed')
                if case.get('source_id') and any(abs(result[k]-case[e])>1e-6 for k,e in
                        [('risk_score','expected_primary'),('graph_risk_score','expected_graph')]):
                    raise ValueError('readonly_weight_replay_mismatch')
                value['prospective'].append(dict(case=n+1,result=result))
            with execution.live_http(service) as (client,port):
                value['port']=port
                for endpoint in execution.SPEC['fast_endpoints']:
                    url='/api/v1/health' if endpoint=='health' else '/api/v1/coverage/'+manifest['cases'][0]['transaction_id']
                    samples=[]
                    for _ in range(100):
                        monitor.require()
                        start=time.perf_counter();status=None;outcome='transport_error'
                        try:
                            response=client.get(url);status=response.status_code
                            outcome='ok' if status==200 else 'http_error'
                        except httpx.TimeoutException:outcome='timeout'
                        except httpx.HTTPError:pass
                        samples.append(Sample(case_id='case-01',input_sha256=digest(url),protocol_sha256=digest(PROTOCOL),
                            scope='real',component='api_read',outcome=outcome,http_status=status,
                            elapsed_ms=(time.perf_counter()-start)*1000))
                    value['fast'].append(dict(endpoint=endpoint,samples=[s.model_dump() for s in samples],
                        summary=summarize(samples,100)))
        after=read_results(source,root,output)['ledger']
        if before != after:
            raise ValueError('generation_changed_in_non_llm_repeat')
        return dict(status='passed',mode='non_llm_repeat_only',tools=len(value['tools']),
                    prospective=len(value['prospective']),api_samples=sum(len(g['samples']) for g in value['fast']),new_real_calls=0)
    except Exception as error:
        value['error_type']=type(error).__name__
        raise
    finally:
        if service:service.close()
        value['envelopes']=[e.model_dump(mode='json') for e in timer.records]
        write_json(repeat_dir/'benchmark.json',seal(value))
        write_json(repeat_dir/'repeat-binding.json',seal(dict(parent_execution=binding(output/'execution.json'),
            analysis_source=current['analysis_source'],ledger_before=before,
            mode='non_llm_repeat_not_new_agent_quality')))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('check','report','benchmark'))
    parser.add_argument('--runtime-root',type=Path,default=Path('E:/GraphRisk-Agent'))
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--repeat-dir',type=Path)
    args=parser.parse_args()
    source,root,output=Path(__file__).resolve().parents[3],args.runtime_root.resolve(),args.output_dir.resolve()
    if not output.is_relative_to(root/'reports/generated/stage10/route2'):
        parser.error('registered route2 directory required')
    if args.action=='benchmark':
        if args.repeat_dir is None:
            parser.error('benchmark requires new --repeat-dir under output/readonly-repeats')
        result=benchmark(source,root,output,args.repeat_dir)
    else:
        result=read_results(source,root,output)
    if args.action=='report':
        write_json(output/('analysis-summary-'+str(time.time_ns())+'.json'),result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
