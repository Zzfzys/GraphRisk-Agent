"""Serve the unchanged compact QA engine behind a separate web continuation gate."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from fastapi import FastAPI

from extensions.case_qa_compact_v1.runtime import components, service
from extensions.case_qa_live_v1.api import router
from extensions.case_qa_v1.sessions import SessionError, digest
from graphrisk.agent.upgrade_contract import file_hash
from .gate import WebResumeGate

REVISION = "case-qa-web-v1"


class WebService:
    def __init__(self, current, archive, gate, manifest):
        self.current, self.archive, self.gate, self.manifest = current, archive, gate, manifest

    def owner(self, identifier):
        return self.archive if identifier == self.manifest["archive_conversation_id"] else self.current

    def present(self, value):
        value = deepcopy(value)
        status = self.gate.status()
        value["web_gate"] = status
        value["web_gate_revision"] = REVISION
        if not status["can_send"] or value["case_alias"] != self.manifest["allowed_case_alias"]:
            value["send_block_reason"] = "qa_disabled"
        return value

    def get(self, identifier):
        return self.present(self.owner(identifier).get(identifier))

    def create(self, body, key):
        return self.present(self.current.create(body, key))

    def created(self, key):
        return self.present(self.current.created(key))

    def evidence(self, identifier):
        return self.owner(identifier).evidence(identifier)

    def raw(self, identifier, message_id):
        return self.owner(identifier).raw(identifier, message_id)

    def control(self, identifier, body):
        if self.owner(identifier) is self.archive:
            raise SessionError("qa_disabled")
        return self.present(self.current.control(identifier, body))

    def audit(self, event, identifier, body, result=None):
        item = {"at":datetime.now(timezone.utc).isoformat(), "event":event,
            "manifest_sha256":file_hash(self.gate.path),
            "approval_sha256":file_hash(Path(self.manifest["resume_approval_path"])),
            "conversation_id":identifier, "request_sha256":digest(body)}
        if result is not None:
            item["budget_audit"] = result["turn"]["result"].get("budget_audit")
            item["status"] = result["turn"]["result"]["status"]
        try:
            with Path(self.manifest["audit_path"]).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False)+"\n")
        except OSError:
            raise SessionError("preflight_failed" if result is None else "checkpoint_recovery_required") from None

    def follow(self, identifier, body):
        if self.owner(identifier) is self.archive:
            raise SessionError("qa_disabled")
        # Missing approval is rejected before touching a conversation or a provider.
        status = self.gate.status()
        if not status["can_send"]:
            # A saved ID can still be replayed by the unchanged idempotent engine.
            saved = self.current.store.get(identifier)
            if type(body) is dict and any(t["message_id"] == body.get("message_id") for t in saved["turns"]):
                return self.current.follow(identifier, body)
            raise SessionError("qa_disabled")
        value = self.current.store.get(identifier)
        if type(body) is dict and any(t["message_id"] == body.get("message_id") for t in value["turns"]):
            return self.current.follow(identifier, body)
        if value["case_alias"] != status["allowed_case_alias"]:
            raise SessionError("qa_disabled")
        self.audit("before_follow", identifier, body)
        result = self.current.follow(identifier, body)
        self.audit("saved_result", identifier, body, result)
        return result


def build_app(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive_path = Path(manifest["archive_trial_path"])
    if file_hash(archive_path) != manifest["archive_trial_sha256"]:
        raise ValueError("archive_trial_changed")
    archive_trial = json.loads(archive_path.read_text(encoding="utf-8"))
    if not any(item["conversation_id"] == manifest["archive_conversation_id"] for item in archive_trial["cases"]):
        raise ValueError("archive_conversation_changed")
    cfg, _, budget = components(manifest["config_path"])
    if (archive_path.resolve() != Path(cfg["trial_result_path"]).resolve()
            or not Path(cfg["conversation_path"]).is_file()
            or archive_trial["cases"][0]["conversation_id"] != manifest["archive_conversation_id"]
            or archive_trial["cases"][0]["case_alias"] != cfg["cases"][1]["case_alias"]):
        raise ValueError("archive_scope_invalid")
    # Only these two new local outputs may be written by the web facade.
    for field, name in (("conversation_path", "conversations.sqlite3"), ("audit_path", "requests.jsonl")):
        path = Path(manifest[field])
        if not path.is_absolute() or path.resolve() != manifest_path.parent / name:
            raise ValueError("web_output_path_invalid")
        if path.resolve() in {Path(cfg[k]).resolve() for k in ("conversation_path", "ledger_path", "trial_result_path")}:
            raise ValueError("web_output_path_invalid")
    gate = WebResumeGate(manifest_path, budget)
    # New local conversations never rewrite the two archived trial turns.
    current_cfg = {**cfg, "conversation_path":manifest["conversation_path"]}
    facade = WebService(service(current_cfg, budget), service(cfg, budget), gate, manifest)
    app = FastAPI(title="GraphRisk 本案问答网页接口")
    app.state.case_qa_service = facade
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status":"ok", "web_gate_revision":REVISION, "model_calls_on_start":0}

    @app.get("/api/v1/case-qa/web-status")
    def status():
        return {**gate.status(), "web_gate_revision":REVISION,
            "archive_conversation_id":manifest["archive_conversation_id"],
            "m1_transaction_id":cfg["case_files"][0]["transaction_id_local_only"]}
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(build_app(args.manifest), host="127.0.0.1", port=8003)


if __name__ == "__main__":
    main()
