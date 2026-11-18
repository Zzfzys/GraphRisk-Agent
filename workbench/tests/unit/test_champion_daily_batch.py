import json

import numpy as np
import pandas as pd
import pytest

from graphrisk.serving import champion_daily_batch as daily


def make_service(tmp_path, monkeypatch):
    source = tmp_path/'source.parquet'
    derived = tmp_path/'derived.parquet'
    pd.DataFrame({'transaction_id': ['a', 'b'], 'source_transaction_dt_seconds': [86401, 86402],
                  'TransactionAmt': [1.0, 2.0], 'card1': ['a', 'b'], 'addr1': ['a', 'b'],
                  'P_emaildomain': ['a', 'b']}).to_parquet(source, index=False)
    pd.DataFrame({'transaction_id': ['a', 'b'], 'source_transaction_dt_seconds': [86401, 86402],
                  'batch_uid_count': [1.0, 2.0]}).to_parquet(derived, index=False)
    model, protocol, maps, audit, policy = [tmp_path/name for name in
        ('model.json', 'protocol.json', 'maps.json', 'audit.json', 'policy.json')]
    model.write_text('model')
    protocol.write_text(json.dumps({'raw': ['TransactionAmt'], 'base_mapping': {'day': {}},
                                    'uid_extra': {'day': ['batch_uid_count']}}))
    maps.write_text('{}')
    audit.write_text(json.dumps({'study_protocol_sha256': daily.file_sha256(protocol),
        'category_maps_sha256': daily.file_sha256(maps), 'raw_source_sha256': daily.file_sha256(source),
        'derived_source_sha256': daily.file_sha256(derived), 'columns_verified': 265,
        'exact_float32_and_nan_match': True}))
    policy.write_text(json.dumps({'model_sha256': daily.file_sha256(model), 'selection_cutoff': 0,
        'review_numerator': 14571, 'review_denominator': 88531, 'high_threshold': 0.7}))
    monkeypatch.setattr(daily, 'MODEL_SHA256', daily.file_sha256(model))
    monkeypatch.setattr(daily, 'POLICY_SHA256', daily.file_sha256(policy))
    monkeypatch.setattr(daily, 'FEATURE_AUDIT_SHA256', daily.file_sha256(audit))
    class FakeBooster:
        def __init__(self, **_): pass
        def load_model(self, _): pass
        def inplace_predict(self, matrix, **_): return np.array([0.9, 0.1])
    monkeypatch.setattr(daily.xgb, 'Booster', FakeBooster)
    monkeypatch.setattr(daily, 'transform_batch', lambda raw, extra, *_args, **_kw: raw[['TransactionAmt']])
    return daily.ChampionDailyBatch(model=model, protocol=protocol, maps=maps, audit=audit,
                                    policy=policy, source=source, derived=derived), source, policy


def test_closed_day_scores_and_binds_model_and_policy(tmp_path, monkeypatch):
    service, _, _ = make_service(tmp_path, monkeypatch)
    result = service.score_day(1, available_through_second=172800)
    assert result['temporal_scope'] == 'relative_day_end'
    assert result['batch_end_second'] == 172800
    assert result['model_sha256'] == daily.MODEL_SHA256
    assert [(v['transaction_id'], v['risk_level']) for v in result['results']] == [('a', 'high'), ('b', 'low')]
    with pytest.raises(ValueError, match='not closed'):
        service.score_day(1, available_through_second=172799)
    with pytest.raises(ValueError, match='selection cutoff'):
        service.score_day(0, available_through_second=86400)


def test_changed_bound_source_and_wrong_model_fail_closed(tmp_path, monkeypatch):
    service, source, policy = make_service(tmp_path, monkeypatch)
    source.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        service.score_day(1, available_through_second=172800)
    obj = json.loads(policy.read_text())
    obj['model_sha256'] = '0'*64
    policy.write_text(json.dumps(obj))
    with pytest.raises(ValueError, match='not compatible'):
        daily.ChampionDailyBatch(**service.paths)
