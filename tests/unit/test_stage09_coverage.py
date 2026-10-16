"""Independent small fixtures for temporal, null-score and live artifact boundaries."""
from datetime import datetime,timedelta,timezone
import hashlib
import json

import pandas as pd
import pytest
from pydantic import ValidationError

from graphrisk.graph.coverage_data import DiskHistory,NewTransaction
from graphrisk.graph.coverage_service import CoverageGraphService
from graphrisk.investigation.contracts import RiskyNeighbor
from graphrisk.graph.coverage_build import model_for_time
from graphrisk.graph.coverage_service import CoverageRiskTool
from graphrisk.investigation.repository import InvestigationRepository


@pytest.fixture
def history(tmp_path):
    identity=hashlib.sha256(b'C|A|E').hexdigest()
    rows=[]
    for key,second,amount in [('old-a',100,10.),('old-b',200,20.),('target',300,30.),('simultaneous',300,500.),('future',400,1000.)]:
        rows.append(dict(transaction_id=key,source_transaction_dt_seconds=second,
            transaction_time=datetime(2000,1,1,tzinfo=timezone.utc)+timedelta(seconds=second),
            transaction_amount=amount,card_id='C',device_id='D',email_domain='E',address_id='A',
            product_code='W',identity_key=identity,is_fraud=int(amount>15),
            tx_count_last_1h=0.,tx_count_last_24h=0.,tx_count_last_7d=0.,amount_mean_last_24h=None,
            amount_std_last_24h=None,amount_deviation_last_24h=None,transaction_hour=0.,transaction_day_of_week=6.))
    file=tmp_path/'history.parquet';pd.DataFrame(rows).to_parquet(file,index=False)
    return DiskHistory(file,file,tmp_path/'scratch')


def new_event(**extra):
    return NewTransaction.model_validate(dict(transaction_id='new',source_transaction_dt_seconds=300,
        transaction_amount=30.,card_id='C',device_id='D',email_domain='E',address_id='A',product_code='W',**extra))


def test_disk_graph_matches_strict_past_including_ties_and_bounded_fanout(history):
    target=history.graph_row('target')
    assert [r['transaction_id'] for r in history.selected_history(target,cap=1)]==['old-b']
    graph=history.graph(target)
    assert set(graph.selected_history_ids)=={'old-a','old-b'}
    assert all(n.transaction.transaction_time<target['transaction_time'] for n in graph.nodes[1:] if n.transaction)
    assert all('fraud' not in name for name in target)


def test_new_event_server_features_are_independently_correct_and_future_invariant(history):
    columns=['TransactionAmt','hist_card_count_24h','hist_card_amount_mean_24h',
             'hist_card_amount_std_24h','hist_card_seconds_since_previous','hist_card_age_seconds']
    row,values=history.new_features(new_event(),columns=columns,raw_names=['TransactionAmt','ProductCD'])
    assert values==dict(zip(columns,[30.,2,15.,5.,100,200]))
    assert [row[n] for n in ('tx_count_last_1h','tx_count_last_24h','tx_count_last_7d')]==[2,2,2]
    assert row['amount_deviation_last_24h']==15.
    graph_before=history.graph(row).content_sha256()
    data=pd.read_parquet(history.canonical)
    data.loc[data.source_transaction_dt_seconds>=300,'transaction_amount']=999999.
    data.is_fraud=1-data.is_fraud
    data.to_parquet(history.canonical,index=False)
    after_row,after_values=history.new_features(new_event(),columns=columns,raw_names=['TransactionAmt','ProductCD'])
    assert after_values==values
    assert history.graph(after_row).content_sha256()==graph_before


@pytest.mark.parametrize('field',['isFraud','is_fraud','label','risk_score','hist_card_count_24h','relative_day'])
def test_new_event_rejects_labels_and_caller_derived_statistics(field):
    with pytest.raises(ValidationError):new_event(raw_features={field:1.})


def test_new_event_missing_entities_and_unregistered_or_duplicate_inputs(history):
    event=new_event().model_copy(update={'card_id':'unknown-card-new','device_id':'unknown-device-new',
        'address_id':'unknown-address-new','email_domain':'unknown-email-new'})
    row,values=history.new_features(event,columns=['TransactionAmt','hist_card_count_24h'],raw_names=['TransactionAmt','ProductCD'])
    assert values['hist_card_count_24h']==0 and history.graph(row).selected_history_ids==()
    with pytest.raises(ValueError,match='unregistered'):
        history.new_features(new_event(raw_features={'mystery':1.}),columns=[],raw_names=[])
    with pytest.raises(ValueError,match='already exists'):
        history.new_features(new_event().model_copy(update={'transaction_id':'target'}),columns=[],raw_names=[])


def service_for(history,tmp_path):
    scores=tmp_path/'artifacts/scores.parquet';scores.parent.mkdir()
    pd.DataFrame([dict(transaction_id=k,split='validation',event_second=t,selection_cutoff=50,
         primary_score=.6,graph_score=s,primary_model_version='x3-fold2',graph_model_version='c4-fold2')
         for k,t,s in [('old-b',200,.8),('target',300,.7)]]).to_parquet(scores,index=False)
    profile={'gnn_model_version':'coverage-v1','files':{},'scores_path':'artifacts/scores.parquet','risk_thresholds':{'low':.3,'high':.7}}
    return CoverageGraphService(tmp_path,profile,history.canonical,history.features)


