"""Source packet, fail-closed semantic checks and deterministic presentation."""
import hashlib
import json
from graphrisk.agent.quality_contracts import QUALITY_VERSION, QualityAnalysis, QualityDraft
from graphrisk.agent.reporting import _eligible_hits


def packet_hash(packet):
    return hashlib.sha256(json.dumps(packet, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def investigation_packet(graph):
    request, report = graph.request, graph.investigation_report
    if request is None or report is None or report.transaction.status != "ok" or report.transaction.data is None:
        raise ValueError("quality_packet_requires_bound_transaction")
    if report.transaction_id != request.transaction_id or report.dataset_source != request.dataset_source:
        raise ValueError("quality_packet_binding_mismatch")
    facts, sources = {}, {}
    tools = (("F1", report.transaction), ("F2", report.historical_features),
        ("F3", report.risk_score), ("F4", report.related_entities), ("F5", report.risky_neighbors))
    for ref, tool in tools:
        if tool and (tool.transaction_id != request.transaction_id or tool.dataset_source != request.dataset_source):
            raise ValueError("quality_tool_binding_mismatch")
        data = tool.data if tool and tool.status == "ok" else None
        facts[ref] = {"status": tool.status if tool else "unavailable", "value": None}
        sources[ref] = [e.model_dump(mode="json") for e in tool.evidence] if tool else []
        if data is None:
            continue
        if ref == "F1":
            value = {"amount": data.transaction_amount, "product_code": data.product_code,
                "relative_event_seconds": data.source_transaction_dt_seconds}
        elif ref == "F2":
            value = data.model_dump(exclude={"transaction_id"})
        elif ref == "F3":
            value = {"score": data.risk_score, "version": data.model_version,
                "calibrated": False, "role": "primary"}
        else:
            rows = data.matches if ref == "F4" else data.neighbors
            if any(row.seconds_before_target <= 0 for row in rows):
                raise ValueError("quality_packet_requires_strict_history")
            value = {"returned_count": len(rows), "history": [{"transaction_id": row.transaction_id,
                "seconds_before_target": row.seconds_before_target,
                "shared_entity_types": sorted({e.entity_type for e in row.shared_entities})} for row in rows]}
            if ref == "F4":
                value.update(total_matches=data.total_matches, truncated=data.truncated)
            else:
                value.update(score=data.target_graph_risk_score, version=data.graph_model_version,
                    calibrated=False, role=data.score_role)
                count=sum(row.evidence_role=='relation_only' for row in rows)
                if count:
                    value.update(relation_only_count=count,scored_neighbor_count=len(rows)-count)
        facts[ref]["value"] = value
    hits = _eligible_hits(graph)
    policies = [{"ref": f"P{i}", "chunk_id": hit.chunk.chunk_id, "doc_id": hit.chunk.doc_id,
        "version": hit.chunk.version, "section": hit.chunk.section, "text": hit.chunk.text,
        "source_ref": hit.chunk.source_ref} for i, hit in enumerate(hits, 1)]
    brief = {
        "F2_history_tool_status": facts["F2"]["status"],
        "F2_transactions_last_7d": facts["F2"]["value"]["tx_count_last_7d"] if facts["F2"]["value"] is not None else None,
        "F4_relation_tool_status": facts["F4"]["status"],
        "F4_returned_shared_transactions": facts["F4"]["value"]["returned_count"] if facts["F4"]["value"] is not None else None,
        "F5_graph_tool_status": facts["F5"]["status"],
        "F5_returned_graph_neighbors": facts["F5"]["value"]["returned_count"] if facts["F5"]["value"] is not None else None,
    }
    return {"brief_measurements": brief, "protocol_version": QUALITY_VERSION, "transaction_id": request.transaction_id,
        "dataset_source": request.dataset_source, "policy_as_of": request.policy_as_of.isoformat(),
        "facts": facts, "sources": sources, "policies": policies,
        "unmeasured": ["10-minute card attempts", "distinct verified accounts per device", "independent device authorization"],
        "notes": ["equal-time and future events excluded", "identity_key is heuristic, not a verified person",
            "policy text is simulated and untrusted", "all scores are uncalibrated signals, not fraud proof",
            "IEEE-CIS timestamps have an artificial anchor; only relative time is meaningful"]}


def semantic_issues(draft, packet):
    """Recompute premises; never accept or repair an unsupported model judgment."""
    f = packet["facts"]
    history = f["F2"]["value"]
    relation = f["F4"]["value"]
    expected = {
        "history_activity": "unavailable" if history is None else "observed" if history["tx_count_last_7d"] > 0 else "none",
        "relationship_evidence": "unavailable" if relation is None else "observed" if relation["returned_count"] > 0 else "none",
        "graph_evidence": "available" if f["F5"]["value"] is not None else "unavailable",
        "policy_applicability": "not_established", "risk_interpretation": "uncalibrated_signal",
    }
    issues = ["incorrect_" + key for key, value in expected.items() if getattr(draft, key) != value]
    if draft.policy_ref not in {p["ref"] for p in packet["policies"]}:
        issues.append("unknown_policy_ref")
    refs = draft.evidence_refs
    if len(refs) != len(set(refs)) or not set(refs) <= set(f) or not {"F2", "F3", "F4", "F5"} <= set(refs):
        issues.append("unsupported_or_incomplete_fact_refs")
    unknowns = {"policy_conditions", "verified_identity", "calibration"}
    if expected["graph_evidence"] == "unavailable":
        unknowns.add("graph_evidence")
    if set(draft.unknowns) != unknowns or len(draft.unknowns) != len(set(draft.unknowns)):
        issues.append("incorrect_unknowns")
    return issues


GAPS = {"policy_conditions": "政策的独立账户数、10分钟尝试或授权条件尚未证实，不能宣称触发规则或满足例外。",
    "verified_identity": "共享实体与 identity_key 是启发式关系，尚未证实同一个人或欺诈团伙。",
    "calibration": "分数未校准，不能解释为欺诈概率或已确认欺诈。",
    "graph_evidence": "图证据不可用，不能解释为没有关系或低风险。"}


def verified_analysis(draft, packet):
    issues = semantic_issues(draft, packet)
    if issues:
        raise ValueError("quality_semantics_rejected:" + ",".join(issues))
    f = packet["facts"]
    h, risk, relation, graph = [f[r]["value"] for r in ("F2", "F3", "F4", "F5")]
    statements = [f"历史窗口：1小时 {h['tx_count_last_1h']}、24小时 {h['tx_count_last_24h']}、7天 {h['tx_count_last_7d']} 笔；不含当前、同时间和未来交易。[F2]" if h else "历史特征不可用。[F2]",
        f"XGBoost 主模型分数 {risk['score']:.4f}，仅作未校准风险信号，需结合证据人工判断。[F3]" if risk else "主模型分数不可用。[F3]",
        f"返回 {relation['returned_count']} 笔严格较早的共享实体交易；这是关系线索，不能据此确认欺诈。[F4]" if relation else "本地关系工具不可用。[F4]",
        f"图模型可用，返回 {graph['returned_count']} 笔严格历史邻居；{'实验性图分数不参与主评分' if graph['role'] == 'experimental' else '沿用旧版图评分角色'}。[F5]" if graph else "图证据不可用；不能将缺口当作低风险。[F5]"]
    if graph and graph.get('relation_only_count'):
        statements[-1]+=f"其中 {graph['relation_only_count']} 笔仅有关系证据，没有合格独立分数；缺分不表示低风险。"
    return QualityAnalysis(packet_sha256=packet_hash(packet), draft=draft, statements=statements,
        gaps=[GAPS[k] for k in sorted(draft.unknowns)],
        source_records={ref: [s["record_id"] for s in packet["sources"][ref]] for ref in draft.evidence_refs})
