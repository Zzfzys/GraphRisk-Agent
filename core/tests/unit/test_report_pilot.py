from copy import deepcopy
import sqlite3
import pytest

from test_autonomous_tool_contracts import scope, index, registry, executor_for, run_core
from test_autonomous_action_providers import profile
from graphrisk.agent.judgment_v3 import packet_for
from graphrisk.agent.report_pilot import (
    MODEL, REVISION, RATES, PRICE_URL, REVISION_URL, reviewed_profile, freeze_contract, check_contract, readonly_budget,
)
from graphrisk.agent.upgrade_contract import canonical, digest


@pytest.fixture
def inputs(scope, index, registry, profile):
    pricing = {'verified_date': '2026-10-24', 'pricing_source': PRICE_URL, 'revision_source': REVISION_URL,
               'model': MODEL, 'documented_revision': REVISION, 'peak_cny_per_million': RATES}
    cfg = reviewed_profile(profile, pricing, '2026-10-24')
    cases = []
    for i in (1, 2):
        current = scope if i == 1 else scope.model_copy(update={'case_alias': 'e'*32, 'transaction_id': 'PRIVATE-SECOND'})
        executor, _ = executor_for(current, index)
        run_core(executor)
        cases.append({'id': 'C'+str(i), 'packet': packet_for(current, executor.observations, registry, cfg),
                      'evidence_file_sha256': str(i)*64})
    caps = {'total_attempts': 1000, 'deepseek_attempts': 600, 'local_attempts': 400,
            'hard_limit_cny': 100, 'pause_estimate_cny': 80}
    return {'profile': cfg, 'pricing': pricing, 'verified_date': '2026-10-24', 'cases': cases,
            'budget': {'counts': {'deepseek': 63, 'local': 23}, 'total_attempts': 86, 'accounted_micro_cny': 2352657,
                       'unknown': [{'attempt_id': 'old-unknown', 'accounted': 2103296}], 'integrity_stops': 0,
                       'supplement_used': 26, 'supplement_remaining': 74,
                       'registration': ['agent-investigation-20261019', canonical(caps)]},
            'source': {'python_inventory_sha256': 'a'*64}, 'parent_contract_sha256': 'b'*64, 'rubric_sha256': 'c'*64}


def test_freeze_is_deterministic_bounded_and_never_authorizes_calls(inputs):
    before = deepcopy(inputs)
    contract, payloads = freeze_contract(**inputs)
    again, payloads2 = freeze_contract(**inputs)
    assert contract == again and payloads == payloads2 and inputs == before
    assert contract['max_requests'] == 4 and contract['max_requests_per_case'] == 2
    assert not contract['execution_ready'] and not contract['outbound_authorized'] and not contract['live_enabled']
    assert contract['budget_before']['unknown'] == before['budget']['unknown']
    assert 'old_unknown_requires_new_report_specific_quarantine' in contract['execution_blockers']
    assert contract['model_calls_for_judging'] == 0 and contract['review']['independent_judge'] == 'Pending'
    assert contract['maximum_new_reserves_micro_cny'] == 8413184
    assert contract['conservative_accounted_plus_all_reserves_micro_cny'] == 10765841
    for payload in payloads.values():
        text = canonical(payload)
        assert 'repair' not in __import__('json').loads(payload['messages'][1]['content'])
        assert all(s not in text for s in ('PRIVATE-TARGET', 'PRIVATE-SECOND', 'is_fraud', 'scope_private'))
    checked = check_contract(contract, budget=inputs['budget'], source=inputs['source'], today='2026-10-24')
    assert checked['new_model_calls'] == 0 and not checked['execution_ready']


