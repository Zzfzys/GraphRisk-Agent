"""Run meaningful confirmation and dependency regressions in E-only temporary files."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from graphrisk.demo.stage09_evaluation import resources, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = Path("E:/GraphRisk-Agent").resolve(), args.output.resolve()
    if not output.is_relative_to(root / "reports/generated/stage09/route5b") or output.exists():
        parser.error("fresh E verification directory required")
    output.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1]
    tests = ["tests/unit/test_stage09_confirmation.py", "tests/integration/test_stage09_confirmation_data.py",
        "tests/unit/test_stage09_conditional_graph.py", "tests/integration/test_stage09_conditional_data.py",
        "tests/unit/test_stage09_graph_expansion.py", "tests/integration/test_stage09_expansion_data.py",
        "tests/unit/test_stage09_model_enhancement.py", "tests/unit/test_stage09_enhanced_baselines.py",
        "tests/unit/test_stage09_wide_preprocessing.py", "tests/integration/test_stage09_enhanced_data.py"]
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "--basetemp", str(output/"pytest-temp"),
        *[str(source/p) for p in tests]], cwd=root, text=True, capture_output=True, timeout=240)
    (output/"pytest.txt").write_text(run.stdout+run.stderr, encoding="utf-8")
    if run.returncode:
        print(run.stdout+run.stderr)
        raise SystemExit(run.returncode)
    result = {"status":"passed","tests":tests,"pytest_summary":run.stdout.strip().splitlines()[-1],
        "resource_observation":resources(True),"real_dataset_fits":0,"new_generation_calls":0,"paid_calls":0}
    write_json(output/"verification.json",result)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == "__main__":
    main()
