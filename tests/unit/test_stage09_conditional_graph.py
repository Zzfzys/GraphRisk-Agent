"""Train-only normalization, meaningful controls, trainer parity and protected budget."""
from pathlib import Path
import numpy as np
import pytest
import torch
import pandas as pd
from torch_geometric.data import Batch, Data

from graphrisk.demo.stage09_evaluation import read_json, write_json
from graphrisk.graph.stage09_conditional_data import fit_normalizer, normalized, audit_rewiring, neural_core, neural_columns
from graphrisk.graph.stage09_conditional_training import fit, select
from graphrisk.graph.stage09_conditional_execution import settings_for, reference_prefix, development_remaining, remaining_seconds, decision, reference_files, prefix_equivalence
from graphrisk.models.enhanced_execution import binding
from graphrisk.graph.stage09_enhancement import EnhancementLedger, load_contract
from graphrisk.graph.stage09_expansion_training import train
from graphrisk.graph.stage09_packed import transform_edges

SOURCE = Path(__file__).resolve().parents[2]


def test_normalization_matches_independent_population_statistics_and_clip(tmp_path):
    rng = np.random.default_rng(91)
    raw = rng.normal(1e6, 10, (9000, 3)).astype(np.float32)
    raw[:, 2] = 3
    path = tmp_path / "train.npy"
    np.save(path, raw)
    state = fit_normalizer(path, ["a", "b", "constant"], 10.)
    np.testing.assert_allclose(state["mean"], raw.astype(float).mean(0), atol=1e-8)
    np.testing.assert_allclose(state["scale"][:2], raw.astype(float).std(0)[:2], atol=1e-8)
    assert state["scale"][2] == 1 and state["train_rows"] == 9000 and not state["labels_used"]
    applied = normalized(np.asarray([[1e9, -1e9, 3.]]), state)
    np.testing.assert_array_equal(applied, [[10., -10., 0.]])
    assert fit_normalizer(path, ["a", "b", "constant"], 10.) == state
    path.unlink()  # Windows mapping lifetime must allow release.


def test_neural_categories_keep_missing_unknown_distinct_and_hash_deterministically():
    state = {"numeric": {"n": {}}, "categorical": {"small": ["A", "B"], "large": [str(i) for i in range(9)]},
             "output_columns": ["n", "missing:n", "small", "large"]}
    frame = pd.DataFrame({"n": [2., 3., 4., 5.], "missing:n": [0., 0., 1., 0.],
        "small": pd.Categorical(["__missing__", "__unknown__", "v:A", "v:B"], categories=["__missing__", "__unknown__", "v:A", "v:B"]),
        "large": pd.Categorical(["__missing__", "__unknown__", "v:3", "v:7"], categories=["__missing__", "__unknown__", *["v:"+str(i) for i in range(9)]])})
    values = neural_core(frame, state)
    assert values.shape == (4, len(neural_columns(state))) == (4, 16)
    np.testing.assert_array_equal(values[:, 2:6], np.eye(4, dtype=np.float32))
    np.testing.assert_array_equal(values[:2, 6:8], np.eye(2, dtype=np.float32))
    assert (np.abs(values[2:, 8:]).sum(1) == 1).all() and values[2:, 6:8].sum() == 0
    np.testing.assert_array_equal(neural_core(frame.iloc[::-1], state), values[::-1])
    with pytest.raises(ValueError, match="order"):
        neural_core(frame[["small", "large", "n", "missing:n"]], state)


@pytest.mark.parametrize("columns,clip", [([], 10), (["x", "x"], 10), (["a", "b"], 0), (["a", "b"], float("nan"))])
def test_invalid_normalizer_contract_is_rejected(tmp_path, columns, clip):
    path = tmp_path / "train.npy"
    np.save(path, np.ones((2, 2), dtype=np.float32))
    with pytest.raises(ValueError):
        fit_normalizer(path, columns, clip)


