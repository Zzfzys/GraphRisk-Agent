from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import httpx
import pytest

from test_autonomous_tool_contracts import scope, index, registry, executor_for, run_core, ROOT
from test_autonomous_action_providers import profile, ledger, cloud_body
from test_judgment_v3 import good
from graphrisk.agent import report_pilot_execution as channel
from graphrisk.agent import judgment_v3 as v3
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_loop import observation_rows
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.report_quality import blank_review
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash
from run_report_pilot import run_case, review_case, authorize, connection_preflight


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value)+'\n', encoding='utf-8')


@pytest.fixture
def pilot(tmp_path, scope, index, registry, profile):
    budget = ledger(tmp_path, {'test': 'natural-pilot'}, cap=100)
    old = budget.reserve(provider='deepseek', request_key='original-unknown', packet_sha='a'*64,
                         phase='pilot', reserve_micro=2103296)
    budget.unknown(old, 'provider_http_rejected')
    store = SessionStore(budget)
    root = tmp_path/'source'
    (root/'configs').mkdir(parents=True)
    policy = root/'configs/agent_investigation_policy_requirements.json'
    policy.write_bytes((ROOT/'configs/agent_investigation_policy_requirements.json').read_bytes())
    source = {'python_files': {}, 'python_inventory_sha256': digest({}),
              'configs': {policy.name: file_hash(policy)}}
    index_path = tmp_path/'index.json'
    write(index_path, index.model_dump(mode='json'))
    output = tmp_path/'batch'
    output.mkdir()
    files = {str(index_path): file_hash(index_path)}
    cfg = deepcopy(profile)
    cfg['deepseek']['pricing_verified_date'] = date.today().isoformat()
    cases = {}
    for n in (1, 2):
        case = 'C'+str(n)
        current = scope if n == 1 else scope.model_copy(update={'case_alias': 'e'*32, 'transaction_id': 'PRIVATE-SECOND'})
        executor, _ = executor_for(current, index)
        run_core(executor)
        bundle = build_evidence(current, executor.observations, registry)
        packet = v3.packet_for(current, executor.observations, registry, cfg)
        raw = output/(case+'-raw.json')
        write(raw, {'scope': current.model_dump(mode='json'), 'observations': observation_rows(executor.observations),
                    'evidence': bundle.model_dump(mode='json')})
        files[str(raw)] = file_hash(raw)
        formatter = object.__new__(v3.JudgmentProvider)
        formatter.provider, formatter.profile = 'deepseek', cfg
        cases[case] = {'raw_evidence_path': str(raw), 'first_payload_sha': digest(formatter.payload(packet)),
                       'evidence_sha': digest(bundle.model_dump(mode='json')), 'session': 'batch-'+case,
                       'session_manifest': {'case': case, 'evidence': digest(bundle.model_dump(mode='json'))}}
    with budget.transaction() as db:
        unknown = channel.unknown_record(db, old)
        base = channel.baseline(db)
    contract = {'version': channel.VERSION, 'source_root': str(root), 'source': source, 'files': files,
                'cases': cases, 'registry_index': str(index_path), 'campaign': 'batch', 'max_requests': 4,
                'max_requests_per_case': 2, 'order': ['C1', 'C2'], 'profile': cfg,
                'pricing_date': date.today().isoformat(), 'unknown': unknown, 'baseline': base, 'closed_campaigns': ['old-stopped']}
    contract['contract_sha256'] = digest(contract)
    write(output/'contract-private.json', contract)
    store.campaign('batch', contract['contract_sha256'], cap_each=4, group_key='route5_supplement_v3')
    channel.register(budget, output/'contract-private.json', 'User explicitly approved exactly these two cases, max four calls.')
    return budget, store, contract, output


def request(pilot, case='C1', slot=0):
    budget, store, cfg, output = pilot
    session = cfg['cases'][case]['session']
    store.open(session, cfg['campaign'], cfg['cases'][case]['session_manifest'], 'deepseek', calls=2, seconds=600)
    with budget.transaction() as db:
        packet, _, _ = channel.expected_packet(db, cfg, case, slot)
    wrapper = {'profile_sha256': digest(cfg['profile']), 'packets': [packet], 'session': session}
    phase = store.register_packet(session, slot, digest(wrapper), 'deepseek')
    payload = channel.bind_packet(budget, cfg['campaign'], case, phase, packet)
    return phase, packet, payload, digest([digest(wrapper), 'deepseek', str(slot)])


def reserve(pilot, case='C1', slot=0, **overrides):
    phase, packet, payload, key = request(pilot, case, slot)
    args = {'provider': 'deepseek', 'request_key': key, 'packet_sha': digest(payload), 'phase': phase, 'reserve_micro': 2103296}
    args.update(overrides)
    return pilot[0].reserve(**args)


