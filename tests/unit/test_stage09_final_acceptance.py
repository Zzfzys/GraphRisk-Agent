"""Final evidence must reject drift, hidden model work and escaped paths."""
from pathlib import Path

import pytest

from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.models.enhanced_execution import binding,sealed
from graphrisk.graph.stage09_acceptance import check_final,evidence_path,CRITERIA,LEDGERS


@pytest.fixture
def acceptance(tmp_path):
    report=tmp_path/'reports/generated/stage09/route5b/final';report.mkdir(parents=True)
    decision=report/'model-decision.json'
    write_json(decision,sealed(dict(promoted=False,primary_quality_eligible=True)))
    ledgers={}
    for relative in LEDGERS:
        file=tmp_path/relative;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('unchanged')
        ledgers[relative]=binding(file)
    value=dict(schema_version='stage09-5b-final-acceptance-v1',engineering='done',
        criteria={k:True for k in CRITERIA},baseline_quality='passed',gnn_increment='failed_no_promotion',
        personal_learning='Pending',new_real_data_fits=0,new_source_scoring_label_batches=0,
        real_generation_calls=0,paid_calls=0,files={decision.relative_to(tmp_path).as_posix():binding(decision)},
        live_ledgers=ledgers,model_decision_path=decision.relative_to(tmp_path).as_posix(),
        model_decision_sha256=sealed(dict(promoted=False,primary_quality_eligible=True))['content_sha256'],
        regression='fixture mechanism',real_database_tests=0)
    def save():write_json(report/'acceptance.json',sealed(value))
    save();return tmp_path,report,value,save


def test_read_only_check_keeps_personal_learning_and_failed_gnn_separate(acceptance):
    root,report,_,_=acceptance
    before=(report/'acceptance.json').read_bytes()
    result=check_final(root,report)
    assert result['engineering']=='done' and result['gnn_increment']=='failed_no_promotion'
    assert result['personal_learning']=='Pending' and before==(report/'acceptance.json').read_bytes()


@pytest.mark.parametrize('change',[{'real_generation_calls':1},{'paid_calls':1},
    {'personal_learning':'Done'},{'gnn_increment':'passed'}])
def test_false_completion_or_hidden_calls_are_rejected(acceptance,change):
    root,report,value,save=acceptance;value.update(change);save()
    with pytest.raises(ValueError):check_final(root,report)


def test_ledger_and_source_drift_cannot_pass(acceptance):
    root,report,value,_=acceptance
    (root/next(iter(value['live_ledgers']))).write_text('unexpected model work')
    with pytest.raises(ValueError,match='ledger changed'):check_final(root,report)


@pytest.mark.parametrize("relative", ["../outside", "configs/secret.yaml", ".secrets/password"])
def test_evidence_cannot_include_secrets_or_escape_registered_roots(tmp_path,relative):
    with pytest.raises(ValueError):evidence_path(tmp_path,relative)


def test_bound_source_or_decision_corruption_fails_before_trusting_verdict(acceptance):
    root,report,value,_=acceptance
    (root/next(iter(value['files']))).write_text('{}')
    with pytest.raises(ValueError,match='file changed'):check_final(root,report)


def test_one_missing_engineering_criterion_cannot_be_called_complete(acceptance):
    root,report,value,save=acceptance;value['criteria']['container_recovery']=False;save()
    with pytest.raises(ValueError,match='criteria incomplete'):check_final(root,report)
