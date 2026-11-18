"""Small authored cases exercise scoring arithmetic, never actual model quality."""
from __future__ import annotations

from copy import deepcopy
import socket

import pytest

import evaluate_reference_answers as evaluation


def case(positive=True):
    return {'case_id': 'fixture', 'kind': 'positive_reference_explanation' if positive else 'negative_no_answer',
            'query': 'authored question', 'purpose': 'reference_explanation', 'context': {'as_of': '2026-11-12'},
            'expected_decision': 'cite_reference' if positive else 'insufficient_evidence',
            'expected_reason': 'reference_explanation' if positive else 'no_answer_in_sources',
            'expected_status': 'reference_excerpt' if positive else 'insufficient_evidence',
            'gold_sections': [{'doc_id': 'fixture-doc', 'section': 'target'}] if positive else []}


def saved(decision='cite_reference', section='target'):
    draft = {'decision': decision, 'reason': 'reference_explanation' if decision == 'cite_reference' else 'no_answer_in_sources',
             'citation_ids': ['R1'] if decision == 'cite_reference' else []}
    source = {'ref': 'R1', 'doc_id': 'fixture-doc', 'section': section, 'text': 'Authored complete fixture.'}
    return {'provider_result': {'code': 'ok', 'action': draft},
            'answer': {'status': 'reference_excerpt' if decision == 'cite_reference' else 'insufficient_evidence',
                       'source_integrity': 'verified_saved_excerpt', 'semantic_support': 'requires_review',
                       'draft': draft, 'sources': [source] if decision == 'cite_reference' else [],
                       'retrieved_sources': [source]}}


def test_valid_but_wrong_source_cannot_count_as_complete_answer_support():
    wrong = evaluation.score_case(case(), saved(section='distractor'))
    assert wrong['schema_valid'] and wrong['source_integrity_verified']
    assert not wrong['positive_all_gold_selected'] and not wrong['structural_target_pass']
    assert wrong['positive_incomplete_selection']
    right = evaluation.score_case(case(), saved())
    assert right['positive_all_gold_selected'] and right['structural_target_pass']
    assert right['semantic_correctness'] == 'NotEvaluated' and right['human_reading'] == 'Pending'
    assert right['model_origin'] == 'not_authenticated_by_pure_scorer'


def test_positive_refusal_is_not_a_false_pass_and_background_does_not_invalidate_negative_refusal():
    refusal = saved('insufficient_evidence')
    positive = evaluation.score_case(case(), refusal)
    assert positive['positive_over_refusal'] and not positive['structural_target_pass']
    negative = evaluation.score_case(case(False), refusal)
    assert negative['answer']['retrieved_sources']
    assert negative['structural_target_pass'] and not negative['no_answer_wrong_citation']
    wrong_negative = evaluation.score_case(case(False), saved())
    assert wrong_negative['no_answer_wrong_citation'] and not wrong_negative['structural_target_pass']


def test_negative_reference_gold_is_not_a_required_refusal_citation():
    negative = case(False)
    negative['gold_sections'] = [{'doc_id': 'fixture-doc', 'section': 'background-only'}]
    row = evaluation.score_case(negative, saved('insufficient_evidence'))
    assert row['selected_gold_count'] == 0 and row['positive_all_gold_selected'] is None
    assert row['structural_target_pass']


@pytest.mark.parametrize('bad', [None, {'provider_result': {'code': 'provider_connection_unknown'}},
                                {'provider_result': {'code': 'invalid_model_action'}},
                                {'provider_result': {'code': 'ok', 'action': {'answer': 'unbounded prose'}}}])
def test_missing_or_invalid_output_stays_in_original_denominator(bad):
    failed = evaluation.score_case(case(), bad)
    valid = evaluation.score_case(case(), saved())
    summary = evaluation.summarize([failed, valid])
    assert not failed['structural_target_pass']
    assert failed['missing_response'] or failed['invalid_output']
    assert summary['case_count'] == summary['positive_count'] == 2
    assert summary['positive_complete_target_count'] == 1 and summary['positive_complete_target_rate'] == .5
    assert summary['semantic_correctness'] == 'NotEvaluated'


def test_source_integrity_failure_is_separate_from_gold_coverage():
    result = deepcopy(saved())
    result['answer']['source_integrity'] = 'unavailable'
    row = evaluation.score_case(case(), result)
    assert row['positive_all_gold_selected'] and row['source_integrity_error']
    assert not row['structural_target_pass']


def test_evaluation_never_writes_without_shared_authenticator_success(tmp_path, monkeypatch):
    def reject_network(*args, **kwargs):
        pytest.fail('the offline evaluator must not access a model or network')

    def reject_authentication(*args, **kwargs):
        raise ValueError('authored fixture: shared authenticator rejected this input')

    monkeypatch.setattr(socket.socket, 'connect', reject_network)
    monkeypatch.setattr(evaluation, 'check', reject_authentication)
    target = tmp_path / 'not-written'
    with pytest.raises(ValueError, match='shared authenticator rejected'):
        evaluation.evaluate_completed(tmp_path / 'untrusted-config.json', target)
    assert not target.exists()
