"""Exact AP early stopping, ID-aware ranking and reload checks for fold-1."""
import math
import time

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import average_precision_score, roc_auc_score

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.models.enhanced_data import MatrixIterator


def metrics(ids, labels, scores):
    labels, scores = np.asarray(labels), np.asarray(scores)
    if len(set(ids)) != len(ids) or len(ids) != len(labels) or len(labels) != len(scores) or labels.ndim != 1 or scores.ndim != 1:
        raise ValueError("unique aligned ID/label/prediction vectors required")
    if not np.isin(labels, [0, 1]).all() or len(np.unique(labels)) != 2 or not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
        raise ValueError("finite probability scores and both binary classes required")
    order = np.lexsort((np.asarray(ids, dtype=str), -scores))
    result = {"ap": float(average_precision_score(labels, scores)), "roc_auc": float(roc_auc_score(labels, scores)),
              "rows": len(ids), "positive_count": int(labels.sum()), "positive_fraction": float(labels.mean())}
    for fraction, name in ((0.01, "top1"), (0.05, "top5")):
        k = math.ceil(len(ids)*fraction)
        positive = labels[order[:k]].sum()
        result["precision_"+name] = float(positive/k)
        result["recall_"+name] = float(positive/labels.sum())
    return result


def exact_ap(prediction, matrix):
    return "AP", float(average_precision_score(matrix.get_label(), prediction))


class GuardCallback(xgb.callback.TrainingCallback):
    def __init__(self, guard):
        self.guard = guard

    def after_iteration(self, model, epoch, history):
        self.guard()
        if epoch % 100 == 0:
            print(f"xgb round={epoch+1} inner_AP={history['inner']['AP'][-1]:.6f}", flush=True)
        return False


def quantile(factory, count, settings, guard, ref=None):
    iterator = MatrixIterator(factory, count, guard)
    matrix = xgb.QuantileDMatrix(iterator, max_bin=settings.max_bin, nthread=settings.threads, enable_categorical=True, ref=ref)
    if matrix.num_row() != count:
        raise ValueError("quantile matrix row count changed")
    return matrix


def train_xgb(task, settings, train_matrix, inner_matrix, guard):
    parameters = {k: getattr(settings, k) for k in ("device", "objective", "max_bin", "scale_pos_weight", "subsample", "colsample_bytree", "reg_lambda", "max_cat_to_onehot", "max_cat_threshold", "max_cached_hist_node")}
    parameters.update({k:v for k,v in task["parameters"].items() if k != "id"})
    parameters.update(tree_method="hist", nthread=settings.threads, seed=settings.seed, eval_metric="logloss")
    history = {}
    model = xgb.train(parameters, train_matrix, num_boost_round=settings.max_estimators,
        evals=[(train_matrix, "train"), (inner_matrix, "inner")], custom_metric=exact_ap,
        callbacks=[GuardCallback(guard), xgb.callback.EarlyStopping(rounds=settings.patience, metric_name="AP", data_name="inner", maximize=True, save_best=True)],
        evals_result=history, verbose_eval=False)
    guard()
    return model, {"history": history, "best_round": model.best_iteration+1,
        "rounds_observed": len(history["inner"]["AP"]), "best_inner_ap": float(model.best_score),
        "parameters": parameters, "saved_rounds": model.num_boosted_rounds()}


def predict_xgb(model, factory, guard):
    pieces = []
    for frame, _ in factory():
        guard()
        pieces.append(model.inplace_predict(frame, validate_features=True))
    return np.concatenate(pieces)


def select_candidate(results, order):
    if set(results) != set(order) or any(results[name]["status"] != "completed" for name in order):
        raise ValueError("all preregistered candidates must complete before selection")
    chosen = min(order, key=lambda name: (-results[name]["metrics"]["ap"], results[name]["fit_seconds"], order.index(name)))
    value = {"schema_version": "stage09-5b-tabular-choice-v1", "candidate": chosen,
        "selection": "inner_ap_then_fit_seconds_then_registered_order", "scope": "baseline_choice_only_not_final_gnn_decision",
        "candidates": {name: {"metrics": results[name]["metrics"], "fit_seconds": results[name]["fit_seconds"], "model_sha256": results[name]["model_sha256"]} for name in order},
        "outer_quality_gate": "pending_three_fold_confirmation_route5", "service_switched": False}
    return {**value, "content_sha256": _content_hash(value)}