def test_scored_and_relation_only_neighbors_are_distinct_not_zero_or_risk_ranked(history,tmp_path):
    service=service_for(history,tmp_path)
    result=service.find_neighbors('target',limit=20)
    assert [(r.transaction_id,r.graph_risk_score,r.evidence_role) for r in result]==[
        ('old-b',.8,'scored'),('old-a',None,'relation_only')]
    assert service.coverage('target')['status']=='covered'
    assert service.coverage('old-a')['status']=='out_of_coverage'
    assert service.predict('target').graph_model_version=='c4-fold2'
    with service.scores.open('ab') as stream:stream.write(b'changed')
    assert service.coverage('target')['status']=='artifact_unavailable'


def test_relation_only_contract_keeps_legacy_serialization_and_forbids_fabricated_score():
    common=dict(transaction_id='old',seconds_before_target=10,shared_entities=[dict(entity_type='card',entity_id='C')])
    scored=RiskyNeighbor(**common,graph_risk_score=.1)
    assert set(scored.model_dump())=={'transaction_id','graph_risk_score','seconds_before_target','shared_entities'}
    relation=RiskyNeighbor(**common,evidence_role='relation_only',score_unavailable_reason='outside_independent_score_scope')
    assert relation.model_dump()['graph_risk_score'] is None
    with pytest.raises(ValidationError):RiskyNeighbor(**common,evidence_role='relation_only',graph_risk_score=0.)
    with pytest.raises(ValidationError):RiskyNeighbor(**common)


def test_time_selection_checks_early_stop_and_calibration_not_just_training():
    def window(name,a,b,c):return dict(name=name,train={'max_relative_seconds':a},
        early_stop={'max_relative_seconds':b},calibration={'max_relative_seconds':c})
    windows=[window('fold-1',10,20,30),window('fold-2',40,50,60),window('fold-3',55,70,90)]
    assert model_for_time(windows,80)['name']=='fold-2'
    with pytest.raises(ValueError):model_for_time(windows,30)


def test_new_root_calendar_uses_frozen_timezone_while_history_remains_utc(history):
    frozen=DiskHistory(history.canonical,history.features,history.scratch,calendar_timezone='Asia/Hong_Kong')
    row,_=frozen.new_features(new_event(),columns=[],raw_names=['TransactionAmt','ProductCD'])
    assert row['transaction_time'].hour==0
    assert row['transaction_hour']==8 and row['transaction_day_of_week']==6


def test_out_of_coverage_is_not_reported_as_missing_features(history,tmp_path):
    service=service_for(history,tmp_path)
    repository=InvestigationRepository('ieee_cis',history.canonical,history.features)
    tool=CoverageRiskTool(repository,service)
    def request(key):return {'transaction_id':key,'dataset_source':'ieee_cis'}
    assert tool.get_risk_score(request('old-a')).error.code=='prediction_out_of_coverage'
    assert tool.get_risk_score(request('not-present')).error.code=='transaction_not_found'
    assert tool.get_risk_score(request('target')).data.model_version=='x3-fold2'


def test_fold_specific_versions_and_old_version_default_remain_distinct(history,tmp_path):
    from types import SimpleNamespace
    from graphrisk.serving.investigation_service import Stage06Service,RunVersions
    from graphrisk.agent.contracts import Stage05Request
    graph=service_for(history,tmp_path)
    graph.profile['schema_version']='stage09-historical-coverage-v1'
    runner=SimpleNamespace(investigation=SimpleNamespace(graph_service=graph))
    versions=RunVersions(graph_comparison_run='demo',policy_index_version='index',generation_model_id='fake')
    service=Stage06Service(dataset_source='ieee_cis',versions=versions,runner=runner)
    request=Stage05Request(transaction_id='target',dataset_source='ieee_cis',policy_as_of='2026-07-01')
    actual=service._versions_for_request(request)
    assert actual.primary_model_version=='x3-fold2' and actual.experimental_graph_model_version=='c4-fold2'
    assert service.versions==versions


def test_input_rejects_contradictory_entity_and_other_events_unknown_placeholder():
    with pytest.raises(ValidationError,match='contradict'):
        new_event(raw_features={'DeviceInfo':'other','DeviceType':'desktop'})
    payload=new_event().model_dump();payload['device_id']='unknown-device-old'
    with pytest.raises(ValidationError,match='scoped'):
        NewTransaction.model_validate(payload)


def test_frozen_profile_digest_and_bound_file_corruption_are_rejected(tmp_path):
    from graphrisk.models.enhanced_execution import sealed,binding
    from graphrisk.graph.coverage_service import load_coverage
    folder=tmp_path/'artifacts';folder.mkdir()
    weights=folder/'weights.bin';weights.write_bytes(b'frozen')
    profile=sealed(dict(schema_version='stage09-historical-coverage-v1',score_mode='xgboost_only',
        gnn_role='experimental',files={'artifacts/weights.bin':binding(weights)}))
    path=folder/'profile.json';path.write_text(json.dumps(profile),encoding='utf-8')
    with pytest.raises(ValueError,match='hash mismatch'):load_coverage(tmp_path,path,'0'*64)
    weights.write_bytes(b'changed')
    with pytest.raises(ValueError,match='bound file'):load_coverage(tmp_path,path,profile['content_sha256'])
