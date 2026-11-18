from graphrisk.demo.stage10_measurement import PROTOCOL, Sample
from graphrisk.demo.stage09_evaluation import digest
from graphrisk.demo.stage10_results import timing_summary
from graphrisk.demo import stage10_results
import pytest
import hashlib


def observation(key,component):
    return Sample(case_id=key,input_sha256='a'*64,protocol_sha256=digest(PROTOCOL),
        scope='real',component=component,outcome='ok',elapsed_ms=100.).model_dump()


def test_twelve_runs_are_twenty_four_component_records_not_twenty_four_cases():
    queue=[{'run_key':f'case-{i:02d}'} for i in range(1,11)]
    queue += [{'run_key':'case-01-repeat'},{'run_key':'case-06-repeat'}]
    rows=[dict(run_key=q['run_key'],model_samples=[observation(q['run_key'],'ollama_http')],
        envelopes=[{'sample':observation(q['run_key'],'agent')}],http_status=503 if i==3 else 201)
        for i,q in enumerate(queue)]
    result=timing_summary(rows,queue)
    assert result['planned_runs']==12 and result['observation_records']==24
    agent=next(g for g in result['groups'] if g['component']=='agent')
    assert agent['errors']==1 and agent['ok']==11 and agent['p99_ms'] is None
    model=next(g for g in result['groups'] if g['component']=='ollama_http')
    assert model['ok']==12 and model['errors']==0


def test_unrequested_cases_keep_both_component_slots_without_zero_latency():
    result=timing_summary([], [{'run_key':'case-01'}])
    assert result['observation_records']==2
    assert all(g['timed_n']==0 and g['p50_ms'] is None for g in result['groups'])


def test_retry_observation_is_retained_in_its_own_component():
    key='case-01'
    rows=[dict(run_key=key,model_samples=[observation(key,'ollama_http')]*2,
        envelopes=[{'sample':observation(key,'agent')}],http_status=201)]
    result=timing_summary(rows,[{'run_key':key}])
    assert result['planned_runs']==1 and result['observation_records']==3
    assert sum(g['timed_n'] for g in result['groups'])==3


def test_readonly_benchmark_rejects_overwrite_and_outside_path(tmp_path,monkeypatch):
    monkeypatch.setattr(stage10_results,'read_results',lambda *args:{'ledger':{},'analysis_source':{}})
    with pytest.raises(ValueError,match='new_readonly'):
        stage10_results.benchmark(tmp_path,tmp_path,tmp_path,tmp_path/'outside')
    used=tmp_path/'readonly-repeats/old';used.mkdir(parents=True)
    with pytest.raises(ValueError,match='new_readonly'):
        stage10_results.benchmark(tmp_path,tmp_path,tmp_path,used)


def test_archive_under_generated_is_checked_by_explicit_paths(tmp_path):
    archive=tmp_path/'reports/generated/snapshot'
    module=archive/'src/module.py';module.parent.mkdir(parents=True);module.write_bytes(b'original')
    files={'src/module.py':hashlib.sha256(b'original').hexdigest()}
    stage10_results.verify_archive(archive,files)
    module.write_bytes(b'changed')
    with pytest.raises(ValueError,match='archive_changed'):
        stage10_results.verify_archive(archive,files)