def test_nonfinite_or_wrong_width_core_cannot_be_silently_scaled(tmp_path):
    path = tmp_path / "train.npy"
    np.save(path, [[1, np.nan]])
    with pytest.raises(ValueError, match="nonfinite"):
        fit_normalizer(path, ["a", "b"], 10.)
    state = {"columns": ["a"], "mean": [0.], "scale": [1.], "clip": 10.}
    for raw in (np.ones((2, 2)), np.asarray([[np.inf]])):
        with pytest.raises(ValueError, match="finite aligned"):
            normalized(raw, state)


def test_rewire_changes_history_without_changing_degree_type_root_or_nodes():
    kinds = np.asarray([0, 0, 0, 0, 0, 1, 1])
    x = np.zeros((7, 28), dtype=np.float32)
    x[np.arange(7), 23+kinds] = 1
    pairs = [(0, 5), (1, 5), (2, 5), (3, 6), (4, 6)]
    edges = np.asarray(sorted(pairs+[(b, a) for a, b in pairs]), dtype=np.int64)
    found = None
    for seed in range(20):
        transformed, changed = transform_edges(edges, kinds, 0, "fixture", seed)
        audit_rewiring(x, edges, transformed)
        if changed:
            found = transformed
            repeated, _ = transform_edges(edges, kinds, 0, "fixture", seed)
            np.testing.assert_array_equal(found, repeated)
            break
    assert found is not None and not np.array_equal(edges, found)
    bad = found.copy()
    bad[0] = [0, 6]
    with pytest.raises(ValueError):
        audit_rewiring(x, edges, bad)


class Tiny:
    def __init__(self, n, prefix, dimension=28):
        self.ids = tuple(prefix+str(i) for i in range(n))
        self.dimension = dimension
    def batch(self, positions):
        graphs = []
        for i in positions:
            x = torch.zeros(3, self.dimension)
            x[0, 0] = x[2, 0] = float(i % 2)
            x[0, 23] = x[2, 23] = x[1, 24] = 1
            if self.dimension > 28:
                x[0, 28] = float(i % 2)*2-1
            graphs.append(Data(x=x, edge_index=torch.tensor([[0, 1, 2, 1], [1, 0, 1, 2]]), root_index=torch.tensor([0])))
        return Batch.from_data_list(graphs)


@pytest.mark.parametrize("dimension", [28, 91])
def test_generic_trainer_reloads_best_and_28dim_is_identical_to_parent(tmp_path, dimension):
    a, b = Tiny(12, "t", dimension), Tiny(6, "v", dimension)
    y, yi = np.arange(12) % 2, np.arange(6) % 2
    task = {"id": "fixture", "seed": 7, "parameters": {"learning_rate": .02, "hidden_channels": 8, "dropout": .4, "positive_weight": "train_ratio"}}
    settings = {"device": "cpu", "cpu_threads": 1, "batch_size": 4, "max_epochs": 7, "patience": 3, "train_probe_count": 12}
    new = tmp_path / "new"
    new.mkdir()
    result = fit(a, b, y, yi, task, settings, new)
    history = read_json(new / "training-history.json")
    assert result["reload_rows"] == 6 and result["reload_max_abs_delta"] == 0
    assert result["metrics"]["ap"] == max(h["inner_ap"] for h in history)
    assert result["input_dimension"] == dimension
    if dimension == 28:
        old = tmp_path / "old"
        old.mkdir()
        before = train(a, b, y, yi, task, settings, old)
        assert before["metrics"] == result["metrics"] and before["best_epoch"] == result["best_epoch"]
        np.testing.assert_array_equal(np.load(old / "inner-scores.npy"), np.load(new / "inner-scores.npy"))
        original = read_json(old / "training-history.json")
        assert [{k:v for k,v in h.items() if k != "elapsed_seconds"} for h in original] == [{k:v for k,v in h.items() if k != "elapsed_seconds"} for h in history]


