"""Six explicitly approved day-end cases, served through the existing report UI.

Preparing materials and starting the server never sends a model request.  Only
the report task can reserve one request, through the separately bound protocol.
"""
from __future__ import annotations

from argparse import ArgumentParser
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import threading
import time

from graphrisk.agent import judgment_v12_daily_quotes as quotes
from graphrisk.agent.autonomous_budget import ExecutionStop, InvestigationBudget
from graphrisk.agent.autonomous_evidence import EvidenceBundle, build_evidence
from graphrisk.agent.autonomous_loop import observation_rows, restore_observations
from graphrisk.agent.autonomous_policy import PolicyRegistry
from graphrisk.agent.autonomous_session import SessionStore
from graphrisk.agent.autonomous_tools import DailyInvestigationScope
from graphrisk.agent.policy_retrieval import load_policy_index
from graphrisk.agent.report_generation_profile import quality_report_profile
from graphrisk.agent.upgrade_contract import canonical, digest, file_hash, load_contract
from graphrisk.serving.conversation_backend import BoundedConversationBackend
from graphrisk.serving.conversation_store import MemoryConversationStore
from graphrisk.serving.conversations import BoundCase, ConversationError, ConversationService
from graphrisk.serving.daily_conversation_backend import DailyConversationBackend
from graphrisk.serving.daily_narrative import configure_daily_narrative, prepare_daily_narrative
from graphrisk.serving.daily_risk_service import RoutedConversationService


SOURCE = Path(r'C:\Users\12138\.codex\worktrees\71df\GraphRisk-Agent')
RUNTIME = Path(r'E:\GraphRisk-Agent')
TRIAL = RUNTIME / 'artifacts/agent-trial-20261115-2u0xpeep'
ROOT = RUNTIME / 'reports/generated/agent-investigation-upgrade/route5/report-v3/20261115-web-live-six-v1'
PROFILE_SOURCE = RUNTIME / 'reports/generated/agent-investigation-upgrade/route9/20261115-full-case-execution-v1/profile.json'
CASES = (('H1', '3558253'), ('H2', '3558246'), ('M1', '3558671'),
         ('M2', '3557515'), ('L1', '3559456'), ('L2', '3557768'))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_once(path, value):
    path = Path(path)
    text = canonical(value)
    if path.exists():
        if path.read_text(encoding='utf-8') != text:
            raise ValueError('prepared_material_already_exists_with_different_content')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return file_hash(path)


def report_payload(packet, profile):
    """Use the real provider's pure payload builder without constructing a client."""
    provider = object.__new__(quotes.Provider)
    provider.provider, provider.profile = 'deepseek', profile
    return provider.payload(packet)


def prepare(root=ROOT, *, runtime=RUNTIME, batch_db=TRIAL / 'arrivals.sqlite',
            profile_source=PROFILE_SOURCE):
    from serve_champion_daily import build_new_services

    root, runtime, batch_db = Path(root).resolve(), Path(runtime).resolve(), Path(batch_db).resolve()
    profile = quality_report_profile(read(profile_source))
    profile_path = root / 'profile.json'
    files = {str(profile_path): write_once(profile_path, profile)}
    risk, routed = build_new_services(runtime, db_path=batch_db,
        history_cutoff_second=15119999, clock=lambda: 15206400,
        policy_as_of=date(2026, 11, 6))
    backend = routed.daily.backend
    selected = []
    for review_case, identifier in CASES:
        bound = backend.resolve(identifier)
        entry = backend.entries[bound.alias]
        packet = prepare_daily_narrative(entry['scope'], entry['observations'], entry['registry'], profile)
        packet['logical_key'] = '0'
        payload = report_payload(packet, profile)
        source = dict(scope=entry['scope'].model_dump(mode='json'),
            evidence=entry['evidence'].model_dump(mode='json'),
            observations=observation_rows(entry['observations']))
        paths = {kind: root / 'cases' / (review_case + suffix) for kind, suffix in (
            ('source', '-source-private.json'), ('packet', '-packet-private.json'),
            ('payload', '-payload.json'))}
        for kind, value in (('source', source), ('packet', packet), ('payload', payload)):
            files[str(paths[kind])] = write_once(paths[kind], value)
        selected.append(dict(review_case=review_case, alias=bound.alias, transaction_id=identifier,
            risk_level=bound.evidence.scoring_context.risk_level,
            scope_sha256=bound.binding, evidence_version=bound.evidence.evidence_version,
            profile_sha256=digest(profile),
            **{kind + '_path': str(path) for kind, path in paths.items()},
            **{kind + '_sha256': files[str(path)] for kind, path in paths.items()}))
    day = risk.get_day(175)
    original_count = len(day['results'])
    identifiers = {value for _, value in CASES}
    day['results'] = [row for row in day['results'] if str(row['transaction_id']) in identifiers]
    if len(day['results']) != 6:
        raise ValueError('prepared_six_not_in_one_sealed_day')
    day['transaction_count'] = original_count
    day['trial_case_count'] = 6
    day_path = root / 'day-view-private.json'
    files[str(day_path)] = write_once(day_path, day)
    index_path = runtime / 'reports/generated/agent-investigation-upgrade/route1/20261019-native-v2/lexical-policy-index.json'
    annotations = SOURCE / 'configs/agent_investigation_policy_requirements.json'
    for path in (batch_db, index_path, annotations):
        files[str(path)] = file_hash(path)
    value = dict(version='daily-web-trial-prepared-v1', runtime=str(runtime),
        source_root=str(SOURCE), profile_path=str(profile_path), profile_sha256=digest(profile),
        policy_as_of='2026-11-06', db_path=str(batch_db),
        available_through_second=15206400, history_cutoff_second=15119999,
        index_path=str(index_path), annotations_path=str(annotations),
        day_view_path=str(day_path), cases=selected, files=files)
    manifest = root / 'PREPARED-MANIFEST-PRIVATE.json'
    write_once(manifest, value)
    return {'status': 'prepared_not_sent', 'cases': 6, 'model_calls': 0,
            'manifest_path': str(manifest), 'manifest_sha256': file_hash(manifest)}


