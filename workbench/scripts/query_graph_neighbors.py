from __future__ import annotations

import argparse

from graphrisk.config import load_config
from graphrisk.graph.config import load_graph_config
from graphrisk.graph.service import GraphRiskService
from graphrisk.investigation.relation_tools import find_risky_neighbors
from graphrisk.investigation.repository import InvestigationRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Query strictly historical scored graph neighbors")
    parser.add_argument("transaction_id")
    parser.add_argument("--config", default="configs/graph_ieee_cis.yaml")
    parser.add_argument("--comparison-run", default="stage05-route8")
    parser.add_argument("--limit", type=int, default=5)
    arguments = parser.parse_args()
    graph_config = load_graph_config(arguments.config)
    data_config = load_config(graph_config.resolve_path(graph_config.paths.data_config))
    repository = InvestigationRepository.from_config(data_config)
    comparison_dir = (
        graph_config.resolve_path(graph_config.paths.artifact_dir)
        / "comparisons" / arguments.comparison_run
    )
    service = GraphRiskService(graph_config, comparison_dir=comparison_dir)
    result = find_risky_neighbors(
        {
            "transaction_id": arguments.transaction_id,
            "dataset_source": graph_config.dataset_source,
            "limit": arguments.limit,
        },
        repository,
        service,
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