def transport(values, posts):
    def handler(request):
        posts.append(json.loads(request.content))
        return httpx.Response(200, json=cloud_body(values[len(posts)-1]))
    return httpx.MockTransport(handler)


def review(pilot, case):
    budget, _, cfg, output = pilot
    final_path = output/(case+'-result-private.json')
    final = channel.read(final_path)
    items = []
    for slot in final['slots']:
        row = channel.read(slot['path'])
        if row['quality_packet_path']:
            quality = channel.read(row['quality_packet_path'])
            value = blank_review(quality)
            value['reviewer'] = {'kind': 'assistant_development', 'reviewer_id': 'fixture', 'version': 'fixture-v1'}
            value['facts'] = {'status': 'Pass', 'reason': 'Fixture evidence reviewed; not real model quality.', 'anchors': ['risk_level']}
            value['scores'] = {n: {'score': 2, 'reason': 'Fixture rubric input only.', 'anchors': ['risk_level']}
                               for n in ('specificity', 'analysis', 'actionability', 'readability')}
            for role in value['roles']:
                role['role'] = 'alternative'
            items.append({'slot': slot['slot'], 'review': value,
                          'risk_level_review': {'status': 'Pass', 'reason': 'Fixture only; undetermined is evidence limited.'}})
        else:
            items.append({'slot': slot['slot'], 'review': None})
    path = output/(case+'-input-review.json')
    write(path, {'case': case, 'result_sha256': file_hash(final_path), 'drafts': items})
    return review_case(output, output, case, path, budget=budget)


def test_valid_single_first_drafts_need_two_calls_not_four_and_saved_replay_is_free(pilot):
    budget, _, cfg, output = pilot
    posts = []
    mock = transport([good(), good()], posts)
    assert run_case(output, output, 'C1', budget=budget, transport=mock)['attempts'] == 1
    assert review(pilot, 'C1')['quality'] == 'Pass'
    assert run_case(output, output, 'C2', budget=budget, transport=mock)['attempts'] == 1
    assert review(pilot, 'C2')['quality'] == 'Pass'
    assert run_case(output, output, 'C1', budget=budget, transport=mock)['status'] == 'saved_case_no_new_request'
    assert len(posts) == 2 and budget.snapshot()['total_attempts'] == 3
    with budget.transaction() as db:
        assert channel.unknown_record(db, cfg['unknown']['attempt_id']) == cfg['unknown']
        assert channel.baseline(db, 'batch') == cfg['baseline']
        assert db.execute('SELECT closed_reason FROM report_pilot_grants').fetchone() == ('two_cases_completed',)


def test_first_case_review_is_mandatory_before_second_post(pilot):
    with pytest.raises(ExecutionStop, match='first_case_review_required'):
        reserve(pilot, 'C2')
    assert pilot[0].snapshot()['total_attempts'] == 1


@pytest.mark.parametrize('change', ['payload', 'request_key', 'local', 'second_session', 'third_slot', 'unbound'])
def test_only_bound_case_slot_payload_can_reserve(pilot, change):
    budget, store, cfg, _ = pilot
    phase, packet, payload, key = request(pilot)
    if change == 'second_session':
        store.open('other', 'batch', {'other': True}, 'deepseek', calls=2, seconds=600)
        phase = store.register_packet('other', 0, digest('other'), 'deepseek')
    elif change == 'third_slot':
        with pytest.raises(ExecutionStop): store.register_packet('batch-C1', 2, digest('third'), 'deepseek')
        return
    elif change == 'unbound':
        with budget.transaction() as db: db.execute('DELETE FROM report_pilot_payloads')
    with pytest.raises(ExecutionStop):
        budget.reserve(provider='local' if change == 'local' else 'deepseek', request_key='wrong' if change == 'request_key' else key,
                       packet_sha=digest('wrong') if change == 'payload' else digest(payload), phase=phase,
                       reserve_micro=0 if change == 'local' else 2103296)
    assert budget.snapshot()['total_attempts'] == 1


@pytest.mark.parametrize('change', ['unknown_amount', 'new_unknown', 'new_reserved', 'integrity', 'old_row', 'file', 'source', 'date'])
def test_changed_unknown_source_or_any_new_unknown_cannot_be_waived(pilot, change):
    budget, store, cfg, output = pilot
    phase, packet, payload, key = request(pilot)
    with budget.transaction() as db:
        if change == 'unknown_amount': db.execute('UPDATE execution_requests SET accounted=0 WHERE attempt_id=?', (cfg['unknown']['attempt_id'],))
        elif change in ('new_unknown', 'new_reserved'):
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)', ('unexpected', 'deepseek', 'unknown' if change == 'new_unknown' else 'reserved', 1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)', ('unexpected','unexpected','pilot','x',1))
        elif change == 'integrity': db.execute('UPDATE execution_requests SET integrity_stop=1')
        elif change == 'old_row': db.execute("UPDATE execution_requests SET usage_json='{}'")
    if change == 'file': Path(cfg['registry_index']).write_text('{}')
    elif change == 'source':
        (Path(cfg['source_root'])/'src').mkdir()
        (Path(cfg['source_root'])/'src/new.py').write_text('pass')
    elif change == 'date':
        cfg['pricing_date'] = '2020-01-01'
        cfg['contract_sha256'] = digest({k:v for k,v in cfg.items() if k != 'contract_sha256'})
        write(output/'contract-private.json', cfg)
    with pytest.raises(ExecutionStop):
        budget.reserve(provider='deepseek', request_key=key, packet_sha=digest(payload), phase=phase, reserve_micro=2103296)


