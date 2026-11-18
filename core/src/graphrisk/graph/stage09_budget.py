"""Durable Stage09 GNN budget; failed/crashed attempts cannot refund quota."""
from datetime import UTC, datetime
import time
from graphrisk.demo.stage09_evaluation import exclusive, read_json, write_json

class GraphBudgetLedger:
    def __init__(self, path, limits):
        self.path, self.limits = path, limits

    def read(self):
        return read_json(self.path) if self.path.exists() else {"runs": [], "work": [], "test_batches": []}

    def spent(self, value):
        return sum(entry.get("seconds", entry["cap_seconds"]) if entry["state"] != "reserved"
                   else entry["cap_seconds"] for entry in value["runs"] + value["work"])

    def reserve(self, kind, name, registration, clock=time.perf_counter):
        if kind not in ("runs", "work"):
            raise ValueError("invalid budget reservation kind")
        with exclusive(self.path.with_suffix(".lock")):
            value = self.read()
            if value["test_batches"]:
                raise ValueError("final test sealed; no more candidate training")
            if any(e["name"] == name for e in value["runs"] + value["work"]):
                raise ValueError("attempt already reserved/completed; reuse evidence")
            if any(e["state"] == "reserved" for e in value["runs"] + value["work"]):
                raise ValueError("unresolved previous attempt; fail closed, preserve its budget")
            if kind == "runs" and len(value["runs"]) >= self.limits.max_new_gnn_runs:
                raise ValueError("new GNN run quota exhausted")
            remaining = self.limits.max_total_training_seconds - self.spent(value)
            cap = min(self.limits.max_run_seconds, remaining)
            if cap <= 0:
                raise ValueError("GNN cumulative wall-clock budget exhausted")
            entry = {"name": name, "registration": registration, "state": "reserved",
                "cap_seconds": cap, "at": datetime.now(UTC).isoformat()}
            value[kind].append(entry)
            write_json(self.path, value)
        started = clock()
        return BudgetAttempt(self, kind, name, started, cap, clock)

    def finish(self, kind, name, state, seconds):
        with exclusive(self.path.with_suffix(".lock")):
            value = self.read()
            entry = next(e for e in value[kind] if e["name"] == name)
            if entry["state"] != "reserved":
                raise ValueError("attempt already finalized")
            entry.update(state=state, seconds=seconds)
            write_json(self.path, value)

    def seal_test(self, decision):
        with exclusive(self.path.with_suffix(".lock")):
            value = self.read()
            if any(e["state"] == "reserved" for e in value["runs"] + value["work"]):
                raise ValueError("candidate work unresolved")
            if len(value["test_batches"]) >= self.limits.maximum_final_test_batches:
                raise ValueError("final test batch quota exhausted")
            value["test_batches"].append({"decision_sha256": decision, "state": "reserved_before_label_read",
                "at": datetime.now(UTC).isoformat()})
            write_json(self.path, value)

    def finish_test(self, state, seconds):
        with exclusive(self.path.with_suffix(".lock")):
            value = self.read()
            entry = value["test_batches"][-1]
            if entry["state"] != "reserved_before_label_read":
                raise ValueError("final review already finalized")
            entry.update(state=state, seconds=seconds)
            write_json(self.path, value)

class BudgetAttempt:
    def __init__(self, ledger, kind, name, started, cap, clock):
        self.ledger, self.kind, self.name = ledger, kind, name
        self.started, self.cap, self.clock = started, cap, clock

    def guard(self):
        if self.clock() - self.started >= self.cap:
            raise TimeoutError("Stage09 GNN wall-clock deadline reached")

    def finish(self, state):
        self.ledger.finish(self.kind, self.name, state, self.clock() - self.started)

