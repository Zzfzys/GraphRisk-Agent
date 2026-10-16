"""Bounded CUDA probe, meaningful regressions and completed artifact verification."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import torch
from torch_geometric.data import Data

from graphrisk.demo.stage09_evaluation import write_json,resources
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.stage09_expansion_execution import check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-root",type=Path,default=Path("E:/GraphRisk-Agent"))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--report",type=Path)
    parser.add_argument("--cuda-probe",action="store_true")
    args = parser.parse_args()
    root,output = args.execution_root.resolve(),args.output.resolve()
    if root.drive.upper() != "E:" or not output.is_relative_to(root / "reports/generated/stage09/route5b") or output.exists():
        parser.error("fresh E verification output required")
    output.mkdir(parents=True)
    before = resources(True)
    if args.cuda_probe:
        from graphrisk.agent.stage09_preflight import check_resources
        from graphrisk.graph.stage09_enhancement import load_contract
        source = Path(__file__).resolve().parents[1]
        contract = load_contract(source / "configs/stage09_model_enhancement.json")
        if check_resources(contract.resources,before)["status"] != "passed" or not torch.cuda.is_available():
            raise ValueError("CUDA probe resource gate failed")
        torch.manual_seed(7)
        model = GraphSAGERiskModel(28,64,.2).cuda()
        graph = Data(x=torch.randn(3,28,device="cuda"),edge_index=torch.tensor([[0,1,2,1],[1,0,1,2]],device="cuda"),root_index=torch.tensor([0],device="cuda"))
        value = model(graph)
        torch.nn.functional.binary_cross_entropy_with_logits(value,torch.ones_like(value)).backward()
        gradients = {name:float(p.grad.abs().sum()) for name,p in model.named_parameters() if p.grad is not None}
        if not torch.isfinite(value).all() or not gradients or not all(torch.isfinite(torch.tensor(v)) for v in gradients.values()) or sum(gradients.values()) <= 0:
            raise ValueError("CUDA forward/backward invalid")
        write_json(output / "cuda-probe.json",{"torch_version":torch.__version__,"cuda_version":torch.version.cuda,"gpu":torch.cuda.get_device_name(),"finite_logit":True,"gradients":gradients,"real_dataset_fits":0,"new_generation_calls":0})
        del model,graph,value
        torch.cuda.empty_cache()
    source = Path(__file__).resolve().parents[1]
    tests = ["tests/unit/test_stage09_graph_expansion.py","tests/integration/test_stage09_expansion_data.py",
             "tests/unit/test_stage09_model_enhancement.py","tests/unit/test_stage09_diagnosis.py",
             "tests/unit/test_stage09_graphsage_audit.py","tests/unit/test_stage09_tabular_recovery.py",
             "tests/unit/test_stage09_enhanced_baselines.py","tests/integration/test_stage09_enhanced_data.py"]
    completed = subprocess.run([sys.executable,"-m","pytest","-q","--basetemp",str(output / "pytest-temp"),*[str(source / p) for p in tests]],cwd=root,text=True,capture_output=True,timeout=180)
    (output / "pytest.txt").write_text(completed.stdout+completed.stderr,encoding="utf-8")
    if completed.returncode:
        print(completed.stdout+completed.stderr)
        raise SystemExit(completed.returncode)
    verification = {"status":"passed","tests":tests,"pytest_summary":completed.stdout.strip().splitlines()[-1],"before_resources":before,
                    "artifact_check":check(root,args.report) if args.report else None}
    write_json(output / "verification.json",verification)
    print(json.dumps(verification,ensure_ascii=False))


if __name__ == "__main__":
    main()
