"""Save focused Route0 regression and read-only freeze check on E; no model calls."""
import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from graphrisk.demo.stage09_evaluation import write_json
from graphrisk.graph.stage09_enhancement_preflight import check_registration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, default=Path("E:/GraphRisk-Agent"))
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    root = args.execution_root.resolve()
    if root.drive.upper() != "E:" or not args.name.replace("-", "").replace("_", "").isalnum():
        parser.error("safe evidence name and E execution root required")
    output = (root / "reports/generated/stage09/route5b" / args.name).resolve()
    if not output.is_relative_to(root / "reports/generated/stage09/route5b"):
        parser.error("evidence path escape")
    output.mkdir(parents=True, exist_ok=False)
    tests = [source / p for p in (
        "tests/unit/test_stage09_model_enhancement.py",
        "tests/integration/test_stage09_enhancement_data_gate.py",
        "tests/unit/test_stage09_preflight.py",
        "tests/unit/test_stage09_diagnosis.py",
        "tests/unit/test_stage09_deployment.py")]
    command = ["uv", "run", "--offline", "--no-sync", "pytest", *map(str, tests), "-q",
               "--basetemp", str(root / "artifacts" / ("pytest-5b-" + args.name))]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=180)
    (output / "pytest.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    check = check_registration(source, root, args.registration)
    value = {"at": datetime.now(UTC).isoformat(), "command": command,
             "exit_code": completed.returncode, "scope": "5B gates plus prior contract/budget/deployment unit regressions; no database/HTTP/real training",
             "freeze_check": check, "new_fits": 0, "new_generation_calls": 0, "new_paid_calls": 0}
    write_json(output / "verification.json", value)
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
