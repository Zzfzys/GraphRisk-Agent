"""Explicit, separately approved web calls beside the frozen Route9 execution.

The four installed hooks are part of the new execution protocol. They never
change the original contract, source files, history, or saved case outcomes.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3

from graphrisk.agent import autonomous_session as sessions
from graphrisk.agent import full_case_execution as formal
from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_providers import cloud_usage, reservation_micro
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash

VERSION = 'daily-web-trial-with-route9-v1'
GROUP = 'daily_web_trial_v1'
ABSOLUTE = {'deepseek': 1771, 'local': 348, 'total': 2119}
_ORIGINAL_HISTORY = formal.history_rows
_ORIGINAL_GUARD = formal.guard
_ORIGINAL_IDENTITY = formal.identity_for_attempt
_ORIGINAL_SESSION_GUARD = sessions.guard_session_reservation
_ACTIVE = None


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(ok, code):
    if not ok:
        raise ExecutionStop('web_trial_' + code)


class TrialProtocol:
    def __init__(self, config_path, approval_path=None):
        self.config_path = Path(config_path).resolve()
        self.approval_path = Path(approval_path).resolve() if approval_path else None
        self.cfg = read(self.config_path)
        self.sha256 = file_hash(self.config_path)
        self.route9 = read(self.cfg['route9_execution_path'])
        self._validate_files()

    def _validate_files(self):
        cfg = self.cfg
        require(file_hash(self.config_path) == self.sha256, 'config_changed')
        require(cfg['version'] == VERSION and cfg['group'] == GROUP, 'version')
        require(file_hash(Path(cfg['route9_execution_path'])) == cfg['route9_execution_sha256'], 'route9_contract_changed')
        require(cfg['source_inventory'] == self.route9['source_inventory'], 'route9_source_inventory')
        root = Path(cfg['source_root'])
        require(root.resolve() == Path(self.route9['source_root']).resolve(), 'source_root_changed')
        actual = {p.relative_to(root).as_posix(): file_hash(p)
                  for folder in ('src', 'api', 'scripts') for p in (root / folder).rglob('*.py')}
        require(actual == cfg['source_inventory'], 'source_changed')
        for inventory in ('files', 'extension_files'):
            for path, sha in cfg[inventory].items():
                require(Path(path).is_file() and file_hash(Path(path)) == sha, 'bound_file_changed')
        require(cfg['extension_files'].get(str(Path(__file__).resolve())) == file_hash(Path(__file__)), 'extension_not_bound')
        caps = cfg['caps']
        require(caps['absolute'] == ABSOLUTE and caps['web_calls'] == 6
                and caps['web_cost_micro_cny'] == 5_000_000
                and caps['pause_micro_cny'] == 80_000_000
                and caps['total_cost_micro_cny'] == 100_000_000, 'caps')
        pending = cfg['route9_pending_sessions']
        registered = {e['session_id'] for e in self.route9['episodes'] if e['provider'] == 'local'}
        require(set(pending) <= registered and len(set(pending)) == len(pending) and len(pending) * 5 == caps['route9_remaining_calls']
                and 0 < caps['route9_remaining_calls'] <= 200, 'remaining_route9_scope')
        if not cfg.get('fixture'):
            require(len(pending) == 40 and cfg['baseline']['count'] == 1802
                    and cfg['baseline']['provider_counts'] == {'deepseek': 1707, 'local': 95}, 'live_baseline')
        require(cfg['baseline']['count'] + caps['web_calls'] + caps['route9_remaining_calls'] <= ABSOLUTE['total'], 'remaining_allocation')
        cases = cfg['cases']
        require(len(cases) == 6 and len({c['transaction_id'] for c in cases}) == 6
                and len({c['alias'] for c in cases}) == 6
                and len({c['payload_sha256'] for c in cases}) == 6
                and len({c['packet_sha256'] for c in cases}) == 6, 'case_whitelist')
        profile = read(cfg['profile_path'])
        require(digest(profile) == cfg['profile_sha256']
                and all(c['profile_sha256'] == cfg['profile_sha256'] for c in cases), 'profile_changed')
        require(profile['deepseek']['endpoint'] == 'https://api.deepseek.com/chat/completions'
                and profile['deepseek']['model'] == 'deepseek-flash'
                and cfg['expected_identity'] == self.route9['identities']['deepseek'], 'provider_scope')

    def authorize(self):
        """Validate concrete approval; this method creates no rows or files."""
        self._validate_files()
        require(self.approval_path is not None and self.approval_path.is_file(), 'specific_approval_required')
        note = read(self.approval_path)
        require(note.get('outbound_authorized') is True
                and note.get('config_sha256') == self.sha256
                and note.get('max_web_requests') == 6
                and note.get('max_cost_micro_cny') == 5_000_000
                and note.get('route9_resume_protocol_authorized') is True
                and isinstance(note.get('question_item_id'), str) and bool(note['question_item_id'].strip())
                and isinstance(note.get('user_answer'), str) and bool(note['user_answer'].strip()), 'specific_approval_required')
        profile = read(self.cfg['profile_path'])
        require(profile['deepseek']['pricing_verified_date'] == date.today().isoformat(), 'pricing_refresh_required')
        return note

    def _case(self, payload_sha=None, packet_sha=None):
        found = [c for c in self.cfg['cases'] if
                 (payload_sha is None or c['payload_sha256'] == payload_sha)
                 and (packet_sha is None or c['packet_sha256'] == packet_sha)]
        require(len(found) == 1, 'payload_outside_approved_cases')
        return found[0]

    def _grant(self, db, *, required=True):
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='web_trial_grants'").fetchone()
        row = db.execute('SELECT config_path,config_sha,approval_path,approval_sha FROM web_trial_grants WHERE campaign_id=?',
                         (self.cfg['campaign'],)).fetchone() if exists else None
        if row is None:
            require(not required, 'grant_not_registered')
            return None
        self.authorize()
        require(row == (str(self.config_path), self.sha256, str(self.approval_path), file_hash(self.approval_path)), 'grant_changed')
        require(db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',
                           (self.cfg['campaign'],)).fetchone() == (self.sha256, 6, GROUP), 'campaign_changed')
        return row

    def _partition(self, db, *, validate_receipts=True):
        """Authenticate the complete ledger before returning its named partitions."""
        self._validate_files()
        rows = _ORIGINAL_HISTORY(db)
        base = self.cfg['baseline']
        identities = db.execute('SELECT provider,identity FROM provider_identity ORDER BY provider').fetchall()
        require(digest(identities) == base['provider_identity_rows_sha256'], 'historical_provider_identity_changed')
        require(len(rows) >= base['count'] and digest(rows[:base['count']]) == base['rows_sha256'], 'baseline_changed')
        require({p: sum(r[1] == p for r in rows[:base['count']]) for p in ('deepseek', 'local')} == base['provider_counts'], 'baseline_provider_counts')
        require(digest([r for r in rows if r[2] in ('reserved', 'unknown')]) == base['unknown_rows_sha256'], 'new_unresolved_attempt')
        require(digest([r for r in rows if r[-1]]) == base['integrity_rows_sha256'], 'new_integrity_stop')
        web, route9 = [], []
        old_campaigns = {self.route9['campaign']}
        if self.route9.get('predecessor'):
            old_campaigns.add(self.route9['predecessor']['campaign'])
        for row in rows[base['count']:]:
            bound = db.execute('SELECT s.campaign_id,c.group_key,s.session_id,s.provider,s.max_calls,p.slot '
                               'FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) '
                               'JOIN autonomous_campaigns c USING(campaign_id) WHERE p.phase=?', (row[5],)).fetchone()
            require(bound is not None, 'unclassified_attempt')
            if bound[0] == self.cfg['campaign']:
                self._grant(db)
                require(bound[1] == GROUP and bound[3:] == ('deepseek', 1, 0)
                        and row[1] == 'deepseek' and row[2] == 'settled' and row[-1] == 0, 'web_attempt_binding')
                case = self._case(payload_sha=row[6])
                self._verify_web_request(db, row[5], row[4], row[6], case)
                if validate_receipts:
                    require(row[9] and Path(row[9]).is_file() and file_hash(Path(row[9])) == row[8]
                            and row[11] == self.cfg['expected_identity'], 'web_receipt_changed')
                    receipt = read(row[9])
                    require(receipt['attempt_id'] == row[0] and receipt['payload_sha256'] == row[6]
                            and receipt['profile_sha256'] == case['profile_sha256'], 'web_receipt_scope')
                    usage, cost = cloud_usage(receipt['body'], read(self.cfg['profile_path']))
                    require(canonical([receipt['body'].get('model'), receipt['body'].get('system_fingerprint')]) == row[11]
                            and canonical(usage) == row[12] and cost == row[7] and cost <= row[3], 'web_receipt_accounting')
                web.append(row)
            else:
                require(bound[0] in old_campaigns and bound[1] == formal.GROUP
                        and bound[2] in self.cfg['route9_pending_sessions'] and row[1] == 'local'
                        and row[2] == 'settled' and row[-1] == 0, 'unrelated_new_attempt')
                route9.append(row)
        require(len(web) <= 6 and len({r[6] for r in web}) == len(web), 'case_already_requested')
        require(sum(r[7] for r in web) <= self.cfg['caps']['web_cost_micro_cny'], 'web_cost_cap')
        require(len(route9) <= self.cfg['caps']['route9_remaining_calls'], 'route9_remaining_call_cap')
        counts = {p: sum(r[1] == p for r in rows) for p in ('deepseek', 'local')}
        require(len(rows) <= ABSOLUTE['total'] and all(counts[p] <= ABSOLUTE[p] for p in counts), 'global_attempt_cap')
        return rows, web, route9

    def _verify_web_request(self, db, phase, request_key, payload_sha, case):
        row = db.execute('SELECT manifest_sha FROM execution_phases WHERE phase=?', (phase,)).fetchone()
        require(row is not None and request_key == digest([row[0], 'deepseek', '0']), 'request_key_binding')
        binding = db.execute('SELECT wrapper_json FROM web_trial_packets WHERE phase=?', (phase,)).fetchone()
        require(binding is not None, 'packet_not_registered')
        wrapper = json.loads(binding[0])
        require(digest(wrapper) == row[0], 'wrapper_changed')
        self._validate_campaign(wrapper, read(self.cfg['profile_path']), case=case)
        require(payload_sha == case['payload_sha256'], 'payload_changed')

    def _validate_campaign(self, campaign, profile, *, case=None):
        require(set(campaign) == {'profile_sha256', 'packets', 'session'}
                and campaign.get('profile_sha256') == digest(profile) == self.cfg['profile_sha256']
                and len(campaign.get('packets', [])) == 1, 'campaign_payload_binding')
        packet = campaign['packets'][0]
        case = case or self._case(packet_sha=digest(packet))
        require(digest(packet) == case['packet_sha256'] and packet.get('logical_key') == '0'
                and packet.get('source_scope_sha256') == case['scope_sha256']
                and packet.get('scope_private', {}).get('transaction_id') == case['transaction_id'], 'packet_scope_changed')
        require(isinstance(campaign.get('session'), str) and bool(campaign['session']), 'session_binding')
        from graphrisk.agent.judgment_v12_daily_quotes import Provider
        formatter = object.__new__(Provider)
        formatter.provider, formatter.profile = 'deepseek', profile
        require(digest(formatter.payload(packet)) == case['payload_sha256'], 'payload_changed')
        return case

    def guard_campaign(self, campaign, phase, profile):
        self.authorize()
        case = self._validate_campaign(campaign, profile)
        with sqlite3.connect(Path(self.cfg['ledger_path']).as_uri() + '?mode=ro', uri=True) as db:
            self._grant(db)
            _, web, _ = self._partition(db)
            require(not any(r[6] == case['payload_sha256'] for r in web), 'case_already_requested')
            self._phase(db, phase, campaign)
        return case

    def _phase(self, db, phase, campaign):
        require(db.execute('SELECT s.campaign_id,s.session_id,s.provider,s.max_calls,p.slot,e.manifest_sha '
                           'FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) '
                           'JOIN execution_phases e USING(phase) WHERE p.phase=?', (phase,)).fetchone()
                == (self.cfg['campaign'], campaign['session'], 'deepseek', 1, 0, digest(campaign)), 'phase_binding')

    def register_campaign(self, store):
        """The only registration entry: explicit approval, same durable ledger."""
        self.authorize()
        require(store.budget.path.resolve() == Path(self.cfg['ledger_path']).resolve()
                and (store.budget.mode == 'fixture') == bool(self.cfg.get('fixture')), 'ledger_binding')
        with store.budget.transaction() as db:
            self._partition(db)
            db.execute('CREATE TABLE IF NOT EXISTS web_trial_grants(campaign_id TEXT PRIMARY KEY,config_path TEXT NOT NULL,'
                       'config_sha TEXT NOT NULL,approval_path TEXT NOT NULL,approval_sha TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS web_trial_packets(phase TEXT PRIMARY KEY,wrapper_json TEXT NOT NULL)')
            wanted = (self.cfg['campaign'], str(self.config_path), self.sha256, str(self.approval_path), file_hash(self.approval_path))
            old = db.execute('SELECT * FROM web_trial_grants WHERE campaign_id=?', (self.cfg['campaign'],)).fetchone()
            require(old is None or old == wanted, 'grant_changed')
            if old is None:
                require(not db.execute('SELECT 1 FROM autonomous_campaigns WHERE campaign_id=?', (self.cfg['campaign'],)).fetchone(), 'campaign_already_exists')
                db.execute('INSERT INTO web_trial_grants VALUES(?,?,?,?,?)', wanted)
                db.execute('INSERT INTO autonomous_campaigns VALUES(?,?,?,?)', (self.cfg['campaign'], self.sha256, 6, GROUP))
        return self.cfg['campaign']

    def register_packet(self, budget, phase, campaign, profile):
        """Freeze the actual wrapper after SessionStore.register_packet, before POST."""
        self.authorize()
        self._validate_campaign(campaign, profile)
        with budget.transaction() as db:
            self._grant(db)
            self._phase(db, phase, campaign)
            old = db.execute('SELECT wrapper_json FROM web_trial_packets WHERE phase=?', (phase,)).fetchone()
            body = canonical(campaign)
            require(old is None or old == (body,), 'wrapper_changed')
            if old is None:
                db.execute('INSERT INTO web_trial_packets VALUES(?,?)', (phase, body))

    def _web_phase(self, db, phase):
        table = db.execute("SELECT 1 FROM sqlite_master WHERE name='autonomous_phases'").fetchone()
        return bool(table and db.execute('SELECT 1 FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) '
                                        'WHERE p.phase=? AND s.campaign_id=?', (phase, self.cfg['campaign'])).fetchone())

    def install(self):
        """Install the four explicitly disclosed hooks; do not register anything."""
        global _ACTIVE
        self._validate_files()
        require(_ACTIVE is None or _ACTIVE.sha256 == self.sha256, 'different_protocol_already_installed')
        _ACTIVE = self

        def history(db):
            rows, web, _ = _ACTIVE._partition(db)
            excluded = {r[0] for r in web}
            return [r for r in rows if r[0] not in excluded]

        def guard(db, **kwargs):
            active = _ACTIVE
            if not active._web_phase(db, kwargs['phase']):
                return _ORIGINAL_GUARD(db, **kwargs)
            active._grant(db)
            _, web, _ = active._partition(db)
            case = active._case(payload_sha=kwargs['packet_sha'])
            require(kwargs['provider'] == 'deepseek' and len(web) < 6
                    and not any(r[6] == case['payload_sha256'] for r in web), 'case_already_requested')
            profile = read(active.cfg['profile_path'])
            require(kwargs['reserve_micro'] == reservation_micro(profile)
                    and sum(r[7] for r in web) + kwargs['reserve_micro'] <= active.cfg['caps']['web_cost_micro_cny'], 'web_reservation_cap')
            active._verify_web_request(db, kwargs['phase'], kwargs['request_key'], kwargs['packet_sha'], case)
            return {'absolute_caps': active.cfg['caps']['absolute']}

        def identity(db, attempt):
            row = db.execute('SELECT phase FROM execution_requests WHERE attempt_id=?', (attempt,)).fetchone()
            if row and _ACTIVE._web_phase(db, row[0]):
                _ACTIVE._grant(db)
                return _ACTIVE.cfg['expected_identity']
            return _ORIGINAL_IDENTITY(db, attempt)

        def session_guard(db, phase, provider):
            if not _ACTIVE._web_phase(db, phase):
                return _ORIGINAL_SESSION_GUARD(db, phase, provider)
            row = db.execute('SELECT s.provider,s.max_calls,s.deadline,c.cap_each,c.group_key,p.slot '
                             'FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) '
                             'JOIN autonomous_campaigns c USING(campaign_id) WHERE p.phase=?', (phase,)).fetchone()
            import time
            require(row and row[:2] == ('deepseek', 1) and provider == 'deepseek'
                    and row[2] > time.time() and row[3:] == (6, GROUP, 0), 'session_limits')

        formal.history_rows = history
        formal.guard = guard
        formal.identity_for_attempt = identity
        sessions.guard_session_reservation = session_guard
        return self

    def uninstall(self):
        global _ACTIVE
        formal.history_rows = _ORIGINAL_HISTORY
        formal.guard = _ORIGINAL_GUARD
        formal.identity_for_attempt = _ORIGINAL_IDENTITY
        sessions.guard_session_reservation = _ORIGINAL_SESSION_GUARD
        _ACTIVE = None

    def status(self):
        authorized = True
        try:
            self.authorize()
        except ExecutionStop as error:
            if str(error) != 'web_trial_specific_approval_required':
                raise
            authorized = False
        with sqlite3.connect(Path(self.cfg['ledger_path']).as_uri() + '?mode=ro', uri=True) as db:
            rows, web, route9 = self._partition(db)
        return {'status': 'authorized' if authorized else 'approval_pending', 'authorized': authorized,
                'protocol_sha256': self.sha256, 'global_calls': len(rows),
                'global_provider_counts': {p: sum(r[1] == p for r in rows) for p in ('deepseek', 'local')},
                'web_calls': len(web), 'web_remaining': 6 - len(web),
                'web_accounted_micro_cny': sum(r[7] for r in web),
                'route9_calls_after_baseline': len(route9), 'route9_remaining_call_cap': self.cfg['caps']['route9_remaining_calls'] - len(route9),
                'used_case_aliases': [self._case(payload_sha=r[6])['alias'] for r in web],
                'full_ledger_rows_sha256': digest(rows), 'web_rows_sha256': digest(web),
                'route9_rows_after_baseline_sha256': digest(route9),
                'partition_disclosure': 'All web calls remain in the shared ledger, global counts and money; only authenticated web calls are excluded from Route9 case statistics.'}
