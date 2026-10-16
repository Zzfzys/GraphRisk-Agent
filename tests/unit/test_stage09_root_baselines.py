"""Deterministic boundary/control tests; no real dataset/model-provider dependency."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
import torch
from graphrisk.demo.stage09_evaluation import digest, read_json, write_json
from graphrisk.graph.build_artifacts import LoadedGraphArtifacts
from graphrisk.graph.schema import GraphFeatureEncoder, GraphTransaction
from graphrisk.graph.temporal_index import TemporalEntityIndex
from graphrisk.graph.stage09_baselines import (root_matrix, verify_encoder, sampling_rows,
    summarize_sampling, create_larger_manifest, reserve_control, finish_control, RootMLP,
    fit_mlp, DeadlineCallback, align_scores, verify_registration)

def row(key, hour, amount=1., unknown=False, product="C"):
    entities = [f"unknown-{i}" for i in range(4)] if unknown else ["card", "device", "email", "addr"]
    return GraphTransaction(key, datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=hour),
        *entities, product, (amount, 1., 2., 3., None, None, None, 0., 1.))

def store(rows=None):
    rows = rows or [row("a", 0, 1), row("b", 1, 2), row("c", 2, 99, product="UNSEEN")]
    ids = {"train": tuple(r.transaction_id for r in rows[:2]), "validation": (rows[2].transaction_id,), "test": ()}
    encoder = GraphFeatureEncoder.fit(rows, train_transaction_ids=ids["train"])
    return LoadedGraphArtifacts({"fanout": [10, 5]}, TemporalEntityIndex(rows), encoder, ids)

def test_common_roots_are_exact_native_root_and_train_only():
    source = store()
    roots = root_matrix(source, source.target_ids["validation"])
    graph = source.ego_graph("c").to_pyg(source.encoder)
    np.testing.assert_array_equal(roots[0], graph.x[0].numpy())
    assert source.encoder.product_codes == ("C",)
    assert source.encoder.means[0] == 1.5
    assert verify_encoder(source)["train_only_refit_equal"]
    assert not any("card" in name and not name.startswith("type:") for name in source.encoder.feature_names)

def test_encoder_refit_rejects_validation_contamination():
    source = store()
    contaminated = GraphFeatureEncoder.fit(source.index.by_id.values(),
        train_transaction_ids=("a", "b", "c"))
    with pytest.raises(ValueError, match="train-only"):
        verify_encoder(LoadedGraphArtifacts({}, source.index, contaminated, source.target_ids))

@pytest.mark.parametrize("ids", [(), ("a", "a")])
def test_common_roots_reject_nonunique_or_empty(ids):
    with pytest.raises(ValueError, match="unique"):
        root_matrix(store(), ids)

def test_history_cap_deduplicates_entities_and_excludes_equal_future():
    rows = [row(f"h{i:02d}", i) for i in range(12)] + [row("root", 12), row("tie", 12), row("future", 13)]
    source = store(rows)
    five, _ = sampling_rows(source, ("root",), 5, row("bound", 20).transaction_time)
    ten, _ = sampling_rows(source, ("root",), 10, row("bound", 20).transaction_time)
    assert five.iloc[0].history == 5 and ten.iloc[0].history == 10
    assert five.iloc[0].selected_entity_incidences == 20  # Same five transactions over four postings.
    assert five.iloc[0].available_entity_incidences == 48
    assert five.iloc[0].simultaneous_incidences_excluded == 8  # root and tie, four entities each.
    assert five.iloc[0].truncated_entities == 4
    assert set(source.index.build("root", max_history_per_entity=5).selected_history_ids).isdisjoint({"root", "tie", "future"})

def test_unknown_entities_zero_edges_and_late_validation_context():
    source = store([row("old", 0), row("v1", 10), row("v2", 11), row("none", 12, unknown=True)])
    frames, degrees = sampling_rows(source, ("v2", "none"), 5, row("cut", 10).transaction_time)
    assert frames.iloc[0].earlier_validation_context == 1
    assert frames.iloc[1].missing_root_entities == 4
    assert frames.iloc[1].directed_pyg_edges == 0
    summary = summarize_sampling(frames)
    assert summary["zero_edges_fraction"] == .5 and summary["zero_history_fraction"] == .5
    assert summary["earlier_validation_context_targets"] == 1
    assert degrees["min"] == 0

@pytest.mark.parametrize("cap", [0, -1, True, 1.5])
def test_invalid_caps_fail(cap):
    with pytest.raises(ValueError, match="positive integer"):
        sampling_rows(store(), ("c",), cap, row("cut", 1).transaction_time)

def test_candidate_is_separate_immutable_targets_features_not_stale_ego_sample(tmp_path):
    project = tmp_path
    parent = project / "artifacts/parent"
    parent.mkdir(parents=True)
    manifest = {"content_sha256": "parent-hash", "fanout": [10, 5], "artifacts": {}}
    for name in ("encoder", "train_ids", "validation_ids", "test_ids"):
        write_json(parent / (name + ".json"), {"name": name})
        manifest["artifacts"][name] = {"path": str((parent / (name + ".json")).relative_to(project)), "sha256": "old"}
    manifest["artifacts"]["ego_sample"] = {"path": "old-cap5-sample.json", "sha256": "old"}
    original = deepcopy(manifest)
    source = SimpleNamespace(manifest=manifest)
    destination = project / "artifacts/stage-09/route3/probe/history-cap-10"
    candidate = create_larger_manifest(project, source, destination, (10, 10))
    value = read_json(candidate)
    assert source.manifest == original and value["fanout"] == [10, 10]
    assert value["parent_manifest_sha256"] == "parent-hash"
    assert "ego_sample" not in value["artifacts"]
    assert value["content_sha256"] == digest({k:v for k,v in value.items() if k != "content_sha256"})
    for name in ("encoder", "train_ids", "validation_ids", "test_ids"):
        assert (project / value["artifacts"][name]["path"]).read_bytes() == (parent / (name + ".json")).read_bytes()
    with pytest.raises(FileExistsError):
        create_larger_manifest(project, source, destination, (10, 10))
    with pytest.raises(ValueError, match="inside"):
        create_larger_manifest(project, source, project / "outside", (10, 10))

def test_controls_each_once_including_failed_attempt(tmp_path):
    ledger = tmp_path / "ledger.json"
    reserve_control(ledger, "xgboost", "registration-a")
    finish_control(ledger, "xgboost", "failed_no_refund", 2.)
    with pytest.raises(ValueError, match="repeat"):
        reserve_control(ledger, "xgboost", "registration-b")
    reserve_control(ledger, "mlp", "registration-a")
    assert len(read_json(ledger)["attempts"]) == 2
    with pytest.raises(ValueError, match="unregistered"):
        reserve_control(ledger, "gnn", "x")

def test_mlp_restores_best_validation_state_and_stops(monkeypatch):
    import graphrisk.graph.stage09_baselines as module
    scripted = iter([.4, .8, .3, .2])
    snapshots = []
    native = module.evaluate_binary_classifier
    def metric(y, scores, **kw):
        result = native(y, scores, **kw)
        result["pr_auc"] = next(scripted)
        snapshots.append(scores.copy())
        return result
    monkeypatch.setattr(module, "evaluate_binary_classifier", metric)
    x = np.array([[0., 1.], [1., 0.], [2., 1.], [3., 0.]], dtype=np.float32)
    y = np.array([0., 0., 1., 1.], dtype=np.float32)
    plan = {"seed": 20260921, "cpu_threads": 1, "mlp": {"hidden_channels": 4, "dropout": .2,
        "learning_rate": .01, "max_epochs": 8, "batch_size": 2, "patience": 2}}
    model, score, detail = fit_mlp(x, y, x, y, plan)
    assert detail["best_epoch"] == 2 and detail["stopped_after_epoch"] == 4
    np.testing.assert_array_equal(score, snapshots[1])
    assert not model.training

def test_mlp_and_xgboost_callback_fail_closed_on_deadline():
    def exhausted():
        raise TimeoutError("expired")
    with pytest.raises(TimeoutError):
        DeadlineCallback(exhausted).after_iteration(None, 0, {})
    x = np.ones((2, 2), dtype=np.float32)
    y = np.array([0, 1], dtype=np.float32)
    plan = {"seed": 20260921, "cpu_threads": 1,
        "mlp": {"hidden_channels": 4, "dropout": .2, "learning_rate": .01,
            "max_epochs": 2, "batch_size": 2, "patience": 1}}
    with pytest.raises(TimeoutError):
        fit_mlp(x, y, x, y, plan, exhausted)

def test_frozen_scores_align_by_id_reject_duplicates_missing_and_bad_score():
    frame = pd.DataFrame({"transaction_id": ["b", "a"], "graphsage_score": [.2, .1]})
    assert align_scores(frame, ("a", "b")).graphsage_score.tolist() == [.1, .2]
    for broken in (frame.iloc[:1], pd.concat([frame, frame.iloc[:1]]),
                   frame.assign(graphsage_score=[np.nan, .1]),
                   frame.assign(graphsage_score=[1.1, .1])):
        with pytest.raises(ValueError):
            align_scores(broken, ("a", "b"))

def test_train_cannot_read_outside_registered_report_root(tmp_path):
    with pytest.raises(ValueError, match="inside"):
        verify_registration(tmp_path, tmp_path / "elsewhere")

def test_prepare_actually_calls_label_readers_only_for_train_validation(tmp_path, monkeypatch):
    import graphrisk.graph.stage09_baselines as module
    rows = [row("t0", 0), row("t1", 1), row("v0", 10), row("v1", 11)]
    ids = {"train": ("t0", "t1"), "validation": ("v0", "v1"), "test": ("forbidden",)}
    source = LoadedGraphArtifacts({"content_sha256": "parent"}, TemporalEntityIndex(rows),
        GraphFeatureEncoder.fit(rows, train_transaction_ids=ids["train"]), ids)
    prior = tmp_path / "reports/generated/stage09/route2/20261007-audit-v1/inputs-after-audit.json"
    write_json(prior, {})
    monkeypatch.setattr(module, "check_freeze", lambda *a: {"status": "passed"})
    monkeypatch.setattr(module, "check_resources", lambda *a: {"status": "passed"})
    monkeypatch.setattr(module, "resources", lambda *a: {})
    monkeypatch.setattr(module, "load_contract", lambda *a: SimpleNamespace(
        resources=None, graph=SimpleNamespace(larger_fanout=(10, 10))))
    monkeypatch.setattr(module, "load_plan", lambda *a: {})
    monkeypatch.setattr(module, "load_graph_config", lambda *a: SimpleNamespace(
        paths=SimpleNamespace(graph_manifest="graph", training_config="training"),
        resolve_path=lambda p: tmp_path / p))
    monkeypatch.setattr(module, "load_training_config", lambda *a: SimpleNamespace(
        paths=SimpleNamespace(train_features="train", validation_features="validation", test_features="FORBIDDEN"),
        resolve_path=lambda p: tmp_path / p))
    monkeypatch.setattr(module, "load_graph_artifacts", lambda *a, **kw: source)
    provenance, read_splits = [], []
    monkeypatch.setattr(module, "_verify_label_provenance",
        lambda config, paths: provenance.extend(paths))
    def labels(path, target_ids, split):
        assert split in ("train", "validation") and "FORBIDDEN" not in str(path)
        read_splits.append(split)
        return np.array([0., 1.], dtype=np.float32)
    monkeypatch.setattr(module, "_load_labels", labels)
    class EndOfArrayProbe(Exception):
        pass
    def stop_after_arrays(*a):
        raise EndOfArrayProbe()
    monkeypatch.setattr(module, "create_larger_manifest", stop_after_arrays)
    out = tmp_path / "reports/generated/stage09/route3/probe"
    with pytest.raises(EndOfArrayProbe):
        module.prepare(tmp_path, out)
    assert provenance == read_splits == ["train", "validation"]
    saved = np.load(out / "root-matrices.npz", allow_pickle=False)
    assert set(saved.files) == {"train_x", "train_y", "validation_x", "validation_y"}
    np.testing.assert_array_equal(saved["validation_x"], root_matrix(source, ids["validation"]))

