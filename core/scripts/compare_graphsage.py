from __future__ import annotations

import argparse

from graphrisk.graph.compare_models import compare_same_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 04 same-ID GraphSAGE, XGBoost, and fusion comparison")
    parser.add_argument("--config", default="configs/graph_ieee_cis.yaml")
    parser.add_argument("--run-name", default="same-id-v1")
    parser.add_argument("--graph-checkpoint-dir")
    arguments = parser.parse_args()
    result = compare_same_ids(
        arguments.config,
        run_name=arguments.run_name,
        graph_checkpoint_dir=arguments.graph_checkpoint_dir,
    )
    print(
        f"comparison complete: alpha={result.selected_alpha}, "
        f"validation_fusion_pr_auc={result.validation_metrics['fusion']['pr_auc']:.6f}, "
        f"test_fusion_pr_auc={result.test_metrics['fusion']['pr_auc']:.6f}, "
        f"report={result.report_path}"
    )


if __name__ == "__main__":
    main()
