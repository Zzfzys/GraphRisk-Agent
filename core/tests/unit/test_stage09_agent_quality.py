"""Tests catch wrong model judgments, source drift and approval bypass."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import httpx
import pytest
from test_stage05_reporting import graph_run, NoMatchPlanner
from test_stage06_service import _result, FakeRunner
from graphrisk.agent.quality_contracts import QualityDraft, QUALITY_VERSION
from graphrisk.agent.quality_evidence import investigation_packet, semantic_issues, verified_analysis
from graphrisk.agent.quality_reporting import QualityProviderResult, QualityReportWorkflow, OllamaQualityProvider
from graphrisk.agent.reporting import Stage05ReportWorkflow, FakeReportProvider
from graphrisk.agent.config import Stage05Generation
from graphrisk.serving.investigation_service import Stage06Service, RunVersions, ServiceError
from graphrisk.investigation.contracts import FindRiskyNeighborsResult, GetTransactionFeaturesResult


def correct_draft(packet):
    f=packet['facts'];h=f['F2']['value'];r=f['F4']['value'];g=f['F5']['value']
    return QualityDraft(policy_ref='P1', history_activity='unavailable' if h is None else 'observed' if h['tx_count_last_7d'] else 'none',
        relationship_evidence='unavailable' if r is None else 'observed' if r['returned_count'] else 'none',
        graph_evidence='available' if g else 'unavailable',policy_applicability='not_established',risk_interpretation='uncalibrated_signal',
        evidence_refs=['F2','F3','F4','F5'],unknowns=['policy_conditions','verified_identity','calibration']+(['graph_evidence'] if g is None else []))

class FixtureProvider:
    def __init__(self, alter=None): self.alter=alter or {};self.calls=0
    def generate(self,prompt,schema):
        self.calls+=1;packet=json.loads(prompt.split('\nEVIDENCE_JSON:\n')[1]);value=correct_draft(packet).model_dump()|self.alter
        return QualityProviderResult(draft=QualityDraft(**value),model_id='quality-fixture',elapsed_ms=0,estimated_cost_cny=0)


def test_packet_contains_numeric_facts_but_no_labels_or_verified_identity(graph_run):
    graph=graph_run();p=investigation_packet(graph)
    assert p['facts']['F1']['value']['amount']==graph.investigation_report.transaction.data.transaction_amount
    assert p['facts']['F3']['value']['score']==graph.investigation_report.risk_score.data.risk_score
    assert p['facts']['F2']['value']['tx_count_last_7d']==graph.investigation_report.historical_features.data.tx_count_last_7d
    assert 'isFraud' not in json.dumps(p) and 'card_id' not in json.dumps(p)
    assert p['policies'][0]['ref']=='P1' and p['sources']['F1'][0]['record_id']=='T3'

@pytest.mark.parametrize('alter',[
    {'policy_ref':'P999'},{'history_activity':'unavailable'},{'relationship_evidence':'unavailable'},
    {'graph_evidence':'unavailable'},{'policy_applicability':'established'},{'risk_interpretation':'confirmed_fraud'},
    {'evidence_refs':['F2','F3','F4','F99']},{'evidence_refs':['F2','F2','F3','F4','F5']},
    {'unknowns':['calibration']},{'unknowns':['policy_conditions','verified_identity','calibration','graph_evidence']},
])
def test_wrong_semantics_closes_without_report_or_silent_repair(graph_run,alter):
    result=QualityReportWorkflow(FixtureProvider(alter)).run(graph_run())
    assert result.status=='rejected' and result.report is None
    assert result.issue_code=='quality_semantics_mismatch'


def test_missing_graph_has_no_score_and_requires_gap(graph_run):
    graph=graph_run();report=graph.investigation_report
    missing=FindRiskyNeighborsResult.failure(code='graph_prediction_unavailable',transaction_id='T3',dataset_source='synthetic')
    # One eligible manual clause, so the partial case still has a report task.
    graph=graph.model_copy(update={'status':'partial_evidence','investigation_report':report.model_copy(update={'status':'partial','risky_neighbors':missing})})
    p=investigation_packet(graph);d=correct_draft(p)
    assert p['facts']['F5']['value'] is None and 'graph_evidence' in d.unknowns
    assert 'incorrect_graph_evidence' in semantic_issues(d.model_copy(update={'graph_evidence':'available'}),p)
    assert 'incorrect_unknowns' in semantic_issues(d.model_copy(update={'unknowns':d.unknowns[:-1]}),p)


def test_zero_history_is_distinct_from_unavailable(graph_run):
    graph=graph_run();report=graph.investigation_report
    zero=report.historical_features.model_copy(update={'data':report.historical_features.data.model_copy(update={'tx_count_last_1h':0,'tx_count_last_24h':0,'tx_count_last_7d':0})})
    p=investigation_packet(graph.model_copy(update={'investigation_report':report.model_copy(update={'historical_features':zero})}))
    assert correct_draft(p).history_activity=='none'
    missing=GetTransactionFeaturesResult.failure(code='feature_artifact_unavailable',transaction_id='T3',dataset_source='synthetic')
    p=investigation_packet(graph.model_copy(update={'investigation_report':report.model_copy(update={'historical_features':missing})}))
    assert correct_draft(p).history_activity=='unavailable'


def test_unknown_policy_conditions_still_stop_without_model_call(graph_run):
    provider=FixtureProvider();result=QualityReportWorkflow(provider).run(graph_run(planner=NoMatchPlanner()))
    assert provider.calls==0 and result.status=='insufficient_evidence' and not result.report.policy_conclusions


def test_source_packet_binding_and_forged_history_are_rejected(graph_run):
    graph=graph_run();r=graph.investigation_report
    with pytest.raises(ValueError,match='tool_binding'):
        investigation_packet(graph.model_copy(update={'investigation_report':r.model_copy(update={'historical_features':r.historical_features.model_copy(update={'transaction_id':'T999'})})}))
    row=r.related_entities.data.matches[0].model_copy(update={'seconds_before_target':0})
    r=r.model_copy(update={'related_entities':r.related_entities.model_copy(update={'data':r.related_entities.data.model_copy(update={'matches':[row]})})})
    with pytest.raises(ValueError,match='strict_history'):
        investigation_packet(graph.model_copy(update={'investigation_report':r}))


def test_legacy_serialization_has_no_new_analysis_field(graph_run):
    result=Stage05ReportWorkflow(FakeReportProvider()).run(graph_run())
    assert 'analysis' not in result.report.model_dump() and result.audit.prompt_version=='stage05-route5-extractive-v1'


def test_service_checks_analysis_even_when_report_hash_is_recomputed(graph_run):
    graph=graph_run();report_run=QualityReportWorkflow(FixtureProvider()).run(graph)
    original=_result(report_run.status)
    reliable=original.model_copy(update={'graph_run':graph,'report_run':report_run,'audit':original.audit.model_copy(update={'model_id':'quality-fixture'})})
    versions=RunVersions(graph_comparison_run='fixture',policy_index_version=graph.policy_retrieval.index_version,
        generation_model_id='quality-fixture',report_protocol=QUALITY_VERSION)
    service=Stage06Service(dataset_source='synthetic',versions=versions,runner=FakeRunner(reliable))
    assert service.run(graph.request).result.report_run.report.analysis is not None
    tampered=report_run.report.model_copy(deep=True);tampered.analysis.statements[0]='已证实欺诈'
    altered=report_run.model_copy(update={'report':tampered,'audit':report_run.audit.model_copy(update={'report_sha256':hashlib.sha256(tampered.model_dump_json().encode()).hexdigest()})})
    service.runner=FakeRunner(reliable.model_copy(update={'report_run':altered}))
    with pytest.raises(ServiceError,match='analysis_evidence_mismatch'):service.run(graph.request)
    service.close()


def test_native_adapter_pins_digest_parses_quality_and_records_non_thinking(graph_run):
    draft=correct_draft(investigation_packet(graph_run()));requests=[]
    cfg=Stage05Generation(provider='local_http',model_id='qwen3:4b',model_revision='a'*64,
        endpoint='http://127.0.0.1:11434/api/generate',max_output_tokens=512,request_timeout_seconds=180.)
    def transport(request):
        if request.method=='GET':return httpx.Response(200,json={'models':[{'name':cfg.model_id,'digest':cfg.model_revision}]})
        requests.append(json.loads(request.content));return httpx.Response(200,json={'model':cfg.model_id,'done':True,'response':draft.model_dump_json(),'prompt_eval_count':12,'eval_count':10})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        p=OllamaQualityProvider(cfg,client).with_deadline(lambda:60)
        result=p.generate('fixture',QualityDraft.model_json_schema())
    assert result.draft==draft and requests[0]['think'] is False and result.prompt_tokens==12


def test_fresh_selection_is_disjoint_reproducible_and_label_independent():
    import pandas as pd
    from graphrisk.demo.stage09_quality import fresh_cases
    frame=pd.DataFrame({'transaction_id':[str(i) for i in range(300)],'isFraud':[i%2 for i in range(300)]})
    covered={str(i) for i in range(300) if i%2==0};old={'0','1','2','3'}
    a=fresh_cases(frame,[i/300 for i in range(300)],covered,old)
    b=fresh_cases(frame.assign(isFraud=1-frame.isFraud),[i/300 for i in range(300)],covered,old)
    assert a==b and len(a[0])==5 and len(a[1])==10
    ids=[c['transaction_id'] for group in a for c in group]
    assert len(set(ids))==15 and not set(ids)&old
    assert sum(c['graph_covered'] for c in a[0])==3 and sum(c['graph_covered'] for c in a[1])==6


def test_raw_grader_checks_model_semantics_instead_of_published_status(graph_run):
    from graphrisk.demo.stage09_quality import raw_judgments
    graph=graph_run();packet=investigation_packet(graph)
    snapshot={'investigation_report':graph.investigation_report.model_dump(mode='json'),'quality_packet':packet}
    d=correct_draft(packet).model_dump()
    calls=[{'ledger_sequence':1,'response':{'response':json.dumps(d)}}]
    assert raw_judgments(snapshot,calls)[0]['semantic_pass']
    calls[0]['response']['response']=json.dumps(d|{'graph_evidence':'unavailable'})
    assert not raw_judgments(snapshot,calls)[0]['semantic_pass']
    assert raw_judgments(snapshot,calls)[0]['issues']==['graph_evidence']


def test_all_quality_variants_share_one_phase_cap(tmp_path):
    from graphrisk.demo.stage09_evaluation import AttemptLedger,read_json
    ledger=AttemptLedger(tmp_path/'ledger.json','route5a')
    for i in range(66):ledger.reserve(('A','B','C')[i%3]+str(i))
    with pytest.raises(ValueError,match='phase_generation_budget_exhausted'):ledger.reserve('new-case')
    assert len(read_json(ledger.path)['attempts'])==66


def test_model_selection_uses_only_development_quality_then_latency():
    from graphrisk.demo.stage09_quality import selection_from_results
    rows={v:[{'grade':{'quality_pass':True,'joint_pass':v!='B','raw_semantic_pass':v=='C'},'elapsed_ms':1 if v=='B' else 9}] for v in ('A','B','C')}
    result=selection_from_results({'content_sha256':'a'*64},rows)
    assert result['selected']=='C'
    rows['B'][0]['grade'].update(joint_pass=True,raw_semantic_pass=True)
    assert selection_from_results({'content_sha256':'a'*64},rows)['selected']=='B'


def test_brief_is_only_measurements_not_precomputed_answers(graph_run):
    graph=graph_run();p=investigation_packet(graph);brief=p['brief_measurements']
    assert brief['F2_transactions_last_7d']==p['facts']['F2']['value']['tx_count_last_7d']
    assert brief['F4_returned_shared_transactions']==len(graph.investigation_report.related_entities.data.matches)
    assert brief['F5_graph_tool_status']==graph.investigation_report.risky_neighbors.status
    assert not {'history_activity','relationship_evidence','unknowns'}&brief.keys()
