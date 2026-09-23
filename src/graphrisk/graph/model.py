"""Two-layer GraphSAGE for a transaction root in a temporal ego graph."""

from __future__ import annotations

import torch
from torch import Tensor, nn
from torch_geometric.data import Batch, Data
from torch_geometric.nn import SAGEConv


class GraphSAGERiskModel(nn.Module):
    def __init__(self, input_channels: int, hidden_channels: int, dropout: float) -> None:
        super().__init__()
        if input_channels < 1 or hidden_channels < 1:
            raise ValueError("GraphSAGE feature and hidden dimensions must be positive")
        if not 0 <= dropout < 1:
            raise ValueError("GraphSAGE dropout must be in [0, 1)")
        self.input_channels = input_channels
        self.hidden_channels = hidden_channels
        self.dropout_probability = dropout
        self.conv1 = SAGEConv(input_channels, hidden_channels)
        self.conv2 = SAGEConv(hidden_channels, hidden_channels)
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(hidden_channels, 1)

    def encode_root(self, graph: Data | Batch) -> Tensor:
        if graph.x.ndim != 2 or graph.x.shape[1] != self.input_channels:
            raise ValueError("GraphSAGE graph feature dimension does not match checkpoint")
        if graph.edge_index.ndim != 2 or graph.edge_index.shape[0] != 2:
            raise ValueError("GraphSAGE edge_index must have shape [2, edges]")
        roots = graph.root_index
        if roots.ndim != 1 or roots.dtype != torch.long:
            raise ValueError("GraphSAGE root_index must be a one-dimensional long tensor")
        if roots.numel() == 0 or torch.any(roots < 0) or torch.any(roots >= graph.x.shape[0]):
            raise ValueError("GraphSAGE root_index is outside the graph")
        if isinstance(graph, Batch) and roots.numel() != graph.num_graphs:
            raise ValueError("GraphSAGE requires exactly one root per graph in the batch")
        features = self.conv1(graph.x, graph.edge_index).relu()
        features = self.dropout(features)
        features = self.conv2(features, graph.edge_index).relu()
        features = self.dropout(features)
        return features[roots]

    def forward(self, graph: Data | Batch) -> Tensor:
        return self.classifier(self.encode_root(graph)).squeeze(-1)
