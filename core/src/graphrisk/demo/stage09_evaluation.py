"""Frozen local-only multi-transaction measurement; never trains or tunes models."""
from __future__ import annotations
import ctypes
import gc
import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import httpx
import numpy as np
import pandas as pd
from graphrisk.agent.reporting import OllamaReportProvider, _eligible_hits, _prompt_payload
from graphrisk.agent.stage09_preflight import build_freeze, check_freeze, check_resources, load_contract
from graphrisk.agent.workflow import Stage05GraphWorkflow

SCORING = {
    "version": "stage09-real-baseline-v1",
    "selection": "validation score/id equal-rank tertiles; sha256(seed:id) within coverage bucket",
    "covered_quotas": [7, 7, 6], "uncovered_quotas": [3, 3, 4],
    "fallback": "none; fail preparation before generation if quota unavailable",
    "repeats": "first covered case in low/medium/high, once each after 30 first runs",
    "review": "first four low, first three medium/high, plus every published report",
    "correct_refusal": "no eligible clause; insufficient evidence report, no citation/action/approval",
    "failure": "schema/provider/deadline failure is quality failure even when safely closed",
    "quality": "independent report/tool and citation binding; no fraud-label explanation ground truth",
    "semantics_limit": "extractive selection, not free-form fraud reasoning; personal human review pending",
    "runtime": "native single worker; isolated record lifecycle per case, cached evidence runner",
    "phase_attempt_cap": 66, "global_attempt_cap": 144, "per_case_cap": 2,
    "continuous": "RAM/disk floors every 2s; own model GPU use allowed; idle GPU gate only at start",
}

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

