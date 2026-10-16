"""Save focused feature/gate regression plus read-only full-data artifact check."""
import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from graphrisk.demo.stage09_evaluation import read_json, write_json
from graphrisk.features.wide_execution import check_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root", type=Path, default=Path("E:/GraphRisk-Agent"))
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    source, root = Path(__file__).resolve().parents[1], args.execution_root.resolve()
    if root.drive.upper() != "E:" or not args.name.replace("-", "").replace("_", "").isalnum():
        parser.error("safe evidence name and E execution root required")
    output = (root / "reports/generated/stage09/route5b" / args.name).resolve()
    if not output.is_relative_to(root / "reports/generated/stage09/route5b"):
        parser.error("evidence path escape")
    output.mkdir(parents=True, exist_ok=False)
    check = check_outputs(root, args.features)
    tests = [source / p for p in (
        "tests/unit/test_stage09_wide_preprocessing.py",
        "tests/unit/test_stage09_wide_execution.py",
        "tests/integration/test_stage09_wide_features.py",
        "tests/unit/test_temporal_features.py",
        "tests/unit/test_ieee_cis_adapter.py",
        "tests/unit/test_stage09_model_enhancement.py",
        "tests/integration/test_stage09_enhancement_data_gate.py")]
    command = ["uv", "run", "--offline", "--no-sync", "pytest", *map(str, tests), "-q",
               "--basetemp", str(root / "artifacts" / ("pytest-5b-" + args.name))]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=240)
    (output / "pytest.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    after = check_outputs(root, args.features)
    manifest = read_json(args.features / "manifest.json")
    samples = read_json(args.features / "resources.json")
    summary = {"at": datetime.now(UTC).isoformat(), "exit_code": completed.returncode,
        "command": command, "artifact_before": check, "artifact_after": after,
        "resource_samples": len(samples), "minimum_available_ram_gib": min(s["available_ram_gib"] for s in samples),
        "process_peak_working_set_mib": manifest["process_peak_working_set_mib"],
        "pipeline_seconds": manifest["pipeline_seconds"],
        "scope": "features, time, preprocessing, binding, shared budget gates; no DB/HTTP/real model fit or generation"}
    write_json(output / "verification.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
