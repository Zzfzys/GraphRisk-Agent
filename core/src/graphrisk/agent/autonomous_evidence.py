"""Metric/subject/unit/window/version facts, without raw identifiers or rows."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from graphrisk.agent.autonomous_tools import (
    DailyScoringContext, FrozenModel, InvestigationScope, SPEC_BY_NAME, ToolBoundaryError, ToolObservation, bind_call,
)
from graphrisk.agent.autonomous_policy import PolicyRegistry, mark_potential_conflicts
from graphrisk.agent.upgrade_contract import digest


class Fact(FrozenModel):
    fact_id: str = Field(pattern=r"^F-[a-f0-9]{24}$")
    metric: str
    subject: str
    unit: str
    window_seconds: int | None = Field(ge=1)
    temporal_scope: Literal["target_event", "strictly_before_target", "target_against_strict_history", "relative_day_end"]
    source_tool: str
    query_ref: str
    source_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_version: str | None
    availability: Literal["available", "unavailable", "not_measured"]
    value: int | float | bool | None
    missing_reason: Literal["not_requested", "tool_failed", "no_history_statistic", "not_provided_by_legacy_contract", "outside_independent_score_scope"] | None

    @model_validator(mode="after")
    def availability_matches_value(self):
        if self.availability == "available":
            if self.value is None or self.missing_reason is not None:
                raise ValueError("available fact requires a value and no missing reason")
        elif self.value is not None or self.missing_reason is None:
            raise ValueError("unavailable fact requires null and a missing reason")
        return self


class PolicyCondition(FrozenModel):
    metric: str
    subject: str
    unit: str
    window_seconds: int | None
    operator: Literal["gte", "eq"]
    expected: int | bool
    measurement: Literal["measured", "unmeasured"]
    fact_ref: str | None
    observed: int | bool | None
    satisfied: bool | None


class PolicyRecord(FrozenModel):
    policy_ref: str
    chunk_id: str
    policy_id: str
    doc_id: str
    version: int
    section: str
    line_start: int
    line_end: int
    text: str
    content_sha256: str
    source_type: str
    license: str
    effective_from: str
    effective_to: str | None
    purpose: Literal["trigger_condition", "exception_condition", "evidence_guidance", "general_guidance", "unmapped"]
    applicability: Literal["unmapped_requirements", "guidance_only", "unverified_conditions", "measured_conditions_only", "conditions_not_met"]
    conditions: tuple[PolicyCondition, ...]
    conflicts_with: tuple[str, ...]
    conflict_state: Literal["potential_conflict_requires_review", "not_observed"]
    content_trust: Literal["untrusted_policy_data"]


class ToolOutcome(FrozenModel):
    tool: str
    query_ref: str
    status: Literal["ok", "not_found", "invalid_input", "unavailable", "dependency_error", "no_match", "error", "not_requested"]
    error_code: str | None


class EvidenceBundle(FrozenModel):
    schema_version: Literal["investigation-facts-v1"] = "investigation-facts-v1"
    case_alias: str = Field(pattern=r"^[a-f0-9]{32}$")
    evidence_version: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_as_of: str
    facts: tuple[Fact, ...]
    policies: tuple[PolicyRecord, ...]
    tool_outcomes: tuple[ToolOutcome, ...]
    limitations: tuple[str, ...]
    # Historical v1 JSON and its digest remain byte-for-byte compatible.
    scoring_context: DailyScoringContext | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def score_time_binding(self):
        daily = [fact for fact in self.facts if fact.temporal_scope == 'relative_day_end']
        if self.scoring_context is None:
            if daily:
                raise ValueError('day-end facts require a sealed scoring context')
            return self
        context = self.scoring_context
        scores = [fact for fact in self.facts if fact.metric == 'primary_model_score']
        if (len(scores) != 1 or scores[0].temporal_scope != 'relative_day_end' or
                scores[0].subject != 'target_transaction' or scores[0].unit != 'uncalibrated_score' or
                scores[0].source_tool != 'get_risk_score' or scores[0].window_seconds is not None or
                scores[0].model_version != context.model_version or
                scores[0].source_version != context.model_sha256 or
                any(fact.metric != 'primary_model_score' for fact in daily)):
            raise ValueError('primary day-end score does not match its sealed scoring context')
        outcomes = [outcome for outcome in self.tool_outcomes if outcome.tool == 'get_risk_score']
        score = scores[0]
        if len(outcomes) != 1:
            raise ValueError('day-end score requires exactly one tool outcome')
        status = outcomes[0].status
        if status == 'ok':
            valid = (score.availability == 'available' and type(score.value) in (int, float)
                     and score.value == context.score)
        elif status == 'not_requested':
            valid = (score.availability == 'not_measured' and score.value is None
                     and score.missing_reason == 'not_requested')
        else:
            valid = (score.availability == 'unavailable' and score.value is None
                     and score.missing_reason == 'tool_failed')
        if not valid:
            raise ValueError('primary day-end score does not match its sealed scoring context or tool outcome')
        return self


# Metric, subject, unit, window, temporal scope. Names are semantic contracts,
# not labels supplied by the model or inferred from the numeric value.
LAYOUT = {
    "get_transaction": (("transaction_amount", "target_transaction", "dataset_amount_unit", None, "target_event"),),
    "get_transaction_features": (
        ("history_transaction_count", "heuristic_identity_history", "transactions", 3600, "strictly_before_target"),
        ("history_transaction_count", "heuristic_identity_history", "transactions", 86400, "strictly_before_target"),
        ("history_transaction_count", "heuristic_identity_history", "transactions", 604800, "strictly_before_target"),
        ("history_amount_mean", "heuristic_identity_history", "dataset_amount_unit", 86400, "strictly_before_target"),
        ("history_amount_population_std", "heuristic_identity_history", "dataset_amount_unit", 86400, "strictly_before_target"),
        ("amount_minus_history_mean", "target_against_heuristic_history", "dataset_amount_unit", 86400, "target_against_strict_history")),
    "get_risk_score": (("primary_model_score", "target_transaction", "uncalibrated_score", None, "target_event"),),
    "get_related_entities": (
        ("returned_related_count", "returned_shared_entity_history", "transactions", None, "strictly_before_target"),
        ("total_related_count", "all_shared_entity_history", "transactions", None, "strictly_before_target"),
        ("relations_truncated", "returned_shared_entity_history", "boolean", None, "strictly_before_target"),
        *(("returned_relation_type_count", f"returned_shared_{kind}_history", "transactions", None, "strictly_before_target")
          for kind in ("card", "device", "email_domain", "address"))),
    "find_risky_neighbors": (
        ("experimental_graph_score", "target_transaction", "uncalibrated_score", None, "target_event"),
        ("returned_graph_neighbor_count", "graph_history_sample", "transactions", None, "strictly_before_target"),
        ("scored_graph_neighbor_count", "graph_history_sample", "transactions", None, "strictly_before_target"),
        ("relation_only_graph_neighbor_count", "graph_history_sample", "transactions", None, "strictly_before_target"),
        ("graph_neighbors_truncated", "graph_history_sample", "boolean", None, "strictly_before_target")),
}


def fact_layout(scope, tool):
    """Only the primary signal changes time basis in a day-end investigation."""
    context = scope.get('scoring_context') if isinstance(scope, dict) else getattr(scope, 'scoring_context', None)
    rows = LAYOUT.get(tool, ())
    if context is not None and tool == 'get_risk_score':
        return tuple((*row[:4], 'relative_day_end') for row in rows)
    return rows

SAFE_RETRIEVAL_ERRORS = frozenset({
    "empty_query", "invalid_query", "invalid_top_k", "invalid_policy_date", "index_unavailable",
    "embedding_model_mismatch", "embedding_dimension_mismatch", "embedding_unavailable",
    "database_unavailable", "no_active_policy", "no_match",
})


def _provenance(scope, observation):
    call, result = observation.call, observation.result()
    try:
        rebound = bind_call(scope, call.name, json.loads(call.arguments_json))
    except (ValueError, TypeError):
        raise ToolBoundaryError("observation_request_invalid") from None
    if (call.binding_sha256 != scope.binding_sha256 or rebound.request.model_dump(mode="json") != call.request.model_dump(mode="json")):
        raise ToolBoundaryError("observation_context_mismatch")
    if call.name == "retrieve_policy":
        return result
    if result.transaction_id != scope.transaction_id or result.dataset_source != scope.dataset_source:
        raise ToolBoundaryError("tool_result_binding_mismatch")
    if result.status != "ok":
        return result
    data = result.data
    refs = {(e.source_kind, e.record_id) for e in result.evidence}
    if call.name == "get_transaction":
        expected = {("canonical_transaction", scope.transaction_id)}
        if data.source_transaction_dt_seconds != scope.target_event_second:
            raise ToolBoundaryError("tool_result_time_mismatch")
    elif call.name == "get_transaction_features":
        expected = {("historical_features", scope.transaction_id)}
    elif call.name == "get_risk_score":
        expected = {("model_artifact", scope.primary_model_version)}
        if data.model_version != scope.primary_model_version or data.model_name != "xgboost":
            raise ToolBoundaryError("model_version_binding_mismatch")
    else:
        rows = data.matches if call.name == "get_related_entities" else data.neighbors
        if len({row.transaction_id for row in rows}) != len(rows):
            raise ToolBoundaryError("duplicate_history_evidence")
        if any(row.transaction_id == scope.transaction_id or row.seconds_before_target <= 0 for row in rows):
            raise ToolBoundaryError("non_historical_evidence")
        if scope.target_event_second is not None and any(row.seconds_before_target > scope.target_event_second for row in rows):
            raise ToolBoundaryError("history_before_dataset_origin")
        if len(rows) > json.loads(call.arguments_json)["limit"]:
            raise ToolBoundaryError("tool_result_limit_exceeded")
        expected = {("entity_link", row.transaction_id) for row in rows}
        if call.name == "find_risky_neighbors":
            expected.add(("relation_snapshot" if data.score_role == 'relation_only' else
                          "model_artifact", scope.graph_model_version))
            if data.graph_model_version != scope.graph_model_version or data.score_role not in ("experimental", "relation_only"):
                raise ToolBoundaryError("graph_role_or_version_mismatch")
    if refs != expected or len(refs) != len(result.evidence):
        raise ToolBoundaryError("source_reference_binding_mismatch")
    return result


def _values(name, data):
    if name == "get_transaction":
        return [data.transaction_amount]
    if name == "get_transaction_features":
        return [data.tx_count_last_1h, data.tx_count_last_24h, data.tx_count_last_7d,
                data.amount_mean_last_24h, data.amount_std_last_24h, data.amount_deviation_last_24h]
    if name == "get_risk_score":
        return [data.risk_score]
    if name == "get_related_entities":
        return [len(data.matches), data.total_matches, data.truncated,
                *[sum(any(e.entity_type == kind for e in row.shared_entities) for row in data.matches)
                  for kind in ("card", "device", "email_domain", "address")]]
    return [data.target_graph_risk_score, len(data.neighbors),
            sum(n.evidence_role == "scored" for n in data.neighbors),
            sum(n.evidence_role == "relation_only" for n in data.neighbors), None]


def build_evidence(scope: InvestigationScope, observations: tuple[ToolObservation, ...],
                   registry: PolicyRegistry) -> EvidenceBundle:
    if scope.policy_index_version != registry.index_version:
        raise ToolBoundaryError("policy_index_binding_mismatch")
    results = [(o, _provenance(scope, o)) for o in observations]
    if len({o.call.request_sha256 for o, _ in results}) != len(results):
        raise ToolBoundaryError("duplicate_observation")
    facts, outcomes, policies = [], [], {}
    observed = {o.call.name for o, _ in results}
    rows = results + [(None, name) for name in SPEC_BY_NAME if name not in observed]
    for observation, result in rows:
        name = observation.call.name if observation else result
        status = result.status if observation else "not_requested"
        code = None
        if observation and status != "ok":
            # Legacy retrieval messages may contain paths/exception text: omit them.
            if name == "retrieve_policy":
                code = result.error_code if result.error_code in SAFE_RETRIEVAL_ERRORS else (
                    "no_match" if status == "no_match" else "policy_retrieval_failed")
            else:
                code = result.error.code
        outcomes.append(ToolOutcome(tool=name, query_ref=observation.call.request_sha256 if observation else digest([name, "not_requested"]), status=status, error_code=code))
        if name == "retrieve_policy":
            continue
        data = result.data if observation and status == "ok" else None
        values = _values(name, data) if data is not None else [None] * len(LAYOUT[name])
        revision = (scope.transaction_revision if name in ("get_transaction", "get_related_entities") else
                    scope.feature_revision if name == "get_transaction_features" else scope.model_revision)
        investigation_revision = getattr(scope, 'investigation_revision', None)
        if investigation_revision is not None and name in (
                'get_transaction_features', 'get_related_entities', 'find_risky_neighbors'):
            revision = investigation_revision
        model_version = (scope.primary_model_version if name == "get_risk_score" else
                         scope.graph_model_version if name == "find_risky_neighbors" else None)
        for layout, value in zip(fact_layout(scope, name), values):
            metric, subject, unit, window, temporal = layout
            reason = None
            if observation is None:
                availability, reason = "not_measured", "not_requested"
            elif data is None:
                availability, reason = "unavailable", "tool_failed"
            elif value is None:
                availability = "unavailable"
                reason = ("not_provided_by_legacy_contract" if metric == "graph_neighbors_truncated" else
                          "outside_independent_score_scope" if metric == "experimental_graph_score" and
                          getattr(data, 'score_role', None) == 'relation_only' else "no_history_statistic")
            else:
                availability = "available"
            payload = dict(metric=metric, subject=subject, unit=unit, window_seconds=window,
                           temporal_scope=temporal, source_tool=name, source_version=revision,
                           query_ref=observation.call.request_sha256 if observation else digest([name, "not_requested"]),
                           model_version=model_version, availability=availability,
                           value=value, missing_reason=reason)
            try:
                key = digest([scope.binding_sha256, observation.result_sha256 if observation else None, payload])[:24]
                facts.append(Fact(fact_id="F-" + key, **payload))
            except (ValidationError, ValueError):
                raise ToolBoundaryError("fact_value_contract_invalid") from None
    for observation, result in results:
        if observation.call.name != "retrieve_policy":
            continue
        if len(result.hits) > json.loads(observation.call.arguments_json)["top_k"]:
            raise ToolBoundaryError("policy_result_limit_exceeded")
        for record in registry.evaluate(result, policy_as_of=scope.policy_as_of, facts=tuple(facts)):
            policies[record["policy_ref"]] = record
    payload = {"schema_version": "investigation-facts-v1", "case_alias": scope.case_alias,
               "policy_as_of": scope.policy_as_of.isoformat(), "facts": [f.model_dump(mode="json") for f in facts],
               "policies": list(mark_potential_conflicts(tuple(policies.values()))),
               "tool_outcomes": [o.model_dump(mode="json") for o in outcomes],
               "limitations": ["identity_is_heuristic_not_verified_person", "scores_uncalibrated_not_fraud_proof",
                               "graph_score_experimental_not_fused", "policy_hits_are_not_satisfied_conditions",
                               "graph_sample_completeness_unknown", "currency_not_verified"]}
    if getattr(scope, 'scoring_context', None) is not None:
        payload['scoring_context'] = scope.scoring_context.model_dump(mode='json')
        payload['limitations'].append('day_end_score_unavailable_at_target_event')
        if getattr(scope, 'investigation_revision', None) is not None:
            payload['limitations'].extend([
                'strict_event_history_within_sealed_arrivals_not_event_time_arrival_history',
                'policy_as_of_server_selected_not_transaction_calendar',
                'graph_relation_only_no_graphsage_prediction'])
    payload["evidence_version"] = digest([scope.binding_sha256, registry.annotation_version, payload])
    return EvidenceBundle.model_validate_json(json.dumps(payload, ensure_ascii=False, allow_nan=False))


class FactClaim(FrozenModel):
    evidence_version: str
    fact_id: str
    metric: str
    subject: str
    unit: str
    window_seconds: int | None
    temporal_scope: str
    source_version: str
    query_ref: str
    model_version: str | None
    value: int | float | bool | None
    availability: Literal["available", "unavailable", "not_measured"]


def validate_fact_claim(claim: FactClaim, bundle: EvidenceBundle) -> Fact:
    """Typed slot validation. Does NOT claim to validate arbitrary Chinese prose."""
    if claim.evidence_version != bundle.evidence_version:
        raise ToolBoundaryError("stale_evidence_version")
    fact = next((f for f in bundle.facts if f.fact_id == claim.fact_id), None)
    if fact is None:
        raise ToolBoundaryError("unknown_fact_reference")
    fields = ("metric", "subject", "unit", "window_seconds", "temporal_scope", "source_version", "query_ref", "model_version", "value", "availability")
    if any(getattr(claim, k) != getattr(fact, k) for k in fields) or type(claim.value) is not type(fact.value):
        raise ToolBoundaryError("fact_reference_semantics_mismatch")
    return fact
