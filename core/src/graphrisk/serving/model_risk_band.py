"""Experimental historical XGBoost bands, separate from LLM judgment.

Thresholds are from the frozen, label-guided route6 experiment. They are
bound to one coverage profile and the exact source model artifacts. This is
a read-only presentation; it never changes reports, scores, or decisions.
"""
from math import isfinite

PROFILE_SHA = 'e386298dfced099bfedcc741889a0129319fde82f513a654b8358ed272ea6c61'
VERSION = 'route6-supervised-bands-20261028-v1'
# (model artifact SHA-256, strictly earlier selection cutoff, low/medium, medium/high)
MODELS = {
    'x3-fold-2-historical-v1': ('6c02de8186b3969cf7c26151c0594aaa64f4f25c501229cc13b17a705339d1a6', 9409262, 0.01725749857723713, 0.16679318249225616),
    'x3-fold-3-historical-v1': ('165dc84808771175bd4b172a7e286a123f6f06f3fcc37ca6df41a1164fdb60c6', 11542318, 0.020007457584142685, 0.23131798207759857),
}


def model_risk_band(scope, evidence, bindings):
    """Return a versioned display projection or an explicit unavailable result.

    `scope`, `evidence`, and source bindings are server-owned and already
    checked by the conversation backend; never accept them from an LLM.
    """
    unavailable = {'status': 'unavailable', 'reason': 'model_or_time_not_supported'}
    recipe = MODELS.get(scope.primary_model_version)
    if (recipe is None or scope.dataset_source != 'ieee_cis' or
        scope.model_revision != PROFILE_SHA or evidence.case_alias != scope.case_alias or
        type(scope.target_event_second) is not int or scope.target_event_second <= recipe[1]):
        return unavailable
    fold = 'fold-2' if scope.primary_model_version == 'x3-fold-2-historical-v1' else 'fold-3'
    def bound(suffix, sha):
        return any(path.as_posix().lower().endswith(suffix) and value == sha for path, value in bindings)
    if (not bound('/coverage-profile-calendar-v2.json', PROFILE_SHA) or
        not bound(f'/{fold}/x3/model.json', recipe[0])):
        return unavailable
    facts = [f for f in evidence.facts if f.metric == 'primary_model_score']
    if len(facts) != 1:
        return {'status': 'unavailable', 'reason': 'score_not_available'}
    fact = facts[0]
    if (fact.availability != 'available' or fact.subject != 'target_transaction' or
        fact.unit != 'uncalibrated_score' or fact.temporal_scope != 'target_event' or
        fact.model_version != scope.primary_model_version or fact.source_version != scope.model_revision or
        isinstance(fact.value, bool) or not isinstance(fact.value, (float, int)) or
        not isfinite(fact.value) or not 0 <= fact.value <= 1):
        return {'status': 'unavailable', 'reason': 'score_not_available'}
    level = 'high' if fact.value >= recipe[3] else 'medium' if fact.value >= recipe[2] else 'low'
    return {'status': 'available', 'risk_level': level, 'version': VERSION}
