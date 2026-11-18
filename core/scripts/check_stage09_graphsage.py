"""Check saved Stage09 route4 evidence; no training or source test label access."""
from argparse import ArgumentParser
from pathlib import Path
import json
import numpy as np
import torch
from graphrisk.demo.stage09_evaluation import read_json, digest, resources, continuous_ok
from graphrisk.agent.stage09_preflight import load_contract
from graphrisk.graph.stage09_diagnosis import (check_registration, ROUTE3, pick_winner,
    evaluate_decision, open_data, scores_for)
from graphrisk.graph.stage09_packed import verify_cache
from graphrisk.graph.stage09_budget import GraphBudgetLedger
from graphrisk.graph.train_graphsage import _sha256
from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.training.metrics import evaluate_binary_classifier

def validate_history(history, metadata, plan):
    epochs = [item["epoch"] for item in history]
    if epochs != list(range(1, len(history) + 1)) or len(history) > plan["max_epochs"]:
        raise ValueError("invalid epoch sequence or frozen epoch limit")
    best = max(history, key=lambda item: item["validation_pr_auc"])
    if best["epoch"] != metadata["best_epoch"] or len(history) != metadata["stopped_after_epoch"]:
        raise ValueError("saved best/stopping epoch differs from history")
    if abs(best["validation_pr_auc"] - metadata["validation_metrics"]["pr_auc"]) > 1e-12:
        raise ValueError("saved validation AP differs from best checkpoint selection")
    if len(history) < plan["max_epochs"] and len(history) - best["epoch"] < plan["patience"]:
        raise ValueError("early stopping occurred before frozen patience")
    return best

def check(root, directory):
    plan = check_registration(root, directory)
    contract = load_contract(root / "configs/stage09_experiment.json")
    decision = read_json(directory / "decision.json")
    if digest({k:v for k,v in decision.items() if k != "content_sha256"}) != decision["content_sha256"]:
        raise ValueError("validation decision hash changed")
    results = read_json(directory / "completed-runs.json")
    winner = pick_winner(results, plan["runs"])
    if winner != decision["winner"]:
        raise ValueError("validation winner differs from frozen decision")
    specs = plan["runs"] + [{**winner, "name":f"confirm-{seed}", "seed":seed}
        for seed in plan["confirmation_seeds"]]
    if set(results) != {s["name"] for s in specs}:
        raise ValueError("missing or extra registered real training result")
    cache = verify_cache(root / plan["cache_path"])
    if cache["content_sha256"] != plan["reused_cache_sha256"] or cache["labels_in_cache"]:
        raise ValueError("reused cache binding or label-free contract failed")
    matrices = np.load(root / ROUTE3 / "root-matrices.npz", allow_pickle=False)
    labels = matrices["validation_y"]
    root3 = read_json(root / ROUTE3 / "results.json")["validation_metrics"]
    reference = max(root3["stage04_business_50k"]["pr_auc"],root3["stage02_business_full_data"]["pr_auc"])
    recomputed = evaluate_decision([results[name] for name in
        [winner["name"], *decision["confirmation_names"]]], reference, plan["promotion_min_ap"])
    if any(recomputed[key] != decision[key] for key in recomputed):
        raise ValueError("frozen decision inconsistent with three validation seeds")
    artifact = root / "artifacts/stage-09/route4" / directory.name / "runs"
    torch.set_num_threads(plan["cpu_threads"])
    differences = {}
    for spec in specs:
        if not continuous_ok(contract.resources, resources(False)):
            raise ValueError("read-only CPU reload resource floor failed")
        name = spec["name"]
        meta = read_json(artifact / name / "metadata.json")
        if meta != results[name] or meta["spec"] != spec or meta["registration_sha256"] != plan["content_sha256"]:
            raise ValueError("saved candidate registration mismatch: " + name)
        for filename, field in (("model.pt","model_sha256"),("validation-scores.npy","prediction_sha256"),
                                ("training-history.json","history_sha256")):
            if _sha256(artifact / name / filename) != meta[field]:
                raise ValueError("candidate bytes changed: " + name + "/" + filename)
        validate_history(read_json(artifact / name / "training-history.json"),meta,plan)
        values = np.load(artifact / name / "validation-scores.npy",allow_pickle=False)
        if values.shape != labels.shape or not np.isfinite(values).all():
            raise ValueError("candidate validation scores invalid")
        metrics = evaluate_binary_classifier(labels, values, decision_threshold=.5)
        if any(abs(metrics[key]-meta["validation_metrics"][key]) > 1e-12 for key in metrics):
            raise ValueError("validation metrics failed independent recalculation")
        model = GraphSAGERiskModel(len(cache["feature_names"]),spec["hidden_channels"],spec["dropout"])
        model.load_state_dict(torch.load(artifact / name / "model.pt", weights_only=True, map_location="cpu"),strict=True)
        model.eval()
        data = open_data(root,directory,plan,cache,spec,"validation")
        # Evenly spaced fixed validation roots include both early and late periods.
        positions = np.linspace(0,len(data.ids)-1,512,dtype=int)
        with torch.no_grad():
            scores = np.concatenate([torch.sigmoid(model(data.batch(positions[start:start+128]))).numpy()
                for start in range(0,len(positions),128)])
        delta = float(np.max(np.abs(scores-values[positions])))
        if delta > 1e-5:
            raise ValueError("saved CPU checkpoint reload exceeds CUDA score tolerance: " + name)
        differences[name] = delta
        del model,data
    ledger = GraphBudgetLedger(root / "reports/generated/stage09/gnn-budget-ledger.json",contract.graph)
    budget = ledger.read()
    if len(budget["runs"]) > contract.graph.max_new_gnn_runs or ledger.spent(budget) > contract.graph.max_total_training_seconds:
        raise ValueError("global budget exceeded")
    if any(e["state"] == "reserved" for e in budget["work"] + budget["runs"]):
        raise ValueError("unresolved work reservation")
    if {e["name"] for e in budget["runs"] if e["state"]=="completed"} != set(results):
        raise ValueError("completed ledger does not match saved real training results")
    test = read_json(directory / "fixed-test-review.json")
    if len(budget["test_batches"]) != 1 or budget["test_batches"][0]["state"] != "completed":
        raise ValueError("single sealed test review missing")
    if test["decision_sha256"] != decision["content_sha256"] or budget["test_batches"][0]["decision_sha256"] != decision["content_sha256"]:
        raise ValueError("test review does not bind frozen validation decision")
    if test["test_targets"] != len(cache["target_ids"]["test"]) or test["selection_changed_after_test"]:
        raise ValueError("test review changed selection or target set")
    return {"status":"passed","registered_input_files":len(read_json(directory/"inputs.json")["artifacts"]),
        "models_checked":len(results),"reload_roots_per_model":512,
        "maximum_reload_score_difference":max(differences.values()),
        "reload_difference_by_model":differences,"new_training":False,
        "source_test_labels_read":False,"selected_by":"validation_only",
        "new_gnn_runs_total":len(budget["runs"]),"sealed_test_batches":len(budget["test_batches"]),
        "charged_wall_seconds":ledger.spent(budget),"decision_sha256":decision["content_sha256"]}

def main():
    parser=ArgumentParser()
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    directory=args.output_dir.resolve()
    if not directory.is_relative_to(root/"reports/generated/stage09/route4"):
        raise ValueError("only Stage09 route4 evidence is supported")
    print(json.dumps(check(root,directory),ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()

