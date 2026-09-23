from __future__ import annotations

import argparse

from graphrisk.graph.train_graphsage import train_graphsage


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Stage 04 GraphSAGE")
    parser.add_argument("--config", default="configs/graph_ieee_cis.yaml")
    arguments = parser.parse_args()
    result = train_graphsage(arguments.config)
    print(
        "GraphSAGE training complete: "
        f"best_epoch={result.best_epoch}, "
        f"validation_pr_auc={result.validation_pr_auc:.6f}, "
        f"test_pr_auc={result.test_pr_auc:.6f}, "
        f"device={result.device}, "
        f"training_seconds={result.training_seconds:.1f}, "
        f"model={result.model_path}"
    )


if __name__ == "__main__":
    main()
