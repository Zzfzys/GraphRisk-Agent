"""Fixture-only QA purpose in the existing shared-ledger schema; no live hooks."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

import httpx

from graphrisk.agent.autonomous_budget import ExecutionStop, InvestigationBudget
from graphrisk.agent.autonomous_http import post_one
from graphrisk.agent.autonomous_providers import cloud_usage, reservation_micro
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

VERSION = "case-qa-budget-fixture-v1"
HISTORY_SQL = ("SELECT a.attempt_id,a.provider,a.state,a.reserved_micro_cny,r.request_key,r.phase,"
               "r.packet_sha,r.accounted,r.receipt_sha,r.receipt_path,r.result_code,r.identity,r.usage_json,r.integrity_stop "
               "FROM model_attempts a JOIN execution_requests r USING(attempt_id) ORDER BY a.rowid")
ABSOLUTE = {"total":2119,"deepseek":1771,"local":348}
CAPS = {"session_calls":3,"case_calls":3,"batch_calls":6,"batch_micro_cny":5_000_000,
        "absolute":ABSOLUTE,"route9_reserved_calls":200,"web_reserved_calls":6,
        "pause_micro_cny":80_000_000,"total_micro_cny":100_000_000}
LIMITS = {"context_bytes":32768,"max_output_bytes":32768,"max_http_response_bytes":131072}


def require(ok, code):
    if not ok:
        raise ExecutionStop(code)


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raise ExecutionStop("qa_config_invalid") from None


class _HistoryView:
    """Only the parent's frozen history query is projected, never counts/sums."""
    def __init__(self, db, excluded, quarantined=None):
        self.db, self.excluded, self.projected = db, tuple(excluded), 0
        self.quarantined, self.nested_projected = quarantined, 0

    def execute(self, sql, parameters=()):
        if sql == HISTORY_SQL:
            require(not parameters, "qa_history_projection_invalid")
            self.projected += 1
            if self.excluded:
                sql = sql.replace(" ORDER BY a.rowid", " WHERE a.attempt_id NOT IN (" + ",".join("?" for _ in self.excluded) + ") ORDER BY a.rowid")
                return self.db.execute(sql, self.excluded)
        quarantine_sql = HISTORY_SQL.replace(" ORDER BY a.rowid"," WHERE a.attempt_id<>? ORDER BY a.rowid")
        if self.quarantined is not None and sql == quarantine_sql:
            require(tuple(parameters) == (self.quarantined,),"qa_history_projection_invalid")
            self.nested_projected += 1
            if self.excluded:
                sql = sql.replace(" ORDER BY a.rowid"," AND a.attempt_id NOT IN (" + ",".join("?" for _ in self.excluded) + ") ORDER BY a.rowid")
                return self.db.execute(sql,tuple(parameters)+self.excluded)
        return self.db.execute(sql, parameters)


