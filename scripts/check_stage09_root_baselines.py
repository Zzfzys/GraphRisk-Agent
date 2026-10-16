"""Read-only verification of saved common-root models, scores, bindings and budgets."""
from argparse import ArgumentParser
from pathlib import Path
import json
import numpy as np
import pandas as pd
import torch
from xgboost import XGBClassifier
from graphrisk.demo.stage09_evaluation import digest, read_json
from graphrisk.graph.stage09_baselines import RootMLP, verify_registration, array_hash
from graphrisk.graph.train_graphsage import _sha256
from graphrisk.training.metrics import evaluate_binary_classifier

def check(root, directory):
    registration = verify_registration(root, directory)
    results = read_json(directory / "results.json")
    if results["registration_sha256"] != registration["content_sha256"] or results["test_evaluated"]:
        raise ValueError("results binding or label scope changed")
    plan = registration["plan"]
    torch.set_num_threads(plan["cpu_threads"])
    matrix = np.load(directory / "root-matrices.npz", allow_pickle=False)
    for split in ("train", "validation"):
        for suffix, binding in (("x", "features_sha256"), ("y", "labels_sha256")):
            if array_hash(matrix[split + "_" + suffix]) != registration["bindings"][split][binding]:
                raise ValueError("root matrix array binding changed")
    x, y = matrix["validation_x"], matrix["validation_y"]
    folder = root / "artifacts/stage-09/route3" / directory.name / "root-controls"
    verified = {}
    for name in ("xgboost", "mlp"):
        metadata = read_json(folder / (name + "-metadata.json"))
        weights = folder / ("xgboost.json" if name == "xgboost" else "mlp.pt")
        if _sha256(weights) != metadata["model_sha256"] or metadata["registration_sha256"] != registration["content_sha256"]:
            raise ValueError("saved model binding changed")
        if name == "xgboost":
            model = XGBClassifier(n_jobs=plan["cpu_threads"])
            model.load_model(weights)
            score = model.predict_proba(x)[:, 1]
        else:
            model = RootMLP(x.shape[1], plan["mlp"]["hidden_channels"], plan["mlp"]["dropout"])
            model.load_state_dict(torch.load(weights, weights_only=True, map_location="cpu"), strict=True)
            model.eval()
            with torch.no_grad():
                score = torch.sigmoid(model(torch.from_numpy(x))).numpy()
        saved = np.load(directory / (name + "-validation-scores.npy"), allow_pickle=False)
        if score.shape != saved.shape or not np.isfinite(saved).all():
            raise ValueError("score shape or finiteness changed")
        delta = float(np.max(np.abs(score - saved)))
        if delta > 1e-7:
            raise ValueError("saved model does not reproduce its validation predictions")
        measured = evaluate_binary_classifier(y, score, decision_threshold=.5)
        if any(abs(measured[key] - results["validation_metrics"][name][key]) > 1e-12 for key in measured):
            raise ValueError("reported metrics do not reproduce")
        verified[name] = {"model_sha256": _sha256(weights), "maximum_score_difference": delta,
            "validation_metrics_equal": True}
    frame = pd.read_parquet(directory / "validation-predictions.parquet")
    if frame.transaction_id.tolist() != read_json(directory / "validation-ids.json"):
        raise ValueError("joined validation prediction IDs changed")
    np.testing.assert_array_equal(frame.is_fraud.to_numpy(), y)
    for name in results["validation_metrics"]:
        metrics = evaluate_binary_classifier(y, frame[name].to_numpy(), decision_threshold=.5)
        if any(abs(metrics[key] - results["validation_metrics"][name][key]) > 1e-12 for key in metrics):
            raise ValueError("joined prediction metrics changed")
    candidate = read_json(root / registration["larger_manifest"])
    for section in ("sources", "artifacts"):
        for item in candidate[section].values():
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root) or _sha256(path) != item["sha256"]:
                raise ValueError("larger graph shared immutable input changed")
    ledger = read_json(root / "reports/generated/stage09/root-control-ledger.json")["attempts"]
    if len(ledger) != 2 or {a["model"] for a in ledger} != {"mlp", "xgboost"}:
        raise ValueError("root baseline attempt budget changed")
    if any(a["state"] != "completed" or a["registration"] != registration["content_sha256"] for a in ledger):
        raise ValueError("root baseline ledger incomplete")
    return {"status": "passed", "validation_targets": len(y), "controls": verified,
        "registration_sha256": registration["content_sha256"], "candidate_inputs_hash_checked": True,
        "test_evaluated": False, "new_training_runs": 0}

def main():
    parser = ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(check(root, args.output_dir.resolve()), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()

