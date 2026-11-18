import pytest
import torch

from graphrisk.graph.smoke import run_smoke, select_device


def test_cpu_sage_smoke_runs_and_reports_shape() -> None:
    result = run_smoke("cpu")
    assert result["selected_device"] == "cpu"
    assert result["cpu_sage_shape"] == [3, 4]
    assert result["selected_sage_shape"] == [3, 4]
    assert result["torch"] == torch.__version__


def test_auto_device_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert select_device("auto").type == "cpu"
    with pytest.raises(RuntimeError, match="CUDA was requested but is unavailable"):
        select_device("cuda")
