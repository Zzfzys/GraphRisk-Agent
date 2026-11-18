"""Read saved results only; no model calls, training or raw test label access."""
from argparse import ArgumentParser
from pathlib import Path
import hashlib
import json
import os
import math
import numpy as np
import pandas as pd
from graphrisk.training.metrics import evaluate_binary_classifier

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser=ArgumentParser()
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    directory=args.output_dir.resolve()
    if not directory.is_relative_to(root/"reports/generated/stage09/route4"):
        raise ValueError("only Stage09 route4 saved reports supported")
    plan=read(directory/"registration.json")
    inputs=read(directory/"inputs.json")
    decision=read(directory/"decision.json")
    results=read(directory/"completed-runs.json")
    artifacts=root/"artifacts/stage-09/route4"/directory.name/"runs"
    npz=np.load(root/"reports/generated/stage09/route3/20261007-baselines-v1/root-matrices.npz",allow_pickle=False)
    labels=npz["validation_y"]
    baseline=read(root/"reports/generated/stage09/route3/20261007-baselines-v1/results.json")["validation_metrics"]
    base_predictions=pd.read_parquet(root/"reports/generated/stage09/route3/20261007-baselines-v1/validation-predictions.parquet")
    rows=[]
    for name,result in results.items():
        folder=artifacts/name
        for file,field in (("model.pt","model_sha256"),("training-history.json","history_sha256"),
                           ("validation-scores.npy","prediction_sha256")):
            if sha(folder/file)!=result[field]:
                raise ValueError("candidate outputs changed: "+name)
        values=np.load(folder/"validation-scores.npy",allow_pickle=False)
        metric=evaluate_binary_classifier(labels,values,decision_threshold=.5)
        if any(abs(metric[k]-result["validation_metrics"][k])>1e-12 for k in metric):
            raise ValueError("stored validation metrics mismatch: "+name)
        rows.append({"name":name,"seed":result["spec"]["seed"],"cap":result["spec"]["cap"],
            "variant":result["spec"]["variant"],"hidden":result["hidden_channels"],"lr":result["learning_rate"],
            "dropout":result["dropout"],"best_epoch":result["best_epoch"],"stop_epoch":result["stopped_after_epoch"],
            "seconds":result["training_seconds"],"cuda_peak_mib":result["cuda_peak_allocated_mib"],**metric,
            "paired_ap_delta_vs_old_gnn":metric["pr_auc"]-baseline["graphsage_frozen"]["pr_auc"],
            "paired_ap_delta_vs_root_mlp":metric["pr_auc"]-baseline["mlp"]["pr_auc"],
            "paired_ap_delta_vs_business50k":metric["pr_auc"]-baseline["stage04_business_50k"]["pr_auc"]})
    pd.DataFrame(rows).to_csv(directory/"metrics.csv",index=False)
    winner_names=[decision["winner"]["name"],*decision["confirmation_names"]]
    aggregate={}
    for metric in ("pr_auc","roc_auc","precision_at_1pct","recall_at_1pct","precision_at_5pct","recall_at_5pct"):
        values=[results[n]["validation_metrics"][metric] for n in winner_names]
        aggregate[metric]={"mean":float(np.mean(values)),"std_population":float(np.std(values)),
            "min":min(values),"max":max(values),"by_seed":values}
    grouped={g:{metric:{"mean":float(np.mean([results[n]["groups"][g]["metrics"][metric] for n in winner_names])),
                       "by_seed":[results[n]["groups"][g]["metrics"][metric] for n in winner_names]}
                   for metric in ("pr_auc","roc_auc")}
             for g in ("early_half","late_half")}
    cache=read(root/plan["cache_path"]/"manifest.json")
    rewiring={s:cache["statistics"]["cap5-rewired-"+s] for s in ("train","validation","test")}
    observations=read(directory/"resources.json")
    complete=[o for o in observations if "available_ram_gib" in o]
    summary={"registration_sha256":plan["content_sha256"],"input_freeze_sha256":inputs["content_sha256"],
        "models_checked":len(rows),"selected_seeds":winner_names,"validation_mean_std":aggregate,
        "selected_time_groups":grouped,"rewiring_statistics":rewiring,
        "minimum_observed_ram_gib":min(o["available_ram_gib"] for o in complete),
        "resource_measurement_failures":sum(o.get("measurement_error",False) for o in observations),
        "decision":decision,"old_checkpoint_replay":read(directory/"checkpoint-replay.json"),
        "validation_targets":len(labels),"validation_positive":int(labels.sum()),
        "with_history_validation_targets":10000,"without_history_validation_targets":0,
        "new_training":False,"raw_test_labels_read":False}
    if (directory/"fixed-test-review.json").exists():
        summary["fixed_test_review"]=read(directory/"fixed-test-review.json")
    ledger=read(root/"reports/generated/stage09/gnn-budget-ledger.json")
    summary["ledger"]=ledger
    summary["charged_wall_seconds"]=sum(e.get("seconds",e["cap_seconds"]) for e in ledger["runs"]+ledger["work"])
    (directory/"analysis.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    os.environ["MPLCONFIGDIR"]=str(root/"artifacts/mpl-cache")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(12,5))
    for name in results:
        history=read(artifacts/name/"training-history.json")
        axes[0].plot([r["epoch"] for r in history],[r["validation_pr_auc"] for r in history],label=name)
        axes[1].plot([r["epoch"] for r in history],[r["train_loss"] for r in history],label=name)
    axes[0].set(xlabel="Epoch",ylabel="Validation AP",title="Frozen validation targets, AP selection")
    axes[1].set(xlabel="Epoch",ylabel="Weighted BCE",title="Training loss")
    for axis in axes:
        axis.grid(alpha=.2)
    handles,legends=axes[0].get_legend_handles_labels()
    fig.legend(handles,legends,loc="lower center",ncol=4,fontsize=8)
    fig.tight_layout(rect=(0,.16,1,1))
    fig.savefig(directory/"training-curves.png",dpi=150)
    plt.close(fig)
    print(json.dumps({k:summary[k] for k in ("models_checked","minimum_observed_ram_gib","charged_wall_seconds",
        "validation_mean_std","selected_time_groups","rewiring_statistics")},ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()

