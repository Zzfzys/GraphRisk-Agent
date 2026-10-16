"""Paired local Agent replay under one frozen Stage09 model decision."""
from pathlib import Path
from datetime import UTC,datetime
from collections import Counter
import ast
import gc
import hashlib
import json
import threading
import time
import numpy as np
import torch
from graphrisk.demo.stage09_evaluation import (SCORING,AttemptLedger,ObservedProvider,
    digest,read_json,write_json,stable,grade,summarize,exclusive,resources)
from graphrisk.agent.stage09_preflight import build_freeze,check_freeze,check_resources,load_contract
from graphrisk.agent.config import load_stage05_config
from graphrisk.agent.reporting import ReportDraft,_eligible_hits,_prompt_payload,PROMPT_VERSION
from graphrisk.agent.workflow import Stage05GraphWorkflow
from graphrisk.graph.deployment import load_policy
from graphrisk.graph.stage09_monitor import DiagnosisMonitor
from graphrisk.graph.train_graphsage import _sha256
from graphrisk.serving.investigation_service import Stage06Service
from graphrisk.serving.investigation_views import project_investigation

BASELINE=Path("reports/generated/stage09/route1/20261007-baseline-v1")
CONFIG=Path("configs/stage09.yaml")
EXPECTED_CODE_CHANGES={
"src/graphrisk/agent/config.py","src/graphrisk/agent/components.py",
"src/graphrisk/investigation/contracts.py","src/graphrisk/investigation/relation_tools.py",
"src/graphrisk/investigation/workflow.py","src/graphrisk/graph/service.py",
"src/graphrisk/serving/investigation_service.py","src/graphrisk/serving/investigation_views.py"}

def prompt_template_sha(source):
    tree=ast.parse(source)
    constants=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)
        and "You select source quotations for a fraud analyst." in n.value]
    if len(constants)!=1:raise ValueError("extractive prompt template not uniquely identified")
    return hashlib.sha256(constants[0].encode()).hexdigest()

def field_diff(before,after,path=""):
    if isinstance(before,dict) and isinstance(after,dict):
        return [change for key in sorted(set(before)|set(after)) for change in (
            [{"path":path+"/"+key,"before_sha256":digest(before.get(key)),"after_sha256":digest(after.get(key))}]
            if key not in before or key not in after else field_diff(before[key],after[key],path+"/"+key))]
    if isinstance(before,list) and isinstance(after,list):
        if len(before)!=len(after):
            return [{"path":path,"before_sha256":digest(before),"after_sha256":digest(after)}]
        return [c for i,(old,new) in enumerate(zip(before,after)) for c in field_diff(old,new,path+f"/{i}")]
    if before!=after:
        return [{"path":path,"before_sha256":digest(before),"after_sha256":digest(after)}]
    return []

def check_snapshot_changes(old,new):
    for key in ("request","policy_retrieval","eligible_hits","legal_history_ids"):
        if stable(old[key])!=stable(new[key]):raise ValueError("non-model snapshot changed: "+key)
    for key in ("transaction","historical_features","risk_score","related_entities","status","issues","unavailable_capabilities"):
        if stable(old["investigation_report"][key])!=stable(new["investigation_report"][key]):
            raise ValueError("primary/data/coverage evidence changed: "+key)
    old_prompt,new_prompt=old["expected_prompt_payload"],new["expected_prompt_payload"]
    if {k:v for k,v in old_prompt.items() if k!="evidence_ids"}!={k:v for k,v in new_prompt.items() if k!="evidence_ids"}:
        raise ValueError("prompt fields other than model/neighbor evidence IDs changed")
    changes=field_diff(stable(old),stable(new))
    allowed=("/investigation_report/risky_neighbors","/investigation_report/evidence_bindings",
        "/investigation_report/limitations","/investigation_report/score_mode",
        "/investigation_report/model_decision_sha256","/expected_prompt_payload/evidence_ids")
    if any(not any(c["path"]==a or c["path"].startswith(a+"/") for a in allowed) for c in changes):
        raise ValueError("unregistered snapshot field change")
    return changes

def check_baseline(root):
    baseline=root/BASELINE
    cases=read_json(baseline/"cases.json")
    if digest({k:v for k,v in cases.items() if k!="content_sha256"})!=cases["content_sha256"] or cases["scoring"]!=SCORING:
        raise ValueError("baseline case/scoring manifest changed")
    for case in cases["cases"]:
        if digest(read_json(baseline/"snapshots"/(case["case_id"]+".json")))!=case["snapshot_sha256"]:
            raise ValueError("baseline snapshot changed")
    if not read_json(baseline/"acceptance.json")["engineering_evidence_complete"]:
        raise ValueError("real baseline evidence incomplete")
    return cases

