from types import SimpleNamespace

import pytest

from graphrisk.demo import stage10_execution as execution
from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.demo.stage10_registration import seal


def fake_process(rss):
    return SimpleNamespace(pid=7, memory_info=lambda: SimpleNamespace(rss=rss*2**20))


@pytest.mark.parametrize('phase,available,rss,stops', [
    ('cpu',1.6,1279,False), ('cpu',1.4,500,True), ('cpu',4.,1281,True),
    ('llm',3.1,500,False), ('llm',2.9,500,True),
])
def test_resource_gates_keep_cpu_and_llm_contracts(tmp_path,monkeypatch,phase,available,rss,stops):
    monkeypatch.setattr(execution.psutil,'Process',lambda:fake_process(rss))
    monkeypatch.setattr(execution.psutil,'virtual_memory',lambda:SimpleNamespace(available=available*2**30))
    monkeypatch.setattr(execution.psutil,'process_iter',lambda _:iter([]))
    monitor=execution.ResourceMonitor(tmp_path/'resources.json');monitor.phase=phase
    if stops:
        with pytest.raises(MemoryError):monitor.require()
    else:
        monitor.require()
    assert bool(monitor.violations)==stops
    assert monitor.rows[0]['gpu_used_mib'] is None


def test_resource_dip_remains_a_stop_after_memory_returns(tmp_path,monkeypatch):
    values=iter((2.9,6.))
    monkeypatch.setattr(execution.psutil,'Process',lambda:fake_process(400))
    monkeypatch.setattr(execution.psutil,'virtual_memory',lambda:SimpleNamespace(available=next(values)*2**30))
    monkeypatch.setattr(execution.psutil,'process_iter',lambda _:iter([]))
    monitor=execution.ResourceMonitor(tmp_path/'resources.json');monitor.phase='llm'
    with pytest.raises(MemoryError):monitor.require()
    with pytest.raises(MemoryError):monitor.require()


def test_durable_completed_result_resumes_but_orphan_marker_blocks(tmp_path):
    manifest={'queue':[{'run_key':'case-01'},{'run_key':'case-02'}]}
    write_json(tmp_path/'started/case-01.json',{'at':'old'})
    write_json(tmp_path/'results/case-01.json',seal({'run_key':'case-01','joint_pass':False}))
    rows=execution.completed_rows(tmp_path,manifest)
    assert len(rows)==1 and rows[0]['joint_pass'] is False
    write_json(tmp_path/'started/case-02.json',{'at':'interrupted'})
    with pytest.raises(ValueError,match='audit_no_auto_replay'):
        execution.completed_rows(tmp_path,manifest)


def test_changed_saved_result_never_resumes(tmp_path):
    write_json(tmp_path/'results/case-01.json',{**seal({'run_key':'case-01'}),'run_key':'case-02'})
    with pytest.raises(ValueError,match='sealed_input_changed'):
        execution.completed_rows(tmp_path,{'queue':[{'run_key':'case-01'}]})


def test_http_ok_does_not_make_missing_model_report_a_quality_success():
    snapshot={'investigation_report':{key:{'status':'unavailable'} for key in
        ('historical_features','related_entities','risky_neighbors')},'quality_packet':{'policies':[]}}
    value=execution.evaluate(snapshot,None,[{'request':{},'response':{},'metadata':{'attempt_sequence':131}}],None,'a'*64)
    assert value['joint_pass'] is False
    assert value['safety_pass'] is True
    assert value['generation_attempts']==1
    assert value['raw_semantic_pass'] is False


def test_route2_context_binds_profile_model_schema_and_original_input():
    case={'case_id':'case-01','request':{'transaction_id':'abc'}}
    value=execution.context({'manifest_sha256':'a'*64,'content_sha256':'b'*64},case,'agent','c'*64,'case-01-repeat')
    assert value.case_id=='case-01-repeat' and value.model_id=='qwen3:4b'
    assert value.input_sha256==execution.digest(case['request'])
    assert value.prompt_sha256 and value.schema_sha256


def test_regular_resource_probe_never_enumerates_unrelated_processes(tmp_path,monkeypatch):
    def forbidden(*args):raise AssertionError('full process scan in measured session')
    monkeypatch.setattr(execution.psutil,'process_iter',forbidden)
    monkeypatch.setattr(execution.psutil,'Process',lambda:fake_process(400))
    monkeypatch.setattr(execution.psutil,'virtual_memory',lambda:SimpleNamespace(available=6*2**30))
    child=SimpleNamespace(pid=9,name=lambda:'ollama-runner.exe',memory_info=lambda:SimpleNamespace(rss=120*2**20))
    root=SimpleNamespace(pid=8,name=lambda:'ollama.exe',memory_info=lambda:SimpleNamespace(rss=60*2**20),
        children=lambda recursive:[child])
    monitor=execution.ResourceMonitor(tmp_path/'resources.json');monitor.model_roots=[root]
    monitor.require();monitor.require()
    assert {p['pid'] for p in monitor.rows[-1]['model_processes']}=={8,9}
    assert len(monitor.violations)==0


def test_returned_error_response_is_not_in_http_success_subset():
    row=execution.Sample(case_id='case-01',input_sha256='a'*64,protocol_sha256='b'*64,
        scope='real',component='agent',outcome='ok',elapsed_ms=230.).model_dump()
    sample=execution.http_sample(row,503)
    assert sample.outcome=='http_error' and sample.elapsed_ms==230.
    assert execution.http_sample(row,201).outcome=='ok'
