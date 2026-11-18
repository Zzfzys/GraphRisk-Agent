"""Bounded Chinese tasks and versioned, evidence-only conversation state.

Raw messages are transient. This module never grants business actions or turns
an earlier generated answer into a fact. All model work is delegated to the
durable route3 executor, not to a separate unmetered chat completion.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
import time
from typing import Literal
from uuid import uuid4

from pydantic import Field

from graphrisk.agent.autonomous_tools import FrozenModel
from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.upgrade_contract import canonical, digest

Task = Literal['investigate', 'explain_evidence', 'policy_conditions', 'continue_checks', 'risk_assessment']


class ConversationError(ValueError):
    """Only fixed, public error codes may cross the API boundary."""


class CreateConversation(FrozenModel):
    transaction_id: str | None = Field(default=None, pattern=r'^[0-9]{1,18}$')
    message: str = Field(default='调查风险', min_length=1, max_length=500)
    risk_profile: Literal['x3', 'daily_batch'] = 'x3'
    batch_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$')


class FollowUp(FrozenModel):
    parent_turn_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    evidence_version: str = Field(pattern=r'^[a-f0-9]{64}$')
    message: str = Field(min_length=1, max_length=500)


def normalize_task(message: str, transaction_id: str | None = None):
    """Finite supported language, never a heuristic redactor for arbitrary text.

    Unrecognized or mixed commands require clarification before any dispatch.
    Only a fully matched task and optional numeric ID leave this parser.
    """
    text = message.strip().rstrip('。？！?!').strip()
    match = re.fullmatch(r'(?:请)?调查(?:交易)?\s*([0-9]{1,18})(?:的)?(?:风险)?', text)
    if match:
        supplied = match[1]
        if transaction_id is not None and supplied != transaction_id:
            raise ConversationError('case_switch_requires_new_conversation')
        return 'investigate', supplied
    patterns = {
        'risk_assessment': r'(?:请)?(?:生成|给出|撰写)(?:中文)?(?:研判|风险研判|风险报告)',
        'investigate': r'(?:请)?(?:调查|分析|核查)(?:这笔交易|该交易|本案)?(?:的)?风险|调查这笔交易',
        'explain_evidence': r'(?:请)?(?:解释|说明|查看)(?:风险)?(?:依据|证据|反证|依据与反证|支持和反向证据)|为什么(?:这笔交易)?有风险|有什么反证|先问反证|风险依据是什么',
        'policy_conditions': r'(?:请)?(?:解释|说明|查看|核对)?(?:政策|规则)(?:的)?(?:前提|适用条件|未测量项|依据)|政策前提是什么',
        'continue_checks': r'(?:请)?继续(?:允许的)?(?:核查|调查|补查)|补查缺失证据',
    }
    for task, pattern in patterns.items():
        if re.fullmatch(pattern, text):
            return task, transaction_id
    raise ConversationError('clarification_required')


@dataclass(frozen=True)
class BoundCase:
    alias: str
    binding: str
    evidence: EvidenceBundle


def evidence_answer(bundle: EvidenceBundle, task: str) -> dict:
    """A transparent program projection, not an LLM risk verdict."""
    return {
        'kind': 'evidence_projection', 'task': task,
        'facts': [f.model_dump(mode='json') for f in bundle.facts],
        'policies': [p.model_dump(mode='json') for p in bundle.policies],
        'limitations': list(bundle.limitations),
        'missing_tools': [o.tool for o in bundle.tool_outcomes if o.status != 'ok'],
        'message': ('这些是政策要求与已测／未测前提；命中条款不代表本案满足条件。'
                    if task == 'policy_conditions' else
                    '复用已封批的日终评分及本地调查证据；该分数在交易发生时未必可用。高、中、低为模型调查优先级；各项是否取得以工具状态和事实卡为准。'
                    if bundle.scoring_context is not None else
                    '复用本案证据。分数是未校准信号；共享关系不证明同一人或欺诈。反证未测量不能当成没有反证。'),
        'risk_rating': None, 'approval_eligible': False,
        'llm_generated': False,
    }


class ConversationService:
    """Store provides atomic document mutation; backend owns private scope.

    resolve(ID) binds server artifacts; check(alias,binding) verifies them on
    every read. investigate(alias,binding,turn,task,parent_evidence) must use
    the metered bounded executor and return only revalidated evidence/results.
    A running turn is not retried on HTTP replay, including after restart.
    """
    def __init__(self, store, backend, *, clock=time.time):
        self.store, self.backend, self.clock = store, backend, clock

    def _current(self, value):
        if self.clock() >= value['expires_at']:
            raise ConversationError('conversation_expired')
        self.backend.check(value['case_alias'], value['binding'])

    def create(self, request: CreateConversation, key: str):
        task, transaction = normalize_task(request.message, request.transaction_id)
        if transaction is None:
            raise ConversationError('transaction_required')
        if request.risk_profile != getattr(self.backend, 'risk_profile', 'x3'):
            raise ConversationError('conversation_service_unconfigured')
        # The fingerprint retains no raw text or unhashed transaction identifier.
        if request.risk_profile == 'x3' and request.batch_id is not None:
            raise ConversationError('batch_selection_mismatch')
        fingerprint = digest([task, transaction] if request.risk_profile == 'x3' else
                             [task, transaction, request.risk_profile, request.batch_id])
        case = (self.backend.resolve(transaction, batch_id=request.batch_id)
                if request.risk_profile == 'daily_batch' else self.backend.resolve(transaction))
        if case.alias != case.evidence.case_alias:
            raise ConversationError('evidence_binding_changed')
        value = {'version': 'conversation-v1', 'conversation_id': uuid4().hex,
                 'case_alias': case.alias, 'binding': case.binding,
                 'expires_at': self.clock()+86400, 'turns': [],
                 'reserved_model_calls': 0, 'reserved_tool_calls': 0,
                 'evidence': case.evidence.model_dump(mode='json')}
        if hasattr(self.backend, 'public_reference'):
            reference = self.backend.public_reference(case.alias, case.binding, case.evidence)
            if reference is not None:
                value['public_reference'] = reference
        value = self.store.create(key, fingerprint, value)
        self._current(value)
        return self._turn(value['conversation_id'], task, key, fingerprint, None, None, initial=True)

    def follow(self, identifier: str, request: FollowUp, key: str):
        task, transaction = normalize_task(request.message)
        if transaction is not None:
            raise ConversationError('case_switch_requires_new_conversation')
        fingerprint = digest([task, request.parent_turn_id, request.evidence_version])
        return self._turn(identifier, task, key, fingerprint, request.parent_turn_id, request.evidence_version)

    def get(self, identifier: str):
        value = self.store.get(identifier)
        self._current(value)
        result = deepcopy(value)
        result.pop('binding')
        # Pure presentation from trusted scope and current evidence; never
        # mutate stored evidence, old reports, LLM output, or approval state.
        if hasattr(self.backend, 'model_band'):
            evidence = EvidenceBundle.model_validate_json(canonical(result['evidence']), strict=True)
            result['model_band'] = self.backend.model_band(value['case_alias'], value['binding'], evidence)
        if hasattr(self.backend, 'public_reference'):
            evidence = EvidenceBundle.model_validate_json(canonical(result['evidence']), strict=True)
            reference = self.backend.public_reference(value['case_alias'], value['binding'], evidence,
                                                     saved=value.get('public_reference'))
            if reference is not None:
                result['public_reference'] = reference
                # Project the server's binding independently of appendix data.
                result['reference_case_binding_sha256'] = value['binding']
        if result['evidence'].get('scoring_context') is not None:
            result['scoring_context'] = deepcopy(result['evidence']['scoring_context'])
        for turn in result['turns']:
            if turn['state'] == 'completed' and hasattr(self.backend, 'check_report'):
                bundle = EvidenceBundle.model_validate_json(canonical(result['evidence']), strict=True)
                self.backend.check_report(turn.get('report'), bundle)
            if turn['state'] == 'completed' and hasattr(self.backend, 'case_report_package'):
                saved = turn.get('report_package')
                configured = getattr(self.backend, 'case_reference_service', None) is not None
                if configured and saved is None:
                    raise ConversationError('case_reference_package_missing')
                if configured or saved is not None:
                    bundle = EvidenceBundle.model_validate_json(canonical(result['evidence']), strict=True)
                    if turn['output_evidence_version'] != bundle.evidence_version:
                        raise ConversationError('case_reference_evidence_changed')
                    package = self.backend.case_report_package(value['case_alias'], value['binding'], bundle,
                        task=turn['task'], turn_id=turn['turn_id'], report=turn['report'], saved=saved)
                    if package is not None:
                        turn['report_package'] = package
                        from graphrisk.serving.daily_reference import case_reference_root
                        expected_root = case_reference_root(task=turn['task'], turn_id=turn['turn_id'],
                            case_alias=value['case_alias'], case_binding_sha256=value['binding'],
                            evidence=bundle, report=turn['report'])
                        if turn.get('case_reference_root') != expected_root:
                            raise ConversationError('case_reference_root_changed')
                        turn['case_reference_root'] = expected_root
                        result['case_reference_binding_sha256'] = value['binding']
            turn.pop('key_sha', None)
            turn.pop('fingerprint', None)
        result['storage_mode'] = self.store.mode
        result['incomplete_turn_recovery'] = 'read_only_no_automatic_resend'
        return result

    def set_reference_answer(self, identifier: str, draft: dict):
        """Server-only mechanical guard, not model provenance or semantic acceptance."""
        if not hasattr(self.backend, 'finish_reference_answer'):
            raise ConversationError('public_reference_unconfigured')
        def attach(value):
            self._current(value)
            evidence = EvidenceBundle.model_validate_json(canonical(value['evidence']), strict=True)
            value['public_reference'] = self.backend.finish_reference_answer(
                value['case_alias'], value['binding'], evidence, draft, saved=value.get('public_reference'))
            return value, None
        self.store.mutate(identifier, attach)
        return self.get(identifier)

    def _turn(self, identifier, task, key, fingerprint, parent, version, initial=False):
        key_sha = digest(key)
        def claim(value):
            self._current(value)
            prior = next((t for t in value['turns'] if t['key_sha'] == key_sha), None)
            if prior:
                if prior['fingerprint'] != fingerprint:
                    raise ConversationError('idempotency_conflict')
                return value, ('replay', prior['turn_id'])
            if any(t['state'] == 'running' for t in value['turns']):
                raise ConversationError('turn_in_progress_recovery_required')
            if (initial and value['turns']) or (not initial and (not value['turns'] or parent != value['turns'][-1]['turn_id'])):
                raise ConversationError('parent_turn_conflict')
            bundle = EvidenceBundle.model_validate_json(canonical(value['evidence']))
            if not initial and version != bundle.evidence_version:
                raise ConversationError('evidence_version_conflict')
            if len(value['turns']) >= 6:
                raise ConversationError('conversation_turn_limit')
            program_report = (task == 'risk_assessment' and hasattr(self.backend, 'program_report')
                and not getattr(self.backend, 'formal_report_enabled', False))
            needs_work = task == 'risk_assessment' and not program_report or task in ('investigate', 'continue_checks') and (
                task == 'continue_checks' or any(o.status == 'not_requested' for o in bundle.tool_outcomes))
            if task == 'investigate' and getattr(self.backend, 'local_evidence_only', False):
                needs_work = False
            call_allowance,tool_allowance=(2,0) if task=='risk_assessment' else (5,8)
            if task == 'risk_assessment':
                call_allowance = getattr(self.backend, 'narrative_call_allowance', call_allowance)
            if needs_work:
                if task=='risk_assessment' and not (hasattr(self.backend,'narrative_factory') or hasattr(self.backend,'run_report')):
                    raise ConversationError('narrative_unconfigured')
                if task == 'risk_assessment' and hasattr(self.backend, 'authorize_report'):
                    self.backend.authorize_report()
                elif hasattr(self.backend, 'authorize'):
                    self.backend.authorize()
                if value['reserved_model_calls']+call_allowance > 10 or value['reserved_tool_calls']+tool_allowance > 16:
                    raise ConversationError('conversation_budget_limit')
                # Conservative durable authorization, not fabricated actual usage.
                value['reserved_model_calls'] += call_allowance
                value['reserved_tool_calls'] += tool_allowance
            turn = {'turn_id': uuid4().hex, 'parent_turn_id': parent, 'task': task,
                    'key_sha': key_sha, 'fingerprint': fingerprint, 'state': 'running',
                    'input_evidence_version': bundle.evidence_version,
                    'requires_investigation': needs_work, 'answer': None, 'report': None,
                    'actual_model_calls': 0, 'actual_tool_calls': 0}
            value['turns'].append(turn)
            return value, ('execute', turn['turn_id'])
        mode, turn_id = self.store.mutate(identifier, claim)
        if mode == 'replay':
            return self.get(identifier)
        value = self.store.get(identifier)
        turn = value['turns'][-1]
        bundle = EvidenceBundle.model_validate_json(canonical(value['evidence']))
        report = None
        calls = tools = 0
        outcome = 'reused_evidence'
        try:
            if task == 'risk_assessment' and hasattr(self.backend, 'program_report') and not turn['requires_investigation']:
                report = self.backend.program_report(value['case_alias'], value['binding'], bundle)
                outcome = 'program_daily_report'
            elif turn['requires_investigation']:
                if task=='risk_assessment':
                    if hasattr(self.backend, 'run_report'):
                        result = self.backend.run_report(value['case_alias'], value['binding'], turn_id, bundle.evidence_version)
                    else:
                        from graphrisk.agent.narrative import run_narrative
                        result=run_narrative(self.backend,value['case_alias'],value['binding'],turn_id,bundle.evidence_version)
                else:
                    result = self.backend.investigate(value['case_alias'], value['binding'], turn_id, task, bundle.evidence_version)
                bundle = result['evidence']
                if bundle.case_alias != value['case_alias']:
                    raise ConversationError('evidence_binding_changed')
                report = result['report']
                calls, tools = result['model_calls'], result['tool_calls']
                outcome = result['status']
                model_limit = getattr(self.backend, 'narrative_call_allowance', 5) if task == 'risk_assessment' else 5
                if not 0 <= calls <= model_limit or not 0 <= tools <= 8:
                    raise ConversationError('executor_budget_contract_failed')
            self.backend.check(value['case_alias'], value['binding'])
            answer = evidence_answer(bundle, task)
        except Exception:
            # Retain reservation and running state: an ambiguous model attempt
            # must be reconciled, never automatically resent after a restart.
            raise ConversationError('turn_in_progress_recovery_required') from None
        def publish(current):
            self._current(current)
            pending = current['turns'][-1]
            if pending['turn_id'] != turn_id or pending['state'] != 'running':
                raise ConversationError('turn_publish_conflict')
            pending.update(state='completed', answer=answer, report=report,
                           report_version=turn_id if report else None,
                           approval_eligible=False, output_evidence_version=bundle.evidence_version,
                           actual_model_calls=calls, actual_tool_calls=tools, outcome=outcome,
                           activity=result.get('activity',[]) if turn['requires_investigation'] else [])
            pending['failure_codes']=result.get('failure_codes',[]) if turn['requires_investigation'] else []
            if turn['requires_investigation'] and 'failure_diagnostics' in result:
                pending['failure_diagnostics'] = deepcopy(result['failure_diagnostics'])
            if hasattr(self.backend, 'case_report_package'):
                package = self.backend.case_report_package(current['case_alias'], current['binding'], bundle,
                    task=task, turn_id=turn_id, report=report)
                if package is not None:
                    pending['report_package'] = package
                    from graphrisk.serving.daily_reference import case_reference_root
                    pending['case_reference_root'] = case_reference_root(task=task, turn_id=turn_id,
                        case_alias=current['case_alias'], case_binding_sha256=current['binding'],
                        evidence=bundle, report=report)
            current['evidence'] = bundle.model_dump(mode='json')
            return current, None
        self.store.mutate(identifier, publish)
        return self.get(identifier)