class FrozenRiskService:
    """Read-only six-case UI projection; the original batch size stays visible."""
    def __init__(self, day, guard):
        self.day, self.guard = deepcopy(day), guard

    def list_batches(self):
        self.guard()
        return [{key: deepcopy(value) for key, value in self.day.items() if key != 'results'}]

    def get_day(self, day):
        self.guard()
        if day != self.day['relative_day']:
            raise ValueError('transaction_outside_trial')
        return deepcopy(self.day)

    def get_transaction(self, day, identifier, mode='daily'):
        if mode != 'daily':
            raise ValueError('trial_daily_model_only')
        value = self.get_day(day)
        rows = value.pop('results')
        row = next((row for row in rows if str(row['transaction_id']) == identifier), None)
        if row is None:
            raise ValueError('transaction_outside_trial')
        return {**value, **row}


class FrozenDailyBackend(DailyConversationBackend):
    """Restore only prepared server-owned scopes, never user-supplied evidence."""
    def __init__(self, entries, *, policy_as_of, guard, narrative_backend):
        super().__init__(None, policy_as_of=policy_as_of, source_guard=guard,
                         narrative_backend=narrative_backend)
        self.entries = {entry['scope'].case_alias: entry for entry in entries}
        self.by_transaction = {entry['scope'].transaction_id: entry for entry in entries}
        self.report_lock = threading.Lock()

    def resolve(self, identifier, *, batch_id=None):
        entry = self.by_transaction.get(identifier)
        if entry is None:
            raise ConversationError('transaction_outside_registered_cases')
        scope = entry['scope']
        if batch_id is not None and batch_id != scope.scoring_context.batch_id:
            raise ConversationError('batch_selection_mismatch')
        self.check(scope.case_alias, scope.binding_sha256)
        return BoundCase(scope.case_alias, scope.binding_sha256, entry['evidence'])

    def check(self, alias, binding):
        entry = self.entries.get(alias)
        if entry is None or entry['scope'].binding_sha256 != binding:
            raise ConversationError('evidence_binding_changed')
        try:
            self.source_guard()
        except (ValueError, OSError):
            raise ConversationError('source_version_changed') from None

    def run_report(self, alias, binding, turn_id, evidence_version):
        # ponytail: serialize this six-case desktop trial; per-account queues if
        # it becomes a multi-user service.  No second concurrent POST can start.
        with self.report_lock:
            self.narrative_backend.current_entry = self.entries[alias]
            return super().run_report(alias, binding, turn_id, evidence_version)