def prepare(root,directory):
    if (directory/"cases.json").exists():raise ValueError("replay already registered")
    gate=read_json(directory/"before-code-gate.json")
    if gate["route4_input_check"]["status"]!="passed":raise ValueError("route4 pre-code gate missing")
    previous=check_baseline(root)
    baseline_config=load_stage05_config(root/"configs/stage05.yaml")
    config=load_stage05_config(root/CONFIG)
    if any(getattr(config,key)!=getattr(baseline_config,key) for key in ("generation","retrieval","limits")):
        raise ValueError("LLM/retrieval/budget settings changed")
    contract=load_contract(root/"configs/stage09_experiment.json")
    monitor=DiagnosisMonitor(contract.resources,directory/"prepare-resources.json")
    if check_resources(contract.resources,monitor.sample(True))["status"]!="passed":
        raise ValueError("prepare start resource gate failed before generation")
    torch.set_num_threads(4)
    service=Stage06Service.from_config(root/CONFIG)
    try:
        if service.readiness().status!="ready":raise ValueError("new service dependencies not ready")
        monitor.sample(False);monitor.require()
        policy=load_policy(root,config.resolve_path(config.model_policy),config.model_policy_sha256)
        source=(root/"src/graphrisk/agent/reporting.py").read_text(encoding="utf-8")
        old_source=(root/"artifacts/stage-09/route5/20261007-replay-v1/source-before/src/graphrisk/agent/reporting.py").read_text(encoding="utf-8")
        if prompt_template_sha(source)!=prompt_template_sha(old_source):raise ValueError("prompt template changed")
        workflow=Stage05GraphWorkflow(service.runner.investigation,service.runner.retriever,
            dataset_source="ieee_cis",limits=service.runner.limits,top_k=service.runner.top_k)
        cases=[]
        for case in previous["cases"]:
            monitor.sample(False);monitor.require()
            old=read_json(root/BASELINE/"snapshots"/(case["case_id"]+".json"))
            graph=workflow.run(old["request"])
            if graph.investigation_report is None or graph.policy_retrieval is None:
                raise ValueError("snapshot collection failed")
            hits=_eligible_hits(graph)
            store=service.runner.investigation.graph_service.store
            history=store.ego_graph(case["transaction_id"]).selected_history_ids if case["graph_covered"] else ()
            if case["graph_covered"]:
                target=store.index.by_id[case["transaction_id"]]
                if any(store.index.by_id[i].transaction_time >= target.transaction_time for i in history):
                    raise ValueError("nonhistorical_graph_context")
            if list(history)!=old["legal_history_ids"]:raise ValueError("historical sample differs")
            snapshot={"request":old["request"],"investigation_report":graph.investigation_report.model_dump(mode="json"),
                "policy_retrieval":graph.policy_retrieval.model_dump(mode="json"),
                "eligible_hits":[h.model_dump(mode="json") for h in hits],
                "expected_prompt_payload":_prompt_payload(graph,hits),"legal_history_ids":list(history)}
            changes=check_snapshot_changes(old,snapshot)
            write_json(directory/"snapshots"/(case["case_id"]+".json"),snapshot)
            write_json(directory/"diffs"/(case["case_id"]+".json"),{"changes":changes,
                "baseline_snapshot_sha256":case["snapshot_sha256"],"replay_snapshot_sha256":digest(snapshot)})
            cases.append({**case,"baseline_snapshot_sha256":case["snapshot_sha256"],
                "snapshot_sha256":digest(snapshot),"changed_fields":len(changes)})
        inputs=build_freeze(root,root/"configs/stage09_experiment.json")
        drift=check_freeze(root,read_json(root/"reports/generated/stage09/route4/20261007-diagnosis-v4/inputs-after-check.json"))
        violations=drift["violations"]
        # Record intentional source evolution without overwriting the historical freeze.
        changed={v.split(":",1)[1] for v in violations if v.startswith("artifact_changed:")}
        if changed!=EXPECTED_CODE_CHANGES or len(changed)!=len(violations):
            raise ValueError("unexpected route4 drift: "+str(violations))
        write_json(directory/"inputs.json",inputs)
        baseline_files=[root/BASELINE/"cases.json",root/BASELINE/"inputs.json"]
        baseline_files += sorted((root/BASELINE/"snapshots").glob("*.json"))
        baseline_files += sorted((root/BASELINE/"results").glob("*.json"))
        extra=[root/CONFIG,root/"streamlit_app.py",config.resolve_path(config.model_policy)]
        manifest={"schema_version":"stage09-model-replay-v1","created_at":datetime.now(UTC).isoformat(),
            "cases":cases,"repeat_case_ids":previous["repeat_case_ids"],"review_case_ids":previous["review_case_ids"],
            "scoring":SCORING,"baseline_manifest_sha256":previous["content_sha256"],
            "input_freeze":inputs["content_sha256"],"decision_sha256":policy["decision_sha256"],
            "profile_sha256":policy["content_sha256"],"generation":config.generation.model_dump(),
            "prompt_version":PROMPT_VERSION,"prompt_template_sha256":prompt_template_sha(source),
            "draft_schema_sha256":digest(ReportDraft.model_json_schema()),
            "baseline_files":[{"path":p.relative_to(root).as_posix(),"sha256":_sha256(p)} for p in baseline_files],
            "extra_inputs":[{"path":p.relative_to(root).as_posix(),"sha256":_sha256(p)} for p in extra],
            "intentional_route4_code_drift":drift,"config":CONFIG.as_posix(),"generation_calls_at_registration":0}
        manifest["content_sha256"]=digest(manifest)
        write_json(directory/"cases.json",manifest)
        write_json(directory/"prepared-service-versions.json",service.versions.model_dump(mode="json"))
        print(json.dumps({"prepared":len(cases),"same_unique_ids":True,"generation_calls":0,
            "changed_snapshot_count":sum(c["changed_fields"]>0 for c in cases),"manifest_sha256":manifest["content_sha256"]}))
    finally:
        service.close()

