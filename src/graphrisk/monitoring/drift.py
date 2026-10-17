"""Label-free numeric population stability, with a frozen earlier reference."""
from __future__ import annotations

from bisect import bisect_right
import hashlib
import json
import math
from numbers import Real

SCHEMA = "stage09-numeric-drift-reference-v1"


def _numbers(values):
    result = []
    for value in values:
        if value is None or isinstance(value, Real) and math.isnan(value):
            result.append(None)
        elif isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            raise ValueError("numeric drift accepts finite numbers or explicit missing values only")
        else:
            result.append(float(value))
    if not result:
        raise ValueError("drift populations must not be empty")
    return result


def _quantile(values, fraction):
    point = (len(values)-1)*fraction
    lo, hi = math.floor(point), math.ceil(point)
    return values[lo] + (values[hi]-values[lo])*(point-lo)


def _counts(values, cuts):
    # Include explicit lower/upper overflow support and a separate missing bucket.
    counts = [0] * (len(cuts)+2)
    for value in values:
        counts[-1 if value is None else bisect_right(cuts, value)] += 1
    return counts


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def freeze_reference(values, *, feature, model_binding=None, bins=10, smoothing=.5, warning=.1, alert=.25):
    if feature not in {"transaction_amount", "tx_count_last_24h", "risk_score"}:
        raise ValueError("only declared label-free monitoring features are accepted")
    if not isinstance(bins, int) or isinstance(bins, bool) or not 2 <= bins <= 20:
        raise ValueError("quantile bins must be an integer between 2 and 20")
    if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in (smoothing, warning, alert)) or not smoothing > 0 or not 0 <= warning < alert:
        raise ValueError("invalid frozen smoothing or alert thresholds")
    if feature == "risk_score" and not model_binding:
        raise ValueError("score drift requires a pinned model binding")
    rows = _numbers(values)
    observed = sorted(v for v in rows if v is not None)
    if not observed:
        raise ValueError("a frozen reference requires at least one observed number")
    if feature == "risk_score" and any(not 0 <= v <= 1 for v in observed):
        raise ValueError("risk scores must be in [0, 1]")
    cuts = sorted(set(_quantile(observed, i/bins) for i in range(bins+1)))
    value = dict(schema_version=SCHEMA, feature=feature, model_binding=model_binding,
                 cuts=cuts, counts=_counts(rows, cuts), total=len(rows), smoothing=float(smoothing),
                 warning_psi=float(warning), alert_psi=float(alert))
    value["content_sha256"] = _hash(value)
    return value


def compare_population(reference, values, *, model_binding=None, population_kind="historical_observation"):
    if population_kind not in {"historical_observation", "synthetic_shift", "fixture"}:
        raise ValueError("declare the population provenance explicitly")
    payload = {k: v for k, v in reference.items() if k != "content_sha256"}
    if reference.get("schema_version") != SCHEMA or _hash(payload) != reference.get("content_sha256"):
        raise ValueError("frozen drift reference was changed")
    if reference["model_binding"] != model_binding:
        raise ValueError("model binding changed; score drift is not a same-model comparison")
    rows = _numbers(values)
    if reference["feature"] == "risk_score" and any(v is not None and not 0 <= v <= 1 for v in rows):
        raise ValueError("risk scores must be in [0, 1]")
    current = _counts(rows, reference["cuts"])
    if len(current) != len(reference["counts"]) or sum(reference["counts"]) != reference["total"]:
        raise ValueError("invalid frozen reference counts")
    alpha = reference["smoothing"]
    total = len(rows)
    p = [(c+alpha)/(reference["total"]+alpha*len(current)) for c in reference["counts"]]
    q = [(c+alpha)/(total+alpha*len(current)) for c in current]
    psi = sum((a-b)*math.log(a/b) for a, b in zip(p, q))
    status = "alert" if psi >= reference["alert_psi"] else "warning" if psi >= reference["warning_psi"] else "stable"
    return {"feature": reference["feature"], "reference_sha256": reference["content_sha256"],
            "population_kind": population_kind, "reference_count": reference["total"], "current_count": total,
            "psi": psi, "status": status, "reference_missing_rate": reference["counts"][-1]/reference["total"],
            "current_missing_rate": current[-1]/total, "counts": current,
            "predictive_accuracy_change": "not_measured_no_labels", "production_drift_claimed": False}