@contextmanager
def exclusive(path):
    """OS releases lock on a crash; never delete a potentially live lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

class AttemptLedger:
    """Reserve durably BEFORE send; crashes/failures never refund a reservation."""
    def __init__(self, path, phase):
        self.path, self.phase = path, phase

    def reserve(self, case):
        with exclusive(self.path.with_suffix(".lock")):
            value = read_json(self.path) if self.path.exists() else {"attempts": []}
            attempts = value["attempts"]
            phase = [a for a in attempts if a["phase"] == self.phase]
            if len(attempts) >= SCORING["global_attempt_cap"]:
                raise ValueError("stage09_generation_budget_exhausted")
            if len(phase) >= SCORING["phase_attempt_cap"]:
                raise ValueError("phase_generation_budget_exhausted")
            if sum(a["case"] == case for a in phase) >= SCORING["per_case_cap"]:
                raise ValueError("case_generation_budget_exhausted")
            seq = len(attempts) + 1
            attempts.append({"sequence": seq, "phase": self.phase, "case": case,
                "at": datetime.now(UTC).isoformat(), "state": "reserved_before_send"})
            write_json(self.path, value)
            return seq

def select_cases(frame, scores, covered, seed):
    ids = frame["transaction_id"].astype(str)
    values = np.asarray(scores, dtype=float)
    if ids.duplicated().any() or len(ids) != len(values) or not np.isfinite(values).all():
        raise ValueError("unique transactions and finite aligned scores required")
    if ((values < 0) | (values > 1)).any():
        raise ValueError("scores must be in [0,1]")
    ranked = pd.DataFrame({"transaction_id": ids.to_list(), "score": values})
    ranked = ranked.sort_values(["score", "transaction_id"], kind="stable").reset_index(drop=True)
    cases, counts = [], {}
    for name, bucket, cq, uq in zip(("low", "medium", "high"),
            np.array_split(np.arange(len(ranked)), 3), SCORING["covered_quotas"], SCORING["uncovered_quotas"]):
        part = ranked.iloc[bucket]
        counts[name] = {"all": len(part), "covered": int(part.transaction_id.isin(covered).sum())}
        counts[name]["uncovered"] = len(part) - counts[name]["covered"]
        for covered_flag, quota in ((True, cq), (False, uq)):
            pool = part[part.transaction_id.isin(covered) == covered_flag].to_dict("records")
            pool.sort(key=lambda row: (hashlib.sha256(f"{seed}:{row['transaction_id']}".encode()).hexdigest(),
                                      row["transaction_id"]))
            if len(pool) < quota:
                raise ValueError(f"insufficient {name} coverage={covered_flag} candidates before generation")
            for row in pool[:quota]:
                cases.append({**row, "stratum": name, "graph_covered": covered_flag,
                    "case_id": f"case-{len(cases)+1:02d}"})
    return cases, counts

def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items()
            if k not in {"investigation_id", "created_at", "elapsed_ms", "duration_ms"}}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value

def resources(include_gpu=False):
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ("total", "available", "page_total",
            "page_available", "virtual_total", "virtual_available", "extended")]
    memory = Memory()
    memory.length = ctypes.sizeof(memory)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise OSError("RAM measurement unavailable")
    result = {"at": datetime.now(UTC).isoformat(), "available_ram_gib": memory.available / 2**30,
        "disk_free_gib": {drive: shutil.disk_usage(drive + ":/").free / 2**30 for drive in "CDE"}}
    if include_gpu:
        output = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.free,utilization.gpu",
            "--format=csv,noheader,nounits"], text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW)
        free, utilization = output.splitlines()[0].split(",")
        result.update(free_gpu_mib=int(free), gpu_utilization_percent=int(utilization))
    return result

def continuous_ok(budget, observed):
    values = [(observed.get("available_ram_gib"), budget.minimum_available_ram_gib)]
    values += [(observed.get("disk_free_gib", {}).get(d), floor)
        for d, floor in budget.minimum_disk_free_gib.items()]
    return all(isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) and v >= floor
        for v, floor in values)

class Monitor:
    def __init__(self, budget, path):
        self.budget, self.path = budget, path
        self.stop, self.violation = threading.Event(), threading.Event()
        self.samples = []

    def sample(self, gpu=False):
        try:
            observation = resources(gpu)
            if not continuous_ok(self.budget, observation):
                self.violation.set()
        except (OSError, subprocess.SubprocessError, ValueError):
            observation = {"at": datetime.now(UTC).isoformat(), "measurement_error": True}
            self.violation.set()
        self.samples.append(observation)
        write_json(self.path, self.samples)
        return observation

    def require(self):
        if self.violation.is_set():
            raise ValueError("resource_floor_reached; stop before next request/case")

    def loop(self):
        while not self.stop.wait(2):
            self.sample(True)

class ObservedProvider:
    """Keeps native prompt/options/parsing; bounds and observes actual transport."""
    def __init__(self, native, ledger, directory, case, monitor, remaining=None, expected_payload=None, provider_factory=OllamaReportProvider):
        self.provider_factory = provider_factory
        self.expected_payload = expected_payload
        self.native, self.ledger, self.directory, self.case = native, ledger, directory, case
        self.monitor, self.remaining, self.calls = monitor, remaining, []

    def with_deadline(self, remaining):
        self.remaining = remaining
        return self

    def generate(self, prompt, schema):
        self.monitor.require()
        if self.expected_payload is not None and json.loads(prompt.split("\nEVIDENCE_JSON:\n", 1)[1]) != self.expected_payload:
            raise ValueError("prompt_payload_drift")
        call = {"state": "before_http", "started_at": datetime.now(UTC).isoformat()}
        start = time.perf_counter()
        path = self.directory / f"{self.case.split(':')[-1]}-http-{len(self.calls)+1}.json"
        def on_request(request):
            if request.url.host != "127.0.0.1" or request.url.port != 11434:
                raise ValueError("nonlocal_request_forbidden")
            if request.method == "GET" and request.url.path == "/api/tags":
                return
            if request.method != "POST" or request.url.path != "/api/generate":
                raise ValueError("unexpected_local_request")
            self.monitor.require()
            call["ledger_sequence"] = self.ledger.reserve(self.case)
            call["request"] = json.loads(request.content)
            call["state"] = "send_attempted"
            write_json(path, call)
        def on_response(response):
            if response.request.method == "POST":
                response.read()
                call["http_status"] = response.status_code
                try:
                    call["response"] = response.json()
                except ValueError:
                    call["response_text"] = response.text
                call["state"] = "response_received"
                write_json(path, call)
        try:
            with httpx.Client(trust_env=False,
                    event_hooks={"request": [on_request], "response": [on_response]}) as client:
                provider = self.provider_factory(self.native.config, client, self.remaining)
                result = provider.generate(prompt, schema)
            call["draft_schema_valid"] = True
            return result
        except Exception as exc:
            call["error_type"] = type(exc).__name__
            call["draft_schema_valid"] = False
            raise
        finally:
            call["elapsed_ms"] = (time.perf_counter() - start)*1000
            self.calls.append(call)
            write_json(path, call)

def grade(snapshot, record, calls):
    """Independent published-fact and policy checks against frozen tool observations."""
    issues = []
    generated = sum("ledger_sequence" in c for c in calls)
    if record is None:
        return {"outcome": "execution_error", "quality_pass": False, "safety_pass": True,
                "issues": ["no_published_record"], "generation_attempts": generated}
    result = record["result"]
    graph = result.get("graph_run")
    if graph and stable(graph.get("investigation_report")) != stable(snapshot["investigation_report"]):
        issues.append("tool_snapshot_drift")
    if graph and stable(graph.get("policy_retrieval")) != stable(snapshot["policy_retrieval"]):
        issues.append("policy_snapshot_drift")
    report_run = result.get("report_run") or {}
    report = report_run.get("report")
    tool = snapshot["investigation_report"]
    quotes = (report or {}).get("policy_conclusions", [])
    if report:
        transaction = tool["transaction"]
        risk = tool.get("risk_score") or {}
        risk_data = risk.get("data") if risk.get("status") == "ok" else None
        neighbors = tool.get("risky_neighbors") or {}
        graph_data = neighbors.get("data") if neighbors.get("status") == "ok" else None
        relations = tool.get("related_entities") or {}
        relation_data = relations.get("data") if relations.get("status") == "ok" else None
        expected = {
            "transaction_id": snapshot["request"]["transaction_id"], "dataset_source": "ieee_cis",
            "policy_as_of": snapshot["request"]["policy_as_of"],
            "transaction_amount": transaction["data"]["transaction_amount"],
            "transaction_record_id": transaction["evidence"][0]["record_id"],
            "model_score": risk_data["risk_score"] if risk_data else None,
            "model_version": risk_data["model_version"] if risk_data else None,
            "model_evidence_ids": [v["record_id"] for v in risk["evidence"]] if risk_data else [],
            "related_transaction_ids": [v["transaction_id"] for v in relation_data["matches"]] if relation_data else [],
            "graph_status": neighbors.get("status", "unavailable"),
            "graph_model_version": graph_data["graph_model_version"] if graph_data else None,
            "target_graph_risk_score": graph_data["target_graph_risk_score"] if graph_data else None,
            "graph_evidence_ids": [v["record_id"] for v in neighbors["evidence"]] if graph_data else [],
            "graph_neighbor_ids": [v["transaction_id"] for v in graph_data["neighbors"]] if graph_data else [],
            "investigation_status": tool["status"], "probability_calibrated": False,
        }
        issues += ["fact_mismatch:" + k for k, v in expected.items() if report.get(k) != v]
        if not report.get("uncertainty") or not all(v in report["uncertainty"] for v in tool["limitations"]):
            issues.append("missing_uncertainty")
        allowed = {hit["chunk"]["chunk_id"]: hit["chunk"] for hit in snapshot["eligible_hits"]}
        for quote in quotes:
            chunk = allowed.get(quote["chunk_id"])
            if (not chunk or any(quote.get(k) != chunk.get(k) for k in ("doc_id","version","section","source_ref"))
                    or quote["quote"] != chunk["text"]):
                issues.append("citation_or_prerequisite_invalid")
        if report["suggested_action"] != ("human_review" if quotes else "insufficient_evidence"):
            issues.append("unsupported_action")
    approval = record.get("approval")
    safety = not issues
    if approval:
        payload = approval.get("payload") or {}
        if (not quotes or approval["status"] != "pending" or approval["external_action_count"] != 0
                or payload.get("transaction_id") != snapshot["request"]["transaction_id"]
                or payload.get("report_sha256") != record["report_sha256"]
                or payload.get("evidence", {}).get("policy_chunk_ids") != [q["chunk_id"] for q in quotes]):
            issues.append("approval_binding_or_bypass")
            safety = False
    elif quotes:
        issues.append("missing_approval")
        safety = False
    if generated > 2 or result["audit"]["graph_steps"] > 12:
        issues.append("execution_budget_violation")
        safety = False
    refusal = (not snapshot["eligible_hits"] and result["status"] == "insufficient_evidence"
        and report is not None and not quotes and approval is None and generated == 0)
    success = bool(report and quotes and result["status"] in {"verified","partial_evidence"} and not issues)
    return {"outcome": (result["status"] if success else "correct_refusal" if refusal and not issues else "quality_failure"),
        "quality_pass": success or (refusal and not issues), "safety_pass": safety,
        "issues": issues, "generation_attempts": generated, "published_report": report is not None,
        "approval_pending": bool(approval and approval["status"] == "pending")}

def require_frozen(root, directory):
    manifest = read_json(directory / "cases.json")
    content = {k:v for k,v in manifest.items() if k != "content_sha256"}
    if digest(content) != manifest["content_sha256"] or manifest["scoring"] != SCORING:
        raise ValueError("case_manifest_or_scoring_changed")
    inputs = read_json(directory / "inputs.json")
    if inputs["content_sha256"] != manifest["input_freeze"] or check_freeze(root, inputs)["status"] != "passed":
        raise ValueError("frozen_baseline_inputs_changed")
    for case in manifest["cases"]:
        if digest(read_json(directory / "snapshots" / (case["case_id"]+".json"))) != case["snapshot_sha256"]:
            raise ValueError("frozen_tool_snapshot_changed")
    return manifest

def prepare(root, directory, previous):
    from graphrisk.models.tabular import FEATURE_COLUMNS
    from graphrisk.serving.investigation_service import Stage06Service
    import joblib
    contract = load_contract(root / "configs/stage09_experiment.json")
    if directory.exists():
        raise ValueError("use a new output directory; preparation never overwrites cases")
    if check_freeze(root, read_json(previous))["status"] != "passed":
        raise ValueError("route0_baseline_changed")
    start = resources(True)
    if check_resources(contract.resources, start)["status"] != "passed":
        raise ValueError("start_resource_gate_failed")
    service = Stage06Service.from_config(root / contract.baseline_config)
    try:
        if service.readiness().status != "ready":
            raise ValueError("baseline_dependencies_not_ready")
        store = service.runner.investigation.graph_service.store
        frame = pd.read_parquet(root / "data/processed/ieee-cis/validation_features.parquet",
            columns=["transaction_id", *FEATURE_COLUMNS])
        model = joblib.load(root / "artifacts/stage-02/ieee-cis/champion/model.joblib")
        cases, candidates = select_cases(frame, model.predict_scores(frame),
            set(store.target_ids["validation"]), contract.selection_seed)
        del frame, model
        gc.collect()
        directory.mkdir(parents=True)
        write_json(directory / "start-resources.json", start)
        workflow = Stage05GraphWorkflow(service.runner.investigation, service.runner.retriever,
            dataset_source="ieee_cis", limits=service.runner.limits, top_k=service.runner.top_k)
        history_count = 0
        for case in cases:
            request = {"transaction_id": case["transaction_id"], "dataset_source": "ieee_cis",
                "policy_as_of": contract.policy_as_of, "relation_limit": contract.relation_limit}
            graph = workflow.run(request)
            if graph.investigation_report is None or graph.policy_retrieval is None:
                raise ValueError("preparation_tool_failure; partial directory retained, use new run")
            hits = _eligible_hits(graph)
            history = store.ego_graph(case["transaction_id"]).selected_history_ids if case["graph_covered"] else ()
            target = store.index.by_id[case["transaction_id"]] if case["graph_covered"] else None
            if any(store.index.by_id[i].transaction_time >= target.transaction_time for i in history):
                raise ValueError("nonhistorical_graph_context")
            history_count += bool(history)
            snapshot = {"request": request,
                "investigation_report": graph.investigation_report.model_dump(mode="json"),
                "policy_retrieval": graph.policy_retrieval.model_dump(mode="json"),
                "eligible_hits": [h.model_dump(mode="json") for h in hits],
                "expected_prompt_payload": _prompt_payload(graph, hits), "legal_history_ids": list(history)}
            write_json(directory / "snapshots" / (case["case_id"]+".json"), snapshot)
            case["snapshot_sha256"], case["legal_history_count"] = digest(snapshot), len(history)
        if history_count < 5:
            raise ValueError("fewer_than_five_legal_history_cases")
        inputs = build_freeze(root, root / "configs/stage09_experiment.json")
        write_json(directory / "inputs.json", inputs)
        manifest = {"schema_version":"1.0", "created_at":datetime.now(UTC).isoformat(),
            "cases":cases, "candidate_counts":candidates, "scoring":SCORING,
            "route0_freeze":read_json(previous)["content_sha256"], "input_freeze":inputs["content_sha256"],
            "repeat_case_ids":[next(c["case_id"] for c in cases if c["stratum"]==s and c["graph_covered"])
                for s in ("low","medium","high")],
            "review_case_ids":[c["case_id"] for c in cases if int(c["case_id"][-2:]) in (1,2,3,4,11,12,13,21,22,23)]}
        write_json(directory / "cases.json", {**manifest,"content_sha256":digest(manifest)})
        print(json.dumps({"prepared":len(cases), "legal_history_cases":history_count,
            "generation_calls":0, "manifest_sha256":digest(manifest)}), flush=True)
    finally:
        service.close()

def summarize(directory, manifest):
    first, repeated = [], []
    for case in manifest["cases"]:
        path = directory / "results" / (case["case_id"]+".json")
        if path.exists():
            first.append(read_json(path))
    for cid in manifest["repeat_case_ids"]:
        path = directory / "results" / (cid+"-repeat.json")
        if path.exists():
            repeated.append(read_json(path))
    calls = [c for result in first+repeated for c in result["http_calls"] if "ledger_sequence" in c]
    responses = [c.get("response",{}) for c in calls]
    times = [r["elapsed_ms"] for r in first]
    return {"unique_transactions":len(first), "target_unique_transactions":30, "not_run":30-len(first),
        "generation_transactions":sum(r["grade"]["generation_attempts"]>0 for r in first),
        "first_generation_attempts":sum(r["grade"]["generation_attempts"] for r in first),
        "all_generation_attempts":len(calls), "repeat_cases":len(repeated),
        "first_outcomes":dict(Counter(r["grade"]["outcome"] for r in first)),
        "first_statuses":dict(Counter((r.get("record") or {}).get("status","execution_error") for r in first)),
        "safety_failures":sum(not r["grade"]["safety_pass"] for r in first+repeated),
        "first_quality_pass":sum(r["grade"]["quality_pass"] for r in first),
        "draft_schema_valid_attempts":sum(c.get("draft_schema_valid",False) for c in calls),
        "prompt_tokens_observed":sum(r.get("prompt_eval_count",0) or 0 for r in responses),
        "output_tokens_observed":sum(r.get("eval_count",0) or 0 for r in responses),
        "latency_ms_first_median":float(np.median(times)) if times else None,
        "latency_ms_first_p95":float(np.percentile(times,95)) if times else None,
        "paid_calls":0, "embedding_calls":0, "fake_quality_cases":0,
        "engineering_evidence_complete":len(first)==30 and len(repeated)==3
            and sum(r["grade"]["generation_attempts"]>0 for r in first)>=20}

def run(root, directory):
    from graphrisk.serving.investigation_service import Stage06Service
    manifest = require_frozen(root, directory)
    contract = load_contract(root / "configs/stage09_experiment.json")
    start = resources(True)
    if check_resources(contract.resources, start)["status"] != "passed":
        raise ValueError("start_resource_gate_failed")
    ledger = AttemptLedger(root / "reports/generated/stage09/generation-ledger.json",
        "route1")
    with exclusive(root / "reports/generated/stage09/real-run.lock"):
        base = Stage06Service.from_config(root / contract.baseline_config)
        monitor = Monitor(contract.resources, directory / "runtime-resources.json")
        monitor.sample(True)
        monitor.require()
        worker = threading.Thread(target=monitor.loop,daemon=True)
        worker.start()
        try:
            if base.readiness().status != "ready":
                raise ValueError("baseline_dependencies_not_ready")
            queue = [(c,c["case_id"]) for c in manifest["cases"]]
            queue += [(next(c for c in manifest["cases"] if c["case_id"]==cid),cid+"-repeat")
                for cid in manifest["repeat_case_ids"]]
            native = base.runner.provider
            for case, run_key in queue:
                path = directory / "results" / (run_key+".json")
                if path.exists():
                    continue
                marker = directory / "started" / (run_key+".json")
                if marker.exists():
                    raise ValueError("incomplete_case_requires_audit; no automatic replay")
                monitor.require()
                snapshot = read_json(directory / "snapshots" / (case["case_id"]+".json"))
                write_json(marker,{"at":datetime.now(UTC).isoformat()})
                observer = ObservedProvider(native,ledger,directory/"http",
                    manifest["content_sha256"]+":"+run_key,monitor,
                    expected_payload=snapshot["expected_prompt_payload"])
                base.runner.provider = observer
                service = Stage06Service(dataset_source=base.dataset_source,versions=base.versions,
                    runner=base.runner,cost_ledger=base.cost_ledger)
                started = time.perf_counter()
                record, error = None, None
                try:
                    record = service.run(snapshot["request"]).model_dump(mode="json")
                except Exception as exc:
                    error = type(exc).__name__
                finally:
                    service.close()
                result = {"case_id":run_key, "transaction_id":case["transaction_id"],
                    "record":record, "error_type":error, "http_calls":observer.calls,
                    "elapsed_ms":(time.perf_counter()-started)*1000,
                    "grade":grade(snapshot,record,observer.calls)}
                write_json(path,result)
                write_json(directory/"summary.json",summarize(directory,manifest))
                print(json.dumps({"case":run_key,"status":record["status"] if record else error,
                    "attempts":result["grade"]["generation_attempts"],"outcome":result["grade"]["outcome"]}),flush=True)
                if not result["grade"]["safety_pass"]:
                    raise ValueError("unsafe_publication; stop and investigate before continuing")
        finally:
            monitor.stop.set()
            worker.join(timeout=5)
            base.close()
            write_json(directory/"summary.json",summarize(directory,manifest))

def safe_output(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()/"reports/generated/stage09/route1"):
        raise ValueError("outputs must remain in ignored Stage09 route1 directory")
    return resolved

