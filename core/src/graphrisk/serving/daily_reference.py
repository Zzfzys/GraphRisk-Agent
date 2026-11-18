"""Local case questions and per-turn reference appendices; no model selector."""
from __future__ import annotations

from copy import deepcopy
import re

from graphrisk.agent.autonomous_evidence import EvidenceBundle
from graphrisk.agent.public_references import ReferenceContext
from graphrisk.agent.upgrade_contract import canonical, digest


PACKAGE_VERSION = 'daily-case-reference-report-package-v1'
ROOT_VERSION = 'daily-case-reference-root-v1'
CATALOG_VERSION = 'daily-case-reference-catalog-v1'

# Questions select an investigation topic, never assert anonymous transaction facts.
_INVESTIGATION = {
    'question_id': 'authorization-investigation', 'purpose': 'reference_explanation',
    'query': '为本案收集授权材料时，公开资料对调查要求和限制如何说明？仅解释资料，不认定本案事实。 '
             'What investigation requirements and limits concern unauthorized use, a missing report, '
             'and other confirming information? Explain references only, not this transaction.'}
CATALOG = {
    'investigate': _INVESTIGATION,
    'risk_assessment': _INVESTIGATION,
    'continue_checks': _INVESTIGATION,
    'explain_evidence': {
        'question_id': 'authorization-meaning', 'purpose': 'reference_explanation',
        'query': '本案尚需核查授权与获益；公开资料如何定义未经授权使用？不把匿名字段认定为授权事实。 '
                 'Explain unauthorized transfer, actual authority and consumer benefit; '
                 'an anonymous account field does not establish a verified customer fact.'},
    'policy_conditions': {
        'question_id': 'case-applicability-gaps', 'purpose': 'transaction_application',
        'query': '现有本案资料能否证明消费者账户、支付工具、授权和通知条件，从而认定责任规则适用？ '
                 'Can this case establish the consumer account, payment rail, authorization and notice '
                 'facts required to apply a liability rule? Retain unknown case facts.'},
}


def _bundle(evidence):
    raw = evidence.model_dump(mode='json') if isinstance(evidence, EvidenceBundle) else evidence
    return EvidenceBundle.model_validate_json(canonical(raw), strict=True)


def case_reference_root(*, task, turn_id, case_alias, case_binding_sha256, evidence, report):
    bundle = _bundle(evidence)
    policy_as_of = ReferenceContext(as_of=bundle.policy_as_of).as_of.isoformat()
    if (task not in CATALOG or bundle.case_alias != case_alias
            or policy_as_of != bundle.policy_as_of
            or not isinstance(case_binding_sha256, str)
            or re.fullmatch(r'[a-f0-9]{64}', case_binding_sha256) is None
            or not isinstance(turn_id, str) or re.fullmatch(r'[a-f0-9]{32}', turn_id) is None):
        raise ValueError('invalid_case_reference_binding')
    return {'version': ROOT_VERSION, 'task': task, 'turn_id': turn_id, 'case_alias': case_alias,
            'case_binding_sha256': case_binding_sha256, 'evidence_version': bundle.evidence_version,
            'policy_as_of': policy_as_of,
            'report_sha256': digest(report) if report is not None else None}


def build_case_report_package(reference_service, *, task, turn_id, case_alias,
                              case_binding_sha256, evidence, report):
    """Prepare unassessed background from the fixed server task catalog only."""
    root = case_reference_root(task=task, turn_id=turn_id, case_alias=case_alias,
        case_binding_sha256=case_binding_sha256, evidence=evidence, report=report)
    question = CATALOG[task]
    # IEEE card/device fields, scores and inferred identities cannot fill this context.
    context = ReferenceContext(as_of=root['policy_as_of'])
    packet = reference_service.prepare(question['query'], context, purpose=question['purpose'],
                                       top_k=3, planner='rules')
    answer = reference_service.finish(packet, None)
    package = {'version': PACKAGE_VERSION, **{key: value for key, value in root.items() if key != 'version'},
        'reference_root_sha256': digest(root), 'catalog_version': CATALOG_VERSION,
        'catalog_sha256': digest(CATALOG), 'question_id': question['question_id'],
        'request_sha256': digest(packet['request']), 'packet_sha256': digest(packet),
        'answer_sha256': answer['answer_sha256'], 'selection_status': 'not_evaluated',
        'retrieval_status': packet['status'], 'retrieval_error_code': packet['error_code'],
        'context_role': 'case_context_unknown_except_server_policy_date',
        'reference_role': 'case_investigation_reference_not_case_fact',
        'model_calls': 0, 'semantic_support': 'not_evaluated',
        'reference': {'packet': packet, 'answer': answer}}
    package['package_sha256'] = digest(package)
    return package


def verify_case_report_package(value, turn, reference_service):
    """Rebuild from server-projected roots; never load browser-provided paths."""
    try:
        bundle = _bundle(value['evidence'])
        if (turn['state'] != 'completed' or turn['output_evidence_version'] != bundle.evidence_version
                or bundle.case_alias != value['case_alias']):
            raise ValueError('case_reference_evidence_changed')
        root = case_reference_root(task=turn['task'], turn_id=turn['turn_id'],
            case_alias=value['case_alias'], case_binding_sha256=value['case_reference_binding_sha256'],
            evidence=bundle, report=turn['report'])
        if turn['case_reference_root'] != root:
            raise ValueError('case_reference_root_changed')
        expected = build_case_report_package(reference_service, task=turn['task'], turn_id=turn['turn_id'],
            case_alias=value['case_alias'], case_binding_sha256=value['case_reference_binding_sha256'],
            evidence=bundle, report=turn['report'])
        if turn['report_package'] != expected:
            raise ValueError('case_reference_package_changed')
        return deepcopy(expected)
    except (KeyError, TypeError, ValueError, OSError, AttributeError):
        raise ValueError('case_reference_report_package_changed') from None
