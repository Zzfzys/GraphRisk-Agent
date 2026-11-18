"""Explicit model choice never turns candidate errors into x3 predictions."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

path = Path(__file__).resolve().parents[2] / 'src/graphrisk/serving/explicit_model_selection.py'
spec = importlib.util.spec_from_file_location('explicit_model_selection_tested', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Candidate:
    hashes = {'model': module.DAY_MODEL_SHA256, **{name: 'a'*64 for name in ('protocol', 'maps', 'policy', 'source', 'derived')}}

    def score_day(self, day, *, available_through_second):
        return {'model_version': module.DAY_MODEL_VERSION, 'model_sha256': self.hashes['model'],
            'feature_protocol_sha256': self.hashes['protocol'], 'category_maps_sha256': self.hashes['maps'],
            'policy_sha256': self.hashes['policy'], 'source_sha256': self.hashes['source'],
            'derived_sha256': self.hashes['derived'], 'relative_day': day, 'temporal_scope': 'relative_day_end',
            'batch_end_second': (day+1)*86400, 'available_through_second': available_through_second,
            'results': [{'transaction_id': 't1', 'event_second': 86403, 'score': .6, 'risk_level': 'high'}]}


def scope():
    return SimpleNamespace(dataset_source='ieee_cis', target_event_second=86403, transaction_id='t1')


def test_daily_preserves_closed_batch_identity_and_time():
    result = module.select_score('daily', scope=scope(), candidate=Candidate(), watermark=172800)
    assert result['score'] == .6 and result['risk_level'] == 'high'
    assert result['batch_end_second'] == 172800 and result['temporal_scope'] == 'relative_day_end'
    assert result['model_sha256'] == module.DAY_MODEL_SHA256 and result['probability_calibrated'] is False


@pytest.mark.parametrize('kind', ['wrong_id', 'wrong_time', 'duplicate', 'wrong_model', 'wrong_mapping',
                                 'wrong_day', 'wrong_cutoff', 'unclosed', 'invalid_score', 'invalid_band'])
def test_rejects_unbound_daily_outputs(kind):
    candidate = Candidate()
    output = deepcopy(candidate.score_day(1, available_through_second=172800))
    if kind == 'wrong_id': output['results'][0]['transaction_id'] = 'other'
    elif kind == 'wrong_time': output['results'][0]['event_second'] += 1
    elif kind == 'duplicate': output['results'].append(deepcopy(output['results'][0]))
    elif kind == 'wrong_model': output['model_sha256'] = '0'*64
    elif kind == 'wrong_mapping': output['category_maps_sha256'] = '0'*64
    elif kind == 'wrong_day': output['relative_day'] = 2
    elif kind == 'wrong_cutoff': output['batch_end_second'] += 1
    elif kind == 'unclosed': output['available_through_second'] = 100
    elif kind == 'invalid_score': output['results'][0]['score'] = float('nan')
    elif kind == 'invalid_band': output['results'][0]['risk_level'] = 'unknown'
    candidate.score_day = lambda *args, **kwargs: output
    with pytest.raises(ValueError):
        module.select_score('daily', scope=scope(), candidate=candidate, watermark=172800)


def test_daily_failure_does_not_attempt_x3():
    class Broken(Candidate):
        def score_day(self, *args, **kwargs):
            raise RuntimeError('candidate unavailable')
    class NeverUse:
        def __getattribute__(self, name):
            raise AssertionError('must not silently fall back')
    with pytest.raises(RuntimeError, match='candidate unavailable'):
        module.select_score('daily', scope=scope(), candidate=Broken(), watermark=172800,
                            x3_tool=NeverUse(), x3_guard=NeverUse())


def test_rejects_implicit_and_missing_selection():
    with pytest.raises(ValueError, match='unknown model selection'):
        module.select_score('auto', scope=scope())
    with pytest.raises(ValueError, match='x3 service or source guard unavailable'):
        module.select_score('x3', scope=scope())
