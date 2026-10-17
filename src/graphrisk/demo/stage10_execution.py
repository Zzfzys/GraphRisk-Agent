"""Registered real Stage 10 experiment; no training, paid calls, or automatic replay."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import socket
import threading
import time

import httpx
import psutil

from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.quality_contracts import QualityDraft
from graphrisk.agent.quality_evidence import investigation_packet
from graphrisk.agent.quality_reporting import QUALITY_PROMPT, OllamaQualityProvider
from graphrisk.agent.reporting import _eligible_hits, _prompt_payload
from graphrisk.agent.workflow import Stage05GraphWorkflow
from graphrisk.demo.stage09_evaluation import digest, exclusive, read_json, write_json
from graphrisk.demo.stage09_quality import result_grade
from graphrisk.demo.stage10_measurement import (
    PROTOCOL, GuardedTransport, Sample, binding, quality_summary, summarize,
)
from graphrisk.demo.stage10_registration import checked, check, inventory, ledger_for, seal
from graphrisk.demo.stage10_telemetry import Context, MeasurementRunner, Resources
from graphrisk.serving.investigation_service import Stage06Service


FROZEN = Path('reports/generated/stage10/route1/20261017-frozen-v1')
ROUTE1 = Path('reports/generated/stage10/route1/20261017-contract-v1')
PROSPECTIVE = Path('reports/generated/stage09/route5b/20261015-coverage-verification-v3/frozen-cases.json')
DIAGNOSTIC = Path('reports/generated/stage10/route2/20261017-real-v1')
SPEC = dict(version='stage10-execution-v2', fast_samples_per_endpoint=100,
            fast_endpoints=['health', 'coverage'], concurrency=1, workers=1,
            prospective_cases=5, tool_cases=10, queue='original_manifest_order',
            automatic_case_retries=0, minimum_llm_available_gib=3.,
            minimum_cpu_available_gib=1.5, maximum_cpu_rss_mib=1280.,
            monitor_interval_seconds=.5, generation_keep_alive=0,
            incomplete_send='stop_for_audit', paid_calls=0, real_fits=0)


def utc():
    return datetime.now(UTC).isoformat()


def state_paths(output):
    return output/'execution.json', output/'prepared.json'


def register(source, root, output):
    """Seal the complete execution source BEFORE even collecting tool outcomes."""
    if output.exists():
        raise ValueError('new_execution_directory_required')
    prior = check(source, root, root/FROZEN)
    if prior['ledger']['appended_attempts'] or prior['ledger']['unresolved']:
        raise ValueError('existing_stage10_requests_require_original_execution')
    files = inventory(source)
    appendix = checked(root/ROUTE1/'verification-v2/source-appendix.json')
    # Route1's final appendix must still agree, not just the original manifest.
    for name, wanted in appendix['source_inventory'].items():
        if files.get(name) != wanted:
            raise ValueError('route1_appendix_source_changed')
    diagnostic = None
    if (root/DIAGNOSTIC/'prepared.json').exists():
        old = checked(root/DIAGNOSTIC/'execution.json')
        prepared = checked(root/DIAGNOSTIC/'prepared.json')
        if old['manifest_sha256'] != prior['manifest_sha256'] or prepared['new_real_calls']:
            raise ValueError('diagnostic_is_not_same_zero_generation_experiment')
        for name, wanted in old['source_inventory'].items():
            if binding(root/DIAGNOSTIC/'source-snapshot'/name)['sha256'] != wanted:
                raise ValueError('diagnostic_source_archive_changed')
        diagnostic = dict(path=DIAGNOSTIC.as_posix(), execution=binding(root/DIAGNOSTIC/'execution.json'),
            prepared=binding(root/DIAGNOSTIC/'prepared.json'),
            reason='replace expensive full-machine RSS enumeration before any model output; same cases/budget')
    output.mkdir(parents=True)
    for name in files:
        target = output/'source-snapshot'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source/name, target)
        if binding(target) != binding(source/name):
            raise ValueError('source_changed_during_snapshot')
    value = seal(dict(spec=SPEC, created_at=utc(), manifest_sha256=prior['manifest_sha256'],
                      source_inventory=files, source_inventory_sha256=digest(files),
                      parent_acceptance=binding(root/ROUTE1/'acceptance.json'),
                      final_route1_appendix=binding(root/ROUTE1/'verification-v2/source-appendix.json'),
                      prospective_inputs=binding(root/PROSPECTIVE),
                      observer_fix_parent=diagnostic,
                      ledger_at_registration=prior['ledger']))
    write_json(output/'execution.json', value)
    return dict(status='registered', source_files=len(files), new_real_calls=0,
                execution_sha256=value['content_sha256'])


def validate(source, root, output):
    value = checked(output/'execution.json')
    if value['spec'] != SPEC or inventory(source) != value['source_inventory']:
        raise ValueError('execution_source_or_spec_changed')
    if parent := value.get('observer_fix_parent'):
        for name in ('execution','prepared'):
            if binding(root/parent['path']/(name+'.json')) != parent[name]:
                raise ValueError('diagnostic_parent_changed')
    prior = check(source, root, root/FROZEN)
    if (prior['manifest_sha256'] != value['manifest_sha256']
            or binding(root/PROSPECTIVE) != value['prospective_inputs']
            or binding(root/ROUTE1/'acceptance.json') != value['parent_acceptance']
            or binding(root/ROUTE1/'verification-v2/source-appendix.json') != value['final_route1_appendix']):
        raise ValueError('execution_parent_drift')
    return value, checked(root/FROZEN/'manifest.json')


class ResourceMonitor:
    """Sample own RSS, system headroom and Ollama processes separately; never kill apps."""
    def __init__(self, path):
        self.path = path
        self.rows = []
        self.phase = 'cpu'
        self.violations = []
        self.stop = threading.Event()
        self.model_roots = []

    def discover_models(self):
        # Full Windows process enumeration can take seconds. Do it once outside
        # measured operations; children() uses the OS parent map to discover a
        # newly loaded runner without fetching every unrelated process's memory.
        for proc in psutil.process_iter(['pid', 'name']):
            if 'ollama' in (proc.info['name'] or '').lower():
                self.model_roots.append(proc)

    def model_processes(self):
        found = {}
        for root in self.model_roots:
            try:
                for proc in [root, *root.children(recursive=True)]:
                    found[proc.pid] = proc
            except psutil.Error:
                continue
        return list(found.values())

    def sample(self):
        row = dict(at=utc(), phase=self.phase, service_pid=psutil.Process().pid,
                   service_rss_mib=psutil.Process().memory_info().rss/2**20,
                   available_gib=psutil.virtual_memory().available/2**30,
                   model_processes=[], gpu_used_mib=None)
        for proc in self.model_processes():
            try:
                row['model_processes'].append(dict(pid=proc.pid, name=proc.name(),
                    rss_mib=proc.memory_info().rss/2**20))
            except (psutil.Error, TypeError, AttributeError):
                continue
        floor = 3. if self.phase == 'llm' else 1.5
        if row['available_gib'] < floor or row['service_rss_mib'] > 1280.:
            self.violations.append(dict(at=row['at'], phase=self.phase, reason='resource_floor_or_rss'))
        self.rows.append(row)
        return row

    def require(self):
        self.sample()
        if self.violations:
            self.save()
            raise MemoryError('registered_resource_gate_reached')

    def probe(self):
        r = self.sample()
        return Resources(process_rss_mib=r['service_rss_mib'],
                         system_available_gib=r['available_gib'], process_scope='service_process')

    def loop(self):
        while not self.stop.wait(.5):
            self.sample()

    def save(self):
        write_json(self.path, dict(samples=self.rows, violations=self.violations,
                   peak_scope='sampled_process_RSS_not_machine_peak', gpu_status='not_measured'))

    def __enter__(self):
        self.discover_models()
        self.require()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(5)
        self.sample()
        self.save()


@contextmanager
def live_http(service):
    """One new loopback server on an OS-assigned port; close only this server."""
    import uvicorn
    from api.main import create_app
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(32)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(lambda: service), log_level='warning',
        access_log=False, workers=1))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    end = time.monotonic()+30
    while not server.started and thread.is_alive() and time.monotonic() < end:
        time.sleep(.05)
    if not server.started:
        server.should_exit = True
        thread.join(5)
        sock.close()
        raise RuntimeError('dedicated_http_start_failed')
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=330., trust_env=False) as client:
            yield client, port
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


def context(execution, case, component, profile, run_key=None):
    return Context(case_id=run_key or case['case_id'], experiment_sha256=execution['manifest_sha256'],
        execution_sha256=execution['content_sha256'], input_sha256=digest(case['request']),
        profile_sha256=profile, scope='real', component=component,
        model_id=PROTOCOL['model_id'] if component == 'agent' else None,
        model_revision=PROTOCOL['model_revision'] if component == 'agent' else None,
        prompt_sha256=digest(QUALITY_PROMPT) if component == 'agent' else None,
        schema_sha256=digest(QualityDraft.model_json_schema()) if component == 'agent' else None)


def collect_snapshot(graph):
    if graph.investigation_report is None or graph.policy_retrieval is None:
        raise ValueError('actual_tool_snapshot_incomplete')
    hits = _eligible_hits(graph)
    return dict(request=graph.request.model_dump(mode='json'),
        investigation_report=graph.investigation_report.model_dump(mode='json'),
        investigation_audit=graph.investigation_audit.model_dump(mode='json'),
        policy_retrieval=graph.policy_retrieval.model_dump(mode='json'),
        eligible_hits=[h.model_dump(mode='json') for h in hits],
        expected_prompt_payload=_prompt_payload(graph, hits), quality_packet=investigation_packet(graph))


def prepare(source, root, output):
    ex, manifest = validate(source, root, output)
    if (output/'prepared.json').exists() or (output/'prepare-started.json').exists():
        raise ValueError('preparation_already_started_do_not_overwrite')
    write_json(output/'prepare-started.json', dict(at=utc(), execution_sha256=ex['content_sha256']))
    ledger = ledger_for(root, manifest)
    before = ledger.state()
    config = load_stage05_config(root/'configs/stage09_coverage.yaml')
    timer = MeasurementRunner(attempts=lambda: ledger.state()['appended_attempts'])
    prepared = dict(execution_sha256=ex['content_sha256'], snapshots={}, prospective=[], fast=[],
                    service_load_ms=None, error_type=None, new_real_calls=0)
    service = None
    try:
        with ResourceMonitor(output/'prepare-resources.json') as monitor:
            start = time.perf_counter()
            service = Stage06Service.from_config(root/'configs/stage09_coverage.yaml')
            prepared['service_load_ms'] = (time.perf_counter()-start)*1000
            monitor.require()
            timer.resources = monitor.probe
            workflow = Stage05GraphWorkflow(service.runner.investigation, service.runner.retriever,
                dataset_source='ieee_cis', limits=service.runner.limits, top_k=service.runner.top_k)
            for case in manifest['cases']:
                monitor.require()
                graph = timer.measure(context(ex, case, 'tool', config.model_policy_sha256),
                                      lambda c=case: workflow.run(c['request']))
                snap = collect_snapshot(graph)
                write_json(output/'snapshots'/(case['case_id']+'.json'), seal(snap))
                prepared['snapshots'][case['case_id']] = binding(output/'snapshots'/(case['case_id']+'.json'))
            # Frozen, separately identified historical new-event replays. No fitting.
            for n, case in enumerate(checked(root/PROSPECTIVE)['cases']):
                monitor.require()
                result = timer.measure(context(ex, manifest['cases'][n], 'prospective', config.model_policy_sha256),
                    lambda c=case: service.runner.investigation.graph_service.prospective(c['event'], guard=monitor.require))
                if case.get('source_id') and any(abs(result[k]-case[e]) > 1e-6 for k, e in
                        [('risk_score','expected_primary'), ('graph_risk_score','expected_graph')]):
                    raise ValueError('frozen_weight_replay_mismatch')
                write_json(output/'prospective'/f'{n+1:02d}.json', seal(dict(input=case, result=result)))
                prepared['prospective'].append(dict(case=n+1, passed=True,
                    tree_worker_peak_rss_mib=result['tree_worker_peak_rss_mib']))
            with live_http(service) as (client, port):
                prepared['port'] = port
                for endpoint in SPEC['fast_endpoints']:
                    rows = []
                    url = '/api/v1/health' if endpoint == 'health' else '/api/v1/coverage/'+manifest['cases'][0]['transaction_id']
                    for _ in range(100):
                        monitor.require()
                        start = time.perf_counter()
                        status, outcome = None, 'transport_error'
                        try:
                            response = client.get(url)
                            status = response.status_code
                            outcome = 'ok' if status == 200 else 'http_error'
                        except httpx.TimeoutException:
                            outcome = 'timeout'
                        except httpx.HTTPError:
                            pass
                        rows.append(Sample(case_id='case-01', input_sha256=digest(url), protocol_sha256=digest(PROTOCOL),
                            scope='real', component='api_read', outcome=outcome,
                            elapsed_ms=(time.perf_counter()-start)*1000, http_status=status))
                    prepared['fast'].append(dict(endpoint=endpoint, samples=[s.model_dump() for s in rows],
                                                 summary=summarize(rows,100)))
    except Exception as error:
        prepared['error_type'] = type(error).__name__
        raise
    finally:
        if service:
            service.close()
        prepared['envelopes'] = [e.model_dump(mode='json') for e in timer.records]
        prepared['ledger_after'] = ledger.state()
        prepared['new_real_calls'] = prepared['ledger_after']['appended_attempts']-before['appended_attempts']
        write_json(output/'prepared.json', seal(prepared))
    return dict(status='prepared', tool_cases=len(prepared['snapshots']), prospective=len(prepared['prospective']),
                api_samples=sum(len(r['samples']) for r in prepared['fast']), new_real_calls=prepared['new_real_calls'])


def completed_rows(output, manifest):
    rows = []
    for item in manifest['queue']:
        key = item['run_key']
        result = output/'results'/(key+'.json')
        marker = output/'started'/(key+'.json')
        if result.exists():
            row = checked(result)
            if row['run_key'] != key:
                raise ValueError('result_key_drift')
            rows.append(row)
        elif marker.exists():
            raise ValueError('incomplete_attempt_requires_audit_no_auto_replay')
    return rows


def evaluate(snapshot, record, calls, view, decision):
    # Independent existing fact/citation/approval evaluator plus raw judgment grader.
    normalized = [{**c, 'ledger_sequence': c['metadata']['attempt_sequence']}
                  for c in calls if c['metadata']['attempt_sequence'] is not None]
    return result_grade(snapshot, record, normalized, view, decision, 'C')


def run(source, root, output):
    ex, manifest = validate(source, root, output)
    prepared = checked(output/'prepared.json')
    if prepared['error_type'] or prepared['new_real_calls'] or len(prepared['snapshots']) != 10:
        raise ValueError('non_llm_preparation_not_accepted')
    for key, wanted in prepared['snapshots'].items():
        if binding(output/'snapshots'/(key+'.json')) != wanted:
            raise ValueError('actual_fact_snapshot_changed')
    ledger = ledger_for(root, manifest)
    done = {r['run_key'] for r in completed_rows(output, manifest)}
    queue = [q for q in manifest['queue'] if q['run_key'] not in done]
    if not queue:
        return report(source, root, output)
    if ledger.state()['unresolved']:
        raise ValueError('uncertain_send_requires_audit')
    config = load_stage05_config(root/'configs/stage09_coverage.yaml')
    session = str(time.time_ns())
    with exclusive(root/'reports/generated/stage10/real-execution.lock'):
        with ResourceMonitor(output/('real-resources-'+session+'.json')) as monitor:
            monitor.phase = 'llm'
            monitor.require()
            base = Stage06Service.from_config(root/'configs/stage09_coverage.yaml')
            try:
                monitor.require()
                with live_http(base) as (client, port):
                    for item in queue:
                        monitor.require()
                        # Check complete registered source and all inputs before each send.
                        validate(source, root, output)
                        key = item['run_key']
                        case = next(c for c in manifest['cases'] if c['case_id'] == item['case_id'])
                        snap = checked(output/'snapshots'/(case['case_id']+'.json'))
                        with httpx.Client(timeout=5, trust_env=False) as observation:
                            tags = observation.get('http://127.0.0.1:11434/api/tags').raise_for_status().json()
                            if not any(m.get('name') == PROTOCOL['model_id'] and m.get('digest') == PROTOCOL['model_revision']
                                       for m in tags['models']):
                                raise ValueError('actual_model_digest_mismatch')
                            ps = observation.get('http://127.0.0.1:11434/api/ps').raise_for_status().json()
                        resident = any(m.get('digest') == PROTOCOL['model_revision'] for m in ps['models'])
                        guard = GuardedTransport(None, ledger, key, digest(snap['quality_packet']),
                            resource_guard=monitor.require, residency_before=resident)
                        timer = MeasurementRunner(resources=monitor.probe, attempts=lambda: ledger.state()['appended_attempts'])
                        record = view = None
                        error_type = None
                        status = None
                        with httpx.Client(transport=guard, trust_env=False) as model_client:
                            base.runner.provider = OllamaQualityProvider(config.generation, model_client)
                            write_json(output/'started'/(key+'.json'), dict(at=utc(), execution_sha256=ex['content_sha256'],
                                prepared_sha256=prepared['content_sha256'], packet_sha256=digest(snap['quality_packet']),
                                residency_observation=ps, port=port, ledger=ledger.state()))
                            try:
                                response = timer.measure(context(ex, case, 'agent', config.model_policy_sha256, key),
                                    lambda: client.post('/api/v1/investigations', json=case['request']))
                                status = response.status_code
                                view = response.json()
                                if view.get('run_id'):
                                    record = base.get(view['run_id']).model_dump(mode='json')
                            except Exception as error:
                                error_type = type(error).__name__
                        grade = evaluate(snap, record, guard.private_calls, view, base.versions.model_decision_sha256)
                        row = seal(dict(run_key=key, case_id=case['case_id'], execution_sha256=ex['content_sha256'],
                            prepared_sha256=prepared['content_sha256'], record=record, public_view=view,
                            error_type=error_type, http_status=status, http_calls=guard.private_calls,
                            model_samples=[s.model_dump() for s in guard.samples],
                            envelopes=[e.model_dump(mode='json') for e in timer.records], grade=grade,
                            ledger=ledger.state(), resource_contract_violated=bool(monitor.violations)))
                        write_json(output/'results'/(key+'.json'), row)
                        monitor.save()
                        print(json.dumps(dict(case=key, status=record['status'] if record else error_type,
                            joint_pass=grade['joint_pass'], safety_pass=grade['safety_pass'],
                            actual_requests=len(guard.private_calls), remaining=ledger.state()['remaining'])), flush=True)
                        if not grade['safety_pass']:
                            raise ValueError('unsafe_publication_stop')
                        monitor.require()
            finally:
                base.close()
    return report(source, root, output)


def report(source, root, output):
    ex, manifest = validate(source, root, output)
    config = load_stage05_config(root/'configs/stage09_coverage.yaml')
    decision = checked(config.resolve_path(config.model_policy))['decision_sha256']
    rows = completed_rows(output, manifest)
    samples = []
    quality = []
    for row in rows:
        if row['execution_sha256'] != ex['content_sha256']:
            raise ValueError('result_execution_binding_drift')
        snap = checked(output/'snapshots'/(row['case_id']+'.json'))
        if evaluate(snap,row['record'],row['http_calls'],row['public_view'],decision) != row['grade']:
            raise ValueError('independent_regrade_changed')
        quality.append(dict(run_key=row['run_key'],joint_pass=bool(row['grade']['joint_pass']),
                            unsafe=not row['grade']['safety_pass']))
        samples += [Sample.model_validate(s) for s in row['model_samples']]
        samples += [http_sample(e['sample'],row['http_status']) for e in row['envelopes']]
    done = {r['run_key'] for r in rows}
    for item in manifest['queue']:
        if item['run_key'] not in done:
            case = next(c for c in manifest['cases'] if c['case_id'] == item['case_id'])
            samples.append(Sample(case_id=item['run_key'], input_sha256=digest(case['request']),
                protocol_sha256=digest(PROTOCOL), scope='real', component='agent', outcome='not_run'))
    value = seal(dict(execution_sha256=ex['content_sha256'], quality=quality_summary(
        [c['case_id'] for c in manifest['cases']],quality), performance=summarize(samples,12),
        completed_runs=len(rows), planned_runs=12, ledger=ledger_for(root,manifest).state(),
        resource_violations=sum(r['resource_contract_violated'] for r in rows),
        paid_calls=0, new_real_fits=0, results={r['run_key']:binding(output/'results'/(r['run_key']+'.json')) for r in rows}))
    # A report is a new timestamped receipt; never overwrite previous conclusions.
    write_json(output/('summary-'+str(time.time_ns())+'.json'),value)
    return value


def http_sample(sample, status):
    """Envelope measures return/raise; this report additionally checks HTTP status."""
    value = Sample.model_validate(sample)
    if status is not None:
        value = value.model_copy(update={'http_status':status,
            'outcome':'ok' if 200 <= status < 300 else 'http_error'})
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['register','check','prepare','run','report'])
    parser.add_argument('--runtime-root', type=Path, default=Path('E:/GraphRisk-Agent'))
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    source, root, output = Path(__file__).resolve().parents[3], args.runtime_root.resolve(), args.output_dir.resolve()
    if not output.is_relative_to(root/'reports/generated/stage10/route2') or output == root/'reports/generated/stage10/route2':
        parser.error('dedicated ignored route2 output required')
    if args.action == 'check':
        ex, manifest = validate(source,root,output)
        result = dict(status='passed',execution_sha256=ex['content_sha256'],ledger=ledger_for(root,manifest).state())
    else:
        result = globals()[args.action](source,root,output)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
