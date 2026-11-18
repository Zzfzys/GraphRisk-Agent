
"""Read-only deployment/paired evidence audit; no provider generation or training."""
from argparse import ArgumentParser
from pathlib import Path
import json
from graphrisk.demo.stage09_evaluation import read_json,write_json,digest
from graphrisk.demo.stage09_replay import require_registered,analyze,CONFIG
from graphrisk.graph.deployment import load_policy,verify_files
from graphrisk.graph.train_graphsage import _sha256
from graphrisk.agent.config import load_stage05_config
from scripts.replay_stage09_agent import safe_output


def raw_draft_diagnostics(root,directory,manifest):
    pairs=[]
    for case in manifest["cases"]:
        row={"case_id":case["case_id"]}
        for name,folder in (("before",root/"reports/generated/stage09/route1/20261007-baseline-v1"),("after",directory)):
            snapshot=read_json(folder/"snapshots"/(case["case_id"]+".json"))
            result=read_json(folder/"results"/(case["case_id"]+".json"))
            payload=snapshot["expected_prompt_payload"]
            expected="partial_evidence" if payload["investigation_status"]=="partial" else "evidence_complete"
            calls=[call for call in result["http_calls"] if "ledger_sequence" in call]
            try:
                raw=json.loads(calls[-1]["response"]["response"])
                ids=raw.get("evidence_ids",[])
                row[name]={"uncertainty_correct":raw.get("uncertainty")==expected,
                    "expected_uncertainty":expected,"raw_uncertainty":raw.get("uncertainty"),
                    "evidence_ids_correct":len(ids)==len(set(ids)) and set(ids)==set(payload["evidence_ids"]),
                    "method":"post-hoc field diagnostic; not replaced source-binding grade or human review"}
            except (ValueError,KeyError,TypeError,IndexError):
                row[name]={"uncertainty_correct":False,"evidence_ids_correct":False,"parse_failed":True}
        pairs.append(row)
    summary={f"{when}_{key}":sum(r[when][key] for r in pairs)
        for when in ("before","after") for key in ("uncertainty_correct","evidence_ids_correct")}
    summary.update(unique_transactions=len(pairs),human_review="pending",
        nonempty_uncertainty_is_not_semantic_correctness=True,
        inference="uncertainty enum compared to authoritative tool status; not fraud reasoning accuracy")
    write_json(directory/"draft-diagnostic.json",{"pairs":pairs,"summary":summary})
    return summary


def check(root,directory):
    manifest=require_registered(root,directory)
    config=load_stage05_config(root/CONFIG)
    profile=load_policy(root,config.resolve_path(config.model_policy),config.model_policy_sha256)
    verify_files(root,profile["primary_files"]+profile["graph_files"])
    if profile["decision_sha256"]!=manifest["decision_sha256"]:raise ValueError("deployment decision differs")
    summary=analyze(root,directory)
    diagnostic=raw_draft_diagnostics(root,directory,manifest)
    if not summary["engineering_evidence_complete"] or summary["safety_failures"]:
        raise ValueError("incomplete or unsafe replay")
    if (directory/"acceptance.json").exists():
        acceptance=read_json(directory/"acceptance.json")
        if digest({k:v for k,v in acceptance.items() if k!="content_sha256"})!=acceptance["content_sha256"]:
            raise ValueError("acceptance digest differs")
        for item in acceptance["closure_files"]:
            file=(root/item["path"]).resolve()
            if not file.is_relative_to(root) or _sha256(file)!=item["sha256"]:
                raise ValueError("accepted evidence/code differs")
    result={"passed":True,"bound_model_files":len(profile["primary_files"]+profile["graph_files"]),
        "unique_transactions":summary["unique_transactions"],"real_calls":summary["all_generation_attempts"],
        "safety_failures":summary["safety_failures"],"decision_sha256":profile["decision_sha256"],
        "draft_diagnostic":diagnostic,"generation_calls_added":0,"paid_calls_added":0,"scope":"frozen sources, model files, snapshots, transport, independent grades, durable ledger"}
    write_json(directory/"read-only-check.json",result)
    return result

def main():
    parser=ArgumentParser();parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    print(json.dumps(check(root,safe_output(root,args.output_dir))))
if __name__=="__main__":main()