def require_registered(root,directory):
    manifest=read_json(directory/"cases.json")
    if digest({k:v for k,v in manifest.items() if k!="content_sha256"})!=manifest["content_sha256"] or manifest["scoring"]!=SCORING:
        raise ValueError("replay registration/scoring changed")
    if check_freeze(root,read_json(directory/"inputs.json"))["status"]!="passed":
        raise ValueError("registered replay inputs drifted")
    for item in manifest["baseline_files"]+manifest["extra_inputs"]:
        file=(root/item["path"]).resolve()
        if not file.is_relative_to(root) or _sha256(file)!=item["sha256"]:
            raise ValueError("paired source or deployment input changed")
    baseline=check_baseline(root)
    if manifest["baseline_manifest_sha256"]!=baseline["content_sha256"] or manifest["repeat_case_ids"]!=baseline["repeat_case_ids"]:
        raise ValueError("baseline/repetition binding changed")
    if [(c["case_id"],c["transaction_id"]) for c in manifest["cases"]]!=[(c["case_id"],c["transaction_id"]) for c in baseline["cases"]]:
        raise ValueError("paired IDs/order changed")
    for case in manifest["cases"]:
        if digest(read_json(directory/"snapshots"/(case["case_id"]+".json")))!=case["snapshot_sha256"]:
            raise ValueError("replay snapshot changed")
    if digest(ReportDraft.model_json_schema())!=manifest["draft_schema_sha256"]:raise ValueError("draft schema changed")
    return manifest

def grade_replay(snapshot,record,calls,view,decision):
    result=grade(snapshot,record,calls)
    if not record or not (record["result"].get("report_run") or {}).get("report"):return result
    issues=[]
    if not view or view["versions"].get("score_mode")!="xgboost_only" or view["versions"].get("model_decision_sha256")!=decision:
        issues.append("model_decision_view_mismatch")
    else:
        report=view["report"]
        if report["model"]["score_mode"]!="xgboost_only" or report["graph"]["score_role"]!="experimental":
            issues.append("misleading_score_role")
        if report["graph"]["fusion_score"] is not None or report["graph"]["fusion_alpha"] is not None:
            issues.append("unexpected_fusion_publication")
    if issues:
        result["issues"]+=issues
        result.update(safety_pass=False,quality_pass=False,outcome="quality_failure")
    return result

