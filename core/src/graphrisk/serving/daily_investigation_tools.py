"""Measured investigations of an immutable day-end arrival population.

The primary score is a day-end signal. Every history/relationship measurement
below instead uses strictly earlier event seconds within that sealed population.
No outcome labels, inferred calendar dates or GNN predictions are read.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from graphrisk.agent.autonomous_evidence import build_evidence
from graphrisk.agent.autonomous_tools import ArtifactGuard, ReadOnlyToolExecutor, ToolBoundaryError
from graphrisk.agent.upgrade_contract import digest, file_hash
from graphrisk.investigation.contracts import (
    FindRiskyNeighborsResult, GetRelatedEntitiesResult, GetRiskScoreResult,
    GetTransactionFeaturesResult, GetTransactionResult,
)
from graphrisk.investigation.relation_tools import ENTITY_TYPES


GRAPH_RELATION_VERSION = 'relation-only-daily-v1'


def _canonical_source(db):
    """Reproduce IEEE canonical entity tokens, never champion UID semantics."""
    def token(name):
        return f'NULLIF(lower(trim("{name}")), \'\')'

    card = "'card-' || " + " || ':' || ".join(
        token('card1') if i == 1 else f"coalesce({token('card'+str(i))}, 'na')"
        for i in range(1, 7))
    device = f"coalesce({token('DeviceInfo')}, {token('DeviceType')})"
    mail = f"coalesce({token('P_emaildomain')}, {token('R_emaildomain')})"
    address = f"'address-' || {token('addr1')} || ':' || coalesce({token('addr2')}, 'na')"
    expressions = [
        'transaction_id', 'source_transaction_dt_seconds',
        '"TransactionAmt" AS transaction_amount', 'upper(trim("ProductCD")) AS product_code',
        f"coalesce({card}, 'unknown-card-' || transaction_id) AS card_id",
        f"coalesce('device-' || {device}, 'unknown-device-' || transaction_id) AS device_id",
        f"coalesce({mail}, 'unknown-email-' || transaction_id) AS email_domain",
        f"coalesce({address}, 'unknown-address-' || transaction_id) AS address_id",
    ]
    db.execute('CREATE TEMP VIEW investigation_entities AS SELECT ' + ','.join(expressions) +
               ' FROM daily_source')
    # Matches data.contracts.build_identity_key, including lower/trim. A UID
    # assembled for champion model aggregates is deliberately never substituted.
    db.execute("CREATE TEMP VIEW investigation_source AS SELECT *, "
               "sha256(lower(trim(card_id)) || '|' || lower(trim(address_id)) || '|' || "
               "lower(trim(email_domain))) AS identity_key FROM investigation_entities")


def _row(cursor):
    values = cursor.fetchall()
    if len(values) != 1:
        raise ValueError('sealed investigation target missing or duplicate')
    return dict(zip((entry[0] for entry in cursor.description), values[0], strict=True))


class DailyInvestigationTools:
    """One bounded disk scan registers six existing read-only tool contracts."""

    def __init__(self, store, *, retriever, registry, source_guard=lambda: None):
        self.store, self.retriever, self.registry = store, retriever, registry
        self.source_guard = source_guard
        paths = [Path(__file__), Path(__file__).with_name('daily_batch_store.py')]
        self.implementation = {path.name: file_hash(path) for path in paths}
        bindings = {path: self.implementation[path.name] for path in paths}
        # Local TF-IDF retrieval has no outbound provider or embedding call.
        if getattr(retriever, 'provider', None) is not None:
            raise ValueError('daily investigation requires provider-free local policy retrieval')
        bindings[retriever.index_path] = file_hash(retriever.index_path)
        self._guard = ArtifactGuard(bindings)

    def check(self):
        self.source_guard()
        self._guard()
        self.store.candidate._check_files()

    def revision(self, row):
        self.check()
        if row.get('source_kind') != 'arrival_journal':
            raise ValueError('daily investigation requires a sealed arrival journal')
        return digest({'version': 'sealed-daily-investigation-v1',
            'sealed_content_sha256': row['sealed_content_sha256'],
            'source_snapshot_sha256': row['source_snapshot_sha256'],
            'history_sha256': self.store.binding['history_sha256'],
            'history_cutoff_second': self.store.history_cutoff_second,
            'implementations': self.implementation,
            'policy_index_version': self.registry.index_version,
            'policy_annotation_version': self.registry.annotation_version})

    def _measure(self, scope, row):
        with self.store.investigation_snapshot(row['relative_day']) as (batch, db):
            sealed = next((value for value in batch['results']
                           if value['transaction_id'] == scope.transaction_id), None)
            if sealed is None or digest({**{key: value for key, value in batch.items()
                    if key != 'results'}, **sealed}) != digest(row):
                raise ToolBoundaryError('daily_scoring_record_changed')
            _canonical_source(db)
            target = _row(db.execute('SELECT * FROM investigation_source WHERE transaction_id=?',
                                     [scope.transaction_id]))
            fields = ('transaction_amount', 'product_code', 'card_id', 'device_id',
                      'email_domain', 'address_id')
            if (target['source_transaction_dt_seconds'] != scope.target_event_second or
                    any(target[name] != row[name] for name in fields)):
                raise ToolBoundaryError('daily_canonical_entities_changed')
            stamp = scope.target_event_second
            counts = db.execute('''SELECT
                count(*) FILTER (WHERE source_transaction_dt_seconds>=?),
                count(*) FILTER (WHERE source_transaction_dt_seconds>=?), count(*),
                avg(transaction_amount) FILTER (WHERE source_transaction_dt_seconds>=?),
                stddev_pop(transaction_amount) FILTER (WHERE source_transaction_dt_seconds>=?)
                FROM investigation_source WHERE identity_key=?
                AND source_transaction_dt_seconds>=? AND source_transaction_dt_seconds<?''',
                [stamp-3600, stamp-86400, stamp-86400, stamp-86400,
                 target['identity_key'], stamp-604800, stamp]).fetchone()
            features = dict(transaction_id=scope.transaction_id, tx_count_last_1h=counts[0],
                tx_count_last_24h=counts[1], tx_count_last_7d=counts[2],
                amount_mean_last_24h=counts[3], amount_std_last_24h=counts[4],
                amount_deviation_last_24h=None if counts[3] is None else target['transaction_amount']-counts[3])
            active = {field: target[field] for field, _ in ENTITY_TYPES
                      if not target[field].lower().startswith('unknown-')}
            matches, total = [], 0
            if active:
                condition = ' OR '.join('"'+field+'"=?' for field in active)
                where = 'source_transaction_dt_seconds<? AND ('+condition+')'
                args = [stamp, *active.values()]
                total = db.execute('SELECT count(*) FROM investigation_source WHERE '+where, args).fetchone()[0]
                cursor = db.execute('SELECT transaction_id,source_transaction_dt_seconds,' +
                    ','.join(field for field, _ in ENTITY_TYPES) + ' FROM investigation_source WHERE '+
                    where+' ORDER BY source_transaction_dt_seconds DESC,transaction_id ASC LIMIT 50', args)
                names = [entry[0] for entry in cursor.description]
                for values in cursor.fetchall():
                    candidate = dict(zip(names, values, strict=True))
                    matches.append(dict(transaction_id=candidate['transaction_id'],
                        seconds_before_target=stamp-candidate['source_transaction_dt_seconds'],
                        shared_entities=[dict(entity_type=kind, entity_id=active[field])
                            for field, kind in ENTITY_TYPES if field in active and candidate[field] == active[field]]))
            transaction = {name: target[name] for name in ('transaction_id',
                'source_transaction_dt_seconds', *fields)}
        return transaction, features, matches, total

    def register(self, scope, row, *, prefetch=True):
        if type(prefetch) is not bool:
            raise ToolBoundaryError('invalid_daily_tool_prefetch')
        if (scope.investigation_revision != self.revision(row) or
                scope.policy_index_version != self.registry.index_version or
                scope.graph_model_version != GRAPH_RELATION_VERSION):
            raise ToolBoundaryError('daily_investigation_binding_mismatch')
        transaction, features, matches, total = self._measure(scope, row)

        def result(cls, data, refs):
            return cls(status='ok', transaction_id=scope.transaction_id,
                dataset_source=scope.dataset_source, data=data,
                evidence=[dict(source_kind=kind, record_id=identifier,
                    dataset_source=scope.dataset_source) for kind, identifier in refs])

        def related(request):
            rows = deepcopy(matches[:request['limit']])
            return result(GetRelatedEntitiesResult, dict(transaction_id=scope.transaction_id,
                matches=rows, total_matches=total, truncated=total>len(rows)),
                [('entity_link', item['transaction_id']) for item in rows])

        def graph(request):
            rows = [{**deepcopy(item), 'graph_risk_score': None, 'evidence_role': 'relation_only',
                'score_unavailable_reason': 'outside_independent_score_scope'}
                for item in matches[:request['limit']]]
            return result(FindRiskyNeighborsResult, dict(transaction_id=scope.transaction_id,
                neighbors=rows, graph_model_version=GRAPH_RELATION_VERSION,
                score_role='relation_only', target_graph_risk_score=None),
                [('relation_snapshot', GRAPH_RELATION_VERSION),
                 *[('entity_link', item['transaction_id']) for item in rows]])

        def guard():
            self.check()
            current = self.store.get_transaction(row['relative_day'], scope.transaction_id)
            if digest(current) != digest(row):
                raise ToolBoundaryError('daily_scoring_record_changed')

        executor = ReadOnlyToolExecutor(scope, {
            'get_transaction': lambda request: result(GetTransactionResult, deepcopy(transaction),
                [('canonical_transaction', scope.transaction_id)]),
            'get_transaction_features': lambda request: result(GetTransactionFeaturesResult, deepcopy(features),
                [('historical_features', scope.transaction_id)]),
            'get_risk_score': lambda request: result(GetRiskScoreResult,
                dict(transaction_id=scope.transaction_id, risk_score=row['score'], risk_level=row['risk_level'],
                     model_name='xgboost', model_version=scope.primary_model_version),
                [('model_artifact', scope.primary_model_version)]),
            'get_related_entities': related, 'find_risky_neighbors': graph,
            'retrieve_policy': lambda request: self.retriever.retrieve_policy(
                request['query'], request['policy_as_of'], request['top_k']),
        }, guard)
        if prefetch:
            for name in ('get_transaction', 'get_transaction_features', 'get_risk_score',
                         'get_related_entities', 'find_risky_neighbors'):
                executor.execute(name, {})
            executor.execute('retrieve_policy', {'focus': 'manual_review', 'top_k': 6})
        bundle = build_evidence(scope, executor.observations, self.registry)
        return executor, executor.observations, bundle
