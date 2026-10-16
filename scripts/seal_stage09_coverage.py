"""Explicitly seal Route6 evidence/source; never fit, predict or call a model."""
import argparse
from pathlib import Path
import shutil

from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.models.enhanced_execution import binding,checked_json,sealed


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seal',action='store_true',required=True)
    parser.parse_args()
    root=Path('E:/GraphRisk-Agent');source=Path(__file__).resolve().parents[1]
    reports=root/'reports/generated/stage09/route5b';out=reports/'20261015-coverage-delivery-v1'
    if (out/'route6-closure.json').exists():raise ValueError('already sealed; use read-only check')
    accepted=checked_json(reports/'20261015-coverage-verification-v3/acceptance.json')
    delivered=checked_json(out/'delivery.json')
    build=checked_json(reports/'20261015-coverage-lowmem-v3/closure.json')
    if accepted['status']!='passed' or delivered['status']!='passed':raise ValueError('acceptance missing')
    tests=['unit/test_stage09_coverage.py','unit/test_investigation_contracts.py','unit/test_graph_service_neighbors.py',
        'unit/test_stage06_service.py','unit/test_stage05_approval.py','unit/test_stage09_deployment.py',
        'unit/test_stage09_agent_quality.py','unit/test_stage09_persisted_validation.py','integration/test_relation_tools.py',
        'api/test_investigation_api.py','api/test_stage09_coverage_api.py','demo/test_streamlit_app.py']
    write_json(out/'pytest-summary.json',dict(provenance='Observed final terminal pytest output transcribed without rerun',
        passed=173,errors=0,failed=0,warnings=1,seconds=21.98,
        command='uv run --offline --no-sync pytest <listed files> -q',files=['tests/'+p for p in tests]))
    write_json(out/'failure-history.json',dict(provenance='Retrospective index; native evidence retained',events=[
        'lowmem-v1: original 3GiB gate stopped before data build',
        'pilot-v1: combined JSON model parsing exceeded process cap',
        'lowmem-v2: complete graph preparation, subsequent DuckDB wide SQL 128MiB OOM',
        'verification-v1: new root UTC calendar differs from frozen HK calendar',
        'verification-v2: model replay passed, HTTP imported old execution-root API',
        'delivery first attempt: Ollama absent/booting; final ready subsequently passed']))
    snapshot=root/'artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/delivery-source-snapshot'
    files={}
    sources=[p for base in ('src','api','scripts','configs','tests') for p in (source/base).rglob('*')
        if p.is_file() and p.suffix in ('.py','.ps1','.yaml','.json') and '__pycache__' not in p.parts]
    sources.extend(source/name for name in ('AGENTS.md','pyproject.toml','uv.lock'))
    for path in sources:
        target=snapshot/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
        wanted=binding(path);shutil.copyfile(path,target)
        if binding(target)!=wanted:raise ValueError('source changed during seal')
        files[target.relative_to(root).as_posix()]=wanted
    for path in (out/'pytest-summary.json',out/'failure-history.json',out/'delivery.json'):
        files[path.relative_to(root).as_posix()]=binding(path)
    ledger=root/'artifacts/stage-09/route5b/20261009-preflight-v1/fit-ledger.json'
    if binding(ledger)!=accepted['fit_ledger_before']:raise ValueError('fit/access ledger changed')
    result=sealed(dict(schema_version='stage09-route6-closure-v1',status='engineering_done',
        build_sha256=build['content_sha256'],acceptance_sha256=accepted['content_sha256'],
        delivery_sha256=delivered['content_sha256'],profile_path='artifacts/stage-09/route5b/20261015-coverage-lowmem-v3/coverage-profile-calendar-v2.json',
        profile_sha256=accepted['profile_sha256'],fit_ledger_path=ledger.relative_to(root).as_posix(),
        fit_ledger_binding=binding(ledger),tests_passed=173,files=files,new_fits=0,
        new_scoring_label_batches=0,real_generation_calls=0,paid_calls=0,
        graph_role='experimental_no_promotion',learning='LB6 Pending',
        next='5B Route7; original Stage09 Routes6-8 not started',postgres_live_regression='not_run_in_route6'))
    write_json(out/'route6-closure.json',result)
    print(dict(status=result['status'],source_and_evidence_files=len(files),sha256=result['content_sha256']),flush=True)


if __name__=='__main__':main()
