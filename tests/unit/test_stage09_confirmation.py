import numpy as np
import pytest
from sklearn.metrics import average_precision_score

from graphrisk.agent.stage09_preflight import _content_hash
from graphrisk.graph.stage09_confirmation_metrics import WeightedRanking, choose_alpha, evaluate_folds, paired_uncertainty
from graphrisk.graph.stage09_confirmation_execution import validate_result
from graphrisk.graph.stage09_enhancement import EnhancementLedger, load_contract
from pathlib import Path


def folds():
    rng = np.random.default_rng(19)
    labels = np.tile([0, 0, 0, 1], 50)
    ids = [str(100000+i) for i in range(len(labels))]
    scores = labels*.6+rng.uniform(0, .3, len(labels))
    return [dict(name="fold-"+str(i+1), ids=ids, labels=labels, seconds=np.arange(len(labels))//20*86400,
        baseline=scores, graphs=[scores.copy() for _ in range(3)], alpha=1.) for i in range(3)]


def test_calibration_ties_prefer_baseline_even_when_graphs_differ():
    value = folds()[0]
    result = choose_alpha(value["ids"], value["labels"], value["baseline"], value["graphs"], [0., .25, .5, .75, 1.])
    assert result["alpha"] == 1.
    assert len(result["grid_results"]) == 5
    assert all(len(row["seed_ap"]) == 3 for row in result["grid_results"])


@pytest.mark.parametrize("graph_count,grid", [(2, [0., .25, .5, .75, 1.]), (3, [0., 1.])])
def test_selection_rejects_changed_seed_or_grid_contract(graph_count, grid):
    value = folds()[0]
    with pytest.raises(ValueError):
        choose_alpha(value["ids"], value["labels"], value["baseline"], value["graphs"][:graph_count], grid)


def test_weighted_tied_AP_and_partial_topk_equal_physical_day_replication():
    ids = ["a", "b", "c", "d", "e", "f"]
    labels = np.array([0, 1, 1, 0, 0, 1])
    scores = np.array([.9, .9, .6, .4, .4, .1])
    days = np.array([0, 0, 1, 1, 2, 2])
    multiplicity = np.array([3, 0, 2])
    observed = WeightedRanking(ids, labels, scores, days).measure(multiplicity)
    repeated = np.repeat(np.arange(len(ids)), multiplicity[days])
    order = np.lexsort((np.asarray(ids)[repeated], -scores[repeated]))
    expected_recall = labels[repeated[order[:1]]].sum()/labels[repeated].sum()
    assert observed[0] == pytest.approx(average_precision_score(labels[repeated], scores[repeated]), abs=1e-15)
    assert observed[1] == expected_recall


def test_no_positive_bootstrap_and_negative_multiplicity_are_explicit():
    ranking = WeightedRanking(["a","b"], [1,0], [.9,.1], [0,1])
    assert ranking.measure([0,1]) is None
    with pytest.raises(ValueError):
        ranking.measure([-1,2])


def test_paired_bootstrap_reproducible_and_baseline_only_gain_is_zero():
    data = folds()
    threshold = load_contract(Path(__file__).resolve().parents[2]/"configs/stage09_model_enhancement.json").metrics
    first, samples = evaluate_folds(data, threshold, 20)
    second, repeated = evaluate_folds(data, threshold, 20)
    assert first == second
    np.testing.assert_array_equal(samples, repeated)
    assert np.max(np.abs(samples)) == 0
    assert first["summary"]["mean_ap_delta"] == 0
    assert first["summary"]["mean_relative_recall_top1_delta"] == 0
    assert first["uncertainty"]["ap_delta_ci95"] == [0.,0.]
    assert first["gates"]["baseline_quality"]
    assert not first["promoted"]


def test_three_seed_metrics_are_not_prediction_ensemble_metrics():
    data = folds()
    for fold in data:
        fold["alpha"] = 0.
        fold["graphs"][2] = 1-fold["graphs"][2]
    threshold = load_contract(Path(__file__).resolve().parents[2]/"configs/stage09_model_enhancement.json").metrics
    result, _ = evaluate_folds(data, threshold, 10)
    expected = np.mean([average_precision_score(data[0]["labels"], scores) for scores in data[0]["graphs"]])
    ensemble = average_precision_score(data[0]["labels"], np.mean(data[0]["graphs"], axis=0))
    assert expected != ensemble
    assert result["summary"]["fusion_mean_ap"] == pytest.approx(expected)


def test_low_day_support_cannot_claim_stable_increment():
    value = folds()[0]
    value["seconds"] = np.zeros(len(value["ids"]), dtype=int)
    paired = [{"ids": value["ids"], "labels": value["labels"], "seconds": value["seconds"],
               "baseline": value["baseline"], "fusion": value["graphs"]} for _ in range(3)]
    with pytest.raises(ValueError, match="two relative-day"):
        paired_uncertainty(paired, 10)


def test_zero_baseline_top1_recall_is_ineligible_not_division_by_zero():
    data = folds()
    for fold in data:
        fold["baseline"] = 1-fold["baseline"]
        fold["alpha"] = 0.
    threshold = load_contract(Path(__file__).resolve().parents[2]/"configs/stage09_model_enhancement.json").metrics
    result, _ = evaluate_folds(data, threshold, 10)
    assert result["summary"]["mean_relative_recall_top1_delta"] is None
    assert not result["gates"]["recall_gain"] and not result["gates"]["baseline_quality"]
    assert not result["promoted"]


def test_outer_open_requires_every_confirmation_seed_and_freeze(tmp_path):
    contract = load_contract(Path(__file__).resolve().parents[2]/"configs/stage09_model_enhancement.json")
    ledger = EnhancementLedger(tmp_path/"ledger.json", contract, "a"*64)
    ledger.initialize()
    with pytest.raises(ValueError, match="frozen"):
        ledger.open_results("outer")
    ledger.freeze_decision("b"*64)
    for fold in contract.windows:
        for seed in contract.confirmation_seeds:
            if fold.name == "fold-3" and seed == contract.confirmation_seeds[-1]:
                continue
            attempt = ledger.reserve("gnn", "confirmation", fold.name+str(seed),
                {"fold": fold.name, "seed": seed, "input_sha256": "c"*64}, clock=lambda: 0.)
            attempt.finish("completed")
    with pytest.raises(ValueError, match="three folds"):
        ledger.open_results("outer")
    final = ledger.reserve("gnn", "confirmation", "last",
        {"fold": "fold-3", "seed": contract.confirmation_seeds[-1], "input_sha256": "c"*64}, clock=lambda: 0.)
    final.finish("completed")
    ledger.open_results("outer")
    with pytest.raises(ValueError, match="no repeat"):
        ledger.open_results("outer")
    with pytest.raises(ValueError, match="sealed"):
        ledger.reserve("gnn", "confirmation", "extra", {"fold":"fold-1","seed":20260921,"input_sha256":"c"*64})


def test_result_cannot_substitute_seed_or_family(tmp_path):
    class Ledger:
        def read(self):
            return {"fits": [{"name":"run", "family":"gnn", "lane":"confirmation","state":"completed",
                "registration":{"fold":"fold-2","seed":20260921,"input_sha256":"a"*64}}]}
    value = {"attempt_name":"run","family":"gnn","fold":"fold-2","seed":20260928,"input_sha256":"a"*64,"files":{}}
    with pytest.raises(ValueError):
        validate_result(tmp_path, value, Ledger())
    value["seed"] = 20260921
    validate_result(tmp_path, value, Ledger())
    value["family"] = "xgboost"
    with pytest.raises(ValueError):
        validate_result(tmp_path, value, Ledger())