class LiveDelegate(BoundedConversationBackend):
    def __init__(self, protocol, profile, *, provider_transport=None):
        super().__init__(entries=[], store=None, campaign=protocol.cfg['campaign'],
            provider='deepseek', profile=profile, limits={},
            directory=Path(protocol.config_path).parent / 'sessions',
            provider_factory=None, tool_batch=None)
        self.protocol, self.provider_transport = protocol, provider_transport
        self.init_lock = threading.Lock()
        configure_daily_narrative(self, narrative_factory=self.make_provider, allow_generation=True)

    def authorize(self):
        try:
            self.protocol.authorize()
            with self.init_lock:
                if self.store is None:
                    cfg = self.protocol.cfg
                    contract = load_contract(Path(cfg['source_root']) / 'configs/agent_investigation_experiment.json')
                    budget = InvestigationBudget(Path(cfg['ledger_path']), contract,
                        mode='fixture' if self.provider_transport is not None else 'live')
                    self.store = SessionStore(budget)
                self.protocol.register_campaign(self.store)
        except (ExecutionStop, OSError) as error:
            raise ConversationError(str(error) if isinstance(error, ExecutionStop) else
                                    'web_trial_configuration_unavailable') from None

    def make_provider(self, campaign, phase, deadline):
        self.protocol.register_packet(self.store.budget, phase, campaign, self.report_profile)
        self.protocol.guard_campaign(campaign, phase, self.report_profile)
        return quotes.Provider('deepseek', profile=self.report_profile, budget=self.store.budget,
            campaign=campaign, campaign_sha=digest(campaign), phase=phase,
            output=Path(self.protocol.config_path).parent / 'receipts',
            runtime=Path(self.protocol.cfg['runtime']), live=True,
            transport=self.provider_transport, remaining=lambda: deadline - time.time())


def public_status(protocol):
    try:
        value = protocol.status()
        approved = bool(value.get('approved', value.get('authorized', False)))
        used = value.get('used_calls', value.get('web_calls', value.get('used', 0)))
        if type(used) is not int:
            used = 0
        return dict(approved=approved, used_calls=used, max_calls=6,
            state='pending_approval' if not approved else 'stopped' if used >= 6 or value.get('stopped') else 'ready')
    except (ExecutionStop, OSError, ValueError):
        return dict(approved=False, used_calls=0, max_calls=6,
                    state='stopped', error_code='web_trial_configuration_unavailable')


def make_app(config_path, approval_path=None, *, protocol=None, provider_transport=None):
    from api.main import create_app
    if protocol is None:
        from web_trial_protocol import TrialProtocol
        protocol = TrialProtocol(config_path, approval_path)
    cfg = protocol.cfg
    manifest_path = Path(cfg['prepared_manifest_path'])
    if file_hash(manifest_path) != cfg['prepared_manifest_sha256']:
        raise ExecutionStop('web_trial_material_manifest_changed')
    manifest = read(manifest_path)

    def guard():
        for name, sha in manifest['files'].items():
            if file_hash(Path(name)) != sha:
                raise ExecutionStop('web_trial_prepared_material_changed')

    guard()
    profile = read(cfg['profile_path'])
    if digest(profile) != cfg['profile_sha256']:
        raise ExecutionStop('web_trial_profile_changed')
    registry = PolicyRegistry.from_file(load_policy_index(Path(manifest['index_path'])),
                                        Path(manifest['annotations_path']))
    entries = []
    for item in manifest['cases']:
        raw = read(item['source_path'])
        scope = DailyInvestigationScope.model_validate_json(canonical(raw['scope']), strict=True)
        observations = restore_observations(scope, raw['observations'])
        bundle = EvidenceBundle.model_validate_json(canonical(raw['evidence']), strict=True)
        if (scope.case_alias != item['alias'] or scope.transaction_id != item['transaction_id']
                or scope.binding_sha256 != item['scope_sha256']
                or build_evidence(scope, observations, registry) != bundle):
            raise ExecutionStop('web_trial_prepared_evidence_changed')
        entries.append(dict(scope=scope, observations=observations, evidence=bundle,
                            registry=registry, guard=guard))
    protocol.install()
    delegate = LiveDelegate(protocol, profile, provider_transport=provider_transport)
    backend = FrozenDailyBackend(entries, policy_as_of=date.fromisoformat(manifest['policy_as_of']),
                                 guard=guard, narrative_backend=delegate)
    risk = FrozenRiskService(read(manifest['day_view_path']), guard)
    conversations = RoutedConversationService(ConversationService(MemoryConversationStore(), backend))
    app = create_app(daily_risk_service_factory=lambda: risk,
                     conversation_service_factory=lambda: conversations)

    @app.get('/api/v1/live-report-status')
    def live_report_status():
        return public_status(protocol)

    return app


def main():
    parser = ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--serve', action='store_true')
    parser.add_argument('--runtime', type=Path, default=RUNTIME)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--batch-db', type=Path, default=TRIAL / 'arrivals.sqlite')
    parser.add_argument('--profile-source', type=Path, default=PROFILE_SOURCE)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--approval', type=Path)
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    if args.prepare:
        print(canonical(prepare(args.root, runtime=args.runtime, batch_db=args.batch_db,
                                profile_source=args.profile_source)))
        return
    if args.config is None:
        parser.error('--serve requires --config')
    import uvicorn
    uvicorn.run(make_app(args.config, args.approval), host='127.0.0.1',
                port=args.port, access_log=False)


if __name__ == '__main__':
    main()