class CaseQABudget(InvestigationBudget):
    def __init__(self, path, base_contract, config_path, *, parent_protocol, transport):
        self.config_path = Path(config_path).resolve()
        self.config = read(self.config_path)
        self.config_sha = file_hash(self.config_path)
        self.parent_protocol, self.transport = parent_protocol, transport
        path = Path(path).resolve()
        require(type(transport) is httpx.MockTransport and callable(getattr(parent_protocol, "_partition", None)), "qa_fixture_only")
        require(path.is_file(), "qa_fixture_only")
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                require(db.execute("SELECT version,mode FROM execution_settings WHERE id=1").fetchone() == (2,"fixture"), "qa_fixture_only")
        except sqlite3.Error:
            raise ExecutionStop("qa_fixture_only") from None
        self._validate_config(path, base_contract)
        super().__init__(path, base_contract, mode="fixture")
        with self.transaction() as db:
            self._partition(db)
            db.execute("CREATE TABLE IF NOT EXISTS case_qa_grants_v1 (campaign_id TEXT PRIMARY KEY, config_path TEXT NOT NULL, config_sha TEXT NOT NULL, authorization TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS case_qa_requests_v1 (attempt_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, phase TEXT UNIQUE NOT NULL, session_id TEXT NOT NULL, case_key TEXT NOT NULL, message_id TEXT NOT NULL, request_json TEXT NOT NULL, packet_json TEXT NOT NULL, reservation INTEGER NOT NULL, transport_started INTEGER NOT NULL DEFAULT 0)")
            wanted = (self.config["campaign"],str(self.config_path),self.config_sha,"fixture_only_not_outbound")
            old = db.execute("SELECT * FROM case_qa_grants_v1 WHERE campaign_id=?",(wanted[0],)).fetchone()
            require(old is None or old == wanted,"qa_grant_changed")
            if old is None:
                db.execute("INSERT INTO case_qa_grants_v1 VALUES (?,?,?,?)",wanted)

    def _validate_config(self, path=None, contract=None):
        cfg = self.config
        require(file_hash(self.config_path) == self.config_sha and read(self.config_path) == cfg,"qa_config_changed")
        require(cfg.get("version") == VERSION and cfg.get("purpose") == "case_qa" and cfg.get("fixture") is True,"qa_fixture_only")
        require(isinstance(cfg.get("campaign"),str) and re.fullmatch(r"case-qa-[A-Za-z0-9_-]{1,80}",cfg["campaign"]),"qa_config_invalid")
        require(Path(cfg["ledger_path"]).resolve() == (path or self.path).resolve(),"qa_ledger_binding_changed")
        require(cfg["base_contract_sha256"] == digest(contract or self.contract),"qa_base_contract_changed")
        require(cfg["caps"] == CAPS,"qa_caps_changed")
        require(cfg["limits"] == LIMITS,"qa_limits_changed")
        p = cfg["profile"]
        require(p["deepseek"]["endpoint"] == "https://api.deepseek.com/chat/completions"
            and p["deepseek"]["model"] == "deepseek-flash" and p.get("pricing_scope") == "fixture_only_not_current_quote"
            and type(p["max_output_tokens"]) is int and 0 < p["max_output_tokens"] <= 2048
            and type(p["timeout_seconds"]) in (int,float) and 0 < p["timeout_seconds"] <= 120
            and type(p["deepseek"]["context_limit"]) is int and 0 < p["deepseek"]["context_limit"] <= 131072
            and 0 < reservation_micro(p) <= CAPS["batch_micro_cny"],"qa_profile_invalid")
        require(isinstance(cfg["expected_identity"],str) and len(cfg["expected_identity"]) <= 512
            and re.fullmatch(r"[0-9a-f]{64}",cfg["system_prompt_sha256"]),"qa_profile_invalid")
        identity = json.loads(cfg["expected_identity"])
        require(type(identity) is list and len(identity) == 2 and type(identity[0]) is str and identity[0] in {p["deepseek"]["model"],p["deepseek"].get("documented_revision")}
            and type(identity[1]) is str and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}",identity[1]),"qa_profile_invalid")
        require(type(cfg["cases"]) is list and len(cfg["cases"]) == 2 and len({c["case_alias"] for c in cfg["cases"]}) == 2,"case_qa_not_authorized")
        require(type(cfg.get("files")) is dict and cfg["files"].get(str(Path(__file__).resolve())) == file_hash(Path(__file__)),"qa_source_binding_missing")
        for name,sha in cfg["files"].items():
            require(Path(name).is_file() and file_hash(Path(name)) == sha,"qa_bound_file_changed")
        require(type(cfg.get("parent_bindings")) is dict and bool(cfg["parent_bindings"]),"qa_parent_binding_changed")
        for name,sha in cfg["parent_bindings"].items():
            require(Path(name).is_file() and file_hash(Path(name)) == sha,"qa_parent_binding_changed")

    def _grant(self, db):
        self._validate_config()
        require(db.execute("SELECT * FROM case_qa_grants_v1 WHERE campaign_id=?",(self.config["campaign"],)).fetchone()
            == (self.config["campaign"],str(self.config_path),self.config_sha,"fixture_only_not_outbound"),"qa_grant_changed")

    def _case(self, value):
        c = value["case"]
        e = c["evidence"]
        scope = {"case_alias":c["case_alias"],"binding":c["binding"],"batch_revision":c["batch_revision"],
                 "evidence_version":e["evidence_version"],"view_sha256":e["view_sha256"]}
        require(scope in self.config["cases"],"case_qa_not_authorized")
        require(e["case_alias"] == scope["case_alias"] and e["view_sha256"] == digest({k:v for k,v in e.items() if k != "view_sha256"}),"case_qa_not_authorized")
        return scope,digest(scope)

    def payload(self, packet):
        require(type(packet) is dict and set(packet) == {"purpose","system_prompt","view"} and packet["purpose"] == "case_qa","qa_packet_invalid")
        require(hashlib.sha256(packet["system_prompt"].encode("utf-8")).hexdigest() == self.config["system_prompt_sha256"],"qa_prompt_changed")
        require(len(canonical(packet).encode("utf-8")) <= LIMITS["context_bytes"],"qa_context_limit")
        question = packet["view"]["question"]
        require(type(question) is str and question.strip() and len(question) <= 2000,"qa_packet_invalid")
        return {"model":self.config["profile"]["deepseek"]["model"],
            "messages":[{"role":"system","content":packet["system_prompt"]},{"role":"user","content":canonical(packet["view"])}],
            "temperature":0,"max_tokens":self.config["profile"]["max_output_tokens"],"thinking":{"type":"disabled"},
            "stream":False,"response_format":{"type":"json_object"}}

    def _partition(self, db):
        self._validate_config()
        rows = db.execute(HISTORY_SQL).fetchall()
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='case_qa_requests_v1'").fetchone()
        qa = db.execute("SELECT * FROM case_qa_requests_v1").fetchall() if exists else []
        by_attempt = {r[0]:r for r in rows}
        excluded = []
        for entry in qa:
            attempt,key,phase,session,case_key,message,request_json,packet_json,reservation,started = entry
            require(attempt in by_attempt and started in (0,1),"qa_request_links_invalid")
            request,packet = json.loads(request_json),json.loads(packet_json)
            require(type(request) is dict and set(request) == {"message_id","parent_turn_id","evidence_version","message"}
                and (request["parent_turn_id"] is None or (type(request["parent_turn_id"]) is str and re.fullmatch(r"[0-9a-f]{32}",request["parent_turn_id"])))
                and re.fullmatch(r"[0-9a-f]{32}",session) and re.fullmatch(r"[0-9a-f]{32}",message),"qa_request_binding_changed")
            scopes = [c for c in self.config["cases"] if digest(c) == case_key]
            require(len(scopes) == 1 and packet["view"]["evidence"]["case_alias"] == scopes[0]["case_alias"]
                and packet["view"]["evidence"]["view_sha256"] == scopes[0]["view_sha256"]
                and request["message"] == packet["view"]["question"] and request["message_id"] == message
                and request["evidence_version"] == scopes[0]["evidence_version"],"qa_request_binding_changed")
            evidence = packet["view"]["evidence"]
            require(evidence["view_sha256"] == digest({k:v for k,v in evidence.items() if k != "view_sha256"}),"qa_request_binding_changed")
            require(db.execute("SELECT manifest_sha,cloud_cap,local_cap FROM execution_phases WHERE phase=?",(phase,)).fetchone() == (digest(packet),1,0),"qa_request_binding_changed")
            payload = self.payload(packet)
            require(key == digest([self.config_sha,session,message]) and phase == "case-qa-"+key
                and reservation == reservation_micro(self.config["profile"]),"qa_request_binding_changed")
            row = by_attempt[attempt]
            require(row[1] == "deepseek" and row[2] in ("reserved","unknown","settled")
                and row[3] == reservation and row[4:7] == (key,phase,digest(payload)),"qa_request_binding_changed")
            require((row[7] == reservation) if row[2] != "settled" else (type(row[7]) is int and row[7] >= 0),"qa_accounting_changed")
            require((row[2] == "reserved" and row[8] is None) or started == 1,"qa_request_binding_changed")
            if row[8] is not None:
                path = Path(row[9]).resolve()
                require(path.is_relative_to((self.config_path.parent/"receipts").resolve()) and path.is_file()
                    and file_hash(path) == row[8],"qa_receipt_changed")
                receipt = read(path)
                require(receipt["attempt_id"] == attempt and receipt["payload_sha256"] == row[6]
                    and receipt["profile_sha256"] == digest(self.config["profile"]),"qa_receipt_changed")
                if row[2] == "settled":
                    usage,cost = cloud_usage(receipt["body"],self.config["profile"])
                    require(not self._hard(receipt["body"],usage,cost,reservation) or row[-1] == 1,"qa_accounting_changed")
                    require(canonical(usage) == row[12] and cost == row[7]
                        and row[11] == canonical([receipt["body"].get("model"),receipt["body"].get("system_fingerprint")]),"qa_accounting_changed")
            else:
                require(row[2] != "settled","qa_receipt_changed")
            excluded.append(attempt)
        recovery = getattr(self.parent_protocol,"recovery_cfg",None)
        quarantined = recovery["unknown"]["attempt_id"] if recovery else None
        view = _HistoryView(db,excluded,quarantined)
        old_rows,web,route9 = self.parent_protocol._partition(view)
        require(view.projected == 1 and view.nested_projected == (1 if quarantined is not None else 0) and old_rows == [r for r in rows if r[0] not in set(excluded)],"qa_history_projection_invalid")
        return rows,[by_attempt[a] for a in excluded],web,route9

    def _status(self, db, value=None):
        self._grant(db)
        rows,qa,web,route9 = self._partition(db)
        unresolved = [r for r in qa if r[2] in ("reserved","unknown")]
        reason = "ledger_unknown_paused" if unresolved else "budget_integrity_stop" if any(r[-1] for r in qa) else None
        entries = db.execute("SELECT attempt_id,session_id,case_key,transport_started FROM case_qa_requests_v1").fetchall()
        session_used = case_used = None
        if value is not None:
            _,key = self._case(value)
            session_used = sum(e[1] == value["conversation_id"] for e in entries)
            case_used = sum(e[2] == key for e in entries)
            reason = reason or ("quota_exhausted" if session_used >= 3 else "quota_exhausted" if case_used >= 3 else None)
        cost,needed = sum(r[7] for r in qa),reservation_micro(self.config["profile"])
        reason = reason or ("quota_exhausted" if len(qa) >= 6 else None)
        reason = reason or ("budget_exhausted" if cost+needed > 5_000_000 else None)
        total_cost = sum(r[7] for r in rows)
        reason = reason or ("budget_exhausted" if total_cost+needed > 80_000_000 else None)
        route9_left,web_left = 200-len(route9),6-len(web)
        counts = {p:sum(r[1] == p for r in rows) for p in ("deepseek","local")}
        protected = (len(rows)+1+route9_left+web_left <= 2119 and counts["deepseek"]+1+web_left <= 1771 and counts["local"]+route9_left <= 348)
        reason = reason or (None if protected else "global_quota_protected")
        identity = db.execute("SELECT identity FROM provider_identity WHERE provider='deepseek'").fetchone()
        reason = reason or (None if identity == (self.config["expected_identity"],) else "budget_integrity_stop")
        session_ids = {e[0] for e in entries if value is None or e[1] == value["conversation_id"]}
        confirmed = sum(r[0] in session_ids and (r[8] is not None or (r[2] == "unknown" and r[10] in {"qa_transport_unknown","qa_receipt_save_failed"})) for r in qa)
        def money(amount):
            return format(Decimal(amount)/1_000_000,".6f")
        return {"scope":"case_qa_fixture","provider":"mock","model":self.config["profile"]["deepseek"]["model"],"real_deepseek_attempts":0,
            "mock_attempts_confirmed":confirmed,"unresolved_slots":sum(r[0] in session_ids for r in unresolved),
            "batch_unresolved_slots":len(unresolved),
            "session_remaining_calls":3 if session_used is None else max(0,3-session_used),
            "case_remaining_calls":3 if case_used is None else max(0,3-case_used),"batch_remaining_calls":max(0,6-len(qa)),
            "cost_estimate_cny":money(sum(r[7] for r in qa if r[0] in session_ids and r[2] == "settled")),
            "batch_cost_estimate_cny":money(sum(r[7] for r in qa if r[2] == "settled")),
            "unsettled_hold_cny":money(sum(r[7] for r in unresolved)),
            "global_unsettled_hold_cny":money(sum(r[7] for r in rows if r[2] in ("reserved","unknown"))),
            "budget_status":"offline_ledger","authorization":"fixture_only_not_outbound","block_reason":reason,
            "global_calls":len(rows),"global_provider_counts":counts,"global_accounted_micro_cny":total_cost,
            "global_remaining_calls":2119-len(rows),"global_deepseek_remaining_calls":1771-counts["deepseek"],
            "protected_route9_calls":route9_left,"protected_web_calls":web_left,
            "cost_kind":"usage_estimate_not_invoice"}

    def usage(self, value=None):
        with sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True) as db:
            return self._status(db,value)

    def reserve(self, value, request, packet):
        require(re.fullmatch(r"[0-9a-f]{32}",value["conversation_id"]) and re.fullmatch(r"[0-9a-f]{32}",request["message_id"]),"qa_packet_invalid")
        scope,case_key = self._case(value)
        require(packet["view"]["evidence"] == value["case"]["evidence"] and request["message"] == packet["view"]["question"]
            and request["evidence_version"] == scope["evidence_version"],"case_qa_not_authorized")
        payload = self.payload(packet)
        key = digest([self.config_sha,value["conversation_id"],request["message_id"]])
        phase,reservation = "case-qa-"+key,reservation_micro(self.config["profile"])
        try:
            with self.transaction() as db:
                self._check_registration(db)
                self._check_request_links(db)
                require(not db.execute("SELECT 1 FROM execution_requests WHERE request_key=?",(key,)).fetchone(),"qa_request_already_reserved")
                status = self._status(db,value)
                require(status["block_reason"] is None,status["block_reason"])
                attempt = uuid4().hex
                db.execute("INSERT INTO execution_phases VALUES (?,?,?,?)",(phase,digest(packet),1,0))
                db.execute("INSERT INTO model_attempts VALUES (?,?,'reserved',?)",(attempt,"deepseek",reservation))
                db.execute("INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES (?,?,?,?,?)",(attempt,key,phase,digest(payload),reservation))
                db.execute("INSERT INTO case_qa_requests_v1 VALUES (?,?,?,?,?,?,?,?,?,0)",(attempt,key,phase,value["conversation_id"],case_key,request["message_id"],canonical(request),canonical(packet),reservation))
        except ExecutionStop as error:
            if str(error) == "execution_storage_unavailable":
                raise ExecutionStop("checkpoint_recovery_required") from None
            raise
        return {"attempt_id":attempt,"request_key":key,"phase":phase,"payload_sha256":digest(payload),
            "reserved_micro_cny":reservation,"transport_attempts":0,"mode":"fixture_only","state":"reserved",
            "cost_estimate_micro_cny":None,"hold_micro_cny":reservation}

    def _audit(self, ticket, *, attempts, replayed=False):
        with sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True) as db:
            row = db.execute("SELECT a.state,r.accounted,r.receipt_sha,r.receipt_path,r.result_code,r.integrity_stop FROM model_attempts a JOIN execution_requests r USING(attempt_id) WHERE attempt_id=?",(ticket["attempt_id"],)).fetchone()
        return {**ticket,"state":row[0],"accounted_micro_cny":row[1],"receipt_sha256":row[2],"receipt_path":row[3],
            "result_code":row[4],"integrity_stop":bool(row[5]),"transport_attempts":attempts,"replayed_without_model_request":replayed,
            "cost_estimate_micro_cny":row[1] if row[0] == "settled" else None,"hold_micro_cny":row[1] if row[0] != "settled" else 0,
            "cost_kind":"usage_estimate_not_invoice"}

    def generate(self, ticket, packet):
        require(type(self.transport) is httpx.MockTransport,"qa_fixture_only")
        self._validate_config()
        payload = self.payload(packet)
        with self.transaction() as db:
            self._grant(db)
            self._partition(db)
            row = db.execute("SELECT q.packet_json,q.request_key,q.phase,q.reservation,q.transport_started,a.state,r.receipt_path FROM case_qa_requests_v1 q JOIN model_attempts a USING(attempt_id) JOIN execution_requests r USING(attempt_id) WHERE q.attempt_id=?",(ticket["attempt_id"],)).fetchone()
            require(row and row[:4] == (canonical(packet),ticket["request_key"],ticket["phase"],ticket["reserved_micro_cny"])
                and ticket["payload_sha256"] == digest(payload) and ticket["mode"] == "fixture_only","qa_ticket_changed")
            replay = row[4] == 1
            if not replay:
                require(row[5] == "reserved","qa_request_state_invalid")
                db.execute("UPDATE case_qa_requests_v1 SET transport_started=1 WHERE attempt_id=?",(ticket["attempt_id"],))
        if replay:
            receipt = read(row[6]) if row[5] == "settled" and row[6] else None
            raw = self._raw(receipt["body"]) if receipt else None
            audit = self._audit(ticket,attempts=0,replayed=True)
            ok = audit["state"] == "settled" and audit["result_code"] == "ok" and not audit["integrity_stop"]
            return {"raw":raw,"audit":audit,"error_code":None if ok else "qa_saved_request_unresolved"}
        try:
            body = post_one(self.config["profile"]["deepseek"]["endpoint"],payload,headers={"Authorization":"Bearer fixture-key-not-real"},
                timeout=self.config["profile"]["timeout_seconds"],transport=self.transport)
        except Exception:
            self.unknown(ticket["attempt_id"],"qa_transport_unknown")
            return {"raw":None,"audit":self._audit(ticket,attempts=1),"error_code":"qa_transport_unknown"}
        raw = self._raw(body)
        receipt = {"version":VERSION,"attempt_id":ticket["attempt_id"],"payload_sha256":digest(payload),
            "profile_sha256":digest(self.config["profile"]),"body":body,"transport":"httpx.MockTransport"}
        directory = self.config_path.parent/"receipts"
        path = directory/(ticket["attempt_id"]+".json")
        try:
            directory.mkdir(parents=True,exist_ok=True)
            with path.open("x",encoding="utf-8",newline="\n") as handle:
                handle.write(canonical(receipt))
            self.attach_receipt(ticket["attempt_id"],file_hash(path),str(path))
        except Exception:
            self.unknown(ticket["attempt_id"],"qa_receipt_save_failed")
            return {"raw":raw,"audit":self._audit(ticket,attempts=1),"error_code":"qa_receipt_save_failed"}
        try:
            usage,cost = cloud_usage(body,self.config["profile"])
            details = body["usage"].get("completion_tokens_details",{})
            require(type(details) is dict,"qa_usage_unknown")
        except Exception:
            self.unknown(ticket["attempt_id"],"qa_usage_unknown")
            return {"raw":raw,"audit":self._audit(ticket,attempts=1),"error_code":"qa_usage_unknown"}
        identity = canonical([body.get("model"),body.get("system_fingerprint")])
        choices = body.get("choices")
        hard = self._hard(body,usage,cost,ticket["reserved_micro_cny"])
        valid = (type(choices) is list and len(choices) == 1 and type(choices[0]) is dict and choices[0].get("finish_reason") == "stop"
            and type(choices[0].get("message")) is dict and choices[0]["message"].get("role") == "assistant"
            and not choices[0]["message"].get("tool_calls") and type(raw) is str and len(raw.encode("utf-8")) <= LIMITS["max_output_bytes"])
        code = "qa_identity_or_context_violation" if hard else "ok" if valid else "qa_output_invalid"
        try:
            super().settle(ticket["attempt_id"],cost_micro=cost,receipt_sha=file_hash(path),receipt_path=str(path),
                identity=identity,usage=usage,result_code=code,integrity_stop=hard)
        except ExecutionStop:
            code = "budget_integrity_stop"
        return {"raw":raw,"audit":self._audit(ticket,attempts=1),"error_code":None if code == "ok" else code}

    def _hard(self, body, usage, cost, reservation):
        details = body["usage"].get("completion_tokens_details",{})
        require(type(details) is dict,"qa_usage_unknown")
        choices = body.get("choices")
        reasoning = (isinstance(choices,list) and any(isinstance(c,dict) and isinstance(c.get("message"),dict)
                     and bool(c["message"].get("reasoning_content")) for c in choices))
        return (canonical([body.get("model"),body.get("system_fingerprint")]) != self.config["expected_identity"]
                or cost > reservation or details.get("reasoning_tokens",0) != 0 or reasoning or bool(body.get("thinking"))
                or usage["total_tokens"] > self.config["profile"]["deepseek"]["context_limit"])

    @staticmethod
    def _raw(body):
        choices = body.get("choices")
        if type(choices) is list and len(choices) == 1 and type(choices[0]) is dict and type(choices[0].get("message")) is dict:
            return choices[0]["message"].get("content")
        return None
