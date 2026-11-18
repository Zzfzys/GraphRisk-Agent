"""Explicit, server-owned selection of the daily candidate or existing x3 service."""
from math import isfinite
from types import SimpleNamespace

from graphrisk.serving.model_risk_band import model_risk_band

DAY_MODEL_VERSION = 'champion-xgb-day-216-plus49-fold3-v1'
DAY_MODEL_SHA256 = '39df475311dc34ecd83de66372cc1bc7aae4505517f6d878a4e1c4b11d47f2ba'


def select_score(mode, *, scope, candidate=None, watermark=None, x3_tool=None, x3_guard=None):
    """Select exactly one path. Scope, service, watermark and mode belong to the server."""
    if scope.dataset_source != 'ieee_cis' or type(scope.target_event_second) is not int:
        raise ValueError('a bound IEEE transaction and event time are required')
    if mode == 'daily':
        if candidate is None:
            raise ValueError('daily candidate unavailable')
        # Exceptions are propagated; x3 is never invoked implicitly.
        day = scope.target_event_second // 86400
        output = candidate.score_day(day, available_through_second=watermark)
        hashes = candidate.hashes
        expected = {'model_sha256': hashes['model'], 'feature_protocol_sha256': hashes['protocol'],
                    'category_maps_sha256': hashes['maps'], 'policy_sha256': hashes['policy'],
                    'source_sha256': hashes['source'], 'derived_sha256': hashes['derived']}
        if (output.get('model_version') != DAY_MODEL_VERSION or hashes['model'] != DAY_MODEL_SHA256 or
                output.get('temporal_scope') != 'relative_day_end' or output.get('relative_day') != day or
                output.get('batch_end_second') != (day + 1) * 86400 or
                output.get('available_through_second') != watermark or
                type(watermark) is not int or watermark < output['batch_end_second'] or
                any(output.get(k) != v for k, v in expected.items())):
            raise ValueError('daily result identity or cutoff mismatch')
        rows = [row for row in output['results'] if row['transaction_id'] == scope.transaction_id]
        if len(rows) != 1 or rows[0]['event_second'] != scope.target_event_second:
            raise ValueError('daily result transaction or event mismatch')
        row = rows[0]
        score, level = row['score'], row['risk_level']
        if (isinstance(score, bool) or not isinstance(score, (float, int)) or not isfinite(score) or
                not 0 <= score <= 1 or level not in ('low', 'medium', 'high')):
            raise ValueError('invalid daily score or band')
        return {'transaction_id': scope.transaction_id, 'score': float(score), 'risk_level': level,
                'model_version': DAY_MODEL_VERSION, 'model_sha256': DAY_MODEL_SHA256,
                'temporal_scope': 'relative_day_end', 'batch_end_second': output['batch_end_second'],
                'available_through_second': watermark, 'policy_sha256': output['policy_sha256'],
                'feature_protocol_sha256': output['feature_protocol_sha256'],
                'category_maps_sha256': output['category_maps_sha256'],
                'source_sha256': output['source_sha256'], 'derived_sha256': output['derived_sha256'],
                'probability_calibrated': False}
    if mode != 'x3':
        raise ValueError('unknown model selection')
    if x3_tool is None or x3_guard is None:
        raise ValueError('x3 service or source guard unavailable')
    x3_guard()
    service = x3_tool._predictor.service
    recorded = service.lookup(scope.transaction_id)
    if (recorded['primary_model_version'] != scope.primary_model_version or
            type(recorded['event_second']) is not int or recorded['event_second'] != scope.target_event_second):
        raise ValueError('x3 result identity or event mismatch')
    result = x3_tool.get_risk_score({'transaction_id': scope.transaction_id, 'dataset_source': scope.dataset_source})
    if (result.status != 'ok' or result.transaction_id != scope.transaction_id or
            result.data.model_version != scope.primary_model_version or
            result.data.model_name != 'xgboost' or result.data.risk_score != float(recorded['primary_score'])):
        raise ValueError('x3 scoring service failed or returned mismatched identity')
    fact = SimpleNamespace(metric='primary_model_score', availability='available', subject='target_transaction',
        unit='uncalibrated_score', temporal_scope='target_event', model_version=scope.primary_model_version,
        source_version=scope.model_revision, value=result.data.risk_score)
    band = model_risk_band(scope, SimpleNamespace(case_alias=scope.case_alias, facts=[fact]), x3_guard._bindings)
    x3_guard()
    if band['status'] != 'available':
        raise ValueError('x3 trial band is unavailable for this bound scope')
    return {'transaction_id': scope.transaction_id, 'score': result.data.risk_score,
            'risk_level': band['risk_level'], 'model_version': result.data.model_version,
            'model_revision': scope.model_revision, 'band_version': band['version'],
            'temporal_scope': 'target_event', 'probability_calibrated': False}