def run(root,directory):
    manifest=require_registered(root,directory)
    contract=load_contract(root/"configs/stage09_experiment.json")
    monitor=DiagnosisMonitor(contract.resources,directory/"runtime-resources.json")
    if check_resources(contract.resources,monitor.sample(True))["status"]!="passed":
        raise ValueError("start_resource_gate_failed")
    ledger=AttemptLedger(root/"reports/generated/stage09/generation-ledger.json","route5")
    with exclusive(root/"reports/generated/stage09/real-run.lock"):
        torch.set_num_threads(4)
        base=Stage06Service.from_config(root/manifest["config"])
        monitor.sample(False);monitor.require()
        if base.readiness().status!="ready":raise ValueError("replay_dependencies_not_ready")
        worker=threading.Thread(target=monitor.loop,daemon=True);worker.start()
        queue=[(c,c["case_id"]) for c in manifest["cases"]]
        queue += [(next(c for c in manifest["cases"] if c["case_id"]==cid),cid+"-repeat") for cid in manifest["repeat_case_ids"]]
        native=base.runner.provider
        try:
            for case,key in queue:
                output=directory/"results"/(key+".json")
                if output.exists():
                    validate_result_file(directory,case,key,manifest)
                    continue
                marker=directory/"started"/(key+".json")
                if marker.exists():raise ValueError("incomplete_case_requires_audit_no_auto_replay")
                monitor.require()
                snapshot=read_json(directory/"snapshots"/(case["case_id"]+".json"))
                write_json(marker,{"at":datetime.now(UTC).isoformat()})
                observer=ObservedProvider(native,ledger,directory/"http",manifest["content_sha256"]+":"+key,monitor,
                    expected_payload=snapshot["expected_prompt_payload"])
                base.runner.provider=observer
                service=Stage06Service(dataset_source=base.dataset_source,versions=base.versions,
                    runner=base.runner,cost_ledger=base.cost_ledger)
                started=time.perf_counter()
                record,view,error=None,None,None
                try:
                    actual=service.run(snapshot["request"])
                    record=actual.model_dump(mode="json")
                    view=project_investigation(actual).model_dump(mode="json")
                    if actual.approval and not (directory/"api-demo.json").exists():
                        api_smoke(service,actual,directory)
                except Exception as exc:
                    error=type(exc).__name__
                finally:service.close()
                result={"case_id":key,"transaction_id":case["transaction_id"],"record":record,"public_view":view,
                    "error_type":error,"http_calls":observer.calls,"elapsed_ms":(time.perf_counter()-started)*1000,
                    "grade":grade_replay(snapshot,record,observer.calls,view,manifest["decision_sha256"])}
                write_json(output,result)
                write_json(directory/"summary.json",summarize(directory,manifest))
                print(json.dumps({"case":key,"status":record["status"] if record else error,
                    "attempts":result["grade"]["generation_attempts"],"outcome":result["grade"]["outcome"]}),flush=True)
                if not result["grade"]["safety_pass"]:raise ValueError("unsafe_publication_stop")
        except Exception as error:
            write_json(directory/"interruption.json",{"type":type(error).__name__,"reason":str(error),
                "at":datetime.now(UTC).isoformat(),"summary":summarize(directory,manifest)})
            raise
        finally:
            monitor.stop.set();worker.join(timeout=10);base.close()
            write_json(directory/"summary.json",summarize(directory,manifest))



def validate_result_file(directory,case,key,manifest):
    result=read_json(directory/"results"/(key+".json"))
    snapshot=read_json(directory/"snapshots"/(case["case_id"]+".json"))
    if result["case_id"]!=key or result["transaction_id"]!=case["transaction_id"]:
        raise ValueError("result ID binding differs")
    fresh=grade_replay(snapshot,result["record"],result["http_calls"],result.get("public_view"),manifest["decision_sha256"])
    if fresh!=result["grade"]:raise ValueError("stored grade differs from independent regrade")
    return result

def queue_for(manifest):
    return [(c,c["case_id"]) for c in manifest["cases"]]+[
        (next(c for c in manifest["cases"] if c["case_id"]==cid),cid+"-repeat")
        for cid in manifest["repeat_case_ids"]]

def raw_uncertainty(result):
    drafts=[]
    for call in result["http_calls"]:
        try:
            draft=json.loads(call.get("response",{}).get("response",""))
            drafts.append({"present":bool(draft.get("uncertainty")),
                "valid_evidence_ids":draft.get("evidence_ids",[])})
        except (ValueError,TypeError):pass
    return drafts

