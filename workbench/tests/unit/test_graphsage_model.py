from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
import torch
from torch_geometric.data import Batch, Data

from graphrisk.graph.model import GraphSAGERiskModel
from graphrisk.graph.train_graphsage import _select_device, load_graphsage_checkpoint


def _graph(root_features: tuple[float, float]) -> Data:
    return Data(
        x=torch.tensor([root_features, (0.5, 0.5)], dtype=torch.float32),
        edge_index=torch.tensor([[0, 1], [1, 0]], dtype=torch.long),
        root_index=torch.tensor([0], dtype=torch.long),
    )


def test_graphsage_batch_roots_match_individual_predictions() -> None:
    torch.manual_seed(5)
    model = GraphSAGERiskModel(2, 8, 0.0).eval()
    first, second = _graph((0.0, 1.0)), _graph((1.0, 0.0))
    batch = Batch.from_data_list([first, second])
    assert batch.root_index.tolist() == [0, 2]
    with torch.no_grad():
        result = model(batch)
        expected = torch.cat([model(first), model(second)])
    assert result.shape == (2,)
    assert torch.allclose(result, expected)
    assert torch.sigmoid(result).min() >= 0
    assert torch.sigmoid(result).max() <= 1
    with pytest.raises(ValueError, match="feature dimension"):
        model(Data(x=torch.zeros(2, 3), edge_index=first.edge_index, root_index=first.root_index))


def test_graphsage_single_step_updates_parameters_and_tiny_fixture_overfits() -> None:
    torch.manual_seed(7)
    model = GraphSAGERiskModel(2, 8, 0.0)
    batch = Batch.from_data_list([_graph((0.0, 1.0)), _graph((1.0, 0.0))])
    labels = torch.tensor([0.0, 1.0])
    optimizer = torch.optim.Adam(model.parameters(), lr=0.05)
    loss_fn = torch.nn.BCEWithLogitsLoss()
    before = {name: value.detach().clone() for name, value in model.named_parameters()}
    first_loss = float(loss_fn(model(batch), labels).detach())
    for _ in range(80):
        optimizer.zero_grad()
        loss = loss_fn(model(batch), labels)
        loss.backward()
        optimizer.step()
    final_loss = float(loss_fn(model(batch), labels).detach())
    assert final_loss < first_loss * 0.1
    assert any(not torch.equal(before[name], value) for name, value in model.named_parameters())


def test_checkpoint_loads_on_cpu_and_rejects_mismatch_or_corruption(tmp_path) -> None:
    model = GraphSAGERiskModel(2, 8, 0.0)
    model_path = tmp_path / "model.pt"
    metadata_path = tmp_path / "metadata.json"
    torch.save(model.state_dict(), model_path)
    metadata = {
        "dataset_source": "synthetic",
        "model_version": "graphsage-test",
        "graph_manifest_sha256": "graph-hash",
        "feature_names": ["a", "b"],
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "input_channels": 2,
        "hidden_channels": 8,
        "dropout": 0.0,
    }
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    store = SimpleNamespace(
        manifest={"dataset_source": "synthetic", "content_sha256": "graph-hash"},
        encoder=SimpleNamespace(feature_names=("a", "b")),
    )
    loaded = load_graphsage_checkpoint(model_path, metadata_path, store, device="cpu")
    assert loaded.training is False
    with pytest.raises(ValueError, match="model_version"):
        load_graphsage_checkpoint(
            model_path, metadata_path, store, expected_model_version="other-version"
        )
    with torch.no_grad():
        assert torch.allclose(loaded(_graph((1.0, 0.0))), model(_graph((1.0, 0.0))))
    store.manifest["content_sha256"] = "different"
    with pytest.raises(ValueError, match="manifest hash"):
        load_graphsage_checkpoint(model_path, metadata_path, store)
    store.manifest["content_sha256"] = "graph-hash"
    store.encoder.feature_names = ("a", "other")
    with pytest.raises(ValueError, match="feature schema"):
        load_graphsage_checkpoint(model_path, metadata_path, store)
    store.encoder.feature_names = ("a", "b")
    model_path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checkpoint hash"):
        load_graphsage_checkpoint(model_path, metadata_path, store)
    model_path.unlink()
    with pytest.raises(FileNotFoundError, match="missing"):
        load_graphsage_checkpoint(model_path, metadata_path, store)


def test_device_auto_records_cpu_fallback_when_cuda_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    device, reason = _select_device("auto")
    assert device.type == "cpu"
    assert "CUDA unavailable" in reason
    with pytest.raises(RuntimeError, match="CUDA was requested"):
        _select_device("cuda")
