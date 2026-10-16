"""Calibration-only fusion, seed/fold aggregation and paired temporal uncertainty."""
import math

import numpy as np

from graphrisk.models.enhanced_training import metrics


def choose_alpha(ids, labels, baseline, graph_scores, grid):
    if len(graph_scores) != 3 or list(grid) != [0., .25, .5, .75, 1.]:
        raise ValueError("three seeds and frozen XGBoost weight grid required")
    baseline = np.asarray(baseline)
    rows = []
    for alpha in grid:
        values = [metrics(ids, labels, alpha*baseline+(1-alpha)*np.asarray(g))["ap"] for g in graph_scores]
        rows.append({"xgboost_weight": alpha, "seed_ap": values, "mean_ap": float(np.mean(values))})
    selected = min(rows, key=lambda row: (-row["mean_ap"], -row["xgboost_weight"]))
    return {"alpha": selected["xgboost_weight"], "grid_results": rows,
            "selection": "calibration_mean_seed_AP_tie_larger_xgb", "rows": len(ids)}


class WeightedRanking:
    """Exact duplicated-day AP (including score ties) and integer Top1% recall.

    Integer day multiplicities equal physically repeating all that day's rows;
    no fractional selection, independent per-row bootstrap or ensemble AP shortcut.
    """
    def __init__(self, ids, labels, scores, day_codes):
        metrics(ids, labels, scores)
        self.order = np.lexsort((np.asarray(ids, dtype=str), -np.asarray(scores)))
        self.labels = np.asarray(labels)[self.order]
        self.days = np.asarray(day_codes)[self.order]
        ordered = np.asarray(scores)[self.order]
        self.ends = np.r_[np.flatnonzero(ordered[1:] != ordered[:-1]), len(ordered)-1]

    def measure(self, multiplicities):
        weights = np.asarray(multiplicities)[self.days]
        if (weights < 0).any() or not np.equal(weights, np.floor(weights)).all():
            raise ValueError("nonnegative integer day multiplicities required")
        cumulative = np.cumsum(weights)
        positives = weights*self.labels
        tp = np.cumsum(positives)
        if not len(cumulative) or not cumulative[-1] or not tp[-1]:
            return None
        group_positive = np.diff(np.r_[0, tp[self.ends]])
        precision = np.divide(tp[self.ends], cumulative[self.ends],
                              out=np.zeros(len(self.ends), dtype=float), where=cumulative[self.ends] > 0)
        ap = float(np.dot(group_positive, precision)/tp[-1])
        k = math.ceil(.01*int(cumulative[-1]))
        selected = np.minimum(weights, np.maximum(0, k-np.r_[0, cumulative[:-1]]))
        return ap, float(np.dot(selected, self.labels)/tp[-1])


