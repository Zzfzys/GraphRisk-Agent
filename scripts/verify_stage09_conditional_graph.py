"""E-only meaningful conditional regressions and an enhanced-input CUDA probe."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
import torch
from torch_geometric.data import Data
from graphrisk.demo.stage09_evaluation import resources, write_json
from graphrisk.graph.model import GraphSAGERiskModel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, output = Path("E:/GraphRisk-Agent").resolve(), args.output.resolve()
    if not output.is_relative_to(root / "reports/generated/stage09/route5b") or output.exists():
        parser.error("fresh E verification output required")
    output.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1]
    tests = ["tests/unit/test_stage09_conditional_graph.py", "tests/integration/test_stage09_conditional_data.py",
             "tests/unit/test_stage09_graph_expansion.py", "tests/integration/test_stage09_expansion_data.py",
             "tests/unit/test_stage09_model_enhancement.py", "tests/unit/test_stage09_diagnosis.py",
             "tests/unit/test_stage09_graphsage_audit.py", "tests/unit/test_stage09_tabular_recovery.py",
             "tests/unit/test_stage09_enhanced_baselines.py", "tests/integration/test_stage09_enhanced_data.py"]
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "--basetemp", str(output / "pytest-temp"),
                          *[str(source / p) for p in tests]], cwd=root, text=True, capture_output=True, timeout=180)
    (output / "pytest.txt").write_text(run.stdout+run.stderr, encoding="utf-8")
    if run.returncode:
        print(run.stdout+run.stderr)
        raise SystemExit(run.returncode)
    from graphrisk.agent.stage09_preflight import check_resources
    from graphrisk.graph.stage09_enhancement import load_contract
    before = resources(True)
    if check_resources(load_contract(source / "configs/stage09_model_enhancement.json").resources, before)["status"] != "passed" or not torch.cuda.is_available():
        raise ValueError("CUDA probe resource gate failed")
    torch.manual_seed(7)
    model = GraphSAGERiskModel(191, 64, .4).cuda()
    graph = Data(x=torch.randn(3, 191, device="cuda"), edge_index=torch.tensor([[0,1,2,1], [1,0,1,2]], device="cuda"), root_index=torch.tensor([0], device="cuda"))
    value = model(graph)
    torch.nn.functional.binary_cross_entropy_with_logits(value, torch.ones_like(value)).backward()
    gradients = {name: float(p.grad.abs().sum()) for name, p in model.named_parameters() if p.grad is not None}
    if not torch.isfinite(value).all() or not gradients or not all(torch.isfinite(torch.tensor(v)) for v in gradients.values()) or sum(gradients.values()) <= 0:
        raise ValueError("191dim CUDA forward/backward invalid")
    result = {"status": "passed", "tests": tests, "pytest_summary": run.stdout.strip().splitlines()[-1],
              "cuda_probe": {"dimension": 191, "finite_logit": True, "gradients": gradients, "gpu": torch.cuda.get_device_name()},
              "before_resources": before, "real_dataset_fits": 0, "new_generation_calls": 0}
    write_json(output / "verification.json", result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
