import json
from pathlib import Path
from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import pytest

from graphrisk.graph.coverage_data import NewTransaction
from graphrisk.models.champion_daily_features import (
    ASSOCIATION_FIELDS, KEYS, UNIQUE_FIELDS, build_daily_features, normalized_raw, raw_fields,
)
from graphrisk.serving.daily_batch_store import DailyBatchStore


def contract():
    mapping = {f'freq_{name}': f'batch_freq_{name}' for name in KEYS}
    mapping.update({f'{name}_{field}_{moment}': f'batch_{name}_{field}_{moment}'
        for name in ('card1', 'card1_addr1', 'card1_addr1_email')
        for field in ('TransactionAmt', 'D9', 'D11') for moment in ('mean', 'std')})
    extra = ['batch_uid_count', 'batch_uid_frequency',
        *[f'batch_uid_{field}_{moment}' for field in ('TransactionAmt', 'D4', 'D9', 'D10', 'D15')
          for moment in ('mean', 'std')],
        *[f'batch_uid_C{i}_mean' for i in range(1, 15) if i != 3], 'batch_uid_C14_std',
        *[f'batch_uid_M{i}_T_rate' for i in (1, 2, 3, 5, 6, 7, 8, 9)],
        *[f'batch_uid_M4_{name}_rate' for name in ('M0', 'M1', 'M2')],
        *[f'batch_uid_{field}_distinct' for field in UNIQUE_FIELDS]]
    return {'raw': ['TransactionAmt', 'ProductCD', 'card1', 'addr1', 'P_emaildomain', 'D1', 'D11'],
        'categorical': ['ProductCD', 'card1', 'addr1', 'P_emaildomain'],
        'base_mapping': {'day': mapping}, 'uid_extra': {'day': extra}}


