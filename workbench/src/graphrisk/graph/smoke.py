from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Literal

import torch
import torch_geometric
from torch_geometric.nn import SAGEConv

from graphrisk.graph.config import load_graph_config


DeviceChoice = Literal["auto", "cpu", "cuda"]


def select_device(choice: DeviceChoice) -> torch.device:
    if choice == "cpu":
        return torch.device("cpu")
    if choice == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is unavailable; choose cpu or auto")
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _run_sage(device: torch.device) -> list[int]:
    features = torch.tensor(
        [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], device=device
    )
    edges = torch.tensor(
        [[0, 1, 1, 2], [1, 0, 2, 1]], dtype=torch.long, device=device
    )
    layer = SAGEConv(2, 4).to(device)
    with torch.no_grad():
        result = layer(features, edges)
    if result.shape != (3, 4) or not bool(torch.isfinite(result).all().item()):
        raise RuntimeError("SAGEConv smoke produced an invalid output")
    return list(result.shape)


def run_smoke(choice: DeviceChoice) -> dict[str, Any]:
    selected = select_device(choice)
    cpu_shape = _run_sage(torch.device("cpu"))
    selected_shape = (
        cpu_shape if selected.type == "cpu" else _run_sage(selected)
    )
    return {
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "torch_geometric": torch_geometric.__version__,
        "torch_cuda_runtime": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "selected_device": selected.type,
        "cpu_sage_shape": cpu_shape,
        "selected_sage_shape": selected_shape,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 04 torch/PyG device smoke test")
    parser.add_argument(
        "--config", default="configs/graph_ieee_cis.yaml", help="Graph config path"
    )
    args = parser.parse_args()
    config = load_graph_config(args.config)
    result = run_smoke(config.training.device)
    result["stage_input_sizes_bytes"] = config.check_stage_inputs()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