def analyze(root,directory):
    manifest=require_registered(root,directory)
    rows=[]
    current=[]
    for case,key in queue_for(manifest):
        path=directory/"results"/(key+".json")
        if not path.exists():raise ValueError("replay is incomplete")
        new=validate_result_file(directory,case,key,manifest)
        old=read_json(root/BASELINE/"results"/(key+".json"))
        snapshot=read_json(directory/"snapshots"/(case["case_id"]+".json"))
        baseline=read_json(root/BASELINE/"snapshots"/(case["case_id"]+".json"))
        for call in new["http_calls"]:
            if "ledger_sequence" not in call:continue
            request=call["request"]
            # All generation settings/schema/template stay equal; only registered evidence IDs can differ.
            old_request=next(c["request"] for c in old["http_calls"] if "ledger_sequence" in c)
            if {k:v for k,v in request.items() if k!="prompt"}!={k:v for k,v in old_request.items() if k!="prompt"}:
                raise ValueError("native HTTP generation/schema settings differ")
            if request["prompt"].split("\nEVIDENCE_JSON:\n",1)[0]!=old_request["prompt"].split("\nEVIDENCE_JSON:\n",1)[0]:
                raise ValueError("native prompt template differs")
            if json.loads(request["prompt"].split("\nEVIDENCE_JSON:\n",1)[1])!=snapshot["expected_prompt_payload"]:
                raise ValueError("HTTP prompt differs from frozen evidence payload")
        old_graph=(baseline["investigation_report"]["risky_neighbors"] or {}).get("data") or {}
        new_graph=(snapshot["investigation_report"]["risky_neighbors"] or {}).get("data") or {}
        old_ids=[v["transaction_id"] for v in old_graph.get("neighbors",[])]
        new_ids=[v["transaction_id"] for v in new_graph.get("neighbors",[])]
        rows.append({"case_id":key,"transaction_id":case["transaction_id"],"covered":case["graph_covered"],
            "before_status":(old.get("record") or {}).get("status"),
            "after_status":(new.get("record") or {}).get("status"),"before_grade":old["grade"],"after_grade":new["grade"],
            "before_elapsed_ms":old["elapsed_ms"],"after_elapsed_ms":new["elapsed_ms"],
            "before_calls":old["grade"]["generation_attempts"],"after_calls":new["grade"]["generation_attempts"],
            "before_raw_uncertainty":any(d["present"] for d in raw_uncertainty(old)),
            "after_raw_uncertainty":any(d["present"] for d in raw_uncertainty(new)),
            "before_graph_score":old_graph.get("target_graph_risk_score"),
            "after_graph_score":new_graph.get("target_graph_risk_score"),
            "neighbor_order_changed":old_ids!=new_ids,"before_neighbor_ids":old_ids,"after_neighbor_ids":new_ids,
            "prompt_payload_changed":baseline["expected_prompt_payload"]!=snapshot["expected_prompt_payload"],
            "snapshot_changed_fields":read_json(directory/"diffs"/(case["case_id"]+".json"))["changes"]})
        current.append(new)
    attempts=read_json(root/"reports/generated/stage09/generation-ledger.json")["attempts"]
    ledger={v["sequence"]:v for v in attempts}
    used=[]
    for result in current:
        for call in result["http_calls"]:
            if "ledger_sequence" not in call:continue
            seq=call["ledger_sequence"];entry=ledger.get(seq)
            if not entry or entry["phase"]!="route5" or entry["case"]!=manifest["content_sha256"]+":"+result["case_id"]:
                raise ValueError("HTTP attempt not bound to durable route5 ledger")
            if read_json(directory/"http"/(result["case_id"]+f"-http-{result['http_calls'].index(call)+1}.json"))!=call:
                raise ValueError("HTTP transport evidence differs")
            used.append(seq)
    if len(set(used))!=len(used):raise ValueError("duplicate ledger sequence")
    reserved=[v["sequence"] for v in attempts if v["phase"]=="route5"]
    if sorted(used)!=reserved:raise ValueError("reserved attempt missing from replay audit")
    if len(attempts)>144 or len(reserved)>66:raise ValueError("generation budget exceeded")
    first=rows[:30]
    summary=summarize(directory,manifest)
    summary.update(before_summary=read_json(root/BASELINE/"summary.json"),
        same_unique_ids=len({r["transaction_id"] for r in first})==30,
        neighbor_order_changed=sum(r["neighbor_order_changed"] for r in first),
        prompt_payload_changed=sum(r["prompt_payload_changed"] for r in first),
        raw_uncertainty_before=sum(r["before_raw_uncertainty"] for r in first),
        raw_uncertainty_after=sum(r["after_raw_uncertainty"] for r in first),
        paired_transitions=dict(Counter(str(r["before_status"])+"->"+str(r["after_status"]) for r in first)),
        repeated_report_equal=sum(stable(current[i]["record"]["result"]["report_run"]["report"])==
            stable(next(r["record"]["result"]["report_run"]["report"] for r in current[:30] if r["case_id"]==current[i]["case_id"].replace("-repeat","")))
            for i in range(30,33)),
        stage09_generation_attempts_total=len(attempts),phase_reserved_attempts=len(reserved),
        decision_sha256=manifest["decision_sha256"],new_gnn_training_runs=0,
        inference="paired diagnostic cohort; no fraud reasoning accuracy or independent generalization claim")
    write_json(directory/"paired.json",{"pairs":rows,"summary":summary})
    write_json(directory/"summary.json",summary)
    return summary

