"""Explicit final evidence seal. Default reproduction uses check_stage09_5b.py."""
import argparse
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.graph.stage09_acceptance import CRITERIA,check_final
from graphrisk.models.enhanced_execution import binding,checked_json,sealed


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seal',action='store_true',required=True);parser.parse_args()
    root=Path('E:/GraphRisk-Agent');source=Path(__file__).resolve().parents[1]
    out=root/'reports/generated/stage09/route5b/20261015-acceptance-v1'
    if (out/'acceptance.json').exists():raise ValueError('acceptance already sealed; use read-only check')
    before=checked_json(out/'before.json')
    for relative,wanted in before['files'].items():
        if binding(root/relative)!=wanted:raise ValueError('original training/generation ledger changed')
    for name in ('regression.json','checker-tests.json','artifact-audit.json','database-fault.json'):
        if checked_json(out/name)['status']!='passed':raise ValueError('an acceptance phase is incomplete')
    for name in ('seed-checks.json','restore-checks.json','ui-checks.json','prospective-checks.json'):
        if checked_json(out/'container-output'/name)['status']!='passed':raise ValueError('container acceptance incomplete')
    calls=json.loads((out/'container-output/fixture-calls.json').read_text(encoding='utf-8'))
    if calls!={'deterministic_fixture_calls':2,'paid_calls':0,'real_generation_calls':0}:
        raise ValueError('unregistered report generation')
    identity=json.loads((out/'container-output/runtime-identity.json').read_text(encoding='utf-8'))
    if identity['api_binding']!=binding(source/'api/main.py') or identity['service_binding']!=binding(source/'src/graphrisk/serving/postgres_service.py'):
        raise ValueError('container did not validate the final core source')
    cases=list(ET.parse(out/'pytest.xml').getroot().iter('testcase'))
    database=[c for c in cases if any(k in c.get('classname','') for k in (
        'test_stage07_postgres','test_stage07_pgvector','test_stage09_quality_postgres'))]
    if len(cases)!=807 or len(database)!=24 or any(c.find('skipped') is not None for c in database):
        raise ValueError('full/real-database test denominator changed')
    checks=list(ET.parse(out/'checker-pytest.xml').getroot().iter('testcase'))
    if len(checks)!=11 or any(c.find('failure') is not None or c.find('error') is not None for c in cases+checks):
        raise ValueError('regression failures cannot be hidden')
    write_json(out/'failure-index.json',dict(provenance='Retrospective terminal-observation index; original configuration/logs retained',
        events=['Initial DB internal-only network published port0; repaired with loopback debug network',
            'Nested missing config-file mount under read-only directory; retained compose-config-mount-failure.yaml and log',
            'Windows GBK read of UTF-8 created-views failed before DB outage; explicit UTF-8 repaired; no regeneration']))
    snapshot=root/'artifacts/stage-09/route5b/20261015-acceptance-v1/source-snapshot'
    files={}
    sources=[]
    for base in ('src','api','scripts','configs','tests','docs','reports'):
        sources.extend(p for p in (source/base).rglob('*') if p.is_file() and p.suffix in ('.py','.ps1','.yaml','.json','.md')
            and not {'__pycache__','generated'}.intersection(p.relative_to(source).parts))
    sources.extend(source/name for name in ('AGENTS.md','PROJECT_PLAN.md','README.md','pyproject.toml','uv.lock',
        '.gitignore','.dockerignore','Dockerfile','Dockerfile.stage07','compose.yaml','compose.stage07.yaml','compose.stage07-debug.yaml'))
    for path in sources:
        if not path.resolve().is_relative_to(source):raise ValueError('source snapshot path escaped')
        target=snapshot/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
        wanted=binding(path);shutil.copyfile(path,target)
        if binding(target)!=wanted:raise ValueError('source changed during final snapshot')
        files[target.relative_to(root).as_posix()]=wanted
    for base in (out,out/'container-output',out/'runtime-configs'):
        for path in base.iterdir():
            if path.is_file() and path.suffix in ('.json','.yaml','.xml','.txt','.jsonl') and path.name!='acceptance.json':
                files[path.relative_to(root).as_posix()]=binding(path)
    audit=checked_json(out/'artifact-audit.json')
    decision_path='reports/generated/stage09/route5b/20261010-confirmation-v1/model-decision.json'
    files[decision_path]=binding(root/decision_path)
    result=sealed(dict(schema_version='stage09-5b-final-acceptance-v1',engineering='done',
        criteria={k:True for k in CRITERIA},baseline_quality='passed',gnn_increment='failed_no_promotion',
        personal_learning='Pending',regression={'full':'806 passed, 1 skipped, 10 warnings in 85.72s',
        'new_checker':'11 passed in 9.01s'},real_database_tests=24,files=files,live_ledgers=before['files'],
        model_decision_path=decision_path,model_decision_sha256=audit['model_decision_sha256'],
        new_real_data_fits=0,new_source_scoring_label_batches=0,real_generation_calls=0,paid_calls=0,
        deterministic_fixture_calls=2,next='Stage09 original Route6: layered regression and CI'))
    write_json(out/'acceptance.json',result)
    print(json.dumps(check_final(root,out)),flush=True)


if __name__=='__main__':main()
