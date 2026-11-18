"""Read-only checks for the final 5B evidence and deliberately separate verdicts."""
from pathlib import Path

from graphrisk.models.enhanced_execution import binding,checked_json


CRITERIA={'artifact_chain','full_regression','real_database','container_recovery','container_data_boundary',
          'container_workbench','model_data_cards','read_only_entrypoint','handoff'}
LEDGERS={'artifacts/stage-09/route5b/20261009-preflight-v1/fit-ledger.json',
         'reports/generated/stage09/generation-ledger.json'}


def evidence_path(root,relative,*,ledger=False):
    root=Path(root).resolve();value=Path(relative)
    if value.is_absolute() or '..' in value.parts:
        raise ValueError('acceptance path must be relative and remain inside its evidence root')
    path=(root/value).resolve()
    allowed=(relative in LEDGERS if ledger else any(path.is_relative_to(root/p) for p in (
        'artifacts/stage-09/route5b','reports/generated/stage09/route5b')))
    if not path.is_relative_to(root) or not allowed:
        raise ValueError('acceptance path escapes the registered evidence boundary')
    return path


def check_final(root,report):
    root=Path(root).resolve();report=Path(report).resolve()
    if not report.is_relative_to(root/'reports/generated/stage09/route5b'):
        raise ValueError('final report must stay under registered evidence root')
    value=checked_json(report/'acceptance.json')
    if value['schema_version']!='stage09-5b-final-acceptance-v1' or value['engineering']!='done':
        raise ValueError('5B engineering acceptance is not complete')
    if set(value['criteria'])!=CRITERIA or any(v is not True for v in value['criteria'].values()):
        raise ValueError('5B acceptance criteria incomplete')
    if value['baseline_quality']!='passed' or value['gnn_increment']!='failed_no_promotion' or value['personal_learning']!='Pending':
        raise ValueError('engineering, baseline, GNN and personal-learning verdicts differ')
    if any(type(value[k]) is not int or value[k]!=0 for k in (
            'new_real_data_fits','new_source_scoring_label_batches','real_generation_calls','paid_calls')):
        raise ValueError('final acceptance cannot conceal new model work')
    if not value['files'] or set(value['live_ledgers'])!=LEDGERS:
        raise ValueError('missing file inventory or independent budget ledgers')
    for relative,wanted in value['files'].items():
        if binding(evidence_path(root,relative))!=wanted:raise ValueError('final evidence/source file changed')
    for relative,wanted in value['live_ledgers'].items():
        if binding(evidence_path(root,relative,ledger=True))!=wanted:raise ValueError('original budget ledger changed')
    decision=checked_json(evidence_path(root,value['model_decision_path']))
    if (decision['content_sha256']!=value['model_decision_sha256'] or decision['promoted'] is not False
            or decision['primary_quality_eligible'] is not True):
        raise ValueError('frozen model decision changed')
    return dict(engineering='done',baseline_quality='passed',gnn_increment='failed_no_promotion',
        personal_learning='Pending',checked_files=len(value['files']),
        regression=value['regression'],real_database_tests=value['real_database_tests'],
        real_generation_calls=0,new_real_data_fits=0,paid_calls=0,
        acceptance_sha256=value['content_sha256'])