def test_repeat_authorization_is_idempotent_but_cannot_register_another_batch(pilot):
    budget, store, cfg, output = pilot
    statement = 'User explicitly approved exactly these two cases, max four calls.'
    channel.register(budget, output/'contract-private.json', statement)
    with pytest.raises(ExecutionStop, match='single_batch_no_reset'):
        channel.register(budget, output/'contract-private.json', statement+' more')
    cfg['campaign'] = 'new-batch'
    cfg['contract_sha256'] = digest({k:v for k,v in cfg.items() if k != 'contract_sha256'})
    write(output/'other-contract.json', cfg)
    store.campaign('new-batch', cfg['contract_sha256'], cap_each=4, group_key='route5_supplement_v3')
    with pytest.raises(ExecutionStop, match='single_batch_no_reset'):
        channel.register(budget, output/'other-contract.json', statement)


def test_repair_requires_real_failed_saved_response_not_authored_candidate(pilot):
    with pytest.raises(ExecutionStop, match='actual_first_response'):
        request(pilot, slot=1)
    budget, _, cfg, output = pilot
    posts = []
    assert run_case(output, output, 'C1', budget=budget, transport=transport([good()], posts))['attempts'] == 1
    with pytest.raises(ExecutionStop, match='valid_first_needs_no_repair'):
        request(pilot, slot=1)


def test_actual_failed_first_and_exact_patch_can_use_four_total_requests(pilot):
    budget, _, cfg, output = pilot
    bad = good()
    bad['risk_level'] = 'high'
    bad['rationale']['text'] = '目标交易金额为999999。'
    posts = []
    def handler(req):
        payload = json.loads(req.content)
        posts.append(payload)
        body = json.loads(payload['messages'][1]['content'])
        if len(posts) % 2:
            answer = bad
        else:
            repair = body['repair']
            # The actual repair format is frozen by v3; use saved plan, not a witness in messages.
            with budget.transaction() as db:
                packet, bundle, plan = channel.expected_packet(db, cfg, 'C1' if len(posts)==2 else 'C2', 1)
            answer = {'base_sha256': plan['base_sha256'], 'changes': [
                {'path': p, 'op': 'replace', 'value': good()[p]} for p in plan['allowed_paths']]}
        return httpx.Response(200, json=cloud_body(answer))
    mock = httpx.MockTransport(handler)
    assert run_case(output, output, 'C1', budget=budget, transport=mock)['attempts'] == 2
    assert review(pilot, 'C1')['quality'] == 'Pass'
    assert run_case(output, output, 'C2', budget=budget, transport=mock)['attempts'] == 2
    assert review(pilot, 'C2')['quality'] == 'Pass'
    assert len(posts) == 4 and budget.snapshot()['total_attempts'] == 5
    with pytest.raises(ExecutionStop): reserve(pilot)


def test_timeout_is_saved_once_and_no_second_request_is_sent(pilot):
    budget, _, cfg, output = pilot
    posts = []
    def handler(req):
        posts.append(req)
        raise httpx.ReadTimeout('private text must not leak', request=req)
    result = run_case(output, output, 'C1', budget=budget, transport=httpx.MockTransport(handler))
    assert result['status'] == 'stopped' and len(posts) == 1
    assert budget.snapshot()['unresolved_attempts'] == 2
    with pytest.raises(ExecutionStop): reserve(pilot, 'C2')
    assert 'private text' not in canonical(channel.read(output/'C1-STOP.json'))


def test_closed_grant_cannot_be_reopened_by_new_phase_or_authorization(pilot):
    budget, _, cfg, output = pilot
    channel.stop(budget, 'batch', 'fixture_stop')
    with pytest.raises(ExecutionStop, match='closed_fixture_stop'): request(pilot)
    with pytest.raises(ExecutionStop, match='closed_fixture_stop'):
        channel.register(budget, output/'contract-private.json', 'User explicitly approved exactly these two cases, max four calls.')