def test_reference_prefix_and_selection_cannot_promote_diagnostic():
    ref = {"best_epoch": 18, "epochs_observed": 28, "metrics": {"ap": .18}, "fit_seconds": 800}
    proof = reference_prefix(ref, [{"epoch": 18, "inner_ap": .18}, {"epoch": 26, "inner_ap": .17}], 25)
    assert not proof["new_reference_fit"]
    with pytest.raises(ValueError, match="prefix"):
        reference_prefix({**ref, "best_epoch": 26}, [{"epoch": 18, "inner_ap": .17}], 25)
    tasks = [{"id": "c"+str(i), "eligible": i in {3, 4}} for i in range(1, 6)]
    results = {t["id"]: {"metrics": {"ap": .9 if not t["eligible"] else .17}, "fit_seconds": 1.} for t in tasks}
    assert select(ref, results, tasks) == "g3"
    results["c4"]["metrics"]["ap"] = .2
    assert select(ref, results, tasks) == "c4"
    del results["c1"]
    with pytest.raises(ValueError, match="all five"):
        select(ref, results, tasks)


def test_grid_and_schedule_cannot_expand_after_registration(tmp_path):
    contract = load_contract(SOURCE / "configs/stage09_model_enhancement.json")
    settings = settings_for(SOURCE, contract)
    assert sum(t["max_seconds"] for t in settings["tasks"]) == 4350
    (tmp_path / "configs").mkdir()
    for key, value in (("max_epochs", 50), ("seed", 1), ("core_profile", "anonymous")):
        write_json(tmp_path / "configs/stage09_conditional_graph.json", {**settings, key: value})
        with pytest.raises(ValueError, match="preregistered"):
            settings_for(tmp_path, contract)


def ledger_at(tmp_path):
    result = EnhancementLedger(tmp_path / "ledger.json", load_contract(SOURCE / "configs/stage09_model_enhancement.json"), "b"*64)
    result.initialize()
    return result


def registration(**extra):
    return {"fold": "fold-1", "seed": 7, "input_sha256": "a"*64, "appendix_sha256": "c"*64, **extra}


def test_tighter_deadline_and_abandonment_retain_confirmation_reserve(tmp_path):
    ledger = ledger_at(tmp_path)
    assert development_remaining(ledger) == 10800
    now = [0.]
    attempt = ledger.reserve("gnn", "conditional", "bounded", registration(max_fit_seconds=800), clock=lambda: now[0])
    assert attempt.cap == 800
    now[0] = 801
    with pytest.raises(TimeoutError):
        attempt.guard()
    ledger.finish("bounded", "abandoned", 0)
    assert ledger.read()["fits"][0]["seconds"] == 800
    assert development_remaining(ledger) == 10000
    with pytest.raises(ValueError, match="retain"):
        ledger.reserve("gnn", "failure", "changed", registration(retry_of="bounded", max_fit_seconds=900))
    with pytest.raises(ValueError, match="retain"):
        ledger.reserve("gnn", "failure", "missing", registration(retry_of="bounded"))
    recovered = ledger.reserve("gnn", "failure", "same", registration(retry_of="bounded", max_fit_seconds=800))
    assert recovered.cap == 800
    ledger.finish("same", "failed", 1)
    ledger.freeze_decision("d"*64)
    confirmed = ledger.reserve("gnn", "confirmation", "confirmed", registration(seed=20260921))
    assert confirmed.cap == 3600


@pytest.mark.parametrize("cap", [True, 0, -1, 3601, 1.5])
def test_conditional_deadline_rejects_invalid_values_without_charging(tmp_path, cap):
    ledger = ledger_at(tmp_path)
    with pytest.raises(ValueError, match="deadline"):
        ledger.reserve("gnn", "conditional", "bad", registration(max_fit_seconds=cap))
    assert not ledger.read()["fits"]


