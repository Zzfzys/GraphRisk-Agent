"""New sealed-day cases and free program evidence reports, separate from x3."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

from graphrisk.agent.autonomous_evidence import EvidenceBundle, Fact, ToolOutcome, fact_layout
from graphrisk.agent.autonomous_tools import DailyInvestigationScope, DailyScoringContext, SPEC_BY_NAME
from graphrisk.agent.fact_presentation import fact_text
from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.serving.conversations import BoundCase, ConversationError


DAILY_LIMITATIONS = (
    '日终关联特征仅使用相对日结束前发生、截至封批水位已接收的数据；不能称为交易发生时已可用的即时评分。',
    '高、中、低是封批内模型调查优先级；分数未校准，不是已确认欺诈或欺诈概率。',
    '相对日与相对秒尚无真实日历日期及业务时区映射。',
    '匿名源字段的实际产生时间未经核实；UID和设备字段均不是已核验客户或物理设备身份。',
    '当前会话没有测量历史反证、图信号或政策前提，不能将缺口当作安全或规则已触发。',
    '封批后迟到交易不回写本修订的评分；应查看独立迟到记录和后续处理。',
)


def daily_report(bundle: EvidenceBundle) -> dict:
    """Deterministic report from verified facts; never represents an LLM verdict."""
    if bundle.scoring_context is None:
        raise ConversationError('daily_batch_context_required')
    # Revalidate even a caller's model_copy(update=...) before rendering facts.
    bundle = EvidenceBundle.model_validate_json(bundle.model_dump_json(), strict=True)
    limitations = list(DAILY_LIMITATIONS)
    measured = {outcome.tool for outcome in bundle.tool_outcomes if outcome.status == 'ok'}
    if {'get_transaction_features', 'get_related_entities', 'retrieve_policy'} <= measured:
        limitations[4] = '历史行为与关系只使用封批快照中严格早于目标交易的记录；共享字段不证明邻居高风险，政策匹配也不等于条件成立。'
    elif {'get_transaction_features', 'get_related_entities', 'retrieve_policy'} & measured:
        limitations[4] = '仅依据已标记可用的事实判断；未测量或不可用的历史、图及政策前提，不能当作安全或规则已触发。'
    if any(fact.metric == 'experimental_graph_score' and
           fact.missing_reason == 'outside_independent_score_scope' for fact in bundle.facts):
        limitations.append('当前交易不在独立图评分覆盖范围；本轮只取得真实历史关系线索，没有GraphSAGE风险分数。')
    return {
        'version': 'daily-batch-evidence-report-v1', 'source': 'program_from_sealed_batch',
        'scope': 'sealed_daily_batch_evidence_only', 'llm_generated': False,
        'approval_eligible': False, 'evidence_version': bundle.evidence_version,
        'evidence_sha256': digest(bundle.model_dump(mode='json')),
        'scoring_context': bundle.scoring_context.model_dump(mode='json'),
        'model_risk_level': bundle.scoring_context.risk_level,
        'facts': [{'ref': 'F' + str(index), 'fact_id': fact.fact_id,
                   'text': fact_text(fact.model_dump(mode='json'))}
                  for index, fact in enumerate(bundle.facts, 1)],
        'limitations': limitations,
    }


def _case_alias(identifier, content_sha):
    return digest(['daily-batch-case-v1', identifier, content_sha])[:32]


def _daily_evidence(scope, row):
    context = scope.scoring_context
    facts, outcomes = [], []
    amount = row.get('transaction_amount')
    for tool in SPEC_BY_NAME:
        available = tool == 'get_risk_score' or tool == 'get_transaction' and amount is not None
        query = digest([scope.binding_sha256, tool, context.sealed_content_sha256])
        outcomes.append(ToolOutcome(tool=tool, query_ref=query,
            status='ok' if available else 'not_requested', error_code=None))
        for metric, subject, unit, window, temporal in fact_layout(scope, tool):
            value = context.score if tool == 'get_risk_score' else amount if tool == 'get_transaction' else None
            revision = (scope.transaction_revision if tool in ('get_transaction', 'get_related_entities') else
                        scope.feature_revision if tool == 'get_transaction_features' else scope.model_revision)
            model = (scope.primary_model_version if tool == 'get_risk_score' else
                     scope.graph_model_version if tool == 'find_risky_neighbors' else None)
            payload = dict(metric=metric, subject=subject, unit=unit, window_seconds=window,
                temporal_scope=temporal, source_tool=tool, query_ref=query, source_version=revision,
                model_version=model, availability='available' if available else 'not_measured',
                value=value, missing_reason=None if available else 'not_requested')
            facts.append(Fact(fact_id='F-' + digest([scope.binding_sha256, payload])[:24], **payload))
    payload = dict(schema_version='investigation-facts-v1', case_alias=scope.case_alias,
        policy_as_of=scope.policy_as_of.isoformat(), facts=[fact.model_dump(mode='json') for fact in facts],
        policies=[], tool_outcomes=[outcome.model_dump(mode='json') for outcome in outcomes],
        limitations=['day_end_score_unavailable_at_target_event', 'scores_uncalibrated_not_fraud_proof',
            'source_field_arrival_times_unverified', 'identity_is_heuristic_not_verified_person',
            'unmeasured_daily_investigation_tools', 'late_events_do_not_mutate_sealed_revision'],
        scoring_context=context.model_dump(mode='json'))
    payload['evidence_version'] = digest([scope.binding_sha256, payload])
    return EvidenceBundle.model_validate_json(canonical(payload), strict=True)


class DailyConversationBackend:
    """Only a trusted server store can register new day-end evidence.

    resolve_transaction/get_transaction must verify immutable sealed batch
    contents and model bindings. No old x3 observation or checkpoint is changed.
    """
    local_evidence_only = True
    risk_profile = 'daily_batch'

    def __init__(self, store, *, policy_as_of: date, source_guard=lambda: None,
                 evidence_tools=None, narrative_backend=None,
                 reference_answer_service=None, reference_query=None, reference_cache=None,
                 case_reference_service=None):
        if case_reference_service is not None:
            from graphrisk.agent.reference_answers import ReferenceAnswerService
            if (not isinstance(case_reference_service, ReferenceAnswerService) or
                    any(item is not None for item in (reference_answer_service, reference_query, reference_cache))):
                raise ValueError('case reference service and fixed public reference modes are mutually exclusive')
        if reference_cache is not None and (not isinstance(reference_cache, (str, Path)) or
                reference_answer_service is not None or reference_query is not None):
            raise ValueError('reviewed reference cache and a reference service/question are mutually exclusive')
        if ((reference_answer_service is None) != (reference_query is None) or
                reference_query is not None and
                (not isinstance(reference_query, str) or not reference_query.strip() or len(reference_query)>1000)):
            raise ValueError('public reference service and a fixed public question must be configured together')
        self.store, self.policy_as_of, self.source_guard = store, policy_as_of, source_guard
        self.evidence_tools, self.narrative_backend = evidence_tools, narrative_backend
        self.reference_answer_service, self.reference_query = reference_answer_service, reference_query
        self.reference_cache = Path(reference_cache).resolve() if reference_cache is not None else None
        self.case_reference_service = case_reference_service
        self.entries = {}

    @property
    def formal_report_enabled(self):
        return self.narrative_backend is not None and self.narrative_backend.allow_investigation

    narrative_call_allowance = 1

    def authorize_report(self):
        if not self.formal_report_enabled:
            raise ConversationError('investigation_generation_disabled')
        from graphrisk.agent.judgment_v3 import CONTRACT as LEGACY_CONTRACT
        from graphrisk.serving.daily_narrative import CONTRACT
        if (getattr(self.narrative_backend, 'report_contract', None) not in (CONTRACT, LEGACY_CONTRACT) or
                type(getattr(self.narrative_backend, 'max_narrative_calls', None)) is not int or
                self.narrative_backend.max_narrative_calls != 1):
            raise ConversationError('daily_narrative_configuration_invalid')
        self.narrative_backend.authorize()

    def run_report(self, alias, binding, turn_id, evidence_version):
        from graphrisk.agent.narrative import run_narrative
        self.authorize_report()
        self.check(alias, binding)
        entry = self.entries[alias]
        if entry['evidence'].evidence_version != evidence_version:
            raise ConversationError('evidence_version_conflict')
        self.narrative_backend.entries[alias] = entry
        return run_narrative(self.narrative_backend, alias, binding, turn_id, evidence_version)

    def authorize(self):
        raise ConversationError('investigation_generation_disabled')

    def resolve(self, identifier, *, batch_id=None):
        try:
            self.source_guard()
            row = self.store.resolve_transaction(identifier)
            if batch_id is not None and row['batch_id'] != batch_id:
                raise ConversationError('batch_selection_mismatch')
            if row['temporal_scope'] != 'relative_day_end' or row['calibrated_probability'] is not False:
                raise ValueError('only an explicitly marked day-end signal may be registered')
            context = DailyScoringContext.model_validate({key: row[key] for key in
                DailyScoringContext.model_fields if key in row})
            if str(row['transaction_id']) != identifier:
                raise ValueError('source row transaction does not match')
            alias = _case_alias(identifier, context.sealed_content_sha256)
            scope = DailyInvestigationScope(case_alias=alias, transaction_id=identifier,
                dataset_source='ieee_cis', target_event_second=row['event_second'],
                policy_as_of=self.policy_as_of, transaction_revision=context.sealed_content_sha256,
                feature_revision=context.feature_protocol_sha256, model_revision=context.model_sha256,
                primary_model_version=context.model_version,
                graph_model_version='relation-only-daily-v1' if self.evidence_tools else 'unmeasured-day-end-graph',
                policy_index_version=self.evidence_tools.registry.index_version if self.evidence_tools else
                    digest('no-policy-index-for-daily-batch'), scoring_context=context,
                investigation_revision=self.evidence_tools.revision(row) if self.evidence_tools else None)
            if self.evidence_tools:
                executor, observations, bundle = self.evidence_tools.register(scope, row)
            else:
                bundle = _daily_evidence(scope, row)
        except ConversationError:
            raise
        except (KeyError, ValueError, TypeError, OSError):
            raise ConversationError('transaction_outside_registered_cases') from None
        self.entries[alias] = {'scope': scope, 'evidence': bundle, 'source_sha256': digest(row)}
        if self.evidence_tools:
            self.entries[alias].update(registry=self.evidence_tools.registry,
                observations=observations, executor=executor,
                guard=lambda: self.check(alias, scope.binding_sha256))
        return BoundCase(alias, scope.binding_sha256, bundle)

    def check(self, alias, binding):
        if alias not in self.entries:
            # ponytail: scan the small sealed-day list on restart; index aliases
            # in the store when retention grows beyond this local demo workload.
            try:
                for batch in self.store.list_batches():
                    for row in self.store.get_day(batch['relative_day'])['results']:
                        identifier = str(row['transaction_id'])
                        if _case_alias(identifier, batch['sealed_content_sha256']) == alias:
                            self.resolve(identifier, batch_id=batch['batch_id'])
                            break
                    if alias in self.entries:
                        break
            except Exception:
                raise ConversationError('source_version_changed') from None
        entry = self.entries.get(alias)
        if entry is None or entry['scope'].binding_sha256 != binding:
            raise ConversationError('evidence_binding_changed')
        try:
            self.source_guard()
            scope = entry['scope']
            row = self.store.get_transaction(scope.scoring_context.relative_day, scope.transaction_id)
            if digest(row) != entry['source_sha256']:
                raise ValueError('sealed scoring record changed')
            if self.evidence_tools and self.evidence_tools.revision(row) != scope.investigation_revision:
                raise ValueError('daily investigation implementation or policy changed')
        except Exception:
            raise ConversationError('source_version_changed') from None

    def model_band(self, alias, binding, evidence):
        self.check(alias, binding)
        if evidence != self.entries[alias]['evidence']:
            raise ConversationError('evidence_binding_changed')
        context = evidence.scoring_context
        return {'status': 'available', 'risk_level': context.risk_level,
                'version': 'sealed-day-model-investigation-priority-v1',
                'temporal_scope': 'relative_day_end', 'batch_id': context.batch_id,
                'revision': context.revision, 'policy_sha256': context.policy_sha256}

    def program_report(self, alias, binding, evidence):
        self.model_band(alias, binding, evidence)
        return daily_report(evidence)

    def check_report(self, report, evidence):
        """Revalidate formal quotes on API reads as well as workbench exports."""
        if report and report.get('version') == 'separated-risk-report-v12-daily-quotes-v2':
            from graphrisk.serving.daily_narrative import verify_daily_report
            try:
                verify_daily_report(report, evidence)
            except (ValueError, KeyError, TypeError):
                raise ConversationError('daily_report_binding_changed') from None

    def case_report_package(self, alias, binding, evidence, *, task, turn_id, report, saved=None):
        """Keep a task-bound local appendix outside report/F/P evidence."""
        if self.case_reference_service is None:
            if saved is not None:
                raise ConversationError('case_reference_unconfigured')
            return None
        try:
            self.check(alias, binding)
            entry = self.entries[alias]
            if (evidence != entry['evidence'] or entry['scope'].policy_as_of != self.policy_as_of
                    or evidence.policy_as_of != entry['scope'].policy_as_of.isoformat()):
                raise ValueError('case reference evidence changed')
            from graphrisk.serving.daily_reference import build_case_report_package
            expected = build_case_report_package(self.case_reference_service, task=task, turn_id=turn_id,
                case_alias=alias, case_binding_sha256=binding, evidence=evidence, report=report)
            if saved is not None and saved != expected:
                raise ValueError('case reference package changed')
            return deepcopy(expected)
        except ConversationError:
            raise
        except (KeyError, TypeError, ValueError, OSError, AttributeError):
            raise ConversationError('case_reference_binding_changed') from None

    def _reference_request(self, alias, binding, evidence, *, cache=None):
        """Bind a public question separately; never substitute its context for case facts."""
        from graphrisk.agent.public_references import ReferenceContext
        self.check(alias, binding)
        entry = self.entries[alias]
        if evidence != entry['evidence']:
            raise ConversationError('evidence_binding_changed')
        if self.reference_cache is not None:
            if cache is None:
                from graphrisk.agent.reference_answer_cache import load_reviewed_reference_cache
                cache = load_reviewed_reference_cache(self.reference_cache)
            packet = cache['packet']
            context = ReferenceContext.model_validate_json(canonical(packet['request']['context']), strict=True)
            if context.as_of != entry['scope'].policy_as_of:
                raise ValueError('reviewed reference date does not match the server reference date')
        else:
            context = ReferenceContext(as_of=entry['scope'].policy_as_of)
            packet = self.reference_answer_service.prepare(self.reference_query, context,
                purpose='reference_explanation', top_k=3, planner='rules')
        metadata = {'version': 'daily-public-reference-sidecar-v1', 'case_alias': alias,
            'case_binding_sha256': binding, 'evidence_version': evidence.evidence_version,
            'query_sha256': digest(packet['request']['query']),
            'context_sha256': digest(context.model_dump(mode='json')),
            'request_sha256': digest(packet['request']), 'packet_sha256': digest(packet)}
        if cache is not None:
            metadata.update(context_role='public_question_scope_not_case_facts',
                            reference_provenance=deepcopy(cache['provenance']))
        return packet, metadata

    def public_reference(self, alias, binding, evidence, *, saved=None):
        """Rebuild an independent appendix; never changes F/P evidence or report bytes."""
        if self.reference_answer_service is None and self.reference_cache is None:
            if saved is not None:
                raise ConversationError('public_reference_unconfigured')
            return None
        try:
            cache = None
            if self.reference_cache is not None:
                from graphrisk.agent.reference_answer_cache import load_reviewed_reference_cache
                cache = load_reviewed_reference_cache(self.reference_cache)
            packet, metadata = self._reference_request(alias, binding, evidence, cache=cache)
            service = cache['service'] if cache is not None else self.reference_answer_service
            if saved is None:
                answer = deepcopy(cache['answer']) if cache is not None else service.finish(packet, None)
                value = {**metadata, 'answer': answer}
                value['sidecar_sha256'] = digest(value)
            else:
                value = deepcopy(saved)
                if (set(value) != set(metadata) | {'answer', 'sidecar_sha256'} or
                        any(value.get(key) != expected for key, expected in metadata.items()) or
                        value['sidecar_sha256'] != digest({k:v for k,v in value.items() if k!='sidecar_sha256'})):
                    raise ValueError('reference sidecar binding changed')
            if (not isinstance(value['answer'], dict) or
                    value['answer'].get('packet_sha256') != metadata['packet_sha256'] or
                    cache is not None and value['answer'] != cache['answer'] or
                    service.verify_saved(value['answer']) != value['answer']):
                raise ValueError('reference answer changed')
            return deepcopy(value)
        except ConversationError:
            raise
        except (KeyError, TypeError, ValueError, OSError):
            raise ConversationError('public_reference_binding_changed') from None

    def finish_reference_answer(self, alias, binding, evidence, draft, *, saved=None):
        """Server selector guard only; receipt provenance and semantic acceptance remain separate."""
        if self.reference_cache is not None:
            raise ConversationError('public_reference_cache_read_only')
        if self.reference_answer_service is None:
            raise ConversationError('public_reference_unconfigured')
        if not isinstance(draft, dict):
            raise ValueError('reference answer draft must be an object')
        self.public_reference(alias, binding, evidence, saved=saved)
        packet, metadata = self._reference_request(alias, binding, evidence)
        answer = self.reference_answer_service.finish(packet, draft)
        value = {**metadata, 'answer': answer}
        value['sidecar_sha256'] = digest(value)
        return self.public_reference(alias, binding, evidence, saved=value)