def rollback(root,directory):
    manifest=require_registered(root,directory)
    contract=load_contract(root/"configs/stage09_experiment.json")
    monitor=DiagnosisMonitor(contract.resources,directory/"rollback-resources.json")
    if check_resources(contract.resources,monitor.sample(True))["status"]!="passed":
        raise ValueError("rollback resource gate failed")
    torch.set_num_threads(4)
    service=Stage06Service.from_config(root/"configs/stage05.yaml")
    try:
        monitor.sample(False);monitor.require()
        workflow=Stage05GraphWorkflow(service.runner.investigation,service.runner.retriever,
            dataset_source="ieee_cis",limits=service.runner.limits,top_k=service.runner.top_k)
        rows=[]
        for case in manifest["cases"]:
            monitor.sample(False);monitor.require()
            old=read_json(root/BASELINE/"snapshots"/(case["case_id"]+".json"))
            graph=workflow.run(old["request"])
            equal=(stable(graph.investigation_report.model_dump(mode="json"))==stable(old["investigation_report"])
                and stable(graph.policy_retrieval.model_dump(mode="json"))==stable(old["policy_retrieval"])
                and _prompt_payload(graph,_eligible_hits(graph))==old["expected_prompt_payload"])
            rows.append({"case_id":case["case_id"],"equal_to_baseline":equal})
        result={"legacy_config":"configs/stage05.yaml","service_versions":service.versions.model_dump(mode="json"),
            "cases":rows,"passed":all(v["equal_to_baseline"] for v in rows),"generation_calls":0,
            "scope":"real data/model tool snapshots; not another LLM replay"}
        write_json(directory/"rollback.json",result)
        if not result["passed"]:raise ValueError("rollback evidence differs from frozen baseline")
        return result
    finally:service.close()



def api_smoke(service,record,directory):
    """Use the live real result; no new generation, no claim of human semantic review."""
    from api.main import create_app
    from fastapi.testclient import TestClient
    prefix="/api/v1/investigations/"+record.run_id
    with TestClient(create_app(investigation_service_factory=lambda:service)) as client:
        view=client.get(prefix)
        if view.status_code!=200:raise ValueError("real API view failed")
        value=view.json()
        graph=value["report"]["graph"]
        if (value["versions"]["score_mode"]!="xgboost_only" or graph["score_role"]!="experimental"
            or graph["fusion_score"] is not None or value["approval"]["status"]!="pending"):
            raise ValueError("real API model/approval label differs")
        decision={"run_id":record.run_id,"session_id":record.session_id,
            "transaction_id":record.request.transaction_id,"report_sha256":record.report_sha256,
            "decision":"reject","reviewer_id":"route5-demo-check","note":"Automated boundary check; not semantic human review."}
        wrong=client.post(prefix+"/approval",json={**decision,"report_sha256":"0"*64})
        final=client.post(prefix+"/approval",json=decision)
        reread=client.get(prefix)
        if (wrong.status_code!=409 or final.status_code!=200 or final.json()["status"]!="rejected"
            or reread.json()["approval"]["status"]!="rejected" or final.json()["external_action_count"]!=0):
            raise ValueError("real API approval boundary failed")
        evidence={"case_id":"first_approvable_real_case","wrong_report_status":wrong.status_code,
            "get_status":view.status_code,"rejection_status":final.status_code,"final_status":final.json()["status"],
            "versions":value["versions"],"primary_model":value["report"]["model"],
            "graph_score_role":graph["score_role"],"graph_model_version":graph["graph_model_version"],
            "fusion_score":graph["fusion_score"],"external_actions":final.json()["external_action_count"],
            "generation_calls_added":0,"reviewer_authenticated":False,"passed":True}
        write_json(directory/"api-demo.json",evidence)
    return evidence