def event(key, second, amount=10, **changes):
    raw = {name: None for name in raw_fields(contract())}
    raw.update(TransactionAmt=float(amount), ProductCD='W', card1='1', addr1='2',
               P_emaildomain='mail.test', D1=float(second // 86400),
               M1='T', M4='M0', C14=2, D11=2)
    raw.update(changes)
    return NewTransaction(transaction_id=key, source_transaction_dt_seconds=second,
        transaction_amount=float(amount), card_id='card-1:na:na:na:na:na',
        device_id='unknown-device-'+key, email_domain='mail.test',
        address_id='address-2:na', product_code='W', raw_features=raw)


def candidate(tmp_path):
    implementation = tmp_path/'source.parquet'
    implementation.touch()
    class Candidate:
        contract = contract()
        policy = {'selection_cutoff': 0}
        hashes = dict.fromkeys(['model', 'protocol', 'maps', 'policy', 'source'], 'a'*64)
        paths = {'source': implementation}

        def _check_files(self): pass

        def score_frames(self, day, source, derived, **kwargs):
            assert len(source) == len(derived)
            self.last_source, self.last_derived = source, derived
            return {'relative_day': day, 'batch_end_second': (day+1)*86400,
                'available_through_second': kwargs['available_through_second'],
                'temporal_scope': 'relative_day_end', 'model_version': 'test-candidate',
                'model_sha256': self.hashes['model'], 'feature_protocol_sha256': self.hashes['protocol'],
                'category_maps_sha256': self.hashes['maps'], 'policy_sha256': self.hashes['policy'],
                'source_sha256': kwargs['source_sha256'], 'derived_sha256': kwargs['derived_sha256'],
                'calibrated_probability': False,
                'results': [{'transaction_id': row.transaction_id, 'event_second': int(row.source_transaction_dt_seconds),
                    'score': .25, 'risk_level': 'low'} for row in source.itertuples()]}
    return Candidate()


def test_same_day_peers_future_exclusion_and_unknown_uid(tmp_path):
    service = candidate(tmp_path)
    store = DailyBatchStore(tmp_path/'journal.sqlite', service, history_cutoff_second=0,
                           scratch=tmp_path/'work')
    for row in [event('a', 86401, 10), event('b', 86401, 30),
                event('missing', 86402, 50, D1=None), event('future', 172801, 999)]:
        store.ingest(row, received_second=200000)
    result = store.seal(1, available_through_second=200000)
    assert len(result['results']) == 3
    features = service.last_derived.set_index('transaction_id')
    assert features.loc['a', 'batch_uid_count'] == 2
    assert features.loc['a', 'batch_uid_TransactionAmt_mean'] == 20
    assert features.loc['a', 'batch_uid_TransactionAmt_std'] == pytest.approx(np.sqrt(200))
    assert features.loc['a', 'batch_freq_card1'] == 1
    assert features.loc['a', 'batch_uid_frequency'] == pytest.approx(2/3)
    assert pd.isna(features.loc['missing', 'batch_uid_count'])
    assert features.loc['a', 'batch_uid_M1_T_rate'] == 1
    assert len(features.columns) == 75


def test_immutable_seal_duplicate_conflict_late_quarantine_and_restart(tmp_path):
    service = candidate(tmp_path)
    options = dict(history_cutoff_second=0, scratch=tmp_path/'work')
    store = DailyBatchStore(tmp_path/'journal.sqlite', service, **options)
    row = event('a', 86401)
    assert store.ingest(row, received_second=86402)['status'] == 'accepted'
    assert store.ingest(row, received_second=86403)['status'] == 'duplicate'
    with pytest.raises(ValueError, match='conflicts'):
        store.ingest(event('a', 86401, 99), received_second=86404)
    with pytest.raises(ValueError, match='not closed'):
        store.seal(1, available_through_second=172799)
    first = store.seal(1, available_through_second=172800)
    assert store.ingest(event('late', 86402), received_second=172801)['status'] == 'quarantined'
    assert store.ingest(event('late', 86402), received_second=172802)['original_status'] == 'quarantined'
    assert store.seal(1, available_through_second=172802) == first
    assert store.get_day(1) == first
    assert len(store.quarantined()) == 1
    with pytest.raises(ValueError, match='quarantined'):
        store.resolve_transaction('late')
    reloaded = DailyBatchStore(tmp_path/'journal.sqlite', service, **options)
    assert reloaded.resolve_transaction('a')['batch_id'] == first['batch_id']
    assert reloaded.resolve_transaction('a')['transaction_amount'] == 10
    assert reloaded.list_batches()[0]['transaction_count'] == 1
    with pytest.raises(ValueError, match='own route'):
        reloaded.get_transaction(1, 'a', 'x3')


def test_failed_seal_stays_open_and_validation_is_actionable(tmp_path):
    service = candidate(tmp_path)
    store = DailyBatchStore(tmp_path/'journal.sqlite', service,
                           history_cutoff_second=0, scratch=tmp_path/'work')
    missing = event('a', 86401)
    missing.raw_features.pop('D9')
    with pytest.raises(ValueError, match='explicit null'):
        store.ingest(missing, received_second=86401)
    with pytest.raises(ValueError, match='arrival second'):
        store.ingest(event('future', 86401), received_second=86400)
    with pytest.raises(ValueError, match='not sealed'):
        store.get_day(1)
    with pytest.raises(ValueError, match='no accepted transactions'):
        store.seal(1, available_through_second=172800)
    assert store.list_batches() == []
    store.ingest(event('valid', 86401), received_second=172801)
    with pytest.raises(ValueError, match='watermark precedes'):
        store.seal(1, available_through_second=172800)
    assert store.list_batches() == []
    store.seal(1, available_through_second=172802)


def test_journal_binding_content_corruption_and_chronological_seals(tmp_path):
    service = candidate(tmp_path)
    store = DailyBatchStore(tmp_path/'journal.sqlite', service,
                           history_cutoff_second=0, scratch=tmp_path/'work')
    store.ingest(event('first', 86401), received_second=180000)
    store.ingest(event('second', 172801), received_second=180000)
    with pytest.raises(ValueError, match='earlier nonempty'):
        store.seal(2, available_through_second=259200)
    store.seal(1, available_through_second=259200)
    service.hashes = {**service.hashes, 'maps': 'b'*64}
    with pytest.raises(ValueError, match='binding changed'):
        DailyBatchStore(tmp_path/'journal.sqlite', service, history_cutoff_second=0,
                        scratch=tmp_path/'work')
    with store._connect() as conn:
        payload = json.loads(conn.execute('SELECT payload FROM batches').fetchone()[0])
        payload['results'][0]['score'] = .99
        conn.execute('UPDATE batches SET payload=?', [json.dumps(payload)])
    with pytest.raises(ValueError, match='hash mismatch'):
        store.get_day(1)


def test_bound_history_joins_once_and_ignores_its_later_rows(tmp_path):
    service = candidate(tmp_path)
    old, later = event('history', 1, 5), event('hidden-future', 259201, 999)
    rows = []
    for item in (old, later):
        rows.append({'transaction_id': item.transaction_id,
            'source_transaction_dt_seconds': item.source_transaction_dt_seconds,
            **normalized_raw(item, service.contract)})
    pd.DataFrame(rows).to_parquet(service.paths['source'], index=False)
    store = DailyBatchStore(tmp_path/'journal.sqlite', service, history=service.paths['source'],
                           history_cutoff_second=86399, scratch=tmp_path/'work')
    with pytest.raises(ValueError, match='already exists'):
        store.ingest(event('history', 86401), received_second=90000)
    store.ingest(event('a', 86401, 15), received_second=90000)
    store.seal(1, available_through_second=172800)
    values = service.last_derived.iloc[0]
    assert values.batch_uid_count == 2
    assert values.batch_uid_TransactionAmt_mean == 10
    assert values.batch_uid_TransactionAmt_std == pytest.approx(np.sqrt(50))
    assert values.batch_uid_frequency == 1


def test_memory_guard_stops_below_two_gib(monkeypatch):
    from graphrisk.serving import daily_batch_store as module
    monkeypatch.setattr(module.psutil, 'virtual_memory', lambda: SimpleNamespace(available=2*2**30-1))
    with pytest.raises(MemoryError, match='below 2 GiB'):
        module._memory_guard()
    monkeypatch.setattr(module.psutil, 'virtual_memory', lambda: SimpleNamespace(available=2*2**30))
    module._memory_guard()