def paired_uncertainty(folds, iterations=1000, seed=20261009, guard=lambda: None):
    if len(folds) != 3 or isinstance(iterations, bool) or iterations < 1:
        raise ValueError("three folds and positive bootstrap iteration count required")
    rng = np.random.default_rng(seed)
    prepared, support = [], []
    for fold in folds:
        if len(fold["fusion"]) != 3:
            raise ValueError("three seed predictions per fold required")
        days, codes = np.unique(np.asarray(fold["seconds"], dtype=np.int64)//86400, return_inverse=True)
        if len(days) < 2:
            raise ValueError("at least two relative-day blocks per outer fold required")
        prepared.append((WeightedRanking(fold["ids"], fold["labels"], fold["baseline"], codes),
            [WeightedRanking(fold["ids"], fold["labels"], v, codes) for v in fold["fusion"]], len(days)))
        support.append({"days": len(days), "rows": len(codes)})
    draws = []
    for iteration in range(iterations):
        if iteration % 25 == 0:
            guard()
        diffs, gains = [], []
        for baseline, fusions, count in prepared:
            multiplicities = rng.multinomial(count, np.full(count, 1/count))
            observed = baseline.measure(multiplicities)
            models = [fusion.measure(multiplicities) for fusion in fusions]
            if observed is None or observed[1] == 0 or any(v is None for v in models):
                break
            diffs.append(float(np.mean([v[0]-observed[0] for v in models])))
            gains.append(float(np.mean([(v[1]-observed[1])/observed[1] for v in models])))
        if len(diffs) == 3:
            draws.append([float(np.mean(diffs)), float(np.mean(gains))])
    guard()
    array = np.asarray(draws, dtype=float).reshape(-1, 2)
    intervals = np.percentile(array, [2.5, 97.5], axis=0) if len(array) else np.full((2, 2), np.nan)
    return {"method": "paired_relative_day_block_bootstrap_seed_mean_then_equal_fold_mean",
        "iterations": iterations, "valid_iterations": len(array), "seed": seed,
        "support": support, "ap_delta_ci95": intervals[:, 0].tolist() if len(array) else None,
        "relative_recall_top1_delta_ci95": intervals[:, 1].tolist() if len(array) else None}, array


def evaluate_folds(folds, thresholds, iterations=1000, seed=20261009, guard=lambda: None):
    if len(folds) != 3 or len({fold["name"] for fold in folds}) != 3:
        raise ValueError("three distinct aligned confirmation folds required")
    result, paired = [], []
    for fold in folds:
        if len(fold["graphs"]) != 3 or fold["alpha"] not in [0., .25, .5, .75, 1.]:
            raise ValueError("three seeds and frozen alpha required")
        base = metrics(fold["ids"], fold["labels"], fold["baseline"])
        fused_scores = [fold["alpha"]*np.asarray(fold["baseline"])+(1-fold["alpha"])*np.asarray(g) for g in fold["graphs"]]
        graph = [metrics(fold["ids"], fold["labels"], g) for g in fold["graphs"]]
        fused = [metrics(fold["ids"], fold["labels"], v) for v in fused_scores]
        mean = {key: float(np.mean([v[key] for v in fused])) for key in ("ap", "roc_auc", "recall_top1", "recall_top5", "precision_top1", "precision_top5")}
        delta = mean["ap"]-base["ap"]
        relative = (mean["recall_top1"]-base["recall_top1"])/base["recall_top1"] if base["recall_top1"] > 0 else None
        result.append({"fold": fold["name"], "alpha": fold["alpha"], "baseline": base,
            "graph_seeds": graph, "fusion_seeds": fused, "fusion_seed_mean": mean,
            "ap_delta": delta, "relative_recall_top1_delta": relative,
            "seed_ap_std": float(np.std([v["ap"] for v in fused], ddof=1))})
        paired.append({"ids": fold["ids"], "seconds": fold["seconds"], "labels": fold["labels"],
                       "baseline": fold["baseline"], "fusion": fused_scores})
    uncertainty, draws = paired_uncertainty(paired, iterations, seed, guard)
    summary = {"baseline_mean_roc_auc": float(np.mean([v["baseline"]["roc_auc"] for v in result])),
        "baseline_mean_ap": float(np.mean([v["baseline"]["ap"] for v in result])),
        "fusion_mean_ap": float(np.mean([v["fusion_seed_mean"]["ap"] for v in result])),
        "fusion_mean_roc_auc": float(np.mean([v["fusion_seed_mean"]["roc_auc"] for v in result])),
        "mean_ap_delta": float(np.mean([v["ap_delta"] for v in result])),
        "mean_relative_recall_top1_delta": float(np.mean([v["relative_recall_top1_delta"] for v in result])) if all(v["relative_recall_top1_delta"] is not None for v in result) else None,
        "positive_ap_folds": sum(v["ap_delta"] > 0 for v in result),
        "worst_baseline_roc_auc": min(v["baseline"]["roc_auc"] for v in result)}
    stable = (uncertainty["valid_iterations"] == iterations and uncertainty["ap_delta_ci95"] is not None
              and uncertainty["ap_delta_ci95"][0] > 0 and uncertainty["relative_recall_top1_delta_ci95"][0] > 0)
    gates = {"baseline_quality": summary["baseline_mean_roc_auc"] >= thresholds.minimum_xgboost_roc_auc,
        "ap_gain": summary["mean_ap_delta"] >= thresholds.minimum_fusion_ap_delta,
        "recall_gain": summary["mean_relative_recall_top1_delta"] is not None and summary["mean_relative_recall_top1_delta"] >= thresholds.minimum_relative_recall_top1_delta,
        "two_positive_folds": summary["positive_ap_folds"] >= thresholds.minimum_positive_folds,
        "paired_uncertainty_positive": bool(stable)}
    return {"folds": result, "summary": summary, "uncertainty": uncertainty, "gates": gates,
        "promoted": all(gates.values()), "aggregation": "mean_metrics_across_seeds_then_equal_fold_mean",
        "subgroup_promotion": False, "historically_observed_not_blind": True}, draws