def test_unchanged_rewire_cannot_claim_relation_gain_or_open_outer():
    tasks = settings_for(SOURCE, load_contract(SOURCE / "configs/stage09_model_enhancement.json"))["tasks"]
    ref = {"best_epoch": 18, "epochs_observed": 28, "metrics": {"ap": .3}, "fit_seconds": 1., "task": {"id": "g3"}}
    results = {t["id"]: {"metrics": {"ap": .1}, "fit_seconds": 2., "input_dimension": 91 if t["core"] else 28, "task": t} for t in tasks}
    plan = {"tasks": tasks, "input_schema": {}, "cache_sha256": "a"*64, "relation_audit": {"validation": {"changed_graph_fraction": 0.}}}
    chosen = decision(plan, ref, results)
    assert not chosen["exploratory_relationship_rule_met"] and not chosen["confirmed_relation_gain"]
    assert chosen["fusion"]["xgboost_weight_grid"] == [0., .25, .5, .75, 1.]
    assert not chosen["final_model_decision"] and not chosen["outer_test_access"]


def test_readonly_budget_summary_still_works_after_decision_without_allowing_fits(tmp_path):
    ledger = ledger_at(tmp_path)
    ledger.freeze_decision("d"*64)
    assert remaining_seconds(ledger) == 10800
    with pytest.raises(ValueError, match="sealed"):
        development_remaining(ledger)
    attempt = ledger.reserve("gnn", "confirmation", "final", registration(seed=20260921))
    assert remaining_seconds(ledger) == 10800  # Reserved time is charged conservatively.
    ledger.finish(attempt.name, "completed", 1)
    assert remaining_seconds(ledger) == 14399


def test_reference_files_detect_same_size_corruption_and_path_escape(tmp_path):
    folder = tmp_path / "g3"
    folder.mkdir()
    path = folder / "model.pt"
    path.write_bytes(b"AAAA")
    ref = {"model_directory": "g3", "files": {"model.pt": binding(path)}}
    reference_files(tmp_path, ref)
    path.write_bytes(b"BBBB")
    with pytest.raises(ValueError, match="bytes drift"):
        reference_files(tmp_path, ref)
    with pytest.raises(ValueError, match="escape"):
        reference_files(tmp_path, {**ref, "model_directory": "../outside"})


def test_recovery_requires_every_original_epoch_and_exact_five_metrics():
    fields = ("train_weighted_loss", "train_probe_ap", "train_probe_roc_auc", "inner_ap", "inner_roc_auc")
    history = [{"epoch": i+1, "elapsed_seconds": 100., **{k: .2+i for k in fields}} for i in range(17)]
    repeated = [{**h, "elapsed_seconds": 1.} for h in history]
    assert prefix_equivalence(history, repeated)["epochs"] == 17
    with pytest.raises(ValueError, match="full original"):
        prefix_equivalence(history, repeated[:-1])
    repeated[-1]["inner_ap"] += .0001
    with pytest.raises(ValueError, match="changed"):
        prefix_equivalence(history, repeated)


def test_consolidated_gradient_guard_still_rejects_nonfinite_before_update(tmp_path, monkeypatch):
    from graphrisk.graph.model import GraphSAGERiskModel
    class BadGradient(GraphSAGERiskModel):
        def __init__(self, *args):
            super().__init__(*args)
            self.classifier.weight.register_hook(lambda grad: torch.full_like(grad, float("nan")))
    monkeypatch.setattr("graphrisk.graph.stage09_conditional_training.GraphSAGERiskModel", BadGradient)
    task = {"id": "bad-gradient", "seed": 7, "parameters": {"learning_rate": .02, "hidden_channels": 8, "dropout": .4, "positive_weight": "train_ratio"}}
    settings = {"device": "cpu", "cpu_threads": 1, "batch_size": 4, "max_epochs": 7, "patience": 3, "train_probe_count": 12}
    with pytest.raises(ValueError, match="nonfinite conditional gradient"):
        fit(Tiny(12, "t"), Tiny(6, "v"), np.arange(12)%2, np.arange(6)%2, task, settings, tmp_path)
    assert not (tmp_path / "model.pt").exists() and not (tmp_path / "training-history.json").exists()