@pytest.mark.parametrize('change', ['model', 'revision', 'price', 'date', 'url', 'endpoint', 'input_limit', 'output_limit'])
def test_prices_models_and_input_limits_cannot_be_silently_changed(inputs, change):
    args = deepcopy(inputs)
    if change == 'model':args['pricing']['model'] = 'other-model'
    elif change == 'revision':args['pricing']['documented_revision'] = 'other-revision'
    elif change == 'price':args['pricing']['peak_cny_per_million']['output'] = '7'
    elif change == 'date':args['pricing']['verified_date'] = '2026-10-23'
    elif change == 'url':args['pricing']['pricing_source'] = 'https://untrusted.invalid'
    elif change == 'endpoint':args['profile']['deepseek']['endpoint'] = 'https://untrusted.invalid'
    elif change == 'input_limit':args['profile']['max_prompt_utf8_bytes'] = 14000
    else:args['profile']['max_output_tokens'] = 1536
    with pytest.raises(ValueError):freeze_contract(**args)


@pytest.mark.parametrize('change', ['case_count', 'case_order', 'duplicate_case', 'message_hash', 'include_repair',
                                  'raw_id', 'old_grade_field', 'packet_version', 'registration', 'remaining', 'integrity', 'money'])
def test_wrong_cases_payloads_and_budget_block_freeze(inputs, change):
    args = deepcopy(inputs)
    if change == 'case_count':args['cases'] = args['cases'][:1]
    elif change == 'case_order':args['cases'].reverse()
    elif change == 'duplicate_case':args['cases'][1]['packet']['source_scope_sha256'] = args['cases'][0]['packet']['source_scope_sha256']
    elif change == 'message_hash':args['cases'][0]['packet']['messages_sha256'] = '0'*64
    elif change in ('include_repair', 'raw_id', 'old_grade_field'):
        packet = args['cases'][0]['packet']
        if change == 'include_repair':packet['view']['repair'] = {'source': 'authored-answer'}
        elif change == 'raw_id':packet['view']['injected'] = 'PRIVATE-TARGET'
        else:packet['view']['is_fraud'] = True
        from graphrisk.agent.judgment_v3 import PROMPT
        packet.update(view_sha256=digest(packet['view']), messages_sha256=digest([PROMPT, canonical(packet['view'])]),
                      prompt_utf8_bytes=len((PROMPT+canonical(packet['view'])).encode()))
    elif change == 'packet_version':args['cases'][0]['packet']['schema_version'] = 'old'
    elif change == 'registration':args['budget']['registration'][0] = 'reset-budget'
    elif change == 'remaining':args['budget']['supplement_remaining'] = 3
    elif change == 'integrity':args['budget']['integrity_stops'] = 1
    else:args['budget']['accounted_micro_cny'] = 79_000_000
    with pytest.raises(ValueError):freeze_contract(**args)


@pytest.mark.parametrize('change', ['hash', 'ready', 'date', 'budget', 'source'])
def test_check_rejects_changed_frozen_state_and_never_resumes_old_stops(inputs, change):
    contract, _ = freeze_contract(**inputs)
    kwargs = {'budget': inputs['budget'], 'source': inputs['source'], 'today': '2026-10-24'}
    if change == 'hash':contract['max_requests'] = 100
    elif change == 'ready':
        contract['execution_ready'] = True
        contract['contract_sha256'] = digest({k:v for k,v in contract.items() if k != 'contract_sha256'})
    elif change == 'date':kwargs['today'] = '2026-10-25'
    elif change == 'budget':kwargs['budget'] = {**kwargs['budget'], 'total_attempts': 87}
    else:kwargs['source'] = {'python_inventory_sha256': 'new'}
    with pytest.raises(ValueError):check_contract(contract, **kwargs)


def test_readonly_probe_never_creates_an_absent_ledger(tmp_path):
    target = tmp_path/'absent.sqlite3'
    with pytest.raises(ValueError, match='pilot_readonly_budget_unavailable'):
        readonly_budget(target)
    assert not target.exists()


def test_freeze_cli_has_no_live_generation_switch(monkeypatch):
    import freeze_report_pilot
    monkeypatch.setattr('sys.argv', ['freeze_report_pilot', '--live'])
    with pytest.raises(SystemExit) as exc:
        freeze_report_pilot.main()
    assert exc.value.code == 2
