from __future__ import annotations

import argparse

from graphrisk.graph.train_graphsage import train_graphsage


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Stage 04 GraphSAGE")
    parser.add_argument("--config", default="configs/graph_ieee_cis.yaml")
    parser.add_argument(
        "--validation-only",
        action="store_true",
        help="select a checkpoint using validation without reading test labels",
    )
    parser.add_argument(
        "--run-name",
        help="write an isolated run under models/<model_version>/runs/<run_name>",
    )
    arguments = parser.parse_args()
    result = train_graphsage(
        arguments.config,
        evaluate_test=not arguments.validation_only,
        run_name=arguments.run_name,
    )
    test_display = (
        f"{result.test_pr_auc:.6f}"
        if result.test_pr_auc is not None else "not_evaluated"
    )
    print(
        "GraphSAGE training complete: "
        f"best_epoch={result.best_epoch}, "
        f"validation_pr_auc={result.validation_pr_auc:.6f}, "
        f"test_pr_auc={test_display}, "
        f"device={result.device}, "
        f"training_seconds={result.training_seconds:.1f}, "
        f"model={result.model_path}"
    )


if __name__ == "__main__":
    main()