def test_exact_permission_contract_binding_required_before_registration(pilot, monkeypatch):
    budget, _, cfg, output = pilot
    path = output/'permission.json'
    write(path, {'contract_sha256': '0'*64, 'authorization': 'yes'})
    monkeypatch.setattr('run_report_pilot.ledger', lambda runtime: budget)
    with pytest.raises(ExecutionStop, match='not_bound_to_new_contract'):
        authorize(output, output, path)


def test_four_total_hard_cap_is_enforced_inside_reservation(pilot):
    budget, store, cfg, output = pilot
    phase, packet, payload, key = request(pilot)
    with budget.transaction() as db:
        for n in range(4):
            identifier = 'already-'+str(n)
            db.execute('INSERT INTO model_attempts VALUES(?,?,?,?)', (identifier, 'deepseek', 'settled', 1))
            db.execute('INSERT INTO execution_requests(attempt_id,request_key,phase,packet_sha,accounted) VALUES(?,?,?,?,?)',
                       (identifier, identifier, phase, digest(identifier), 1))
    with pytest.raises(ExecutionStop, match='four_request_cap'):
        budget.reserve(provider='deepseek', request_key=key, packet_sha=digest(payload), phase=phase, reserve_micro=2103296)
    assert budget.snapshot()['total_attempts'] == 5


def test_stopped_parent_cannot_use_old_ten_cell_recovery_after_new_grant(pilot):
    budget, store, cfg, output = pilot
    store.campaign('old-stopped', digest('old'), cap_each=10, group_key='route5_supplement_v3')
    store.open('old-session', 'old-stopped', {'old': True}, 'deepseek', calls=1, seconds=600)
    phase = store.register_packet('old-session', 0, digest('old-packet'), 'deepseek')
    with pytest.raises(ExecutionStop, match='previous_campaign_closed'):
        budget.reserve(provider='deepseek', request_key='old-next', packet_sha=digest('old-next'), phase=phase, reserve_micro=2103296)


def test_known_hard_fail_cannot_be_rescued_by_eight_point_review(pilot):
    budget, _, _, output = pilot
    bad = good()
    bad['rationale']['text'] = '目标交易金额为999999。'
    posts = []
    # One invalid patch is also retained; there is no third call for the case.
    patch = {'base_sha256': '0'*64, 'changes': [{'path': 'rationale', 'op': 'replace', 'value': good()['rationale']}]}
    result = run_case(output, output, 'C1', budget=budget, transport=transport([bad, patch], posts))
    assert result['attempts'] == 2 and len(posts) == 2
    assert review(pilot, 'C1')['quality'] == 'Fail'
    review_data = channel.read(output/'C1-review.json')
    assert review_data['drafts'][0]['quality'] == 'Fail'


def test_output_truncation_stops_without_using_repair_slot(pilot):
    budget, _, cfg, output = pilot
    posts = []
    def handler(req):
        posts.append(req)
        body = cloud_body(good())
        body['choices'][0]['finish_reason'] = 'length'
        return httpx.Response(200, json=body)
    result = run_case(output, output, 'C1', budget=budget, transport=httpx.MockTransport(handler))
    assert result['attempts'] == 1 and len(posts) == 1
    with pytest.raises(ExecutionStop, match='closed_output_or_provider_limit'): request(pilot, 'C2')


@pytest.mark.parametrize('status', [200, 401])
def test_free_connectivity_check_never_sends_auth_or_model_post(status):
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(status)
    result = connection_preflight(transport=httpx.MockTransport(handler))
    assert result['model_requests'] == 0 and not result['credentials_used']
    assert len(seen) == 1 and seen[0].method == 'GET' and 'authorization' not in seen[0].headers


@pytest.mark.parametrize('failure', ['permission', 'service'])
def test_free_connectivity_failure_is_actionable_before_reservation(failure):
    def handler(req):
        if failure == 'permission': raise httpx.ConnectError('blocked', request=req)
        return httpx.Response(503)
    with pytest.raises(ExecutionStop, match='before_reservation'):
        connection_preflight(transport=httpx.MockTransport(handler))


def test_connection_block_does_not_allocate_session_attempt_or_stop_grant(pilot, monkeypatch):
    budget, _, cfg, output = pilot
    before = budget.snapshot()
    # Exercise the live orchestration gate without credentials or a POST.
    budget.mode = 'live'
    def fail(): raise ExecutionStop('report_pilot_connection_unavailable_before_reservation')
    monkeypatch.setattr('run_report_pilot.connection_preflight', fail)
    with pytest.raises(ExecutionStop, match='before_reservation'):
        run_case(output, output, 'C1', budget=budget)
    budget.mode = 'fixture'
    assert budget.snapshot() == before
    with budget.transaction() as db:
        assert db.execute('SELECT count(*) FROM autonomous_sessions').fetchone() == (0,)
        assert db.execute('SELECT closed_reason FROM report_pilot_grants').fetchone() == (None,)
