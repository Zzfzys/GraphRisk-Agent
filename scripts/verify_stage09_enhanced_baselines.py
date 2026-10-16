"""Save frozen tabular/feature/budget tests and read-only real-run verification."""
import argparse
import json
import subprocess
from pathlib import Path

from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.models.enhanced_execution import check


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution-root',type=Path,default=Path('E:/GraphRisk-Agent'))
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--name',required=True)
    args=parser.parse_args()
    root=args.execution_root.resolve()
    if root.drive.upper()!='E:' or not args.name.replace('-','').replace('_','').isalnum():
        parser.error('safe name and E execution root required')
    output=(root/'reports/generated/stage09/route5b'/args.name).resolve()
    if not output.is_relative_to(root/'reports/generated/stage09/route5b'):
        parser.error('verification path escape')
    output.mkdir(parents=True,exist_ok=False)
    before=check(root,args.run)
    source=Path(__file__).resolve().parents[1]
    tests=[source/p for p in (
        'tests/unit/test_stage09_enhanced_baselines.py','tests/integration/test_stage09_enhanced_data.py',
        'tests/unit/test_stage09_model_enhancement.py','tests/integration/test_stage09_enhancement_data_gate.py',
        'tests/unit/test_stage09_wide_preprocessing.py','tests/unit/test_stage09_wide_execution.py',
        'tests/integration/test_stage09_wide_features.py','tests/unit/test_temporal_features.py','tests/unit/test_ieee_cis_adapter.py')]
    command=['uv','run','--offline','--no-sync','pytest',*map(str,tests),'-q','--basetemp',str(root/'artifacts'/('pytest-5b-'+args.name))]
    result=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=240)
    (output/'pytest.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
    value={'exit_code':result.returncode,'command':command,'before':before,'after':check(root,args.run),
           'scope':'new tabular/label/root/early-stop and previous feature/budget rules; no new real-data fits, DB, Docker, LLM or paid calls'}
    write_json(output/'verification.json',value)
    print(json.dumps(value))
    raise SystemExit(result.returncode)


if __name__=='__main__':
    main()
