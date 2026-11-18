"""Bridge conversations to the real route3 engine and shared attempt ledger."""
from pathlib import Path
import json

from graphrisk.agent.autonomous_loop import BoundedInvestigation, latest_observations, observation_rows, restore_observations
from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.upgrade_contract import canonical, digest
from graphrisk.serving.conversations import BoundCase, ConversationError


class BoundedConversationBackend:
    """Trusted registration only; public callers cannot supply scope or evidence.

    entries are server-created scopes, source guards, registry and observations.
    factory(provider,...)/tool_batch are the existing budgeted model and real
    read-only tool adapters. No generic chat client or user-selected path exists.
    """
    def __init__(self, *, entries, store, campaign, provider, profile, limits,
                 directory, provider_factory, tool_batch, allow_investigation=True):
        self.entries = {entry['scope'].case_alias: entry for entry in entries}
        self.store, self.campaign, self.provider = store, campaign, provider
        self.profile, self.limits = profile, limits
        self.directory = Path(directory)
        self.factory, self.batch = provider_factory, tool_batch
        self.allow_investigation = allow_investigation

    def authorize(self):
        if not self.allow_investigation:
            raise ConversationError('investigation_generation_disabled')

    def check(self, alias, binding):
        entry = self.entries.get(alias)
        if entry is None or entry['scope'].binding_sha256 != binding:
            raise ConversationError('evidence_binding_changed')
        try:
            entry['guard']()
        except Exception:
            raise ConversationError('source_version_changed') from None

    def resolve(self, identifier):
        found = [e for e in self.entries.values() if e['scope'].transaction_id == identifier]
        if len(found) != 1:
            raise ConversationError('transaction_outside_registered_cases')
        entry = found[0]
        scope = entry['scope']
        self.check(scope.case_alias, scope.binding_sha256)
        bundle = build_evidence(scope, latest_observations(entry['observations']), entry['registry'])
        return BoundCase(scope.case_alias, scope.binding_sha256, bundle)

    def model_band(self, alias, binding, evidence):
        """Compute the optional UI projection from server-owned scope and guard."""
        from graphrisk.serving.model_risk_band import model_risk_band
        self.check(alias, binding)
        entry = self.entries[alias]
        return model_risk_band(entry['scope'], evidence, getattr(entry['guard'], '_bindings', ()))

    def _observations(self, entry, version):
        initial = entry['observations']
        scope = entry['scope']
        if build_evidence(scope, latest_observations(initial), entry['registry']).evidence_version == version:
            return initial
        # Recover only a bound content-addressed checkpoint referenced by the
        # original shared ledger, never a client-provided file or report text.
        with self.store.budget.transaction() as db:
            rows = db.execute('SELECT session_id FROM autonomous_sessions WHERE campaign_id=?', (self.campaign,)).fetchall()
        for (identifier,) in rows:
            state = self.store.restore(identifier, self.directory/identifier/'checkpoints')
            if not state or state['scope_sha256'] != scope.binding_sha256:
                continue
            observations = restore_observations(scope, state['observations'])
            if build_evidence(scope, latest_observations(observations), entry['registry']).evidence_version == version:
                return observations
        raise ConversationError('evidence_checkpoint_missing')

    def investigate(self, alias, binding, turn_id, task, evidence_version):
        self.authorize()
        self.check(alias, binding)
        entry = self.entries[alias]
        observations = self._observations(entry, evidence_version)
        manifest = {'conversation_protocol': 'conversation-v1', 'scope': binding,
                    'task': task, 'parent_evidence': evidence_version, 'turn_id': turn_id,
                    'profile_sha': digest(self.profile), 'limits_sha': digest(self.limits)}
        deadline = self.store.open(turn_id, self.campaign, manifest, self.provider, calls=5, seconds=600)
        engine = BoundedInvestigation(scope=entry['scope'], registry=entry['registry'],
            profile=self.profile, limits=self.limits, store=self.store, session=turn_id,
            directory=self.directory/turn_id, deadline=deadline,
            tool_batch=lambda rows, calls: self.batch(entry, rows, calls, deadline),
            provider_factory=self.factory, source_guard=entry['guard'],
            task=task, conversation_observations=observations, parent_evidence_version=evidence_version)
        state = engine.run()
        return {'evidence': engine.bundle(), 'report': state['report'],
                'model_calls': self.store.used(turn_id), 'tool_calls': state['tool_requests'],
                'status': state['status']}
