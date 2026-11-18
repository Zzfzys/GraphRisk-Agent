"""Separately approved quarantine of ONE unknown web request, without retry.

The original six-case protocol, grant, source inventory and ledger are retained.
Only its history-reading query receives an explicit, authenticated projection;
all actual reservations, money, receipts and global SQL counts stay unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from graphrisk.agent.autonomous_budget import ExecutionStop
from graphrisk.agent.autonomous_providers import reservation_micro
from graphrisk.agent.upgrade_contract import digest, file_hash
import web_trial_protocol as original

VERSION = 'daily-web-trial-unknown-quarantine-v1'
_HISTORY_SQL = ('SELECT a.attempt_id,a.provider,a.state,a.reserved_micro_cny,r.request_key,r.phase,'
                'r.packet_sha,r.accounted,r.receipt_sha,r.receipt_path,r.result_code,r.identity,r.usage_json,r.integrity_stop '
                'FROM model_attempts a JOIN execution_requests r USING(attempt_id) ORDER BY a.rowid')


def require(ok, code):
    if not ok:
        raise ExecutionStop('web_trial_recovery_' + code)


class _QuarantineView:
    """Read projection for the one frozen history SQL; all other SQL is real."""
    def __init__(self, db, attempt):
        self.db, self.attempt = db, attempt
        self.projected_queries = 0

    def execute(self, sql, parameters=()):
        if sql == _HISTORY_SQL:
            require(not parameters, 'unexpected_history_parameters')
            self.projected_queries += 1
            sql = sql.replace(' ORDER BY a.rowid', ' WHERE a.attempt_id<>? ORDER BY a.rowid')
            return self.db.execute(sql, (self.attempt,))
        return self.db.execute(sql, parameters)


class RecoveryTrialProtocol(original.TrialProtocol):
    def __init__(self, config_path, approval_path=None, *, recovery_config_path, recovery_approval_path=None):
        self.recovery_config_path = Path(recovery_config_path).resolve()
        self.recovery_approval_path = Path(recovery_approval_path).resolve() if recovery_approval_path else None
        self.recovery_cfg = original.read(self.recovery_config_path)
        self.recovery_sha256 = file_hash(self.recovery_config_path)
        super().__init__(config_path, approval_path)

    def _validate_files(self):
        super()._validate_files()
        cfg = self.recovery_cfg
        require(file_hash(self.recovery_config_path) == self.recovery_sha256 and cfg['version'] == VERSION, 'addendum_changed')
        require(Path(cfg['original_config_path']).resolve() == self.config_path
                and cfg['original_config_sha256'] == self.sha256
                and self.approval_path is not None
                and Path(cfg['original_approval_path']).resolve() == self.approval_path
                and file_hash(self.approval_path) == cfg['original_approval_sha256'], 'original_binding_changed')
        require(cfg['caps'] == {'web_calls': 6, 'web_cost_micro_cny': 5_000_000, 'remaining_web_calls': 5,
                                'route9_remaining_calls': self.cfg['caps']['route9_remaining_calls']}, 'caps_changed')
        for path, sha in cfg['files'].items():
            require(Path(path).is_file() and file_hash(Path(path)) == sha, 'new_bound_file_changed')
        require(cfg['files'].get(str(Path(__file__).resolve())) == file_hash(Path(__file__)), 'recovery_source_not_bound')
        unknown = cfg['unknown']
        require(digest(unknown['row']) == unknown['row_sha256'] and len(unknown['row']) == 14, 'frozen_unknown_row')
        if not self.cfg.get('fixture'):
            require(self.sha256 == '1a55cd01397e47947408a93fb6d409828a287b8360defbfc9ad564ae072dc99d'
                    and unknown['attempt_id'] == '3bc78d591d334a97bc7cd12a033e25ca'
                    and unknown['accounted_micro_cny'] == 2_129_920, 'exact_live_quarantine_only')

    def authorize(self):
        original.TrialProtocol.authorize(self)
        require(self.recovery_approval_path is not None and self.recovery_approval_path.is_file(), 'specific_approval_required')
        note = original.read(self.recovery_approval_path)
        require(note.get('recovery_config_sha256') == self.recovery_sha256
                and note.get('unknown_attempt_id') == self.recovery_cfg['unknown']['attempt_id']
                and note.get('exact_unknown_quarantine_authorized') is True
                and note.get('max_remaining_web_requests') == 5
                and note.get('preserve_unknown') is True and note.get('no_h1_retry') is True
                and isinstance(note.get('question_item_id'), str) and bool(note['question_item_id'].strip())
                and isinstance(note.get('user_answer'), str) and bool(note['user_answer'].strip()), 'specific_approval_required')
        return note

    def _unknown(self, db, rows):
        """Verify exact old approval/grant/packet and unchanged unknown hold."""
        original.TrialProtocol.authorize(self)
        cfg = self.recovery_cfg
        unknown = cfg['unknown']
        found = [r for r in rows if r[0] == unknown['attempt_id']]
        require(len(found) == 1 and digest(found[0]) == unknown['row_sha256'], 'unknown_row_changed')
        row = found[0]
        require(rows[self.cfg['baseline']['count']] == row and list(row) == unknown['row']
                and row[1:3] == ('deepseek', 'unknown') and row[8:10] == (None, None)
                and row[10:] == ('provider_connection_unknown', None, None, 0)
                and row[3] == row[7] == unknown['accounted_micro_cny']
                and row[3] == reservation_micro(original.read(self.cfg['profile_path']))
                and row[4] == unknown['request_key'] and row[5] == unknown['phase']
                and row[6] == unknown['payload_sha256'], 'unknown_scope_changed')
        grant = db.execute('SELECT * FROM web_trial_grants WHERE campaign_id=?', (self.cfg['campaign'],)).fetchone()
        require(grant is not None and digest(grant) == cfg['original_grant_sha256']
                and grant == (self.cfg['campaign'], str(self.config_path), self.sha256,
                              str(self.approval_path), file_hash(self.approval_path)), 'original_grant_changed')
        bound = db.execute('SELECT s.campaign_id,c.group_key,s.provider,s.max_calls,p.slot '
                           'FROM autonomous_phases p JOIN autonomous_sessions s USING(session_id) '
                           'JOIN autonomous_campaigns c USING(campaign_id) WHERE p.phase=?', (row[5],)).fetchone()
        require(bound == (self.cfg['campaign'], original.GROUP, 'deepseek', 1, 0)
                and unknown['campaign'] == self.cfg['campaign'], 'unknown_campaign_changed')
        require(db.execute('SELECT manifest_sha,cap_each,group_key FROM autonomous_campaigns WHERE campaign_id=?',
                           (self.cfg['campaign'],)).fetchone() == (self.sha256, 6, original.GROUP), 'original_campaign_changed')
        case = self._case(payload_sha=row[6])
        require(case['alias'] == unknown['case_alias'] and case['packet_sha256'] == unknown['packet_sha256']
                and case['profile_sha256'] == unknown['profile_sha256'], 'quarantined_case_changed')
        self._verify_web_request(db, row[5], row[4], row[6], case)
        return row

    def _partition(self, db, *, validate_receipts=True):
        self._validate_files()
        all_rows = original._ORIGINAL_HISTORY(db)
        unknown = self._unknown(db, all_rows)
        view = _QuarantineView(db, unknown[0])
        projected, web, route9 = super()._partition(view, validate_receipts=validate_receipts)
        require(view.projected_queries == 1 and len(all_rows) == len(projected) + 1
                and projected == [r for r in all_rows if r[0] != unknown[0]], 'projection_not_exact')
        used = {r[0] for r in web} | {unknown[0]}
        web = [r for r in all_rows if r[0] in used]
        require(len(web) <= 6 and len({r[6] for r in web}) == len(web), 'quarantined_case_must_not_retry')
        require(sum(r[7] for r in web) <= 5_000_000, 'web_cost_including_hold')
        require(len(all_rows) <= original.ABSOLUTE['total']
                and all(sum(r[1] == p for r in all_rows) <= original.ABSOLUTE[p] for p in ('deepseek', 'local')), 'global_attempt_cap')
        return all_rows, web, route9

    def install(self):
        super().install()
        installed_guard = original.formal.guard
        installed_identity = original.formal.identity_for_attempt

        def guard(db, **kwargs):
            # Read-only status can inspect the isolation; every new reservation
            # including a Route9 continuation requires the separate amendment.
            self.authorize()
            return installed_guard(db, **kwargs)

        def identity(db, attempt):
            require(attempt != self.recovery_cfg['unknown']['attempt_id'], 'quarantined_attempt_must_remain_unknown')
            return installed_identity(db, attempt)

        original.formal.guard = guard
        original.formal.identity_for_attempt = identity
        return self

    def status(self):
        authorized = True
        try:
            self.authorize()
        except ExecutionStop as error:
            if str(error) != 'web_trial_recovery_specific_approval_required':
                raise
            authorized = False
        with sqlite3.connect(Path(self.cfg['ledger_path']).as_uri() + '?mode=ro', uri=True) as db:
            rows, web, route9 = self._partition(db)
        unknown = self.recovery_cfg['unknown']
        return {'status': 'authorized_recovery' if authorized else 'paused_recovery_approval_pending',
                'authorized': authorized, 'protocol_sha256': self.sha256, 'recovery_sha256': self.recovery_sha256,
                'global_calls': len(rows), 'global_provider_counts': {p: sum(r[1] == p for r in rows) for p in ('deepseek', 'local')},
                'web_calls': len(web), 'web_remaining': 6 - len(web), 'web_accounted_micro_cny': sum(r[7] for r in web),
                'route9_calls_after_baseline': len(route9),
                'route9_remaining_call_cap': self.cfg['caps']['route9_remaining_calls'] - len(route9),
                'used_case_aliases': [self._case(payload_sha=r[6])['alias'] for r in web],
                'quarantined_unknown_attempt_id': unknown['attempt_id'], 'quarantined_unknown_state': 'unknown',
                'quarantined_hold_micro_cny': unknown['accounted_micro_cny'], 'quarantined_case_retry_allowed': False,
                'full_ledger_rows_sha256': digest(rows), 'web_rows_sha256': digest(web),
                'route9_rows_after_baseline_sha256': digest(route9),
                'partition_disclosure': 'The exact unknown remains charged at its full reservation and consumes one of six calls. It is never retried or settled; all calls remain in the real shared ledger.'}
